from __future__ import annotations

import logging
import os
import shutil
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, List, Optional

from bson import ObjectId
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.agents.rag_chatbot import answer_question
from backend.agents.database import (
    ensure_telemetry_collections,
    get_business_snapshot_telemetry_collection,
    get_hybrid_telemetry_collection,
    get_invoices_collection,
    log_application_event,
    store_invoice_result,
)
from backend.agents.ocr import ALLOWED_EXTS, process_file
from backend.app_logging import configure_logging

from datetime import datetime, date


logger = logging.getLogger(__name__)
user_audit_logger = logging.getLogger("user_audit")

_USER_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "user.log"
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
        ever_hitl_true = _to_bool(additional_fields.get("ever_hitl_true"))

        if hitl:
            hitl_flagged_files += 1
            hitl_process_pending += 1
        elif ever_hitl_true:
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


def _persist_telemetry_overview_snapshot(overview: TelemetryOverviewResponse) -> None:
    now = datetime.utcnow()
    snapshot_date = now.strftime("%Y-%m-%d")
    payload = overview.model_dump()
    payload["generated_at"] = now
    payload["snapshot_date"] = snapshot_date
    payload["period"] = "daily"

    business_coll = get_business_snapshot_telemetry_collection()
    business_coll.update_one(
        {"snapshot_date": snapshot_date, "period": "daily"},
        {"$set": payload},
        upsert=True,
    )

    hybrid_coll = get_hybrid_telemetry_collection()
    hybrid_coll.insert_one(
        {
            "ts": now,
            "category": "business_overview",
            "source": "api",
            "metrics": overview.model_dump(),
        }
    )


load_dotenv()
configure_logging()

RAW_DIR = Path(os.environ.get("RAW_DIR", "./raw")).expanduser().resolve()
RAW_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "./invoices_data/uploads")).expanduser().resolve()
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

ERROR_FILES_DIR = Path(os.environ.get("ERROR_FILES_DIR", "./invoices_data/error_files")).expanduser().resolve()
ERROR_FILES_DIR.mkdir(parents=True, exist_ok=True)


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


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str


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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "hit", "hitl"}


def _calculate_hitl_flag(gemini_json: dict[str, Any]) -> bool:
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}

    # Human approval can override automated HITL calculation.
    human_approved = _to_bool(additional_fields.get("human_approved"))
    if human_approved:
        return False

    deblurred_applied = _to_bool(additional_fields.get("deblurred_applied"))

    main_total = _to_float(
        gemini_json.get("total_amount")
        or gemini_json.get("grand_total")
        or gemini_json.get("amount")
    )
    summary_total_amount = _to_float(additional_fields.get("summary_total_amount"))

    mismatch = False
    if main_total is None and summary_total_amount is None:
        mismatch = False
    elif main_total is None or summary_total_amount is None:
        # If one exists and the other is missing/unparseable, treat it as mismatch.
        mismatch = True
    else:
        mismatch = abs(main_total - summary_total_amount) > 0.01

    return mismatch or deblurred_applied


def _calculate_status_from_hitl(*, hitl_value: bool, ever_hitl_true: bool) -> int:
    # 0: System processed, 1: HITL process pending, 2: HITL processed
    if hitl_value:
        return 1
    if ever_hitl_true:
        return 2
    return 0


def _sync_hitl_and_status(coll: Any, oid: ObjectId, gemini_json: dict[str, Any]) -> tuple[bool, int]:
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    hitl_value = _calculate_hitl_flag(gemini_json)
    existing_hitl = additional_fields.get("HITL", additional_fields.get("HIT"))
    existing_hitl_bool = _to_bool(existing_hitl)

    existing_ever_hitl_true = _to_bool(additional_fields.get("ever_hitl_true"))
    ever_hitl_true = bool(existing_ever_hitl_true or hitl_value)
    status_value = _calculate_status_from_hitl(hitl_value=hitl_value, ever_hitl_true=ever_hitl_true)

    existing_status_raw = additional_fields.get("status")
    try:
        existing_status = int(existing_status_raw) if existing_status_raw is not None else None
    except Exception:
        existing_status = None

    update_doc: dict[str, Any] = {}
    if existing_hitl is None or existing_hitl_bool != hitl_value:
        update_doc["gemini.json.additional_fields.HITL"] = hitl_value
    if existing_ever_hitl_true != ever_hitl_true:
        update_doc["gemini.json.additional_fields.ever_hitl_true"] = ever_hitl_true
    if existing_status != status_value:
        update_doc["gemini.json.additional_fields.status"] = status_value

    if update_doc:
        try:
            coll.update_one({"_id": oid}, {"$set": update_doc})
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
    try:
        ensure_telemetry_collections()
    except Exception as e:
        logger.warning("Failed to ensure telemetry collections at startup: %s", e)
    yield


