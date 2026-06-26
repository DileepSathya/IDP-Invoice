from __future__ import annotations

import logging
import os
import hmac
import hashlib
import threading
import time
import json
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, List, Optional
from uuid import uuid4
from urllib import request as urllib_request
from urllib import error as urllib_error

from bson import ObjectId
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.app_paths import app_dir, frontend_dist_dir, load_app_dotenv
from backend.hitl_status import (
    calculate_hitl_flag as _calculate_hitl_flag,
    calculate_status_from_hitl as _calculate_status_from_hitl,
    pipeline_lifecycle_status,
    to_bool as _to_bool,
)
from backend.invoice_files import (
    ensure_invoice_data_layout,
    finalize_invoice_file,
    relocate_after_hitl_processed,
    resolve_invoice_file,
    staging_path_for_upload,
)
from backend.agents.chat_engine import chat as run_chat, get_suggestions
from backend.agents.chat_sessions import get_or_create_session, load_session
from backend.agents.database import (
    get_api_clients_collection,
    get_invoices_collection,
    get_jobs_collection,
    get_webhook_attempts_collection,
    record_pipeline_telemetry,
    store_invoice_result,
)
from backend.agents.ocr import ALLOWED_EXTS, process_file
from backend.app_logging import configure_logging

from datetime import datetime, date


logger = logging.getLogger(__name__)
user_audit_logger = logging.getLogger("user_audit")

_USER_LOG_PATH = app_dir() / "logs" / "user.log"
if not user_audit_logger.handlers:
    _USER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _user_fh = logging.FileHandler(_USER_LOG_PATH, encoding="utf-8")
    _user_fh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
    )
    user_audit_logger.addHandler(_user_fh)
    user_audit_logger.setLevel(logging.INFO)
    user_audit_logger.propagate = False


def _parse_invoice_date(value: Optional[str]) -> Optional[date]:
    """
    Try to parse the invoice_date string coming from Gemini into a Python date.

    Supports several common formats:
    - "YYYY-MM-DD"  (HTML date input / ISO)
    - "DD-Mon-YYYY" (e.g. "01-Dec-2025")
    - "DD-Mon-YY"   (e.g. "1-Dec-25")
    - "DD/MM/YYYY"
    - "DD/MM/YY"
    """
    if not value:
        return None

    value = str(value).strip()

    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _flatten_for_audit(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for k in sorted(value.keys()):
            next_prefix = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten_for_audit(value[k], next_prefix))
        return out
    if isinstance(value, list):
        for idx, item in enumerate(value):
            next_prefix = f"{prefix}[{idx}]"
            out.update(_flatten_for_audit(item, next_prefix))
        return out
    out[prefix or "$"] = value
    return out


def _compute_audit_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old_flat = _flatten_for_audit(before)
    new_flat = _flatten_for_audit(after)
    keys = sorted(set(old_flat.keys()) | set(new_flat.keys()))
    changes: list[str] = []
    for key in keys:
        old_v = old_flat.get(key)
        new_v = new_flat.get(key)
        if old_v != new_v:
            changes.append(f"{key}: {old_v!r} -> {new_v!r}")
    return changes


def _collect_telemetry_overview_counts() -> TelemetryOverviewResponse:
    coll = get_invoices_collection()
    total_uploaded_files = 0
    healthy_files = 0
    error_files = 0
    hitl_flagged_files = 0
    hitl_process_pending = 0
    hitl_processed = 0
    system_processed = 0
    human_approved_files = 0

    for doc in coll.find({}):
        total_uploaded_files += 1
        file_status = str(doc.get("file_status") or "").strip().lower()
        if file_status == "healthy file":
            healthy_files += 1
        elif file_status == "error":
            error_files += 1

        gemini_json = (doc.get("gemini") or {}).get("json") or {}
        if not isinstance(gemini_json, dict):
            gemini_json = {}
        additional_fields = gemini_json.get("additional_fields") or {}
        if not isinstance(additional_fields, dict):
            additional_fields = {}

        hitl = _to_bool(additional_fields.get("HITL", additional_fields.get("HIT")))
        status_raw = additional_fields.get("status")
        try:
            status_value = int(status_raw) if status_raw is not None else None
        except Exception:
            status_value = None

        if hitl:
            hitl_flagged_files += 1
        if status_value == 1:
            hitl_process_pending += 1
        elif status_value == 2:
            hitl_processed += 1
        else:
            system_processed += 1

        if _to_bool(additional_fields.get("human_approved")):
            human_approved_files += 1

    return TelemetryOverviewResponse(
        generated_at=datetime.utcnow().isoformat() + "Z",
        total_uploaded_files=total_uploaded_files,
        healthy_files=healthy_files,
        error_files=error_files,
        hitl_flagged_files=hitl_flagged_files,
        hitl_process_pending=hitl_process_pending,
        hitl_processed=hitl_processed,
        system_processed=system_processed,
        human_approved_files=human_approved_files,
    )


load_app_dotenv()
configure_logging()

ensure_invoice_data_layout()

API_KEY_HEADER = "x-api-key"
MAX_UPLOAD_BYTES = int(os.environ.get("API_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
RATE_LIMIT_PER_MIN = int(os.environ.get("API_RATE_LIMIT_PER_MIN", "60"))
_RATE_LIMIT_STATE: dict[str, list[float]] = {}
_RATE_LIMIT_LOCK = threading.Lock()
WEBHOOK_DEFAULT_SECRET = os.environ.get("WEBHOOK_DEFAULT_SECRET", "").strip()
WEBHOOK_TIMEOUT_SECONDS = int(os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "10"))
WEBHOOK_MAX_RETRIES = int(os.environ.get("WEBHOOK_MAX_RETRIES", "6"))
WEBHOOK_RETRY_BACKOFF_SECONDS = [10, 30, 120, 600, 1800, 7200]
WEBHOOK_ALLOWED_EVENTS = {"job.completed", "job.failed"}


class ApiClientContext(BaseModel):
    tenant_id: str
    client_name: Optional[str] = None
    key_hash: str


class ApiUploadResponse(BaseModel):
    job_id: str
    status: str
    request_id: Optional[str] = None
    webhook_enabled: bool = False
    callback_url: Optional[str] = None
    callback_events: List[str] = Field(default_factory=list)


class ApiJobStatusResponse(BaseModel):
    job_id: str
    status: str
    tenant_id: str
    invoice_id: Optional[str] = None
    file_status: Optional[str] = None
    error: Optional[str] = None
    external_ref: Optional[str] = None
    webhook_enabled: bool = False
    callback_url: Optional[str] = None
    callback_events: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class ApiInvoiceResponse(BaseModel):
    invoice_id: str
    tenant_id: str
    file_status: Optional[str] = None
    uploaded_file_path: Optional[str] = None
    created_at: Optional[str] = None
    gemini_json: dict[str, Any]


class WebhookAttemptResponse(BaseModel):
    webhook_id: str
    event: str
    attempt_no: int
    delivery_status: str
    response_status: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: Optional[str] = None


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return None


def _check_rate_limit(api_key_hash: str) -> None:
    now = time.time()
    threshold = now - 60.0
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_STATE.setdefault(api_key_hash, [])
        bucket[:] = [t for t in bucket if t >= threshold]
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            raise HTTPException(status_code=429, detail="Rate limit exceeded for this API key.")
        bucket.append(now)


def _client_from_env(api_key: str) -> Optional[ApiClientContext]:
    # Format: API_CLIENT_KEYS_JSON='{"my-raw-api-key-1":{"tenant_id":"acme","client_name":"ACME"}}'
    raw = os.environ.get("API_CLIENT_KEYS_JSON")
    if not raw:
        return None
    try:
        mapping = __import__("json").loads(raw)
    except Exception:
        logger.warning("Failed to parse API_CLIENT_KEYS_JSON.")
        return None
    item = mapping.get(api_key)
    if not isinstance(item, dict):
        return None
    tenant_id = str(item.get("tenant_id") or "").strip()
    if not tenant_id:
        return None
    return ApiClientContext(
        tenant_id=tenant_id,
        client_name=str(item.get("client_name") or "").strip() or None,
        key_hash=_sha256_hex(api_key),
    )


def _authenticate_api_key(request: Request) -> ApiClientContext:
    api_key = request.headers.get(API_KEY_HEADER) or request.headers.get(API_KEY_HEADER.upper())
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key.")

    key_hash = _sha256_hex(api_key)
    _check_rate_limit(key_hash)

    # First try Mongo (recommended), then env fallback.
    try:
        clients = get_api_clients_collection()
        row = clients.find_one({"key_hash": key_hash, "active": True})
    except Exception:
        row = None
    if isinstance(row, dict):
        tenant_id = str(row.get("tenant_id") or "").strip()
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid API key mapping.")
        return ApiClientContext(
            tenant_id=tenant_id,
            client_name=str(row.get("client_name") or "").strip() or None,
            key_hash=key_hash,
        )

    env_client = _client_from_env(api_key)
    if env_client:
        # Constant-time check to avoid timing leaks in env mode path.
        if not hmac.compare_digest(env_client.key_hash, key_hash):
            raise HTTPException(status_code=401, detail="Invalid API key.")
        return env_client

    raise HTTPException(status_code=401, detail="Invalid API key.")


def _parse_callback_events(raw: Optional[str]) -> list[str]:
    if not raw:
        return ["job.completed", "job.failed"]
    text = str(raw).strip()
    if not text:
        return ["job.completed", "job.failed"]
    events: list[str] = []
    if text.startswith("["):
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                events = [str(v).strip() for v in arr]
        except Exception:
            events = []
    else:
        events = [part.strip() for part in text.split(",")]
    clean = [evt for evt in events if evt in WEBHOOK_ALLOWED_EVENTS]
    return clean or ["job.completed", "job.failed"]


def _build_webhook_signature(secret: str, timestamp: int, body_bytes: bytes) -> str:
    signed_payload = f"{timestamp}.".encode("utf-8") + body_bytes
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _record_webhook_attempt(
    *,
    webhook_id: str,
    job_id: str,
    tenant_id: str,
    event: str,
    attempt_no: int,
    callback_url: str,
    request_id: Optional[str],
    request_body: bytes,
    response_status: Optional[int],
    response_body: Optional[str],
    latency_ms: int,
    delivery_status: str,
    error_message: Optional[str],
    next_retry_in_seconds: Optional[int],
) -> None:
    try:
        attempts = get_webhook_attempts_collection()
        attempts.insert_one(
            {
                "webhook_id": webhook_id,
                "job_id": job_id,
                "tenant_id": tenant_id,
                "event": event,
                "attempt_no": attempt_no,
                "callback_url": callback_url,
                "request_id": request_id,
                "request_body_sha256": hashlib.sha256(request_body).hexdigest(),
                "response_status": response_status,
                "response_body_snippet": (response_body or "")[:1000],
                "latency_ms": latency_ms,
                "delivery_status": delivery_status,
                "error_message": error_message,
                "next_retry_in_seconds": next_retry_in_seconds,
                "created_at": datetime.utcnow(),
            }
        )
    except Exception as e:
        logger.warning("Failed to record webhook attempt for job_id=%s event=%s: %s", job_id, event, e)


def _deliver_webhook_with_retries(
    *,
    event: str,
    callback_url: str,
    callback_secret: str,
    payload: dict[str, Any],
    job_id: str,
    tenant_id: str,
    request_id: Optional[str],
) -> None:
    webhook_id = str(uuid4())
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    max_attempts = max(1, WEBHOOK_MAX_RETRIES)
    backoff = WEBHOOK_RETRY_BACKOFF_SECONDS
    for attempt in range(1, max_attempts + 1):
        ts = int(time.time())
        signature = _build_webhook_signature(callback_secret, ts, body)
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Id": webhook_id,
            "X-Webhook-Event": event,
            "X-Webhook-Timestamp": str(ts),
            "X-Webhook-Signature": signature,
        }
        if request_id:
            headers["X-Request-Id"] = request_id
        req = urllib_request.Request(callback_url, data=body, headers=headers, method="POST")
        start = time.perf_counter()
        response_status: Optional[int] = None
        response_body: Optional[str] = None
        error_message: Optional[str] = None
        should_retry = False
        try:
            with urllib_request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS) as resp:
                response_status = int(resp.status)
                response_bytes = resp.read()
                response_body = response_bytes.decode("utf-8", errors="replace")
            if response_status < 200 or response_status >= 300:
                should_retry = response_status in {408, 409, 425, 429} or response_status >= 500
                if not should_retry:
                    logger.warning(
                        "Webhook permanently rejected request_id=%s job_id=%s event=%s status=%s",
                        request_id,
                        job_id,
                        event,
                        response_status,
                    )
        except urllib_error.HTTPError as e:
            response_status = int(e.code)
            try:
                response_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                response_body = None
            should_retry = response_status in {408, 409, 425, 429} or response_status >= 500
            error_message = f"http_error:{response_status}"
        except Exception as e:
            should_retry = True
            error_message = str(e)
        latency_ms = int((time.perf_counter() - start) * 1000)
        is_success = response_status is not None and 200 <= response_status < 300
        next_retry = None
        if not is_success and should_retry and attempt < max_attempts:
            backoff_idx = min(attempt - 1, len(backoff) - 1)
            next_retry = backoff[backoff_idx]
        _record_webhook_attempt(
            webhook_id=webhook_id,
            job_id=job_id,
            tenant_id=tenant_id,
            event=event,
            attempt_no=attempt,
            callback_url=callback_url,
            request_id=request_id,
            request_body=body,
            response_status=response_status,
            response_body=response_body,
            latency_ms=latency_ms,
            delivery_status="success" if is_success else ("retrying" if next_retry is not None else "failed"),
            error_message=error_message,
            next_retry_in_seconds=next_retry,
        )
        if is_success:
            logger.info(
                "webhook_delivered request_id=%s tenant_id=%s job_id=%s event=%s attempt=%s status=%s",
                request_id,
                tenant_id,
                job_id,
                event,
                attempt,
                response_status,
            )
            return
        if not should_retry or attempt >= max_attempts:
            logger.warning(
                "webhook_delivery_failed request_id=%s tenant_id=%s job_id=%s event=%s attempt=%s status=%s error=%s",
                request_id,
                tenant_id,
                job_id,
                event,
                attempt,
                response_status,
                error_message,
            )
            return
        time.sleep(next_retry or 0)


