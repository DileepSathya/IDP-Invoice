"""Regression tests for voucher construction in tally/create_voucher.py.

Every test asserts the same invariant: the debits and credits of the built
voucher sum to zero. That is exactly the condition Tally checks on import, and
the one that failed on invoice KLKA2526-12078 ("Mismatch in total amount between
Credit and Debit entries") because the Round Off entry was posted on the wrong
side of the books.

Runs offline - requests.post is stubbed, so no Tally instance is needed:

    cd "TALLY INTEGRATION"
    python tests/test_create_voucher.py        # or: pytest tests/
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import requests  # noqa: E402
from tally import create_voucher as cv  # noqa: E402

TEMPLATE = str(BASE / "xml_scripts" / "create_voucher.xml")

_captured = {}


class _FakeResponse:
    status_code = 200
    text = ("<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED>"
            "<ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></RESPONSE>")


def _fake_post(url, data=None, headers=None, timeout=None):
    _captured["payload"] = data.decode("utf-8") if isinstance(data, bytes) else data
    return _FakeResponse()


requests.post = _fake_post
cv.requests.post = _fake_post


def _invoice(total_amount, line_items, additional_fields=None, *,
             invoice_number="TEST-001", invoice_date="1-Jul-25", po_id="PO1001"):
    return {"gemini": {"json": {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "total_amount": total_amount,
        "line_items": line_items,
        "po_id": po_id,
        "additional_fields": additional_fields or {},
    }}}


def _build(data):
    """Run the full send path and return (result, parsed_payload_or_None)."""
    _captured.clear()
    result = cv.send_template_to_tally(
        "http://localhost:9000", TEMPLATE, "Test Company", data,
        data["gemini"]["json"]["invoice_number"], "Purchase", "Test Vendor",
    )
    payload = _captured.get("payload")
    return result, (ET.fromstring(payload) if payload else None)


def _assert_balanced(data):
    result, root = _build(data)
    assert result["success"], f"push failed: {result['error_reason']}"
    imbalance = cv._voucher_imbalance(root)
    assert abs(imbalance) <= cv.BALANCE_EPSILON, f"voucher out of balance by {imbalance:+.2f}"
    return root


# --- the invoice that originally failed -------------------------------------
# 28 x 499 = 13,972.00 goods, less 4,319.00 discount, plus 18% IGST 1,737.54,
# rounded from 11,390.54 up to a payable 11,391.00 (round off = +0.46 Dr).
REAL_INVOICE = _invoice(
    "11391.00",
    [{"erp_item_name": "Waltr A Monthly Subscribe", "quantity": 28,
      "price_per_unit": 499.00, "unit": "Nos"}],
    {"discount": 4319, "igst_rate": 18, "igst_amount": 1737.54, "round_off": 0.46,
     "seller_gstin": "29ABCDE1234F1Z5", "buyer_gstin": "36ABCDE1234F1Z5"},
    invoice_number="KLKA2526-12078",
)


def test_real_invoice_balances():
    root = _assert_balanced(REAL_INVOICE)
    entries = {e.find("LEDGERNAME").text: float(e.find("AMOUNT").text)
               for e in root.iter("LEDGERENTRIES.LIST")}
    # Round Off must be a DEBIT here (negative amount), which is the bug that
    # produced the original 0.92 mismatch when it was posted as a credit.
    assert entries["Round Off"] == -0.46, entries


def test_round_off_direction_flips_when_invoice_rounds_down():
    root = _assert_balanced(_invoice(
        "1179.00", [{"service": "Widget", "quantity": 1, "price_per_unit": 1000, "unit": "Nos"}],
        {"igst_rate": 18, "igst_amount": 180.00, "round_off": -1.00,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))
    entries = {e.find("LEDGERNAME").text: float(e.find("AMOUNT").text)
               for e in root.iter("LEDGERENTRIES.LIST")}
    assert entries["Round Off"] == 1.00, entries  # positive = credit


def test_round_off_closes_gap_even_when_field_missing():
    """Extraction sometimes misses the round-off line. The voucher must still
    balance - the old code emitted no entry at all and went out short."""
    _assert_balanced(_invoice(
        "11391.00", [{"service": "Waltr", "quantity": 28, "price_per_unit": 499.00, "unit": "Nos"}],
        {"discount": 4319, "igst_rate": 18, "igst_amount": 1737.54,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))


def test_multi_line_batch_allocations_mirror_their_own_line():
    """Each BATCHALLOCATIONS amount must equal ITS line, not the running total."""
    root = _assert_balanced(_invoice(
        "4720.00",
        [{"service": "Item A", "quantity": 10, "price_per_unit": 100, "unit": "Nos"},
         {"service": "Item B", "quantity": 5, "price_per_unit": 200, "unit": "Nos"},
         {"service": "Item C", "quantity": 2, "price_per_unit": 1000, "unit": "Nos"}],
        {"igst_rate": 18, "igst_amount": 720.00,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))
    lines = list(root.iter("ALLINVENTORYENTRIES.LIST"))
    assert len(lines) == 3
    for idx, line in enumerate(lines, 1):
        line_amt = float(line.find("AMOUNT").text)
        batch_amt = float(line.find("BATCHALLOCATIONS.LIST").find("AMOUNT").text)
        assert abs(line_amt - batch_amt) < 0.01, f"line {idx}: {line_amt} vs batch {batch_amt}"


def test_intra_state_cgst_sgst_split_balances():
    """Same state code on both GSTINs takes the CGST/SGST branch."""
    root = _assert_balanced(_invoice(
        "1180.00", [{"service": "Widget", "quantity": 1, "price_per_unit": 1000, "unit": "Nos"}],
        {"igst_rate": 18, "igst_amount": 180.00,
         "seller_gstin": "29AAA", "buyer_gstin": "29BBB"},
    ))
    names = {e.find("LEDGERNAME").text for e in root.iter("LEDGERENTRIES.LIST")}
    assert {"CGST", "SGST"} <= names, names


def test_invoice_with_no_rounding_needed():
    root = _assert_balanced(_invoice(
        "1000.00", [{"service": "Widget", "quantity": 4, "price_per_unit": 250, "unit": "Nos"}]))
    names = {e.find("LEDGERNAME").text for e in root.iter("LEDGERENTRIES.LIST")}
    assert "Round Off" not in names, names


def test_messy_extracted_values_are_parsed():
    """Gemini returns strings, sometimes with separators or a percent sign."""
    _assert_balanced(_invoice(
        "11,391.00", [{"service": "Waltr", "quantity": "28", "price_per_unit": "499.00", "unit": "Nos"}],
        {"discount": "4,319.00", "igst_rate": "18%", "igst_amount": "1,737.54",
         "round_off": "0.46", "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))


def test_large_gap_is_refused_not_plugged():
    """A gap too big to be a rounding artefact means the extraction is wrong.
    Pushing it would create a plausible-looking but incorrect voucher in Tally."""
    result, payload = _build(_invoice(
        "9999.00", [{"service": "Widget", "quantity": 1, "price_per_unit": 1000, "unit": "Nos"}],
        {"igst_rate": 18, "igst_amount": 180.00,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))
    assert not result["success"]
    assert payload is None, "nothing should have been sent to Tally"
    assert "does not reconcile" in result["error_reason"]


def test_voucher_uses_the_real_invoice_date():
    """The date was hardcoded to 2025-07-01 for every voucher pushed."""
    _, root = _build(_invoice(
        "1000.00", [{"service": "Widget", "quantity": 4, "price_per_unit": 250, "unit": "Nos"}],
        invoice_date="2026-03-15",
    ))
    assert next(root.iter("DATE")).text == "20260315"


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
