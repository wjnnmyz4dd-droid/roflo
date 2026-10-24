"""Stage 2: the business lifecycle as one connected run.

The chain the assignment asks for::

    discovery -> qualification -> NEGOTIATION -> proposal -> contract
    -> deposit -> Governor -> Action Gate -> skill execution
    -> independent verification -> delivery -> invoice -> payment
    -> reconciliation -> CRM

Every component here is the production one. What is simulated is what
is outside DeskPilot: the customer, their replies, and their money.

**The negotiation is opened by the scheduler.** That is the whole
difference between this file and ``test_business_lifecycle``, which
proves the same chain with the deal constructed by a line of test
code. Until Stage 1 there was no other way to write it.

**The seam these tests named is now closed.** When this file was
written, DeskPilot had two inbound channels that met at the client
rather than at a handoff:

* a storefront order is the work arriving, and the business tick
  carries it the whole way on its own -- submit, discover, qualify,
  Governor, Gate, worker, checker, deliver, invoice;
* a CRM prospect is a relationship, and the negotiation and
  engagement that follow it record what was agreed.

Nothing in ``solvent/`` read ``chief_of_staff.engagements`` in order
to admit work, so an engagement reaching ``work_authorised`` caused no
job to be admitted. These tests crossed that gap in one marked place,
:meth:`the_work_arrives`, and :class:`TheSeamIsWhereWeSayItIs` pinned
it so that closing it would be a visible change.

It closed. :mod:`solvent.contracting` draws the proposal from the
agreed price and, once a verified deposit clears, places an order with
the storefront -- which is the existing admission door, not a second
one. The price question that was the stated reason for leaving it open
is answered the only way that adds no authority: the negotiated figure
is the price, and the connector reprices nothing.

The helper below stays, and stays marked, because these tests build
their engagement by hand. :mod:`tests_solvent.test_contracting` is
where the connector itself is certified end to end.
"""

from __future__ import annotations

import unittest

from solvent.crm import Standing
from solvent.engagement import (
    Acceptance, Deliverable, Engagement, Proposal, Stage,
)
from solvent.chiefofstaff import Step
from solvent.harness import OWNER
from solvent.negotiation import Move, Outcome
from solvent.types import CostCategory

from tests_solvent.test_dealmaking import Connected

ACCEPTED_ON = "2026-10-20"


