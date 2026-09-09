"""Merge differences are audit metadata, not reasons for human approval."""
import unittest
from copy import deepcopy

from backend.hitl_status import calculate_hitl_flag, calculate_status_from_hitl


class MergeConflictStatusTests(unittest.TestCase):
    def invoice(self):
        return {
            "invoice_date": "2026-09-09", "po_id": "PO-1",
            "term_to_pay": "30 days", "total_amount": 10,
            "line_items": [{"service": "Item", "quantity": 1,
                            "price_per_unit": 10, "amount": 10}],
            "additional_fields": {
                "HITL": True, "status": 1,
                "hitl_remark": "Merged pages conflict: seller",
                "hitl_remarks": ["Merged pages conflict: seller"],
                "merge_conflicts": [{"path": "seller", "canonical": "A", "incoming": "B"}],
            },
        }

    def test_recalculation_clears_old_reason_and_pending_status_preserving_metadata(self):
        for human_processed in (False, True):
            with self.subTest(human_processed=human_processed):
                invoice = self.invoice()
                conflicts = deepcopy(invoice["additional_fields"]["merge_conflicts"])
                hitl = calculate_hitl_flag(invoice, run_erp_matching_now=False)
                self.assertFalse(hitl)
                self.assertEqual(calculate_status_from_hitl(hitl_value=hitl, human_processed=human_processed),
                                 2 if human_processed else 0)
                self.assertEqual(invoice["additional_fields"]["hitl_remarks"], [])
                self.assertEqual(invoice["additional_fields"]["hitl_remark"], "")
                self.assertEqual(invoice["additional_fields"]["merge_conflicts"], conflicts)

    def test_other_validation_failures_still_require_hitl(self):
        invoice = self.invoice()
        invoice.pop("invoice_date")
        invoice["additional_fields"]["erp_hitl_reasons"] = ["Vendor not matched"]
        self.assertTrue(calculate_hitl_flag(invoice, run_erp_matching_now=False))
        self.assertEqual(invoice["additional_fields"]["hitl_remarks"],
                         ["Invoice date is missing", "Vendor not matched"])

    def test_merge_conflicts_do_not_revoke_human_approval(self):
        invoice = self.invoice()
        invoice["additional_fields"]["human_approved"] = True
        self.assertFalse(calculate_hitl_flag(invoice, run_erp_matching_now=False))
        self.assertTrue(invoice["additional_fields"]["human_approved"])


if __name__ == "__main__":
    unittest.main()
