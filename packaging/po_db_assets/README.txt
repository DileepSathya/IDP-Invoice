PO_DB — bundled ERP reference database
======================================

Run po-db.exe to:
  1. Sync settings from the root .env
  2. Start bundled PostgreSQL (IDP_USE_BUNDLED_POSTGRES=1) or connect to external Postgres (0)
  3. Create the PO_DB database and tables when missing
  4. Start watching po-db\data\ for CSV files and load them automatically

Layout
------
  po-db.exe           Single service executable (setup + watcher)
  start-po-db.bat     Optional wrapper that runs po-db.exe
  pgsql/              Bundled PostgreSQL server (bin, lib, share)
  pgdata/             Bundled PostgreSQL cluster data (created on first init)
  sql/                Database creation script
  templates/          Sample CSV templates
  data/               Drop customer CSVs here
  config.ini          Loader/watcher database credentials

Usage
-----
  po-db.exe                 Setup Postgres + schema, then watch data\
  po-db.exe --setup-only    Setup only (no watcher)
  po-db.exe --once          Setup, run one CSV check/load, exit

Bundled vs external PostgreSQL
------------------------------
  IDP_USE_BUNDLED_POSTGRES=1  Use po-db\pgsql\ (default). If port 5432 is busy,
                              bundled Postgres automatically uses 15432+ instead.
                              On first run, po-db.exe creates POSTGRES_USER and the
                              IDP database automatically (connects as postgres superuser).
  IDP_USE_BUNDLED_POSTGRES=0  Use POSTGRES_* from the root .env against your own server.
                              If POSTGRES_USER does not exist yet, set POSTGRES_ADMIN_USER
                              and POSTGRES_ADMIN_PASSWORD (e.g. postgres) so po-db.exe can
                              provision the application role and database.

Main app connection
-------------------
The IDP API reads PO_DB using POSTGRES_* in the root .env file. po-db.exe keeps
po-db\config.ini in sync with those values on startup.
