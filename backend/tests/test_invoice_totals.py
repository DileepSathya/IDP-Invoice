"""Regression coverage for invoice-total reconciliation semantics."""

from __future__ import annotations

import unittest

from backend.hitl_status import calculate_hitl_flag, compute_expected_total


class InvoiceTotalTests(unittest.TestCase):
    def test_accounting_minus_round_off_reconciles_observed_invoice(self) -> None:
        invoice = {
            "line_items": [{"amount": "6,521.20"}],
            "additional_fields": {
                "igst_amount": "1,173.82",
                "round_off": "(-)0.02",
            },
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 7695.00)

    def test_parenthesized_round_off_is_negative(self) -> None:
        invoice = {
            "line_items": [{"amount": "100.02"}],
            "additional_fields": {"round_off": "(0.02)"},
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 100.00)

    def test_discount_is_a_reduction_even_when_printed_with_minus_marker(self) -> None:
        invoice = {
            "line_items": [{"amount": "1,000.00"}],
            "additional_fields": {"discount": "(-)100.00", "igst_amount": "162.00"},
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 1062.00)

    def test_positive_round_off_increases_payable_total(self) -> None:
        invoice = {
            "line_items": [{"amount": "100.48"}],
            "additional_fields": {"round_off": "+0.02"},
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 100.50)

    def test_unsigned_round_off_uses_direction_that_reconciles_payable_total(self) -> None:
        invoice = {
            "total_amount": "7695.00",
            "line_items": [{"amount": "6521.20"}],
            "additional_fields": {"igst_amount": "1173.82", "round_off": "0.02"},
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 7695.00)

    def test_round_off_is_not_applied_twice_when_after_tax_total_already_matches(self) -> None:
        invoice = {
            "total_amount": "7695.00",
            "line_items": [{"amount": "6521.20", "amount_after_tax": "7695.00"}],
            "additional_fields": {"igst_amount": "1173.82", "round_off": "-0.02"},
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 7695.00)

    def test_subtotal_after_discount_is_not_discounted_twice(self) -> None:
        invoice = {
            "line_items": [],
            "additional_fields": {
                "subtotal_after_discount": "900.00",
                "discount": "100.00",
                "igst_amount": "162.00",
            },
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 1062.00)

    def test_partial_amount_after_tax_does_not_drop_other_lines(self) -> None:
        invoice = {
            "line_items": [
                {"amount": "100.00", "amount_after_tax": "118.00"},
                {"amount": "200.00"},
            ],
            "additional_fields": {"igst_amount": "54.00"},
        }

        self.assertAlmostEqual(compute_expected_total(invoice), 354.00)

    def test_validation_refreshes_a_stale_cached_summary(self) -> None:
        invoice = {
            "invoice_date": "2025-07-01",
            "po_id": "PO-293",
            "term_to_pay": "30 days",
            "total_amount": "7695.00",
            "line_items": [
                {
                    "service": "Bottle",
                    "quantity": 548,
                    "price_per_unit": 11.90,
                    "amount": "6521.20",
                }
            ],
            "additional_fields": {
                "igst_amount": "1173.82",
                "round_off": "(-)0.02",
                "summary_total_amount": "7695.02",
            },
        }

        flagged = calculate_hitl_flag(invoice, run_erp_matching_now=False)

        self.assertFalse(flagged)
        self.assertEqual(invoice["additional_fields"]["summary_total_amount"], "7695.00")


if __name__ == "__main__":
    unittest.main()
