# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IDP FastAPI server (onedir)."""

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
    [str(ROOT / "backend" / "run_api.py")],
    pathex=[str(ROOT)],
    binaries=_paddle_binaries,
    datas=_paddle_datas,
    hiddenimports=[
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
        "multipart",
        "python_multipart",
        "pydantic",
        "pydantic.deprecated.decorator",
        "fastapi",
        "starlette",
        "bson",
        "pymongo",
        "google.genai",
        "watchdog",
        "watchdog.observers",
        "watchdog.observers.polling",
        "backend",
        "backend.api",
        "backend.app_paths",
        "backend.app_logging",
        "backend.run_api",
        "backend.agents.ocr",
        "backend.agents.database",
        "backend.agents.rag_chatbot",
        "backend.agents.chat_engine",
        "backend.agents.chat_analytics",
        "backend.agents.chat_vector_index",
        "backend.agents.chat_embeddings",
        "backend.agents.qdrant_store",
        "backend.agents.chat_sessions",
        "qdrant_client",
        "qdrant_client.models",
        "qdrant_client.http",
        "fastembed",
        "fastembed.text",
        "backend.agents.watch_raw",
        "backend.agents.preprocess_2",
        "backend.invoice_files",
        "backend.hitl_status",
        "backend.erp_db",
        "backend.erp_matching",
        "backend.erp_match_status",
        "backend.erp_settings",
        "backend.erp_sync",
        "backend.erp_scheduler",
        "backend.tally_integration",
        "backend.tally_integration.config",
        "backend.tally_integration.bridge_client",
        "backend.tally_integration.pipeline",
        "backend.tally_sync",
        "requests",
        "psycopg2",
        "psycopg2.extras",
        "psycopg2._psycopg",
        *_LICENSE_IMPORTS,
        *_paddle_hiddenimports,
    ],
    hookspath=[str(ROOT / "packaging")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyi_rth_paddle.py")],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "sentence_transformers",
        "llama_index",
        "llama_index.core",
        "llama_index.embeddings",
        "llama_index.llms",
        "sklearn",
    ],
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
    name="idp-api",
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
    name="idp-api",
)
