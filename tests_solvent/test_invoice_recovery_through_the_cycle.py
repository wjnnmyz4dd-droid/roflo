"""The obligation is written by the real cycle, not only by the tests.

``test_invoice_recovery`` builds the crash states by hand, which proves the
reconciliation works but not that production ever writes the record it
reconciles against. This runs the business cycle and looks.
"""

from __future__ import annotations

import os
import secrets
import unittest

from solvent.harness import OWNER
from solvent.types import ActionClass
from tests_solvent.test_business_cycle import Cycle


class TheCycleWritesTheObligation(Cycle):

    def setUp(self):
        os.environ["SOLVENT_OWNER_KEY"] = secrets.token_hex(32)
        self.addCleanup(os.environ.pop, "SOLVENT_OWNER_KEY", None)
        super().setUp()
        self.s.policy.amend({"preauthorizations": [
            {"action_class": ActionClass.C2_EXTERNAL_COMMUNICATION.value,
             "max_cents": 0}]}, OWNER, "test: routine delivery preauthorised")
        self.cycle = self.runCycle()
        self.assertTrue(self.cycle.delivered, self.cycle.why_stopped)

    def test_a_delivered_cycle_leaves_an_obligation_on_file(self):
        obligation = self.s.ledger.obligation_for(self.cycle.job_id)
        self.assertIsNotNone(
            obligation,
            "the cycle invoiced without first recording what was owed, so a "
            "crash between the two would lose the money silently")

    def test_the_obligation_matches_the_invoice(self):
        obligation = self.s.ledger.obligation_for(self.cycle.job_id)
        payment = self.s.ledger.payment_for(self.cycle.job_id)
        self.assertEqual(obligation["amount_cents"], payment["amount_cents"])
        self.assertEqual(obligation["rail"], payment["rail"])

    def test_the_obligation_names_who_owes_it(self):
        obligation = self.s.ledger.obligation_for(self.cycle.job_id)
        self.assertTrue(obligation["client_ref"],
                        "an obligation with nobody attached cannot be chased")

    def test_a_second_cycle_does_not_reopen_the_obligation(self):
        """Re-running must not double-bill; the key is the job."""
        before = self.s.ledger.obligation_for(self.cycle.job_id)
        self.register_board([{**self.posting, "ref": "second"}])
        self.runCycle()
        after = self.s.ledger.obligation_for(self.cycle.job_id)
        self.assertEqual(before, after)

    def test_the_agreed_basis_is_on_file(self):
        """Written before the work, so a crash at any later point keeps it."""
        terms = [e for e in self.s.audit.events(self.cycle.job_id)
                 if e.get("event") == "billing.terms_agreed"]
        self.assertTrue(terms, "nothing records what this job is billed on")
        self.assertEqual(terms[-1]["decision"], "FIXED_PRICE")

    def test_reconciliation_finds_nothing_left_to_do(self):
        from solvent import runtime
        notes = runtime.reconcile(self.s)
        self.assertFalse([n for n in notes if "unbilled" in n or "invoice" in n],
                         f"a complete cycle left work for recovery: {notes}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
