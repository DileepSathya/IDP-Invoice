"""Regenerate tests/fixtures/generated_mixed_purchase_voucher.xml from the reference case."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tally import create_voucher as cv  # noqa: E402
from tally.configurations.config import clean_tally_xml, convert_date_yyyymmdd  # noqa: E402
from tally.voucher_xml import build_ledger_entry_block, elements_to_fragment  # noqa: E402

TEMPLATE = BASE / "xml_scripts" / "create_voucher.xml"
OUTPUT = Path(__file__).resolve().parent / "fixtures" / "generated_mixed_purchase_voucher.xml"


def main() -> int:
    data = {
        "gemini": {
            "json": {
                "invoice_number": "MIXED-TEST-001",
                "invoice_date": "1-Apr-26",
                "total_amount": "33251.46",
                "purchase_ledger": "Purchase -Local",
                "line_items": [
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
                "additional_fields": {
                    "igst_rate": 18,
                    "igst_amount": 5072.26,
                    "seller_gstin": "29ABCDE1234F1Z5",
                    "buyer_gstin": "36ABCDE1234F1Z5",
                },
                "po_id": "",
            }
        }
    }

    inv_xml, exp_xml, taxable = cv.line_entries_xml(data)
    tax_xml = cv.tax_entries_xml(data, taxable)
    party_xml = elements_to_fragment(
        [
            build_ledger_entry_block(
                "Test Vendor",
                cv._num("33251.46"),
                "Cr",
                bill_ref="MIXED-TEST-001",
                is_party_ledger=True,
            )
        ],
        indent=" " * 6,
    )

    template = TEMPLATE.read_text(encoding="utf-8")
    payload = clean_tally_xml(
        template.format(
            COMPANY_NAME="Reference Company",
            INVOICE_NUMBER="MIXED-TEST-001",
            VOUCHER_TYPE="Purchase",
            VOUCHER_DATE=convert_date_yyyymmdd("1-Apr-26"),
            PARTY_LEDGER="Test Vendor",
            INVENTORY_ENTRIES_XML=inv_xml,
            EXPENSE_LEDGER_ENTRIES_XML=exp_xml,
            TAX_ENTRIES_XML=tax_xml,
            PARTY_LEDGER_ENTRY_XML=party_xml,
        )
    )

    root = ET.fromstring(payload)
    imbalance = cv._voucher_imbalance(root)
    if imbalance is None or abs(imbalance) > cv.BALANCE_EPSILON:
        raise SystemExit(f"fixture voucher out of balance: {imbalance}")

    OUTPUT.write_text(payload, encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
