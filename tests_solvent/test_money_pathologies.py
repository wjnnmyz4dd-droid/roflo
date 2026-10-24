"""Every way a client can pay, fail to pay, or take it back.

The question behind all of them is the same one: **does the shop
seize?** A capacity accounting that cannot tell "this work is
unfinished" from "this invoice is unpaid" stops taking new work the
first time somebody pays late, and never starts again.

Four things are counted separately and must stay separate. Each row
says who owns it and what it is for:

================================  ===========================================
execution capacity                ``Capacity`` reservations -- hours of work
                                  owed. Released when the work is done.
accepted commitments              ``qualification.OCCUPIES_CAPACITY`` -- job
                                  states that still owe labour.
outstanding receivables           the Ledger. Money billed and not collected.
financial exposure                ``Governor.committed_unverified_cents`` --
                                  delivered and not yet verified as paid,
                                  against the owner's ceiling.
================================  ===========================================

Nothing here forgives an invoice, and nothing fabricates a
settlement: every payment is a signed delivery the production rail
verifies and the production Ledger decides on.
"""

from __future__ import annotations

import os
import pathlib
import unittest
from unittest import mock

from solvent.errors import FailClosed
from solvent.harness import OWNER
from solvent.qualification import OCCUPIES_CAPACITY
from solvent.runtime import DEFAULT_INTERVALS, reconcile
from solvent.settlement import settle_paid_jobs
from solvent.types import JobState

from simulation import rail
from tests_solvent.test_capacity_and_contracting import Billable


class Money(Billable):
    """A shop with work delivered and a spool the production tick reads."""

    def setUp(self):
        super().setUp()
        self.spool = pathlib.Path(self.box.name) / "payments-inbox"
        self.s.policy.amend({"runtime": {"payment_spool": str(self.spool)}},
                            OWNER, "where payment deliveries land")
        secret = mock.patch.dict(os.environ,
                                 {rail.SECRET_ENV: rail.TEST_SECRET})
        secret.start()
        self.addCleanup(secret.stop)

    def invoiced(self, how_many: int = 1):
        """``how_many`` jobs delivered, invoiced and awaiting payment."""
        jobs = self.deliver(how_many)
        self.assertEqual(len(jobs), how_many)
        for job in jobs:
            self.assertIs(job.state, JobState.AWAITING_PAYMENT, job.id)
        return [(job, int((self.s.ledger.payment_for(job.id) or {})
                          .get("amount_cents") or 0)) for job in jobs]

    def drain(self):
        runtime = self.runtime()
        done = runtime.run_due()
        self._now += DEFAULT_INTERVALS["payments"] + 1
        return [o for o in done if o.startswith("payments:")]

    # ---- the four counters, read from their own authorities ----------

    def books(self) -> dict:
        jobs = self.s.orchestrator.jobs()
        return {
            "owed_seconds": self.s.capacity.owed_seconds(),
            "reservations": len(self.s.capacity.outstanding()),
            "occupying": sum(1 for j in jobs
                             if j.state in OCCUPIES_CAPACITY),
            "receivable_cents": sum(
                max(0, int(r["amount_cents"]) - int(r["collected_cents"]))
                for r in self.s.store.for_authority("ledger").query(
                    "SELECT amount_cents, collected_cents FROM payments")),
            "exposure_cents": self.s.governor.committed_unverified_cents(),
            "real_revenue_cents": self.s.ledger.real_revenue_cents(),
        }

    def assert_not_seized(self, why: str = ""):
        """There is room for another job. The property the whole file is for."""
        room = self.s.capacity.room_for(3_600.0, kind="EXECUTE",
                                        capability="csv-cleanup")
        self.assertTrue(room.allowed, f"the shop has seized{why}: {room.why}")


