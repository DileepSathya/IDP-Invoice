"""Fetch Purchase Order vouchers from Tally Prime."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests
import xml.etree.ElementTree as ET

from tally.configurations.config import clean_tally_xml, normalize_to_bytes

logger = logging.getLogger(__name__)


def _xml_scripts_dir() -> Path:
    env = os.environ.get("TALLY_XML_SCRIPTS_DIR", "").strip()
    if env:
        return Path(env)
    cwd_scripts = Path.cwd() / "xml_scripts"
    if cwd_scripts.is_dir():
        return cwd_scripts
    return Path(__file__).resolve().parent.parent / "xml_scripts"


def _get_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _get_text_anywhere(element: ET.Element, tag: str) -> str:
    child = element.find(f".//{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _parse_qty(value: str) -> Optional[float]:
    if not value:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", value.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_rate(value: str) -> Optional[float]:
    if not value:
        return None
    cleaned = value.split("/")[0].replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_amount(value: str) -> Optional[float]:
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return abs(float(cleaned))
    except ValueError:
        return None


def _po_export_xml(company_name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
    <HEADER>
        <VERSION>1</VERSION>
        <TALLYREQUEST>EXPORT</TALLYREQUEST>
        <TYPE>DATA</TYPE>
        <ID>Voucher Register</ID>
    </HEADER>
    <BODY>
        <DESC>
            <STATICVARIABLES>
                <SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY>
                <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                <VoucherTypeName>Purchase Order</VoucherTypeName>
            </STATICVARIABLES>
        </DESC>
    </BODY>
</ENVELOPE>"""


def fetch_purchase_orders(
    tally_url: str,
    *,
    company_name: str,
    timeout: int = 120,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    """Return (po_headers, po_details, error_message)."""
    if not company_name.strip():
        return [], [], "TALLY_COMPANY is not set"

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    payload = _po_export_xml(company_name.strip())

    try:
        response = requests.post(
            tally_url,
            data=normalize_to_bytes(payload),
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        return [], [], "Cannot connect to Tally. Verify Tally is running on port 9000."
    except requests.exceptions.Timeout:
        return [], [], "Tally request timed out while fetching purchase orders."

    if response.status_code != 200:
        return [], [], f"Tally returned HTTP {response.status_code} for purchase orders."

    raw_text = response.content.decode("utf-8", errors="replace")
    cleaned = clean_tally_xml(raw_text)

    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        return [], [], f"Invalid XML from Tally (purchase orders): {exc}"

    po_headers: list[dict[str, Any]] = []
    po_details: list[dict[str, Any]] = []
    business_unit = company_name.strip()

    for voucher in root.findall(".//VOUCHER"):
        order_no = _get_text_anywhere(voucher, "ORDERNO")
        voucher_number = _get_text(voucher, "VOUCHERNUMBER")
        po_id = order_no or voucher_number
        if not po_id:
            continue

        party_name = _get_text(voucher, "PARTYLEDGERNAME")
        voucher_date = _get_text(voucher, "DATE")

        po_headers.append(
            {
                "po_id": po_id,
                "business_unit": business_unit,
                "vendor_id": party_name or None,
                "po_status": "Open",
                "po_type": "Purchase Order",
                "po_date": voucher_date or None,
                "party_name": party_name or None,
                "voucher_number": voucher_number or None,
            }
        )

        inventory_entries = voucher.findall(".//ALLINVENTORYENTRIES.LIST")
        for line_index, item in enumerate(inventory_entries, start=1):
            stock_item = _get_text(item, "STOCKITEMNAME")
            if not stock_item:
                continue
            qty_raw = _get_text(item, "ACTUALQTY") or _get_text(item, "BILLEDQTY")
            rate_raw = _get_text(item, "RATE")
            amount_raw = _get_text(item, "AMOUNT")
            po_details.append(
                {
                    "po_id": po_id,
                    "business_unit": business_unit,
                    "item_id": stock_item,
                    "line_number": line_index,
                    "rate": _parse_rate(rate_raw),
                    "qty": _parse_qty(qty_raw),
                    "units": None,
                    "comments": None,
                    "total": _parse_amount(amount_raw),
                }
            )

    if not po_headers:
        logger.info("[purchase_orders] No purchase orders returned from Tally for company %s", company_name)

    return po_headers, po_details, None
