"""
User-configurable AI agent settings (AI model + Gemini API key).

MongoDB (`app_settings` / `ai_agent`) is the only store for the API key.
The key is never written to or read from `.env` or os.environ.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from backend.app_paths import app_dir, load_app_dotenv

logger = logging.getLogger(__name__)

SUPPORTED_MODELS = ("gemini",)
MODEL_LABELS = {"gemini": "Gemini"}

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_ENV_KEYS_TO_PURGE = frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY", "AI_MODEL_PROVIDER"})


def _env_path() -> Path:
    return app_dir() / ".env"


def _purge_ai_credentials_from_process_env() -> None:
    for key in _ENV_KEYS_TO_PURGE:
        os.environ.pop(key, None)


def _purge_ai_credentials_from_env_file() -> None:
    """Remove legacy API key lines from .env so secrets are not kept on disk there."""
    path = _env_path()
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = False
    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) in _ENV_KEYS_TO_PURGE:
            removed = True
            continue
        kept.append(line)
    if removed:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        logger.info("[agent_settings] Removed AI credentials from .env (MongoDB is authoritative).")


def _settings_from_mongodb() -> dict[str, Optional[str]]:
    try:
        from backend.agent_settings_db import get_stored_agent_settings_with_status

        doc, db_ok = get_stored_agent_settings_with_status()
        if not db_ok:
            return {"model": None, "api_key": None}
        api_key = str(doc.get("gemini_api_key") or "").strip() or None
        model = str(doc.get("model") or "").strip().lower() or None
        if api_key and not model:
            model = SUPPORTED_MODELS[0]
        return {"model": model, "api_key": api_key}
    except Exception as exc:
        logger.warning("[agent_settings] Could not load from MongoDB: %s", exc)
        return {"model": None, "api_key": None}


def get_agent_settings() -> dict:
    """Returns {"model": str|None, "api_key": str|None} from MongoDB only."""
    return _settings_from_mongodb()


def read_gemini_api_key() -> str:
    """Current Gemini API key from MongoDB."""
    return get_agent_settings().get("api_key") or ""


def is_agent_configured() -> bool:
    settings = get_agent_settings()
    return bool(settings.get("model")) and bool(settings.get("api_key"))


def missing_agent_settings() -> list[str]:
    settings = get_agent_settings()
    missing: list[str] = []
    if not settings.get("model"):
        missing.append("ai_model")
    if not settings.get("api_key"):
        missing.append("api_key")
    return missing


def masked_api_key(api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return f"{'*' * (len(api_key) - 4)}{api_key[-4:]}"


def save_agent_settings(model: str, api_key: str) -> dict:
    model = (model or "").strip().lower()
    api_key = (api_key or "").strip()

    if model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported AI model: '{model}'. Supported: {sorted(SUPPORTED_MODELS)}"
        )
    if not api_key:
        raise ValueError("API key is required.")

    from backend.agent_settings_db import save_stored_agent_settings

    save_stored_agent_settings(model=model, gemini_api_key=api_key)
    _purge_ai_credentials_from_process_env()
    _purge_ai_credentials_from_env_file()
    logger.info("[agent_settings] Saved AI agent settings to MongoDB (model=%s).", model)
    return get_agent_settings()


def load_agent_settings_into_env() -> None:
    """Startup hook — load `.env` for infrastructure; never use env for the Gemini API key."""
    load_app_dotenv()
    _purge_ai_credentials_from_process_env()
    _purge_ai_credentials_from_env_file()
