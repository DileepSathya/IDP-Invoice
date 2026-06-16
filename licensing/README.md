# Offline license system (Windows)

Completely **offline** — no license server or internet required for validation.

## One-time vendor setup

```powershell
venv\Scripts\pip install cryptography
venv\Scripts\python.exe keygen\generate_keys.py
```

This creates:

- `keygen/private_key.pem` — **never share, never commit, never bundle in exe**
- `keygen/public_key.pem` — reference copy
- `licensing/public_key_embed.py` — public key embedded in customer exes

Rebuild portable app after generating keys:

```powershell
.\packaging\build.ps1 -SkipMongoDB
```

Build customer fingerprint tool:

```powershell
venv\Scripts\python.exe -m PyInstaller packaging\fingerprint_tool.spec --distpath dist\IDP-Invoice --workpath build\pyinstaller --noconfirm
```

Output: `dist\IDP-Invoice\fingerprint_tool.exe`

## Issue a license (vendor)

1. Customer runs `fingerprint_tool.exe` and sends you the fingerprint hash.
2. You run:

```powershell
venv\Scripts\python.exe keygen\license_generator.py `
  --customer ACME `
  --fingerprint <hash-from-customer> `
  --plan yearly `
  --limit 500 `
  --expires 2027-12-31
```

3. Send the generated `ACME.lic` file. Customer renames/copies it to **`license.lic`**.

## Customer install layout

```
dist\IDP-Invoice\
  Start IDP Invoice.exe
  license.lic              ← required
  invoice_count.enc        ← auto-created (encrypted quota counter)
  idp-api\
  idp-watcher\
  ...
```

## Validation (automatic)

On startup, these entry points call `validate_license()`:

- `Start IDP Invoice.exe` (launcher)
- `idp-api.exe`
- `idp-watcher.exe`

After each successful invoice stored to MongoDB, `increment_invoice_count()` runs.

## Development bypass

In `.env`:

```env
IDP_SKIP_LICENSE=1
```

## PyInstaller notes

- Use **onedir** for main app (Paddle/OCR size) — already configured.
- `license.lic` and `invoice_count.enc` live next to the exe (`sys.executable` parent), not inside `_internal`.
- **Single-file** `Start IDP Invoice.exe` still resolves paths correctly for `license.lic`.