def _send_job_webhook_if_enabled(
    *,
    job_id: str,
    tenant_id: str,
    event: str,
    request_id: Optional[str],
    invoice_id: Optional[str],
    file_status: Optional[str],
    error_message: Optional[str],
) -> None:
    jobs = get_jobs_collection()
    job = jobs.find_one({"job_id": job_id, "tenant_id": tenant_id})
    if not isinstance(job, dict):
        return
    callback_url = str(job.get("callback_url") or "").strip()
    if not callback_url:
        return
    callback_events = job.get("callback_events") or ["job.completed", "job.failed"]
    if not isinstance(callback_events, list):
        callback_events = ["job.completed", "job.failed"]
    if event not in callback_events:
        return
    callback_secret = str(job.get("callback_secret") or "").strip()
    if not callback_secret:
        logger.warning("Skipping webhook (missing callback secret) for job_id=%s", job_id)
        return
    payload: dict[str, Any] = {
        "event": event,
        "occurred_at": datetime.utcnow().isoformat() + "Z",
        "tenant_id": tenant_id,
        "job": {
            "job_id": job_id,
            "status": str(job.get("status") or ""),
            "external_ref": job.get("external_ref"),
            "request_id": request_id,
        },
    }
    if event == "job.completed":
        payload["result"] = {"invoice_id": invoice_id, "file_status": file_status}
    if event == "job.failed":
        payload["error"] = {"code": "PIPELINE_ERROR", "message": error_message or "Unknown error"}
    _deliver_webhook_with_retries(
        event=event,
        callback_url=callback_url,
        callback_secret=callback_secret,
        payload=payload,
        job_id=job_id,
        tenant_id=tenant_id,
        request_id=request_id,
    )


