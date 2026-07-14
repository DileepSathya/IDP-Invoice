# PO_DB CSV Loader

A small Python CLI app that loads the customer's CSV files into `PO_DB`.
Same script works for the **first** (full) load and every **incremental**
load after that — existing rows get updated, new rows get inserted,
nothing is deleted.

## 1. Setup

```bash
pip install -r requirements.txt
```

Edit `config.ini` with your real database credentials:

```ini
[database]
host = localhost
port = 5432
dbname = PO_DB
user = postgres
password = your_password_here
```

Make sure `PO_DB` and its tables already exist (run `create_po_database.sql`
first if you haven't).

## 2. Folder of CSVs

Put the customer's CSVs in a folder, e.g. `./data/`:

```
data/
  vendor_master.csv
  item_master.csv
  po_header.csv
  po_details.csv
```

You don't need all four every time — from the second run onward the
customer can send just the file(s) that changed (e.g. only
`po_details.csv` with 3 new lines). Missing files are skipped, not
treated as errors.

## 3. Run it

```bash
python load_data.py --data-dir ./data --config config.ini
```

Preview without writing to the database:

```bash
python load_data.py --data-dir ./data --config config.ini --dry-run
```

## 4. What it does

- Reads each CSV, matching columns by header name.
- Blank cells become `NULL`.
- Loads tables in FK-safe order: `vendor_master` → `item_master` →
  `po_header` → `po_details`.
- For each row: if the primary key already exists, updates that row;
  otherwise inserts it (`INSERT ... ON CONFLICT ... DO UPDATE`).
- Everything runs in a single transaction — if any row fails, the whole
  run is rolled back and nothing is partially applied.
- Logs a summary of rows processed per table.

## Notes

- This only inserts/updates — it never deletes. If the customer needs to
  cancel/remove a PO line item, that needs a status column or a separate
  step (ask if you want this added).
- CSV column headers must match the table column names exactly (see the
  template CSVs already provided: `vendor_master.csv`, `item_master.csv`,
  `po_header.csv`, `po_details.csv`).
