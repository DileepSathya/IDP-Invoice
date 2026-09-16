"""
Tally Bridge API — wraps TALLY INTEGRATION voucher pipeline.

Run from this directory:
    pip install -r requirements.txt
    uvicorn api_server:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from backend.tally_company_settings import current_tally_company

load_dotenv()

from tally.expense_vendors import fetch_expense_ledgers
from tally.master_data import fetch_all_masters, fetch_items, fetch_vendors
from tally.pipeline import push_invoice_by_id
from tally.purchase_orders import fetch_purchase_orders
from tally.tally_details import get_purchase_ledgers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Tally Bridge", version="1.0.0")

TALLY_URL = os.environ.get("TALLY_URL", "http://localhost:9000").strip()


class PushResponse(BaseModel):
    success: bool
    invoice_id: str
    invoice_number: str = ""
    message: str = ""
    error_reason: Optional[str] = None
    tally_company: Optional[str] = None
    pushed_at: Optional[str] = None
    skipped: bool = False


class HealthResponse(BaseModel):
    bridge_ok: bool = True
    tally_reachable: bool = False
    tally_url: str = TALLY_URL
    tally_error: Optional[str] = None


class PurchaseLedgersResponse(BaseModel):
    ledgers: list[str]
    company: Optional[str] = None
    error: Optional[str] = None


class TallyMastersResponse(BaseModel):
    success: bool
    company: Optional[str] = None
    vendors: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    expense_ledgers: list[dict[str, Any]] = []
    po_headers: list[dict[str, Any]] = []
    po_details: list[dict[str, Any]] = []
    errors: list[str] = []


def _ping_tally() -> tuple[bool, Optional[str]]:
    xml = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Ledgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        resp = requests.post(
            TALLY_URL,
            data=xml.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=10,
        )
        resp.raise_for_status()
        return True, None
    except Exception as exc:
        return False, str(exc)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    reachable, err = _ping_tally()
    return HealthResponse(bridge_ok=True, tally_reachable=reachable, tally_error=err)


@app.get("/ledgers/purchase", response_model=PurchaseLedgersResponse)
def list_purchase_ledgers() -> PurchaseLedgersResponse:
    company = current_tally_company() or None
    ledgers, error = get_purchase_ledgers(TALLY_URL, company_name=company)
    if not ledgers and error:
        logger.warning("[tally_bridge] purchase ledgers fetch failed: %s", error)
    return PurchaseLedgersResponse(ledgers=ledgers, company=company, error=error)


@app.get("/masters/all", response_model=TallyMastersResponse)
def get_all_masters() -> TallyMastersResponse:
    company = current_tally_company()
    if not company:
        return TallyMastersResponse(
            success=False,
            company=None,
            errors=["TALLY_COMPANY is not set in the bridge .env"],
        )
    result = fetch_all_masters(TALLY_URL, company_name=company)
    return TallyMastersResponse(**result)


@app.get("/masters/vendors", response_model=TallyMastersResponse)
def get_vendor_masters() -> TallyMastersResponse:
    company = current_tally_company()
    vendors, error = fetch_vendors(TALLY_URL, company_name=company or None)
    errors = [error] if error else []
    return TallyMastersResponse(
        success=not errors,
        company=company or None,
        vendors=vendors,
        errors=errors,
    )


@app.get("/masters/items", response_model=TallyMastersResponse)
def get_item_masters() -> TallyMastersResponse:
    company = current_tally_company()
    items, error = fetch_items(TALLY_URL, company_name=company or None)
    errors = [error] if error else []
    return TallyMastersResponse(
        success=not errors,
        company=company or None,
        items=items,
        errors=errors,
    )


@app.get("/masters/expense-ledgers", response_model=TallyMastersResponse)
def get_expense_ledger_masters() -> TallyMastersResponse:
    company = current_tally_company()
    expense_ledgers, error = fetch_expense_ledgers(TALLY_URL, company_name=company or None)
    errors = [error] if error else []
    return TallyMastersResponse(
        success=not errors,
        company=company or None,
        expense_ledgers=expense_ledgers,
        errors=errors,
    )


@app.get("/masters/purchase-orders", response_model=TallyMastersResponse)
def get_purchase_order_masters() -> TallyMastersResponse:
    company = current_tally_company()
    if not company:
        return TallyMastersResponse(
            success=False,
            company=None,
            errors=["TALLY_COMPANY is not set in the bridge .env"],
        )
    po_headers, po_details, error = fetch_purchase_orders(TALLY_URL, company_name=company)
    errors = [error] if error else []
    return TallyMastersResponse(
        success=not errors,
        company=company or None,
        po_headers=po_headers,
        po_details=po_details,
        errors=errors,
    )


@app.post("/push/invoice/{invoice_id}", response_model=PushResponse)
def push_invoice(
    invoice_id: str,
    force: bool = Query(default=False),
) -> PushResponse:
    # force is accepted for API compatibility; voucher pipeline always pushes when called.
    _ = force
    result: dict[str, Any] = push_invoice_by_id(invoice_id)
    if not result.get("success") and result.get("message") == "Invoice not found":
        raise HTTPException(status_code=404, detail=result.get("error_reason") or "Invoice not found")
    return PushResponse(**result)