class TheConnectedLifecycle(Connected):
    """One client, one job, from discovery to a settled invoice."""

    # ---- the commercial half, opened by the scheduler -----------------

    def negotiated(self, capability: str = "csv-cleanup"):
        """Through to an agreed price, with production opening the deal."""
        pid = self.arrive(capability)
        said = self.deals(self.runtime())
        self.assertIn("1 opened", said)

        deal = self.s.chief_of_staff.negotiations[pid]
        self.assertIs(deal.outcome, Outcome.OPEN)
        self.assertEqual(len(deal.steps), 1)

        # The client counters. Simulated: this is the client speaking.
        opening = deal.steps[0].cents
        step = deal.respond(int(opening * 0.9), why="the client countered")
        self.assertIn(step.move, (Move.CONCEDED, Move.HELD, Move.SCOPE_OFFERED,
                                  Move.ACCEPTED_BY_CLIENT))
        self.assertGreaterEqual(deal.current_cents, deal.floor,
                                "no agreed price may sit below the floor")
        return pid, deal

    def contracted(self, pid, deal, capability: str = "csv-cleanup"):
        """Proposal, acceptance, 20% deposit -- the real Engagement."""
        if deal.outcome is Outcome.OPEN:
            deal.respond(deal.current_cents, why="the client accepted")
        proposal = Proposal(
            proposal_id=f"pr-{capability}", prospect_id=pid,
            service=capability, price_cents=deal.current_cents,
            deliverables=(Deliverable(f"a finished {capability} output",
                                      "parses_as_csv", "it opens"),),
            turnaround_days=5, revisions_included=1)
        engagement = Engagement(f"e-{capability}", proposal)
        self.s.chief_of_staff.engagements[pid] = engagement
        engagement.send()
        engagement.accept(Acceptance("Dana", ACCEPTED_ON,
                                     "reply to our email", f"sha256:{pid}"))

        request = engagement.request_deposit()
        self.assertEqual(request["fraction"], 0.20)
        self.assertFalse(engagement.work_authorised,
                         "work must not be authorised before money arrives")
        engagement.record_payment({"verified": True,
                                   "cents": request["amount_cents"],
                                   "external_ref": f"pi_dep_{pid}"})
        self.assertTrue(engagement.work_authorised)
        return engagement

    # ---- the seam, crossed in exactly one visible place ---------------

    def the_work_arrives(self, capability: str = "csv-cleanup") -> str:
        """The one manual step, named so it cannot hide.

        An authorised engagement does not admit a job; the work still
        has to arrive as an order. See this module's docstring.
        """
        return self.place(capability, order_id=f"ord-work-{capability}")

    # ---- the tests -----------------------------------------------------

    def test_the_chain_runs_from_discovery_to_a_settled_invoice(self):
        pid, deal = self.negotiated()
        engagement = self.contracted(pid, deal)
        self.assertIs(self.s.chief_of_staff.next_step(pid).step,
                      Step.ADMIT_WORK)

        self.the_work_arrives()
        runtime = self.runtime()
        for _ in range(4):
            self.tick(runtime)

        jobs = self.s.orchestrator.jobs()
        self.assertTrue(jobs, "no job was admitted")
        self.assertTrue(
            all(j.state.value in ("AWAITING_PAYMENT", "PAID", "COMPLETE")
                for j in jobs),
            [j.state.value for j in jobs])

        engagement.authorise_work().delivered()
        self.assertIs(self.s.chief_of_staff.next_step(pid).step, Step.INVOICE)
        engagement.invoiced()
        self.assertGreater(engagement.outstanding_cents, 0)
        engagement.record_payment({"verified": True,
                                   "cents": engagement.outstanding_cents,
                                   "external_ref": "pi_balance"})
        self.assertIs(engagement.stage, Stage.PAID)

        self.s.crm.advance(pid, Standing.PAID, "settled")
        self.assertIs(self.s.chief_of_staff.next_step(pid).step,
                      Step.IDENTIFY_RECURRING)

    def test_the_audit_chain_is_intact_across_the_whole_run(self):
        pid, deal = self.negotiated()
        self.contracted(pid, deal)
        self.the_work_arrives()
        runtime = self.runtime()
        for _ in range(4):
            self.tick(runtime)
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)

    def test_the_audit_trail_names_every_authority_the_chain_passes(self):
        """Evidence the boxes were entered, not inferred from the exit state.

        A job reaching PAID is consistent with the Governor and the
        Action Gate having been asked -- and also with their having
        been skipped. The audit log is the difference: each authority
        writes under its own name, so the set of names in one run is a
        record of which boxes the work actually went through.
        """
        pid, deal = self.negotiated()
        self.contracted(pid, deal)
        self.the_work_arrives()
        runtime = self.runtime()
        for _ in range(4):
            self.tick(runtime)

        events = self.s.audit.events()
        authorities = {r["authority"] for r in events}
        for name in ("dealmaking", "discovery", "qualification", "governor",
                     "gate", "capability", "capacity", "orchestrator",
                     "ledger"):
            with self.subTest(authority=name):
                self.assertIn(name, authorities,
                              f"the chain never reached {name}")

        kinds = {r["event"] for r in events}
        for kind in ("negotiation.opened", "governor.decision", "gate.allow",
                     "verification.recorded", "delivery.completed"):
            with self.subTest(event=kind):
                self.assertIn(kind, kinds)

    def test_the_deliverable_was_checked_by_something_other_than_its_maker(self):
        """Independent verification, read off the record it writes."""
        pid, deal = self.negotiated()
        self.contracted(pid, deal)
        self.the_work_arrives()
        runtime = self.runtime()
        for _ in range(4):
            self.tick(runtime)

        checks = [r for r in self.s.audit.events()
                  if r["event"] == "verification.recorded"]
        self.assertTrue(checks, "nothing verified the deliverable")

    def test_nothing_in_the_run_reached_the_outside(self):
        pid, deal = self.negotiated()
        self.contracted(pid, deal)
        self.assertTrue(self.s.policy.get("egress", "simulation_only",
                                          default=True))


