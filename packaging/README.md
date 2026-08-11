# IDP Invoice — Windows packaging

Portable build output: `dist/IDP-Invoice/`

## Prerequisites

- Python 3.11 venv with project dependencies (`install.bat`)
- Node.js (for frontend build)
- Internet on first build (downloads MongoDB and PostgreSQL zip archives once)

## Build

```powershell
.\packaging\build.ps1
```

Options:

- `-SkipFrontend` — reuse existing `frontend/dist`
- `-SkipPyInstaller` — only assemble `dist/IDP-Invoice` (frontend + MongoDB + PO_DB assets; skips frozen exes)
- `-SkipMongoDB` — skip downloading/copying bundled MongoDB
- `-SkipPoDB` — skip the entire `po-db/` bundle
- `-SkipPostgreSQL` — bundle PO_DB loader/watcher only (no PostgreSQL binaries)
- `-MongoVersion 7.0.14` — pin MongoDB Community version
- `-PostgresVersion 16.14` — pin PostgreSQL binary version

Fast rebuild after launcher-only changes:

```powershell
.\packaging\build.ps1 -SkipFrontend -SkipMongoDB -SkipPoDB
```

Bundle MongoDB into an existing dist without rebuilding exes:

```powershell
.\packaging\bundle_mongodb.ps1
```

Bundle PO_DB (PostgreSQL + loader/watcher) into an existing dist:

```powershell
.\packaging\bundle_po_db.ps1
```

## Output layout

```
dist/IDP-Invoice/
  Start IDP Invoice.exe    ← launcher (MongoDB + PostgreSQL + watchers + API + browser)
  mongodb/bin/mongod.exe   ← bundled MongoDB server
  data/db/                 ← MongoDB data files (created on first run)
  po-db/                   ← bundled ERP reference database (PostgreSQL + CSV tools)
    pgsql/                 ← PostgreSQL server (bin, lib, share)
    pgdata/                ← PostgreSQL cluster data (created on first run)
    data/                  ← CSV drop folder for po-watcher.exe
    sql/create_po_database.sql
    templates/             ← sample CSV templates
    config.ini             ← loader/watcher DB credentials
    po-loader.exe          ← one-shot CSV loader
    po-watcher.exe         ← polls data/ and runs po-loader.exe
    init-po-db.bat         ← manual first-run DB init (launcher auto-inits too)
    run-watcher.bat        ← start CSV watcher manually
  idp-api/idp-api.exe
  idp-watcher/idp-watcher.exe
  tally-bridge/tally-bridge.exe   ← Tally voucher bridge (port 8001)
  tally-bridge/xml_scripts/       ← voucher XML template
  tally-bridge/.env.example       ← TallyPrime URL + company name
  frontend/                ← built React UI
  .env.example
  invoices_data/
    to_be_processed/       ← drop files here (watcher input only)
    _api_staging/          ← temporary UI/API upload staging (not watched)
    HITL_pending/          ← files awaiting human review
    ERROR/                 ← pipeline / extraction failures
    Completed/             ← finished invoices (incl. after HITL)
  logs/                    ← idp.log, mongod.log
```

## First run (end user)

1. Copy `.env.example` → `.env`
2. Set `GEMINI_API_KEY`
3. Keep `MONGO_URI=mongodb://localhost:27017` to use bundled MongoDB
4. Keep `POSTGRES_HOST=localhost` to use bundled PostgreSQL in `po-db\` (default).
   On first launch the launcher initializes Postgres, creates the `PO_DB` database,
   and applies the schema automatically.
5. Drop ERP CSV files (`vendor_master.csv`, `item_master.csv`, `po_header.csv`,
   `po_details.csv`) into `po-db\data\`. The launcher starts `po-watcher.exe` by
   default (`PO_DB_WATCHER_ENABLED=true`).
6. (Optional) To push matched invoices to TallyPrime, set `TALLY_ENABLED=true` in `.env`
   and edit `tally-bridge\.env`:
   - `TALLY_URL=http://localhost:9000` (TallyPrime HTTP port)
   - `TALLY_COMPANY=` exact company name open in TallyPrime
   - Use the same `MONGO_URI` / `MONGO_DB` as the main app
7. Double-click **Start IDP Invoice.exe**
8. Browser opens at `http://localhost:8000`
9. Drop invoice files in `invoices_data/to_be_processed/` (or upload via the UI)

When `TALLY_ENABLED=true`, the launcher also starts `tally-bridge.exe` on port 8001.
TallyPrime must be running separately with the target company open.

The launcher starts bundled MongoDB automatically when:

- `MONGO_URI` points to `localhost` or `127.0.0.1`
- `IDP_USE_BUNDLED_MONGO` is not disabled
- `mongodb/bin/mongod.exe` exists in the portable folder

The launcher starts bundled PostgreSQL automatically when:

- `POSTGRES_HOST` is `localhost` or `127.0.0.1`
- `IDP_USE_BUNDLED_POSTGRES` is not disabled
- `po-db/pgsql/bin/postgres.exe` exists in the portable folder

If either database is already running on the configured port, the launcher reuses it.

For **MongoDB Atlas**, set `MONGO_URI` to your cloud connection string and `IDP_USE_BUNDLED_MONGO=0`.

For an **external PostgreSQL** server, set `POSTGRES_HOST` to that host and `IDP_USE_BUNDLED_POSTGRES=0`.

To disable the CSV watcher while keeping ERP matching, set `PO_DB_WATCHER_ENABLED=false`.

## Development (serve UI from API without PyInstaller)

```powershell
cd frontend && npm run build
cd ..
venv\Scripts\python.exe -m backend.run_api
```

Tally bridge (separate terminal, when `TALLY_ENABLED=true`):

```powershell
venv\Scripts\python.exe -m backend.run_tally_bridge
```

PO_DB loader/watcher (development, from repo):

```powershell
cd PO_DB
PO_DB\run.bat
```

Or use `run_production.bat` option 5 — it starts the bridge automatically when `TALLY_ENABLED=true` in `.env`.

The API serves `frontend/dist` when present and maps `/api/*` to backend routes.

## Bundle size

The API spec intentionally excludes unused vector-embedding stacks (`torch`, `transformers`,
`sentence_transformers`, `llama_index`). The chatbot uses MongoDB keyword retrieval + Gemini only.

Bundled PostgreSQL adds roughly 150–250 MB to the portable folder (cached after first download).

## Offline licensing

See `licensing/README.md` for key generation, customer fingerprint tool, and `license.lic` workflow.
Run `python keygen/generate_keys.py` once before shipping builds.

## Paddle / OCR in frozen builds

PyInstaller specs collect Paddle native DLLs into `idp-api/_internal/paddle/libs/` and use
`packaging/pyi_rth_paddle.py` to add that folder to the Windows DLL search path before import.

If OCR still fails on a clean machine, install
[Microsoft Visual C++ 2015–2022 Redistributable (x64)](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
