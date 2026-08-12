#!/usr/bin/env python3
"""
PO_DB CSV Loader
=================
Loads / upserts the customer's CSV files into the PO_DB PostgreSQL database.

Works identically for:
  - The initial full dump (customer sends all data)
  - Every incremental run afterwards (customer sends only new/changed rows)

Existing primary keys are UPDATED, new primary keys are INSERTED.
Nothing is ever deleted from the database.

po_header and po_details can be loaded even when vendor_master.csv /
item_master.csv are missing or empty. Any vendor_id / item_id referenced
by PO data that doesn't yet exist in the parent table gets a minimal
placeholder ("stub") row inserted so the foreign key constraint is
satisfied. Stub rows are clearly flagged (e.g. vendor_name = 'PENDING
VENDOR MASTER DATA') and are automatically overwritten with real data
the next time the actual master file is loaded, since upserts use
ON CONFLICT DO UPDATE.

ATOMICITY
---------
Database: every CSV in a single run is loaded inside ONE Postgres
transaction. If any file fails validation or any statement errors, the
whole transaction is rolled back - either every row from every file in
this run lands in Postgres, or none of them do.

FILESYSTEM
----------
This script never deletes or moves the source CSVs itself - it only
reads them. Sorting the files out of the data directory after a run
(into a "completed" folder on success, or an "ERROR" folder on failure)
is the job of the separate watch_data_folder.py watcher, which invokes
this script as a subprocess and checks its exit code (0 = success,
non-zero = failure) to decide where each file goes. That keeps file
movement in exactly one place and guarantees a source CSV is never
deleted, only ever relocated.

On failure, this script does write a small error.txt directly into
--data-dir (not into a subfolder, so it doesn't interfere with the
watcher's move step) describing exactly what went wrong, for the next
person who looks at the failed run.

Usage:
    python load_data.py --data-dir ./data --config config.ini
    python load_data.py --data-dir ./data --config config.ini --dry-run

The data directory must contain (any subset is fine, missing files are
skipped, but be mindful of foreign-key order):
    vendor_master.csv
    item_master.csv
    po_header.csv
    po_details.csv
"""

import argparse
import configparser
import csv
import io
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras


# ------------------------------------------------------------------
# Table definitions — order matters (parents must load before children)
# ------------------------------------------------------------------
@dataclass
class TableSpec:
    csv_file: str
    table: str
    pk_columns: List[str]
    # columns that should be parsed as int / float instead of left as text
    int_columns: List[str] = field(default_factory=list)
    float_columns: List[str] = field(default_factory=list)
    # text/varchar columns and their Postgres VARCHAR(n) limit, so an
    # oversized value is caught here with a clear message instead of
    # surfacing as a generic Postgres error after the whole batch is sent.
    # Keep in sync with PO_DB/sql/create_po_database.sql.
    varchar_limits: Dict[str, int] = field(default_factory=dict)
    # if set, ensures the referenced parent row exists (creating a minimal
    # stub if necessary) before this table's rows are upserted
    fk_stub: Optional[dict] = None


