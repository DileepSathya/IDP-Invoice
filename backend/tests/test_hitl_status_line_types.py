from backend.hitl_status import evaluate_line_items


def test_ledger_line_does_not_require_quantity_or_rate():
    flagged, reasons = evaluate_line_items([
        {
            "match_type": "LEDGER",
            "service": "Factory expenses",
            "quantity": "",
            "price_per_unit": "",
            "amount": "5072.26",
        }
    ])

    assert flagged is False
    assert reasons == []


def test_ledger_line_requires_positive_amount():
    flagged, reasons = evaluate_line_items([
        {
            "match_type": "LEDGER",
            "service": "Factory expenses",
            "amount": "",
        }
    ])

    assert flagged is True
    assert reasons == ["Item 1 (Factory expenses): amount missing"]


def test_stock_line_still_requires_quantity_and_rate():
    flagged, reasons = evaluate_line_items([
        {
            "match_type": "STOCK_ITEM",
            "service": "Widget",
            "quantity": "",
            "price_per_unit": "",
            "amount": "1000.00",
        }
    ])

    assert flagged is True
    assert reasons == ["Item 1 (Widget): missing quantity & rate"]
