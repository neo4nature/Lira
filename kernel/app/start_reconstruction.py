"""Start Reconstruction

Builds a canonical, useful Core Memory Ledger from existing memory artifacts.
This is intended to run:
- once after startup (daemon) and/or
- after night cycle (optional)

No LLM calls are performed here (deterministic rebuild). Safety gates are
applied by callers if needed (veto/memory_mode), but rebuild itself is safe.
"""

from agents.memory.reconstruction import rebuild_core_memory


def main():
    rebuild_core_memory(light=True)


if __name__ == "__main__":
    main()
