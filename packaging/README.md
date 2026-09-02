# IDP Invoice — Windows packaging

Portable build output: `dist/IDP-Invoice/`

## Prerequisites

- Python 3.11 venv with project dependencies (`install.bat`)
- Node.js (for frontend build)
- Internet on first build (downloads MongoDB zip archive once)

## Build

```powershell
.\packaging\build.ps1
```

Options:

- `-SkipFrontend` — reuse existing `frontend/dist`
- `-SkipPyInstaller` — assemble `dist/IDP-Invoice` without rebuilding exes
- `-SkipMongoDB` — skip downloading/copying bundled MongoDB
- `-MongoVersion 7.0.14` — pin MongoDB Community version

Fast rebuild after launcher-only changes:

```powershell
.\packaging\build.ps1 -SkipFrontend -SkipMongoDB
```

## Output layout

```
dist/IDP-Invoice/
  Start IDP Invoice.exe    ← launcher (MongoDB + watcher + API + Tally bridge)
  mongodb/bin/mongod.exe
  data/db/                 ← MongoDB (invoices + Tally master cache)
  idp-api/idp-api.exe
  idp-watcher/idp-watcher.exe
  tally-bridge/tally-bridge.exe
  tally-bridge/xml_scripts/
  frontend/
  .env.example
  invoices_data/ ...
  logs/
```

MongoDB collections for Tally ERP matching (populated via **Settings → Tally Master Data → Refresh** or the scheduler):

- `tally_vendor_master`, `tally_item_master`, `tally_expense_ledger_master`, `tally_po_header`, `tally_po_details`
- Scheduler settings: `erp_settings` document `_id: tally_master_scheduler` (mode, frequency, rematch flag)

The Tally master refresh **scheduler runs inside `idp-api.exe`** (same process as the ERP sync scheduler). No extra Windows service or `.env` key is required — configure it from **Settings → Tally Master Data** after deploy.

## First run (end user)

1. Set `GEMINI_API_KEY` in `.env`
2. Configure `tally-bridge\.env` (`TALLY_URL`, `TALLY_COMPANY`)
3. Run **Start IDP Invoice.exe**
4. Sign in at `http://127.0.0.1:8000/login` (`IDP_admin` / `idpadmin@123`)
5. **Settings → Tally Master Data** — manual Refresh or **Scheduled** refresh (minutes); configure purchase ledger under Ledger Settings
6. **ERP → Force Re-match** after a master refresh if scheduled rematch is off
7. Open **Health** to verify services

### Tally master refresh scheduler

Configured in **Settings → Tally Master Data** (stored in MongoDB, not `.env`):

| Mode | Behavior |
|---|---|
| **Manual only** (default) | Master data refreshes only when you click **Refresh from Tally** |
| **Scheduled** | Background scheduler inside `idp-api.exe` pulls vendors/items/POs every N minutes |

The manual **Refresh from Tally** button remains available in both modes. Scheduler settings survive restarts and portable upgrades (MongoDB `erp_settings` doc `tally_master_scheduler`). No extra Windows service is required — the scheduler starts with the API when you run **Start IDP Invoice.exe**.

### Purchase order for Tally push (`.env`)

| `MANDATORY_PURCHASE_ORDER` | Behavior |
|---|---|
| `1` (default) | PO ID required — missing PO blocks HITL clear and Tally push |
| `0` | PO optional — missing PO is stored as **Not applicable** and vouchers push without PO order matching |

The portable build copies `.env.example` (includes `MANDATORY_PURCHASE_ORDER`) into `dist/IDP-Invoice/.env` on first build; the launcher merges any new keys from `.env.example` on startup.

When `TALLY_ENABLED=true`, the launcher starts MongoDB (if bundled), tally-bridge, watcher, and API.
TallyPrime must be running separately with the target company open.

For **MongoDB Atlas**, set `MONGO_URI` and `IDP_USE_BUNDLED_MONGO=0`.

## Development (serve UI from API)

```powershell
cd frontend && npm run build
cd ..
venv\Scripts\python.exe -m backend.run_api
```

Tally bridge (separate terminal):

```powershell
venv\Scripts\python.exe -m backend.run_tally_bridge
```

Or use `run_production.bat` option 5.

## Bundle size

The API spec excludes unused vector-embedding stacks. Bundled MongoDB adds ~100–150 MB.

See `licensing/README.md` for offline licensing.
