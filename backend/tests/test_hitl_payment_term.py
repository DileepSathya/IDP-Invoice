"""HITL payment-term check is gated by HITL_REQUIRE_TERM_TO_PAY in .env."""
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from backend.hitl_status import calculate_hitl_flag


def _minimal_invoice() -> dict:
    return {
        "invoice_date": "2026-09-09",
        "po_id": "PO-1",
        "total_amount": 10,
        "line_items": [
            {"service": "Item", "quantity": 1, "price_per_unit": 10, "amount": 10},
        ],
        "additional_fields": {},
    }


class HitlPaymentTermEnvTests(unittest.TestCase):
    def test_missing_payment_terms_do_not_hitl_when_env_false_or_unset(self) -> None:
        invoice = _minimal_invoice()
        for env in ({}, {"HITL_REQUIRE_TERM_TO_PAY": "false"}, {"HITL_REQUIRE_TERM_TO_PAY": "0"}):
            with self.subTest(env=env):
                with patch.dict(os.environ, env, clear=False):
                    if "HITL_REQUIRE_TERM_TO_PAY" not in env:
                        os.environ.pop("HITL_REQUIRE_TERM_TO_PAY", None)
                    data = deepcopy(invoice)
                    hitl = calculate_hitl_flag(data, run_erp_matching_now=False)
                    self.assertFalse(hitl)
                    self.assertNotIn(
                        "Term to pay / Due date is missing",
                        data["additional_fields"].get("hitl_remarks") or [],
                    )

    def test_missing_payment_terms_hitl_when_env_true(self) -> None:
        invoice = _minimal_invoice()
        with patch.dict(os.environ, {"HITL_REQUIRE_TERM_TO_PAY": "true"}, clear=False):
            data = deepcopy(invoice)
            hitl = calculate_hitl_flag(data, run_erp_matching_now=False)
            self.assertTrue(hitl)
            self.assertIn(
                "Term to pay / Due date is missing",
                data["additional_fields"]["hitl_remarks"],
            )

    def test_term_to_pay_alone_clears_payment_check_when_env_true(self) -> None:
        invoice = _minimal_invoice()
        invoice["term_to_pay"] = "30 days"
        with patch.dict(os.environ, {"HITL_REQUIRE_TERM_TO_PAY": "1"}, clear=False):
            data = deepcopy(invoice)
            hitl = calculate_hitl_flag(data, run_erp_matching_now=False)
            self.assertFalse(hitl)
            self.assertNotIn(
                "Term to pay / Due date is missing",
                data["additional_fields"].get("hitl_remarks") or [],
            )


if __name__ == "__main__":
    unittest.main()
