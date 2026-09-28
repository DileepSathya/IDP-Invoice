# Build the portable payload and compile it into a single Inno Setup installer.
param(
    [string]$Version = "1.0.0",
    [switch]$SkipAppBuild,
    [switch]$SkipFrontend,
    [switch]$SkipPyInstaller,
    [switch]$SkipMongoDB,
    [string]$MongoVersion = "7.0.14",
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DistRoot = Join-Path $Root "dist\IDP-Invoice"
$InstallerScript = Join-Path $PSScriptRoot "installer\IDP-Invoice.iss"
$ExpectedInstaller = Join-Path $Root "dist\installer\IDP-Invoice-Setup.exe"

if (-not $SkipAppBuild) {
    $buildArgs = @{}
    if ($SkipFrontend) { $buildArgs.SkipFrontend = $true }
    if ($SkipPyInstaller) { $buildArgs.SkipPyInstaller = $true }
    if ($SkipMongoDB) { $buildArgs.SkipMongoDB = $true }
    $buildArgs.MongoVersion = $MongoVersion
    & (Join-Path $PSScriptRoot "build.ps1") @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "Application build failed." }
}

$requiredFiles = @(
    (Join-Path $DistRoot "Start IDP Invoice.exe"),
    (Join-Path $DistRoot "idp-services\idp-api.exe"),
    (Join-Path $DistRoot "idp-services\idp-watcher.exe"),
    (Join-Path $DistRoot "idp-services\_internal"),
    (Join-Path $DistRoot "idp-services\_internal\paddleocr\ppocr\data\__init__.py"),
    (Join-Path $DistRoot "idp-services\_internal\paddleocr\ppocr\data\imaug\operators.py"),
    (Join-Path $DistRoot "frontend\index.html"),
    (Join-Path $DistRoot "tally-bridge\tally-bridge.exe"),
    (Join-Path $DistRoot "tally-bridge\.env.example"),
    (Join-Path $DistRoot "mongodb\bin\mongod.exe"),
    (Join-Path $DistRoot "mongodb\bin\vc_redist.x64.exe"),
    (Join-Path $DistRoot ".env.example")
)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Installer payload is incomplete; missing: $requiredFile"
    }
}

if (-not $InnoCompiler) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $InnoCompiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
    throw "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup or pass -InnoCompiler with its full path."
}

$env:IDP_INVOICE_VERSION = $Version
try {
    & $InnoCompiler $InstallerScript
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed with exit code $LASTEXITCODE." }
} finally {
    Remove-Item Env:\IDP_INVOICE_VERSION -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $ExpectedInstaller)) {
    throw "Inno Setup completed but the expected output was not found: $ExpectedInstaller"
}
Write-Host "[OK] Installer ready: $ExpectedInstaller"
