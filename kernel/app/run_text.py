from agents.core.heartbeat import main

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
    _bootstrap_listen_state()
    main()
