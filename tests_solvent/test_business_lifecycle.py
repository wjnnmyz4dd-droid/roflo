"""The whole business, end to end, for all five skills.

Section 6's sequence: discover, research, price against market
evidence, negotiate inside the floor, contract, take a 20% deposit,
admit the work through the *real* Governor and Action Gate, execute a
*real* worker, verify with the *real* checker, deliver, invoice,
reconcile, update the CRM, schedule a follow-up.

What is simulated and what is not matters more than the sequence.
Simulated: the client, their replies, the market observations, and the
payment events -- all external. Real: every DeskPilot component
downstream of the agreed price. The production path is reused exactly
as `test_production_path.py` drives it, which is through the scheduled
business task with nothing substituted.

The Chief of Staff appears here as what it is: a layer that says what
to do next and then watches the existing authorities do it.
"""

from __future__ import annotations

import os
import unittest

from solvent.chiefofstaff import ChiefOfStaff, Step
from solvent.crm import CRM, Basis, Contact, Evidence, Standing
from solvent.engagement import (
    Acceptance, Deliverable, Engagement, Proposal, Stage,
)
from solvent.errors import FailClosed
from solvent.marketrates import Basis as RateBasis
from solvent.marketrates import MarketEvidence, Observation
from solvent.negotiation import Costs, Move, Negotiation, NegotiationPolicy, Outcome
from solvent.outreach import Campaign, Draft, Reply, Suppressions, classify
from tests_solvent.test_production_path import ProductionPath, SKILLS

TODAY = "2026-10-22"
SEEN = "2026-10-01"

#: One synthetic company per target industry, each matched to a skill.
CLIENTS = (
    ("construction", "TX", "Lone Star Framing", "csv-cleanup"),
    ("real-estate", "CA", "Bay Property Group", "data-entry"),
    ("accounting", "NY", "Hudson Books", "document-processing"),
    ("property-management", "FL", "Sunshine Residential", "report-builder"),
    ("general-small-business", "OH", "Buckeye Supplies",
     "spreadsheet-automation"),
)


def market(service, *, transacted=6, cents=30_000, units=1_000):
    store = MarketEvidence()
    for i in range(transacted):
        store.record(Observation(service=service, cents=cents + i * 2_000,
                                 basis=RateBasis.TRANSACTED, scope_units=units,
                                 unit="unit", source="published rate guide",
                                 seen_on=SEEN))
    return store.band(service, today=TODAY)


COSTS = Costs(service_cents=4_000, ai_cents=500, verification_cents=800,
              expected_revision_cents=700, payment_processing_cents=1_200,
              risk_allowance_cents=800)


