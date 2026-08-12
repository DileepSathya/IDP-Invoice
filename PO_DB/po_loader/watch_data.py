#!/usr/bin/env python3
"""
PO_DB Data Folder Watcher
==========================
Watches the `data` folder (located one level up from this file, i.e.
alongside load_data.py's parent directory) and automatically runs
load_data.py whenever new CSV files show up.

Design goals (kept simple so this is easy to reconfigure later):
  - Polls on an interval read from config.ini's [watcher] interval_minutes
    (falls back to 5 minutes if that's not set; --interval on the command
    line overrides both). No OS-level file system event API is used, so
    this works identically on Windows/Mac/Linux and inside containers/
    network shares where inotify-style watching is unreliable.
  - Triggers a load whenever at least one watched CSV is present in the
    data folder.
  - After the loader subprocess finishes, the watcher itself sorts the
    watched CSVs out of the data folder:
      * on success       -> moved into data_dir/completed/<timestamp>/
      * on failure       -> moved into data_dir/ERROR/<timestamp>/
    Both folders are created automatically if they don't exist yet. This
    means a CSV sitting directly in data_dir is always unprocessed/new,
    since the watcher clears it out one way or the other right after each
    run. (If load_data.py also does its own file cleanup internally, the
    watcher's move step simply finds nothing left to move and logs that -
    see the note on move_files() below.)
  - Calls load_data.py as a subprocess (not by importing its internals),
    so the two scripts stay decoupled and load_data.py can still be run
    by hand exactly as before.
  - All paths/intervals are CLI flags with sensible defaults, so this is
    easy to drop into a scheduler (cron, Task Scheduler, systemd timer,
    supervisor, etc.) later if polling-in-process is ever not wanted.

Usage:
    python watch_data_folder.py
    python watch_data_folder.py --interval 300 --data-dir ../data
    python watch_data_folder.py --once            # single check, then exit
    python watch_data_folder.py --dry-run          # passes --dry-run through
    python watch_data_folder.py --completed-dir ../done --error-dir ../failed

Stop with Ctrl+C.
"""