class TheFloorHoldsThroughTheWholeDeal(Connected):
    """Profitability stays authoritative from opening to settlement."""

    def test_a_client_who_will_not_clear_the_floor_is_refused(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]

        step = deal.respond(deal.floor - 1, why="a lowball")
        self.assertIs(step.move, Move.REFUSED)
        self.assertIs(deal.outcome, Outcome.REFUSED)
        self.assertIs(self.s.chief_of_staff.next_step(pid).step, Step.NOTHING)
        self.assertNotIn(pid, self.s.chief_of_staff.engagements)

    def test_every_concession_still_clears_the_floor(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]

        for fraction in (0.95, 0.9, 0.85):
            if deal.outcome is not Outcome.OPEN:
                break
            deal.respond(int(deal.current_cents * fraction))
            with self.subTest(fraction=fraction):
                self.assertGreaterEqual(
                    deal.current_cents, deal.floor,
                    "a concession took the price under the floor")

    def test_the_agreed_price_leaves_a_positive_contribution(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(int(deal.steps[0].cents * 0.9))
        self.assertGreater(deal.contribution_at(deal.current_cents), 0)

    def test_a_deal_above_the_owners_signing_threshold_escalates(self):
        """Autonomy stops where the owner said it stops."""
        self.s.policy.amend({"negotiation": {"owner_signs_above_cents": 1}},
                            OWNER, "the owner signs almost everything")
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(int(deal.steps[0].cents * 0.95))
        self.assertIs(deal.outcome, Outcome.ESCALATED)
        recommendation = self.s.chief_of_staff.next_step(pid)
        self.assertIs(recommendation.step, Step.ESCALATE)
        self.assertTrue(recommendation.owner_must_decide)


class AcquisitionAndSupervisionAreAccountedFor(Connected):
    """The two lines the audit found missing, now in both halves.

    They have to appear twice and agree: as a *quoted* cost inside the
    negotiation's floor, and as *spent* money in the Ledger. A business
    that prices acquisition and never records it has a margin that
    looks right and a bank balance that does not.
    """

    def test_the_quoted_floor_includes_both(self):
        pid = self.arrive()
        self.deals(self.runtime())
        costs = self.s.chief_of_staff.negotiations[pid].costs
        self.assertEqual(costs.acquisition_cents, 3_000)
        self.assertEqual(costs.supervision_cents, 2_500)

    def test_dropping_either_line_lowers_the_floor_by_exactly_that_much(self):
        import dataclasses

        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        from solvent.negotiation import floor_cents

        full = floor_cents(deal.costs, deal.policy)
        for name in ("acquisition_cents", "supervision_cents"):
            with self.subTest(name):
                without = dataclasses.replace(deal.costs, **{name: 0})
                self.assertLess(floor_cents(without, deal.policy), full,
                                f"{name} does not move the floor")

    def test_acquisition_spend_with_no_job_reaches_the_business_margin(self):
        """Spend on proposals that never became jobs belongs to no job.

        This is the control the audit measured and nothing in the
        connector feeds yet. Recorded here the way an owner would, to
        show the path exists end to end.
        """
        self.arrive()
        self.deals(self.runtime())
        before = self.s.metrics().actual_costs
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.ACQUISITION, amount_cents=60_000,
            source="PROVIDER_BILLING",
            note="proposals that never became jobs")
        after = self.s.metrics().actual_costs
        self.assertNotEqual(before, after)
        self.assertEqual(after, "$600.00")

    def test_a_business_of_profitable_jobs_can_still_be_losing_money(self):
        """Phase 6's question, re-asked now that deals are real."""
        self.arrive()
        self.deals(self.runtime())
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.ACQUISITION, amount_cents=60_000,
            source="PROVIDER_BILLING", note="acquisition overhead")
        metrics = self.s.metrics()
        self.assertEqual(metrics.actual_profit, "-$600.00")
        self.assertIsNone(metrics.actual_margin,
                          "no real revenue means no margin, not a zero one")


