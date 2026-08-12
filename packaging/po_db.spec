# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for unified PO_DB service (onefile -> po-db/po-db.exe)."""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
LOADER_DIR = ROOT / "PO_DB" / "po_loader"

block_cipher = None

a = Analysis(
    [str(LOADER_DIR / "po_db_service.py")],
    pathex=[str(ROOT), str(LOADER_DIR)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "po_db_runtime",
        "watch_data",
        "load_data",
        "psycopg2",
        "psycopg2._psycopg",
        "configparser",
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
    a.binaries,
    a.datas,
    [],
    name="po-db",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
