"""Dashboard login — hardcoded admin credentials and signed session cookies."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

# Hardcoded dashboard credentials (as requested).
DASHBOARD_LOGIN_ID = "IDP_admin"
DASHBOARD_PASSWORD = "idpadmin@123"

SESSION_COOKIE = "idp_dashboard_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
SESSION_SECRET = b"idp-dashboard-session-secret"


def verify_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, DASHBOARD_LOGIN_ID) and hmac.compare_digest(
        password, DASHBOARD_PASSWORD
    )


def create_session_token(username: str) -> str:
    issued_at = int(time.time())
    payload = f"{username}:{issued_at}"
    signature = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def validate_session_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None

    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return None

    username, issued_at_str, signature = parts
    try:
        issued_at = int(issued_at_str)
    except ValueError:
        return None

    if time.time() - issued_at > SESSION_MAX_AGE:
        return None

    payload = f"{username}:{issued_at}"
    expected = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    if not hmac.compare_digest(username, DASHBOARD_LOGIN_ID):
        return None
    return username


PUBLIC_API_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("POST", "/auth/login"),
        ("POST", "/auth/logout"),
        ("GET", "/auth/me"),
    }
)

API_PATH_PREFIXES: tuple[str, ...] = (
    "/license",
    "/agent-settings",
    "/config-status",
    "/erp",
    "/notifications",
    "/tally",
    "/telemetry",
    "/invoices",
    "/upload",
    "/chat",
    "/raw",
    "/raw-pdf",
    "/health",
    "/auth",
    "/v1",
)


def is_api_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in API_PATH_PREFIXES)


def requires_dashboard_auth(method: str, path: str) -> bool:
    if method == "OPTIONS":
        return False
    if not is_api_path(path):
        return False
    if (method, path) in PUBLIC_API_PATHS:
        return False
    if path.startswith("/v1/"):
        return False
    if path.startswith("/auth/"):
        return False
    return True
