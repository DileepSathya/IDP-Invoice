# Download and stage PostgreSQL Windows x64 binaries into po-db/pgsql/ inside the portable dist.
param(
    [string]$DistRoot = "",
    [string]$PostgresVersion = "16.14"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $DistRoot) {
    $DistRoot = Join-Path $Root "dist\IDP-Invoice"
}

$PoDbRoot = Join-Path $DistRoot "po-db"
$PgsqlOut = Join-Path $PoDbRoot "pgsql"
$CacheDir = Join-Path $Root "packaging\postgresql-cache"
$ZipName = "postgresql-$PostgresVersion-1-windows-x64-binaries.zip"
$ZipPath = Join-Path $CacheDir $ZipName
$Url = "https://get.enterprisedb.com/postgresql/$ZipName"

New-Item -ItemType Directory -Force -Path $CacheDir, $PgsqlOut | Out-Null

function Test-ZipArchiveValid {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
        $zip.Dispose()
        return $true
    } catch {
        return $false
    }
}

$needDownload = -not (Test-Path $ZipPath) -or -not (Test-ZipArchiveValid $ZipPath)
if ($needDownload) {
    if (Test-Path $ZipPath) {
        Write-Host "Removing invalid/incomplete PostgreSQL zip cache: $ZipPath"
        Remove-Item -Force $ZipPath
    }
    $partialPath = "$ZipPath.download"
    if (Test-Path $partialPath) { Remove-Item -Force $partialPath }
    Write-Host "Downloading PostgreSQL $PostgresVersion ..."
    Write-Host "  $Url"
    Invoke-WebRequest -Uri $Url -OutFile $partialPath -UseBasicParsing
    Move-Item -Force $partialPath $ZipPath
    if (-not (Test-ZipArchiveValid $ZipPath)) {
        Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
        throw "Downloaded PostgreSQL archive is not a valid zip: $ZipPath"
    }
} else {
    Write-Host "Using cached PostgreSQL zip: $ZipPath"
}

$ExtractRoot = Join-Path $CacheDir "extracted-$PostgresVersion"
$PostgresExe = Join-Path $ExtractRoot "pgsql\bin\postgres.exe"
if (-not (Test-Path $PostgresExe)) {
    if (Test-Path $ExtractRoot) { Remove-Item -Recurse -Force $ExtractRoot }
    Write-Host "Extracting PostgreSQL ..."
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractRoot -Force
    if (-not (Test-Path $PostgresExe)) {
        $inner = Get-ChildItem $ExtractRoot -Directory | Select-Object -First 1
        if ($inner -and (Test-Path (Join-Path $inner.FullName "pgsql\bin\postgres.exe"))) {
            Get-ChildItem $inner.FullName | Move-Item -Destination $ExtractRoot -Force
            Remove-Item $inner.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}

$SourcePgsql = Join-Path $ExtractRoot "pgsql"
if (-not (Test-Path (Join-Path $SourcePgsql "bin\postgres.exe"))) {
    throw "postgres.exe not found after extract. Check PostgreSQL version or archive layout."
}

Write-Host "Copying PostgreSQL to $PgsqlOut ..."
if (Test-Path $PgsqlOut) { Remove-Item -Recurse -Force $PgsqlOut }
Copy-Item -Recurse $SourcePgsql $PgsqlOut

Write-Host "[OK] PostgreSQL bundled at $PgsqlOut"
