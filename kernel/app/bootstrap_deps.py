import os
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Set

# -----------------------------------------------------------------------------
# Bootstrap deps (best-effort) with ZERO surprise NVIDIA/CUDA bloat.
#
# Defaults:
# - Vosk + Piper are installed if missing (CPU/offline).
# - Whisper is NOT installed unless INSTALL_WHISPER=1.
#
# Whisper install options (when INSTALL_WHISPER=1):
# - WHISPER_BACKEND=faster-whisper  (default; no PyTorch, CPU-friendly)
# - WHISPER_BACKEND=openai-whisper  (requires PyTorch; we FORCE CPU-only torch)
# -----------------------------------------------------------------------------

# Manifest: remove ONLY what we installed (no surprise system-wide removals)
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data/runtime/state/deps_manifest.json"  # kernel/data/...

# Where we keep pinned binaries/models (project-local)
PINNED_PIPER_BIN = Path(__file__).resolve().parents[1] / "models" / "piper" / "bin" / "piper"

def _pin_piper_binary(report: Dict[str, Any]) -> None:
    """Best-effort: create a stable project-local path to piper binary.

    This does NOT download voices. It only pins the executable so other modules can rely on:
      kernel/models/piper/bin/piper
    """
    try:
        p = shutil.which("piper")
        if not p:
            return
        PINNED_PIPER_BIN.parent.mkdir(parents=True, exist_ok=True)
        # Replace any existing file/symlink
        try:
            if PINNED_PIPER_BIN.exists() or PINNED_PIPER_BIN.is_symlink():
                PINNED_PIPER_BIN.unlink()
        except Exception:
            pass
        os.symlink(p, PINNED_PIPER_BIN)
        report["actions"].append({"piper_pinned": str(PINNED_PIPER_BIN)})
    except Exception as e:
        report["actions"].append({"piper_pinned_error": str(e)})

def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _load_manifest() -> Dict[str, Any]:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"schema": "lira.deps_manifest.v1", "items": []}
    return {"schema": "lira.deps_manifest.v1", "items": []}

def _save_manifest(m: Dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)

def _record_manifest(kind: str, name: str, reason: str) -> None:
    m = _load_manifest()
    m.setdefault("items", []).append({
        "ts": _now_iso(),
        "kind": kind,      # "pip" | "apt"
        "name": name,
        "reason": reason,
    })
    _save_manifest(m)

def _cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _run(cmd: List[str]) -> Dict[str, Any]:
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True)
        return {
            "cmd": cmd,
            "returncode": p.returncode,
            "stdout": (p.stdout or "")[-2000:],
            "stderr": (p.stderr or "")[-2000:],
        }
    except Exception as e:
        return {"cmd": cmd, "error": str(e)}

def _pip_install(pkgs: List[str], extra_args: Optional[List[str]] = None) -> Dict[str, Any]:
    args = ["python3", "-m", "pip", "install", "--upgrade"]
    if extra_args:
        args += extra_args
    args += pkgs
    out = _run(args)
    reason = os.environ.get("LIRA_DEPS_REASON", "")
    if out.get("returncode") == 0:
        for p in pkgs:
            _record_manifest("pip", p, reason=reason)
    return out

def _apt_install(pkgs: List[str]) -> Dict[str, Any]:
    # best-effort using sudo when not root
    if os.geteuid() != 0:
        if _cmd_exists("sudo"):
            out = {"_apt_update": _run(["sudo", "apt-get", "update"])}
            out["_apt_install"] = _run(["sudo", "apt-get", "install", "-y"] + pkgs)
            if out.get("_apt_install", {}).get("returncode") == 0:
                reason = os.environ.get("LIRA_DEPS_REASON", "")
                for p in pkgs:
                    _record_manifest("apt", p, reason=reason)
            return out
        return {"error": "apt_install_requires_root", "pkgs": pkgs}
    out = {"_apt_update": _run(["apt-get", "update"])}
    out["_apt_install"] = _run(["apt-get", "install", "-y"] + pkgs)
    if out.get("_apt_install", {}).get("returncode") == 0:
        reason = os.environ.get("LIRA_DEPS_REASON", "")
        for p in pkgs:
            _record_manifest("apt", p, reason=reason)
    return out

def _env_true(name: str, default: str = "0") -> bool:
    v = os.environ.get(name, default).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def _torch_has_cuda() -> Optional[bool]:
    try:
        import torch  # type: ignore
        return torch.version.cuda is not None
    except Exception:
        return None

def _ensure_ffmpeg(report: Dict[str, Any]) -> None:
    if not _cmd_exists("ffmpeg"):
        os.environ["LIRA_DEPS_REASON"] = "media=ffmpeg"
        report["actions"].append({"install_ffmpeg": _apt_install(["ffmpeg"])})
    else:
        report["actions"].append({"ffmpeg": "ok"})

