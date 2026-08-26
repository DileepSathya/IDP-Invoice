# IDP-Invoice

Intelligence Document Processing for invoices — OCR/extraction, Tally ERP matching, HITL review, and optional Tally voucher push.

## Build (portable Windows app)

```powershell
.\packaging\build.ps1
```

Output: `dist/IDP-Invoice/` including **Start IDP Invoice.exe**.

## Configure before first use

### Main app (`.env`)

| Setting | Purpose |
|---------|---------|
| `GEMINI_API_KEY` | Invoice extraction |
| `MONGO_URI` | Invoice + Tally master data storage |
| `TALLY_ENABLED=true` | ERP matching and voucher push |
| `TALLY_BRIDGE_URL=http://localhost:8001` | Tally bridge service |
| `MANDATORY_PURCHASE_ORDER=0` | Allow Tally push without PO (stored as **Not applicable**) |
| `MANDATORY_PURCHASE_ORDER=1` | Block Tally push when PO ID is missing (default) |

### Tally bridge (`TALLY INTEGRATION/.env` or `dist/IDP-Invoice/tally-bridge/.env`)

| Setting | Purpose |
|---------|---------|
| `TALLY_URL=http://localhost:9000` | TallyPrime HTTP port |
| `TALLY_COMPANY` | Exact company name open in Tally |
| `TALLY_VOUCHER_TYPE=Purchase` | Voucher type for imports |

Place `license.lic` next to the executable (see `licensing/README.md`).

## First-run workflow

1. Start Tally Prime with the target company open (HTTP port 9000 enabled).
2. Start the app (`Start IDP Invoice.exe` or `run_production.bat` option 5).
3. Sign in: **IDP_admin** / **idpadmin@123**
4. **Settings → Tally Master Data** — refresh from Tally (manual or scheduled), then pick purchase ledger under Ledger Settings
5. **ERP → Force Re-match** re-matches all invoices after a master refresh (optional checkbox on scheduled refresh)

The Tally master refresh scheduler runs inside the API process (dev and portable build) — configure it on **Settings → Tally Master Data**; no extra service or `.env` key is needed.

Matching uses rapidfuzz. Threshold: `ERP_MATCH_THRESHOLD` (default 70).

## Development

```powershell
.\install.bat
.\run_production.bat   # option 5: API + frontend + watcher + tally bridge
```

Tally bridge only:

```powershell
venv\Scripts\python.exe -m backend.run_tally_bridge
```
