# Download and stage MongoDB Community Server (Windows x64) into the portable dist folder.
param(
    [string]$DistRoot = "",
    [string]$MongoVersion = "7.0.14"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $DistRoot) {
    $DistRoot = Join-Path $Root "dist\IDP-Invoice"
}

$CacheDir = Join-Path $Root "packaging\mongodb-cache"
$ZipName = "mongodb-windows-x86_64-$MongoVersion.zip"
$ZipPath = Join-Path $CacheDir $ZipName
$Url = "https://fastdl.mongodb.org/windows/$ZipName"
$MongoOut = Join-Path $DistRoot "mongodb\bin"

New-Item -ItemType Directory -Force -Path $CacheDir, $MongoOut | Out-Null

if (-not (Test-Path $ZipPath)) {
    Write-Host "Downloading MongoDB $MongoVersion ..."
    Write-Host "  $Url"
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing
} else {
    Write-Host "Using cached MongoDB zip: $ZipPath"
}

$ExtractRoot = Join-Path $CacheDir "extracted-$MongoVersion"
if (-not (Test-Path (Join-Path $ExtractRoot "bin\mongod.exe"))) {
    if (Test-Path $ExtractRoot) { Remove-Item -Recurse -Force $ExtractRoot }
    Write-Host "Extracting MongoDB ..."
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractRoot -Force
    $inner = Get-ChildItem $ExtractRoot -Directory | Select-Object -First 1
    if ($inner -and (Test-Path (Join-Path $inner.FullName "bin\mongod.exe"))) {
        Move-Item (Join-Path $inner.FullName "*") $ExtractRoot -Force
        Remove-Item $inner.FullName -Force -ErrorAction SilentlyContinue
    }
}

$SourceBin = Join-Path $ExtractRoot "bin"
if (-not (Test-Path (Join-Path $SourceBin "mongod.exe"))) {
    throw "mongod.exe not found after extract. Check MongoDB version or archive layout."
}

Write-Host "Copying MongoDB binaries to $MongoOut ..."
Get-ChildItem $MongoOut -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Copy-Item -Recurse (Join-Path $SourceBin "*") $MongoOut

Write-Host "[OK] MongoDB bundled at $MongoOut"
