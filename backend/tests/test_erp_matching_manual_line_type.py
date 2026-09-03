from unittest.mock import patch

from backend.erp_matching import _match_items_into


def _match(name: str, kind: str) -> dict:
    if kind == "stock":
        return {
            "matched": True,
            "score": 100.0,
            "item_id": "stock-1",
            "item_name": name,
            "units": "Nos",
            "category": "Primary",
        }
    return {
        "matched": True,
        "score": 100.0,
        "ledger_id": "ledger-1",
        "ledger_name": name,
    }


def test_manual_ledger_selection_does_not_get_overwritten_by_stock_match():
    data = {"line_items": [{
        "service": "Factory expenses",
        "amount": "5072.26",
        "match_type": "LEDGER",
        "match_type_source": "MANUAL",
    }]}

    with (
        patch("backend.erp_matching.match_item", return_value=_match("Factory expenses", "stock")),
        patch("backend.erp_matching.match_expense_ledger", return_value=_match("Factory expenses", "ledger")),
    ):
        reasons = _match_items_into(data)

    line = data["line_items"][0]
    assert reasons == []
    assert line["match_type"] == "LEDGER"
    assert line["matched_name"] == "Factory expenses"
    assert line["erp_ledger_name"] == "Factory expenses"
    assert line["erp_item_name"] is None


def test_manual_stock_selection_does_not_fall_back_to_ledger_match():
    data = {"line_items": [{
        "service": "Widget",
        "quantity": "1",
        "price_per_unit": "1000",
        "amount": "1000",
        "match_type": "STOCK_ITEM",
        "match_type_source": "MANUAL",
    }]}
    no_stock = {"matched": False, "score": 20.0, "best_candidate_name": "Other item"}

    with (
        patch("backend.erp_matching.match_item", return_value=no_stock),
        patch("backend.erp_matching.match_expense_ledger", return_value=_match("Widget", "ledger")),
    ):
        reasons = _match_items_into(data)

    line = data["line_items"][0]
    assert line["match_type"] == "UNMATCHED"
    assert line["erp_ledger_name"] is None
    assert reasons and "stock" in reasons[0].lower()
