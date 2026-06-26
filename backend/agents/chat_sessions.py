"""Persist chat session history to local JSON files."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app_paths import logs_dir

logger = logging.getLogger(__name__)

_SESSIONS_DIR = logs_dir() / "chat_sessions"


def _sessions_dir() -> Path:
    path = _SESSIONS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    if not safe:
        safe = uuid4().hex
    return _sessions_dir() / f"{safe}.json"


def new_session_id() -> str:
    return uuid4().hex


def load_session(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("[Chat session] Could not load session %s: %s", session_id, exc)
        return None


def save_session(session: dict[str, Any]) -> None:
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    session["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path = _session_path(session_id)
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")


def get_or_create_session(session_id: str | None) -> dict[str, Any]:
    sid = (session_id or "").strip() or new_session_id()
    existing = load_session(sid)
    if existing:
        return existing
    now = datetime.utcnow().isoformat() + "Z"
    session = {
        "session_id": sid,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    save_session(session)
    return session


def append_message(
    session_id: str,
    *,
    role: str,
    content: str,
) -> dict[str, Any]:
    session = get_or_create_session(session_id)
    messages = session.setdefault("messages", [])
    if not isinstance(messages, list):
        messages = []
        session["messages"] = messages
    messages.append(
        {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )
    save_session(session)
    return session


def list_user_questions(session: dict[str, Any]) -> list[str]:
    messages = session.get("messages") or []
    if not isinstance(messages, list):
        return []
    return [
        str(m.get("content", "")).strip()
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user" and str(m.get("content", "")).strip()
    ]
