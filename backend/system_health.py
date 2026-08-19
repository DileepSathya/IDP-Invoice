"""Aggregate configuration and runtime health checks for the dashboard Health page."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from backend.agent_settings import get_agent_settings, is_agent_configured, masked_api_key, missing_agent_settings
from backend.app_paths import app_dir, logs_dir
from backend.app_logging import _resolve_log_file_path
from backend.pipeline_status import collect_pipeline_status

logger = logging.getLogger(__name__)

HealthLevel = Literal["ok", "warning", "error", "info", "disabled"]
OverallLevel = Literal["ok", "warning", "error"]

_LEVEL_RANK = {"ok": 0, "info": 0, "disabled": 0, "warning": 1, "error": 2}

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|smtp_user)\s*[=:]\s*\S+"
)


def _item(
    *,
    item_id: str,
    label: str,
    status: HealthLevel,
    message: str,
    fix_route: Optional[str] = None,
    fix_hint: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "message": message,
        "fix_route": fix_route,
        "fix_hint": fix_hint,
    }


def _section(
    *,
    section_id: str,
    title: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    status: HealthLevel = "ok"
    for item in items:
        item_status = str(item.get("status", "ok"))
        if _LEVEL_RANK.get(item_status, 0) > _LEVEL_RANK.get(status, 0):
            status = item_status  # type: ignore[assignment]
    return {"id": section_id, "title": title, "status": status, "items": items}


def _env_value(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _env_file_has_key(key: str) -> bool:
    env_path = app_dir() / ".env"
    if not env_path.is_file():
        return False
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", re.MULTILINE)
    return bool(pattern.search(env_path.read_text(encoding="utf-8", errors="replace")))


def _check_ai_section() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    settings = get_agent_settings()
    missing = missing_agent_settings()

    if "ai_model" in missing:
        items.append(
            _item(
                item_id="ai_model",
                label="AI model",
                status="error",
                message="No AI model selected.",
                fix_route="/settings",
                fix_hint="Open the account menu → AI and choose a model.",
            )
        )
    else:
        model = settings.get("model") or "gemini"
        items.append(
            _item(
                item_id="ai_model",
                label="AI model",
                status="ok",
                message=f"Using {model}.",
            )
        )

    api_key = settings.get("api_key") or ""
    if "api_key" in missing:
        items.append(
            _item(
                item_id="gemini_api_key",
                label="Gemini API key",
                status="error",
                message="API key is not set in .env or the AI settings pane.",
                fix_route="/settings",
                fix_hint="Open the account menu → AI and enter your Gemini API key.",
            )
        )
    else:
        masked = masked_api_key(api_key) or "configured"
        items.append(
            _item(
                item_id="gemini_api_key",
                label="Gemini API key",
                status="ok",
                message=f"Configured ({masked}).",
            )
        )

    env_path = app_dir() / ".env"
    if env_path.is_file():
        items.append(
            _item(
                item_id="env_file",
                label=".env file",
                status="ok",
                message=f"Found at {env_path.name} in the application folder.",
            )
        )
    else:
        items.append(
            _item(
                item_id="env_file",
                label=".env file",
                status="warning",
                message="No .env file yet — saving AI settings from the UI will create one.",
                fix_route="/settings",
                fix_hint="Configure the AI agent from the account menu.",
            )
        )

    return _section(section_id="ai", title="AI & configuration", items=items)


def _check_mongodb() -> dict[str, Any]:
    uri = _env_value("MONGO_URI") or "mongodb://localhost:27017"
    try:
        from backend.agents.database import _get_client

        _get_client().admin.command("ping")
        return _item(
            item_id="mongodb",
            label="MongoDB",
            status="ok",
            message=f"Connected ({uri}).",
        )
    except Exception as exc:
        return _item(
            item_id="mongodb",
            label="MongoDB",
            status="error",
            message=f"Cannot reach MongoDB at {uri}: {exc}",
            fix_route="/health",
            fix_hint="Start bundled MongoDB or fix MONGO_URI in .env.",
        )


def _check_postgres() -> dict[str, Any]:
    from backend import erp_db

    if not erp_db.is_configured():
        return _item(
            item_id="postgres",
            label="PostgreSQL (ERP)",
            status="disabled",
            message="POSTGRES_HOST is not set — ERP matching is disabled.",
        )

    ok, err = erp_db.connection_check()
    host = _env_value("POSTGRES_HOST")
    if ok:
        return _item(
            item_id="postgres",
            label="PostgreSQL (ERP)",
            status="ok",
            message=f"Connected to {host}.",
        )
    return _item(
        item_id="postgres",
        label="PostgreSQL (ERP)",
        status="warning",
        message=err or f"Cannot connect to PostgreSQL at {host}.",
        fix_route="/erp/settings",
        fix_hint="Ensure po-db.exe / PostgreSQL is running and POSTGRES_* values are correct.",
    )


def _check_services_section(pipeline: dict[str, Any]) -> dict[str, Any]:
    items = [_check_mongodb(), _check_postgres()]

    watcher_active = bool(pipeline.get("watcher_active"))
    items.append(
        _item(
            item_id="folder_watcher",
            label="Folder watcher",
            status="ok" if watcher_active else "info",
            message=(
                "Processing a file from to_be_processed."
                if watcher_active
                else "Idle — drops into to_be_processed are picked up when the watcher runs."
            ),
        )
    )

    queue_total = int(pipeline.get("queue_total") or 0)
    if queue_total > 0 and not watcher_active:
        items.append(
            _item(
                item_id="queue_backlog",
                label="Processing queue",
                status="warning",
                message=f"{queue_total} file(s) waiting in to_be_processed.",
                fix_route="/dashboard",
                fix_hint="Ensure the watcher service is running.",
            )
        )

    return _section(section_id="services", title="Services", items=items)


def _check_license_section() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        from license_validator import get_license_profile, validate_license

        validate_license()
        profile = get_license_profile()
        status: HealthLevel = "ok"
        message = str(profile.get("statusMessage") or "License active.")
        remaining_days = profile.get("remainingDays")
        invoices_remaining = profile.get("invoicesRemaining")
        if remaining_days is not None and int(remaining_days) <= 7:
            status = "warning"
        if invoices_remaining is not None and int(invoices_remaining) <= 10:
            status = "warning"
        items.append(
            _item(
                item_id="license",
                label="License",
                status=status,
                message=message,
                fix_route="/settings",
            )
        )
    except Exception as exc:
        items.append(
            _item(
                item_id="license",
                label="License",
                status="error",
                message=str(exc),
                fix_route="/settings",
                fix_hint="Place a valid license.lic next to the application executable.",
            )
        )
    return _section(section_id="license", title="License", items=items)


def _check_integrations_section() -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    tally_enabled = _env_value("TALLY_ENABLED").lower() in {"1", "true", "yes", "on"}
    if not tally_enabled:
        items.append(
            _item(
                item_id="tally",
                label="Tally integration",
                status="disabled",
                message="TALLY_ENABLED is false — Tally push is off.",
            )
        )
    else:
        try:
            from backend.tally_integration.bridge_client import ping_bridge
            from backend.tally_integration.config import is_tally_configured

            if not is_tally_configured():
                items.append(
                    _item(
                        item_id="tally",
                        label="Tally integration",
                        status="warning",
                        message="Tally is enabled but TALLY_BRIDGE_URL is not configured.",
                        fix_route="/settings/ledger",
                    )
                )
            else:
                reachable, err = ping_bridge()
                items.append(
                    _item(
                        item_id="tally",
                        label="Tally bridge",
                        status="ok" if reachable else "warning",
                        message="Bridge reachable." if reachable else (err or "Bridge not reachable."),
                        fix_route="/settings/ledger",
                        fix_hint="Start tally-bridge and ensure TallyPrime is open.",
                    )
                )
        except Exception as exc:
            items.append(
                _item(
                    item_id="tally",
                    label="Tally integration",
                    status="warning",
                    message=str(exc),
                    fix_route="/settings/ledger",
                )
            )

    try:
        from backend.hitl_notification_settings import get_hitl_notification_settings

        hitl = get_hitl_notification_settings()
        if not hitl.get("enabled"):
            items.append(
                _item(
                    item_id="hitl_email",
                    label="HITL email notifications",
                    status="disabled",
                    message="Notifications are disabled in Settings.",
                    fix_route="/settings/notifications",
                )
            )
        else:
            missing_smtp = [
                key
                for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
                if not _env_value(key) and not _env_file_has_key(key)
            ]
            recipients = hitl.get("recipient_emails") or []
            if missing_smtp:
                items.append(
                    _item(
                        item_id="hitl_email",
                        label="HITL email (SMTP)",
                        status="error",
                        message=f"Missing .env keys: {', '.join(missing_smtp)}.",
                        fix_route="/settings/notifications",
                        fix_hint="Set SMTP_* values in .env, then test from notification settings.",
                    )
                )
            elif not recipients:
                items.append(
                    _item(
                        item_id="hitl_email",
                        label="HITL email recipients",
                        status="warning",
                        message="Notifications are enabled but no recipient emails are saved.",
                        fix_route="/settings/notifications",
                    )
                )
            else:
                items.append(
                    _item(
                        item_id="hitl_email",
                        label="HITL email notifications",
                        status="ok",
                        message=f"Enabled for {len(recipients)} recipient(s). SMTP settings present in .env.",
                        fix_route="/settings/notifications",
                    )
                )
    except Exception as exc:
        items.append(
            _item(
                item_id="hitl_email",
                label="HITL email notifications",
                status="warning",
                message=f"Could not read notification settings: {exc}",
                fix_route="/settings/notifications",
            )
        )

    return _section(section_id="integrations", title="Integrations", items=items)


def _check_pipeline_section(pipeline: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    error_files = int(pipeline.get("error") or 0)
    gemini_quarantine = int(pipeline.get("gemini_api_error") or 0)
    quota_errors = int(pipeline.get("gemini_quota_error_count") or 0)
    network_errors = int(pipeline.get("network_error_count") or 0)

    if error_files:
        items.append(
            _item(
                item_id="pipeline_error_folder",
                label="ERROR folder",
                status="warning",
                message=f"{error_files} file(s) in invoices_data/ERROR.",
                fix_route="/",
                fix_hint="Review failed files on Home or check logs/idp.log.",
            )
        )
    else:
        items.append(
            _item(
                item_id="pipeline_error_folder",
                label="ERROR folder",
                status="ok",
                message="No files in ERROR folder.",
            )
        )

    if gemini_quarantine:
        items.append(
            _item(
                item_id="gemini_api_error_folder",
                label="Gemini retry queue",
                status="warning",
                message=(
                    f"{gemini_quarantine} file(s) in gemini_api_error waiting for API key/quota recovery."
                ),
                fix_route="/health",
                fix_hint="Fix GEMINI_API_KEY or quota, then the watcher will retry automatically.",
            )
        )

    if quota_errors:
        items.append(
            _item(
                item_id="gemini_quota_errors",
                label="Gemini quota errors",
                status="warning",
                message=f"{quota_errors} cumulative quota/rate-limit failure(s) recorded.",
                fix_route="/dashboard",
            )
        )

    if network_errors:
        items.append(
            _item(
                item_id="network_errors",
                label="Network errors",
                status="warning",
                message=f"{network_errors} cumulative network failure(s) during OCR/Gemini.",
                fix_route="/dashboard",
            )
        )

    if not items:
        items.append(
            _item(
                item_id="pipeline_ok",
                label="Pipeline",
                status="ok",
                message="No pipeline errors detected.",
            )
        )

    return _section(section_id="pipeline", title="Pipeline alerts", items=items)


def _redact_log_line(line: str) -> str:
    return _SECRET_RE.sub(r"\1=***", line)


def _read_recent_log_issues(limit: int = 12) -> list[dict[str, str]]:
    log_path = _resolve_log_file_path()
    if not log_path.is_file():
        return []

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    issues: list[dict[str, str]] = []
    for raw in reversed(lines):
        if len(issues) >= limit:
            break
        upper = raw.upper()
        if " ERROR " not in upper and " WARNING " not in upper:
            continue
        level = "error" if " ERROR " in upper else "warning"
        message = _redact_log_line(raw.strip())
        if len(message) > 240:
            message = message[:237] + "..."
        time_part = raw[:19] if len(raw) >= 19 else ""
        issues.append({"time": time_part, "level": level, "message": message})
    issues.reverse()
    return issues


def _compute_overall(sections: list[dict[str, Any]]) -> OverallLevel:
    overall: OverallLevel = "ok"
    for section in sections:
        section_status = str(section.get("status", "ok"))
        if _LEVEL_RANK.get(section_status, 0) > _LEVEL_RANK.get(overall, 0):
            overall = section_status  # type: ignore[assignment]
    return overall


def _critical_messages(sections: list[dict[str, Any]]) -> list[str]:
    critical_ids = {
        "gemini_api_key",
        "ai_model",
        "mongodb",
        "license",
        "hitl_email",
    }
    messages: list[str] = []
    for section in sections:
        for item in section.get("items") or []:
            if item.get("status") != "error":
                continue
            if item.get("id") not in critical_ids:
                continue
            label = str(item.get("label") or "Issue")
            detail = str(item.get("message") or "")
            messages.append(f"{label}: {detail}")
    return messages


def collect_system_health() -> dict[str, Any]:
    from backend.agents.database import get_pipeline_error_counts
    from backend import erp_db

    error_counts = get_pipeline_error_counts()
    pipeline = collect_pipeline_status(
        overview={
            "total_uploaded_files": 0,
            "healthy_files": 0,
            "error_files": 0,
            "hitl_flagged_files": 0,
            "hitl_process_pending": 0,
            "hitl_processed": 0,
            "system_processed": 0,
            "human_approved_files": 0,
            "gemini_quota_error_count": error_counts.get("gemini_quota_error_count", 0),
            "network_error_count": error_counts.get("network_error_count", 0),
            "erp_configured": erp_db.is_configured(),
            "erp_matched_files": 0,
            "erp_pending_files": 0,
        }
    )

    sections = [
        _check_ai_section(),
        _check_services_section(pipeline),
        _check_license_section(),
        _check_integrations_section(),
        _check_pipeline_section(pipeline),
    ]

    overall = _compute_overall(sections)
    counts = {"ok": 0, "warning": 0, "error": 0}
    for section in sections:
        for item in section.get("items") or []:
            level = str(item.get("status", "ok"))
            if level in counts:
                counts[level] += 1
            elif level in {"info", "disabled"}:
                counts["ok"] += 1

    recent_issues = _read_recent_log_issues()
    critical = _critical_messages(sections)

    if not is_agent_configured() and overall == "ok":
        overall = "error"
    if critical and overall == "ok":
        overall = "error"

    return {
        "overall": overall,
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "summary": counts,
        "sections": sections,
        "recent_issues": recent_issues,
        "critical_messages": critical,
    }