TABLE_SPECS: List[TableSpec] = [
    TableSpec(
        csv_file="vendor_master.csv",
        table="vendor_master",
        pk_columns=["vendor_id"],
        int_columns=["pin_code"],
        varchar_limits={
            "vendor_id": 500,
            "vendor_name": 100,
            "gst_tax_number": 100,
            "tin_number": 100,
            "vendor_address_1": 255,
            "vendor_address_2": 255,
            "vendor_address_3": 255,
            "city": 20,
            "state": 20,
            "country": 20,
            "primary_ph_number": 15,
            "email_primary": 255,
            "bank_ac_number": 34,
            "bank_name": 125,
        },
    ),
    TableSpec(
        csv_file="item_master.csv",
        table="item_master",
        pk_columns=["item_id"],
        float_columns=["rate"],
        varchar_limits={"item_id": 500, "category": 500, "units": 10},
    ),
    TableSpec(
        csv_file="po_header.csv",
        table="po_header",
        pk_columns=["po_id", "business_unit"],
        varchar_limits={
            "po_id": 500,
            "business_unit": 500,
            "vendor_id": 500,
            "po_status": 100,
            "po_type": 100,
        },
        fk_stub={
            "fk_column": "vendor_id",
            "parent_table": "vendor_master",
            "parent_pk": "vendor_id",
            # vendor_name is NOT NULL, so a stub row needs a placeholder
            "extra_required": {"vendor_name": "PENDING VENDOR MASTER DATA"},
        },
    ),
    TableSpec(
        csv_file="po_details.csv",
        table="po_details",
        pk_columns=["po_id", "business_unit", "item_id"],
        int_columns=["line_number"],
        float_columns=["rate", "qty", "total"],
        varchar_limits={"po_id": 500, "business_unit": 500, "item_id": 500, "units": 10},
        fk_stub={
            "fk_column": "item_id",
            "parent_table": "item_master",
            "parent_pk": "item_id",
            "extra_required": {},  # item_master's other columns are nullable
        },
    ),
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("po_loader")


def read_db_config(config_path: str) -> dict:
    parser = configparser.ConfigParser()
    if not parser.read(config_path):
        log.error("Could not read config file: %s", config_path)
        sys.exit(1)
    if "database" not in parser:
        log.error("Config file is missing a [database] section: %s", config_path)
        sys.exit(1)
    section = parser["database"]
    return {
        "host": section.get("host", "localhost"),
        "port": section.get("port", "5432"),
        "dbname": section.get("dbname", "PO_DB"),
        "user": section.get("user", "postgres"),
        "password": section.get("password", ""),
    }


def clean_value(value: Optional[str], col_type: str):
    """Blank strings become NULL. Numeric columns are cast, otherwise text is kept as-is."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if col_type == "int":
        return int(value)
    if col_type == "float":
        return float(value)
    return value


def load_csv_rows(spec: TableSpec, path: str):
    # Read as bytes first and strip any embedded NUL bytes. These show up when a
    # CSV was saved/re-saved with a tool that null-pads the file (seen in practice
    # with some Excel "Save As CSV" + Windows editor round-trips) - Python's csv
    # module refuses to parse a line containing NUL at all, so this has to be
    # cleaned up before csv.DictReader ever sees the data.
    raw_bytes = open(path, "rb").read()
    nul_count = raw_bytes.count(b"\x00")
    if nul_count:
        log.warning(
            "  %-15s -> stripped %d embedded NUL byte(s) from %s (corrupted export?)",
            spec.table, nul_count, spec.csv_file,
        )
        raw_bytes = raw_bytes.replace(b"\x00", b"")

    text = raw_bytes.decode("utf-8-sig")
    with io.StringIO(text, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header row")
        raw_columns = [c.strip() for c in reader.fieldnames]
        # A trailing comma (or two) in the header row - a common artifact of
        # CSVs saved/re-saved from Excel - makes csv.DictReader invent blank
        # ('') field names for the extra empty column(s). Left in, those
        # blank names get joined straight into the INSERT column list and
        # produce invalid SQL (dangling commas). They carry no real data
        # (there's no table column for them to map to), so just drop them.
        columns = [c for c in raw_columns if c != ""]
        blank_count = len(raw_columns) - len(columns)
        if blank_count:
            log.warning(
                "  %-15s -> ignored %d blank/unnamed column(s) in the header of %s "
                "(trailing comma in header row?)",
                spec.table, blank_count, spec.csv_file,
            )
        pk_idx = [columns.index(c) for c in spec.pk_columns if c in columns]

        rows = []
        skipped_blank = 0
        for line_no, raw_row in enumerate(reader, start=2):  # header is line 1
            # Skip fully blank lines (e.g. a trailing newline at EOF)
            if not any((v or "").strip() for v in raw_row.values()):
                skipped_blank += 1
                continue

            row = []
            for col in columns:
                col_type = (
                    "int" if col in spec.int_columns
                    else "float" if col in spec.float_columns
                    else "text"
                )
                try:
                    value = clean_value(raw_row.get(col), col_type)
                except ValueError as e:
                    raise ValueError(
                        f"{path} line {line_no}: bad value for column '{col}' in row {raw_row}: {e}"
                    )

                limit = spec.varchar_limits.get(col)
                if limit is not None and isinstance(value, str) and len(value) > limit:
                    raise ValueError(
                        f"{path} line {line_no}: column '{col}' value {value!r} is "
                        f"{len(value)} characters, but the database column is "
                        f"VARCHAR({limit}). Shorten this value (or widen the column "
                        f"in PO_DB/sql/create_po_database.sql) and try again."
                    )

                row.append(value)

            row = tuple(row)
            if any(row[i] is None for i in pk_idx):
                raise ValueError(
                    f"{path} line {line_no}: row has a null primary key column {spec.pk_columns}: {raw_row}"
                )
            rows.append(row)

        if skipped_blank:
            log.info("  %-15s -> skipped %d blank line(s) in %s", spec.table, skipped_blank, spec.csv_file)
        return columns, rows


def ensure_fk_stubs(conn, spec: TableSpec, columns: List[str], rows: List[tuple], dry_run: bool):
    """
    Insert minimal placeholder parent rows for any FK values referenced by
    this table's data but not yet present in the parent table. This lets
    po_header / po_details load successfully even when vendor_master /
    item_master are missing or incomplete.

    Never overwrites existing data (ON CONFLICT DO NOTHING). When the real
    master file is loaded later, the normal upsert's ON CONFLICT DO UPDATE
    replaces the stub with real data automatically.
    """
    if not spec.fk_stub or not rows:
        return

    fk_col = spec.fk_stub["fk_column"]
    if fk_col not in columns:
        return
    idx = columns.index(fk_col)

    values = {r[idx] for r in rows if r[idx] is not None}
    if not values:
        return

    parent_table = spec.fk_stub["parent_table"]
    parent_pk = spec.fk_stub["parent_pk"]
    extra: Dict[str, str] = spec.fk_stub.get("extra_required", {})
    extra_cols = list(extra.keys())
    all_cols = [parent_pk] + extra_cols

    stub_rows = [tuple([v] + [extra[c] for c in extra_cols]) for v in values]
    col_list = ", ".join(all_cols)
    sql = f"INSERT INTO {parent_table} ({col_list}) VALUES %s ON CONFLICT ({parent_pk}) DO NOTHING"

    if dry_run:
        log.info(
            "  %-15s -> DRY RUN: would ensure up to %d %s reference(s) exist in %s",
            spec.table, len(stub_rows), parent_pk, parent_table,
        )
        return

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, stub_rows)
    log.info(
        "  %-15s -> ensured %d %s reference(s) exist in %s (stubbed if missing)",
        spec.table, len(stub_rows), parent_pk, parent_table,
    )


def upsert(conn, spec: TableSpec, columns: List[str], rows: List[tuple], dry_run: bool):
    if not rows:
        log.info("  %-15s -> 0 rows in file, skipped", spec.table)
        return 0

    update_cols = [c for c in columns if c not in spec.pk_columns]
    col_list = ", ".join(columns)
    pk_list = ", ".join(spec.pk_columns)

    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict_clause = f"ON CONFLICT ({pk_list}) DO UPDATE SET {set_clause}"
    else:
        # table is pure-PK, nothing to update
        conflict_clause = f"ON CONFLICT ({pk_list}) DO NOTHING"

    sql = f"INSERT INTO {spec.table} ({col_list}) VALUES %s {conflict_clause}"

    if dry_run:
        log.info("  %-15s -> DRY RUN: would upsert %d row(s)", spec.table, len(rows))
        return len(rows)

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)
    log.info("  %-15s -> upserted %d row(s)", spec.table, len(rows))
    return len(rows)


def write_error_note(data_dir: str, paths: List[str], error_message: str) -> str:
    """Called only after a rolled-back transaction / a validation failure.
    Writes a small, uniquely-named error.txt directly into data_dir (NOT a
    subfolder, and the source CSVs are left exactly where they are) so the
    next person to look at data_dir can see what went wrong. This script
    never moves or deletes the source CSVs itself - that's watch_data_folder.py's
    job, based on this process's exit code."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    note_path = os.path.join(data_dir, f"load_error_{stamp}.txt")

    present = [os.path.basename(p) for p in paths if os.path.exists(p)]
    with open(note_path, "w") as f:
        f.write(f"Load failed at: {datetime.now().isoformat()}\n")
        f.write(f"Files in this run: {', '.join(present) if present else '(none found on disk)'}\n")
        f.write("\nNothing was written to the database - the transaction was rolled back.\n")
        f.write("Source CSVs were left untouched in this folder.\n")
        f.write("\nError detail:\n")
        f.write(error_message)
        if not error_message.endswith("\n"):
            f.write("\n")

    log.error("Load failed. Wrote error detail to %s. Source file(s) left in place: %s", note_path, present)
    return note_path


def _default_config_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "config.ini")
    return "config.ini"


