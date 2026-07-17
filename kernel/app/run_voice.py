import os
from pathlib import Path

# Load .env so DATA_ROOT and other paths are set correctly before any module imports
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from channeling.channel_router import read_listen_state, write_listen_state


def _bootstrap_listen_state() -> None:
    state = read_listen_state()
    if not state or "listening" not in state:
        write_listen_state({
            "listening": False,
            "active_channel": None,
            "last_wake_word": None,
        })


if __name__ == '__main__':
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "voice"

    if mode == "stt":
        # Tryb STT → Claude Code CLI bridge
        # Użycie: python3 run_voice.py stt [/dev/pts/N]
        if len(sys.argv) > 2:
            os.environ.setdefault("STT_TARGET_TTY", sys.argv[2])
        from voice.stt_to_claude import main
        main()
    else:
        # Tryb normalny: wake word + Lira/Soryel routing
        _bootstrap_listen_state()
        from voice.voice_agent import main
        main()
