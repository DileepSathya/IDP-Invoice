# Build portable IDP Invoice distribution into dist/IDP-Invoice/
param(
    [switch]$SkipFrontend,
    [switch]$SkipPyInstaller,
    [switch]$SkipMongoDB,
    [string]$MongoVersion = "7.0.14"
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

if (-not $SkipFrontend) {
    Write-Host "`n[1/5] Building frontend..."
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
    Write-Host "`n[1/5] Skipping frontend build."
}

if (-not $SkipPyInstaller) {
    Write-Host "`n[2/5] Installing PyInstaller..."
    & $Py -m pip install --upgrade pyinstaller

    $tallyBridgeSrc = Join-Path $Root "TALLY INTEGRATION\api_server.py"
    if (-not (Test-Path $tallyBridgeSrc)) {
        throw "TALLY INTEGRATION source not found ($tallyBridgeSrc). Clone or restore the Tally bridge folder before building."
    }

    Write-Host "`n[3/5] Running PyInstaller (API, watcher, launcher, tally-bridge)..."
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
    Write-Host "`n[2/5] Skipping PyInstaller."
    Write-Host "`n[3/5] Skipping PyInstaller."
}

Write-Host "`n[4/5] Assembling portable folder..."
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
}
$tallyEnvExampleSrc = Join-Path $Root "TALLY INTEGRATION\.env.example"
$tallyEnvExampleDst = Join-Path $tallyBridgeRoot ".env.example"
$tallyEnvDst = Join-Path $tallyBridgeRoot ".env"
if (Test-Path $tallyEnvExampleSrc) {
    Copy-Item -Force $tallyEnvExampleSrc $tallyEnvExampleDst
    if (-not (Test-Path $tallyEnvDst)) {
        Copy-Item -Force $tallyEnvExampleDst $tallyEnvDst
        Write-Host "Created tally-bridge\.env from .env.example (set TALLY_COMPANY and TALLY_URL)."
    }
}

if (-not $SkipMongoDB) {
    Write-Host "`n[5/5] Bundling MongoDB $MongoVersion ..."
    & (Join-Path $Root "packaging\bundle_mongodb.ps1") -DistRoot $DistRoot -MongoVersion $MongoVersion
} else {
    Write-Host "`n[5/5] Skipping MongoDB bundle."
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
Write-Host "  2. (Optional) Fill in POSTGRES_HOST/POSTGRES_USER/POSTGRES_PASSWORD in .env to enable ERP matching - they ship blank on purpose."
Write-Host "  3. Place license.lic next to Start IDP Invoice.exe (see licensing\README.md)."
Write-Host "  4. (Optional) Set TALLY_ENABLED=true in .env and configure tally-bridge\.env (TALLY_URL, TALLY_COMPANY)."
Write-Host "Bundled MongoDB starts automatically when MONGO_URI points to localhost."
