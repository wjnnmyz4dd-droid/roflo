"""The second connector: agreed price -> contract -> order -> work.

``test_connected_lifecycle`` named a seam and crossed it by hand:
nothing in ``solvent/`` read ``chief_of_staff.engagements`` in order to
admit work, so an engagement reaching ``work_authorised`` caused
nothing to happen. ``Proposal(`` and ``Engagement(`` appeared nowhere
in the package outside ``engagement_from_dict``, which rebuilds one
that already exists. Every engagement in the repository had been
constructed by a line of test code.

These tests are about the module that closes it. The emphasis is on
what it must **refuse**, because a connector whose whole job is to
start work is the one component where a missing refusal is a job
admitted for a client who never agreed, never paid, or never sent the
file.

The fictional client is simulated -- their counteroffer, their
acceptance, their deposit, their materials. Everything on DeskPilot's
side is the production component.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from dataclasses import fields

from solvent import contracting
from solvent.contracting import Refused
from solvent.crm import Standing
from solvent.engagement import (
    Acceptance, Engagement, Proposal, Stage, engagement_from_dict,
)
from solvent.errors import FailClosed
from solvent.harness import OWNER
from solvent.negotiation import Outcome
from solvent.runtime import DEFAULT_INTERVALS
from solvent.storefront import OFFERS, OrderState

from tests_solvent.test_dealmaking import Connected

ACCEPTED_ON = "2026-10-21"
TURNAROUND = 3


class Contracted(Connected):
    """A client who has agreed a price, through the production path."""

    def setUp(self):
        super().setUp()
        self.s.policy.amend({"contracting": {"turnaround_days": TURNAROUND}},
                            OWNER, "1C connector certification")

    # ---- the client's half, simulated and marked as such -------------

    def agreed(self, capability: str = "csv-cleanup") -> str:
        """To an agreed price, with the scheduler opening the deal."""
        pid = self.arrive(capability)
        self.assertIn("1 opened", self.deals(self.runtime()))
        deal = self.s.chief_of_staff.negotiations[pid]
        # Simulated: this is the client speaking, not DeskPilot.
        deal.respond(deal.current_cents, why="the client accepted")
        self.assertIs(deal.outcome, Outcome.AGREED_PENDING_AUTHORISATION)
        return pid

    def accepted(self, pid: str) -> Engagement:
        """Proposal drawn by production, accepted by the fictional client."""
        result = contracting.propose(self.s, prospect_id=pid, initiator="test")
        self.assertTrue(result.done, result.summary())
        engagement = self.s.chief_of_staff.engagements[pid]
        engagement.accept(Acceptance("Dana", ACCEPTED_ON, "reply to our email",
                                     f"sha256:{pid}"))
        return engagement

    def paid_the_deposit(self, engagement: Engagement) -> Engagement:
        request = engagement.request_deposit()
        engagement.record_payment({"verified": True,
                                   "cents": request["amount_cents"],
                                   "external_ref": f"pi_{engagement.engagement_id}"})
        self.assertTrue(engagement.work_authorised)
        return engagement

    def sent_their_file(self, engagement: Engagement,
                        capability: str = "csv-cleanup") -> Engagement:
        """The client sends what the offer's intake form asks for.

        Taken from the order the fixture already placed, so the answers
        are the canonical ones for this capability rather than
        something invented here.
        """
        answers = dict(self.s.storefront.order(f"ord-{capability}").answers)
        return engagement.supply(**answers)

    def ready(self, capability: str = "csv-cleanup"):
        """All four client acts done. Returns ``(prospect_id, engagement)``."""
        pid = self.agreed(capability)
        engagement = self.paid_the_deposit(self.accepted(pid))
        self.sent_their_file(engagement, capability)
        return pid, engagement


# ======================================================================
#  What it does
# ======================================================================


class TheConnectorExistsAtAll(Contracted):
    """The claim the module is for, asserted before anything subtler."""

    def test_nothing_else_in_the_package_builds_an_engagement(self):
        """The defect this module was written for, pinned so it stays fixed.

        An AST walk rather than a grep over the source: ``grep`` for
        ``Engagement(`` matches the word in a docstring, in a type
        annotation and in this very sentence, and a guard that reads
        prose as code passes whatever is written near it.
        """
        root = pathlib.Path(__file__).resolve().parent.parent / "solvent"
        built: dict[str, list[str]] = {}
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute)
                        else "")
                if name in ("Engagement", "Proposal"):
                    built.setdefault(path.name, []).append(name)

        self.assertEqual(
            sorted(built),
            ["contracting.py", "engagement.py"],
            "an engagement built somewhere else is a second contracting "
            f"authority; found {built}")

    def test_an_agreed_deal_becomes_a_proposal_on_the_published_terms(self):
        pid = self.agreed()
        self.assertNotIn(pid, self.s.chief_of_staff.engagements)

        result = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertTrue(result.done, result.summary())
        engagement = self.s.chief_of_staff.engagements[pid]
        offer = contracting.offer_for("csv-cleanup")[0]
        proposal = engagement.proposal
        self.assertIs(engagement.stage, Stage.SENT)
        self.assertEqual(proposal.service, "csv-cleanup")
        self.assertEqual(proposal.turnaround_days, TURNAROUND)
        self.assertEqual(proposal.revisions_included, offer.revisions.included)
        self.assertEqual(proposal.scope_excluded, tuple(offer.scope_excluded))
        self.assertEqual([d.check for d in proposal.deliverables],
                         [t.check for t in offer.requirements])
        self.assertEqual(self.s.crm.get(pid).standing, Standing.PROPOSED)

    def test_the_price_is_the_negotiated_one_and_not_a_second_opinion(self):
        """A connector that repriced would be a second pricing authority."""
        pid = self.agreed()
        agreed_at = self.s.chief_of_staff.negotiations[pid].current_cents
        self.assertGreater(agreed_at, 0, "a zero price checks nothing")

        contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertEqual(
            self.s.chief_of_staff.engagements[pid].proposal.price_cents,
            agreed_at)

    def test_a_paid_engagement_becomes_an_order_the_pipeline_can_run(self):
        pid, engagement = self.ready()

        result = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertTrue(result.done, result.summary())
        order = self.s.storefront.order(result.order_id)
        self.assertIsNotNone(order)
        self.assertIs(order.state, OrderState.RECEIVED)
        self.assertEqual(order.client_ref, pid)
        self.assertEqual(order.offered_cents, engagement.proposal.price_cents)
        self.assertIs(engagement.stage, Stage.WORK_AUTHORISED)
        self.assertEqual(self.s.crm.get(pid).standing, Standing.CONTRACTED)

    def test_the_admitted_order_is_executed_by_the_existing_cycle(self):
        """End to end, with nothing in the middle supplied by this test.

        The point is not that a CSV gets cleaned -- the five skills are
        certified elsewhere -- but that the order this connector placed
        is one ``next_work_order`` picks up and the real business cycle
        carries through every stage it owns.
        """
        pid, engagement = self.ready()
        contracting.admit(self.s, prospect_id=pid, initiator="test")

        cycle = self.runtime()._one_business_job()

        self.assertIsNotNone(cycle, "the admitted order was never picked up")
        self.assertEqual(cycle.client_ref, pid)
        self.assertEqual(cycle.opportunity_ref, f"eng-{pid}")
        self.assertTrue(cycle.delivered, cycle.stages)
        self.assertGreater(cycle.invoiced_cents, 0)
        for stage in ("qualify", "price", "budget", "execute", "verify",
                      "deliver", "invoice"):
            self.assertIn(stage, cycle.stages)


# ======================================================================
#  What it refuses
# ======================================================================


class NoProposalWithoutAnAgreement(Contracted):

    def test_an_open_negotiation_gets_no_proposal(self):
        pid = self.arrive()
        self.deals(self.runtime())
        self.assertIs(self.s.chief_of_staff.negotiations[pid].outcome,
                      Outcome.OPEN)

        result = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertFalse(result.done)
        self.assertIs(result.refusal, Refused.NOT_AGREED)
        self.assertNotIn(pid, self.s.chief_of_staff.engagements)

    def test_a_refused_negotiation_gets_no_proposal(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.outcome = Outcome.REFUSED

        result = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NOT_AGREED)

    def test_a_prospect_with_no_negotiation_gets_no_proposal(self):
        pid = self.arrive()

        result = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NO_NEGOTIATION)

    def test_a_second_proposal_is_refused_not_redrawn(self):
        """Two contracts for one agreement is two things to be paid under."""
        pid = self.agreed()
        self.assertTrue(contracting.propose(
            self.s, prospect_id=pid, initiator="test").done)
        first = self.s.chief_of_staff.engagements[pid]

        again = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertIs(again.refusal, Refused.ALREADY_ENGAGED)
        self.assertIs(self.s.chief_of_staff.engagements[pid], first)

    def test_no_turnaround_recorded_means_no_promise_made(self):
        """A delivery date the connector chose is a promise nobody made."""
        pid = self.agreed()
        self.s.policy.amend({"contracting": {"turnaround_days": 0}},
                            OWNER, "the owner withdrew the commitment")

        result = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NO_TURNAROUND)
        self.assertIn("turnaround", result.why)
        self.assertNotIn(pid, self.s.chief_of_staff.engagements)

    def test_a_skill_that_may_not_be_delivered_gets_no_proposal(self):
        """Found here rather than at admission, before the client pays.

        The order of the checks is the point. Asking the registry only
        at admission would mean a client accepting terms and paying a
        deposit for work DeskPilot already knew it could not do.
        """
        pid = self.agreed()
        self.s.capability.decide(
            name="csv-cleanup", decision=self.s.capability.DENIED,
            owner_identity=OWNER, why="a defect was found in the checker")

        result = contracting.propose(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NOT_SELLABLE)
        self.assertIn("csv-cleanup", result.why)
        self.assertNotIn(pid, self.s.chief_of_staff.engagements)


class NoWorkWithoutMoneyAndMaterials(Contracted):

    def test_an_unaccepted_proposal_admits_nothing(self):
        pid = self.agreed()
        contracting.propose(self.s, prospect_id=pid, initiator="test")

        result = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NOT_AUTHORISED)
        self.assertIsNone(self.s.storefront.order(f"eng-{pid}"))

    def test_an_accepted_proposal_with_no_deposit_admits_nothing(self):
        """Accepted binds the client. It does not authorise the work."""
        pid = self.agreed()
        engagement = self.accepted(pid)
        self.sent_their_file(engagement)
        self.assertIsNotNone(engagement.acceptance)

        result = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NOT_AUTHORISED)
        self.assertIsNone(self.s.storefront.order(f"eng-{pid}"))

    def test_a_part_paid_deposit_admits_nothing(self):
        pid = self.agreed()
        engagement = self.accepted(pid)
        request = engagement.request_deposit()
        engagement.record_payment({"verified": True,
                                   "cents": request["amount_cents"] - 1,
                                   "external_ref": "pi_short"})
        self.sent_their_file(engagement)

        result = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.NOT_AUTHORISED)

    def test_a_clients_word_that_they_paid_admits_nothing(self):
        """The gate takes a verified event. There is no argument for it."""
        pid = self.agreed()
        engagement = self.accepted(pid)
        engagement.request_deposit()
        self.sent_their_file(engagement)

        with self.assertRaises(FailClosed):
            engagement.record_payment({"verified": False, "cents": 100_000,
                                       "external_ref": "they_said_so"})

        self.assertIs(contracting.admit(self.s, prospect_id=pid,
                                        initiator="test").refusal,
                      Refused.NOT_AUTHORISED)

    def test_a_paid_engagement_with_no_file_admits_nothing(self):
        pid = self.agreed()
        self.paid_the_deposit(self.accepted(pid))

        result = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(result.refusal, Refused.MATERIALS_MISSING)
        self.assertIn("source_file", result.why)
        self.assertIsNone(self.s.storefront.order(f"eng-{pid}"))

    def test_an_empty_answer_is_not_an_answer(self):
        """``missing_answers`` tests the value, so a blank key would pass."""
        pid = self.agreed()
        engagement = self.paid_the_deposit(self.accepted(pid))

        with self.assertRaises(FailClosed) as caught:
            engagement.supply(source_file="   ")

        self.assertIn("empty", str(caught.exception))
        self.assertEqual(engagement.materials, {})

    def test_the_storefronts_own_refusal_is_reported_not_worked_around(self):
        """The minimum fee is the owner's, and this does not get past it."""
        pid, engagement = self.ready()
        self.s.policy.amend(
            {"commercial": {"minimum_project_fee_cents":
                            engagement.proposal.price_cents + 1}},
            OWNER, "the owner raised the floor")

        result = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertFalse(result.done)
        self.assertIn(result.refusal,
                      (Refused.ORDER_REFUSED, Refused.NO_OFFER))
        self.assertIsNot(engagement.stage, Stage.WORK_AUTHORISED,
                         "a declined order must not authorise work")

    def test_admitting_twice_places_one_order(self):
        """Two jobs for one payment is the thing the storefront exists to stop."""
        pid, _ = self.ready()
        first = contracting.admit(self.s, prospect_id=pid, initiator="test")
        self.assertTrue(first.done)

        again = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(again.refusal, Refused.ALREADY_ADMITTED)
        self.assertEqual(
            [o.order_id for o in self.s.storefront.orders()
             if o.order_id.startswith("eng-")],
            [f"eng-{pid}"])

    def test_this_module_never_submits_to_the_pipeline(self):
        """A second route in would be a second way to run a job twice.

        ``Storefront.submit_to_pipeline`` is the only door, and
        ``workorder.next_work_order`` is the only thing that opens it.
        Asserted on the source rather than by behaviour, because the
        behaviour it forbids is one extra call that the happy path
        would not notice.
        """
        source = (pathlib.Path(contracting.__file__)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        self.assertNotIn("submit_to_pipeline", called)

    def test_an_admitted_order_waits_in_the_shop_until_the_cycle_takes_it(self):
        pid, _ = self.ready()
        contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(self.s.storefront.order(f"eng-{pid}").state,
                      OrderState.RECEIVED)
        self.assertEqual(self.s.storefront.order(f"eng-{pid}").opportunity_id,
                         "", "nothing but next_work_order may open that door")


# ======================================================================
#  Through the real scheduler
# ======================================================================


class TheSchedulerCarriesTheDeal(Contracted):

    def contracts(self, runtime) -> str:
        names = [t.name for t in runtime.tasks]
        self.assertEqual(names.count("contracting"), 1,
                         "there must be exactly one contracting task")
        done = runtime.run_due()
        self._now += DEFAULT_INTERVALS["contracting"] + 1
        line = [o for o in done if o.startswith("contracting:")]
        self.assertTrue(line, f"the contracting task was not due: {done}")
        return line[0].split("contracting: ", 1)[1]

    def test_the_tick_has_a_configured_cadence(self):
        self.assertIn("contracting", DEFAULT_INTERVALS)
        self.assertEqual(self.runtime().unscheduled, [])

    def test_a_scheduler_pass_draws_the_proposal(self):
        pid = self.agreed()

        said = self.contracts(self.runtime())

        self.assertIn("1 carried", said)
        self.assertIs(self.s.chief_of_staff.engagements[pid].stage,
                      Stage.SENT)

    def test_a_scheduler_pass_admits_the_paid_deal(self):
        pid = self.agreed()
        runtime = self.runtime()
        self.contracts(runtime)
        engagement = self.s.chief_of_staff.engagements[pid]
        engagement.accept(Acceptance("Dana", ACCEPTED_ON, "email",
                                     f"sha256:{pid}"))
        self.paid_the_deposit(engagement)
        self.sent_their_file(engagement)

        said = self.contracts(runtime)

        self.assertIn("1 carried", said)
        order = self.s.storefront.order(f"eng-{pid}")
        self.assertIsNotNone(order, "no order was placed")
        # RECEIVED or already taken: the business task runs in the same
        # pass as this one and picks it up immediately, which is the
        # connection working. Asserting RECEIVED alone would make this
        # test fail precisely when the chain is joined.
        self.assertIn(order.state,
                      (OrderState.RECEIVED, OrderState.IN_QUALIFICATION,
                       OrderState.IN_PROGRESS, OrderState.DELIVERED,
                       OrderState.CLOSED))
        self.assertEqual(order.offered_cents,
                         engagement.proposal.price_cents)

    def test_an_empty_book_is_a_real_answer(self):
        self.assertEqual(self.contracts(self.runtime()),
                         "no deal is waiting on a step")

    def test_the_halt_stops_it(self):
        pid = self.agreed()
        self.s.policy.amend({"operations": {"halted": True}}, OWNER,
                            "the owner pulled the lever")

        said = self.contracts(self.runtime())

        self.assertIn("not run", said)
        self.assertNotIn(pid, self.s.chief_of_staff.engagements)

    def test_a_refusal_is_written_to_the_audit_log(self):
        pid = self.agreed()
        self.s.policy.amend({"contracting": {"turnaround_days": 0}},
                            OWNER, "withdrawn")

        self.contracts(self.runtime())

        rows = [r for r in self.s.audit.events()
                if r.get("event") == "contracting.not_carried"]
        self.assertTrue(rows, "a refusal nobody recorded is a refusal nobody "
                              "can act on")
        self.assertEqual(rows[-1]["decision"], Refused.NO_TURNAROUND.value)
        self.assertEqual(rows[-1]["result"], pid)


# ======================================================================
#  Durability
# ======================================================================


class WhatSurvivesARestart(Contracted):

    def test_every_engagement_field_survives_a_round_trip(self):
        """Derived from the dataclass, not from a list kept by hand.

        ``Negotiation.as_dict`` had exactly this defect: two cost lines
        were listed by hand, a third was added to ``Costs``, and a
        restart silently restored a negotiation with a lower floor.
        A hand-written mirror of a type falls behind the type; a test
        that reads the type's own fields cannot.
        """
        pid, engagement = self.ready()
        engagement.supply(needed_by="Friday")

        restored = engagement_from_dict(engagement.as_dict())

        skip = {"on_change"}
        for spec in fields(Engagement):
            if spec.name in skip:
                continue
            with self.subTest(f"Engagement.{spec.name}"):
                self.assertEqual(getattr(restored, spec.name),
                                 getattr(engagement, spec.name))
        for spec in fields(Proposal):
            with self.subTest(f"Proposal.{spec.name}"):
                self.assertEqual(getattr(restored.proposal, spec.name),
                                 getattr(engagement.proposal, spec.name))

    def test_the_materials_are_written_down_as_they_arrive(self):
        """Not at whatever point somebody remembers to save.

        An engagement that gained its source file in memory and lost it
        in a restart is one the pipeline could admit before the crash
        and not after.
        """
        pid, engagement = self.ready()
        saved: list[dict] = []
        engagement.on_change = lambda deal: saved.append(deal.as_dict())

        engagement.supply(needed_by="Friday")

        self.assertTrue(saved, "supplying materials recorded nothing")
        self.assertEqual(saved[-1]["materials"]["needed_by"], "Friday")

    def test_a_restart_does_not_admit_the_work_twice(self):
        """Idempotence lives in the storefront, which outlives the book."""
        pid, engagement = self.ready()
        contracting.admit(self.s, prospect_id=pid, initiator="test")

        # The book comes back from storage; the order is still in the shop.
        self.s.chief_of_staff.engagements[pid] = engagement_from_dict(
            engagement.as_dict())

        again = contracting.admit(self.s, prospect_id=pid, initiator="test")

        self.assertIs(again.refusal, Refused.ALREADY_ADMITTED)


# ======================================================================
#  The offer lookup
# ======================================================================


class WhichTermsTheClientGets(unittest.TestCase):

    def test_every_published_capability_resolves_to_one_offer(self):
        for item in OFFERS:
            with self.subTest(item.capability):
                found, why = contracting.offer_for(item.capability)
                self.assertIsNotNone(found, why)
                self.assertEqual(found.offer_id, item.offer_id)

    def test_an_unsold_capability_is_refused_rather_than_approximated(self):
        found, why = contracting.offer_for("time-travel")
        self.assertIsNone(found)
        self.assertIn("no published offer", why)

    def test_two_offers_for_one_capability_is_the_owners_choice(self):
        """Not resolved here: picking one chooses what the client is promised."""
        twin = OFFERS[0]
        with unittest.mock.patch.object(contracting, "OFFERS",
                                        OFFERS + (twin,)):
            found, why = contracting.offer_for(twin.capability)

        self.assertIsNone(found)
        self.assertIn("not this connector's choice", why)


# ======================================================================
#  What the run reports about itself
# ======================================================================


class TheRunSaysWhatItDid(Contracted):
    """Measured on a real run, not on a constructed report.

    A delivered and invoiced job is everything this loop can do: the
    money arrives on the client's schedule and the reconciliation task
    records it. ``RunReport.completed`` counts cycles that went
    through payment, so on a shop that bills its customers honestly it
    is zero on every run -- and it was the only count the summary
    published. The runtime therefore reported ``0/1 completed`` for a
    job it had delivered, invoiced and counted productive.
    """

    def a_delivered_job(self):
        pid, _ = self.ready()
        contracting.admit(self.s, prospect_id=pid, initiator="test")
        runtime = self.runtime()
        runtime.run_due()
        return runtime

    def test_a_delivered_job_is_reported_as_delivered(self):
        runtime = self.a_delivered_job()
        run = runtime._business

        self.assertEqual(run["attempted"], 1)
        self.assertEqual(run["delivered"], 1,
                         "the job was delivered and invoiced, and the run "
                         "reported that it had done nothing")
        self.assertGreater(run["committed_cents"], 0)

    def test_settlement_is_still_reported_and_still_separate(self):
        """Not conflated in the other direction either.

        The money has not arrived -- no payment was simulated -- so
        ``completed`` is zero, and it must stay zero. A loop that
        marked its own jobs settled to make the figure look better is
        the defect this count was protecting against.
        """
        run = self.a_delivered_job()._business

        self.assertEqual(run["completed"], 0)
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_a_job_that_never_billed_is_not_counted_as_delivered(self):
        """The control, and the reason `delivered` is not `attempted`.

        A run that refused every job it looked at must not report a
        full day's work. The Governor is made to refuse by raising the
        owner's margin floor out of reach, so the refusal is a real
        authority's and not a stub's.
        """
        pid, _ = self.ready()
        contracting.admit(self.s, prospect_id=pid, initiator="test")
        self.s.policy.amend({"financial": {"margin_floor": 0.999}},
                            OWNER, "the owner will not work for this")

        runtime = self.runtime()
        runtime.run_due()
        run = runtime._business

        self.assertEqual(run["attempted"], 1, "no job was attempted, so this "
                                              "test proves nothing")
        self.assertEqual(run["delivered"], 0)
        self.assertEqual(run["committed_cents"], 0)

    def test_the_audit_line_says_invoiced_not_collected(self):
        runtime = self.a_delivered_job()

        rows = [r for r in self.s.audit.events()
                if r.get("event") == "runtime.business_ran"]

        self.assertTrue(rows)
        said = rows[-1]["result"]
        self.assertIn("1 of 1 delivered", said)
        self.assertIn("invoiced", said)
        self.assertNotIn("collected", said)


if __name__ == "__main__":
    unittest.main()
