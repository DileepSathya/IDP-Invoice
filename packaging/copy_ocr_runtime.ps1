# Copy OCR Python packages + .dist-info metadata into frozen idp-api/idp-watcher bundles.
param(
    [string]$DistRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $DistRoot) {
    $DistRoot = Join-Path $Root "dist\IDP-Invoice"
}

$SitePackages = Join-Path $Root "venv\Lib\site-packages"
if (-not (Test-Path $SitePackages)) {
    throw "venv site-packages not found: $SitePackages"
}

$PackageNames = @(
    "imageio",
    "lazy_loader",
    "yaml",
    "bs4",
    "lxml",
    "fontTools",
    "fire",
    "openpyxl",
    "premailer",
    "docx"
)

$DistInfoGlobs = @(
    "imageio*.dist-info",
    "imgaug*.dist-info",
    "scikit_image*.dist-info",
    "lazy_loader*.dist-info",
    "matplotlib*.dist-info",
    "paddleocr*.dist-info",
    "paddlepaddle*.dist-info",
    "Pillow*.dist-info",
    "PyYAML*.dist-info",
    "lxml*.dist-info",
    "shapely*.dist-info",
    "pyclipper*.dist-info"
)

function Copy-OcrRuntime([string]$TargetInternal) {
    if (-not (Test-Path $TargetInternal)) {
        Write-Host "[WARN] Skipping missing bundle dir: $TargetInternal"
        return
    }

    foreach ($name in $PackageNames) {
        $src = Join-Path $SitePackages $name
        if (Test-Path $src) {
            $dst = Join-Path $TargetInternal $name
            if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
            Copy-Item -Recurse $src $dst
            Write-Host "  copied package: $name -> $TargetInternal"
        }
    }

    foreach ($glob in $DistInfoGlobs) {
        Get-ChildItem -Path $SitePackages -Directory -Filter $glob -ErrorAction SilentlyContinue | ForEach-Object {
            $dst = Join-Path $TargetInternal $_.Name
            if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
            Copy-Item -Recurse $_.FullName $dst
            Write-Host "  copied metadata: $($_.Name)"
        }
    }
}

Write-Host "Copying OCR runtime packages into portable bundles ..."
foreach ($bundle in @("idp-api", "idp-watcher")) {
    $internal = Join-Path $DistRoot "$bundle\_internal"
    Write-Host "[$bundle]"
    Copy-OcrRuntime $internal
}
Write-Host "[OK] OCR runtime copy complete."
