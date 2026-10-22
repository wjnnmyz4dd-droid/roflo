"""Proposal, contract, deposit — and the gate between money and work.

The sequence a service business gets wrong goes: start the work, send
the invoice, watch the client go quiet. So the deposit is a gate and
not a milestone, and `work_authorised` is a computed property rather
than a stored flag precisely so that there is no field for a bug, an
optimistic update or a client's assurance to set.

Three collapses this refuses, each tested in both directions:
invoiced is not paid, a client saying they paid is not a payment, and
accepted is not authorised.
"""

from __future__ import annotations

import unittest

from solvent.engagement import (
    Acceptance, DEFAULT_DEPOSIT_FRACTION, Deliverable, Engagement, Proposal,
    Stage,
)
from solvent.errors import FailClosed


def deliverable(check="parses_as_csv"):
    return Deliverable("a cleaned CSV", check, "it opens as CSV")


def proposal(price=35_000, **kw):
    return Proposal(proposal_id="pr1", prospect_id="p1", service="csv-cleanup",
                    price_cents=price, deliverables=(deliverable(),),
                    turnaround_days=5, revisions_included=1, **kw)


def accepted(engagement):
    engagement.send()
    engagement.accept(Acceptance("Dana", "2026-10-20", "web form", "sha:abc"))
    return engagement


def paid(engagement, cents, ref="pi_1"):
    engagement.record_payment({"verified": True, "cents": cents,
                               "external_ref": ref})
    return engagement


class AProposalPromisesSomethingCheckable(unittest.TestCase):

    def test_a_proposal_with_no_deliverable_is_refused(self):
        with self.assertRaises(FailClosed):
            Proposal("pr", "p", "csv-cleanup", 1000, (), 5, 1)

    def test_a_deliverable_with_no_check_is_refused(self):
        with self.assertRaises(FailClosed) as caught:
            Proposal("pr", "p", "csv-cleanup", 1000,
                     (Deliverable("something nice", "", "it is nice"),), 5, 1)
        self.assertIn("verify", str(caught.exception))

    def test_a_proposal_with_no_price_is_refused(self):
        with self.assertRaises(FailClosed):
            proposal(price=0)

    def test_a_turnaround_of_nothing_is_not_a_commitment(self):
        with self.assertRaises(FailClosed):
            Proposal("pr", "p", "csv-cleanup", 1000, (deliverable(),), 0, 1)

    def test_the_rendered_proposal_states_every_term(self):
        text = proposal(scope_excluded=("analysis",),
                        client_must_supply=("your file",)).render()
        for expected in ("Price:", "Deposit to begin", "Balance on delivery",
                         "Turnaround:", "Revisions included:",
                         "Not included:", "What we need from you:"):
            self.assertIn(expected, text)


class TheDepositIsTwentyPercentAndRoundsOurWay(unittest.TestCase):

    def test_the_default_is_twenty_percent(self):
        self.assertEqual(DEFAULT_DEPOSIT_FRACTION, 0.20)
        self.assertEqual(proposal(100_000).deposit_cents(), 20_000)

    def test_deposit_and_balance_are_the_whole_price(self):
        for price in (1, 999, 35_000, 100_001, 7):
            with self.subTest(price=price):
                p = proposal(price)
                self.assertEqual(p.deposit_cents() + p.balance_cents(), price)

    def test_it_rounds_up_rather_than_leaving_us_short(self):
        self.assertEqual(proposal(999).deposit_cents(), 200)

    def test_a_fraction_that_is_not_a_deposit_is_refused(self):
        for bad in (0, 1, 1.5, -0.2):
            with self.subTest(fraction=bad):
                with self.assertRaises(FailClosed):
                    proposal().deposit_cents(bad)


