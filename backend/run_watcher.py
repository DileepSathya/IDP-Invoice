"""PyInstaller / portable entry point for the raw-folder watcher."""

from __future__ import annotations

import multiprocessing
import os

from backend.app_paths import app_dir, load_app_dotenv

load_app_dotenv()


def main() -> None:
    from backend.agent_settings import load_agent_settings_into_env
    from backend.agents.watch_raw import main as watcher_main

    os.chdir(app_dir())
    load_agent_settings_into_env()
    watcher_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.chdir(app_dir())
    main()
