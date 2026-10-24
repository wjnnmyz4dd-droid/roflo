"""Money arriving, from the spool to a closed job.

The ingress was complete and unreachable. ``StripeRail.verify_event``
checks a signature with a replay window and a constant-time compare,
``Ledger.apply_payment_event`` decides what an authentic event means
for the books, and ``harness.ingest_payment_spool`` moves the bytes
between them -- and the only caller of the last one was ``solvent
payments ingest``, typed at a terminal. So ``PaymentState.PAID`` was
reachable in a running shop only through the business cycle's
``simulate_payment`` branch: a real client's money arrived in a real
account and the books never heard.

The deliveries here are signed by :mod:`simulation.rail`, which calls
the production signer rather than writing HMAC code of its own. A
simulated payer with its own implementation would be testing whether
two pieces of code agree, and would keep passing if both drifted.

Nothing here is real money: every event carries ``livemode: false``,
so the Ledger records it with the ``SIMULATED:`` prefix and
``real_revenue_cents`` stays at zero. That is asserted, not assumed.
"""

from __future__ import annotations

import os
import pathlib
import unittest
from collections import Counter
from unittest import mock

from solvent.errors import FailClosed
from solvent.harness import OWNER
from solvent.runtime import DEFAULT_INTERVALS
from solvent.settlement import settle_paid_jobs
from solvent.storefront import OrderState
from solvent.types import JobState

from simulation import rail
from tests_solvent.test_capacity_and_contracting import Billable


class Paid(Billable):
    """A shop with work delivered and a spool the tick can read."""

    def setUp(self):
        super().setUp()
        self.spool = pathlib.Path(self.box.name) / "payments-inbox"
        self.s.policy.amend({"runtime": {"payment_spool": str(self.spool)}},
                            OWNER, "where payment deliveries land")
        secret = mock.patch.dict(
            os.environ, {rail.SECRET_ENV: rail.TEST_SECRET})
        secret.start()
        self.addCleanup(secret.stop)

    def invoiced(self):
        """One job delivered, invoiced and awaiting payment."""
        jobs = self.deliver(1)
        job = jobs[0]
        self.assertIs(job.state, JobState.AWAITING_PAYMENT,
                      "the fixture did not get a job to the point of being "
                      "owed money, so nothing here tests anything")
        row = self.s.ledger.payment_for(job.id) or {}
        return job, int(row.get("amount_cents") or 0)

    def payments(self, runtime) -> str:
        names = [t.name for t in runtime.tasks]
        self.assertEqual(names.count("payments"), 1)
        done = runtime.run_due()
        self._now += DEFAULT_INTERVALS["payments"] + 1
        line = [o for o in done if o.startswith("payments:")]
        self.assertTrue(line, f"the payments task was not due: {done}")
        return line[0].split("payments: ", 1)[1]


class TheSpoolIsDrained(Paid):

    def test_the_task_exists_and_has_a_cadence(self):
        self.assertIn("payments", DEFAULT_INTERVALS)
        self.assertEqual(self.runtime().unscheduled, [])

    def test_an_empty_spool_is_a_real_answer(self):
        self.assertIn("no payments waiting", self.payments(self.runtime()))

    def test_a_signed_delivery_reaches_the_books(self):
        job, owed = self.invoiced()
        self.assertGreater(owed, 0)
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_one")

        said = self.payments(self.runtime())

        self.assertIn("1 applied", said)
        self.assertEqual(self.s.ledger.collected_for(job.id), owed)

    def test_the_money_is_marked_simulated_and_is_not_revenue(self):
        job, owed = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_two")

        self.payments(self.runtime())

        method = (self.s.ledger.payment_for(job.id) or {}).get(
            "verification_method", "")
        self.assertTrue(method.startswith("SIMULATED:"), method)
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0,
                         "a signature over test money is still not revenue")

    def test_a_badly_signed_delivery_is_refused_and_the_owner_told(self):
        job, owed = self.invoiced()
        rail.tamper(self.spool, job_id=job.id, amount_cents=owed,
                    event_id="evt_forged")

        said = self.payments(self.runtime())

        self.assertIn("1 refused", said)
        self.assertEqual(self.s.ledger.collected_for(job.id), 0,
                         "a forged delivery moved money")
        sent = [n for n in self.s.notifier.notifications()
                if n.get("event") == "payment.refused"]
        self.assertTrue(sent, "a refused payment nobody was told about")

    def test_a_redelivery_is_ignored_rather_than_counted_twice(self):
        job, owed = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_same")
        runtime = self.runtime()
        self.payments(runtime)
        once = self.s.ledger.collected_for(job.id)

        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_same")
        self.payments(runtime)

        self.assertEqual(self.s.ledger.collected_for(job.id), once,
                         "the rail re-delivered and the books counted it twice")

    def test_an_overpayment_is_refused_by_the_ledger_not_by_the_tick(self):
        """The tick is plumbing. The refusal is the Ledger's."""
        job, owed = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed + 1,
                 event_id="evt_too_much")

        self.payments(self.runtime())

        self.assertEqual(self.s.ledger.collected_for(job.id), 0)
        events = self.s.ledger.payment_events(job.id)
        self.assertTrue(any("exceeds the invoiced" in str(e.get("outcome"))
                            for e in events), events)

    def test_an_unreadable_spool_says_so_rather_than_saying_nothing(self):
        """"No payments" and "we could not look" are different answers."""
        runtime = self.runtime()
        with mock.patch("solvent.harness.ingest_payment_spool",
                        side_effect=OSError("the disk went away")):
            said = self.payments(runtime)

        self.assertIn("could not read the payment spool", said)
        rows = [r for r in self.s.audit.named(("payment.spool_unreadable",))]
        self.assertTrue(rows, "an unreadable spool left no record")

    def test_the_spool_path_comes_from_policy(self):
        """So a run can point it somewhere of its own."""
        job, owed = self.invoiced()
        elsewhere = pathlib.Path(self.box.name) / "another-inbox"
        rail.pay(elsewhere, job_id=job.id, amount_cents=owed,
                 event_id="evt_elsewhere")

        self.assertIn("no payments waiting", self.payments(self.runtime()))
        self.s.policy.amend({"runtime": {"payment_spool": str(elsewhere)}},
                            OWNER, "the owner moved the spool")

        self.assertIn("1 applied", self.payments(self.runtime()))


