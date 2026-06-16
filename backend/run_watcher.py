"""PyInstaller / portable entry point for the raw-folder watcher."""

from __future__ import annotations

import multiprocessing
import os

from backend.app_paths import app_dir, load_app_dotenv

load_app_dotenv()


def main() -> None:
    from license_validator import validate_license

    validate_license()
    from backend.agents.watch_raw import main as watcher_main

    os.chdir(app_dir())
    watcher_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.chdir(app_dir())
    main()
