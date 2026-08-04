"""Classify pipeline failures and read Gemini API key from .env for recovery."""

from __future__ import annotations

import re
import socket
from pathlib import Path

from backend.app_paths import app_dir

NETWORK_RETRY_SLEEP_SECONDS = 60
NETWORK_MAX_RETRIES = 3

GEMINI_RETRY_SLEEP_SECONDS = 30
GEMINI_RECOVERY_SLEEP_SECONDS = 60
GEMINI_RECOVERY_MAX_CYCLES = 3


class NetworkPipelineError(Exception):
    """Transient network failure during OCR/Gemini pipeline."""


class GeminiApiPipelineError(Exception):
    """Gemini API failure (quota, rate limit, invalid key, 503 overload, etc.)."""


_GEMINI_API_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "quota",
    "rate limit",
    "rate-limit",
    "generativelanguage.googleapis.com",
    "api key not valid",
    "invalid api key",
    "permission denied",
    "resource exhausted",
    "billing",
    "unavailable",
    "high demand",
    "servererror",
    "service unavailable",
    "deadline exceeded",
    "google.genai",
    "generativeai",
)

_GOOGLE_GEMINI_MODULE_PREFIXES = (
    "google.genai",
    "google.api_core",
    "google.generativeai",
)

_GOOGLE_GEMINI_EXCEPTION_NAMES = frozenset(
    {
        "ResourceExhausted",
        "PermissionDenied",
        "InvalidArgument",
        "FailedPrecondition",
        "ServiceUnavailable",
        "InternalServerError",
        "TooManyRequests",
        "DeadlineExceeded",
        "ServerError",
        "ClientError",
        "APIError",
    }
)

_NETWORK_MARKERS = (
    "connection refused",
    "connection reset",
    "connection aborted",
    "timed out",
    "timeout",
    "temporary failure in name resolution",
    "network is unreachable",
    "failed to establish a new connection",
    "getaddrinfo failed",
    "name or service not known",
    "no route to host",
    "ssl:",
    "socket",
    "unreachable",
    "dns",
)


def _exception_message(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        text = str(current).strip()
        if text:
            parts.append(text)
        current = current.__cause__ or current.__context__
    return " ".join(parts).lower()


def _is_google_gemini_exception(exc: BaseException) -> bool:
    module_name = type(exc).__module__ or ""
    if any(module_name.startswith(prefix) for prefix in _GOOGLE_GEMINI_MODULE_PREFIXES):
        return True
    return type(exc).__name__ in _GOOGLE_GEMINI_EXCEPTION_NAMES


def is_gemini_api_error(exc: BaseException) -> bool:
    if isinstance(exc, GeminiApiPipelineError):
        return True

    if _is_google_gemini_exception(exc):
        return True

    message = _exception_message(exc)
    if any(marker in message for marker in _GEMINI_API_MARKERS):
        return True

    return False


def is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, NetworkPipelineError):
        return True

    if isinstance(
        exc,
        (
            ConnectionError,
            TimeoutError,
            socket.timeout,
            BrokenPipeError,
        ),
    ):
        return True

    message = _exception_message(exc)
    if is_gemini_api_error(exc):
        return False
    return any(marker in message for marker in _NETWORK_MARKERS)


def classify_pipeline_error(exc: BaseException) -> type[Exception] | None:
    if is_gemini_api_error(exc):
        return GeminiApiPipelineError
    if is_network_error(exc):
        return NetworkPipelineError
    return None


def wrap_pipeline_error(exc: BaseException) -> BaseException:
    if isinstance(exc, (NetworkPipelineError, GeminiApiPipelineError)):
        return exc
    if is_gemini_api_error(exc):
        return GeminiApiPipelineError(str(exc))
    if is_network_error(exc):
        return NetworkPipelineError(str(exc))
    return exc


def read_gemini_api_key_from_env_file() -> str:
    """Read GEMINI_API_KEY / GOOGLE_API_KEY directly from .env (not os.environ)."""
    env_path = app_dir() / ".env"
    if not env_path.is_file():
        return ""

    keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    pattern = re.compile(r"^\s*({})\s*=\s*(.*)\s*$".format("|".join(keys)))
    found: dict[str, str] = {}

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        key_name = match.group(1)
        value = match.group(2).strip().strip('"').strip("'")
        found[key_name] = value

    return (found.get("GEMINI_API_KEY") or found.get("GOOGLE_API_KEY") or "").strip()
