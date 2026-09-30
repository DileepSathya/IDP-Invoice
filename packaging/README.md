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

The API and watcher are collected into one `idp-services` directory so their
PaddleOCR, OpenCV, NumPy, and Python runtime files are stored once. The bundled
MongoDB payload contains `mongod.exe` and the Visual C++ redistributable only;
server debug symbols and the unused `mongos` executable are excluded. The build
uses a pinned PyInstaller release so rebuilding the same revision does not
silently switch bundler versions.

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
  idp-services/idp-api.exe
  idp-services/idp-watcher.exe
  idp-services/_internal/  ← shared Python/OCR dependencies
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

1. Run **Start IDP Invoice.exe** and sign in at `http://127.0.0.1:8000/login` (`IDP_admin` / `idpadmin@123`)
2. **Settings → AI** — save Gemini API key (MongoDB)
3. **Settings → Licensing** — paste license key (MongoDB)
4. Configure `tally-bridge\.env` (`TALLY_URL`, `TALLY_COMPANY`)
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

The service spec excludes unused Torch/vector-embedding stacks. The MongoDB
runtime adds roughly 85 MB before installer compression; `.pdb`, `mongos.exe`,
and Compass installation files are not shipped.

## Create the single setup EXE

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) once on the Windows
build machine. Then run this from the repository root:

```powershell
.\packaging\build_installer.ps1 -Version "1.0.0"
```

That command rebuilds the frontend, PyInstaller programs, MongoDB runtime, and
then compiles:

```text
dist\installer\IDP-Invoice-Setup.exe
```

The installer uses per-user installation and does not require administrator
access for the application itself. It installs to:

```text
%LOCALAPPDATA%\Programs\IDP Invoice
```

It creates all MongoDB, invoice queue, and log directories, plus Start Menu and
optional desktop shortcuts. Existing `.env`, `tally-bridge\.env`, MongoDB
license/AI settings, `invoice_count.enc`, `license_state.json`, MongoDB data,
invoices, and logs are preserved during upgrades and uninstall. Immutable
application directories are replaced on upgrade so removed modules and frontend
assets cannot remain stale.

### Rebuild after future code changes

For every release, update the version and run the same command:

```powershell
.\packaging\build_installer.ps1 -Version "1.1.0"
```

Useful faster variants:

```powershell
# Reuse an already-built frontend.
.\packaging\build_installer.ps1 -Version "1.1.0" -SkipFrontend

# Compile only the installer from an already-complete dist\IDP-Invoice payload.
.\packaging\build_installer.ps1 -Version "1.1.0" -SkipAppBuild

# If Inno Setup is installed in a non-standard directory.
.\packaging\build_installer.ps1 -Version "1.1.0" `
  -InnoCompiler "D:\Tools\Inno Setup 6\ISCC.exe"
```

Do not use `-SkipAppBuild` after changing Python, frontend, dependencies, or
packaging files. Use it only when the portable payload has already been rebuilt
and validated.

### Configuration and customer upgrades

- License and Gemini API keys are configured after install via **Settings → Licensing**
  and **Settings → AI** (stored in MongoDB, not shipped in the installer).
- The setup copies `.env.example` as `.env` only on the first installation.
- Installing a newer setup over the same location updates program binaries but
  retains customer configuration and operational data.
- The Visual C++ runtime installer is run only if its x64 registry marker is
  missing; Windows may request elevation for that prerequisite.

### Verify the packaging rules

```powershell
node --test packaging\tests\test_release_packaging.cjs
```

See `licensing/README.md` for offline licensing.

The packaged fingerprint tool queries the CPU Processor ID and the physical
serial number of the Windows system disk through PowerShell CIM. Build and
distribute a new `fingerprint_tool.exe` with this release; fingerprints and
licenses made with the former MAC/WMIC algorithm must be replaced through
`IDP-lic_website` before licensed processing can resume.
