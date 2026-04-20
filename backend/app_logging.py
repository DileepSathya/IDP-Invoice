"""
Central logging setup for the IDP backend.

Uvicorn may reconfigure the root logger after this module is imported, which removes a
FileHandler added at import time. Use ensure_application_logging() from FastAPI lifespan
and from long-running workers so logs (including MongoDB / file-save lines) always reach
logs/idp.log.

Environment:
  LOG_LEVEL   — DEBUG, INFO (default), WARNING, ERROR
  LOG_FILE    — optional override for the log file path (default: <repo>/logs/idp.log)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Repository root: backend/app_logging.py -> parents[1] == repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _REPO_ROOT / "logs"
_DEFAULT_LOG_PATH = _DEFAULT_LOG_DIR / "idp.log"


def _make_formatter() -> logging.Formatter:
    # %(lineno)d / %(filename)s refer to the line where logger.info/debug/warning/error was called.
    fmt = (
        "%(asctime)s | %(levelname)-5s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"
    return logging.Formatter(fmt, datefmt)


def _resolve_log_file_path() -> Path:
    override = os.environ.get("LOG_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_LOG_PATH.resolve()


def _normalized_handler_path(handler: logging.Handler) -> str | None:
    if not isinstance(handler, logging.FileHandler):
        return None
    base = getattr(handler, "baseFilename", None)
    if not base:
        return None
    return os.path.normcase(os.path.abspath(str(base)))


def _root_has_file_handler_for(root: logging.Logger, path: Path) -> bool:
    want = os.path.normcase(str(path.resolve()))
    for h in root.handlers:
        got = _normalized_handler_path(h)
        if got == want:
            return True
    return False


class FlushingFileHandler(logging.FileHandler):
    """FileHandler that flushes after each record (helps on Windows / long runs)."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def _has_console_stream_handler(root: logging.Logger) -> bool:
    """True if root has a StreamHandler that is not a FileHandler (stdout/stderr logging)."""
    for h in root.handlers:
        if isinstance(h, logging.FileHandler):
            continue
        if isinstance(h, logging.StreamHandler):
            return True
    return False


def ensure_application_logging() -> None:
    """
    Idempotent: attach our log file + formatter to the root logger, and a stdout handler if the
    process had no handlers at all (e.g. watch_raw CLI). Safe to call after uvicorn reconfigures
    logging (e.g. from FastAPI lifespan).
    """
    root = logging.getLogger()
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    formatter = _make_formatter()
    path = _resolve_log_file_path()

    had_handlers_before = len(root.handlers) > 0
    added_file = False

    if not _root_has_file_handler_for(root, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = FlushingFileHandler(path, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
        added_file = True

    if added_file:
        logging.getLogger(__name__).info(
            "File logging enabled — appending to: %s",
            path,
        )

    if not had_handlers_before and not _has_console_stream_handler(root):
        out = logging.StreamHandler(sys.stdout)
        out.setFormatter(formatter)
        root.addHandler(out)

    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def configure_logging() -> None:
    """Backward-compatible name; ensures file + console logging."""
    ensure_application_logging()