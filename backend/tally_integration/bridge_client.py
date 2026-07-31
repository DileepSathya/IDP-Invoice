"""HTTP client for the TALLY INTEGRATION bridge service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from backend.tally_integration import config

log = logging.getLogger(__name__)


@dataclass
class TallyPushResult:
    success: bool
    invoice_id: str
    invoice_number: str
    message: str
    error_reason: Optional[str]
    tally_company: Optional[str]
    pushed_at: datetime
    skipped: bool = False

    @property
    def erp_remark(self) -> str:
        if self.skipped:
            return self.message
        if self.success:
            return "Successful"
        short = (self.error_reason or self.message or "Unknown error")[:120]
        return f"Unsuccessful: {short}"


def _parse_bridge_response(data: dict[str, Any], invoice_id: str) -> TallyPushResult:
    pushed_at_raw = data.get("pushed_at")
    if pushed_at_raw:
        try:
            pushed_at = datetime.fromisoformat(str(pushed_at_raw).replace("Z", "+00:00"))
        except Exception:
            pushed_at = datetime.now(timezone.utc)
    else:
        pushed_at = datetime.now(timezone.utc)

    return TallyPushResult(
        success=bool(data.get("success")),
        invoice_id=str(data.get("invoice_id") or invoice_id),
        invoice_number=str(data.get("invoice_number") or ""),
        message=str(data.get("message") or ""),
        error_reason=data.get("error_reason"),
        tally_company=data.get("tally_company"),
        pushed_at=pushed_at,
        skipped=bool(data.get("skipped")),
    )


def ping_bridge() -> tuple[bool, Optional[str]]:
    if not config.TALLY_BRIDGE_URL:
        return False, "TALLY_BRIDGE_URL is not set"
    try:
        resp = requests.get(f"{config.TALLY_BRIDGE_URL}/health", timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("bridge_ok"):
            return False, "Bridge reported unhealthy"
        if not body.get("tally_reachable"):
            return False, body.get("tally_error") or "Tally is not reachable from bridge"
        return True, None
    except requests.RequestException as exc:
        return False, f"Bridge unreachable at {config.TALLY_BRIDGE_URL}: {exc}"


def push_invoice_via_bridge(invoice_id: str, *, force: bool = False) -> TallyPushResult:
    url = f"{config.TALLY_BRIDGE_URL}/push/invoice/{invoice_id}"
    try:
        resp = requests.post(
            url,
            params={"force": "true" if force else "false"},
            timeout=config.TALLY_BRIDGE_TIMEOUT,
        )
        if resp.status_code >= 400:
            detail = resp.text[:500]
            try:
                detail = resp.json().get("detail") or resp.json().get("error_reason") or detail
            except Exception:
                pass
            return TallyPushResult(
                success=False,
                invoice_id=invoice_id,
                invoice_number="",
                message="Bridge request failed",
                error_reason=str(detail),
                tally_company=None,
                pushed_at=datetime.now(timezone.utc),
            )
        return _parse_bridge_response(resp.json(), invoice_id)
    except requests.RequestException as exc:
        log.error("[tally_bridge] Push failed for %s: %s", invoice_id, exc)
        return TallyPushResult(
            success=False,
            invoice_id=invoice_id,
            invoice_number="",
            message="Bridge unreachable",
            error_reason=str(exc),
            tally_company=None,
            pushed_at=datetime.now(timezone.utc),
        )
