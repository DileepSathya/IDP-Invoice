PO_DB — bundled ERP reference database
======================================

This folder contains a self-contained PostgreSQL instance plus tools to load
vendor/item/PO master data from CSV files.

Layout
------
  pgsql/              Bundled PostgreSQL server (bin, lib, share)
  pgdata/             PostgreSQL cluster data files (created on first init)
  sql/                Database creation script
  templates/          Sample CSV templates
  data/               Drop customer CSVs here for the watcher
  config.ini          Loader/watcher database credentials
  po-loader.exe       One-shot CSV loader
  po-watcher.exe      Polls data/ and runs po-loader.exe
  init-po-db.bat      First-run: init Postgres + create PO_DB schema
  run-watcher.bat     Start the CSV watcher manually

First run
---------
1. Run init-po-db.bat once (or start Start IDP Invoice.exe — it auto-inits).
2. Copy CSV templates from templates\ if needed.
3. Drop vendor_master.csv, item_master.csv, po_header.csv, po_details.csv
   into data\.
4. The main launcher starts po-watcher.exe when PO_DB_WATCHER_ENABLED=true
   (default). Or run run-watcher.bat manually.

Main app connection
-------------------
The IDP API reads PO_DB using POSTGRES_* settings in the root .env file.
Defaults (localhost:5432, db PO_DB, user postgres) work with the bundled
PostgreSQL using trust auth on 127.0.0.1.

To use an external Postgres server instead, set POSTGRES_HOST to that host
and IDP_USE_BUNDLED_POSTGRES=0 in the root .env.
