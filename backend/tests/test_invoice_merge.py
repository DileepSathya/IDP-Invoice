"""Tests for HITL duplicate invoice merge rules."""

from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import patch

from bson import ObjectId

from backend.invoice_merge import (
    _has_older_processed_sibling,
    invoice_number_from_doc,
    merge_hitl_duplicate_invoices,
    normalize_invoice_number,
)


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = {doc["_id"]: deepcopy(doc) for doc in docs}

    def find(self, query: dict[str, Any] | None = None, **kwargs: Any):
        del kwargs
        for doc in self._docs.values():
            if query and query.get("merged_into") == {"$exists": False} and doc.get("merged_into"):
                continue
            yield deepcopy(doc)

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        if "_id" in query:
            doc = self._docs.get(query["_id"])
            return deepcopy(doc) if doc else None
        return None

    def update_one(self, query: dict[str, Any], update: dict[str, Any], **kwargs: Any) -> None:
        del kwargs
        doc_id = query["_id"]
        doc = self._docs.get(doc_id)
        if not doc:
            return
        for key, value in update.get("$set", {}).items():
            if key == "gemini.json":
                doc.setdefault("gemini", {})["json"] = value
            else:
                doc[key] = value


def _make_doc(
    *,
    invoice_number: str,
    status: int,
    line_items: list[dict[str, Any]] | None = None,
    doc_id: ObjectId | None = None,
) -> dict[str, Any]:
    oid = doc_id or ObjectId()
    return {
        "_id": oid,
        "file_path": f"/files/{oid}.pdf",
        "uploaded_file_path": f"/files/{oid}.pdf",
        "ocr_text": f"ocr-{oid}",
        "gemini": {
            "json": {
                "invoice_number": invoice_number,
                "line_items": line_items
                or [{"service": "Item", "quantity": 1, "price_per_unit": 10, "amount": 10}],
                "total_amount": 10,
                "additional_fields": {"status": status, "HITL": status == 1},
            }
        },
    }


class InvoiceMergeTests(unittest.TestCase):
    def test_normalize_invoice_number_case_insensitive(self) -> None:
        self.assertEqual(normalize_invoice_number("INV-001"), "inv-001")
        self.assertEqual(normalize_invoice_number("  inv-001  "), "inv-001")

    def test_invoice_number_from_doc(self) -> None:
        doc = _make_doc(invoice_number="INV-001", status=1)
        self.assertEqual(invoice_number_from_doc(doc), "inv-001")

    def test_has_older_processed_sibling(self) -> None:
        older = _make_doc(invoice_number="inv-001", status=0)
        canonical = _make_doc(invoice_number="inv-001", status=1)
        group = sorted([older, canonical], key=lambda d: d["_id"])
        self.assertTrue(_has_older_processed_sibling(group, canonical["_id"]))

    @patch("backend.erp_settings.should_merge_hitl_duplicates", return_value=True)
    def test_merge_two_hitl_pending_same_invoice(self, _mock: Any) -> None:
        doc_a = _make_doc(
            invoice_number="inv-001",
            status=1,
            line_items=[{"service": "A", "quantity": 1, "price_per_unit": 5, "amount": 5}],
        )
        doc_b = _make_doc(
            invoice_number="INV-001",
            status=1,
            line_items=[{"service": "B", "quantity": 2, "price_per_unit": 3, "amount": 6}],
        )
        if doc_a["_id"] > doc_b["_id"]:
            doc_a, doc_b = doc_b, doc_a
        coll = _FakeCollection([doc_a, doc_b])

        result = merge_hitl_duplicate_invoices(coll)

        self.assertEqual(result["groups_found"], 1)
        self.assertEqual(result["docs_merged"], 1)
        self.assertEqual(len(coll._docs[doc_a["_id"]]["gemini"]["json"]["line_items"]), 2)
        self.assertEqual(coll._docs[doc_b["_id"]].get("merged_into"), str(doc_a["_id"]))

    @patch("backend.erp_settings.should_merge_hitl_duplicates", return_value=True)
    def test_no_merge_when_older_system_processed(self, _mock: Any) -> None:
        doc_a = _make_doc(invoice_number="inv-001", status=0)
        doc_b = _make_doc(invoice_number="inv-001", status=1)
        if doc_a["_id"] > doc_b["_id"]:
            doc_a, doc_b = doc_b, doc_a
            doc_a["gemini"]["json"]["additional_fields"] = {"status": 0, "HITL": False}
            doc_b["gemini"]["json"]["additional_fields"] = {"status": 1, "HITL": True}

        coll = _FakeCollection([doc_a, doc_b])
        result = merge_hitl_duplicate_invoices(coll)

        self.assertEqual(result["docs_merged"], 0)
        self.assertNotIn("merged_into", coll._docs[doc_b["_id"]])

    @patch("backend.erp_settings.should_merge_hitl_duplicates", return_value=False)
    def test_merge_disabled(self, _mock: Any) -> None:
        doc_a = _make_doc(invoice_number="inv-001", status=1)
        doc_b = _make_doc(invoice_number="inv-001", status=1)
        coll = _FakeCollection([doc_a, doc_b])

        result = merge_hitl_duplicate_invoices(coll)
        self.assertEqual(result, {"groups_found": 0, "docs_merged": 0})


if __name__ == "__main__":
    unittest.main()
