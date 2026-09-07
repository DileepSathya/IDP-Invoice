"""Regression tests for merged-invoice data returned to the dashboard."""

from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from bson import ObjectId

from backend.api import list_invoices


class _InvoiceCollection:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = deepcopy(docs)

    def find(self, *args, **kwargs):
        query = args[0] if args else {}
        docs = sorted(self.docs, key=lambda doc: doc["_id"], reverse=True)
        if query.get("merged_into") == {"$exists": False}:
            docs = [doc for doc in docs if "merged_into" not in doc]
        limit = kwargs.get("limit", 0)
        if limit:
            docs = docs[:limit]
        return iter(deepcopy(docs))

    def update_one(self, *args, **kwargs) -> None:
        del args, kwargs


class MergedInvoiceListTests(unittest.TestCase):
    @patch("backend.api._sync_hitl_and_status", return_value=(True, 1))
    @patch("backend.erp_settings.get_erp_sync_settings", return_value={"mode": "immediate"})
    @patch("backend.api.get_invoices_collection")
    def test_list_rows_include_all_merged_source_files(
        self,
        get_collection,
        _get_settings,
        _sync_status,
    ) -> None:
        invoice_id = ObjectId()
        source_files = [
            r"D:\invoices_data\HITL_pending\page-1.png",
            r"D:\invoices_data\merged_sources\page-2.png",
        ]
        get_collection.return_value = _InvoiceCollection(
            [
                {
                    "_id": invoice_id,
                    "file_path": source_files[0],
                    "uploaded_file_path": source_files[0],
                    "source_files": source_files,
                    "file_status": "healthy file",
                    "gemini": {
                        "json": {
                            "invoice_number": "INV-100",
                            "total_amount": 30,
                            "line_items": [
                                {"service": "First", "amount": 10},
                                {"service": "Second", "amount": 20},
                            ],
                            "additional_fields": {"status": 1, "HITL": True},
                        }
                    },
                }
            ]
        )

        response = list_invoices()

        self.assertEqual(len(response.items), 2)
        self.assertEqual(response.items[0].source_files, source_files)
        self.assertEqual(response.items[1].source_files, source_files)

    @patch("backend.api._sync_hitl_and_status", return_value=(True, 1))
    @patch("backend.erp_settings.get_erp_sync_settings", return_value={"mode": "immediate"})
    @patch("backend.api.get_invoices_collection")
    def test_limit_is_applied_after_merged_away_invoices_are_excluded(
        self,
        get_collection,
        _get_settings,
        _sync_status,
    ) -> None:
        canonical_id = ObjectId("000000000000000000000001")
        merged_id = ObjectId("000000000000000000000002")
        canonical = {
            "_id": canonical_id,
            "uploaded_file_path": r"D:\invoices_data\HITL_pending\canonical.png",
            "file_status": "healthy file",
            "gemini": {
                "json": {
                    "invoice_number": "INV-200",
                    "total_amount": 10,
                    "line_items": [{"service": "Item", "amount": 10}],
                    "additional_fields": {"status": 1, "HITL": True},
                }
            },
        }
        merged_away = deepcopy(canonical)
        merged_away.update({"_id": merged_id, "merged_into": str(canonical_id)})
        get_collection.return_value = _InvoiceCollection([canonical, merged_away])

        response = list_invoices(limit=1)

        self.assertEqual([item.id for item in response.items], [str(canonical_id)])


if __name__ == "__main__":
    unittest.main()
