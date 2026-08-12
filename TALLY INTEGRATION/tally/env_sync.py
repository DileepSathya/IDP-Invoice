"""Merge missing keys from .env.example into .env (non-destructive)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


def sync_env_from_example(env_path: Path, example_path: Path) -> list[str]:
    if not example_path.is_file():
        return []
    if not env_path.is_file():
        shutil.copy(example_path, env_path)
        return ["<created>"]

    existing_text = env_path.read_text(encoding="utf-8")
    existing_keys = {
        match.group(1).strip()
        for line in existing_text.splitlines()
        if (match := re.match(r"^\s*([^#=]+?)=", line))
    }
    added: list[str] = []
    to_append: list[str] = []
    for line in example_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^\s*([^=]+?)=", line)
        if not match:
            continue
        key = match.group(1).strip()
        if key not in existing_keys:
            to_append.append(line)
            added.append(key)

    if to_append:
        with env_path.open("a", encoding="utf-8") as handle:
            if existing_text and not existing_text.endswith("\n"):
                handle.write("\n")
            handle.write("\n".join(to_append) + "\n")
    return added
