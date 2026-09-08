"""Build Tally Purchase Invoice voucher XML fragments using ElementTree.

Matching/classification is done upstream (backend/erp_matching.py). This module
only serializes already-classified line items based on ``match_type``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from xml.dom import minidom


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return default


def _entry(amount: float, side: str, *, display_flag: Optional[str] = None) -> tuple[str, str]:
    if side == "Dr":
        return (display_flag or "Yes"), f"-{abs(amount):.2f}"
    if side == "Cr":
        return (display_flag or "No"), f"{abs(amount):.2f}"
    raise ValueError(f"side must be 'Dr' or 'Cr', got {side!r}")


def line_match_type(item: dict[str, Any]) -> str:
    """Return match_type only — XML generation must not re-run matching.

    When ``match_type`` / ``line_match_type`` is missing we fall back to the
    legacy heuristic (presence of ``erp_ledger_name`` → LEDGER), but we no
    longer silently default unclassified lines to STOCK_ITEM.  An item with
    neither flag set is returned as UNMATCHED so it blocks the push and
    surfaces to human review, rather than sending a wrong XML element to Tally.
    """
    match_type = item.get("match_type") or item.get("line_match_type")
    if match_type:
        return str(match_type).strip().upper()
    # Legacy invoices processed before unified match_type existed.
    # Only ``erp_ledger_name`` is a reliable signal; ``ledger_id`` is an
    # internal DB identifier and must not be used as a classification signal.
    if item.get("erp_ledger_name"):
        return "LEDGER"
    if item.get("erp_item_name") or item.get("matched_name"):
        return "STOCK_ITEM"
    return "UNMATCHED"


def line_quantity(item: dict[str, Any]) -> float:
    return _num(item.get("quantity") or item.get("qty") or item.get("original_quantity"))


def line_rate(item: dict[str, Any]) -> float:
    return _num(
        item.get("price_per_unit")
        or item.get("rate")
        or item.get("unit_price")
        or item.get("original_rate")
    )


def line_amount(item: dict[str, Any]) -> float:
    """Return the pre-tax line amount.

    Checks several field names because Gemini and different ERP systems use
    different keys.  For stock items ``quantity × rate`` acts as a fallback;
    for ledger/service lines there is usually no qty or rate, so the explicit
    amount field is the only source of truth.
    """
    raw_amount = (
        item.get("amount")
        or item.get("total")
        or item.get("net_amount")        # common on service invoices
        or item.get("service_amount")    # Gemini sometimes emits this
        or item.get("line_total")        # another Gemini variant
        or item.get("original_amount")
    )
    if raw_amount not in (None, ""):
        parsed = _num(raw_amount)
        if parsed > 0:
            return parsed
    computed = line_quantity(item) * line_rate(item)
    return computed if computed > 0 else 0.0


def line_unit(item: dict[str, Any]) -> str:
    unit = (
        item.get("erp_unit")
        or item.get("unit")
        or item.get("uom")
        or item.get("unit_of_measure")
        or "Nos"
    )
    return str(unit).strip() or "Nos"


def line_hsn(item: dict[str, Any]) -> Optional[str]:
    for key in ("hsn_number", "HSN_number", "HSN", "hsn", "gst_hsn"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def matched_stock_name(item: dict[str, Any]) -> str:
    return (
        item.get("matched_name")
        or item.get("erp_item_name")
        or item.get("item_id")
#        or item.get("service")
        or item.get("description")
        or item.get("original_name")
        or ""
    )


def matched_ledger_name(item: dict[str, Any]) -> str:
    """Return the Tally ledger name for an expense line.

    ``ledger_id`` is intentionally excluded — it is a MongoDB / internal
    identifier that would cause Tally to report "Object: Ledger not found".
    """
    return (
        item.get("matched_name")
        or item.get("erp_ledger_name")
        or item.get("service")
        or item.get("description")
        or item.get("original_name")
        or ""
    )


def _append_text(parent: ET.Element, tag: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    child = ET.SubElement(parent, tag)
    child.text = text


def _append_gst_metadata(
    parent: ET.Element,
    *,
    match_type: str,
    source_name: str,
    hsn: Optional[str],
) -> None:
    """Add GST/HSN tags present on manually exported Tally purchase invoices."""
    if not hsn:
        return
    supply_type = "Services" if match_type == "LEDGER" else "Goods"
    if match_type == "LEDGER":
        _append_text(parent, "GSTSOURCETYPE", "Ledger")
        _append_text(parent, "GSTLEDGERSOURCE", source_name)
        _append_text(parent, "HSNLEDGERSOURCE", hsn)
    else:
        _append_text(parent, "GSTSOURCETYPE", "Stock Item")
        _append_text(parent, "GSTITEMSOURCE", source_name)
        _append_text(parent, "HSNITEMSOURCE", hsn)
    _append_text(parent, "HSNSOURCETYPE", "As per Masters/Company")
    _append_text(parent, "GSTOVRDNTYPEOFSUPPLY", supply_type)
    _append_text(parent, "GSTHSNNAME", hsn)


def build_stock_inventory_entry(
    item: dict[str, Any],
    *,
    purchase_ledger: str,
    po_id: str = "",
) -> ET.Element:
    name = matched_stock_name(item)
    qty = line_quantity(item)
    rate = line_rate(item)
    amount = line_amount(item)
    if amount <= 0 and qty and rate:
        amount = qty * rate
    unit = line_unit(item)
    is_deemed, line_amount_str = _entry(amount, "Dr")
    qty_display = f"{qty:g} {unit}"
    rate_display = f"{rate:.2f}/{unit}" if unit else f"{rate:.2f}"

    inv = ET.Element("ALLINVENTORYENTRIES.LIST")
    _append_text(inv, "STOCKITEMNAME", name)
    _append_text(inv, "ISDEEMEDPOSITIVE", is_deemed)
    _append_text(inv, "RATE", rate_display)
    _append_text(inv, "ACTUALQTY", qty_display)
    _append_text(inv, "BILLEDQTY", qty_display)
    _append_text(inv, "AMOUNT", line_amount_str)
    _append_gst_metadata(inv, match_type="STOCK_ITEM", source_name=name, hsn=line_hsn(item))

    if po_id:
        batch = ET.SubElement(inv, "BATCHALLOCATIONS.LIST")
        _append_text(batch, "TRACKINGNUMBER", "")
        _append_text(batch, "ORDERNO", po_id)
        _append_text(batch, "ORDERNUMBERS.LIST", po_id)
        _append_text(batch, "NUMBEROFBUYERITEMS", f"{qty:g}")
        _append_text(batch, "AMOUNT", line_amount_str)

    acct = ET.SubElement(inv, "ACCOUNTINGALLOCATIONS.LIST")
    _append_text(acct, "LEDGERNAME", purchase_ledger)
    _append_text(acct, "ISDEEMEDPOSITIVE", is_deemed)
    _append_text(acct, "AMOUNT", line_amount_str)
    return inv


def build_expense_ledger_entry(item: dict[str, Any]) -> ET.Element:
    """Build a ``LEDGERENTRIES.LIST`` block for an expense / service line.

    Tally Prime expense ledger entries do NOT require quantity or rate fields —
    only the ledger name, the pre-tax base amount (``VATEXPAMOUNT``), and the
    final amount are mandatory.  An optional ``RATE`` tag carries the GST rate
    when present and is used by Tally's internal GST computation.
    """
    name = matched_ledger_name(item)
    amount = line_amount(item)
    is_deemed, line_amount_str = _entry(amount, "Dr")

    # GST rate on the line — Tally uses this for assessable value computation.
    # Accept both "tax_rate" (Gemini extraction field) and "gst_rate" (ERP field).
    gst_rate = _num(item.get("tax_rate") or item.get("gst_rate"))

    entry = ET.Element("LEDGERENTRIES.LIST")
    _append_text(entry, "LEDGERNAME", name)
    _append_text(entry, "ISPARTYLEDGER", "No")
    _append_gst_metadata(entry, match_type="LEDGER", source_name=name, hsn=line_hsn(item))
    _append_text(entry, "ISDEEMEDPOSITIVE", is_deemed)
    if gst_rate > 0:
        _append_text(entry, "RATE", f"{gst_rate:.2f}")
    _append_text(entry, "VATEXPAMOUNT", line_amount_str)   # pre-tax assessable value
    _append_text(entry, "AMOUNT", line_amount_str)         # expense debit (tax posted separately)
    return entry


def build_ledger_entry_block(
    ledger_name: str,
    amount: float,
    side: str,
    *,
    display_flag: Optional[str] = None,
    rate: Optional[float] = None,
    bill_ref: Optional[str] = None,
    is_party_ledger: bool = False,
) -> ET.Element:
    is_deemed, amount_str = _entry(amount, side, display_flag=display_flag)
    entry = ET.Element("LEDGERENTRIES.LIST")
    _append_text(entry, "LEDGERNAME", ledger_name)
    _append_text(entry, "ISPARTYLEDGER", "Yes" if is_party_ledger else "No")
    _append_text(entry, "ISDEEMEDPOSITIVE", is_deemed)
    if is_party_ledger:
        _append_text(entry, "ISLASTDEEMEDPOSITIVE", is_deemed)
    if rate is not None and rate > 0:
        _append_text(entry, "RATE", f"{rate:.2f}")
    _append_text(entry, "AMOUNT", amount_str)
    if bill_ref and side == "Cr":
        bill = ET.SubElement(entry, "BILLALLOCATIONS.LIST")
        _append_text(bill, "NAME", bill_ref)
        _append_text(bill, "BILLTYPE", "New Ref")
        _append_text(bill, "AMOUNT", amount_str)
    return entry


def elements_to_fragment(elements: list[ET.Element], *, indent: str = "") -> str:
    """Serialize elements to an indented XML fragment (no XML declaration)."""
    if not elements:
        return ""
    parts: list[str] = []
    for element in elements:
        rough = ET.tostring(element, encoding="unicode")
        if indent:
            parsed = minidom.parseString(rough).documentElement
            formatted = parsed.toprettyxml(indent=indent)
            lines = [ln for ln in formatted.splitlines() if ln.strip()]
            # drop <?xml ...?> from minidom
            if lines and lines[0].startswith("<?xml"):
                lines = lines[1:]
            parts.append("\n".join(lines))
        else:
            parts.append(rough)
    return "\n".join(parts)


def build_line_entry_fragments(
    data: dict[str, Any],
    *,
    purchase_ledger: str,
    po_id: str = "",
) -> tuple[str, str, float, list[str]]:
    """Return (inventory_xml, expense_xml, taxable_total, unmatched_labels)."""
    json_data = data["gemini"]["json"]
    line_items = json_data.get("line_items") or []

    inventory_elements: list[ET.Element] = []
    expense_elements: list[ET.Element] = []
    taxable_total = 0.0
    unmatched_labels: list[str] = []

    for item in line_items:
        if not isinstance(item, dict):
            continue
        match_type = line_match_type(item)
        if match_type == "UNMATCHED":
            label = (
                item.get("original_name")
                or item.get("service")
                or item.get("description")
                or "line item"
            )
            unmatched_labels.append(str(label))
            continue
        if match_type == "LEDGER":
            if not matched_ledger_name(item).strip():
                unmatched_labels.append("ledger line missing Tally ledger name")
                continue
            if line_amount(item) <= 0:
                label = item.get("service") or item.get("description") or "ledger line"
                unmatched_labels.append(f"{label} (amount must be greater than zero)")
                continue
            expense_elements.append(build_expense_ledger_entry(item))
            taxable_total += line_amount(item)
            continue
        if match_type == "STOCK_ITEM":
            label = matched_stock_name(item).strip() or "stock line"
            missing: list[str] = []
            if not matched_stock_name(item).strip():
                missing.append("Tally stock item name")
            if line_quantity(item) <= 0:
                missing.append("quantity")
            if line_rate(item) <= 0:
                missing.append("rate")
            if missing:
                unmatched_labels.append(f"{label} (missing/invalid {', '.join(missing)})")
                continue
            inventory_elements.append(
                build_stock_inventory_entry(
                    item,
                    purchase_ledger=purchase_ledger,
                    po_id=po_id,
                )
            )
            amt = line_amount(item)
            if amt <= 0:
                amt = line_quantity(item) * line_rate(item)
            taxable_total += amt
            continue
        unmatched_labels.append(str(item.get("original_name") or "unknown line"))

    inventory_xml = elements_to_fragment(inventory_elements, indent=" " * 24)
    expense_xml = elements_to_fragment(expense_elements, indent=" " * 6)
    return inventory_xml, expense_xml, taxable_total, unmatched_labels
