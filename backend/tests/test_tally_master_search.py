from unittest.mock import patch

from backend import tally_master_db


def test_stock_search_ranks_exact_then_prefix_and_preserves_tally_spelling():
    rows = [
        {"item_name": "Red Bolt", "item_id": "3"},
        {"item_name": "Bolt Large", "units": "Nos", "category": "Hardware"},
        {"item_name": "Bolt", "item_id": "1"},
        {"item_name": "Nut"},
    ]
    with patch.object(tally_master_db, "fetch_item_master", return_value=rows):
        result = tally_master_db.search_master("items", "BOLT", 2)
    assert [row["name"] for row in result] == ["Bolt", "Bolt Large"]
    assert result[1]["detail"] == "Nos · Hardware"


def test_vendor_search_uses_gst_and_does_not_return_private_fields():
    rows = [{"vendor_name": "ACME & Co.", "vendor_id": "v1",
             "gst_tax_number": "GST123", "bank_account": "private"}]
    with patch.object(tally_master_db, "fetch_vendor_master", return_value=rows):
        result = tally_master_db.search_master("vendors", "gst123")
    assert result == [{"id": "v1", "name": "ACME & Co.", "detail": "GST123"}]


def test_empty_query_browses_and_literal_query_does_not_act_as_regex():
    rows = [{"item_name": "Zinc"}, {"item_name": "A.*"}, {"item_name": ""}]
    with patch.object(tally_master_db, "fetch_item_master", return_value=rows):
        assert len(tally_master_db.search_master("items", "")) == 2
        assert [r["name"] for r in tally_master_db.search_master("items", ".*")] == ["A.*"]


def test_word_search_ranks_names_and_matches_partial_reordered_words():
    names = ["Oil Engine Lubricant", "3 Litre Bottle Lubricant Oil",
             "Lubricant Engine Oil", "1 Ltr Lubricant Oil", "Lubricant Oil", "Engine Oil"]
    with patch.object(tally_master_db, "fetch_item_master", return_value=[{"item_name": n} for n in names]):
        result = tally_master_db.search_master("items", "  LUBRICANT, oil  ")
        assert [r["name"] for r in result] == ["Lubricant Oil", "1 Ltr Lubricant Oil",
            "3 Litre Bottle Lubricant Oil", "Lubricant Engine Oil", "Oil Engine Lubricant"]
        assert len(tally_master_db.search_master("items", "oil lub")) == 5


def test_ledger_and_vendor_word_search_preserves_original_names():
    with patch.object(tally_master_db, "fetch_expense_ledger_master", return_value=[
        {"ledger_id": "L1", "ledger_name": "Factory Machinery Repair Expenses"},
        {"ledger_id": "L2", "ledger_name": "Travel Expenses"},
    ]):
        assert tally_master_db.search_master("ledgers", "repair fact") == [
            {"id": "L1", "name": "Factory Machinery Repair Expenses", "detail": ""}]
    with patch.object(tally_master_db, "fetch_vendor_master", return_value=[
        {"vendor_id": "V1", "vendor_name": "Acme Oil & Lubricant Co."},
    ]):
        assert tally_master_db.search_master("vendors", "lub acme")[0]["name"] == "Acme Oil & Lubricant Co."
