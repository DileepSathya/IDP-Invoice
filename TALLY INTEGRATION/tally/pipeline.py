"""Voucher-only Tally push pipeline (no validation, no Tally master export)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from tally import create_voucher, invoice_data_retriver

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
_template_env = os.environ.get("TALLY_VOUCHER_TEMPLATE", "").strip()
VOUCHER_TEMPLATE = Path(_template_env) if _template_env else BASE_DIR / "xml_scripts" / "create_voucher.xml"

TALLY_URL = os.environ.get("TALLY_URL", "http://localhost:9000").strip()
TALLY_COMPANY = os.environ.get("TALLY_COMPANY", "").strip()
VOUCHER_TYPE = os.environ.get("TALLY_VOUCHER_TYPE", "Purchase").strip() or "Purchase"
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "IDP")
MONGO_COLLECTION = os.environ.get("MONGO_INVOICES_COLLECTION", "invoices")


def _load_purchase_ledger_setting() -> str:
    default = os.environ.get("TALLY_PURCHASE_LEDGER", "Purchase A/c").strip() or "Purchase A/c"
    try:
        from pymongo import MongoClient

        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        doc = client[MONGO_DB]["erp_settings"].find_one({"_id": "tally_settings"}) or {}
        value = str(doc.get("purchase_ledger") or "").strip()
        if value:
            return value
    except Exception as exc:
        logger.debug("[tally_pipeline] Could not load purchase ledger from MongoDB: %s", exc)
    return default


def _inject_purchase_ledger(doc: dict[str, Any]) -> dict[str, Any]:
    """Ensure gemini.json.purchase_ledger is set from app settings before voucher build."""
    doc = dict(doc)
    gemini = dict(doc.get("gemini") or {})
    json_data = dict(gemini.get("json") or {})
    if not str(json_data.get("purchase_ledger") or "").strip():
        json_data["purchase_ledger"] = _load_purchase_ledger_setting()
    gemini["json"] = json_data
    doc["gemini"] = gemini
    return doc


def _vendor_name(doc: dict[str, Any]) -> str:
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    additional = gemini_json.get("additional_fields") or {}
    if not isinstance(additional, dict):
        additional = {}
    return str(
        additional.get("erp_vendor_name")
        or gemini_json.get("seller")
        or ""
    ).strip()


def _invoice_number(doc: dict[str, Any]) -> str:
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    return str(gemini_json.get("invoice_number") or gemini_json.get("invoice") or "").strip()


def push_invoice_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Fetch is already done — only create and POST the voucher."""
    invoice_id = str(doc.get("_id") or "")
    invoice_number = _invoice_number(doc)
    vendor_name = _vendor_name(doc)
    pushed_at = datetime.now(timezone.utc).isoformat()

    if not TALLY_COMPANY:
        return {
            "success": False,
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "message": "TALLY_COMPANY is not set",
            "error_reason": "Set TALLY_COMPANY in the bridge service .env",
            "tally_company": None,
            "pushed_at": pushed_at,
            "skipped": False,
        }

    if not vendor_name:
        return {
            "success": False,
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "message": "Missing vendor name",
            "error_reason": "erp_vendor_name or seller is empty on the invoice",
            "tally_company": TALLY_COMPANY,
            "pushed_at": pushed_at,
            "skipped": False,
        }

    if not VOUCHER_TEMPLATE.is_file():
        return {
            "success": False,
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "message": "Voucher template missing",
            "error_reason": f"Template not found: {VOUCHER_TEMPLATE}",
            "tally_company": TALLY_COMPANY,
            "pushed_at": pushed_at,
            "skipped": False,
        }

    logger.info("[tally_pipeline] Creating voucher for invoice '%s' (_id=%s)", invoice_number, invoice_id)

    doc = _inject_purchase_ledger(doc)

    result = create_voucher.send_template_to_tally(
        TALLY_URL=TALLY_URL,
        path=str(VOUCHER_TEMPLATE),
        company_name=TALLY_COMPANY,
        data=doc,
        invoice_number=invoice_number,
        voucher_type=VOUCHER_TYPE,
        vendor_name=vendor_name,
    )

    return {
        "success": bool(result.get("success")),
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "message": result.get("message") or "",
        "error_reason": result.get("error_reason"),
        "tally_company": TALLY_COMPANY,
        "pushed_at": pushed_at,
        "skipped": False,
    }


def push_invoice_by_id(invoice_id: str) -> dict[str, Any]:
    doc = invoice_data_retriver.fetch_by_id(MONGO_URI, MONGO_DB, MONGO_COLLECTION, invoice_id)
    if not doc:
        return {
            "success": False,
            "invoice_id": invoice_id,
            "invoice_number": "",
            "message": "Invoice not found",
            "error_reason": f"No MongoDB document for _id={invoice_id}",
            "tally_company": TALLY_COMPANY or None,
            "pushed_at": datetime.now(timezone.utc).isoformat(),
            "skipped": False,
        }
    return push_invoice_document(doc)
