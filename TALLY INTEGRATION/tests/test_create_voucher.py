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
    [{"match_type": "STOCK_ITEM", "erp_item_name": "Waltr A Monthly Subscribe", "quantity": 28,
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
        "1179.00", [{"match_type": "STOCK_ITEM", "service": "Widget", "quantity": 1, "price_per_unit": 1000, "unit": "Nos"}],
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
        "11391.00", [{"match_type": "STOCK_ITEM", "service": "Waltr", "quantity": 28, "price_per_unit": 499.00, "unit": "Nos"}],
        {"discount": 4319, "igst_rate": 18, "igst_amount": 1737.54,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))


def test_multi_line_batch_allocations_mirror_their_own_line():
    """Each BATCHALLOCATIONS amount must equal ITS line, not the running total."""
    root = _assert_balanced(_invoice(
        "4720.00",
        [{"match_type": "STOCK_ITEM", "service": "Item A", "quantity": 10, "price_per_unit": 100, "unit": "Nos"},
         {"match_type": "STOCK_ITEM", "service": "Item B", "quantity": 5, "price_per_unit": 200, "unit": "Nos"},
         {"match_type": "STOCK_ITEM", "service": "Item C", "quantity": 2, "price_per_unit": 1000, "unit": "Nos"}],
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
        "1180.00", [{"match_type": "STOCK_ITEM", "service": "Widget", "quantity": 1, "price_per_unit": 1000, "unit": "Nos"}],
        {"cgst_rate": 9, "sgst_rate": 9, "cgst_amount": 90.00, "sgst_amount": 90.00,
         "seller_gstin": "29AAA", "buyer_gstin": "29BBB"},
    ))
    names = {e.find("LEDGERNAME").text for e in root.iter("LEDGERENTRIES.LIST")}
    assert {"CGST", "SGST"} <= names, names


def test_igst_amount_is_used_when_gstin_state_codes_are_missing():
    """Invoice 337: explicit IGST must not be discarded just because GSTIN OCR is blank."""
    root = _assert_balanced(_invoice(
        "33251.00",
        [{
            "match_type": "LEDGER",
            "matched_name": "Repairs & Maintenance- Factory",
            "amount": "28179.20",
        }],
        {
            "igst_rate": "18%",
            "igst_amount": "5072.26",
            "round_off": "-0.46",
        },
        invoice_number="337",
        po_id="Not applicable",
    ))
    entries = {
        e.findtext("LEDGERNAME"): float(e.findtext("AMOUNT"))
        for e in root.iter("LEDGERENTRIES.LIST")
    }
    assert entries["IGST"] == -5072.26
    assert entries["Round Off"] == 0.46


def test_invoice_with_no_rounding_needed():
    root = _assert_balanced(_invoice(
        "1000.00", [{"match_type": "STOCK_ITEM", "service": "Widget", "quantity": 4, "price_per_unit": 250, "unit": "Nos"}]))
    names = {e.find("LEDGERNAME").text for e in root.iter("LEDGERENTRIES.LIST")}
    assert "Round Off" not in names, names


def test_messy_extracted_values_are_parsed():
    """Gemini returns strings, sometimes with separators or a percent sign."""
    _assert_balanced(_invoice(
        "11,391.00", [{"match_type": "STOCK_ITEM", "service": "Waltr", "quantity": "28", "price_per_unit": "499.00", "unit": "Nos"}],
        {"discount": "4,319.00", "igst_rate": "18%", "igst_amount": "1,737.54",
         "round_off": "0.46", "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))


def test_large_gap_is_refused_not_plugged():
    """A gap too big to be a rounding artefact means the extraction is wrong.
    Pushing it would create a plausible-looking but incorrect voucher in Tally."""
    result, payload = _build(_invoice(
        "9999.00", [{"match_type": "STOCK_ITEM", "service": "Widget", "quantity": 1, "price_per_unit": 1000, "unit": "Nos"}],
        {"igst_rate": 18, "igst_amount": 180.00,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))
    assert not result["success"]
    assert payload is None, "nothing should have been sent to Tally"
    assert "does not reconcile" in result["error_reason"]


def test_voucher_uses_the_real_invoice_date():
    """The voucher date must come from the extracted invoice_date field."""
    _, root = _build(_invoice(
        "1000.00", [{"match_type": "STOCK_ITEM", "service": "Widget", "quantity": 4, "price_per_unit": 250, "unit": "Nos"}],
        invoice_date="2026-03-15",
    ))
    assert next(root.iter("DATE")).text == "20260315"


def _ledger_entries(root):
    return list(root.iter("LEDGERENTRIES.LIST"))


def _party_ledger_entry(root, party_name):
    for entry in _ledger_entries(root):
        name_el = entry.find("LEDGERNAME")
        if name_el is not None and name_el.text == party_name:
            flag = entry.find("ISPARTYLEDGER")
            if flag is not None and flag.text == "Yes":
                return entry
    return None


def test_ispartyledger_on_all_ledger_entries():
    """Party entry is Yes; expense/tax/round-off entries are No."""
    root = _assert_balanced(_invoice(
        "33251.46",
        [
            {
                "match_type": "STOCK_ITEM",
                "matched_name": "H.D.P.E 1L Oil Bottle",
                "original_quantity": 1280,
                "original_rate": 11.90,
                "original_amount": 15232.00,
                "unit": "Nos",
            },
            {
                "match_type": "LEDGER",
                "matched_name": "Repairs & Maintanance- Factory",
                "original_amount": 12947.20,
            },
        ],
        {
            "igst_rate": 18,
            "igst_amount": 5072.26,
            "seller_gstin": "29ABCDE1234F1Z5",
            "buyer_gstin": "36ABCDE1234F1Z5",
        },
        po_id="",
    ))
    for entry in _ledger_entries(root):
        flag = entry.find("ISPARTYLEDGER")
        assert flag is not None, "every LEDGERENTRIES.LIST must include ISPARTYLEDGER"
        assert flag.text in ("Yes", "No"), flag.text

    party = _party_ledger_entry(root, "Test Vendor")
    assert party is not None
    assert party.find("ISPARTYLEDGER").text == "Yes"

    expense = next(
        e for e in _ledger_entries(root)
        if e.findtext("LEDGERNAME") == "Repairs & Maintanance- Factory"
    )
    assert expense.find("ISPARTYLEDGER").text == "No"

    igst = next(e for e in _ledger_entries(root) if e.findtext("LEDGERNAME") == "IGST")
    assert igst.find("ISPARTYLEDGER").text == "No"


def test_bill_allocation_uses_name_tag_not_n():
    """Bill reference must serialize as <NAME>, not a corrupted tag from str.format()."""
    _, root = _build(_invoice(
        "1000.00",
        [{"match_type": "STOCK_ITEM", "matched_name": "Widget", "original_quantity": 4,
          "original_rate": 250, "original_amount": 1000.00, "unit": "Nos"}],
        invoice_number="337",
        po_id="",
    ))
    payload = _captured["payload"]
    assert "<NAME>337</NAME>" in payload
    assert "<n>337</n>" not in payload

    party = _party_ledger_entry(root, "Test Vendor")
    bill = party.find("BILLALLOCATIONS.LIST")
    assert bill is not None
    name_el = bill.find("NAME")
    assert name_el is not None, "BILLALLOCATIONS must use a NAME child element"
    assert name_el.text == "337"


def test_pure_stock_purchase_invoice():
    """All STOCK_ITEM lines → only ALLINVENTORYENTRIES, no expense LEDGERENTRIES."""
    root = _assert_balanced(_invoice(
        "1180.00",
        [{
            "match_type": "STOCK_ITEM",
            "matched_name": "Widget A",
            "original_quantity": 10,
            "original_rate": 100.00,
            "original_amount": 1000.00,
            "unit": "Nos",
            "hsn_number": "84713010",
        }],
        {"igst_rate": 18, "igst_amount": 180.00,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))
    assert len(list(root.iter("ALLINVENTORYENTRIES.LIST"))) == 1
    inv = next(root.iter("ALLINVENTORYENTRIES.LIST"))
    assert inv.find("STOCKITEMNAME").text == "Widget A"
    assert inv.find("ACTUALQTY").text == "10 Nos"
    assert inv.find("RATE").text == "100.00/Nos"
    assert inv.find("GSTHSNNAME").text == "84713010"
    assert inv.find("GSTOVRDNTYPEOFSUPPLY").text == "Goods"
    expense_ledgers = [
        e for e in root.iter("LEDGERENTRIES.LIST")
        if e.find("GSTSOURCETYPE") is not None and e.find("GSTSOURCETYPE").text == "Ledger"
    ]
    assert expense_ledgers == []


def test_pure_ledger_purchase_invoice():
    """All LEDGER lines → LEDGERENTRIES with GST metadata, no inventory."""
    root = _assert_balanced(_invoice(
        "1180.00",
        [{
            "match_type": "LEDGER",
            "matched_name": "Repairs & Maintanance- Factory",
            "original_amount": 1000.00,
            "hsn_number": "998719",
        }],
        {"igst_rate": 18, "igst_amount": 180.00,
         "seller_gstin": "29AAA", "buyer_gstin": "36AAA"},
    ))
    assert len(list(root.iter("ALLINVENTORYENTRIES.LIST"))) == 0
    assert next(root.iter("VCHENTRYMODE")).text == "Accounting Invoice"
    expense = [
        e for e in root.iter("LEDGERENTRIES.LIST")
        if e.find("GSTSOURCETYPE") is not None
    ]
    assert len(expense) == 1
    entry = expense[0]
    assert entry.find("LEDGERNAME").text == "Repairs & Maintanance- Factory"
    assert entry.find("GSTSOURCETYPE").text == "Ledger"
    assert entry.find("GSTOVRDNTYPEOFSUPPLY").text == "Services"
    assert entry.find("VATEXPAMOUNT").text == "-1000.00"
    assert entry.find("AMOUNT").text == "-1000.00"


def test_accounting_invoice_emits_party_before_expense_ledgers():
    """Tally uses the first accounting entry as the invoice party/counterparty."""
    root = _assert_balanced(_invoice(
        "1180.00",
        [{
            "match_type": "LEDGER",
            "matched_name": "Factory expenses",
            "amount": "1000.00",
        }],
        {
            "igst_rate": 18,
            "igst_amount": 180,
            "seller_gstin": "29AAA",
            "buyer_gstin": "36AAA",
        },
    ))
    voucher = next(root.iter("VOUCHER"))
    entries = [child for child in voucher if child.tag == "LEDGERENTRIES.LIST"]
    assert [entry.findtext("LEDGERNAME") for entry in entries[:2]] == [
        "Test Vendor",
        "Factory expenses",
    ]
    assert entries[0].findtext("ISPARTYLEDGER") == "Yes"
    assert entries[0].findtext("ISLASTDEEMEDPOSITIVE") == "No"


def test_ledger_line_needs_amount_but_not_quantity_or_rate():
    """A service/expense purchase is an accounting line, not inventory."""
    result, payload = _build(_invoice(
        "1000.00",
        [{
            "match_type": "LEDGER",
            "matched_name": "Factory expenses",
            "quantity": "",
            "price_per_unit": "",
            "amount": "",
        }],
    ))
    assert result.get("success") is False
    assert "amount" in (result.get("error_reason") or "").lower()
    assert payload is None


def test_stock_line_needs_positive_quantity_and_rate():
    result, payload = _build(_invoice(
        "1000.00",
        [{
            "match_type": "STOCK_ITEM",
            "matched_name": "Widget",
            "quantity": "",
            "price_per_unit": "",
            "amount": "1000.00",
            "unit": "Nos",
        }],
    ))
    assert result.get("success") is False
    reason = (result.get("error_reason") or "").lower()
    assert "quantity" in reason
    assert "rate" in reason
    assert payload is None


def test_editor_corrected_values_override_stale_ocr_snapshots():
    root = _assert_balanced(_invoice(
        "1180.00",
        [{
            "match_type": "LEDGER",
            "matched_name": "Factory expenses",
            "original_name": "Old OCR description",
            "original_amount": "900.00",
            "service": "Factory expenses",
            "amount": "1000.00",
            "tax_rate": "18",
        }],
        {
            "igst_rate": 18,
            "igst_amount": 180,
            "seller_gstin": "29AAA",
            "buyer_gstin": "36AAA",
        },
    ))
    entry = next(
        e for e in root.iter("LEDGERENTRIES.LIST")
        if e.findtext("LEDGERNAME") == "Factory expenses"
    )
    assert entry.findtext("RATE") == "18.00"
    assert entry.findtext("AMOUNT") == "-1000.00"


def test_mixed_stock_ledger_with_gst_reference_case():
    """User reference case: H.D.P.E stock + Repairs ledger + IGST."""
    root = _assert_balanced(_invoice(
        "33251.46",
        [
            {
                "match_type": "STOCK_ITEM",
                "original_name": "H.D.P.E 1L Oil Bottle",
                "matched_name": "H.D.P.E 1L Oil Bottle",
                "original_quantity": 1280,
                "original_rate": 11.90,
                "original_amount": 15232.00,
                "unit": "Nos",
                "hsn_number": "39233090",
            },
            {
                "match_type": "LEDGER",
                "original_name": "Repairs & Maintanance- Factory",
                "matched_name": "Repairs & Maintanance- Factory",
                "original_amount": 12947.20,
                "hsn_number": "998719",
            },
        ],
        {
            "igst_rate": 18,
            "igst_amount": 5072.26,
            "seller_gstin": "29ABCDE1234F1Z5",
            "buyer_gstin": "36ABCDE1234F1Z5",
        },
        invoice_number="MIXED-TEST-001",
        po_id="",
    ))
    inventory = list(root.iter("ALLINVENTORYENTRIES.LIST"))
    assert next(root.iter("VCHENTRYMODE")).text == "Item Invoice"
    assert len(inventory) == 1
    stock = inventory[0]
    assert stock.find("STOCKITEMNAME").text == "H.D.P.E 1L Oil Bottle"
    assert stock.find("ACTUALQTY").text == "1280 Nos"
    assert stock.find("BILLEDQTY").text == "1280 Nos"
    assert stock.find("RATE").text == "11.90/Nos"
    assert float(stock.find("AMOUNT").text) == -15232.00
    assert stock.find("GSTOVRDNTYPEOFSUPPLY").text == "Goods"

    expense = [
        e for e in root.iter("LEDGERENTRIES.LIST")
        if e.find("GSTSOURCETYPE") is not None and e.find("GSTSOURCETYPE").text == "Ledger"
    ]
    assert len(expense) == 1
    ledger = expense[0]
    assert ledger.find("LEDGERNAME").text == "Repairs & Maintanance- Factory"
    assert ledger.find("GSTOVRDNTYPEOFSUPPLY").text == "Services"
    assert float(ledger.find("VATEXPAMOUNT").text) == -12947.20

    tax_entries = {
        e.find("LEDGERNAME").text: float(e.find("AMOUNT").text)
        for e in root.iter("LEDGERENTRIES.LIST")
        if e.find("LEDGERNAME") is not None and e.find("LEDGERNAME").text == "IGST"
    }
    assert tax_entries["IGST"] == -5072.26


def test_xml_special_characters_are_escaped():
    """Ampersands in ledger names must be valid XML."""
    _, root = _build(_invoice(
        "500.00",
        [{
            "match_type": "LEDGER",
            "matched_name": "Repair & Maintenance",
            "original_amount": 500.00,
        }],
        po_id="",
    ))
    payload = _captured["payload"]
    assert "Repair &amp; Maintenance" in payload
    entry = next(
        e for e in root.iter("LEDGERENTRIES.LIST")
        if e.find("LEDGERNAME") is not None and e.find("LEDGERNAME").text == "Repair & Maintenance"
    )
    assert entry is not None


def _child_tags(element):
    return {child.tag for child in element}


def test_mixed_voucher_matches_manual_fixture_structure():
    """Generated mixed voucher includes tags present in manual Tally export."""
    fixture_path = Path(__file__).parent / "fixtures" / "manual_mixed_purchase_voucher.xml"
    fixture_root = ET.parse(fixture_path).getroot()
    fixture_voucher = next(fixture_root.iter("VOUCHER"))

    data = _invoice(
        "33251.46",
        [
            {
                "match_type": "STOCK_ITEM",
                "matched_name": "H.D.P.E 1L Oil Bottle",
                "original_quantity": 1280,
                "original_rate": 11.90,
                "original_amount": 15232.00,
                "unit": "Nos",
                "hsn_number": "39233090",
            },
            {
                "match_type": "LEDGER",
                "matched_name": "Repairs & Maintanance- Factory",
                "original_amount": 12947.20,
                "hsn_number": "998719",
            },
        ],
        {
            "igst_rate": 18,
            "igst_amount": 5072.26,
            "seller_gstin": "29ABCDE1234F1Z5",
            "buyer_gstin": "36ABCDE1234F1Z5",
        },
        invoice_number="MIXED-TEST-001",
        po_id="",
    )
    data["gemini"]["json"]["purchase_ledger"] = "Purchase -Local"
    root = _assert_balanced(data)
    generated_voucher = next(root.iter("VOUCHER"))

    fixture_stock = next(fixture_voucher.iter("ALLINVENTORYENTRIES.LIST"))
    gen_stock = next(generated_voucher.iter("ALLINVENTORYENTRIES.LIST"))
    required_stock_tags = {
        "STOCKITEMNAME", "ISDEEMEDPOSITIVE", "RATE", "ACTUALQTY", "BILLEDQTY",
        "AMOUNT", "GSTSOURCETYPE", "GSTITEMSOURCE", "HSNSOURCETYPE", "HSNITEMSOURCE",
        "GSTOVRDNTYPEOFSUPPLY", "GSTHSNNAME", "ACCOUNTINGALLOCATIONS.LIST",
    }
    assert required_stock_tags <= _child_tags(gen_stock)

    fixture_expense = [
        e for e in fixture_voucher.iter("LEDGERENTRIES.LIST")
        if e.find("GSTSOURCETYPE") is not None
    ][0]
    gen_expense = [
        e for e in generated_voucher.iter("LEDGERENTRIES.LIST")
        if e.find("GSTSOURCETYPE") is not None and e.find("GSTSOURCETYPE").text == "Ledger"
    ][0]
    required_ledger_tags = {
        "LEDGERNAME", "ISPARTYLEDGER", "GSTSOURCETYPE", "GSTLEDGERSOURCE", "HSNSOURCETYPE",
        "HSNLEDGERSOURCE", "GSTOVRDNTYPEOFSUPPLY", "GSTHSNNAME",
        "ISDEEMEDPOSITIVE", "VATEXPAMOUNT", "AMOUNT",
    }
    assert required_ledger_tags <= _child_tags(gen_expense)
    assert _child_tags(gen_expense) == _child_tags(fixture_expense)


def test_mixed_stock_and_ledger_lines():
    """Stock lines use ALLINVENTORYENTRIES; expense lines use LEDGERENTRIES."""
    root = _assert_balanced(_invoice(
        "1500.00",
        [
            {
                "match_type": "STOCK_ITEM",
                "original_name": "H.D.P.E 1L Oil Bottle",
                "matched_name": "H.D.P.E 1L Oil Bottle",
                "erp_item_name": "H.D.P.E 1L Oil Bottle",
                "original_quantity": 100,
                "original_rate": 10.00,
                "original_amount": 1000.00,
                "quantity": 100,
                "price_per_unit": 10.00,
                "unit": "Nos",
            },
            {
                "match_type": "LEDGER",
                "original_name": "Repair & Maintenance - Factory",
                "matched_name": "Repair & Maintenance , Installation",
                "erp_ledger_name": "Repair & Maintenance , Installation",
                "original_amount": 500.00,
                "amount": 500.00,
            },
        ],
        po_id="",
    ))
    inventory = list(root.iter("ALLINVENTORYENTRIES.LIST"))
    assert len(inventory) == 1
    assert inventory[0].find("STOCKITEMNAME").text == "H.D.P.E 1L Oil Bottle"

    ledger_names = [
        e.find("LEDGERNAME").text
        for e in root.iter("LEDGERENTRIES.LIST")
    ]
    assert "Repair & Maintenance , Installation" in ledger_names


def test_unmatched_lines_block_voucher_build():
    result, payload = _build(_invoice(
        "1000.00",
        [
            {"match_type": "UNMATCHED", "original_name": "Unknown Service", "amount": 1000.00},
        ],
    ))
    assert not result["success"]
    assert payload is None
    assert "unmatched line item" in (result.get("error_reason") or "").lower()


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