import argparse
import configparser
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watcher")

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _po_db_root() -> str:
    """Portable po-db/ folder when frozen; PO_DB/ when running from source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.normpath(os.path.join(THIS_DIR, ".."))


# "prior location with respect to the current file" -> one directory up,
# then into data/. Adjust DEFAULT_DATA_DIR here if the layout changes.
DEFAULT_DATA_DIR = os.path.join(_po_db_root(), "data")
DEFAULT_LOADER = (
    os.path.join(_po_db_root(), "po-loader.exe")
    if getattr(sys, "frozen", False)
    else os.path.join(THIS_DIR, "load_data.py")
)
DEFAULT_CONFIG = os.path.join(_po_db_root(), "config.ini")
DEFAULT_INTERVAL_SECONDS = 5 * 60  # 5 minutes

# Sub-folder names created inside --data-dir by default (can be overridden
# with --completed-dir / --error-dir to point somewhere else entirely).
DEFAULT_COMPLETED_DIRNAME = "completed"
DEFAULT_ERROR_DIRNAME = "ERROR"

WATCHED_FILES = [
    "vendor_master.csv",
    "item_master.csv",
    "po_header.csv",
    "po_details.csv",
]


def files_present(data_dir: str) -> List[str]:
    """Return the watched CSV filenames currently sitting in data_dir.

    Only looks at the 4 known filenames directly inside data_dir - it never
    descends into completed/ or ERROR/, so files already sorted there are
    not picked back up.
    """
    return [f for f in WATCHED_FILES if os.path.exists(os.path.join(data_dir, f))]


def move_files(file_names: List[str], data_dir: str, dest_root: str) -> List[str]:
    """Move any of file_names that still exist in data_dir into a fresh
    timestamped subfolder under dest_root, creating dest_root (and the
    timestamped subfolder) if they don't exist yet.

    Only files that are still actually present get moved - if load_data.py
    already deleted or relocated a file itself before exiting, it's simply
    skipped here (nothing to move). Returns the list of files that were
    actually moved.
    """
    to_move = [f for f in file_names if os.path.exists(os.path.join(data_dir, f))]
    if not to_move:
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(dest_root, timestamp)
    os.makedirs(dest_dir, exist_ok=True)

    moved = []
    for f in to_move:
        src = os.path.join(data_dir, f)
        dst = os.path.join(dest_dir, f)
        try:
            shutil.move(src, dst)
            moved.append(f)
        except OSError as e:
            log.error("Could not move %s to %s: %s", src, dst, e)
    return moved


def get_interval_seconds(config_path: str, cli_value: Optional[int]) -> int:
    """Decide the poll interval.

    Priority: an explicit --interval on the command line always wins.
    Otherwise, read `interval_minutes` from a [watcher] section in
    config.ini (see the sample section below). If the file, section, or key
    is missing - or the value isn't a valid positive number - fall back to
    DEFAULT_INTERVAL_SECONDS (5 minutes) and log why.

    Expected config.ini shape:
        [watcher]
        interval_minutes = 5

        [database]
        host = localhost
        ...
    """
    if cli_value is not None:
        return cli_value

    parser = configparser.ConfigParser()
    read_ok = parser.read(config_path)
    if not read_ok:
        log.warning(
            "Could not read %s for the poll interval - using default of %d minute(s).",
            config_path, DEFAULT_INTERVAL_SECONDS // 60,
        )
        return DEFAULT_INTERVAL_SECONDS

    if not parser.has_section("watcher") or not parser.has_option("watcher", "interval_minutes"):
        log.info(
            "No [watcher] interval_minutes in %s - using default of %d minute(s).",
            config_path, DEFAULT_INTERVAL_SECONDS // 60,
        )
        return DEFAULT_INTERVAL_SECONDS

    try:
        minutes = parser.getint("watcher", "interval_minutes")
        if minutes <= 0:
            raise ValueError("interval_minutes must be a positive whole number of minutes")
        return minutes * 60
    except ValueError as e:
        log.warning(
            "Invalid [watcher] interval_minutes in %s (%s) - using default of %d minute(s).",
            config_path, e, DEFAULT_INTERVAL_SECONDS // 60,
        )
        return DEFAULT_INTERVAL_SECONDS


INPROCESS_LOADER = "__inprocess__"


def run_loader(loader_path: str, data_dir: str, config_path: str, dry_run: bool) -> bool:
    if loader_path == INPROCESS_LOADER:
        from load_data import run_load

        log.info("Running in-process CSV loader.")
        return run_load(data_dir, config_path, dry_run) == 0

    if loader_path.lower().endswith(".exe"):
        cmd = [loader_path, "--data-dir", data_dir, "--config", config_path]
    else:
        cmd = [sys.executable, loader_path, "--data-dir", data_dir, "--config", config_path]
    if dry_run:
        cmd.append("--dry-run")

    log.info("Running loader: %s", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode == 0:
        log.info("Loader finished successfully.")
        return True
    else:
        log.error("Loader exited with code %d. Will retry on next check.", result.returncode)
        return False


def check_once(args) -> bool:
    """Run a single check. Returns True if a load was attempted."""
    if not os.path.isdir(args.data_dir):
        log.warning("Data directory does not exist yet: %s", args.data_dir)
        return False

    present = files_present(args.data_dir)
    if not present:
        log.info("No watched CSV files present in %s, nothing to do.", args.data_dir)
        return False

    log.info("Found file(s) %s in %s, triggering load.", present, args.data_dir)
    success = run_loader(args.loader, args.data_dir, args.config, args.dry_run)

    if args.dry_run:
        # Validate-only mode: nothing should be touched on disk either way.
        log.info("Dry run - leaving %s in place (not moving to completed/ERROR).", present)
        return True

    if success:
        moved = move_files(present, args.data_dir, args.completed_dir)
        if moved:
            log.info("Moved %s to completed folder: %s", moved, args.completed_dir)
        else:
            log.info(
                "Loader succeeded but %s were already gone from %s (nothing to move).",
                present, args.data_dir,
            )
    else:
        moved = move_files(present, args.data_dir, args.error_dir)
        if moved:
            log.warning(
                "Loader reported failure - moved %s to error folder: %s",
                moved, args.error_dir,
            )
        else:
            log.warning(
                "Loader reported failure, but %s were already gone from %s "
                "(check load_data.py's own error handling too).",
                present, args.data_dir,
            )

    return True


def main():
    parser = argparse.ArgumentParser(description="Watch the PO_DB data folder and auto-run load_data.py.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=f"Folder to watch (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--loader", default=DEFAULT_LOADER, help="Path to load_data.py")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to DB config file passed through to load_data.py")
    parser.add_argument(
        "--interval", type=int, default=None,
        help=(
            "Seconds between checks. Overrides config.ini if given. "
            "If omitted, reads [watcher] interval_minutes from config.ini, "
            f"falling back to {DEFAULT_INTERVAL_SECONDS // 60} minute(s) if that's not set."
        ),
    )
    parser.add_argument(
        "--completed-dir", default=None,
        help="Folder to move CSVs into after a successful load (default: <data-dir>/completed)",
    )
    parser.add_argument(
        "--error-dir", default=None,
        help="Folder to move CSVs into after a failed load (default: <data-dir>/ERROR)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run through to load_data.py (validate only, no DB writes, no files touched)")
    parser.add_argument("--once", action="store_true", help="Check a single time and exit (useful for testing / cron instead of the built-in loop)")
    args = parser.parse_args()

    args.data_dir = os.path.abspath(args.data_dir)
    if args.loader != INPROCESS_LOADER:
        args.loader = os.path.abspath(args.loader)
    args.config = os.path.abspath(args.config)
    args.interval = get_interval_seconds(args.config, args.interval)
    args.completed_dir = (
        os.path.abspath(args.completed_dir) if args.completed_dir
        else os.path.join(args.data_dir, DEFAULT_COMPLETED_DIRNAME)
    )
    args.error_dir = (
        os.path.abspath(args.error_dir) if args.error_dir
        else os.path.join(args.data_dir, DEFAULT_ERROR_DIRNAME)
    )

    log.info("Watching data dir : %s", args.data_dir)
    log.info("Loader script     : %s", args.loader)
    log.info("Config file       : %s", args.config)
    log.info("Check interval    : %d second(s)", args.interval)
    log.info("Completed dir     : %s", args.completed_dir)
    log.info("Error dir         : %s", args.error_dir)

    if args.once:
        check_once(args)
        return

    log.info("Starting watch loop. Press Ctrl+C to stop.")
    try:
        while True:
            start = datetime.now()
            try:
                check_once(args)
            except Exception as e:
                # Never let one bad cycle kill the watcher - log and keep going.
                log.error("Unexpected error during check cycle: %s", e)

            elapsed = (datetime.now() - start).total_seconds()
            sleep_for = max(0, args.interval - elapsed)
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        log.info("Stopped by user.")


if __name__ == "__main__":
    main()