def run_load(data_dir: str, config_path: str, dry_run: bool = False) -> int:
    """Load/upsert CSV files. Returns 0 on success, 1 on failure."""
    db_conf = read_db_config(config_path)

    conn = None
    if not dry_run:
        try:
            conn = psycopg2.connect(**db_conf)
        except psycopg2.OperationalError as e:
            log.error("Could not connect to database: %s", e)
            return 1

    total = 0
    run_paths: List[str] = []

    try:
        log.info("Starting load from: %s", data_dir)
        for spec in TABLE_SPECS:
            path = os.path.join(data_dir, spec.csv_file)
            if not os.path.exists(path):
                log.warning("  %-15s -> file not found (%s), skipped", spec.table, spec.csv_file)
                continue

            run_paths.append(path)
            columns, rows = load_csv_rows(spec, path)
            ensure_fk_stubs(conn, spec, columns, rows, dry_run)
            total += upsert(conn, spec, columns, rows, dry_run)

        if conn:
            conn.commit()

        log.info(
            "Done. %d row(s) processed. %s",
            total,
            "(dry run - nothing written)" if dry_run else "Committed.",
        )
        return 0

    except Exception as e:
        if conn:
            conn.rollback()
        log.error("Load failed, DB transaction rolled back. Reason: %s", e)

        if not dry_run and run_paths:
            write_error_note(data_dir, run_paths, traceback.format_exc())

        return 1
    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Load/upsert PO_DB CSV files.")
    parser.add_argument("--data-dir", required=True, help="Folder containing the CSV files")
    parser.add_argument("--config", default=_default_config_path(), help="Path to DB config file")
    parser.add_argument("--dry-run", action="store_true", help="Validate and preview only, no DB writes")
    args = parser.parse_args()

    raise SystemExit(run_load(args.data_dir, args.config, args.dry_run))


if __name__ == "__main__":
    main()