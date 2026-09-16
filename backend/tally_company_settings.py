"""Read and persist the Tally company selected in the Settings UI."""

from __future__ import annotations

import os
import re
from pathlib import Path

from backend.app_paths import tally_bridge_root

_COMPANY_ENV_KEY = "TALLY_COMPANY"
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _env_path() -> Path:
    return tally_bridge_root() / ".env"


def _normalize_company_name(company_name: str) -> str:
    normalized = (company_name or "").strip()
    if not normalized:
        raise ValueError("Company name is required.")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("Company name must be a single line.")
    return normalized


def _read_company_from_file() -> str:
    path = _env_path()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == _COMPANY_ENV_KEY:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value.strip()
    return ""


def current_tally_company() -> str:
    """Return the latest saved value, reading disk so no restart is needed."""
    return _read_company_from_file() or os.environ.get(_COMPANY_ENV_KEY, "").strip()


def get_tally_company() -> dict[str, str]:
    return {"company_name": current_tally_company()}


def save_tally_company(company_name: str) -> dict[str, str]:
    normalized = _normalize_company_name(company_name)
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    replaced = False
    updated_lines: list[str] = []
    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == _COMPANY_ENV_KEY:
            updated_lines.append(f"{_COMPANY_ENV_KEY}={normalized}")
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"{_COMPANY_ENV_KEY}={normalized}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    os.environ[_COMPANY_ENV_KEY] = normalized
    return {"company_name": normalized}
