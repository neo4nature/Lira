
#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import argparse
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

AGENT_CONFIG = {
    "Lira": {
        "url": "https://chatgpt.com/g/g-p-68b096621938819198de64c0bd9f6398/c/69af76f8-8e78-8387-85c1-1ab8e02f3dd1/",
        "profile_dir": Path("./profiles/lira"),
        "label": "Lira / ChatGPT",
    },
    "Soryel": {
        "url": "https://claude.ai/new",
        "profile_dir": Path("./profiles/soryel"),
        "label": "Soryel / Claude",
    },
}


def find_brave_executable() -> str:
    for name in ("brave-browser", "brave"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "Nie znalazłem Brave w PATH. Sprawdź: `which brave-browser` albo `which brave`."
    )


def bootstrap_agent(agent_name: str, headless: bool = False) -> None:
    if agent_name not in AGENT_CONFIG:
        raise ValueError(f"Nieznany agent: {agent_name}")

    cfg = AGENT_CONFIG[agent_name]
    profile_dir: Path = cfg["profile_dir"]
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Bootstrap profilu: {cfg['label']} ===")
    print(f"Profil: {profile_dir.resolve()}")
    print(f"URL:    {cfg['url']}")
    print()

    with sync_playwright() as p:
        brave_path = find_brave_executable()

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=brave_path,
            headless=headless,
            slow_mo=80,
            viewport={"width": 1400, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()
        stealth_sync(page)
        page.goto(cfg["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        try:
            input("Po zalogowaniu naciśnij ENTER, aby zamknąć i zapisać profil...")
        finally:
            context.close()

    print(f"✓ Profil dla {agent_name} został zapisany.\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", choices=["Lira", "Soryel", "all"])
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    if args.agent == "all":
        for agent_name in ("Lira", "Soryel"):
            bootstrap_agent(agent_name, headless=args.headless)
    else:
        bootstrap_agent(args.agent, headless=args.headless)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPrzerwano ręcznie.")
        sys.exit(1)