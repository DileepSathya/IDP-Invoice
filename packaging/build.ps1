# Build portable IDP Invoice distribution into dist/IDP-Invoice/
param(
    [switch]$SkipFrontend,
    [switch]$SkipPyInstaller,
    [switch]$SkipMongoDB,
    [switch]$SkipPoDB,
    [switch]$SkipPostgreSQL,
    [string]$MongoVersion = "7.0.14",
    [string]$PostgresVersion = "16.14"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DistRoot = Join-Path $Root "dist\IDP-Invoice"
$BuildWork = Join-Path $Root "build\pyinstaller"

Set-Location $Root
Write-Host "==== IDP Invoice - Windows packaging ===="
Write-Host "Root: $Root"
Write-Host "Output: $DistRoot"

function Resolve-Python {
    $venvPy = Join-Path $Root "venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    $altVenvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $altVenvPy) { return $altVenvPy }
    throw "Python venv not found. Run install.bat first."
}

$Py = Resolve-Python
Write-Host "Using Python: $Py"

function Sync-EnvFromExample {
    param(
        [Parameter(Mandatory = $true)][string]$EnvPath,
        [Parameter(Mandatory = $true)][string]$ExamplePath
    )
    if (-not (Test-Path $ExamplePath)) { return @() }
    if (-not (Test-Path $EnvPath)) {
        Copy-Item -Force $ExamplePath $EnvPath
        return @("<created>")
    }
    $existing = Get-Content $EnvPath -Raw
    $added = @()
    foreach ($line in Get-Content $ExamplePath) {
        if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
        if ($line -match '^\s*([^=]+?)=') {
            $key = $matches[1].Trim()
            if ($existing -notmatch "(?m)^\s*$([regex]::Escape($key))\s*=") {
                Add-Content -Path $EnvPath -Value $line
                $added += $key
            }
        }
    }
    return $added
}

function Test-TallyVoucherTemplate {
    param([Parameter(Mandatory = $true)][string]$TemplatePath)
    if (-not (Test-Path $TemplatePath)) {
        throw "Tally voucher template missing: $TemplatePath"
    }
    $content = Get-Content $TemplatePath -Raw
    if ($content -notmatch '<VOUCHERTYPENAME>\{VOUCHER_TYPE\}</VOUCHERTYPENAME>') {
        throw 'create_voucher.xml is missing voucher type placeholder (<VOUCHERTYPENAME>{VOUCHER_TYPE}</VOUCHERTYPENAME>).'
    }
    if ($content -match '<CLASSNAME>') {
        throw 'create_voucher.xml still contains <CLASSNAME> - remove voucher-class tags for ledger-based import.'
    }
}

if (-not $SkipFrontend) {
    Write-Host "`n[1/6] Building frontend..."
    Push-Location (Join-Path $Root "frontend")
    if (-not (Test-Path "node_modules")) {
        npm install
    }
    npm run build
    if (-not (Test-Path "dist\index.html")) {
        throw "Frontend build failed - dist\index.html missing."
    }
    Pop-Location
} else {
    Write-Host "`n[1/6] Skipping frontend build."
}

if (-not $SkipPyInstaller) {
    Write-Host "`n[2/6] Installing PyInstaller..."
    & $Py -m pip install --upgrade pyinstaller

    $tallyBridgeSrc = Join-Path $Root "TALLY INTEGRATION\api_server.py"
    if (-not (Test-Path $tallyBridgeSrc)) {
        throw "TALLY INTEGRATION source not found ($tallyBridgeSrc). Clone or restore the Tally bridge folder before building."
    }

    Write-Host "`n[3/6] Running PyInstaller (API, watcher, launcher, tally-bridge)..."
    New-Item -ItemType Directory -Force -Path $DistRoot, $BuildWork | Out-Null

    $commonArgs = @(
        "--distpath", $DistRoot,
        "--workpath", $BuildWork,
        "--noconfirm"
    )

    & $Py -m PyInstaller @commonArgs (Join-Path $Root "packaging\idp_api.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for idp_api.spec" }

    & $Py -m PyInstaller @commonArgs (Join-Path $Root "packaging\idp_watcher.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for idp_watcher.spec" }

    & $Py -m PyInstaller @commonArgs (Join-Path $Root "packaging\idp_tally_bridge.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for idp_tally_bridge.spec" }

    & $Py -m PyInstaller @commonArgs (Join-Path $Root "packaging\idp_launcher.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for idp_launcher.spec" }
} else {
    Write-Host "`n[2/6] Skipping PyInstaller."
    Write-Host "`n[3/6] Skipping PyInstaller."
}

Write-Host "`n[4/6] Assembling portable folder..."
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null

$frontendOut = Join-Path $DistRoot "frontend"
$frontendSrc = Join-Path $Root "frontend\dist"
if (Test-Path $frontendSrc) {
    if (Test-Path $frontendOut) { Remove-Item -Recurse -Force $frontendOut }
    Copy-Item -Recurse $frontendSrc $frontendOut
} else {
    Write-Host "[WARN] frontend\dist not found - UI will be API-only until you run npm run build."
}

$envExample = Join-Path $DistRoot ".env.example"
$envTarget = Join-Path $DistRoot ".env"
Copy-Item -Force (Join-Path $Root ".env.example") $envExample
if (-not (Test-Path $envTarget)) {
    Copy-Item -Force $envExample $envTarget
    Write-Host "Created .env from .env.example in dist (set GEMINI_API_KEY before processing invoices)."
} else {
    Write-Host "Kept existing dist .env (not overwritten)."
}

Write-Host "`nResetting stored data (MongoDB data dir, invoice files, logs) for a clean build..."
Write-Host "Make sure IDP Invoice / mongod are not running against $DistRoot before continuing."
foreach ($staleDir in @("data", "invoices_data", "logs")) {
    $stalePath = Join-Path $DistRoot $staleDir
    if (Test-Path $stalePath) {
        Remove-Item -Recurse -Force $stalePath
    }
}

foreach ($dir in @(
        "logs", "data\db", "invoices_data",
        "invoices_data\to_be_processed", "invoices_data\_api_staging",
        "invoices_data\HITL_pending", "invoices_data\gemini_api_error",
        "invoices_data\ERROR", "invoices_data\Completed",
        "tally-bridge\xml_scripts"
    )) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot $dir) | Out-Null
}