def _ensure_vosk(report: Dict[str, Any]) -> None:
    try:
        import vosk  # type: ignore
        report["actions"].append({"vosk": "ok"})
    except Exception:
        os.environ["LIRA_DEPS_REASON"] = "stt=vosk"
        report["actions"].append({"install_vosk": _pip_install(["vosk"])})

    # audio I/O (mic)
    try:
        import sounddevice  # type: ignore
        report["actions"].append({"sounddevice": "ok"})
    except Exception:
        os.environ["LIRA_DEPS_REASON"] = "audio=sounddevice"
        report["actions"].append({"install_sounddevice": _pip_install(["sounddevice"])})

def _ensure_piper(report: Dict[str, Any]) -> None:
    if _cmd_exists("piper"):
        report["actions"].append({"piper": "binary_ok"})
        return
    # Piper can be installed via pip on many systems; voice models are separate files.
    os.environ["LIRA_DEPS_REASON"] = "tts=piper"
    report["actions"].append({"install_piper_tts": _pip_install(["piper-tts"])})

def _ensure_whisper(report: Dict[str, Any]) -> None:
    if not _env_true("INSTALL_WHISPER", "0"):
        report["actions"].append({"whisper": "skipped (INSTALL_WHISPER=0)"})
        return

    backend = os.environ.get("WHISPER_BACKEND", "faster-whisper").strip().lower()

    if backend == "faster-whisper":
        # No PyTorch → avoids CUDA/NVIDIA package bloat.
        try:
            import faster_whisper  # type: ignore
            report["actions"].append({"faster_whisper": "ok"})
        except Exception:
            os.environ["LIRA_DEPS_REASON"] = "stt=whisper;backend=faster-whisper"
            report["actions"].append({"install_faster_whisper": _pip_install(["faster-whisper"])})
        return

    if backend == "openai-whisper":
        # This requires PyTorch. We FORCE CPU-only torch wheels.
        # If user already has torch with CUDA build, we DON'T auto-uninstall; we warn and skip.
        cuda_flag = _torch_has_cuda()
        if cuda_flag is True:
            report["actions"].append({
                "openai_whisper": "skipped (torch has CUDA build; refusing auto changes)",
                "hint": "Uninstall torch+cuda and reinstall CPU-only: pip uninstall -y torch torchvision torchaudio && pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision torchaudio && pip install openai-whisper"
            })
            return

        # Ensure CPU-only torch (even if torch not installed yet)
        os.environ["LIRA_DEPS_REASON"] = "stt=whisper;backend=openai-whisper;torch=cpu-only"
        report["actions"].append({
            "install_torch_cpu": _pip_install(
                ["torch", "torchvision", "torchaudio"],
                extra_args=["--index-url", "https://download.pytorch.org/whl/cpu"],
            )
        })

        # Then install whisper
        try:
            import whisper  # type: ignore
            report["actions"].append({"openai_whisper": "ok"})
        except Exception:
            os.environ["LIRA_DEPS_REASON"] = "stt=whisper;backend=openai-whisper"
            report["actions"].append({"install_openai_whisper": _pip_install(["openai-whisper"])})
        return

    report["actions"].append({"whisper": f"unknown backend '{backend}' (supported: faster-whisper, openai-whisper)"})

def run() -> Dict[str, Any]:
    """Bootstrap runtime deps (best-effort, non-destructive)."""
    report: Dict[str, Any] = {"ok": True, "actions": []}

    _ensure_ffmpeg(report)
    _ensure_vosk(report)
    _ensure_piper(report)
    _pin_piper_binary(report)
    _ensure_whisper(report)

    auto_cleanup = _env_true("AUTO_CLEANUP", "1")
    if auto_cleanup:
        report["actions"].append({"cleanup": _cleanup_unused_pip()})

    return report

def _needed_pip_packages() -> Set[str]:
    needed = {"vosk", "sounddevice", "piper-tts"}
    if _env_true("INSTALL_WHISPER", "0"):
        backend = os.environ.get("WHISPER_BACKEND", "faster-whisper").strip().lower()
        if backend == "faster-whisper":
            needed.add("faster-whisper")
        elif backend == "openai-whisper":
            needed.update({"torch", "torchvision", "torchaudio", "openai-whisper"})
    return needed

def _cleanup_unused_pip() -> Dict[str, Any]:
    m = _load_manifest()
    installed = []
    for it in m.get("items", []):
        if it.get("kind") == "pip":
            installed.append(it.get("name"))
    seen = set()
    installed = [p for p in installed if p and not (p in seen or seen.add(p))]
    needed = _needed_pip_packages()
    to_remove = [p for p in installed if p not in needed]
    if not to_remove:
        return {"pip_remove": [], "needed": sorted(needed)}
    out = _run(["python3", "-m", "pip", "uninstall", "-y"] + to_remove)
    return {"pip_remove": to_remove, "result": out, "needed": sorted(needed)}
