# IDP Invoice — Windows packaging

Portable build output: `dist/IDP-Invoice/`

## Prerequisites

- Python 3.11 venv with project dependencies (`install.bat`)
- Node.js (for frontend build)
- Internet on first build (downloads MongoDB Community Server zip once)

## Build

```powershell
.\packaging\build.ps1
```

Options:

- `-SkipFrontend` — reuse existing `frontend/dist`
- `-SkipPyInstaller` — only assemble `dist/IDP-Invoice` (frontend + MongoDB)
- `-SkipMongoDB` — skip downloading/copying bundled MongoDB
- `-MongoVersion 7.0.14` — pin MongoDB Community version

Fast rebuild after launcher-only changes:

```powershell
.\packaging\build.ps1 -SkipFrontend -SkipMongoDB
```

Bundle MongoDB into an existing dist without rebuilding exes:

```powershell
.\packaging\bundle_mongodb.ps1
```

## Output layout

```
dist/IDP-Invoice/
  Start IDP Invoice.exe    ← launcher (MongoDB + watcher + API + browser)
  mongodb/bin/mongod.exe   ← bundled database server
  data/db/                 ← MongoDB data files (created on first run)
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
4. (Optional) To enable ERP/PO matching, fill in `POSTGRES_HOST`, `POSTGRES_USER`, and
   `POSTGRES_PASSWORD` with your own Postgres credentials — these ship blank on
   purpose, so ERP matching stays off until you set them yourself.
5. (Optional) To push matched invoices to TallyPrime, set `TALLY_ENABLED=true` in `.env`
   and edit `tally-bridge\.env`:
   - `TALLY_URL=http://localhost:9000` (TallyPrime HTTP port)
   - `TALLY_COMPANY=` exact company name open in TallyPrime
   - Use the same `MONGO_URI` / `MONGO_DB` as the main app
6. Double-click **Start IDP Invoice.exe**
7. Browser opens at `http://localhost:8000`
8. Drop invoice files in `invoices_data/to_be_processed/` (or upload via the UI)

When `TALLY_ENABLED=true`, the launcher also starts `tally-bridge.exe` on port 8001.
TallyPrime must be running separately with the target company open.

The launcher starts bundled MongoDB automatically when:

- `MONGO_URI` points to `localhost` or `127.0.0.1`
- `IDP_USE_BUNDLED_MONGO` is not disabled
- `mongodb/bin/mongod.exe` exists in the portable folder

If MongoDB is already running on that port, the launcher reuses it.

For **MongoDB Atlas** or another remote server, set `MONGO_URI` to your cloud connection string and set `IDP_USE_BUNDLED_MONGO=0`.

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

Or use `run_production.bat` option 5 — it starts the bridge automatically when `TALLY_ENABLED=true` in `.env`.

The API serves `frontend/dist` when present and maps `/api/*` to backend routes.

## Bundle size

The API spec intentionally excludes unused vector-embedding stacks (`torch`, `transformers`,
`sentence_transformers`, `llama_index`). The chatbot uses MongoDB keyword retrieval + Gemini only.

## Offline licensing

See `licensing/README.md` for key generation, customer fingerprint tool, and `license.lic` workflow.
Run `python keygen/generate_keys.py` once before shipping builds.

## Paddle / OCR in frozen builds

PyInstaller specs collect Paddle native DLLs into `idp-api/_internal/paddle/libs/` and use
`packaging/pyi_rth_paddle.py` to add that folder to the Windows DLL search path before import.

If OCR still fails on a clean machine, install
[Microsoft Visual C++ 2015–2022 Redistributable (x64)](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