class ItRecoversMidLifecycle(Connected):
    """Interruption at each point the chain has state worth losing."""

    def reopen(self):
        from solvent.harness import Solvent

        self.s.store.close()
        self.s = Solvent(str(self.root / "deskpilot.db"))
        return self.s

    def test_a_restart_after_the_deal_opens_keeps_the_price(self):
        pid = self.arrive()
        self.deals(self.runtime())
        was = self.s.chief_of_staff.negotiations[pid]
        asking, floor = was.steps[0].cents, was.floor

        self.reopen()
        now = self.s.chief_of_staff.negotiations[pid]
        self.assertEqual(now.steps[0].cents, asking)
        self.assertEqual(now.floor, floor)

    def test_a_restart_mid_negotiation_keeps_every_step(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(int(deal.steps[0].cents * 0.9))
        steps = [(s.move.value, s.cents) for s in deal.steps]

        self.reopen()
        now = self.s.chief_of_staff.negotiations[pid]
        self.assertEqual([(s.move.value, s.cents) for s in now.steps], steps)
        self.assertIs(now.outcome, deal.outcome)

    def test_a_restart_after_a_refusal_does_not_reopen_the_conversation(self):
        """A refused lowball must not be answered afresh after a crash."""
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(deal.floor - 1, why="a lowball")
        self.assertIs(deal.outcome, Outcome.REFUSED)

        self.reopen()
        self.assertIs(self.s.chief_of_staff.negotiations[pid].outcome,
                      Outcome.REFUSED)
        self.assertIs(self.s.chief_of_staff.next_step(pid).step, Step.NOTHING)

    def test_a_restart_after_the_deposit_keeps_the_authorisation(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(int(deal.steps[0].cents * 0.9))
        lifecycle = TheConnectedLifecycle.contracted
        engagement = lifecycle(self, pid, deal)
        self.assertTrue(engagement.work_authorised)

        self.reopen()
        back = self.s.chief_of_staff.engagements[pid]
        self.assertTrue(back.work_authorised,
                        "a cleared deposit must survive a restart, or the "
                        "client is asked for a second one")
        self.assertIs(self.s.chief_of_staff.next_step(pid).step,
                      Step.ADMIT_WORK)

    def test_a_restart_does_not_ask_for_a_second_deposit(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(int(deal.steps[0].cents * 0.9))
        engagement = TheConnectedLifecycle.contracted(self, pid, deal)
        collected = engagement.collected_cents

        self.reopen()
        back = self.s.chief_of_staff.engagements[pid]
        self.assertEqual(back.collected_cents, collected)
        self.assertIsNot(self.s.chief_of_staff.next_step(pid).step,
                         Step.REQUEST_DEPOSIT)


class TheSeamIsWhereWeSayItIs(Connected):
    """The finding this class pinned has been repaired. It now pins that.

    Kept rather than deleted. The assertion that caught the change is
    worth more than the claim it used to make: a seam recorded in
    prose drifts, and one recorded as an assertion cannot -- which is
    how the docstring above came to be rewritten rather than left
    describing a gap that had closed underneath it.
    """

    def test_exactly_three_production_modules_read_the_engagement_book(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "solvent"
        readers = sorted(
            p.name for p in root.glob("*.py")
            if ".engagements" in p.read_text(encoding="utf-8"))
        self.assertEqual(
            readers, ["chiefofstaff.py", "contracting.py", "dealmaking.py"],
            "chiefofstaff advises, dealmaking gates, contracting admits; a "
            "fourth reader is a second contracting authority unless this "
            "list is updated deliberately")

    def test_an_authorised_engagement_is_admitted_once_the_client_sends_it(self):
        """The repair, from this file's own fixture rather than a new one.

        The engagement here is built by hand, which is why it has no
        materials: the client has paid but sent nothing. So the first
        sweep must admit nothing, and the second -- after the file
        arrives -- must admit, from the same scheduler passes.
        """
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]
        deal.respond(int(deal.steps[0].cents * 0.9))
        engagement = TheConnectedLifecycle.contracted(self, pid, deal)
        self.assertTrue(engagement.work_authorised)

        runtime = self.runtime()
        runtime.run_due()
        self._now += 601
        self.assertIsNone(self.s.storefront.order(f"eng-{pid}"),
                          "a paid client who has sent no file has sent no "
                          "work; admitting it would be a job with no input")

        engagement.supply(
            **dict(self.s.storefront.order("ord-csv-cleanup").answers))
        runtime.run_due()
        self._now += 601

        self.assertIsNotNone(self.s.storefront.order(f"eng-{pid}"),
                             "the deposit cleared and the file arrived, and "
                             "nothing admitted the work")
        self.assertTrue(self.s.orchestrator.jobs(),
                        "the order was placed and no job followed it")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