$tallyBridgeRoot = Join-Path $DistRoot "tally-bridge"
$tallyXmlSrc = Join-Path $Root "TALLY INTEGRATION\xml_scripts"
$tallyXmlDst = Join-Path $tallyBridgeRoot "xml_scripts"
if (Test-Path $tallyXmlSrc) {
    if (Test-Path $tallyXmlDst) { Remove-Item -Recurse -Force $tallyXmlDst }
    Copy-Item -Recurse $tallyXmlSrc $tallyXmlDst
    Test-TallyVoucherTemplate -TemplatePath (Join-Path $tallyXmlDst "create_voucher.xml")
}
$tallyEnvExampleSrc = Join-Path $Root "TALLY INTEGRATION\.env.example"
$tallyEnvExampleDst = Join-Path $tallyBridgeRoot ".env.example"
$tallyEnvDst = Join-Path $tallyBridgeRoot ".env"
if (Test-Path $tallyEnvExampleSrc) {
    Copy-Item -Force $tallyEnvExampleSrc $tallyEnvExampleDst
    $addedKeys = Sync-EnvFromExample -EnvPath $tallyEnvDst -ExamplePath $tallyEnvExampleDst
    if ($addedKeys -contains "<created>") {
        Write-Host "Created tally-bridge\.env from .env.example (set TALLY_COMPANY, TALLY_URL, TALLY_VOUCHER_TYPE, TALLY_PURCHASE_LEDGER)."
    } elseif ($addedKeys.Count -gt 0) {
        Write-Host "Merged missing tally-bridge\.env keys: $($addedKeys -join ', ')"
    } else {
        Write-Host "Kept existing tally-bridge\.env (required keys already present)."
    }
    $syncEnvSrc = Join-Path $Root "TALLY INTEGRATION\sync_env.py"
    if (Test-Path $syncEnvSrc) {
        Copy-Item -Force $syncEnvSrc (Join-Path $tallyBridgeRoot "sync_env.py")
    }
    if (Test-Path $tallyEnvDst) {
        $tallyEnvText = Get-Content $tallyEnvDst -Raw
        if ($tallyEnvText -match '(?m)^\s*TALLY_VOUCHER_CLASS\s*=') {
            Write-Host "[WARN] tally-bridge\.env still has TALLY_VOUCHER_CLASS (unused). Remove it and set TALLY_PURCHASE_LEDGER instead."
        }
        if ($tallyEnvText -match '(?m)^\s*TALLY_VOUCHER_TYPE\s*=\s*.*(account|A/c).*$') {
            Write-Host "[WARN] TALLY_VOUCHER_TYPE looks like a ledger name. Set it to Purchase and use TALLY_PURCHASE_LEDGER for Purchase A/c."
        }
    }
}

