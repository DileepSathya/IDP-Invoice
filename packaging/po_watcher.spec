# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PO_DB CSV folder watcher (onefile -> po-db/po-watcher.exe)."""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
LOADER_DIR = ROOT / "PO_DB" / "po_loader"

block_cipher = None

a = Analysis(
    [str(LOADER_DIR / "watch_data.py")],
    pathex=[str(ROOT), str(LOADER_DIR)],
    binaries=[],
    datas=[],
    hiddenimports=[
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
    name="po-watcher",
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
