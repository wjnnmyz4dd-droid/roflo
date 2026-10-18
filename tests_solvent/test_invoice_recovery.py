"""A crash between the delivery and the invoice.

Measured in the eleven-point crash matrix: killing the process inside
``ledger.open_payment`` leaves a job delivered, in AWAITING_PAYMENT, with
no payment row. Nothing recovered it. Work had gone out of the door with
nothing on paper saying it was owed for, which is the quietest way a
business loses money.

The repair writes the obligation down before the invoice is opened, so a
restart has something durable to reconcile against. The tests below are
mostly about what it must *not* do: never invent a delivery, never invent
a payment, never write a second invoice.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.harness import Solvent, recover_after_restart
from solvent.types import BlockedOn, JobState, LocationSignals, PaymentState


class DeliveredJobs(unittest.TestCase):

    AMOUNT = 42_500

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "invoice.db"))
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def delivered_job(self, *, obligation=True, invoiced=False) -> str:
        """A job that reached AWAITING_PAYMENT, with the crash point chosen.

        ``obligation=False`` is a kill between the delivery and the moment
        the amount was written down. ``invoiced=True`` is a kill after the
        payment row already existed.
        """
        job_id = self.s.orchestrator.intake(
            title="delivered, then interrupted", client_id="client:acme",
            quoted_cents=self.AMOUNT, signals=LocationSignals(),
            initiator="test")
        for step in (JobState.QUALIFYING, JobState.ACCEPTED,
                     JobState.EXECUTING, JobState.VERIFYING,
                     JobState.READY_FOR_DELIVERY, JobState.AWAITING_PAYMENT):
            self.s.orchestrator._transition(job_id, step, why="walking",
                                            initiator="test")
        # The durable record the real cycle writes when the gate lets the
        # delivery out. Recovery must not act on the state alone.
        self.s.audit.record(
            event="delivery.completed", authority="orchestrator",
            initiator="execution", why="delivered", job_id=job_id,
            external_effect="delivery via req_test")
        if obligation:
            self.s.ledger.record_billing_obligation(
                job_id=job_id, amount_cents=self.AMOUNT, rail="fixture_rail",
                basis="FIXED_PRICE", client_ref="client:acme")
        if invoiced:
            self.s.ledger.open_payment(job_id=job_id, amount_cents=self.AMOUNT,
                                       rail="fixture_rail",
                                       client_ref="client:acme")
        return job_id

    def recover(self):
        return recover_after_restart(self.s, workdir=self.box.name)

    def invoices(self, job_id):
        return [p for p in self.s.ledger.payment_rows()
                if p["job_id"] == job_id]

    # --------------------------------------------------------- the defect
    def test_a_delivered_job_with_no_invoice_gets_one(self):
        job_id = self.delivered_job()
        self.assertIsNone(self.s.ledger.payment_for(job_id))
        self.recover()
        payment = self.s.ledger.payment_for(job_id)
        self.assertIsNotNone(payment, "the delivery is still unbilled")
        self.assertEqual(payment["amount_cents"], self.AMOUNT)

    def test_the_recovered_invoice_keeps_the_agreed_terms(self):
        job_id = self.delivered_job()
        self.recover()
        payment = self.s.ledger.payment_for(job_id)
        self.assertEqual(payment["amount_cents"], self.AMOUNT)
        self.assertEqual(payment["rail"], "fixture_rail")
        self.assertEqual(payment["client_ref"], "client:acme")

    def test_the_recovered_invoice_is_billed_not_collected(self):
        job_id = self.delivered_job()
        self.recover()
        payment = self.s.ledger.payment_for(job_id)
        self.assertEqual(payment["state"], PaymentState.INVOICED.value)
        self.assertEqual(int(payment["collected_cents"]), 0)
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0,
                         "recovery invented revenue")

    # ------------------------------------------------- the three windows
    def test_a_crash_before_the_obligation_is_escalated_not_invented(self):
        """Delivered, but nobody ever wrote down what it was worth."""
        job_id = self.delivered_job(obligation=False)
        self.recover()
        self.assertIsNone(self.s.ledger.payment_for(job_id),
                          "an amount was invented for an unpriced delivery")
        job = self.s.orchestrator.job(job_id)
        self.assertEqual(job.state, JobState.BLOCKED)
        self.assertEqual(job.blocked_on, BlockedOn.OWNER)

    def test_a_crash_after_the_invoice_adds_nothing(self):
        job_id = self.delivered_job(invoiced=True)
        self.recover()
        self.assertEqual(len(self.invoices(job_id)), 1)

    def test_repeated_restarts_still_leave_exactly_one_invoice(self):
        job_id = self.delivered_job()
        for _ in range(4):
            self.recover()
        self.assertEqual(len(self.invoices(job_id)), 1)

    def test_two_recoveries_in_the_same_process_leave_one_invoice(self):
        job_id = self.delivered_job()
        self.recover()
        self.recover()
        self.assertEqual(len(self.invoices(job_id)), 1)

    # ------------------------------------------- never invent the basis
    def test_an_undelivered_job_is_never_invoiced(self):
        """The obligation alone is not enough; the delivery has to have
        happened, and the audit log is what says so."""
        job_id = self.s.orchestrator.intake(
            title="never delivered", client_id="client:acme",
            quoted_cents=self.AMOUNT, signals=LocationSignals(),
            initiator="test")
        for step in (JobState.QUALIFYING, JobState.ACCEPTED,
                     JobState.EXECUTING):
            self.s.orchestrator._transition(job_id, step, why="walking",
                                            initiator="test")
        self.s.ledger.record_billing_obligation(
            job_id=job_id, amount_cents=self.AMOUNT, rail="fixture_rail",
            basis="FIXED_PRICE", client_ref="client:acme")
        self.recover()
        self.assertIsNone(self.s.ledger.payment_for(job_id),
                          "undelivered work was invoiced")

    def test_a_billable_state_without_a_delivery_event_is_not_invoiced(self):
        """The state is not the evidence; the delivery event is.

        Mutation testing found this hole: the other "never invoice this"
        test uses a job that is not in a billable state at all, so a
        repair that trusted the state alone passed it. A job can reach
        AWAITING_PAYMENT by a repair, a replay or a migration. Only the
        append-only ``delivery.completed`` event says work went out.
        """
        job_id = self.s.orchestrator.intake(
            title="moved, not delivered", client_id="client:acme",
            quoted_cents=self.AMOUNT, signals=LocationSignals(),
            initiator="test")
        for step in (JobState.QUALIFYING, JobState.ACCEPTED,
                     JobState.EXECUTING, JobState.VERIFYING,
                     JobState.READY_FOR_DELIVERY, JobState.AWAITING_PAYMENT):
            self.s.orchestrator._transition(job_id, step, why="walking",
                                            initiator="test")
        self.s.ledger.record_billing_obligation(
            job_id=job_id, amount_cents=self.AMOUNT, rail="fixture_rail",
            basis="FIXED_PRICE", client_ref="client:acme")
        self.recover()
        self.assertIsNone(
            self.s.ledger.payment_for(job_id),
            "a job that reached a billable state without a recorded "
            "delivery was invoiced anyway")

    # ------------------------------- delivered by recovery, never priced
    # Found by the integrated acceptance run, not by these tests: a job
    # resumed to delivery goes out through `_execute_job`, which has no
    # invoice step, so nothing is ever owed on file. It was escalated
    # correctly -- blocked on the owner, incident recorded -- but a fixed
    # price job does not need a person: the amount is the quote.

    def delivered_by_recovery(self, *, basis="FIXED_PRICE") -> str:
        job_id = self.s.orchestrator.intake(
            title="delivered by recovery", client_id="client:acme",
            quoted_cents=self.AMOUNT, signals=LocationSignals(),
            initiator="test")
        for step in (JobState.QUALIFYING, JobState.ACCEPTED,
                     JobState.EXECUTING, JobState.VERIFYING,
                     JobState.READY_FOR_DELIVERY, JobState.AWAITING_PAYMENT):
            self.s.orchestrator._transition(job_id, step, why="walking",
                                            initiator="test")
        if basis:
            self.s.audit.record(
                event="billing.terms_agreed", authority="ledger",
                initiator="harness", why="the basis this job is billed on",
                job_id=job_id, decision=basis)
        self.s.audit.record(
            event="delivery.completed", authority="orchestrator",
            initiator="execution", why="delivered", job_id=job_id,
            external_effect="delivery via req_test")
        return job_id

    def test_a_fixed_price_job_delivered_by_recovery_is_invoiced(self):
        job_id = self.delivered_by_recovery()
        self.recover()
        payment = self.s.ledger.payment_for(job_id)
        self.assertIsNotNone(payment, "a priced delivery still needed a person")
        self.assertEqual(payment["amount_cents"], self.AMOUNT)
        self.assertEqual(payment["state"], PaymentState.INVOICED.value)

    def test_an_hourly_job_delivered_by_recovery_is_escalated(self):
        """Its figure depends on hours this process cannot reconstruct."""
        job_id = self.delivered_by_recovery(basis="HOURLY")
        self.recover()
        self.assertIsNone(self.s.ledger.payment_for(job_id),
                          "an hourly amount was invented")
        job = self.s.orchestrator.job(job_id)
        self.assertEqual(job.state, JobState.BLOCKED)
        self.assertEqual(job.blocked_on, BlockedOn.OWNER)

    def test_a_job_with_no_agreed_basis_is_escalated(self):
        job_id = self.delivered_by_recovery(basis="")
        self.recover()
        self.assertIsNone(self.s.ledger.payment_for(job_id))
        self.assertEqual(self.s.orchestrator.job(job_id).state,
                         JobState.BLOCKED)

    def test_recovery_pricing_does_not_double_invoice(self):
        self.delivered_by_recovery()
        for _ in range(3):
            self.recover()
        invoiced = self.s.ledger.invoiced_job_ids()
        self.assertEqual(len(invoiced), len(set(invoiced)))

    def test_an_obligation_cannot_be_rewritten_after_the_fact(self):
        """One job, one obligation, enforced by the database."""
        job_id = self.delivered_job()
        with self.assertRaises(Exception):
            self.s.ledger.record_billing_obligation(
                job_id=job_id, amount_cents=999_999, rail="fixture_rail",
                basis="FIXED_PRICE", client_ref="client:acme")
        self.recover()
        self.assertEqual(self.s.ledger.payment_for(job_id)["amount_cents"],
                         self.AMOUNT)

    # ------------------------------------------------- interrupted recovery
    def test_an_interrupted_recovery_leaves_no_half_invoice(self):
        job_id = self.delivered_job()
        original = self.s.ledger.open_payment

        def exploding(**kwargs):
            raise RuntimeError("killed while writing the invoice")

        self.s.ledger.open_payment = exploding
        try:
            self.recover()
        except Exception:
            pass
        finally:
            self.s.ledger.open_payment = original
        self.assertEqual(self.invoices(job_id), [],
                         "a failed invoice write left a row behind")
        # And the next restart still fixes it.
        self.recover()
        self.assertEqual(len(self.invoices(job_id)), 1)

    def test_the_audit_chain_survives_recovery(self):
        self.delivered_job()
        self.recover()
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)

    def test_recovery_is_recorded_where_money_questions_are_read(self):
        job_id = self.delivered_job()
        self.recover()
        events = [e for e in self.s.audit.events(job_id)
                  if e.get("event") == "payment.opened"]
        self.assertTrue(events, "an invoice appeared with no audit entry")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
