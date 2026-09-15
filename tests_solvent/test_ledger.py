"""Ledger: expected revenue is not collected revenue."""

import unittest

from solvent.audit import AuditLog
from solvent.errors import FailClosed
from solvent.ledger import Ledger
from solvent.store import Store
from solvent.types import CostCategory, PaymentState


class Costs(unittest.TestCase):
    def setUp(self):
        store = Store()
        self.ledger = Ledger(store, AuditLog(store))

    def test_untrusted_cost_source_refused(self):
        """A heuristic token count is not a bill. roflo's len/4 must never book."""
        with self.assertRaises(FailClosed):
            self.ledger.record_cost(job_id="J1", category=CostCategory.AI_API,
                                    amount_cents=100, source="TOKEN_HEURISTIC")

    def test_recording_the_same_entry_twice_books_it_once(self):
        entry = self.ledger.record_cost(
            job_id="J1", category=CostCategory.AI_API, amount_cents=1200,
            source="PROVIDER_BILLING")
        self.ledger.record_cost(job_id="J1", category=CostCategory.AI_API,
                                amount_cents=1200, source="PROVIDER_BILLING",
                                entry_id=entry)
        self.assertEqual(self.ledger.costs_for("J1"), 1200)

    def test_correction_appends_a_delta_and_never_edits(self):
        entry = self.ledger.record_cost(
            job_id="J1", category=CostCategory.AI_API, amount_cents=1000,
            source="PROVIDER_BILLING")
        self.ledger.correct(original_id=entry, job_id="J1",
                            category=CostCategory.AI_API, corrected_amount_cents=700,
                            source="PROVIDER_BILLING", reason="provider rebate")
        self.assertEqual(self.ledger.costs_for("J1"), 700)
        rows = self.ledger._db.query("SELECT * FROM ledger_entries WHERE job_id='J1'")
        self.assertEqual(len(rows), 2, "correction must be a new row, not an edit")


class Payments(unittest.TestCase):
    def setUp(self):
        store = Store()
        self.ledger = Ledger(store, AuditLog(store))
        self.payment = self.ledger.open_payment(
            job_id="J1", amount_cents=50_000, rail="test")

    def test_invoiced_is_not_revenue(self):
        self.ledger.set_payment_state(self.payment, PaymentState.INVOICED)
        self.assertEqual(self.ledger.collected_for("J1"), 0)
        self.assertEqual(self.ledger.actual_profit("J1")["collected_revenue_cents"], 0)

    def test_paid_without_external_verification_is_refused(self):
        with self.assertRaises(FailClosed):
            self.ledger.set_payment_state(self.payment, PaymentState.PAID,
                                          collected_cents=50_000)

    def test_verified_payment_becomes_revenue_and_profit(self):
        self.ledger.record_cost(job_id="J1", category=CostCategory.AI_API,
                                amount_cents=2_000, source="PROVIDER_BILLING")
        self.ledger.set_payment_state(self.payment, PaymentState.PAID,
                                      collected_cents=50_000,
                                      verification_method="rail_webhook")
        profit = self.ledger.actual_profit("J1")
        self.assertEqual(profit["collected_revenue_cents"], 50_000)
        self.assertEqual(profit["actual_cost_cents"], 2_000)
        self.assertEqual(profit["actual_profit_cents"], 48_000)

    def test_only_verified_jobs_enter_the_calibration_sample(self):
        self.ledger.set_payment_state(self.payment, PaymentState.INVOICED)
        self.assertEqual(self.ledger.verified_job_ids(), [])
        self.ledger.set_payment_state(self.payment, PaymentState.PAID,
                                      collected_cents=50_000,
                                      verification_method="bank_reconciliation")
        self.assertEqual(self.ledger.verified_job_ids(), ["J1"])

    def test_disputed_and_refunded_are_not_collected(self):
        for state in (PaymentState.DISPUTED, PaymentState.REFUNDED,
                      PaymentState.OVERDUE, PaymentState.AUTHORIZED):
            with self.subTest(state=state):
                self.assertFalse(state.is_collected)


if __name__ == "__main__":
    unittest.main()