class WhenTheyPay(Money):

    def test_promptly(self):
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_prompt")

        self.drain()

        self.assertIs(self.s.orchestrator.job(job.id).state, JobState.COMPLETE)
        after = self.books()
        self.assertEqual(after["owed_seconds"], 0.0)
        self.assertEqual(after["receivable_cents"], 0)
        self.assertEqual(after["real_revenue_cents"], 0,
                         "simulated money must never read as revenue")
        self.assert_not_seized()

    def test_late(self):
        """Ten simulated days of silence, then the money. No seizure."""
        (job, owed), = self.invoiced()
        runtime = self.runtime()
        before = self.books()
        for _ in range(10):
            runtime.run_due()
            self._now += 86_400
            self.assert_not_seized(" while waiting to be paid")

        self.assertEqual(self.books()["receivable_cents"],
                         before["receivable_cents"],
                         "waiting changed what was owed")
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_late")
        runtime.run_due()

        self.assertIs(self.s.orchestrator.job(job.id).state, JobState.COMPLETE)
        self.assertEqual(self.books()["receivable_cents"], 0)

    def test_partially(self):
        """Part of it is not all of it, and the job does not close."""
        (job, owed), = self.invoiced()
        self.assertGreater(owed, 2)

        rail.pay(self.spool, job_id=job.id, amount_cents=owed // 2,
                 event_id="evt_half")
        self.drain()

        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.AWAITING_PAYMENT,
                      "a half-paid job was closed")
        self.assertEqual(self.s.ledger.collected_for(job.id), owed // 2)
        self.assertGreater(self.books()["receivable_cents"], 0,
                           "the rest stopped being owed")
        self.assert_not_seized(" on a part payment")

    def test_the_orchestrator_refuses_a_part_payment_when_asked_directly(self):
        """The rule, at the authority that holds it.

        Tested separately from the sweep because the two guards cover
        each other: with both in place a mutant that removes either
        changes nothing observable, which is what defence in depth
        means and also why each layer needs its own test.
        """
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed // 2,
                 event_id="evt_direct_half")
        self.drain()

        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.complete(job_id=job.id, initiator="test")

        self.assertIn("part payment is not a settled invoice",
                      str(caught.exception))
        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.AWAITING_PAYMENT)

    def test_the_sweep_does_not_even_offer_a_part_paid_job(self):
        """The other layer. If it offered it, the Orchestrator's
        refusal would be recorded -- on every tick, for ever."""
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed // 2,
                 event_id="evt_offered_half")
        self.drain()

        self.assertEqual(settle_paid_jobs(self.s, initiator="test"), [])
        self.assertEqual(
            [r for r in self.s.audit.named(("payment.settlement_refused",))],
            [], "the sweep pushed a part-paid job at the Orchestrator")

    def test_the_rest_afterwards(self):
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed // 2,
                 event_id="evt_first_half")
        self.drain()

        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_in_full")
        self.drain()

        self.assertEqual(self.s.ledger.collected_for(job.id), owed)
        self.assertIs(self.s.orchestrator.job(job.id).state, JobState.COMPLETE)

    def test_twice_over(self):
        """A rail re-delivering is the ordinary case, not an attack."""
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_once")
        self.drain()
        once = self.s.ledger.collected_for(job.id)

        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_once")
        self.drain()

        self.assertEqual(self.s.ledger.collected_for(job.id), once)
        self.assertEqual(once, owed)


class WhenTheyDoNot(Money):

    def test_never_paying_does_not_stop_the_shop(self):
        """Five jobs delivered, nobody pays, thirty simulated days."""
        delivered = self.invoiced(5)
        runtime = self.runtime()

        for _ in range(30):
            runtime.run_due()
            self._now += 86_400

        after = self.books()
        self.assertEqual(after["owed_seconds"], 0.0,
                         "delivered work is still holding execution slots")
        self.assertEqual(after["reservations"], 0)
        self.assertEqual(after["occupying"], 0,
                         "a delivered job owes money, not work")
        self.assertGreater(after["receivable_cents"], 0,
                           "the unpaid invoices were forgiven")
        self.assertEqual(after["real_revenue_cents"], 0)
        self.assert_not_seized(" after thirty days of nonpayment")
        self.assertEqual(
            [j.state for j in self.s.orchestrator.jobs()],
            [JobState.AWAITING_PAYMENT] * len(delivered),
            "an unpaid job changed state on its own")

    def test_the_exposure_ceiling_is_what_stops_the_shop_not_capacity(self):
        """The conservative control is the financial one, and it is the
        owner's number -- not an accident of the capacity ledger.

        Execution capacity comes back when the work is done. What
        bounds a shop whose clients do not pay is
        ``max_committed_unverified_cents``, which the owner sets.
        """
        self.invoiced(1)
        # Lowered *after* the job was delivered. Setting it first
        # makes the Governor refuse the job at the budget stage, so
        # it is REJECTED and never delivered -- which is correct
        # behaviour and measures the wrong thing. The first version
        # of this test did that.
        self.s.policy.amend(
            {"financial": {"max_committed_unverified_cents": 1}},
            OWNER, "the owner will carry almost no unpaid work")

        exposure = self.s.governor.committed_unverified_cents()
        ceiling = int(self.s.policy.get(
            "financial", "max_committed_unverified_cents", default=0))

        self.assertGreater(exposure, ceiling,
                           "nothing is over the ceiling, so this proves "
                           "nothing about the ceiling")
        self.assert_not_seized(" -- capacity is not the thing that should "
                               "refuse here")


