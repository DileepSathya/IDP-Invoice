"""Fetch Tally master data (vendors, items, purchase orders) for ERP matching."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests
import xml.etree.ElementTree as ET

from tally.configurations.config import clean_tally_xml, normalize_to_bytes
from tally.purchase_orders import fetch_purchase_orders

logger = logging.getLogger(__name__)

SUNDRY_CREDITORS_GROUP = "Sundry Creditors"


def _xml_scripts_dir() -> Path:
    env = os.environ.get("TALLY_XML_SCRIPTS_DIR", "").strip()
    if env:
        return Path(env)
    cwd_scripts = Path.cwd() / "xml_scripts"
    if cwd_scripts.is_dir():
        return cwd_scripts
    return Path(__file__).resolve().parent.parent / "xml_scripts"


def _load_xml_template(filename: str, *, company_name: Optional[str] = None) -> str:
    path = _xml_scripts_dir() / filename
    if not path.is_file():
        raise FileNotFoundError(f"Tally XML template not found: {path}")
    xml_text = path.read_text(encoding="utf-8")
    if company_name:
        company_tag = f"<SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY>"
        if "<SVCURRENTCOMPANY>" not in xml_text:
            xml_text = xml_text.replace(
                "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>",
                f"<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>\n                {company_tag}",
            )
        else:
            xml_text = re.sub(
                r"<SVCURRENTCOMPANY>.*?</SVCURRENTCOMPANY>",
                company_tag,
                xml_text,
                count=1,
            )
    return xml_text


def _post_tally(tally_url: str, xml_request: str, *, timeout: int = 60) -> tuple[str, Optional[str]]:
    headers = {"Content-Type": "text/xml;charset=utf-8"}
    try:
        response = requests.post(
            tally_url,
            data=normalize_to_bytes(xml_request),
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        return "", "Cannot connect to Tally. Verify Tally is running on port 9000."
    except requests.exceptions.Timeout:
        return "", "Tally request timed out."

    if response.status_code != 200:
        return "", f"Tally returned HTTP {response.status_code}"

    return response.content.decode("utf-8", errors="replace"), None


def _ledger_gstin(ledger: ET.Element) -> Optional[str]:
    for tag in ("GSTIN", "PARTYGSTIN", "INCOMETAXNUMBER"):
        node = ledger.find(tag)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    for node in ledger.iter():
        if node.tag.upper() in {"GSTIN", "PARTYGSTIN"} and node.text and node.text.strip():
            return node.text.strip()
    return None


def fetch_vendors(
    tally_url: str,
    *,
    company_name: Optional[str] = None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return Sundry Creditors ledgers as vendor_master-shaped rows."""
    try:
        xml_request = _load_xml_template("ledger_list.xml", company_name=company_name)
    except FileNotFoundError as exc:
        return [], str(exc)

    raw_text, error = _post_tally(tally_url, xml_request)
    if error:
        return [], error

    cleaned = clean_tally_xml(raw_text)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        return [], f"Invalid XML from Tally (ledgers): {exc}"

    vendors: list[dict[str, Any]] = []
    for ledger in root.findall(".//LEDGER"):
        parent_tag = ledger.find("PARENT")
        parent = parent_tag.text.strip() if parent_tag is not None and parent_tag.text else ""
        if parent != SUNDRY_CREDITORS_GROUP:
            continue

        name = (ledger.get("NAME") or "").strip()
        if not name:
            name_tag = ledger.find("NAME")
            if name_tag is not None and name_tag.text:
                name = name_tag.text.strip()
        if not name:
            continue

        gst = _ledger_gstin(ledger)
        vendors.append(
            {
                "vendor_id": name,
                "vendor_name": name,
                "gst_tax_number": gst,
                "tin_number": None,
                "vendor_address_1": None,
                "vendor_address_2": None,
                "vendor_address_3": None,
                "city": None,
                "state": None,
                "country": None,
                "pin_code": None,
                "primary_ph_number": None,
                "email_primary": None,
                "bank_ac_number": None,
                "bank_name": None,
            }
        )

    vendors.sort(key=lambda row: (row.get("vendor_name") or "").lower())
    return vendors, None


def fetch_items(
    tally_url: str,
    *,
    company_name: Optional[str] = None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return stock items as item_master-shaped rows."""
    try:
        xml_request = _load_xml_template("stock_items.xml", company_name=company_name)
    except FileNotFoundError as exc:
        return [], str(exc)

    raw_text, error = _post_tally(tally_url, xml_request)
    if error:
        return [], error

    cleaned = clean_tally_xml(raw_text)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        return [], f"Invalid XML from Tally (stock items): {exc}"

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stock in root.findall(".//STOCKITEM"):
        name = (stock.get("NAME") or "").strip()
        if not name:
            name_tag = stock.find("NAME")
            if name_tag is not None and name_tag.text:
                name = name_tag.text.strip()
        if not name or name in seen:
            continue
        seen.add(name)

        parent_tag = stock.find("PARENT")
        parent = parent_tag.text.strip() if parent_tag is not None and parent_tag.text else None
        unit_tag = stock.find("BASEUNITS")
        unit = unit_tag.text.strip() if unit_tag is not None and unit_tag.text else None

        items.append(
            {
                "item_id": name,
                "item_name": name,
                "description": name,
                "category": parent,
                "units": unit,
                "rate": None,
            }
        )

    items.sort(key=lambda row: (row.get("item_name") or "").lower())
    return items, None


def fetch_all_masters(
    tally_url: str,
    *,
    company_name: str,
) -> dict[str, Any]:
    """Fetch vendors, items, and purchase orders in one call."""
    company = (company_name or "").strip()
    errors: list[str] = []

    vendors, vendor_err = fetch_vendors(tally_url, company_name=company or None)
    if vendor_err:
        errors.append(f"vendors: {vendor_err}")

    items, item_err = fetch_items(tally_url, company_name=company or None)
    if item_err:
        errors.append(f"items: {item_err}")

    po_headers: list[dict[str, Any]] = []
    po_details: list[dict[str, Any]] = []
    if company:
        po_headers, po_details, po_err = fetch_purchase_orders(tally_url, company_name=company)
        if po_err:
            errors.append(f"purchase_orders: {po_err}")
    else:
        errors.append("purchase_orders: TALLY_COMPANY is not set")

    return {
        "company": company or None,
        "vendors": vendors,
        "items": items,
        "po_headers": po_headers,
        "po_details": po_details,
        "errors": errors,
        "success": not errors,
    }