class WorkWaitsForVerifiedMoney(unittest.TestCase):

    def setUp(self):
        self.deal = Engagement("e1", proposal())

    def test_a_deposit_cannot_be_requested_before_acceptance(self):
        self.deal.send()
        with self.assertRaises(FailClosed):
            self.deal.request_deposit()

    def test_acceptance_alone_does_not_authorise_work(self):
        accepted(self.deal)
        self.assertFalse(self.deal.work_authorised)
        with self.assertRaises(FailClosed):
            self.deal.authorise_work()

    def test_a_cleared_deposit_authorises_work(self):
        accepted(self.deal).request_deposit()
        paid(self.deal, self.deal.deposit_due_cents)
        self.assertTrue(self.deal.work_authorised)
        self.assertIs(self.deal.authorise_work().stage, Stage.WORK_AUTHORISED)

    def test_a_part_deposit_does_not_authorise_work(self):
        accepted(self.deal).request_deposit()
        paid(self.deal, self.deal.deposit_due_cents - 1)
        self.assertFalse(self.deal.work_authorised)

    def test_an_unverified_payment_is_refused(self):
        accepted(self.deal).request_deposit()
        with self.assertRaises(FailClosed) as caught:
            self.deal.record_payment({"verified": False, "cents": 7_000,
                                      "external_ref": "x"})
        self.assertIn("claim that money arrived", str(caught.exception))

    def test_a_client_saying_they_paid_moves_nothing(self):
        accepted(self.deal).request_deposit()
        with self.assertRaises(FailClosed):
            self.deal.record_payment(
                {"verified": False, "cents": 7_000,
                 "external_ref": "their email says they sent it"})
        self.assertFalse(self.deal.work_authorised)

    def test_a_payment_with_no_reference_is_refused(self):
        accepted(self.deal).request_deposit()
        with self.assertRaises(FailClosed):
            self.deal.record_payment({"verified": True, "cents": 7_000,
                                      "external_ref": ""})

    def test_a_repeated_webhook_is_not_counted_twice(self):
        accepted(self.deal).request_deposit()
        for _ in range(5):
            paid(self.deal, 7_000, ref="pi_same")
        self.assertEqual(self.deal.collected_cents, 7_000)

    def test_delivering_unauthorised_work_is_refused(self):
        accepted(self.deal)
        with self.assertRaises(FailClosed):
            self.deal.delivered()

    def test_there_is_no_field_that_sets_authorisation_directly(self):
        """It is computed, so nothing can simply assign it True."""
        with self.assertRaises(AttributeError):
            self.deal.work_authorised = True


class InvoicedIsNotPaid(unittest.TestCase):

    def setUp(self):
        self.deal = Engagement("e1", proposal())
        accepted(self.deal).request_deposit()
        paid(self.deal, self.deal.deposit_due_cents)
        self.deal.authorise_work().delivered().invoiced()

    def test_invoicing_does_not_settle_anything(self):
        self.assertIs(self.deal.stage, Stage.INVOICED)
        self.assertGreater(self.deal.outstanding_cents, 0)

    def test_the_balance_settles_only_on_a_verified_event(self):
        paid(self.deal, self.deal.outstanding_cents, ref="pi_balance")
        self.assertIs(self.deal.stage, Stage.PAID)
        self.assertEqual(self.deal.outstanding_cents, 0)

    def test_a_paid_engagement_is_terminal(self):
        paid(self.deal, self.deal.outstanding_cents, ref="pi_balance")
        with self.assertRaises(FailClosed):
            self.deal.invoiced()


class AnAcceptanceIsAttributable(unittest.TestCase):

    def test_acceptance_needs_who_when_how_and_which_terms(self):
        for missing in range(4):
            fields = ["Dana", "2026-10-20", "web form", "sha:abc"]
            fields[missing] = "  "
            with self.subTest(missing=missing):
                with self.assertRaises(FailClosed):
                    Acceptance(*fields)

    def test_a_second_acceptance_is_refused(self):
        deal = accepted(Engagement("e1", proposal()))
        with self.assertRaises(FailClosed) as caught:
            deal.accept(Acceptance("Someone", "2026-10-21", "email", "sha:z"))
        self.assertIn("two agreements for one job", str(caught.exception))

    def test_a_declined_engagement_does_not_accept_later(self):
        deal = Engagement("e1", proposal())
        deal.declined("they went elsewhere")
        with self.assertRaises(FailClosed):
            deal.accept(Acceptance("Dana", "2026-10-20", "web", "sha:abc"))


if __name__ == "__main__":
    unittest.main()
