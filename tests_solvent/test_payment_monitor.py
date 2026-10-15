"""Watching money arrive, and never touching it.

The owner's position: DeskPilot should receive payment and keep
working, and should not manage or move funds. The tests that matter
most here are therefore the ones about what this module *cannot* do,
and the one about what counts as proof that money arrived.

That second one is the load-bearing rule. A client's email saying
"sent it this morning", a marketplace message, a PDF that looks like
a remittance advice -- all of them are sentences somebody can write,
and a model reading them cannot tell a true one from a forged one.
Only a signed event from the payment rail can confirm a payment, and
confirmation_problem says no to everything else by default.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import paymentmonitor as pm
from solvent.harness import OWNER, Solvent
from solvent.types import PaymentState

DAY = 86_400.0


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/pay.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def invoice(self, job_id, cents=40_000, state=PaymentState.INVOICED,
                **kw):
        pid = self.s.ledger.open_payment(job_id=job_id, amount_cents=cents,
                                         rail="stripe")
        if state is not None:
            self.s.ledger.set_payment_state(pid, state, **kw)
        return pid

    def paid(self, job_id, cents=25_000):
        return self.invoice(job_id, cents, PaymentState.PAID,
                            collected_cents=cents,
                            verification_method="rail_event")


class OnlyTheProviderCanConfirmAPayment(Base):
    """The rule that stops an AI being talked into declaring revenue."""

    def test_a_rail_event_with_a_reference_is_accepted(self):
        self.assertEqual(
            self.s.payments.confirmation_problem(
                evidence_kind="rail_event", evidence_ref="evt_123"), "")

    def test_an_email_is_not_confirmation(self):
        problem = self.s.payments.confirmation_problem(
            evidence_kind="email", evidence_ref="msg-1")
        self.assertIn("not authoritative", problem)
        self.assertIn("not an email", problem)

    def test_a_client_message_is_not_confirmation(self):
        for kind in ("client_message", "marketplace_thread", "pdf_receipt",
                     "screenshot", "owner_assertion", "model_judgement"):
            with self.subTest(kind=kind):
                self.assertTrue(self.s.payments.confirmation_problem(
                    evidence_kind=kind, evidence_ref="x"))

    def test_nothing_at_all_is_not_confirmation(self):
        self.assertTrue(self.s.payments.confirmation_problem())

    def test_a_rail_event_with_no_reference_is_refused(self):
        """An unverifiable confirmation is not one."""
        problem = self.s.payments.confirmation_problem(
            evidence_kind="rail_event", evidence_ref="")
        self.assertIn("no reference", problem)

    def test_the_allow_list_is_exactly_one_kind(self):
        self.assertEqual(pm.AUTHORITATIVE, ("rail_event",))


class ItMovesNoMoney(Base):
    """A read-only module, asserted over its surface."""

    def test_it_has_no_method_that_could_move_funds(self):
        surface = {n for n in dir(self.s.payments) if not n.startswith("_")}
        for forbidden in ("pay", "transfer", "withdraw", "refund", "send",
                          "convert", "charge", "capture", "payout",
                          "settle", "retry", "set_state", "write"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, surface)

    def test_it_does_not_change_ledger_state(self):
        self.invoice("j1")
        before = self.s.ledger.payment_for("j1")["state"]
        self.s.payments.census()
        self.s.payments.outstanding()
        self.s.payments.profit()
        self.s.payments.client_standing()
        self.assertEqual(self.s.ledger.payment_for("j1")["state"], before)


class TheSevenStates(Base):

    def test_all_seven_are_declared(self):
        self.assertEqual(len(pm.STATES), 7)

    def test_a_job_with_no_payment_row_is_not_invoiced(self):
        self.assertEqual(self.s.payments.state("nobody"), pm.NOT_INVOICED)

    def test_each_ledger_state_maps_to_one_of_the_owners_seven(self):
        cases = {
            PaymentState.INVOICED: pm.INVOICED,
            PaymentState.PAYMENT_PENDING: pm.PAYMENT_PENDING,
            PaymentState.OVERDUE: pm.INVOICED,
            PaymentState.DISPUTED: pm.PAYMENT_DISPUTED,
            PaymentState.REFUNDED: pm.REFUNDED,
            PaymentState.PAYMENT_FAILED: pm.PAYMENT_FAILED,
        }
        for n, (ledger_state, expected) in enumerate(cases.items()):
            job = f"job-{n}"
            with self.subTest(state=ledger_state.value):
                self.invoice(job, state=ledger_state)
                self.assertEqual(self.s.payments.state(job), expected)

    def test_a_collected_payment_reads_as_confirmed(self):
        self.paid("j9")
        self.assertEqual(self.s.payments.state("j9"), pm.PAYMENT_CONFIRMED)

    def test_a_failed_attempt_is_not_a_dispute_or_a_silence(self):
        """Three different problems that need three different answers."""
        self.invoice("f", state=PaymentState.PAYMENT_FAILED)
        self.invoice("d", state=PaymentState.DISPUTED)
        self.invoice("s", state=PaymentState.INVOICED)
        self.assertEqual(self.s.payments.state("f"), pm.PAYMENT_FAILED)
        self.assertEqual(self.s.payments.state("d"), pm.PAYMENT_DISPUTED)
        self.assertEqual(self.s.payments.state("s"), pm.INVOICED)

    def test_the_census_counts_every_state(self):
        self.invoice("a")
        self.paid("b")
        self.invoice("c", state=PaymentState.DISPUTED)
        census = self.s.payments.census()
        self.assertEqual(census[pm.INVOICED], 1)
        self.assertEqual(census[pm.PAYMENT_CONFIRMED], 1)
        self.assertEqual(census[pm.PAYMENT_DISPUTED], 1)


class UnpaidIsTrackedWithoutStoppingWork(Base):
    """Slow payment must not freeze production. The owner was explicit."""

    def test_outstanding_lists_only_what_is_owed(self):
        self.invoice("a", 40_000)
        self.paid("b", 25_000)
        rows = self.s.payments.outstanding()
        self.assertEqual([r["job_id"] for r in rows], ["a"])

    def test_a_confirmed_payment_is_not_outstanding(self):
        self.paid("b")
        self.assertEqual(self.s.payments.outstanding(), [])

    def test_the_total_owed_is_reported(self):
        self.invoice("a", 40_000)
        self.invoice("b", 10_000)
        self.assertEqual(self.s.payments.unpaid_cents(), 50_000)

    def test_an_unpaid_invoice_holds_no_capacity(self):
        """The decisive one. The gate must not know about this.

        An invoice nobody has paid is a debt to chase. If it also
        held a worker, one slow client would take a fifth of the
        shop out of service for as long as they felt like not
        paying.
        """
        self.invoice("a", 40_000)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 0)
        self.assertEqual(self.s.capacity.executing(), 0)

    def test_outstanding_is_oldest_first(self):
        self.invoice("new", 1_000)
        self.invoice("old", 2_000)
        rows = self.s.payments.outstanding(now=None)
        self.assertEqual(len(rows), 2)
        ages = [r["age_days"] for r in rows]
        self.assertEqual(ages, sorted(ages, reverse=True))


class SimulatedMoneyIsNotReceivables(Base):
    """The same mistake as counting it as revenue, in the other column."""

    def simulated_invoice(self, job_id, cents):
        """A simulated invoice, marked the way the Ledger marks them.

        The marker is the SIMULATED: prefix on verification_method,
        which is what Ledger.is_simulated reads. An earlier version
        of this test put the prefix on external_ref, found
        is_simulated said False, and skipped itself -- so it reported
        green while testing nothing at all.
        """
        from solvent.ledger import SIMULATED_PREFIX

        pid = self.s.ledger.open_payment(job_id=job_id, amount_cents=cents,
                                         rail="fixture")
        db = self.s.store.for_authority("ledger")
        db.execute("UPDATE payments SET state = ?, verification_method = ? "
                   "WHERE id = ?",
                   (PaymentState.INVOICED.value,
                    f"{SIMULATED_PREFIX}fixture", pid))
        db.commit()
        self.assertTrue(self.s.ledger.is_simulated(job_id),
                        "the row is not marked simulated, so this case "
                        "would pass without the exclusion working")
        return pid

    def test_simulated_invoices_are_left_out_of_the_total(self):
        self.invoice("real", 40_000)
        self.simulated_invoice("sim", 99_000)
        self.assertEqual(self.s.payments.unpaid_cents(), 40_000)

    def test_they_can_be_asked_for_explicitly(self):
        self.invoice("real", 40_000)
        self.simulated_invoice("sim", 99_000)
        self.assertEqual(
            self.s.payments.unpaid_cents(include_simulated=True), 139_000)

    def test_a_simulated_client_is_not_counted_as_behind(self):
        """Nobody owes the owner money for a rehearsal."""
        self.simulated_invoice("sim", 99_000)
        self.assertEqual(self.s.payments.client_standing(), {})

    def test_collected_revenue_excludes_simulated(self):
        self.assertEqual(
            self.s.payments.profit()["collected_revenue_cents"],
            self.s.ledger.real_revenue_cents())


class ConfirmedIsNotTheSameAsReal(Base):
    """The trap the lifecycle run exposed.

    A simulated payment reaches PAYMENT_CONFIRMED, because that is
    the state it is in and "simulated" is not one of the owner's
    seven. Anything reporting money from the state alone would
    therefore report a rehearsal as income.
    """

    def test_a_simulated_payment_is_confirmed(self):
        self.simulated_confirmed("sim")
        self.assertEqual(self.s.payments.state("sim"),
                         pm.PAYMENT_CONFIRMED)

    def test_but_it_is_not_real(self):
        self.simulated_confirmed("sim")
        self.assertFalse(self.s.payments.real("sim"))

    def test_a_verified_payment_is_real(self):
        self.paid("b", 25_000)
        self.assertTrue(self.s.payments.real("b"))

    def test_an_unpaid_invoice_is_not_real(self):
        self.invoice("a")
        self.assertFalse(self.s.payments.real("a"))

    def simulated_confirmed(self, job_id):
        from solvent.ledger import SIMULATED_PREFIX

        pid = self.s.ledger.open_payment(job_id=job_id, amount_cents=4_000,
                                         rail="fixture")
        db = self.s.store.for_authority("ledger")
        db.execute("UPDATE payments SET state = ?, collected_cents = ?, "
                   "verification_method = ? WHERE id = ?",
                   (PaymentState.PAID.value, 4_000,
                    f"{SIMULATED_PREFIX}fixture", pid))
        db.commit()
        self.assertTrue(self.s.ledger.is_simulated(job_id))


class ProjectedIsNeverAddedToCollected(Base):
    """Two numbers, because adding them is how a business imagines cash."""

    def test_collected_and_projected_are_separate_fields(self):
        self.paid("b", 25_000)
        self.invoice("a", 40_000)
        profit = self.s.payments.profit()
        self.assertEqual(profit["collected_revenue_cents"], 25_000)
        self.assertEqual(profit["invoiced_but_unpaid_cents"], 40_000)

    def test_actual_profit_uses_collected_money_only(self):
        self.paid("b", 25_000)
        self.invoice("a", 40_000)
        profit = self.s.payments.profit()
        self.assertEqual(profit["actual_profit_cents"],
                         25_000 - profit["recorded_costs_cents"])

    def test_the_projection_says_what_it_assumes(self):
        self.assertIn("assumes every outstanding invoice is paid",
                      self.s.payments.profit()["basis"])


class AnUnreadableLedgerIsNotAQuietMonth(Base):
    """Zero and None must not look alike.

    The first version of profit() caught every read failure and
    carried on with zero, so a database that would not open produced
    a tidy "collected $0.00" -- which is exactly what a month with no
    sales looks like. An existing briefing test caught it, which is
    the second time that particular guard has earned its place.
    """

    def break_the_ledger(self):
        def explode(*a, **k):
            raise RuntimeError("the payments table is unreadable")

        self.s.ledger.real_revenue_cents = explode
        self.s.ledger.total_costs_cents = explode

    def test_an_unreadable_figure_is_none_not_zero(self):
        self.break_the_ledger()
        profit = self.s.payments.profit()
        self.assertIsNone(profit["collected_revenue_cents"])
        self.assertIsNone(profit["actual_profit_cents"])

    def test_it_says_which_read_failed(self):
        self.break_the_ledger()
        problems = self.s.payments.profit()["problems"]
        self.assertTrue(problems)
        self.assertTrue(any("collected revenue" in p for p in problems))

    def test_a_genuinely_empty_ledger_reports_zero(self):
        """The other half: zero must still be available as an answer."""
        profit = self.s.payments.profit()
        self.assertEqual(profit["collected_revenue_cents"], 0)
        self.assertEqual(profit["problems"], [])

    def test_the_summary_raises_it_as_something_needing_the_owner(self):
        from solvent import briefing

        self.break_the_ledger()
        summary = briefing.compose(self.s, slot=briefing.MORNING)
        self.assertTrue(any("could not be read" in line
                            for line in summary.needs_you))


class AClientWhoHasNotPaidGetsNoMoreCredit(Base):

    def set_threshold(self, days):
        self.s.policy.amend({"commercial": {"overdue_after_days": days}},
                            OWNER, "test")

    def overdue(self, job_id, client, cents, days):
        """An invoice issued `days` ago, by moving the clock forward."""
        self.invoice(job_id, cents)
        from datetime import datetime, timedelta, timezone

        # Reach in through the authority that owns the table, to
        # backdate the row. Backdating is the only way to test a
        # 45-day-old invoice without waiting 45 days.
        db = self.s.store.for_authority("ledger")
        when = (datetime.now(timezone.utc)
                - timedelta(days=days)).isoformat()
        db.execute("UPDATE payments SET client_ref = ?, updated_at = ? "
                   "WHERE job_id = ?", (client, when, job_id))
        db.commit()

    def test_no_threshold_means_nobody_is_counted_behind(self):
        self.overdue("a", "client:x", 40_000, 90)
        allowed, why = self.s.payments.may_take_more_work_from("client:x")
        self.assertTrue(allowed)
        self.assertIn("has not set an overdue threshold", why)

    def test_a_client_inside_the_threshold_is_fine(self):
        self.set_threshold(30)
        self.overdue("a", "client:x", 40_000, 5)
        allowed, why = self.s.payments.may_take_more_work_from("client:x")
        self.assertTrue(allowed, why)

    def test_a_client_past_the_threshold_is_recommended_against(self):
        self.set_threshold(30)
        self.overdue("a", "client:x", 40_000, 60)
        allowed, why = self.s.payments.may_take_more_work_from("client:x")
        self.assertFalse(allowed)
        self.assertIn("extending more credit", why)

    def test_a_client_who_owes_nothing_is_fine(self):
        self.set_threshold(30)
        allowed, _ = self.s.payments.may_take_more_work_from("client:new")
        self.assertTrue(allowed)

    def test_standing_reports_the_amount_and_the_age(self):
        self.set_threshold(30)
        self.overdue("a", "client:x", 40_000, 45)
        standing = self.s.payments.client_standing()["client:x"]
        self.assertEqual(standing.unpaid_invoices, 1)
        self.assertEqual(standing.unpaid_cents, 40_000)
        self.assertGreater(standing.oldest_unpaid_days, 44)
        self.assertFalse(standing.clean)

    def test_it_recommends_and_does_not_enforce(self):
        """Refusing a customer is a commercial decision, not this one."""
        surface = {n for n in dir(self.s.payments) if not n.startswith("_")}
        for forbidden in ("block", "ban", "refuse", "reject", "suspend"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, surface)


if __name__ == "__main__":
    unittest.main()