class TheWholeLifecycle(ProductionPath):
    """One run per industry, through the real engine at the end."""

    def setUp(self):
        super().setUp()
        self.crm = CRM()
        self.suppressions = Suppressions()
        self.cos = ChiefOfStaff(crm=self.crm, suppressions=self.suppressions,
                                campaign=Campaign())
        self.policy = NegotiationPolicy(minimum_margin_fraction=0.35,
                                        max_discount_fraction=0.25,
                                        owner_signs_above_cents=10_000_000)

    # ---- the front half, per client ----------------------------------

    def acquire(self, industry, state, company, service):
        """Discover through to an agreed price. Steps 1 to 10."""
        pid = f"p-{service}"
        self.crm.discover(prospect_id=pid, company=company,
                          industry=industry, state=state)
        self.assertIs(self.cos.next_step(pid).step, Step.RESEARCH)

        self.crm.add_evidence(pid, Evidence(
            f"Advertised twice this year for help with {service}",
            Basis.PUBLISHED, "their careers page", SEEN))
        self.assertIs(self.cos.next_step(pid).step, Step.QUALIFY)

        self.crm.add_contact(pid, Contact(
            "Dana", f"ops@{service}.example", "operations",
            "their careers page", SEEN))
        self.crm.advance(pid, Standing.QUALIFIED, f"fits {service}")
        self.assertIs(self.cos.next_step(pid, today=TODAY).step,
                      Step.PREPARE_OUTREACH)

        draft = Draft(to=f"ops@{service}.example",
                      subject=f"{company}: a hand with {service}",
                      body="A short, specific note.", prospect_id=pid)
        self.assertIn("STOP", draft.rendered())
        self.cos.campaign.record(draft.to, "first_contact", on="2026-10-02")
        self.crm.advance(pid, Standing.CONTACTED, "first contact prepared")

        self.assertIs(classify("This sounds interesting, how much?"),
                      Reply.INTERESTED)
        self.crm.advance(pid, Standing.REPLIED, "they asked for a price")

        band = market(service)
        self.assertTrue(band.usable, f"no market band for {service}")
        deal = Negotiation(pid, service, COSTS, self.policy,
                           scope_units=1_000, band=band)
        self.cos.negotiations[pid] = deal
        opened = deal.open()
        self.assertGreaterEqual(opened.cents, deal.floor)

        step = deal.respond(int(opened.cents * 0.85))
        self.assertIs(step.move, Move.CONCEDED)
        self.assertIs(deal.outcome, Outcome.AGREED_PENDING_AUTHORISATION)
        return pid, deal

    def contract(self, pid, deal, service):
        """Steps 11 to 15: contract, acceptance, deposit, verified money."""
        proposal = Proposal(
            proposal_id=f"pr-{service}", prospect_id=pid, service=service,
            price_cents=deal.current_cents,
            deliverables=(Deliverable(f"a finished {service} output",
                                      "parses_as_csv", "it opens"),),
            turnaround_days=5, revisions_included=1)
        engagement = Engagement(f"e-{service}", proposal)
        self.cos.engagements[pid] = engagement
        engagement.send()

        self.assertIs(self.cos.next_step(pid).step, Step.PROPOSE)
        engagement.accept(Acceptance("Dana", "2026-10-20", "reply to our email",
                                     f"sha256:{service}"))
        self.assertIs(self.cos.next_step(pid).step, Step.REQUEST_DEPOSIT)

        request = engagement.request_deposit()
        self.assertEqual(request["fraction"], 0.20)
        self.assertIs(self.cos.next_step(pid).step, Step.AWAIT_DEPOSIT)
        self.assertFalse(engagement.work_authorised)

        engagement.record_payment({"verified": True,
                                   "cents": request["amount_cents"],
                                   "external_ref": f"pi_dep_{service}"})
        self.assertTrue(engagement.work_authorised)
        self.assertIs(self.cos.next_step(pid).step, Step.ADMIT_WORK)
        return engagement

    # ---- the tests ------------------------------------------------------

    def test_all_five_industries_reach_authorised_work(self):
        for industry, state, company, service in CLIENTS:
            with self.subTest(industry=industry, service=service):
                pid, deal = self.acquire(industry, state, company, service)
                engagement = self.contract(pid, deal, service)
                self.assertIs(engagement.stage, Stage.DEPOSIT_RECEIVED)
                self.assertGreater(deal.contribution_at(deal.current_cents), 0)

    def test_the_real_engine_executes_the_authorised_work(self):
        """Steps 16 to 21, through the production path with no substitutes."""
        for capability in SKILLS:
            self.place(capability, order_id=f"ord-{capability}")
        runtime = self.runtime()
        for _ in range(5):
            self.tick(runtime)

        states = sorted(j.state.value for j in self.s.orchestrator.jobs())
        self.assertEqual(len(states), len(SKILLS))
        self.assertTrue(all(s in ("AWAITING_PAYMENT", "PAID", "COMPLETE")
                            for s in states), states)
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)
        self.assertTrue(self.s.policy.get("egress", "simulation_only",
                                          default=True))

    def test_the_back_half_settles_and_updates_the_crm(self):
        """Steps 21 to 25: invoice, reconcile, CRM, follow-up."""
        pid, deal = self.acquire(*CLIENTS[0])
        engagement = self.contract(pid, deal, "csv-cleanup")
        engagement.authorise_work().delivered()
        self.assertIs(self.cos.next_step(pid).step, Step.INVOICE)

        engagement.invoiced()
        self.assertIs(self.cos.next_step(pid).step, Step.CHASE_BALANCE)
        self.assertGreater(engagement.outstanding_cents, 0)

        engagement.record_payment({"verified": True,
                                   "cents": engagement.outstanding_cents,
                                   "external_ref": "pi_balance"})
        self.assertIs(engagement.stage, Stage.PAID)
        self.crm.advance(pid, Standing.PAID, "settled")
        self.assertIs(self.cos.next_step(pid).step, Step.IDENTIFY_RECURRING)

    def test_an_unprofitable_client_never_reaches_a_contract(self):
        pid, _ = self.acquire(*CLIENTS[0])
        deal = self.cos.negotiations[pid]
        deal.outcome = Outcome.OPEN
        step = deal.respond(deal.floor - 1)
        self.assertIs(step.move, Move.REFUSED)
        self.assertIs(self.cos.next_step(pid).step, Step.NOTHING)
        self.assertNotIn(pid, self.cos.engagements)

    def test_an_opt_out_mid_pipeline_stops_everything(self):
        pid, _ = self.acquire(*CLIENTS[1])
        self.suppressions.suppress(f"ops@data-entry.example", "replied STOP")
        self.crm.opt_out(pid, "replied STOP")
        self.assertIs(self.cos.next_step(pid, today=TODAY).step, Step.NOTHING)

    def test_the_performance_figures_separate_contracted_from_collected(self):
        pid, deal = self.acquire(*CLIENTS[0])
        self.contract(pid, deal, "csv-cleanup")
        figures = self.cos.performance()
        self.assertEqual(figures["engagements"], 1)
        self.assertGreater(figures["contracted_cents"],
                           figures["collected_cents"],
                           "a 20% deposit was reported as the whole price")
        self.assertGreater(figures["outstanding_cents"], 0)

    def test_nothing_in_the_coordinator_holds_an_authority(self):
        """The rule the whole layer depends on.

        Parsed, not grepped. The first version searched the module
        text and failed on its own docstring, which names the
        authorities in a table explaining that it does not own them --
        the same false positive as flagging a module for containing
        the string it exists to refuse. What matters is whether the
        code imports or constructs one, which is a question about the
        syntax tree.
        """
        import ast
        import pathlib as _p

        AUTHORITIES = {"FinancialGovernor", "ActionGate", "JobOrchestrator",
                       "Ledger", "OwnerChannel", "Qualification"}
        tree = ast.parse(_p.Path("solvent/chiefofstaff.py").read_text())

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.Import):
                imported |= {a.name.split(".")[-1] for a in node.names}
        self.assertFalse(imported & AUTHORITIES,
                         f"the coordinator imports {imported & AUTHORITIES}")

        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)}
        self.assertFalse(called & AUTHORITIES,
                         f"the coordinator constructs {called & AUTHORITIES}")

    def test_the_guard_above_can_actually_fail(self):
        """A guard that cannot fail is decoration."""
        import ast

        tree = ast.parse("from .governor import FinancialGovernor\n"
                         "g = FinancialGovernor()\n")
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported |= {a.name for a in node.names}
        self.assertIn("FinancialGovernor", imported)

    def test_the_coordinator_recommends_and_does_not_act(self):
        pid, _ = self.acquire(*CLIENTS[0])
        before = self.crm.get(pid).standing
        for _ in range(3):
            self.cos.next_step(pid, today=TODAY)
        self.assertIs(self.crm.get(pid).standing, before,
                      "asking what to do next changed something")


if __name__ == "__main__":
    unittest.main()
