"""
User-configurable AI agent settings (AI model + API key), persisted directly
into the app's `.env` file so there is exactly one source of truth:

- Saving from the Settings UI writes straight into `.env` (creating it if it
  doesn't exist yet) and updates this process's os.environ immediately, so
  the very next upload works without a restart.
- A plain restart picks the key back up from `.env` the same way it always
  did (via load_app_dotenv()) — nothing extra to wire up.
- Anything else that already reads `.env` directly — e.g. the folder
  watcher's Gemini-error recovery loop in backend/agents/watch_raw.py, which
  polls `read_gemini_api_key_from_env_file()` to know when to retry
  quarantined invoices — sees a Settings-UI change exactly like a manual
  `.env` edit, with no separate store to keep in sync.
- An install that already had GEMINI_API_KEY set by hand in `.env` (the old
  way) is treated as already configured, even if the Settings UI has never
  been opened.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from backend.app_paths import app_dir, load_app_dotenv

logger = logging.getLogger(__name__)

# AI models offered in the Settings "AI agent" pane's radio button. Only
# Gemini is wired up to the extraction pipeline today; add new keys here once
# a corresponding extraction path exists in backend/agents/ocr.py.
SUPPORTED_MODELS = ("gemini",)
MODEL_LABELS = {"gemini": "Gemini"}

_MODEL_ENV_KEY = "AI_MODEL_PROVIDER"
_API_KEY_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _env_path() -> Path:
    return app_dir() / ".env"


def _read_env_file_values() -> dict[str, str]:
    path = _env_path()
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _write_env_file_values(updates: dict[str, str]) -> None:
    """Create/update `.env` in place: replace matching KEY=VALUE lines,
    leave everything else (including comments) untouched, and append any
    keys that weren't already present."""
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        match = _ENV_LINE_RE.match(line)
        key = match.group(1) if match else None
        if key and key in remaining:
            new_lines.append(f"{key}={remaining.pop(key)}")
        else:
            new_lines.append(line)

    if remaining:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def get_agent_settings() -> dict:
    """Returns {"model": str|None, "api_key": str|None}, read fresh from
    `.env` (falling back to os.environ) every time — cheap, and guarantees
    the Settings UI, the upload gate, and the OCR pipeline all agree with
    what's actually on disk."""
    file_values = _read_env_file_values()

    api_key = None
    for key_name in _API_KEY_ENV_KEYS:
        api_key = file_values.get(key_name) or os.environ.get(key_name)
        if api_key:
            break

    model = file_values.get(_MODEL_ENV_KEY) or os.environ.get(_MODEL_ENV_KEY)
    if not model and api_key:
        # Pre-existing installs that set GEMINI_API_KEY by hand (the old way,
        # before this Settings pane existed) are already configured for Gemini.
        model = SUPPORTED_MODELS[0]

    return {"model": model or None, "api_key": api_key or None}


def is_agent_configured() -> bool:
    settings = get_agent_settings()
    return bool(settings.get("model")) and bool(settings.get("api_key"))


def missing_agent_settings() -> list[str]:
    """Machine-readable list of what's missing, for the home-screen banner."""
    settings = get_agent_settings()
    missing = []
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

    _write_env_file_values({_MODEL_ENV_KEY: model, "GEMINI_API_KEY": api_key})

    # Reflect immediately in this process too, so the very next request
    # (upload, or another OCR call) sees it without needing a restart.
    os.environ["GEMINI_API_KEY"] = api_key
    os.environ[_MODEL_ENV_KEY] = model

    logger.info("[agent_settings] Saved AI agent settings to .env (model=%s).", model)
    return get_agent_settings()


def load_agent_settings_into_env() -> None:
    """Startup hook — load `.env` into os.environ so every consumer (this
    module, the OCR pipeline, the watcher's recovery loop) sees a
    previously-saved key without waiting for the first per-request reload."""
    load_app_dotenv()
