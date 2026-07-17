from __future__ import annotations
import argparse
import os
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

from config import paths, ensure_dirs
from agents.core.runtime_stream import append_event
from agents.core.idle_gate import allows_background

def utc_date() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _tar_gz(out_path: Path, files: List[Path], base_dir: Optional[Path]=None) -> int:
    if not files:
        return 0
    _safe_mkdir(out_path.parent)
    count = 0
    with tarfile.open(out_path, "w:gz") as tf:
        for f in files:
            try:
                arcname = str(f.relative_to(base_dir)) if base_dir else f.name
                tf.add(f, arcname=arcname)
                count += 1
            except FileNotFoundError:
                continue
    return count

def rotate_runtime(runtime_path: Path, dest_dir: Path) -> Optional[Path]:
    """Rotate runtime.jsonl by date OR by size.

    - Daily: if file mtime is not today, archive + truncate.
    - Size: if file size >= LIRA_RUNTIME_ROTATE_MB (default 50MB), archive + truncate.
    """
    if not runtime_path.exists() or runtime_path.stat().st_size == 0:
        return None

    size_mb = runtime_path.stat().st_size / (1024 * 1024)
    rotate_mb = int(os.getenv("LIRA_RUNTIME_ROTATE_MB", "50"))
    mtime_day = datetime.utcfromtimestamp(runtime_path.stat().st_mtime).strftime("%Y-%m-%d")

    by_size = size_mb >= rotate_mb
    by_date = mtime_day != utc_date()
    if not (by_size or by_date):
        return None

    stamp = datetime.utcnow().strftime("%Y-%m-%dT%H%M%S") if by_size else mtime_day
    out = dest_dir / "runtime" / f"runtime_{stamp}.tar.gz"
    _safe_mkdir(out.parent)
    n = _tar_gz(out, [runtime_path], base_dir=runtime_path.parent)
    if n:
        runtime_path.write_text("", encoding="utf-8")
        return out
    return None

def archive_old_json(src_dir: Path, dest_dir: Path, days: int, label: str, max_files: int = 5000) -> Optional[Path]:
    if not src_dir.exists():
        return None
    cutoff = datetime.utcnow() - timedelta(days=days)
    candidates: List[Path] = []
    for f in src_dir.glob("*.json"):
        try:
            if datetime.utcfromtimestamp(f.stat().st_mtime) < cutoff:
                candidates.append(f)
        except FileNotFoundError:
            continue
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    if not candidates:
        return None
    if len(candidates) > max_files:
        candidates = candidates[:max_files]

    stamp = utc_date()
    out = dest_dir / label / f"{label}_{stamp}.tar.gz"
    _safe_mkdir(out.parent)
    n = _tar_gz(out, candidates, base_dir=src_dir.parent)
    if n:
        for f in candidates:
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        return out
    return None


def rotate_embeddings_index(index_path: Path, dest_dir: Path) -> Optional[Path]:
    """Archive embeddings_index.jsonl when it grows or becomes stale.

    Policy:
    - If file doesn't exist or empty: skip.
    - If size exceeds LIRA_EMBED_INDEX_ROTATE_MB (default 256MB): archive now.
    - Else if mtime is not today AND LIRA_EMBED_INDEX_ROTATE_DAILY=1: archive.
    - Optional truncate after archive if LIRA_EMBED_ARCHIVE_TRUNCATE=1.
    """
    if not index_path.exists() or index_path.stat().st_size == 0:
        return None

    rotate_mb = int(os.getenv("LIRA_EMBED_INDEX_ROTATE_MB", "256"))
    rotate_daily = os.getenv("LIRA_EMBED_INDEX_ROTATE_DAILY", "0") == "1"
    truncate_after = os.getenv("LIRA_EMBED_ARCHIVE_TRUNCATE", "0") == "1"

    size_mb = index_path.stat().st_size / (1024 * 1024)
    mtime_day = datetime.utcfromtimestamp(index_path.stat().st_mtime).strftime("%Y-%m-%d")
    should = (size_mb >= rotate_mb) or (rotate_daily and mtime_day != utc_date())
    if not should:
        return None

    stamp = utc_date()
    out = dest_dir / "embeddings" / f"embeddings_{stamp}.tar.gz"
    _safe_mkdir(out.parent)
    n = _tar_gz(out, [index_path], base_dir=index_path.parent)
    if n and truncate_after:
        # keep the file but truncate to allow fresh growth
        index_path.write_text("", encoding="utf-8")
    return out if n else None

def run(days_events: int = 7, days_rebuild: int = 14) -> None:
    ensure_dirs()
    P = paths()
    archive_root = Path(os.getenv("LIRA_ARCHIVE_ROOT", P.get("ARCHIVE_ROOT", "")) or "")
    if not archive_root:
        append_event("archive_skipped", "archive_manager", {"reason": "no_archive_root"})
        return
    _safe_mkdir(archive_root)

    ok, reason = allows_background()
    if not ok:
        append_event("archive_skipped", "archive_manager", {"reason": f"gate:{reason}"})
        return

    runtime_path = Path(P.get("RUNTIME_STREAM", Path(P["SHARED_CONTEXT_DIR"]) / "events" / "runtime.jsonl"))
    rotated = rotate_runtime(runtime_path, archive_root)

    events_dir = Path(P.get("EVENTS_DIR", Path(P["SHARED_CONTEXT_DIR"]) / "events"))
    archived_events = archive_old_json(events_dir, archive_root, days_events, "events")

    rebuild_dir = Path(P.get("REBUILD_DIR", Path(P["RUNTIME_DIR"]) / "rebuild"))
    archived_rebuild = archive_old_json(rebuild_dir, archive_root, days_rebuild, "rebuild", max_files=2000)

    # Embeddings index rotation (optional)
    embeddings_index = Path(P.get("EMBEDDINGS_INDEX", Path(P["MEMORY_DIR"]) / "embeddings" / "embeddings_index.jsonl"))
    archived_embeddings = rotate_embeddings_index(embeddings_index, archive_root)

    payload = {
        "rotated_runtime": str(rotated) if rotated else None,
        "archived_events": str(archived_events) if archived_events else None,
        "archived_rebuild": str(archived_rebuild) if archived_rebuild else None,
        "archived_embeddings": str(archived_embeddings) if archived_embeddings else None,
        "archive_root": str(archive_root),
    }
    append_event("archive_done", "archive_manager", payload)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-events", type=int, default=int(os.getenv("LIRA_ARCHIVE_EVENTS_DAYS", "7")))
    ap.add_argument("--days-rebuild", type=int, default=int(os.getenv("LIRA_ARCHIVE_REBUILD_DAYS", "14")))
    args = ap.parse_args()
    run(days_events=args.days_events, days_rebuild=args.days_rebuild)

if __name__ == "__main__":
    main()
