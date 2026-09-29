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

3. Send the generated license key (the base64 string in the `.lic` file). The customer pastes it under **Settings → Licensing** in the web UI.

## Customer activation

1. Start the application (MongoDB and API start even without a license).
2. Log in to the dashboard.
3. Open **Settings → Licensing**.
4. Paste the full license key and click **Save license key**.

The key is stored in MongoDB (`license_settings` collection) and re-validated on each API/watcher start and after save.

Legacy installs: if `license.lic` exists at the install root but MongoDB has no key yet, the first successful read **migrates** the file contents into MongoDB automatically.

## Quota and usage

- Quota plans reconcile invoice counts from MongoDB (`created_at` since license `issuedAt`).
- `invoice_count.enc` at the install root remains an optional encrypted fallback when MongoDB is temporarily unavailable.

## Validation (automatic)

- API lifespan refreshes license state from MongoDB on startup.
- Invoice processing (uploads, watcher, API jobs) requires a **valid active license**.
- Without a license, the UI and settings remain available; only licensed processing is restricted.

## Development bypass

In `.env`:

```env
IDP_SKIP_LICENSE=1
```

## PyInstaller notes

- Use **onedir** for main app (Paddle/OCR size) — already configured.
- Portable launcher starts MongoDB before API services so license keys can be read from MongoDB.
