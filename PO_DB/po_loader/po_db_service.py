#!/usr/bin/env python3
"""
Unified PO_DB service for the portable build.

On startup this executable:
  1. Syncs po-db/config.ini from the root .env
  2. Starts bundled PostgreSQL when IDP_USE_BUNDLED_POSTGRES=1, otherwise uses external Postgres
  3. Creates the PO_DB database and tables when missing (bundled or external)
  4. Starts the CSV folder watcher immediately

Built as: dist/IDP-Invoice/po-db/po-db.exe
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("po_db")


def _run_watcher(once: bool = False, dry_run: bool = False) -> int:
    from po_db_runtime import INPROCESS_LOADER, config_path, po_db_dir
    import watch_data

    data_dir = po_db_dir() / "data"
    cfg = str(config_path())
    watch_data.DEFAULT_LOADER = INPROCESS_LOADER
    argv = [
        "po-db",
        "--data-dir",
        str(data_dir),
        "--config",
        cfg,
        "--loader",
        INPROCESS_LOADER,
    ]
    if once:
        argv.append("--once")
    if dry_run:
        argv.append("--dry-run")
    sys.argv = argv
    watch_data.main()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PO_DB service: bootstrap PostgreSQL/schema and watch CSV data folder."
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Initialize PostgreSQL + PO_DB schema then exit (no watcher).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single CSV check/load after setup, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSV files only; do not write to the database.",
    )
    args = parser.parse_args()

    try:
        from po_db_runtime import bootstrap_po_db

        bootstrap_po_db()
    except Exception as exc:
        log.error("PO_DB setup failed: %s", exc)
        return 1

    if args.setup_only:
        log.info("PO_DB setup complete.")
        return 0

    log.info("Starting CSV watcher...")
    try:
        return _run_watcher(once=args.once, dry_run=args.dry_run)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
