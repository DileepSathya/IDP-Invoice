# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Tally bridge service (onedir)."""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
TALLY_DIR = ROOT / "TALLY INTEGRATION"

sys.path.insert(0, str(TALLY_DIR))

block_cipher = None

a = Analysis(
    [str(ROOT / "backend" / "run_tally_bridge.py")],
    pathex=[str(ROOT), str(TALLY_DIR)],
    binaries=[],
    # xml_scripts is also copied to dist/tally-bridge/xml_scripts by build.ps1 so
    # the external template can be updated without rebuilding the exe.
    datas=[
        (str(TALLY_DIR / "xml_scripts"), "xml_scripts"),
    ],
    hiddenimports=[
        "api_server",
        "tally",
        "tally.pipeline",
        "tally.create_voucher",
        "tally.tally_details",
        "tally.env_sync",
        "tally.invoice_data_retriver",
        "tally.configurations",
        "tally.configurations.config",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "pydantic",
        "requests",
        "pymongo",
        "bson",
        "dotenv",
        "backend",
        "backend.app_paths",
        "backend.run_tally_bridge",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tally-bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tally-bridge",
)
