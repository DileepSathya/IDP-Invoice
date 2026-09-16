import logging
import os
import re
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from tally.configurations.config import (
    clean_tally_xml,
    normalize_to_bytes,
    convert_date_yyyymmdd,
    parse_tally_response,
)
from tally.voucher_xml import (
    build_ledger_entry_block,
    build_line_entry_fragments,
    elements_to_fragment,
)

logger = logging.getLogger(__name__)

IGST_ledger = "IGST"

# Largest gap between the invoice's stated total and the total computed from its
# line items (+ tax - discount) that we are willing to absorb into the Round Off
# ledger. A genuine round-off is sub-rupee. Anything larger means an upstream
# extraction problem (a mis-read line item, a missed tax component), and quietly
# plugging it would push a *wrong but importable* voucher into Tally - worse than
# failing. Above this we refuse to push so the invoice goes back for review.
ROUND_OFF_TOLERANCE = float(os.environ.get("TALLY_ROUND_OFF_TOLERANCE", "1.00"))

# Voucher type name (VCHTYPE / VOUCHERTYPENAME) — set via TALLY_VOUCHER_TYPE in pipeline.py.
# Purchase ledger for ACCOUNTINGALLOCATIONS — separate from voucher type.
TALLY_PURCHASE_LEDGER = os.environ.get("TALLY_PURCHASE_LEDGER", "Purchase A/c").strip() or "Purchase A/c"

_PO_NOT_APPLICABLE_ALIASES = frozenset(
    {
        "not applicable",
        "not-applicable",
        "n/a",
        "na",
        "none",
        "-",
    }
)


def _po_id_for_tally_order(po_id: object) -> str:
    """Return po_id when it should be sent as an order number; else empty string."""
    if po_id is None:
        return ""
    text = str(po_id).strip()
    if not text:
        return ""
    normalized = text.lower().replace(".", "")
    if normalized in _PO_NOT_APPLICABLE_ALIASES:
        return ""
    return text

# Debits and credits must agree to the paisa, or Tally files the voucher as an
# import exception ("Mismatch in total amount between Credit and Debit entries").
BALANCE_EPSILON = 0.01


class VoucherImbalanceError(ValueError):
    """The extracted invoice figures do not reconcile with each other, so no
    balanced voucher can be built from them. Distinct from a defect in our own
    sign handling, which `_voucher_imbalance()` catches just before sending."""


def _safe(value):
    """XML-escapes any value that will be inserted as element text content.
    Prevents raw '&', '<', '>' in extracted invoice text (e.g. 'S & S Traders')
    from breaking the XML document."""
    return xml_escape(str(value))