def _process_job(
    job_id: str,
    tenant_id: str,
    file_path: str,
    request_id: Optional[str],
) -> None:
    jobs = get_jobs_collection()
    invoices = get_invoices_collection()
    file_received_time = datetime.utcnow()
    file_ext = Path(file_path).suffix.lower().lstrip(".")
    file_size = 0
    try:
        file_size = Path(file_path).stat().st_size
    except Exception:
        file_size = 0
    started_at = datetime.utcnow()
    jobs.update_one(
        {"job_id": job_id, "tenant_id": tenant_id},
        {"$set": {"status": "processing", "started_at": started_at}},
    )
    logger.info(
        "job_started request_id=%s tenant_id=%s job_id=%s file_path=%s",
        request_id,
        tenant_id,
        job_id,
        file_path,
    )
    staging_path = Path(file_path)
    try:
        from license_validator import InvoiceQuotaExceeded, ensure_invoice_quota_available

        ensure_invoice_quota_available()
        ocr_result = process_file(file_path)
        gemini_json_norm: dict[str, Any] = dict(ocr_result.gemini_json or {})
        invoice_number = gemini_json_norm.get("invoice_number") or gemini_json_norm.get("invoice")
        invoice_number_norm = str(invoice_number).strip() if invoice_number is not None else ""
        file_status = "error" if not invoice_number_norm else "healthy file"
        _, status_value = pipeline_lifecycle_status(gemini_json_norm, file_status=file_status)
        final_path = finalize_invoice_file(
            staging_path,
            file_status=file_status,
            status=status_value,
            pipeline_failed=False,
        )
        inserted_id = store_invoice_result(
            file_path=str(final_path),
            uploaded_file_path=str(final_path),
            ocr_text=ocr_result.ocr_text,
            gemini_model=ocr_result.gemini_model or (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"),
            gemini_json=gemini_json_norm,
            gemini_raw_text=ocr_result.gemini_raw_text,
            file_status=file_status,
        )
        invoices.update_one(
            {"_id": ObjectId(inserted_id)},
            {"$set": {"tenant_id": tenant_id, "job_id": job_id}},
        )
        try:
            hitl_value, _ = _sync_hitl_and_status(
                invoices,
                ObjectId(inserted_id),
                gemini_json_norm,
                uploaded_file_path=str(final_path),
            )
            invoices.update_one(
                {"_id": ObjectId(inserted_id)},
                {
                    "$set": {
                        "gemini.json.additional_fields.HITL": hitl_value,
                        "gemini.json.additional_fields.human_processed": False,
                        "gemini.json.additional_fields.status": status_value,
                    }
                },
            )
        except Exception:
            pass
        from license_validator import increment_invoice_count

        increment_invoice_count()
        completed_at = datetime.utcnow()
        record_pipeline_telemetry(
            {
                "run_id": job_id,
                "source": "api_v1",
                "file_id": inserted_id,
                "tenant_id": tenant_id,
                "file_name": final_path.name,
                "file_size": file_size,
                "file_type": file_ext,
                "file_received_time": file_received_time,
                "preprocessing_start_time": ocr_result.preprocessing_start_time,
                "preprocessing_end_time": ocr_result.preprocessing_end_time,
                "ocr_start_time": ocr_result.ocr_start_time,
                "ocr_end_time": ocr_result.ocr_end_time,
                "gemini_start_time": ocr_result.gemini_start_time,
                "gemini_end_time": ocr_result.gemini_end_time,
                "db_insert_time": completed_at,
                "preprocessing_latency": ocr_result.preprocessing_latency,
                "ocr_latency": ocr_result.ocr_latency,
                "gemini_latency": ocr_result.gemini_latency,
                "gemini_prompt_tokens": ocr_result.gemini_prompt_tokens,
                "gemini_output_tokens": ocr_result.gemini_output_tokens,
                "gemini_total_tokens": ocr_result.gemini_total_tokens,
                "gemini_model": ocr_result.gemini_model,
                "total_pipeline_latency": (completed_at - file_received_time).total_seconds(),
                "status": "success",
                "error_stage": None,
                "error_message": None,
            }
        )
        jobs.update_one(
            {"job_id": job_id, "tenant_id": tenant_id},
            {
                "$set": {
                    "status": "completed",
                    "invoice_id": inserted_id,
                    "file_status": file_status,
                    "completed_at": completed_at,
                }
            },
        )
        logger.info(
            "job_completed request_id=%s tenant_id=%s job_id=%s invoice_id=%s",
            request_id,
            tenant_id,
            job_id,
            inserted_id,
        )
        _send_job_webhook_if_enabled(
            job_id=job_id,
            tenant_id=tenant_id,
            event="job.completed",
            request_id=request_id,
            invoice_id=inserted_id,
            file_status=file_status,
            error_message=None,
        )
    except Exception as e:
        from license_validator import InvoiceQuotaExceeded

        if staging_path.is_file():
            try:
                finalize_invoice_file(
                    staging_path,
                    file_status="error",
                    status=0,
                    pipeline_failed=True,
                )
            except Exception:
                pass
        error_stage = "license" if isinstance(e, InvoiceQuotaExceeded) else "pipeline"
        record_pipeline_telemetry(
            {
                "run_id": job_id,
                "source": "api_v1",
                "file_id": None,
                "tenant_id": tenant_id,
                "file_name": staging_path.name,
                "file_size": file_size,
                "file_type": file_ext,
                "file_received_time": file_received_time,
                "status": "error",
                "error_stage": error_stage,
                "error_message": str(e),
                "total_pipeline_latency": (datetime.utcnow() - file_received_time).total_seconds(),
            }
        )
        jobs.update_one(
            {"job_id": job_id, "tenant_id": tenant_id},
            {"$set": {"status": "failed", "error": str(e), "completed_at": datetime.utcnow()}},
        )
        if isinstance(e, InvoiceQuotaExceeded):
            logger.warning(
                "job_failed request_id=%s tenant_id=%s job_id=%s error=%s",
                request_id,
                tenant_id,
                job_id,
                e,
            )
        else:
            logger.exception(
                "job_failed request_id=%s tenant_id=%s job_id=%s error=%s",
                request_id,
                tenant_id,
                job_id,
                e,
            )
        _send_job_webhook_if_enabled(
            job_id=job_id,
            tenant_id=tenant_id,
            event="job.failed",
            request_id=request_id,
            invoice_id=None,
            file_status=None,
            error_message=str(e),
        )


class InvoiceSummary(BaseModel):
    id: str
    file_path: Optional[str]
    uploaded_file_path: Optional[str]
    file_status: Optional[str] = None
    # Display fields for the main dashboard table
    invoice_number: Optional[Any]
    total_amount: Optional[Any]
    hsn_value: Optional[Any]
    invoice_date: Optional[str] = None
    seller: Optional[str] = None
    service_category: Optional[str] = None
    line_item_index: Optional[int] = None
    status: Optional[int] = None
    payment_status: Optional[str] = None
    quantity: Optional[Any] = None
    price_per_unit: Optional[Any] = None
    amount: Optional[Any] = None
    tax_rate: Optional[Any] = None
    tax_amount: Optional[Any] = None
    amount_after_tax: Optional[Any] = None
    sub_total: Optional[Any] = None
    sgst_rate: Optional[Any] = None
    sgst_amount: Optional[Any] = None
    cgst_rate: Optional[Any] = None
    cgst_amount: Optional[Any] = None
    summary_total_amount: Optional[Any] = None
    hitl: Optional[bool] = None
    deblurred_applied: Optional[bool] = None
    human_approved: Optional[bool] = None


class InvoiceListResponse(BaseModel):
    items: List[InvoiceSummary]
    total_amount_sum: float


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    messages: List[ChatMessage] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    mode: Optional[str] = None


class LicenseProfileResponse(BaseModel):
    plan: str
    planLabel: str
    customerId: str
    issuedAt: Optional[str] = None
    expiresAt: Optional[str] = None
    remainingDays: Optional[int] = None
    invoiceLimit: Optional[int] = None
    invoicesUsed: Optional[int] = None
    invoicesRemaining: Optional[int] = None
    isUnlimited: bool
    statusMessage: str


class SearchValuesResponse(BaseModel):
    values: List[str]


class SearchSuggestionsResponse(BaseModel):
    suggestions: List[str]


class TelemetryOverviewResponse(BaseModel):
    generated_at: str
    total_uploaded_files: int
    healthy_files: int
    error_files: int
    hitl_flagged_files: int
    hitl_process_pending: int
    hitl_processed: int
    system_processed: int
    human_approved_files: int


class PipelineStatusResponse(BaseModel):
    generated_at: str
    awaiting_processing: int
    in_process: int
    processed: int
    error: int
    gemini_api_error: int = 0
    hitl_pending: int
    queue_total: int
    api_staging: int
    watcher_active: bool
    async_jobs: int
    stored_total: int
    stored_healthy: int
    stored_errors: int
    hitl_flagged_total: int
    hitl_review_pending: int
    hitl_reviewed: int
    system_processed: int
    human_approved_files: int


class InvoiceUpdate(BaseModel):
    invoice_number: Optional[str] = None
    total_amount: Optional[str] = None
    hsn_value: Optional[str] = None
    invoice_date: Optional[str] = None
    seller: Optional[str] = None
    service_category: Optional[str] = None
    line_item_index: Optional[int] = None
    status: Optional[str] = None
    payment_status: Optional[str] = None
    quantity: Optional[str] = None
    price_per_unit: Optional[str] = None
    amount: Optional[str] = None
    tax_rate: Optional[str] = None
    tax_amount: Optional[str] = None
    amount_after_tax: Optional[str] = None
    sub_total: Optional[str] = None
    sgst_rate: Optional[str] = None
    sgst_amount: Optional[str] = None
    cgst_rate: Optional[str] = None
    cgst_amount: Optional[str] = None


class LineItemCreate(BaseModel):
    hsn_number: Optional[str] = ""
    service: Optional[str] = ""
    quantity: Optional[str] = ""
    price_per_unit: Optional[str] = ""
    amount: Optional[str] = ""
    tax_rate: Optional[str] = ""
    tax_amount: Optional[str] = ""
    amount_after_tax: Optional[str] = ""


class InvoiceJsonEditorResponse(BaseModel):
    id: str
    uploaded_file_path: Optional[str] = None
    gemini_json: dict[str, Any]
    line_items: List[dict[str, Any]]


class InvoiceJsonEditorUpdate(BaseModel):
    gemini_json: dict[str, Any]
    line_items: Optional[List[dict[str, Any]]] = None


def _invoice_summary_row_from_doc(doc: dict[str, Any], *, line_item_index: Optional[int]) -> InvoiceSummary:
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    additional_fields = gemini_json.get("additional_fields") or {}
    invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
    status_value = additional_fields.get("status")
    try:
        status_value = int(status_value) if status_value is not None else None
    except Exception:
        status_value = None
    payment_status_value = gemini_json.get("payment_status") or "not_paid"
    total_amount = (
        gemini_json.get("total_amount")
        or gemini_json.get("grand_total")
        or gemini_json.get("amount")
    )
    invoice_date_raw = gemini_json.get("invoice_date") or gemini_json.get("date")
    seller_value = gemini_json.get("seller")

    line_items = gemini_json.get("line_items") or []
    if isinstance(line_items, list) and line_item_index is not None:
        li = line_items[line_item_index] if 0 <= line_item_index < len(line_items) else {}
        if not isinstance(li, dict):
            li = {}
        hsn_value = li.get("hsn_number") or li.get("HSN_number") or li.get("HSN")
        service_value = li.get("service") or li.get("description")
        quantity_value = li.get("quantity") or li.get("qty")
        price_per_unit_value = li.get("price_per_unit") or li.get("rate") or li.get("unit_price")
        amount_value = li.get("amount") or li.get("total")
        tax_rate_value = li.get("tax_rate")
        tax_amount_value = li.get("tax_amount")
        amount_after_tax_value = li.get("amount_after_tax")
        row_index = line_item_index
    else:
        hsn_value = (
            gemini_json.get("hsn_number")
            or gemini_json.get("HSN_number")
            or gemini_json.get("HSN")
        )
        service_value = gemini_json.get("service")
        quantity_value = gemini_json.get("quantity")
        price_per_unit_value = gemini_json.get("price_per_unit")
        amount_value = gemini_json.get("amount")
        tax_rate_value = gemini_json.get("tax_rate")
        tax_amount_value = gemini_json.get("tax_amount")
        amount_after_tax_value = gemini_json.get("amount_after_tax")
        row_index = None

    hitl_value = None
    try:
        hitl_raw = additional_fields.get("HITL", additional_fields.get("HIT"))
        hitl_value = _to_bool(hitl_raw)
    except Exception:
        hitl_value = None

    deblurred_applied_value: Optional[bool] = None
    try:
        deblurred_applied_value = _to_bool(additional_fields.get("deblurred_applied"))
    except Exception:
        deblurred_applied_value = None

    human_approved_value: Optional[bool] = None
    try:
        human_approved_value = _to_bool(additional_fields.get("human_approved"))
    except Exception:
        human_approved_value = None

    return InvoiceSummary(
        id=str(doc.get("_id")),
        file_path=doc.get("file_path"),
        uploaded_file_path=doc.get("uploaded_file_path"),
        file_status=doc.get("file_status"),
        invoice_number=invoice_number,
        total_amount=total_amount,
        hsn_value=hsn_value,
        invoice_date=invoice_date_raw,
        seller=seller_value,
        service_category=service_value,
        line_item_index=row_index,
        status=status_value,
        payment_status=payment_status_value,
        quantity=quantity_value,
        price_per_unit=price_per_unit_value,
        amount=amount_value,
        tax_rate=tax_rate_value,
        tax_amount=tax_amount_value,
        amount_after_tax=amount_after_tax_value,
        sub_total=additional_fields.get("sub_total"),
        sgst_rate=additional_fields.get("sgst_rate"),
        sgst_amount=additional_fields.get("sgst_amount"),
        cgst_rate=additional_fields.get("cgst_rate"),
        cgst_amount=additional_fields.get("cgst_amount"),
        summary_total_amount=additional_fields.get("summary_total_amount"),
        hitl=hitl_value,
        deblurred_applied=deblurred_applied_value,
        human_approved=human_approved_value,
    )


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _sum_line_item_amounts(line_items: Any) -> Optional[float]:
    if not isinstance(line_items, list) or len(line_items) == 0:
        return None
    running = 0.0
    seen = False
    for li in line_items:
        if not isinstance(li, dict):
            continue
        n = _to_float(li.get("amount") or li.get("total"))
        if n is None:
            continue
        running += n
        seen = True
    return running if seen else None


def _sum_line_item_amounts_after_tax(line_items: Any) -> Optional[float]:
    if not isinstance(line_items, list) or len(line_items) == 0:
        return None
    running = 0.0
    seen = False
    for li in line_items:
        if not isinstance(li, dict):
            continue
        n = _to_float(li.get("amount_after_tax"))
        if n is None:
            continue
        running += n
        seen = True
    return running if seen else None


def _calculate_summary_total_amount(gemini_json: dict[str, Any]) -> Optional[float]:
    additional_fields = gemini_json.get("additional_fields") or {}
    line_items = gemini_json.get("line_items") or []

    # Preferred source: line-item final values (amount_after_tax).
    amount_after_tax_sum = _sum_line_item_amounts_after_tax(line_items)
    if amount_after_tax_sum is not None:
        return amount_after_tax_sum

    base_amount = _sum_line_item_amounts(line_items)
    if base_amount is None:
        base_amount = _to_float(
            additional_fields.get("sub_total")
            or additional_fields.get("subtotal_after_discount")
            or gemini_json.get("amount")
        )

    sgst_amount = _to_float(additional_fields.get("sgst_amount")) or 0.0
    cgst_amount = _to_float(additional_fields.get("cgst_amount")) or 0.0
    igst_amount = _to_float(additional_fields.get("igst_amount") or additional_fields.get("total_igst_amount")) or 0.0
    round_off = _to_float(additional_fields.get("round_off")) or 0.0

    if base_amount is None:
        return _to_float(gemini_json.get("total_amount") or gemini_json.get("grand_total"))
    return base_amount + sgst_amount + cgst_amount + igst_amount + round_off


def _sync_hitl_and_status(
    coll: Any,
    oid: ObjectId,
    gemini_json: dict[str, Any],
    *,
    mark_human_processed: bool = False,
    uploaded_file_path: Optional[str] = None,
) -> tuple[bool, int]:
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    hitl_value = _calculate_hitl_flag(gemini_json)
    existing_hitl = additional_fields.get("HITL", additional_fields.get("HIT"))
    existing_hitl_bool = _to_bool(existing_hitl)

    existing_human_processed = _to_bool(
        additional_fields.get("human_processed") or additional_fields.get("ever_hitl_true")
    )
    human_processed = bool(existing_human_processed or mark_human_processed)
    status_value = _calculate_status_from_hitl(
        hitl_value=hitl_value,
        human_processed=human_processed,
    )

    existing_status_raw = additional_fields.get("status")
    try:
        existing_status = int(existing_status_raw) if existing_status_raw is not None else None
    except Exception:
        existing_status = None

    update_doc: dict[str, Any] = {}
    if existing_hitl is None or existing_hitl_bool != hitl_value:
        update_doc["gemini.json.additional_fields.HITL"] = hitl_value
    if existing_human_processed != human_processed:
        update_doc["gemini.json.additional_fields.human_processed"] = human_processed
        # Keep legacy key in sync for compatibility with old records/views.
        update_doc["gemini.json.additional_fields.ever_hitl_true"] = human_processed
    if existing_status != status_value:
        update_doc["gemini.json.additional_fields.status"] = status_value

    if update_doc:
        try:
            coll.update_one({"_id": oid}, {"$set": update_doc})
        except Exception:
            pass

    if human_processed and status_value == 2 and uploaded_file_path:
        new_path = relocate_after_hitl_processed(uploaded_file_path)
        if new_path and new_path != uploaded_file_path:
            try:
                coll.update_one(
                    {"_id": oid},
                    {"$set": {"uploaded_file_path": new_path, "file_path": new_path}},
                )
            except Exception:
                pass

    return hitl_value, status_value


class DeleteInvoicesRequest(BaseModel):
    ids: List[str]


class DeleteInvoicesResponse(BaseModel):
    requested_count: int
    deleted_count: int


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    # Uvicorn may replace root log handlers after import; re-attach logs/idp.log here so
    # post-OCR lines (uploads path, MongoDB insert) are always recorded.
    configure_logging()
    yield


class StripApiPrefixMiddleware:
    """Map /api/* (Vite dev proxy paths) to backend routes in production."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                scope = dict(scope)
                scope["path"] = path[4:] if len(path) > 4 else "/"
        await self.app(scope, receive, send)


app = FastAPI(title="IDP Invoices API", lifespan=_app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(StripApiPrefixMiddleware)

@app.get("/raw/{filename:path}")
def serve_invoice_file(filename: str) -> FileResponse:
    """Serve invoice images from Completed / HITL_pending / ERROR (by basename)."""
    resolved = resolve_invoice_file(filename)
    if not resolved:
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(resolved)


@app.middleware("http")
async def add_observability_and_security_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - start) * 1000)
    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    response.headers["cache-control"] = "no-store"
    logger.info(
        "api_request request_id=%s method=%s path=%s status=%s latency_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
    )
    return response


SEARCH_FIELD_PATHS: dict[str, list[str]] = {
    "invoice_number": ["gemini.json.invoice_number", "gemini.json.invoice"],
    "hsn_value": [
        "gemini.json.hsn_number",
        "gemini.json.HSN_number",
        "gemini.json.HSN",
        "gemini.json.line_items.hsn_number",
        "gemini.json.line_items.HSN_number",
        "gemini.json.line_items.HSN",
    ],
    "seller": ["gemini.json.seller"],
    "service_category": [
        "gemini.json.service",
        "gemini.json.line_items.service",
        "gemini.json.line_items.description",
    ],
    "file_status": ["file_status"],
}


def _all_distinct_values(field: str) -> list[str]:
    if field not in SEARCH_FIELD_PATHS:
        raise HTTPException(status_code=400, detail="Unsupported search field.")

    coll = get_invoices_collection()
    values: set[str] = set()
    for path in SEARCH_FIELD_PATHS[field]:
        try:
            distinct_values = coll.distinct(path)
        except Exception:
            distinct_values = []
        for raw_value in distinct_values:
            if raw_value is None:
                continue
            text = str(raw_value).strip()
            if text:
                values.add(text)
    return sorted(values, key=lambda v: v.lower())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/license", response_model=LicenseProfileResponse)
def get_license_profile() -> LicenseProfileResponse:
    from license_validator import get_license_profile as _get_license_profile

    return LicenseProfileResponse(**_get_license_profile())


@app.post("/v1/files", response_model=ApiUploadResponse, status_code=202)
async def v1_upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    external_ref: Optional[str] = Form(default=None),
    callback_url: Optional[str] = Form(default=None),
    callback_events: Optional[str] = Form(default=None),
    callback_secret: Optional[str] = Form(default=None),
    client: ApiClientContext = Depends(_authenticate_api_key),
) -> ApiUploadResponse:
    try:
        from license_validator import InvoiceQuotaExceeded, ensure_invoice_quota_available

        ensure_invoice_quota_available()
    except InvoiceQuotaExceeded as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {sorted(ALLOWED_EXTS)}",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max allowed bytes: {MAX_UPLOAD_BYTES}",
        )

    target_path = staging_path_for_upload(file.filename or f"file{ext}")
    target_path.write_bytes(content)

    job_id = str(uuid4())
    jobs = get_jobs_collection()
    now = datetime.utcnow()
    callback_url_value = str(callback_url or "").strip() or None
    callback_secret_value = str(callback_secret or "").strip() or WEBHOOK_DEFAULT_SECRET or None
    if callback_url_value and not callback_url_value.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="callback_url must start with http:// or https://")
    if callback_url_value and not callback_secret_value:
        raise HTTPException(
            status_code=400,
            detail="callback_secret is required when callback_url is provided (or set WEBHOOK_DEFAULT_SECRET).",
        )
    callback_events_value = _parse_callback_events(callback_events) if callback_url_value else []
    jobs.insert_one(
        {
            "job_id": job_id,
            "tenant_id": client.tenant_id,
            "status": "queued",
            "original_filename": file.filename,
            "external_ref": str(external_ref).strip() if external_ref else None,
            "uploaded_file_path": str(target_path),
            "request_id": getattr(request.state, "request_id", None),
            "callback_url": callback_url_value,
            "callback_events": callback_events_value,
            "callback_secret": callback_secret_value,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
        }
    )

    background_tasks.add_task(
        _process_job,
        job_id,
        client.tenant_id,
        str(target_path),
        getattr(request.state, "request_id", None),
    )
    return ApiUploadResponse(
        job_id=job_id,
        status="queued",
        request_id=getattr(request.state, "request_id", None),
        webhook_enabled=bool(callback_url_value),
        callback_url=callback_url_value,
        callback_events=callback_events_value,
    )


@app.get("/v1/jobs/{job_id}", response_model=ApiJobStatusResponse)
def v1_get_job(job_id: str, client: ApiClientContext = Depends(_authenticate_api_key)) -> ApiJobStatusResponse:
    jobs = get_jobs_collection()
    doc = jobs.find_one({"job_id": job_id, "tenant_id": client.tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found.")
    return ApiJobStatusResponse(
        job_id=job_id,
        status=str(doc.get("status") or "unknown"),
        tenant_id=client.tenant_id,
        invoice_id=str(doc.get("invoice_id")) if doc.get("invoice_id") else None,
        file_status=doc.get("file_status"),
        error=doc.get("error"),
        external_ref=doc.get("external_ref"),
        webhook_enabled=bool(doc.get("callback_url")),
        callback_url=doc.get("callback_url"),
        callback_events=doc.get("callback_events") or [],
        created_at=_iso(doc.get("created_at")),
        started_at=_iso(doc.get("started_at")),
        completed_at=_iso(doc.get("completed_at")),
    )


@app.get("/v1/jobs/{job_id}/webhook-attempts", response_model=List[WebhookAttemptResponse])
def v1_get_webhook_attempts(
    job_id: str,
    client: ApiClientContext = Depends(_authenticate_api_key),
) -> List[WebhookAttemptResponse]:
    attempts = get_webhook_attempts_collection()
    docs = attempts.find(
        {"job_id": job_id, "tenant_id": client.tenant_id},
        sort=[("attempt_no", 1), ("created_at", 1)],
    )
    out: List[WebhookAttemptResponse] = []
    for doc in docs:
        out.append(
            WebhookAttemptResponse(
                webhook_id=str(doc.get("webhook_id") or ""),
                event=str(doc.get("event") or ""),
                attempt_no=int(doc.get("attempt_no") or 0),
                delivery_status=str(doc.get("delivery_status") or "unknown"),
                response_status=doc.get("response_status"),
                error_message=doc.get("error_message"),
                latency_ms=doc.get("latency_ms"),
                created_at=_iso(doc.get("created_at")),
            )
        )
    return out


@app.get("/v1/invoices/{invoice_id}", response_model=ApiInvoiceResponse)
def v1_get_invoice(invoice_id: str, client: ApiClientContext = Depends(_authenticate_api_key)) -> ApiInvoiceResponse:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")
    doc = coll.find_one({"_id": oid, "tenant_id": client.tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        gemini_json = {}
    return ApiInvoiceResponse(
        invoice_id=str(doc.get("_id")),
        tenant_id=client.tenant_id,
        file_status=doc.get("file_status"),
        uploaded_file_path=doc.get("uploaded_file_path"),
        created_at=_iso(doc.get("created_at")),
        gemini_json=gemini_json,
    )


@app.get("/v1/invoices", response_model=List[ApiInvoiceResponse])
def v1_list_invoices(
    limit: int = Query(default=50, ge=1, le=200),
    client: ApiClientContext = Depends(_authenticate_api_key),
) -> List[ApiInvoiceResponse]:
    coll = get_invoices_collection()
    out: List[ApiInvoiceResponse] = []
    for doc in coll.find({"tenant_id": client.tenant_id}, sort=[("_id", -1)], limit=limit):
        gemini_json = (doc.get("gemini") or {}).get("json") or {}
        if not isinstance(gemini_json, dict):
            gemini_json = {}
        out.append(
            ApiInvoiceResponse(
                invoice_id=str(doc.get("_id")),
                tenant_id=client.tenant_id,
                file_status=doc.get("file_status"),
                uploaded_file_path=doc.get("uploaded_file_path"),
                created_at=_iso(doc.get("created_at")),
                gemini_json=gemini_json,
            )
        )
    return out


@app.get("/telemetry/overview", response_model=TelemetryOverviewResponse)
def telemetry_overview(persist_snapshot: bool = Query(default=True)) -> TelemetryOverviewResponse:
    overview = _collect_telemetry_overview_counts()
    return overview


@app.get("/telemetry/pipeline-status", response_model=PipelineStatusResponse)
def telemetry_pipeline_status() -> PipelineStatusResponse:
    from backend.pipeline_status import collect_pipeline_status

    overview = _collect_telemetry_overview_counts()
    status = collect_pipeline_status(overview=overview.model_dump())
    return PipelineStatusResponse(**status)


@app.get("/invoices/search/values", response_model=SearchValuesResponse)
def list_search_values(field: str = Query(...)) -> SearchValuesResponse:
    values = _all_distinct_values(field=field)
    return SearchValuesResponse(values=values)


@app.get("/invoices/search/suggestions", response_model=SearchSuggestionsResponse)
def list_search_suggestions(
    field: str = Query(...),
    q: str = Query(...),
) -> SearchSuggestionsResponse:
    query = q.strip()
    if not query:
        return SearchSuggestionsResponse(suggestions=[])

    values = _all_distinct_values(field=field)
    q_lower = query.lower()

    def _score(value: str) -> float:
        value_lower = value.lower()
        sim = SequenceMatcher(None, q_lower, value_lower).ratio()
        if value_lower.startswith(q_lower):
            sim += 0.35
        elif q_lower in value_lower:
            sim += 0.2
        return sim

    ranked = sorted(
        ((v, _score(v)) for v in values),
        key=lambda item: item[1],
        reverse=True,
    )
    # "At least 50% likely values"
    suggestions = [value for value, score in ranked if score >= 0.5][:20]
    return SearchSuggestionsResponse(suggestions=suggestions)


@app.get("/invoices", response_model=InvoiceListResponse)
def list_invoices(
    limit: int = 50,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    file_status: Optional[str] = None,
) -> InvoiceListResponse:
    coll = get_invoices_collection()

    # Date strings in the DB can be in various human formats, so we apply most
    # filters in Python rather than in Mongo.
    cursor = coll.find({}, sort=[("_id", -1)], limit=limit)

    start_dt = _parse_invoice_date(start_date) if start_date else None
    end_dt = _parse_invoice_date(end_date) if end_date else None

    items: List[InvoiceSummary] = []
    total_amount_sum = 0.0
    seen_invoice_ids_for_sum: set[str] = set()
    for doc in cursor:
        gemini_json = (doc.get("gemini") or {}).get("json") or {}

        invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
        # Use the raw Mongo value for filtering (no defaulting), so missing fields
        # don't match "healthy file" when the UI requests file_status filtering.
        file_status_value_db = doc.get("file_status")
        file_status_value = file_status_value_db or "healthy file"
        payment_status_value = gemini_json.get("payment_status") or "not_paid"
        status_value = None

        # Backfill summary_total_amount if missing, similar to payment_status behavior.
        additional_fields = gemini_json.get("additional_fields")
        if not isinstance(additional_fields, dict):
            additional_fields = {}
            gemini_json["additional_fields"] = additional_fields
        if "summary_total_amount" not in additional_fields:
            summary_total = _calculate_summary_total_amount(gemini_json)
            if summary_total is not None:
                summary_str = f"{summary_total:.2f}"
                additional_fields["summary_total_amount"] = summary_str
                try:
                    coll.update_one(
                        {"_id": doc.get("_id")},
                        {"$set": {"gemini.json.additional_fields.summary_total_amount": summary_str}},
                    )
                except Exception:
                    pass

        # Keep HITL and lifecycle status synced.
        hitl_value, status_value = _sync_hitl_and_status(coll, doc.get("_id"), gemini_json)
        invoice_total_amount = (
            gemini_json.get("total_amount")
            or gemini_json.get("grand_total")
            or gemini_json.get("amount")
        )

        line_items = gemini_json.get("line_items") or []
        invoice_date_raw = gemini_json.get("invoice_date") or gemini_json.get("date")
        seller_value = gemini_json.get("seller")

        def _contains(hay: Optional[Any], needle: Optional[str]) -> bool:
            if not needle:
                return True
            if hay is None:
                return False
            return needle.strip().lower() in str(hay).strip().lower()

        # Apply date filter in Python.
        include = True
        if start_dt or end_dt:
            inv_dt = _parse_invoice_date(invoice_date_raw)
            if inv_dt is None:
                include = False
            else:
                if start_dt and inv_dt < start_dt:
                    include = False
                if end_dt and inv_dt > end_dt:
                    include = False

        if not include:
            continue

        if status and str(status_value).strip().lower() != status.strip().lower():
            continue
        if payment_status and str(payment_status_value).strip().lower() != payment_status.strip().lower():
            continue
        if file_status:
            if file_status_value_db is None:
                continue
            if str(file_status_value_db).strip().lower() != file_status.strip().lower():
                continue

        doc_id = str(doc.get("_id"))
        rows_for_doc: List[InvoiceSummary] = []

        # Create one row per line item (service + hsn).
        if isinstance(line_items, list) and len(line_items) > 0:
            for idx, li in enumerate(line_items):
                if not isinstance(li, dict):
                    continue
                li_service = li.get("service") or li.get("description")
                li_hsn = li.get("hsn_number") or li.get("HSN_number") or li.get("HSN")
                li_quantity = li.get("quantity") or li.get("qty")
                li_price_per_unit = li.get("price_per_unit") or li.get("rate") or li.get("unit_price")
                li_amount = li.get("amount") or li.get("total")
                li_tax_rate = li.get("tax_rate")
                li_tax_amount = li.get("tax_amount")
                li_amount_after_tax = li.get("amount_after_tax")

                rows_for_doc.append(
                    InvoiceSummary(
                        id=doc_id,
                        file_path=doc.get("file_path"),
                        uploaded_file_path=doc.get("uploaded_file_path"),
                        file_status=file_status_value,
                        invoice_number=invoice_number,
                        total_amount=invoice_total_amount,
                        hsn_value=li_hsn,
                        invoice_date=invoice_date_raw,
                        seller=seller_value,
                        service_category=li_service,
                        line_item_index=idx,
                        status=status_value,
                        payment_status=payment_status_value,
                        quantity=li_quantity,
                        price_per_unit=li_price_per_unit,
                        amount=li_amount,
                        tax_rate=li_tax_rate,
                        tax_amount=li_tax_amount,
                        amount_after_tax=li_amount_after_tax,
                        sub_total=(gemini_json.get("additional_fields") or {}).get("sub_total"),
                        sgst_rate=(gemini_json.get("additional_fields") or {}).get("sgst_rate"),
                        sgst_amount=(gemini_json.get("additional_fields") or {}).get("sgst_amount"),
                        cgst_rate=(gemini_json.get("additional_fields") or {}).get("cgst_rate"),
                        cgst_amount=(gemini_json.get("additional_fields") or {}).get("cgst_amount"),
                        summary_total_amount=(gemini_json.get("additional_fields") or {}).get("summary_total_amount"),
                        hitl=hitl_value,
                        deblurred_applied=_to_bool((gemini_json.get("additional_fields") or {}).get("deblurred_applied")),
                        human_approved=_to_bool((gemini_json.get("additional_fields") or {}).get("human_approved")),
                    )
                )
        else:
            # Fallback: single row if no line items.
            service_value = gemini_json.get("service")

            hsn_value = (
                gemini_json.get("hsn_number")
                or gemini_json.get("HSN_number")
                or gemini_json.get("HSN")
            )
            tax_rate_value = gemini_json.get("tax_rate")
            tax_amount_value = gemini_json.get("tax_amount")
            amount_after_tax_value = gemini_json.get("amount_after_tax")
            
            rows_for_doc.append(
                InvoiceSummary(
                    id=doc_id,
                    file_path=doc.get("file_path"),
                    uploaded_file_path=doc.get("uploaded_file_path"),
                    file_status=file_status_value,
                    invoice_number=invoice_number,
                    total_amount=invoice_total_amount,
                    hsn_value=hsn_value,
                    invoice_date=invoice_date_raw,
                    seller=seller_value,
                    service_category=service_value,
                    line_item_index=None,
                    status=status_value,
                    payment_status=payment_status_value,
                    quantity=gemini_json.get("quantity"),
                    price_per_unit=gemini_json.get("price_per_unit"),
                    amount=gemini_json.get("amount"),
                    tax_rate=tax_rate_value,
                    tax_amount=tax_amount_value,
                    amount_after_tax=amount_after_tax_value,
                    sub_total=(gemini_json.get("additional_fields") or {}).get("sub_total"),
                    sgst_rate=(gemini_json.get("additional_fields") or {}).get("sgst_rate"),
                    sgst_amount=(gemini_json.get("additional_fields") or {}).get("sgst_amount"),
                    cgst_rate=(gemini_json.get("additional_fields") or {}).get("cgst_rate"),
                    cgst_amount=(gemini_json.get("additional_fields") or {}).get("cgst_amount"),
                    summary_total_amount=(gemini_json.get("additional_fields") or {}).get("summary_total_amount"),
                    hitl=hitl_value,
                    deblurred_applied=_to_bool((gemini_json.get("additional_fields") or {}).get("deblurred_applied")),
                    human_approved=_to_bool((gemini_json.get("additional_fields") or {}).get("human_approved")),
                )
            )

        if not rows_for_doc:
            continue

        items.extend(rows_for_doc)

        # Sum remains "per invoice" (not multiplied by line items), but must
        # reflect the same filters as the returned rows.
        if doc_id not in seen_invoice_ids_for_sum:
            try:
                if invoice_total_amount is not None:
                    total_amount_sum += float(str(invoice_total_amount).replace(",", ""))
                    seen_invoice_ids_for_sum.add(doc_id)
            except ValueError:
                pass

    return InvoiceListResponse(items=items, total_amount_sum=total_amount_sum)


@app.post("/invoices/{invoice_id}/human-approve", response_model=InvoiceSummary)
def human_approve_invoice(invoice_id: str) -> InvoiceSummary:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        gemini_json = {}

    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}

    # Mark as approved by human; this overrides automated HITL calculation.
    additional_fields["human_approved"] = True
    gemini_json["additional_fields"] = additional_fields

    hitl_value, _ = _sync_hitl_and_status(
        coll,
        oid,
        gemini_json,
        mark_human_processed=True,
        uploaded_file_path=doc.get("uploaded_file_path"),
    )

    try:
        coll.update_one(
            {"_id": oid},
            {
                "$set": {
                    "gemini.json.additional_fields.human_approved": True,
                    "gemini.json.additional_fields.HITL": hitl_value,
                },
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to approve HITL: {e}") from e

    doc = coll.find_one({"_id": oid}) or doc
    return _invoice_summary_row_from_doc(doc, line_item_index=None)


@app.get("/invoices/{invoice_id}/json-editor", response_model=InvoiceJsonEditorResponse)
def get_invoice_json_editor(invoice_id: str) -> InvoiceJsonEditorResponse:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        gemini_json = {}
    line_items = gemini_json.get("line_items") if isinstance(gemini_json.get("line_items"), list) else []
    safe_line_items = [li for li in line_items if isinstance(li, dict)]
    return InvoiceJsonEditorResponse(
        id=str(doc.get("_id")),
        uploaded_file_path=doc.get("uploaded_file_path"),
        gemini_json=gemini_json,
        line_items=safe_line_items,
    )


@app.put("/invoices/{invoice_id}/json-editor", response_model=InvoiceSummary)
def update_invoice_json_editor(invoice_id: str, payload: InvoiceJsonEditorUpdate) -> InvoiceSummary:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    previous_gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(previous_gemini_json, dict):
        previous_gemini_json = {}

    gemini_json = payload.gemini_json if isinstance(payload.gemini_json, dict) else {}
    if payload.line_items is not None:
        gemini_json["line_items"] = payload.line_items

    if not isinstance(gemini_json.get("additional_fields"), dict):
        gemini_json["additional_fields"] = {}

    coll.update_one({"_id": oid}, {"$set": {"gemini.json": gemini_json}})

    # Ensure HITL lifecycle flags are updated after manual JSON edits.
    _sync_hitl_and_status(
        coll,
        oid,
        gemini_json,
        mark_human_processed=True,
        uploaded_file_path=doc.get("uploaded_file_path"),
    )
    doc = coll.find_one({"_id": oid}) or doc

    additional_fields = gemini_json.get("additional_fields") or {}
    comment = ""
    if isinstance(additional_fields, dict):
        comment = str(additional_fields.get("hitl_comment") or "").strip()
    prev_invoice_number = (
        previous_gemini_json.get("invoice_number")
        or previous_gemini_json.get("invoice")
        or ""
    )
    next_invoice_number = (
        gemini_json.get("invoice_number")
        or gemini_json.get("invoice")
        or ""
    )
    changes = _compute_audit_changes(previous_gemini_json, gemini_json)
    user_audit_logger.info(
        "User edited invoice_id=%s | invoice_number_before=%r | invoice_number_after=%r | comment=%r | changed_fields_count=%s | changes=%s",
        invoice_id,
        str(prev_invoice_number),
        str(next_invoice_number),
        comment,
        len(changes),
        changes,
    )
    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found after update.")
    return _invoice_summary_row_from_doc(doc, line_item_index=None)


@app.post("/upload", response_model=InvoiceSummary)
async def upload_invoice(file: UploadFile = File(...)) -> InvoiceSummary:
    configure_logging()
    logger.info(
        "[HTTP API → POST /upload] Upload received (FastAPI). Running the same OCR + Gemini "
        "pipeline as the to_be_processed folder watcher.",
    )
    try:
        from license_validator import InvoiceQuotaExceeded, ensure_invoice_quota_available

        ensure_invoice_quota_available()
    except InvoiceQuotaExceeded as exc:
        logger.warning("[HTTP API → POST /upload] %s", exc.message)
        raise HTTPException(status_code=403, detail=exc.message) from exc

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {sorted(ALLOWED_EXTS)}",
        )

    staging_path = staging_path_for_upload(file.filename or f"file{ext}")
    run_id = str(uuid4())
    file_received_time = datetime.utcnow()
    file_size = 0
    file_type = ext.lstrip(".")

    content = await file.read()
    file_size = len(content)
    staging_path.write_bytes(content)
    logger.info(
        "[HTTP API → POST /upload] Staged in API staging (not watched) for pipeline: %s (%s bytes).",
        staging_path,
        len(content),
    )
    try:
        ocr_result = process_file(str(staging_path))
    except Exception as e:
        if staging_path.is_file():
            try:
                finalize_invoice_file(
                    staging_path,
                    file_status="error",
                    status=0,
                    pipeline_failed=True,
                )
            except Exception:
                pass
        record_pipeline_telemetry(
            {
                "run_id": run_id,
                "source": "api",
                "file_id": None,
                "file_name": staging_path.name,
                "file_size": file_size,
                "file_type": file_type,
                "file_received_time": file_received_time,
                "preprocessing_start_time": None,
                "preprocessing_end_time": None,
                "ocr_start_time": None,
                "ocr_end_time": None,
                "gemini_start_time": None,
                "gemini_end_time": None,
                "db_insert_time": None,
                "preprocessing_latency": None,
                "ocr_latency": None,
                "gemini_latency": None,
                "gemini_prompt_tokens": None,
                "gemini_output_tokens": None,
                "gemini_total_tokens": None,
                "gemini_model": os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash",
                "total_pipeline_latency": (datetime.utcnow() - file_received_time).total_seconds(),
                "status": "error",
                "error_stage": "ocr",
                "error_message": str(e),
            }
        )
        raise HTTPException(status_code=500, detail=f"OCR/Gemini failed: {e}") from e

    gemini_json_norm: dict[str, Any] = dict(ocr_result.gemini_json or {})

    gemini_json = gemini_json_norm
    invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
    invoice_number_norm = str(invoice_number).strip() if invoice_number is not None else ""
    file_status = "error" if not invoice_number_norm else "healthy file"

    _, status_value = pipeline_lifecycle_status(gemini_json, file_status=file_status)
    final_path = finalize_invoice_file(
        staging_path,
        file_status=file_status,
        status=status_value,
        pipeline_failed=False,
    )
    if file_status == "error":
        logger.warning(
            "[HTTP API → POST /upload] No invoice number in extraction; file moved to ERROR: %s",
            final_path,
        )

    logger.info(
        "[HTTP API → POST /upload] Persisting result to MongoDB via store_invoice_result.",
    )
    try:
        inserted_id = store_invoice_result(
            file_path=str(final_path),
            uploaded_file_path=str(final_path),
            ocr_text=ocr_result.ocr_text,
            gemini_model=ocr_result.gemini_model or (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"),
            gemini_json=gemini_json_norm,
            gemini_raw_text=ocr_result.gemini_raw_text,
            file_status=file_status,
        )
    except Exception as e:
        record_pipeline_telemetry(
            {
                "run_id": run_id,
                "source": "api",
                "file_id": None,
                "file_name": final_path.name,
                "file_size": file_size,
                "file_type": file_type,
                "file_received_time": file_received_time,
                "preprocessing_start_time": ocr_result.preprocessing_start_time,
                "preprocessing_end_time": ocr_result.preprocessing_end_time,
                "ocr_start_time": ocr_result.ocr_start_time,
                "ocr_end_time": ocr_result.ocr_end_time,
                "gemini_start_time": ocr_result.gemini_start_time,
                "gemini_end_time": ocr_result.gemini_end_time,
                "db_insert_time": None,
                "preprocessing_latency": ocr_result.preprocessing_latency,
                "ocr_latency": ocr_result.ocr_latency,
                "gemini_latency": ocr_result.gemini_latency,
                "gemini_prompt_tokens": ocr_result.gemini_prompt_tokens,
                "gemini_output_tokens": ocr_result.gemini_output_tokens,
                "gemini_total_tokens": ocr_result.gemini_total_tokens,
                "gemini_model": ocr_result.gemini_model,
                "total_pipeline_latency": (datetime.utcnow() - file_received_time).total_seconds(),
                "status": "error",
                "error_stage": "db",
                "error_message": str(e),
            }
        )
        raise
    # Immediately sync HITL lifecycle fields on new upload to avoid telemetry drift.
    try:
        coll = get_invoices_collection()
        _sync_hitl_and_status(
            coll,
            ObjectId(inserted_id),
            gemini_json_norm,
            uploaded_file_path=str(final_path),
        )
    except Exception as e:
        logger.warning("Failed to sync HITL/status immediately after upload insert: %s", e)
    from license_validator import increment_invoice_count

    increment_invoice_count()
    logger.info(
        "[HTTP API → POST /upload] Completed. New invoice id=%s | file_status=%s",
        inserted_id,
        file_status,
    )
    db_insert_time = datetime.utcnow()
    record_pipeline_telemetry(
        {
            "run_id": run_id,
            "source": "api",
            "file_id": inserted_id,
            "file_name": final_path.name,
            "file_size": file_size,
            "file_type": file_type,
            "file_received_time": file_received_time,
            "preprocessing_start_time": ocr_result.preprocessing_start_time,
            "preprocessing_end_time": ocr_result.preprocessing_end_time,
            "ocr_start_time": ocr_result.ocr_start_time,
            "ocr_end_time": ocr_result.ocr_end_time,
            "gemini_start_time": ocr_result.gemini_start_time,
            "gemini_end_time": ocr_result.gemini_end_time,
            "db_insert_time": db_insert_time,
            "preprocessing_latency": ocr_result.preprocessing_latency,
            "ocr_latency": ocr_result.ocr_latency,
            "gemini_latency": ocr_result.gemini_latency,
            "gemini_prompt_tokens": ocr_result.gemini_prompt_tokens,
            "gemini_output_tokens": ocr_result.gemini_output_tokens,
            "gemini_total_tokens": ocr_result.gemini_total_tokens,
            "gemini_model": ocr_result.gemini_model,
            "total_pipeline_latency": (db_insert_time - file_received_time).total_seconds(),
            "status": "success",
            "error_stage": None,
            "error_message": None,
        }
    )

    status_value = status_value if file_status != "error" else None
    payment_status_value = gemini_json.get("payment_status") or "not_paid"
    total_amount = (
        gemini_json.get("total_amount")
        or gemini_json.get("grand_total")
        or gemini_json.get("amount")
    )
    # Align HSN/service in the immediate upload response as well.
    line_items = gemini_json.get("line_items") or []
    first_item: dict[str, Any] = line_items[0] if line_items else {}
    hsn_value = (
        first_item.get("hsn_number")
        or first_item.get("HSN_number")
        or gemini_json.get("hsn_number")
        or gemini_json.get("HSN_number")
        or gemini_json.get("HSN")
    )
    service_value = (
        gemini_json.get("service")
        or first_item.get("service")
    )
    tax_rate_value = (
        first_item.get("tax_rate")
        or gemini_json.get("tax_rate")
    )
    tax_amount_value = (
        first_item.get("tax_amount")
        or gemini_json.get("tax_amount")
    )
    amount_after_tax_value = (
        first_item.get("amount_after_tax")
        or gemini_json.get("amount_after_tax")
    )

    return InvoiceSummary(
        id=inserted_id,
        file_path=str(final_path),
        uploaded_file_path=str(final_path),
        file_status=file_status,
        invoice_number=invoice_number,
        total_amount=total_amount,
        hsn_value=hsn_value,
        service_category=service_value,
        status=status_value,
        payment_status=payment_status_value,
        tax_rate=tax_rate_value,
        tax_amount=tax_amount_value,
        amount_after_tax=amount_after_tax_value,
    )


@app.patch("/invoices/{invoice_id}", response_model=InvoiceSummary)
def update_invoice(invoice_id: str, payload: InvoiceUpdate) -> InvoiceSummary:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")

    update_doc: dict[str, Any] = {}
    line_item_index = payload.line_item_index

    if payload.invoice_number is not None:
        update_doc["gemini.json.invoice_number"] = payload.invoice_number
    if payload.total_amount is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.amount"] = payload.total_amount
        else:
            update_doc["gemini.json.total_amount"] = payload.total_amount
    if payload.quantity is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.quantity"] = payload.quantity
        else:
            update_doc["gemini.json.quantity"] = payload.quantity
    if payload.price_per_unit is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.price_per_unit"] = payload.price_per_unit
        else:
            update_doc["gemini.json.price_per_unit"] = payload.price_per_unit
    if payload.amount is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.amount"] = payload.amount
        else:
            update_doc["gemini.json.amount"] = payload.amount
    if payload.hsn_value is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.hsn_number"] = payload.hsn_value
        else:
            # We store it under a consistent key; reading logic already handles variants.
            update_doc["gemini.json.hsn_number"] = payload.hsn_value
    if payload.invoice_date is not None:
        update_doc["gemini.json.invoice_date"] = payload.invoice_date
    if payload.seller is not None:
        update_doc["gemini.json.seller"] = payload.seller
    if payload.service_category is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.service"] = payload.service_category
        else:
            update_doc["gemini.json.service"] = payload.service_category
    if payload.tax_rate is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.tax_rate"] = payload.tax_rate
        else:
            update_doc["gemini.json.tax_rate"] = payload.tax_rate
    if payload.tax_amount is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.tax_amount"] = payload.tax_amount
        else:
            update_doc["gemini.json.tax_amount"] = payload.tax_amount
    if payload.amount_after_tax is not None:
        if line_item_index is not None:
            update_doc[f"gemini.json.line_items.{line_item_index}.amount_after_tax"] = payload.amount_after_tax
        else:
            update_doc["gemini.json.amount_after_tax"] = payload.amount_after_tax
    if payload.sub_total is not None:
        update_doc["gemini.json.additional_fields.sub_total"] = payload.sub_total
    if payload.sgst_rate is not None:
        update_doc["gemini.json.additional_fields.sgst_rate"] = payload.sgst_rate
    if payload.sgst_amount is not None:
        update_doc["gemini.json.additional_fields.sgst_amount"] = payload.sgst_amount
    if payload.cgst_rate is not None:
        update_doc["gemini.json.additional_fields.cgst_rate"] = payload.cgst_rate
    if payload.cgst_amount is not None:
        update_doc["gemini.json.additional_fields.cgst_amount"] = payload.cgst_amount
    if not update_doc:
        # status/payment_status are intentionally non-persistent now.
        doc = coll.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        return _invoice_summary_row_from_doc(doc, line_item_index=line_item_index)

    result = coll.update_one({"_id": oid}, {"$set": update_doc})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found after update.")

    # Keep billing summary figures synced in DB.
    if any(
        v is not None
        for v in [
            payload.amount,
            payload.tax_amount,
            payload.sub_total,
            payload.sgst_amount,
            payload.cgst_amount,
            payload.sgst_rate,
            payload.cgst_rate,
            payload.amount_after_tax,
        ]
    ):
        gemini_json = (doc.get("gemini") or {}).get("json") or {}
        line_items = gemini_json.get("line_items") or []
        subtotal = _sum_line_item_amounts(line_items)
        summary_total = _calculate_summary_total_amount(gemini_json)
        update_summary_doc: dict[str, Any] = {}
        if subtotal is not None:
            update_summary_doc["gemini.json.additional_fields.sub_total"] = f"{subtotal:.2f}"
        if summary_total is not None:
            summary_str = f"{summary_total:.2f}"
            update_summary_doc["gemini.json.additional_fields.summary_total_amount"] = summary_str
        if update_summary_doc:
            coll.update_one({"_id": oid}, {"$set": update_summary_doc})
            doc = coll.find_one({"_id": oid}) or doc

    # Sync HITL/status after any edits.
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if isinstance(gemini_json, dict):
        _sync_hitl_and_status(
            coll,
            oid,
            gemini_json,
            mark_human_processed=True,
            uploaded_file_path=doc.get("uploaded_file_path"),
        )
        doc = coll.find_one({"_id": oid}) or doc

    return _invoice_summary_row_from_doc(doc, line_item_index=line_item_index)


@app.post("/invoices/{invoice_id}/line-items", response_model=InvoiceSummary)
def add_invoice_line_item(invoice_id: str, payload: LineItemCreate) -> InvoiceSummary:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    item: dict[str, Any] = {
        "hsn_number": payload.hsn_number or "",
        "service": payload.service or "",
        "quantity": payload.quantity or "",
        "price_per_unit": payload.price_per_unit or "",
        "amount": payload.amount or "",
        "tax_rate": payload.tax_rate or "",
        "tax_amount": payload.tax_amount or "",
        "amount_after_tax": payload.amount_after_tax or "",
    }

    result = coll.update_one({"_id": oid}, {"$push": {"gemini.json.line_items": item}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found after update.")

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    line_items = gemini_json.get("line_items") or []
    subtotal = _sum_line_item_amounts(line_items)
    summary_total = _calculate_summary_total_amount(gemini_json)
    update_doc_summary: dict[str, Any] = {}
    if subtotal is not None:
        update_doc_summary["gemini.json.additional_fields.sub_total"] = f"{subtotal:.2f}"
    if summary_total is not None:
        summary_str = f"{summary_total:.2f}"
        update_doc_summary["gemini.json.additional_fields.summary_total_amount"] = summary_str
    if update_doc_summary:
        coll.update_one({"_id": oid}, {"$set": update_doc_summary})
        doc = coll.find_one({"_id": oid}) or doc

    # Sync HITL/status after any line-item add.
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if isinstance(gemini_json, dict):
        _sync_hitl_and_status(
            coll,
            oid,
            gemini_json,
            mark_human_processed=True,
            uploaded_file_path=doc.get("uploaded_file_path"),
        )
        doc = coll.find_one({"_id": oid}) or doc

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    line_items = gemini_json.get("line_items") or []
    new_index = len(line_items) - 1 if isinstance(line_items, list) and len(line_items) > 0 else None
    return _invoice_summary_row_from_doc(doc, line_item_index=new_index)


@app.delete("/invoices/{invoice_id}/line-items/{line_item_index}", response_model=InvoiceListResponse)
def delete_invoice_line_item(invoice_id: str, line_item_index: int) -> InvoiceListResponse:
    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice id.")

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    line_items = gemini_json.get("line_items") or []
    if not isinstance(line_items, list):
        raise HTTPException(status_code=400, detail="Invoice has no line_items array.")
    if line_item_index < 0 or line_item_index >= len(line_items):
        raise HTTPException(status_code=400, detail="Invalid line_item_index.")

    # Remove by rewriting the array (Mongo doesn't support positional delete by index without pipeline).
    new_items = [li for idx, li in enumerate(line_items) if idx != line_item_index]
    coll.update_one({"_id": oid}, {"$set": {"gemini.json.line_items": new_items}})

    # Refresh document after mutation so recalculations use the updated line_items.
    doc = coll.find_one({"_id": oid}) or doc

    # Recalculate summary totals after deletion.
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    line_items = gemini_json.get("line_items") or []
    subtotal = _sum_line_item_amounts(line_items)
    summary_total = _calculate_summary_total_amount(gemini_json)
    update_doc_summary: dict[str, Any] = {}
    if subtotal is not None:
        update_doc_summary["gemini.json.additional_fields.sub_total"] = f"{subtotal:.2f}"
    if summary_total is not None:
        summary_str = f"{summary_total:.2f}"
        update_doc_summary["gemini.json.additional_fields.summary_total_amount"] = summary_str
    if update_doc_summary:
        coll.update_one({"_id": oid}, {"$set": update_doc_summary})

    # Return refreshed rows for this invoice (as InvoiceListResponse-like shape).
    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found after update.")

    # Sync HITL/status after any line-item deletion.
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if isinstance(gemini_json, dict):
        _sync_hitl_and_status(
            coll,
            oid,
            gemini_json,
            mark_human_processed=True,
            uploaded_file_path=doc.get("uploaded_file_path"),
        )
        doc = coll.find_one({"_id": oid}) or doc

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    invoice_total_amount = (
        gemini_json.get("total_amount")
        or gemini_json.get("grand_total")
        or gemini_json.get("amount")
    )
    additional_fields = gemini_json.get("additional_fields") or {}
    status_value_raw = additional_fields.get("status")
    try:
        status_value = int(status_value_raw) if status_value_raw is not None else None
    except Exception:
        status_value = None
    payment_status_value = gemini_json.get("payment_status") or "not_paid"
    file_status_value = doc.get("file_status") or "healthy file"
    invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
    invoice_date_raw = gemini_json.get("invoice_date") or gemini_json.get("date")
    seller_value = gemini_json.get("seller")
    line_items = gemini_json.get("line_items") or []
    items: List[InvoiceSummary] = []
    hitl_value = None
    try:
        additional_fields = gemini_json.get("additional_fields") or {}
        hitl_raw = additional_fields.get("HITL", additional_fields.get("HIT"))
        hitl_value = _to_bool(hitl_raw)
    except Exception:
        hitl_value = None

    if isinstance(line_items, list) and len(line_items) > 0:
        for idx, li in enumerate(line_items):
            if not isinstance(li, dict):
                continue
            items.append(
                InvoiceSummary(
                    id=str(doc.get("_id")),
                    file_path=doc.get("file_path"),
                    uploaded_file_path=doc.get("uploaded_file_path"),
                    file_status=file_status_value,
                    invoice_number=invoice_number,
                    total_amount=invoice_total_amount,
                    hsn_value=li.get("hsn_number") or li.get("HSN_number") or li.get("HSN"),
                    invoice_date=invoice_date_raw,
                    seller=seller_value,
                    service_category=li.get("service") or li.get("description"),
                    line_item_index=idx,
                    status=status_value,
                    payment_status=payment_status_value,
                    quantity=li.get("quantity") or li.get("qty"),
                    price_per_unit=li.get("price_per_unit") or li.get("rate") or li.get("unit_price"),
                    amount=li.get("amount") or li.get("total"),
                    tax_rate=li.get("tax_rate"),
                    tax_amount=li.get("tax_amount"),
                    amount_after_tax=li.get("amount_after_tax"),
                    sub_total=additional_fields.get("sub_total"),
                    sgst_rate=additional_fields.get("sgst_rate"),
                    sgst_amount=additional_fields.get("sgst_amount"),
                    cgst_rate=additional_fields.get("cgst_rate"),
                    cgst_amount=additional_fields.get("cgst_amount"),
                    summary_total_amount=additional_fields.get("summary_total_amount"),
                    hitl=hitl_value,
                )
            )
    else:
        items.append(_invoice_summary_row_from_doc(doc, line_item_index=None))

    return InvoiceListResponse(items=items, total_amount_sum=0.0)


@app.post("/invoices/delete", response_model=DeleteInvoicesResponse)
def delete_invoices(payload: DeleteInvoicesRequest) -> DeleteInvoicesResponse:
    coll = get_invoices_collection()

    oids: list[ObjectId] = []
    for raw_id in payload.ids:
        try:
            oids.append(ObjectId(raw_id))
        except Exception:
            # Skip invalid ids rather than failing the whole request.
            pass

    if not oids:
        raise HTTPException(status_code=400, detail="No valid invoice ids provided.")

    result = coll.delete_many({"_id": {"$in": oids}})
    return DeleteInvoicesResponse(
        requested_count=len(payload.ids),
        deleted_count=int(result.deleted_count),
    )


@app.get("/chat/suggestions")
def chat_suggestions() -> dict[str, list[str]]:
    return {"suggestions": get_suggestions()}


@app.get("/chat/session/{session_id}")
def chat_session(session_id: str) -> dict[str, Any]:
    session = load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return session


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    logger.info(
        "[HTTP API → POST /chat] Chat question received; Qdrant semantic search + structured "
        "intents, optional Gemini (backend.agents.chat_engine.chat).",
    )
    try:
        result = run_chat(req.question, session_id=req.session_id)
        logger.info("[HTTP API → POST /chat] Answer generated and returned to client.")
        messages = [
            ChatMessage(
                role=str(m.get("role", "")),
                content=str(m.get("content", "")),
                timestamp=m.get("timestamp"),
            )
            for m in (result.get("messages") or [])
            if isinstance(m, dict)
        ]
        return ChatResponse(
            answer=str(result.get("answer") or ""),
            session_id=str(result.get("session_id") or ""),
            messages=messages,
            suggestions=list(result.get("suggestions") or []),
            mode=result.get("mode"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}") from e


def _mount_frontend() -> None:
    dist = frontend_dist_dir()
    if not dist.is_dir():
        logger.warning("Frontend dist not found at %s — running API-only.", dist)
        return

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend_assets")

    @app.get("/")
    async def spa_index() -> FileResponse:
        return FileResponse(dist / "index.html")

    @app.get("/{spa_path:path}")
    async def spa_fallback(spa_path: str) -> FileResponse:
        if spa_path.startswith(("api/", "raw/")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = dist / spa_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    logger.info("Serving frontend from %s", dist)


_mount_frontend()