if (-not $SkipMongoDB) {
    Write-Host "`n[5/6] Bundling MongoDB $MongoVersion ..."
    & (Join-Path $Root "packaging\bundle_mongodb.ps1") -DistRoot $DistRoot -MongoVersion $MongoVersion
} else {
    Write-Host "`n[5/6] Skipping MongoDB bundle."
}

if (-not $SkipPoDB) {
    Write-Host "`n[6/6] Bundling PO_DB (PostgreSQL + loader/watcher) ..."
    $poDbArgs = @{
        DistRoot         = $DistRoot
        PostgresVersion  = $PostgresVersion
    }
    if ($SkipPyInstaller) { $poDbArgs.SkipPyInstaller = $true }
    if ($SkipPostgreSQL) { $poDbArgs.SkipPostgreSQL = $true }
    & (Join-Path $Root "packaging\bundle_po_db.ps1") @poDbArgs
} else {
    Write-Host "`n[6/6] Skipping PO_DB bundle."
}

Write-Host "`nCopying OCR runtime packages/metadata into frozen bundles ..."
& (Join-Path $Root "packaging\copy_ocr_runtime.ps1") -DistRoot $DistRoot

if (-not $SkipPyInstaller) {
    Write-Host "`nBuilding fingerprint_tool.exe ..."
    & $Py -m PyInstaller @commonArgs (Join-Path $Root "packaging\fingerprint_tool.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for fingerprint_tool.spec" }
}

Write-Host "`n[OK] Portable build ready:"
Write-Host "  $DistRoot"
Write-Host "  Run: $(Join-Path $DistRoot 'Start IDP Invoice.exe')"
Write-Host "`nBefore first use:"
Write-Host "  1. Edit dist\IDP-Invoice\.env and set GEMINI_API_KEY."
Write-Host "  2. Keep POSTGRES_HOST=localhost for bundled PO_DB (po-db.exe auto-creates DB/tables and watches po-db\data\)."
Write-Host "  3. Or set IDP_USE_BUNDLED_POSTGRES=0 and POSTGRES_* to use an external PostgreSQL server."
Write-Host "  4. Place license.lic next to Start IDP Invoice.exe (see licensing\README.md)."
Write-Host "  5. (Optional) Set TALLY_ENABLED=true in .env and configure tally-bridge\.env (TALLY_URL, TALLY_COMPANY, TALLY_VOUCHER_TYPE, TALLY_PURCHASE_LEDGER)."
Write-Host "Bundled MongoDB starts automatically when MONGO_URI points to localhost."
Write-Host "po-db.exe handles bundled/external PostgreSQL, schema bootstrap, and CSV watching."