class TheJobCloses(Paid):
    """The last link, and it was missing.

    A verified payment reached the Ledger and stopped there: the
    payment row read PAID and the job stayed in AWAITING_PAYMENT for
    ever, so a shop that delivered, invoiced and was paid still showed
    nothing completed and the customer's order never reached CLOSED.
    """

    def test_an_unpaid_job_is_not_closed(self):
        """The control. Without it the next test proves nothing."""
        job, _ = self.invoiced()

        closed = settle_paid_jobs(self.s, initiator="test")

        self.assertEqual(closed, [])
        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.AWAITING_PAYMENT)

    def test_a_paid_job_is_closed(self):
        job, owed = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_close")

        self.payments(self.runtime())

        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.COMPLETE)

    def test_the_orchestrator_still_asks_the_ledger_itself(self):
        """This offers jobs; it does not assert that any was paid."""
        job, _ = self.invoiced()

        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.complete(job_id=job.id, initiator="test")

        self.assertIn("no externally verified collected revenue",
                      str(caught.exception))

    def test_a_disagreement_is_recorded_rather_than_swallowed(self):
        job, owed = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_argue")
        self.s.audit.record(event="probe", authority="orchestrator",
                            initiator="test", why="marker")
        with mock.patch.object(type(self.s.orchestrator), "complete",
                               side_effect=FailClosed("the books disagree")):
            self.payments(self.runtime())

        rows = self.s.audit.named(("payment.settlement_refused",))
        self.assertTrue(rows, "a job the Ledger says is paid and the "
                              "orchestrator will not close left no record")

    def test_settling_twice_closes_it_once(self):
        job, owed = self.invoiced()
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_twice")
        runtime = self.runtime()
        self.payments(runtime)

        again = settle_paid_jobs(self.s, initiator="test")

        self.assertEqual(again, [])
        self.assertIs(self.s.orchestrator.job(job.id).state,
                      JobState.COMPLETE)

    def test_the_customer_is_finally_told_it_is_complete(self):
        from solvent import contracting

        job, owed = self.invoiced()
        order_id = [o.order_id for o in self.s.storefront.orders()][0]
        rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                 event_id="evt_told")
        self.payments(self.runtime())

        contracting.tell_the_customer(self.s, order_id=order_id)

        order = self.s.storefront.order(order_id)
        self.assertIs(order.state, OrderState.CLOSED)
        self.assertEqual(order.customer_status(), "Complete.")


class WhatAWholeShopLooksLike(Paid):
    """Five skills, five clients, all the way to closed."""

    def test_five_jobs_are_delivered_paid_and_closed(self):
        jobs = self.deliver(5)
        self.assertEqual(len(jobs), 5)
        runtime = self.runtime()
        for job in jobs:
            owed = int((self.s.ledger.payment_for(job.id) or {})
                       .get("amount_cents") or 0)
            self.assertGreater(owed, 0, f"{job.id} was never invoiced")
            rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                     event_id=f"evt_{job.id}")

        self.payments(runtime)

        states = Counter(j.state.value for j in self.s.orchestrator.jobs())
        self.assertEqual(states, Counter({"COMPLETE": 5}), states)
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0,
                         "simulated money must never read as revenue")
        collected = sum(self.s.ledger.collected_for(j.id) for j in jobs)
        self.assertGreater(collected, 0)


if __name__ == "__main__":
    unittest.main()
