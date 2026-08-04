# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the raw-folder watcher (onedir)."""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "packaging"))

from pyinstaller_helpers import get_paddle_artifacts  # noqa: E402

_paddle_datas, _paddle_binaries, _paddle_hiddenimports = get_paddle_artifacts()

_LICENSE_IMPORTS = [
    "license_validator",
    "licensing",
    "licensing.hardware_fingerprint",
    "licensing.public_key_embed",
    "cryptography",
    "cryptography.hazmat.primitives.asymmetric.padding",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.backends.openssl",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "backend" / "run_watcher.py")],
    pathex=[str(ROOT)],
    binaries=_paddle_binaries,
    datas=_paddle_datas,
    hiddenimports=[
        "watchdog",
        "watchdog.observers",
        "watchdog.observers.polling",
        "bson",
        "pymongo",
        "google.genai",
        "backend",
        "backend.app_paths",
        "backend.app_logging",
        "backend.run_watcher",
        "backend.agents.watch_raw",
        "backend.agents.ocr",
        "backend.agents.gemini_client",
        "backend.pipeline_errors",
        "backend.agents.database",
        "backend.agents.preprocess_2",
        "backend.invoice_files",
        "backend.hitl_status",
        *_LICENSE_IMPORTS,
        *_paddle_hiddenimports,
    ],
    hookspath=[str(ROOT / "packaging")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyi_rth_paddle.py")],
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
    name="idp-watcher",
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
    name="idp-watcher",
)
