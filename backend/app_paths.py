"""
Resolve install / development paths for IDP Invoice.

Portable layout (frozen):
  IDP-Invoice/
    Start IDP Invoice.exe
    .env
    frontend/          ← built UI (copied at packaging time)
    idp-api/idp-api.exe
    idp-watcher/idp-watcher.exe
    tally-bridge/tally-bridge.exe
    invoices_data/to_be_processed/
    invoices_data/_api_staging/
    invoices_data/HITL_pending/
    invoices_data/ERROR/
    invoices_data/gemini_api_error/
    invoices_data/Completed/
    logs/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_FROZEN_SUBDIRS = frozenset({"idp-api", "idp-watcher", "tally-bridge"})


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return repo_root()


def app_dir() -> Path:
    """Portable install root — .env, data folders, and frontend/ live here."""
    if is_frozen():
        exe_parent = Path(sys.executable).resolve().parent
        if exe_parent.name in _FROZEN_SUBDIRS:
            return exe_parent.parent
        return exe_parent
    return repo_root()


def frontend_dist_dir() -> Path:
    if is_frozen():
        return app_dir() / "frontend"
    return repo_root() / "frontend" / "dist"


def logs_dir() -> Path:
    return app_dir() / "logs"


def tally_bridge_root() -> Path:
    """Tally bridge service folder (dev source tree or portable tally-bridge/)."""
    if is_frozen():
        return app_dir() / "tally-bridge"
    return repo_root() / "TALLY INTEGRATION"


def tally_bridge_xml_scripts_dir() -> Path:
    """XML templates for Tally HTTP requests — external copy preferred in portable builds."""
    external = tally_bridge_root() / "xml_scripts"
    if external.is_dir():
        return external
    bundled = bundle_dir() / "xml_scripts"
    if bundled.is_dir():
        return bundled
    return external


def resolve_data_path(env_key: str, default: str) -> Path:
    raw = os.environ.get(env_key, default).strip() or default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = app_dir() / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def load_app_dotenv() -> None:
    from dotenv import load_dotenv

    load_dotenv(app_dir() / ".env", override=False)
