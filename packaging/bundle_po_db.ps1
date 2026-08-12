# Assemble the portable po-db/ folder: PostgreSQL, SQL, templates, config, and loader exes.
param(
    [string]$DistRoot = "",
    [switch]$SkipPostgreSQL,
    [switch]$SkipPyInstaller,
    [string]$PostgresVersion = "16.14"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $DistRoot) {
    $DistRoot = Join-Path $Root "dist\IDP-Invoice"
}

$PoDbRoot = Join-Path $DistRoot "po-db"
$AssetsRoot = Join-Path $Root "packaging\po_db_assets"

Write-Host "==== Bundling PO_DB into po-db/ ===="
Write-Host "Output: $PoDbRoot"

foreach ($dir in @("sql", "templates", "data", "data\completed", "data\ERROR", "pgdata", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PoDbRoot $dir) | Out-Null
}

$sqlSrc = Join-Path $Root "PO_DB\sql\create_po_database.sql"
if (-not (Test-Path $sqlSrc)) {
    throw "PO_DB SQL not found: $sqlSrc"
}
Copy-Item -Force $sqlSrc (Join-Path $PoDbRoot "sql\create_po_database.sql")

$templatesSrc = Join-Path $Root "PO_DB\templates"
$templatesDst = Join-Path $PoDbRoot "templates"
if (Test-Path $templatesDst) { Remove-Item -Recurse -Force $templatesDst }
Copy-Item -Recurse $templatesSrc $templatesDst

$configExampleSrc = Join-Path $Root "PO_DB\po_loader\config.example.ini"
$configExampleDst = Join-Path $PoDbRoot "config.example.ini"
$configDst = Join-Path $PoDbRoot "config.ini"
Copy-Item -Force $configExampleSrc $configExampleDst
if (-not (Test-Path $configDst)) {
    Copy-Item -Force $configExampleDst $configDst
    Write-Host "Created po-db\config.ini from config.example.ini"
} else {
    Write-Host "Kept existing po-db\config.ini (not overwritten)."
}

foreach ($asset in @("start-po-db.bat", "README.txt")) {
    $src = Join-Path $AssetsRoot $asset
    if (-not (Test-Path $src)) {
        throw "Missing po_db asset: $src"
    }
    Copy-Item -Force $src (Join-Path $PoDbRoot $asset)
}

if (-not $SkipPostgreSQL) {
    & (Join-Path $Root "packaging\bundle_postgresql.ps1") -DistRoot $DistRoot -PostgresVersion $PostgresVersion
} else {
    Write-Host "Skipping PostgreSQL binary bundle."
}

if (-not $SkipPyInstaller) {
    function Resolve-Python {
        $venvPy = Join-Path $Root "venv\Scripts\python.exe"
        if (Test-Path $venvPy) { return $venvPy }
        $altVenvPy = Join-Path $Root ".venv\Scripts\python.exe"
        if (Test-Path $altVenvPy) { return $altVenvPy }
        throw "Python venv not found. Run install.bat first."
    }

    function Invoke-PyInstallerBuild {
        param(
            [string]$Python,
            [string[]]$BuildArgs,
            [string]$SpecPath
        )
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $Python -m PyInstaller @BuildArgs $SpecPath 2>&1 | Out-Host
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEap
        if ($exitCode -ne 0) {
            throw "PyInstaller failed for $SpecPath (exit $exitCode)"
        }
    }

    $Py = Resolve-Python
    $BuildWork = Join-Path $Root "build\pyinstaller-po-db"
    New-Item -ItemType Directory -Force -Path $BuildWork | Out-Null

    Write-Host "Building po-db.exe ..."
    $commonArgs = @(
        "--distpath", $PoDbRoot,
        "--workpath", $BuildWork,
        "--noconfirm"
    )

    Invoke-PyInstallerBuild -Python $Py -BuildArgs $commonArgs -SpecPath (Join-Path $Root "packaging\po_db.spec")

    foreach ($legacyExe in @("po-loader.exe", "po-watcher.exe")) {
        $legacyPath = Join-Path $PoDbRoot $legacyExe
        if (Test-Path $legacyPath) {
            Remove-Item -Force $legacyPath
        }
    }
} else {
    Write-Host "Skipping PyInstaller for po-db loader/watcher."
}

Write-Host "[OK] PO_DB bundled at $PoDbRoot"
