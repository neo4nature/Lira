from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import urllib.request

from voice.tts import speak


def _has(cmd: str) -> bool:
    return subprocess.call(["bash", "-lc", f"command -v {cmd} >/dev/null 2>&1"]) == 0


def _popup(title: str, text: str, error: bool = False) -> None:
    """Best-effort popup; falls back to notifications/console."""
    if os.environ.get("LIRA_NO_GUI", "0") == "1":
        print(f"[{title}] {text}")
        return

    if _has("zenity"):
        cmd = ["zenity", "--error" if error else "--info", "--title", title, "--text", text]
        subprocess.Popen(cmd)
        return
    if _has("notify-send"):
        subprocess.Popen(["notify-send", title, text])
        return
    print(f"[{title}] {text}")


def _read_preflight_ok(preflight_path: Path) -> tuple[bool, str]:
    if not preflight_path.exists():
        return False, f"Brak pliku: {preflight_path}"
    try:
        data = json.loads(preflight_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Nie mogę odczytać preflight: {e}"

    # Be permissive: depending on version, "checks" may be a dict or a list of dicts.
    checks = data.get("checks")

    tts_check = None
    if isinstance(checks, dict):
        tts_check = checks.get("tts_piper")
    elif isinstance(checks, list):
        for item in checks:
            if isinstance(item, dict) and item.get("name") == "tts_piper":
                tts_check = item
                break

    # Some versions may store directly at top-level.
    if tts_check is None and isinstance(data, dict):
        tts_check = data.get("tts_piper")

    if isinstance(tts_check, dict):
        details = tts_check.get("details") if isinstance(tts_check.get("details"), dict) else tts_check
        # If there is an explicit "ok" use it, otherwise infer from common fields.
        ok = details.get("ok")
        if ok is None:
            ok_bin = bool(details.get("piper_found") or details.get("bin_ok") or details.get("piper_bin"))
            ok_player = bool(details.get("player") or details.get("player_ok"))
            ok_model = bool(details.get("model_pl_ok") or details.get("model_ok") or details.get("models_ok"))
            ok = ok_bin and ok_player and ok_model
        msg = details.get("message") or details.get("note") or ""
        return bool(ok), str(msg)

    # If we can't find a definitive check, don't block readiness.
    return True, ""


def _wait_health(host: str, port: int, timeout_s: float = 60.0) -> bool:
    url = f"http://{host}:{port}/health"
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1.0)
    return False


def main() -> int:
    host = os.getenv("LIRA_API_HOST", "127.0.0.1")
    port = int(os.getenv("LIRA_API_PORT", "8000"))

    project_root = Path(os.getenv("LIRA_PROJECT_ROOT", Path(__file__).resolve().parents[3])).resolve()
    preflight_path = project_root / "logs" / "preflight_report.json"

    # Wait for API
    ok_api = _wait_health(host, port, timeout_s=float(os.getenv("LIRA_READY_TIMEOUT", "60")))
    ok_tts, tts_msg = _read_preflight_ok(preflight_path)

    if ok_api and ok_tts:
        # Custom ready phrase requested by Neo
        speak(os.getenv("LIRA_READY_PHRASE", "Kochanie jestem gotowa do eksploracji świata"))
        return 0

    # Otherwise show status popup
    parts = []
    if not ok_api:
        parts.append(f"API nie wstało na {host}:{port} (timeout).")
    if not ok_tts:
        parts.append("TTS Piper nie jest gotowy." + (f"\n{tts_msg}" if tts_msg else ""))
    if not parts:
        parts.append("System nie jest gotowy. Sprawdź logi: logs/preflight_report.json")

    _popup("Lira — status uruchomienia", "\n\n".join(parts), error=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