app = FastAPI(title="IDP Invoices API", lifespan=_app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded files so the frontend can render previews.
app.mount("/raw", StaticFiles(directory=UPLOADS_DIR), name="raw")


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


@app.get("/telemetry/overview", response_model=TelemetryOverviewResponse)
def telemetry_overview(persist_snapshot: bool = Query(default=True)) -> TelemetryOverviewResponse:
    overview = _collect_telemetry_overview_counts()
    if persist_snapshot:
        try:
            _persist_telemetry_overview_snapshot(overview)
        except Exception as e:
            logger.warning("Failed to persist telemetry overview snapshot: %s", e)
    return overview


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

    hitl_value, _ = _sync_hitl_and_status(coll, oid, gemini_json)

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

    try:
        _persist_telemetry_overview_snapshot(_collect_telemetry_overview_counts())
    except Exception as e:
        logger.warning("Failed to persist telemetry snapshot after human approval: %s", e)

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
    _sync_hitl_and_status(coll, oid, gemini_json)

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
    try:
        _persist_telemetry_overview_snapshot(_collect_telemetry_overview_counts())
    except Exception as e:
        logger.warning("Failed to persist telemetry snapshot after invoice JSON editor update: %s", e)

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found after update.")
    return _invoice_summary_row_from_doc(doc, line_item_index=None)


@app.post("/upload", response_model=InvoiceSummary)
async def upload_invoice(file: UploadFile = File(...)) -> InvoiceSummary:
    configure_logging()
    logger.info(
        "[HTTP API → POST /upload] Upload received (FastAPI). Running the same OCR + Gemini "
        "pipeline as the raw-folder watcher.",
    )
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {sorted(ALLOWED_EXTS)}",
        )

    # Create timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Generate filename: original_name + timestamp + extension
    filename_without_ext = Path(file.filename or "file").stem
    timestamped_filename = f"{filename_without_ext}_{timestamp}{ext}"
    
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = UPLOADS_DIR / timestamped_filename

    content = await file.read()
    target_path.write_bytes(content)
    logger.info(
        "[HTTP API → POST /upload] Saved uploaded bytes to disk: %s (%s bytes).",
        target_path,
        len(content),
    )
    log_application_event(
        event_type="invoice_upload_received",
        payload={
            "source": "api",
            "filename": file.filename or "",
            "uploaded_file_path": str(target_path),
            "uploaded_bytes": len(content),
            "success": True,
        },
    )

    try:
        ocr_result = process_file(str(target_path))
    except Exception as e:
        log_application_event(
            event_type="invoice_upload_failed",
            payload={
                "source": "api",
                "filename": file.filename or "",
                "uploaded_file_path": str(target_path),
                "success": False,
                "error_message": f"OCR/Gemini failed: {e}",
            },
        )
        raise HTTPException(status_code=500, detail=f"OCR/Gemini failed: {e}") from e

    gemini_json_norm: dict[str, Any] = dict(ocr_result.gemini_json or {})

    gemini_json = gemini_json_norm
    invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
    invoice_number_norm = str(invoice_number).strip() if invoice_number is not None else ""
    file_status = "error" if not invoice_number_norm else "healthy file"

    # If Gemini couldn't extract an invoice number, keep a copy in error_files/.
    if file_status == "error":
        ERROR_FILES_DIR.mkdir(parents=True, exist_ok=True)
        error_path = ERROR_FILES_DIR / target_path.name
        logger.warning(
            "[HTTP API → POST /upload] No invoice number in extraction; copying file to error_files: %s",
            error_path,
        )
        shutil.copy2(str(target_path), str(error_path))

    logger.info(
        "[HTTP API → POST /upload] Persisting result to MongoDB via store_invoice_result.",
    )
    inserted_id = store_invoice_result(
        file_path=ocr_result.file_path,
        uploaded_file_path=str(target_path),
        ocr_text=ocr_result.ocr_text,
        gemini_model=os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash",
        gemini_json=gemini_json_norm,
        gemini_raw_text=ocr_result.gemini_raw_text,
        file_status=file_status,
    )
    # Immediately sync HITL lifecycle fields on new upload to avoid telemetry drift.
    try:
        coll = get_invoices_collection()
        _sync_hitl_and_status(coll, ObjectId(inserted_id), gemini_json_norm)
    except Exception as e:
        logger.warning("Failed to sync HITL/status immediately after upload insert: %s", e)
    log_application_event(
        event_type="invoice_upload_completed",
        payload={
            "source": "api",
            "invoice_id": inserted_id,
            "invoice_number": str(invoice_number or ""),
            "file_status": file_status,
            "uploaded_file_path": str(target_path),
            "success": True,
        },
    )
    try:
        _persist_telemetry_overview_snapshot(_collect_telemetry_overview_counts())
    except Exception as e:
        logger.warning("Failed to persist telemetry snapshot after upload: %s", e)

    logger.info(
        "[HTTP API → POST /upload] Completed. New invoice id=%s | file_status=%s",
        inserted_id,
        file_status,
    )

    status_value = None
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
        file_path=ocr_result.file_path,
        uploaded_file_path=str(target_path),
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
        _sync_hitl_and_status(coll, oid, gemini_json)

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
        _sync_hitl_and_status(coll, oid, gemini_json)

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
        _sync_hitl_and_status(coll, oid, gemini_json)

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


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    logger.info(
        "[HTTP API → POST /chat] Chat question received; RAG will search MongoDB invoices then "
        "call Gemini (backend.agents.rag_chatbot.answer_question).",
    )
    try:
        resp = answer_question(req.question)
        logger.info("[HTTP API → POST /chat] Answer generated and returned to client.")
        return ChatResponse(answer=resp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}") from e


