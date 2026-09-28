# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller multiprogram bundle for API and watcher with shared dependencies."""

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

_COMMON_IMPORTS = [
    "bson",
    "pymongo",
    "google.genai",
    "watchdog",
    "watchdog.observers",
    "watchdog.observers.polling",
    "backend",
    "backend.app_paths",
    "backend.app_logging",
    "backend.agents.ocr",
    "backend.agents.gemini_client",
    "backend.pipeline_errors",
    "backend.agents.database",
    "backend.agents.preprocess_2",
    "backend.invoice_files",
    "backend.hitl_status",
    "backend.invoice_merge",
    "backend.erp_settings",
    "backend.erp_sync",
    *_LICENSE_IMPORTS,
    *_paddle_hiddenimports,
]

_EXCLUDES = [
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
]

api_a = Analysis(
    [str(ROOT / "backend" / "run_api.py")],
    pathex=[str(ROOT)],
    binaries=list(_paddle_binaries),
    datas=list(_paddle_datas),
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
        "backend.api",
        "backend.auth",
        "backend.system_health",
        "backend.agent_settings",
        "backend.pipeline_status",
        "backend.run_api",
        "backend.agents.invoice_chat_pipeline",
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
        "backend.erp_db",
        "backend.erp_matching",
        "backend.erp_match_status",
        "backend.invoice_merge",
        "backend.erp_scheduler",
        "backend.hitl_notification_settings",
        "backend.hitl_email",
        "backend.hitl_notifications",
        "backend.hitl_notification_scheduler",
        "backend.tally_integration",
        "backend.tally_integration.config",
        "backend.tally_integration.bridge_client",
        "backend.tally_integration.pipeline",
        "backend.tally_sync",
        "backend.tally_settings",
        "backend.tally_master_db",
        "backend.tally_master_sync",
        "backend.tally_master_settings",
        "backend.tally_master_scheduler",
        "requests",
        *_COMMON_IMPORTS,
    ],
    hookspath=[str(ROOT / "packaging")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyi_rth_paddle.py")],
    excludes=_EXCLUDES,
    noarchive=False,
)

watcher_a = Analysis(
    [str(ROOT / "backend" / "run_watcher.py")],
    pathex=[str(ROOT)],
    binaries=list(_paddle_binaries),
    datas=list(_paddle_datas),
    hiddenimports=[
        "backend.run_watcher",
        "backend.agents.watch_raw",
        *_COMMON_IMPORTS,
    ],
    hookspath=[str(ROOT / "packaging")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyi_rth_paddle.py")],
    excludes=_EXCLUDES,
    noarchive=False,
)

api_pyz = PYZ(api_a.pure, api_a.zipped_data)
watcher_pyz = PYZ(watcher_a.pure, watcher_a.zipped_data)

api_exe = EXE(
    api_pyz,
    api_a.scripts,
    [],
    exclude_binaries=True,
    name="idp-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

watcher_exe = EXE(
    watcher_pyz,
    watcher_a.scripts,
    [],
    exclude_binaries=True,
    name="idp-watcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

services = COLLECT(
    api_exe,
    watcher_exe,
    api_a.binaries,
    api_a.datas,
    watcher_a.binaries,
    watcher_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="idp-services",
)