class WhenTheyTakeItBack(Money):

    def paid(self):
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id=f"evt_paid_{job.id}")
        self.drain()
        self.assertEqual(self.s.ledger.collected_for(job.id), owed)
        return job, owed

    def test_a_refund_reduces_what_was_collected(self):
        job, owed = self.paid()

        rail.refund(self.spool, job_id=job.id, amount_cents=owed,
                    event_id="evt_refund")
        self.drain()

        self.assertEqual(self.s.ledger.collected_for(job.id), 0)
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)
        self.assert_not_seized(" after a refund")

    def test_a_dispute_withdraws_the_money_pending_the_outcome(self):
        job, owed = self.paid()

        rail.dispute(self.spool, job_id=job.id, amount_cents=owed,
                     event_id="evt_dispute")
        self.drain()

        self.assertEqual(self.s.ledger.collected_for(job.id), 0,
                         "a disputed payment still counted as collected")
        self.assert_not_seized(" after a chargeback")

    def test_a_refunded_job_stays_closed_and_the_books_say_why(self):
        """The work was done and delivered. Taking the money back does
        not undo that, and pretending it does would be a second lie."""
        job, owed = self.paid()
        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.COMPLETE)

        rail.refund(self.spool, job_id=job.id, amount_cents=owed,
                    event_id="evt_refund_state")
        self.drain()

        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.COMPLETE)
        events = self.s.ledger.payment_events(job.id)
        self.assertTrue(any("REFUNDED" in str(e.get("outcome"))
                            for e in events), events)

    def test_a_forged_refund_moves_nothing(self):
        job, owed = self.paid()

        rail.tamper(self.spool, job_id=job.id, amount_cents=owed,
                    event_id="evt_forged_refund")
        self.drain()

        self.assertEqual(self.s.ledger.collected_for(job.id), owed,
                         "an unsigned refund took the money back")


class WhenItRestartsMidWay(Money):
    """Reconciliation is re-runnable, and running it twice changes nothing."""

    def test_reconciling_an_unpaid_shop_closes_nothing(self):
        self.invoiced(3)
        before = self.books()

        reconcile(self.s, initiator="test")
        reconcile(self.s, initiator="test")

        self.assertEqual(self.books(), before)
        for job in self.s.orchestrator.jobs():
            self.assertIs(job.state, JobState.AWAITING_PAYMENT)

    def test_a_payment_applied_before_a_restart_closes_after_it(self):
        """The spool was drained and the process died before settling."""
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_crash")
        from solvent.harness import ingest_payment_spool

        ingest_payment_spool(self.s, spool=str(self.spool))
        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.AWAITING_PAYMENT,
                      "the ingestor closed the job, so this tests nothing")

        reconcile(self.s, initiator="restart")

        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.COMPLETE)

    def test_settling_is_idempotent(self):
        (job, owed), = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_idem")
        self.drain()

        self.assertEqual(settle_paid_jobs(self.s, initiator="test"), [])
        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.COMPLETE)


class TheFourCountersStaySeparate(Money):
    """The assignment's A, B, C, D, measured on one shop at one moment."""

    def test_delivered_and_unpaid_is_the_only_interesting_state(self):
        self.invoiced(3)

        books = self.books()

        # A: execution capacity -- back, because the work is finished.
        self.assertEqual(books["owed_seconds"], 0.0)
        self.assertEqual(books["reservations"], 0)
        # B: accepted commitments -- none, because none owes labour.
        self.assertEqual(books["occupying"], 0)
        # C: receivables -- three invoices, all outstanding.
        self.assertGreater(books["receivable_cents"], 0)
        # D: exposure -- the same work, counted as money at risk.
        self.assertGreater(books["exposure_cents"], 0)
        # And no revenue, because nobody has paid.
        self.assertEqual(books["real_revenue_cents"], 0)

    def test_paying_moves_c_and_d_and_leaves_a_and_b_alone(self):
        delivered = self.invoiced(3)
        before = self.books()
        for job, owed in delivered:
            rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                     event_id=f"evt_{job.id}")

        self.drain()
        after = self.books()

        self.assertEqual(after["owed_seconds"], before["owed_seconds"])
        self.assertEqual(after["occupying"], before["occupying"])
        self.assertEqual(after["receivable_cents"], 0)
        self.assertLess(after["exposure_cents"], before["exposure_cents"])


if __name__ == "__main__":
    unittest.main()