def _num(value, default=0.0):
    """Parse a number out of extracted invoice data, which arrives as str/int/
    float and sometimes carries thousands separators or a currency symbol
    ("11,391.00", "Rs 11391", "18%")."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return default


def _entry(amount, side, *, display_flag=None):
    """Single source of truth for Tally's Dr/Cr sign convention.

    Tally settles a voucher on the *sign of <AMOUNT>*: negative = Debit,
    positive = Credit. <ISDEEMEDPOSITIVE> is a display hint that is supposed to
    agree with it (Yes = Debit). Writing the two independently at each call site
    is what produced the round-off bug - a branch that set the flag to "Yes"
    while still emitting a positive amount - so every entry now derives both from
    one `side` argument and they can no longer drift apart.

    Returns (isdeemedpositive, formatted_amount).

    `display_flag` overrides only the ISDEEMEDPOSITIVE hint, never the amount
    sign. See the Discount Received entry for the one place that needs it.
    """
    if side == "Dr":
        return (display_flag or "Yes"), f"-{abs(amount):.2f}"
    if side == "Cr":
        return (display_flag or "No"), f"{abs(amount):.2f}"
    raise ValueError(f"side must be 'Dr' or 'Cr', got {side!r}")


def _last_payload_path(template_path):
    """Where the outgoing payload is dumped for debugging.

    By default this sits beside the bridge folder that owns the voucher
    template - which means the source tree and the packaged build write to two
    *different* files, and whichever one you are not running looks stale:

        source run : <repo>/TALLY INTEGRATION/last_payload.xml
        frozen run : <install>/tally-bridge/last_payload.xml

    Set TALLY_LAST_PAYLOAD_PATH to pin both run modes to one location. The
    resolved path is logged on every push so it is never a guess.
    """
    override = os.environ.get("TALLY_LAST_PAYLOAD_PATH", "").strip()
    if override:
        return Path(override)
    return Path(template_path).resolve().parent.parent / "last_payload.xml"


def _voucher_imbalance(root):
    """Sum the signed amounts of every entry that actually posts to a ledger and
    return the residual - 0.00 on a well-formed voucher.

    Only two element types hit the books: the ACCOUNTINGALLOCATIONS inside each
    inventory line, and the voucher-level LEDGERENTRIES. ALLINVENTORYENTRIES,
    BATCHALLOCATIONS and BILLALLOCATIONS amounts are stock and bill-reference
    sub-allocations of those - counting them would double up.

    Returns None if the payload has no <VOUCHER> element to inspect.
    """
    voucher = next(root.iter("VOUCHER"), None)
    if voucher is None:
        return None

    amounts = []

    def _collect_direct_amounts(element):
        for leaf in element:  # direct children only - never nested allocations
            if leaf.tag == "AMOUNT" and (leaf.text or "").strip():
                try:
                    amounts.append(float(leaf.text.strip()))
                except ValueError:
                    logger.warning("Non-numeric <AMOUNT> in voucher payload: %r", leaf.text)

    for child in voucher:
        if child.tag == "LEDGERENTRIES.LIST":
            _collect_direct_amounts(child)
        elif child.tag == "ALLINVENTORYENTRIES.LIST":
            for sub in child:
                if sub.tag == "ACCOUNTINGALLOCATIONS.LIST":
                    _collect_direct_amounts(sub)

    return round(sum(amounts), 2)


def line_entries_xml(data):
    """Build inventory and expense ledger XML blocks from classified line items.

    Classification (match_type) is resolved upstream; this function only serializes.
    """
    json_data = data["gemini"]["json"]
    additional_fields = json_data.get("additional_fields", {})
    purchase_ledger = json_data.get("purchase_ledger") or TALLY_PURCHASE_LEDGER
    po_id = _po_id_for_tally_order(
        json_data.get("po_id") or additional_fields.get("po_id", "")
    )

    if not po_id:
        logger.warning(
            "No po_id found in extracted data (checked top-level 'po_id' and "
            "additional_fields.po_id) - inventory lines will be sent WITHOUT "
            "ORDERDETAILS.LIST, so Tally has nothing to match against."
        )

    inventory_xml, expense_xml, taxable_total, unmatched_labels = build_line_entry_fragments(
        data,
        purchase_ledger=purchase_ledger,
        po_id=po_id,
    )

    if unmatched_labels:
        preview = ", ".join(unmatched_labels[:5])
        suffix = "..." if len(unmatched_labels) > 5 else ""
        raise ValueError(
            f"Cannot build voucher: {len(unmatched_labels)} unmatched line item(s): "
            f"{preview}{suffix}"
        )

    return inventory_xml, expense_xml, taxable_total


def ledger_entries_xml(data):
    """Backward-compatible wrapper returning inventory XML and line total only."""
    inventory_xml, _, total = line_entries_xml(data)
    return inventory_xml, total


def _sum_line_item_tax(json_root: dict) -> float:
    """Sum the ``tax_amount`` field across all line items.

    Gemini sometimes stores the GST total at the line-item level rather than
    as a flat ``igst_amount`` / ``cgst_amount`` / ``sgst_amount`` field inside
    ``additional_fields``.  When those top-level keys are absent this function
    provides the fallback total so the reconciliation formula still works.
    """
    total = 0.0
    for li in json_root.get("line_items") or []:
        if isinstance(li, dict):
            total += _num(li.get("tax_amount"))
    return round(total, 2)


def tax_entries_xml(data, computed_total):
    """Build tax/discount/round-off LEDGERENTRIES.LIST blocks (ElementTree).

    Raises VoucherImbalanceError if figures cannot be reconciled within tolerance.
    """
    json_root = data["gemini"]["json"]
    additional_fields = json_root.get("additional_fields", {})
    elements: list[ET.Element] = []
    total_tax_amount = 0.0

    # Gemini may emit "discount_amount" or the shorter "discount" — accept either.
    # A discount is a reduction regardless of whether OCR preserved the
    # invoice's printed accounting marker, e.g. ``(-)100.00``.
    discount = abs(_num(additional_fields.get("discount") or additional_fields.get("discount_amount")))
    if discount > 0:
        discount_ledger = additional_fields.get("discount_ledger_name", "Discount Received")
        elements.append(
            build_ledger_entry_block(
                discount_ledger,
                discount,
                "Cr",
                display_flag="Yes",
            )
        )

    # Gemini may emit GSTIN under any of these key names depending on the prompt version:
    #   "seller_gstin", "seller_gstin_uin", "gstin_seller"
    #   "buyer_gstin",  "buyer_gstin_uin",  "gstin_buyer"
    # Accept all variants so the inter-state (IGST) detection fires correctly.
    seller_gst = str(
        additional_fields.get("seller_gstin")
        or additional_fields.get("seller_gstin_uin")
        or additional_fields.get("gstin_seller")
        or ""
    )[:2]
    buyer_gst = str(
        additional_fields.get("buyer_gstin")
        or additional_fields.get("buyer_gstin_uin")
        or additional_fields.get("gstin_buyer")
        or ""
    )[:2]

    igst_rate = _num(additional_fields.get("igst_rate"))
    cgst_rate = _num(additional_fields.get("cgst_rate"))
    sgst_rate = _num(additional_fields.get("sgst_rate"))
    explicit_igst = _num(additional_fields.get("igst_amount"))
    explicit_cgst = _num(additional_fields.get("cgst_amount"))
    explicit_sgst = _num(additional_fields.get("sgst_amount"))
    line_tax_total = _sum_line_item_tax(json_root)

    # Explicit extracted tax amounts are stronger evidence than GSTIN state codes.
    # GSTIN is frequently absent from OCR, but that must not make a clearly extracted
    # IGST amount disappear from voucher reconciliation.
    use_igst = explicit_igst > 0 or (
        explicit_cgst <= 0
        and explicit_sgst <= 0
        and line_tax_total > 0
        and (igst_rate > 0 or (seller_gst and buyer_gst and seller_gst != buyer_gst))
    )

    if use_igst:
        igst_amount = explicit_igst or line_tax_total
        if igst_amount > 0:
            total_tax_amount += igst_amount
            elements.append(
                build_ledger_entry_block(IGST_ledger, igst_amount, "Dr", rate=igst_rate)
            )
    else:
        cgst_amount = explicit_cgst
        sgst_amount = explicit_sgst
        for tax_type, rate, tax_amount in (
            ("CGST", cgst_rate, cgst_amount),
            ("SGST", sgst_rate, sgst_amount),
        ):
            if tax_amount > 0:
                total_tax_amount += tax_amount
                elements.append(
                    build_ledger_entry_block(tax_type, tax_amount, "Dr", rate=rate)
                )

    invoice_total = _num(json_root.get("total_amount"))
    computed_from_lines = computed_total - discount + total_tax_amount
    delta = round(invoice_total - computed_from_lines, 2)

    if abs(delta) > ROUND_OFF_TOLERANCE:
        raise VoucherImbalanceError(
            f"Invoice total {invoice_total:.2f} does not reconcile with the extracted "
            f"figures ({computed_total:.2f} line items - {discount:.2f} discount "
            f"+ {total_tax_amount:.2f} tax = {computed_from_lines:.2f}); gap of "
            f"{delta:.2f} exceeds the {ROUND_OFF_TOLERANCE:.2f} round-off tolerance. "
            f"Refusing to push - the extracted values need review."
        )

    if delta != 0:
        round_ledger = additional_fields.get("round_off_ledger_name", "Round Off")
        elements.append(
            build_ledger_entry_block(
                round_ledger,
                abs(delta),
                "Dr" if delta > 0 else "Cr",
                # For a round-down, Tally still needs a positive credit amount
                # to balance the books, but this display hint makes Invoice
                # Voucher View subtract it from the visible payable total.
                display_flag="Yes",
            )
        )
        # Accept both "round_off" and the Gemini-emitted "round_off_amount".
        extracted_round_off = _num(additional_fields.get("round_off") or additional_fields.get("round_off_amount"))
        if abs(abs(extracted_round_off) - abs(delta)) > BALANCE_EPSILON:
            logger.warning(
                "Round off computed as %.2f but extraction reported %.2f - "
                "using the computed value so the voucher balances.",
                delta,
                extracted_round_off,
            )

    return elements_to_fragment(elements, indent=" " * 6)


def send_template_to_tally(TALLY_URL, path, company_name, data, invoice_number, voucher_type, vendor_name,user_name,user_password):
    """Build inventory purchase voucher from template and POST to Tally."""
    base_result = {
        "success": False,
        "message": "",
        "error_reason": None,
        "invoice_number": invoice_number,
        "tally_company": company_name,
        "tally_response": None,
    }
    try:
        json_root = data["gemini"]["json"]
        required = {
            "company_name": company_name,
            "vendor_name": vendor_name,
            "invoice_number": json_root.get("invoice_number") or json_root.get("invoice"),
            "voucher_type": voucher_type,
            "total_amount": json_root.get("total_amount"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required field(s) before sending to Tally: {missing}")

        with open(path, "r", encoding="utf-8") as file:
            template_content = file.read()

        inventory_entries_xml, expense_ledger_entries_xml, cmp_total = line_entries_xml(data)
        voucher_entry_mode = (
            "Item Invoice" if inventory_entries_xml.strip() else "Accounting Invoice"
        )
        tax_entries_string = tax_entries_xml(data, cmp_total)

        invoice_total = _num(json_root.get("total_amount"))
        party_entry = build_ledger_entry_block(
            vendor_name,
            invoice_total,
            "Cr",
            bill_ref=invoice_number,
            is_party_ledger=True,
        )
        party_ledger_xml = elements_to_fragment([party_entry], indent=" " * 6)

        invoice_date = json_root.get("invoice_date") or json_root.get("date")
        if not invoice_date:
            raise ValueError("Missing required field: invoice_date")

        xml_payload = template_content.format(
            TALLY_USER_NAME = _safe(user_name),
            TALLY_USER_PASSWORD = _safe(user_password),
            COMPANY_NAME=_safe(company_name),
            INVOICE_NUMBER=_safe(invoice_number),
            VOUCHER_TYPE=_safe(voucher_type),
            VOUCHER_DATE=convert_date_yyyymmdd("2025-07-01"), #invoice_date
            VOUCHER_ENTRY_MODE=voucher_entry_mode,
            PARTY_LEDGER=_safe(vendor_name),
            INVENTORY_ENTRIES_XML=inventory_entries_xml,
            EXPENSE_LEDGER_ENTRIES_XML=expense_ledger_entries_xml,
            TAX_ENTRIES_XML=tax_entries_string,
            PARTY_LEDGER_ENTRY_XML=party_ledger_xml,
        )

        xml_payload = clean_tally_xml(xml_payload)

        # Dumped before the well-formedness and balance checks below, so a
        # rejected voucher still leaves its payload on disk to inspect.
        debug_path = _last_payload_path(path)
        try:
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(xml_payload, encoding="utf-8")
            logger.info("Outgoing voucher payload written to %s", debug_path)
        except Exception as exc:
            logger.warning("Could not write %s: %s", debug_path, exc)

        try:
            parsed_payload = ET.fromstring(xml_payload)
        except ET.ParseError as exc:
            msg = f"Built XML payload is not well-formed: {exc}"
            logger.error(msg)
            base_result["message"] = "Invalid voucher XML"
            base_result["error_reason"] = msg
            return base_result

        # Last line of defence before Tally sees this. An unbalanced voucher is
        # accepted at the HTTP level and then silently filed as an import
        # exception (EXCEPTIONS=1, CREATED=0), so the failure only surfaces later
        # in Tally's suspense list. Catching it here puts the exact rupee gap in
        # the logs and in tally_error_reason instead.
        imbalance = _voucher_imbalance(parsed_payload)
        if imbalance is None:
            logger.warning("No <VOUCHER> element found in built payload - skipping balance check.")
        elif abs(imbalance) > BALANCE_EPSILON:
            msg = (
                f"Voucher does not balance: debits and credits differ by {imbalance:.2f}. "
                f"Not sending to Tally, which would reject it as an import exception."
            )
            logger.error("%s (invoice=%s)", msg, invoice_number)
            base_result["message"] = "Voucher does not balance"
            base_result["error_reason"] = msg
            return base_result

        headers = {"Content-Type": "text/xml; charset=utf-8"}
        response = requests.post(TALLY_URL, data=normalize_to_bytes(xml_payload), headers=headers, timeout=120)

        base_result["tally_response"] = response.text
        if response.status_code != 200:
            msg = f"HTTP {response.status_code} from Tally"
            logger.error("%s: %s", msg, response.text[:500])
            base_result["message"] = msg
            base_result["error_reason"] = response.text[:500]
            return base_result

        logger.info("Raw response from Tally: %s", response.text[:500])
        parsed = parse_tally_response(response.text)
        base_result["success"] = parsed["success"]
        base_result["error_reason"] = parsed.get("error_reason")
        base_result["message"] = (
            "Voucher created in Tally" if parsed["success"] else "Tally rejected the voucher"
        )
        return base_result

    except VoucherImbalanceError as exc:
        # Upstream extraction problem, not a bug in voucher construction - report
        # it distinctly so it can be routed back for human review. This fires
        # before the payload is assembled, so no last_payload.xml is written;
        # say so explicitly rather than leaving a stale file to mislead.
        logger.error(
            "Cannot build a balanced voucher for %s (no payload written): %s",
            invoice_number,
            exc,
        )
        base_result["message"] = "Invoice figures do not reconcile"
        base_result["error_reason"] = str(exc)
        return base_result

    except Exception as exc:
        logger.error("Error executing Tally template injection: %s", exc)
        base_result["message"] = "Voucher push failed"
        base_result["error_reason"] = str(exc)
        return base_result
