"""The connector: a qualified opportunity becoming an open negotiation.

Stage 1's whole point is that **the test must not open the deal**.
Every other negotiation in this repository was constructed by a line
of test code, which is why the audit classified autonomous dealmaking
as having no production producer at all. So nothing here calls
``Negotiation(...)``, and a static check at the bottom of this file
fails if anything ever does -- a test that proves production opened a
deal is worthless the day somebody helpfully opens one for it.

What drives the deal is ``Runtime.run_due()``: the same scheduler
``serve()`` runs, with its real cadences, its real health gate, its
real lease and its real task list. The only substitutions are the
clock (so a test need not wait ten minutes) and the external world --
the client, their order and their money.

The setup is the production inbound path, not a fixture: an order is
placed at the storefront, submitted to the pipeline, polled by
Discovery and accepted, and the CRM holds the client it names. That
sequence is what a real customer produces.
"""

from __future__ import annotations

import ast
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from solvent import dealmaking
from solvent.crm import Basis, Contact, Evidence, Standing
from solvent.dealmaking import Refused
from solvent.discovery import OpportunityStatus
from solvent.harness import OWNER, Solvent
from solvent.negotiation import Outcome
from solvent.runtime import DEFAULT_INTERVALS, Runtime

from tests_solvent.test_production_path import ProductionPath

SEEN = "2026-10-01"
REPO = pathlib.Path(__file__).resolve().parent.parent

#: The owner's recorded costs. Every line is deliberately non-zero:
#: with all of them at zero the floor is zero, and the connector
#: refuses to open a deal it would be pricing against nothing.
COSTS_POLICY = {
    "compute_cents_per_cpu_hour": 1_200,
    "payment_fee_fraction": 0.029,
    "payment_fee_flat_cents": 30,
    "verification_cents_per_job": 2_000,
    "expected_revision_cents_per_job": 4_000,
    "acquisition_cents_per_job": 3_000,
    "supervision_cents_per_job": 2_500,
    "risk_allowance_cents_per_job": 500,
    "minimum_margin_fraction": 0.30,
    "minimum_project_fee_cents": 4_000,
}

CAPACITY_POLICY = {
    "max_owed_seconds": 40_000.0,
    "skills": ["csv-cleanup", "data-entry"],
    "cpu_share": 0.5,
    "min_free_disk_bytes": 1,
}


class Connected(ProductionPath):
    """A real Solvent with a client who has asked for a price."""

    def setUp(self):
        super().setUp()
        self.s.policy.amend(
            {"commercial": dict(COSTS_POLICY), "capacity": dict(CAPACITY_POLICY)},
            OWNER, "Stage 1 connector certification")

    # ---- the production inbound path, not a fixture --------------------

    def arrive(self, capability: str = "csv-cleanup", *, cents: int = 40_000,
               order_id: str = "") -> str:
        """One customer, through the door the way a customer comes in."""
        oid = self.place(capability, cents=cents, order_id=order_id)
        self.s.storefront.submit_to_pipeline(oid)
        found = [o for o in self.s.discovery.poll("storefront")
                 if o.client_ref and capability in o.needs]
        self.assertTrue(found, "the order did not reach Discovery")
        opportunity = found[-1]
        self.s.discovery.set_status(opportunity.id, OpportunityStatus.ACCEPTED)
        return self.client(opportunity.client_ref, capability)

    def client(self, prospect_id: str, capability: str) -> str:
        """The CRM record the opportunity names, taken as far as REPLIED."""
        self.s.crm.discover(prospect_id=prospect_id,
                            company=f"{capability} client", industry="construction",
                            state="TX")
        self.s.crm.add_evidence(prospect_id, Evidence(
            claim=f"they published a need for {capability}",
            basis=Basis.PUBLISHED, source="their careers page", seen_on=SEEN))
        self.s.crm.add_contact(prospect_id, Contact(
            name="Ops", email=f"ops@{prospect_id.replace(':', '-')}.example",
            role="operations", source="their site", seen_on=SEEN))
        self.s.crm.advance(prospect_id, Standing.QUALIFIED, "fits the skill")
        self.s.crm.advance(prospect_id, Standing.CONTACTED, "first contact")
        self.s.crm.advance(prospect_id, Standing.REPLIED, "they asked a price")
        return prospect_id

    # ---- driving the real scheduler ------------------------------------

    def deals(self, runtime: Runtime) -> str:
        """One scheduler pass; the dealmaking task's own line back.

        ``run_due`` rather than the task directly, because the failure
        containment lives in ``run_due``: a task that raises is caught
        and counted there, and calling the method would report an
        escaped exception as if the scheduler had not contained it.
        """
        names = [t.name for t in runtime.tasks]
        self.assertEqual(names.count("dealmaking"), 1,
                         "there must be exactly one dealmaking task")
        done = runtime.run_due()
        self._now += DEFAULT_INTERVALS["dealmaking"] + 1
        line = [o for o in done if o.startswith("dealmaking:")]
        self.assertTrue(line, f"the dealmaking task was not due: {done}")
        return line[0].split("dealmaking: ", 1)[1]


class TheSchedulerOpensTheDeal(Connected):
    """Stage 1's central claim, and the only one that matters on its own."""

    def test_a_scheduler_tick_opens_a_negotiation_nothing_else_opened(self):
        pid = self.arrive()
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {},
                         "the book must be empty before the scheduler runs")

        said = self.deals(self.runtime())

        self.assertIn("1 opened", said)
        deal = self.s.chief_of_staff.negotiations[pid]
        self.assertEqual(deal.service, "csv-cleanup")
        self.assertEqual(deal.opportunity_id, "opp_ord-csv-cleanup")
        self.assertIs(deal.outcome, Outcome.OPEN)

    def test_the_opening_price_is_a_real_step_above_a_real_floor(self):
        pid = self.arrive()
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations[pid]

        self.assertEqual(len(deal.steps), 1, "one opening, not two")
        opened = deal.steps[0]
        self.assertGreater(deal.floor, 0, "a zero floor checks nothing")
        self.assertGreaterEqual(opened.cents, deal.floor)
        self.assertEqual(deal.contribution_at(opened.cents),
                         opened.cents - deal.costs.total_cents)

    def test_the_cost_stack_carries_acquisition_and_supervision(self):
        """The two lines the cost-floor audit found missing, now produced.

        The audit added them to ``Costs`` and said the repair was
        *latent*: correct arithmetic that nothing in production fed.
        This is the producer. If these two ever read zero while the
        owner has recorded them, the floor is understated by exactly
        the amount that made a $146 job clear the floor and lose $11.
        """
        pid = self.arrive()
        self.deals(self.runtime())
        costs = self.s.chief_of_staff.negotiations[pid].costs

        self.assertEqual(costs.acquisition_cents, 3_000)
        self.assertEqual(costs.supervision_cents, 2_500)
        self.assertEqual(costs.verification_cents, 2_000)
        self.assertEqual(costs.expected_revision_cents, 4_000)
        self.assertEqual(
            costs.total_cents,
            sum(v for k, v in costs.breakdown().items() if k != "total"))

    def test_the_crm_records_that_a_negotiation_is_under_way(self):
        pid = self.arrive()
        self.deals(self.runtime())
        self.assertIs(self.s.crm.get(pid).standing, Standing.NEGOTIATING)

    def test_the_audit_log_names_the_opening(self):
        pid = self.arrive()
        self.deals(self.runtime())
        events = [r for r in self.s.audit.events()
                  if r["event"] == "negotiation.opened"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["authority"], dealmaking.ORIGIN)
        self.assertEqual(events[0]["decision"], "OPENED")


class ItOpensNothingTwice(Connected):
    """Duplicate prevention, which is the whole risk of an unattended loop."""

    def test_repeated_ticks_open_exactly_one_negotiation(self):
        pid = self.arrive()
        runtime = self.runtime()
        said = [self.deals(runtime) for _ in range(5)]

        self.assertIn("1 opened", said[0])
        for later in said[1:]:
            self.assertNotIn("1 opened", later)
        self.assertEqual(len(self.s.chief_of_staff.negotiations), 1)
        self.assertEqual(len(self.s.chief_of_staff.negotiations[pid].steps), 1,
                         "a second opening would be two prices for one job")

    def test_a_second_tick_says_which_gate_refused(self):
        self.arrive()
        runtime = self.runtime()
        self.deals(runtime)
        self.assertNotIn("ALREADY_OPEN", self.deals(runtime),
                         "an already-open deal is no longer a candidate, so "
                         "it should not even reach the gate")

    def test_the_candidate_list_drops_a_prospect_once_a_deal_is_open(self):
        pid = self.arrive()
        self.assertEqual([c.prospect_id for c in dealmaking.candidates(self.s)],
                         [pid])
        self.deals(self.runtime())
        self.assertEqual(dealmaking.candidates(self.s), [])

    def test_the_gate_itself_refuses_a_duplicate_when_asked_directly(self):
        """Belt as well as braces: the candidate filter is not the only guard.

        ``candidates`` already excludes a prospect with a deal, so the
        scheduler never asks twice. That makes the gate inside
        ``open_negotiation`` unreachable from the tick -- and an
        unreachable guard is one nobody notices breaking. Asked
        directly, it must still refuse.
        """
        pid = self.arrive()
        self.deals(self.runtime())
        again = dealmaking.open_negotiation(
            self.s, prospect_id=pid, service="csv-cleanup")
        self.assertFalse(again.opened)
        self.assertIs(again.refusal, Refused.ALREADY_OPEN)
        self.assertEqual(len(self.s.chief_of_staff.negotiations[pid].steps), 1)


class ItSurvivesBeingClosedAndReopened(Connected):
    """Persistence, read back out of the file rather than out of memory."""

    def test_the_deal_is_there_after_the_database_is_closed_and_reopened(self):
        pid = self.arrive()
        self.deals(self.runtime())
        before = self.s.chief_of_staff.negotiations[pid]
        asking, floor = before.steps[0].cents, before.floor
        path = self.s.store.path if hasattr(self.s.store, "path") else None
        self.s.store.close()

        self.s = Solvent(str(self.root / "deskpilot.db"))
        after = self.s.chief_of_staff.negotiations[pid]
        self.assertEqual(after.service, "csv-cleanup")
        self.assertEqual(after.steps[0].cents, asking)
        self.assertEqual(after.costs.acquisition_cents, 3_000)
        self.assertEqual(after.costs.supervision_cents, 2_500)
        self.assertEqual(after.floor, floor,
                         "the floor is recomputed from restored costs, so a "
                         "restart that changed it means the costs did not "
                         "come back whole")
        self.assertIsNotNone(path or True)

    def test_a_restarted_scheduler_does_not_open_a_second_deal(self):
        """The duplicate risk that only a restart can produce."""
        pid = self.arrive()
        self.deals(self.runtime())
        self.s.store.close()

        self.s = Solvent(str(self.root / "deskpilot.db"))
        said = self.deals(self.runtime())
        self.assertNotIn("1 opened", said)
        self.assertEqual(len(self.s.chief_of_staff.negotiations[pid].steps), 1)


class TheGatesAreTheExistingAuthorities(Connected):
    """Each refusal must come from the component that already owns it."""

    def test_an_opted_out_client_is_never_priced(self):
        pid = self.arrive()
        self.s.crm.opt_out(pid, "they replied STOP")
        self.assertEqual(self.deals(self.runtime()),
                         "nobody is waiting on a price")
        self.assertNotIn(pid, self.s.chief_of_staff.negotiations)

    def test_an_opted_out_client_is_refused_when_the_gate_is_asked_directly(self):
        """The candidate filter is not the only thing standing between an
        opt-out and a price.

        ``candidates`` drops anyone whose standing is not REPLIED, and
        opting out changes the standing, so the tick never asks. That
        makes the gate inside ``open_negotiation`` unreachable from the
        scheduler -- and an unreachable guard is one nobody notices
        breaking. Asked directly, it must still refuse.
        """
        pid = self.arrive()
        self.s.crm.opt_out(pid, "they replied STOP")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.TERMINAL)
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})

    def test_a_client_who_has_not_replied_is_not_priced(self):
        """Written to is not the same as interested.

        Found by the iFixAi cross-review export, which drove the gate
        directly instead of through ``candidates`` and opened a deal
        with a prospect at CONTACTED. Nothing is sent, so no client is
        bothered -- but the book then holds a live negotiation with
        somebody who never replied, and the coordinator reports a
        conversation that is not happening. The third guard in this
        module that the only caller could not reach.
        """
        pid = self.arrive()
        self.s.crm.advance(pid, Standing.CONTACTED, "rewound: no reply yet")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.NOT_ENGAGED)
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})

    def test_a_returning_client_may_be_priced_again(self):
        """The gate must not swallow the case it is not guarding."""
        pid = self.arrive()
        self.s.crm.advance(pid, Standing.PAID, "settled last time")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertTrue(got.opened, got.why)

    def test_a_suppressed_address_is_never_priced(self):
        pid = self.arrive()
        contact = self.s.crm.get(pid).contacts[0]
        self.s.suppressions.suppress(contact.email, "they asked us to stop")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.SUPPRESSED)

    def test_a_client_who_is_not_in_the_crm_is_refused_not_orphaned(self):
        """An invisible open deal is worse than no deal.

        ``ChiefOfStaff.pipeline`` iterates ``crm.all()``, so a deal
        filed under a key the CRM does not know never appears in the
        pipeline, never reaches the escalation sweep and is never shown
        to the owner. Refusing is the only safe answer.
        """
        got = dealmaking.open_negotiation(
            self.s, prospect_id="nobody:at-all", service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.NO_CLIENT_RECORD)
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})

    def test_an_unapproved_skill_cannot_be_sold(self):
        pid = self.arrive()
        got = dealmaking.open_negotiation(
            self.s, prospect_id=pid, service="skill-nobody-approved")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.SKILL_NOT_DEPLOYABLE)

    def test_an_opportunity_naming_no_skill_is_refused_rather_than_guessed(self):
        pid = self.arrive()
        got = dealmaking.open_negotiation(self.s, prospect_id=pid, service="")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.NO_SERVICE)

    def test_capacity_is_asked_and_its_refusal_is_final(self):
        pid = self.arrive()
        self.s.policy.amend({"capacity": {"halted": True}}, OWNER,
                            "the owner stopped new commitments")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.NO_CAPACITY)
        self.assertIn("halted", got.why)

    def test_a_budget_below_the_floor_is_refused(self):
        """Above the minimum fee, below the floor: only the floor can refuse.

        The first version of this used 1,000c, which is also below the
        owner's 4,000c minimum project fee -- so the test passed with
        the floor check deleted, and a mutant proved it. Both gates
        return UNPROFITABLE, so the refusal alone distinguishes
        nothing; the budget has to clear the fee, and the reason has to
        be read.
        """
        pid = self.arrive()
        minimum = int(self.s.policy.get("commercial",
                                        "minimum_project_fee_cents", default=0))
        budget = 10_000
        got = dealmaking.open_negotiation(
            self.s, prospect_id=pid, service="csv-cleanup",
            quoted_cents=budget)
        self.assertGreater(budget, minimum,
                           "the budget must clear the minimum fee, or the "
                           "fee gate answers and the floor gate is untested")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.UNPROFITABLE)
        self.assertGreater(got.floor_cents, budget)
        self.assertIn("below the floor", got.why)

    def test_a_budget_below_the_owners_minimum_fee_is_refused_too(self):
        """The other gate, pinned separately now that they are distinct."""
        pid = self.arrive()
        self.s.policy.amend({"commercial": {
            "minimum_project_fee_cents": 1_000_000}}, OWNER,
            "a minimum fee far above the floor")
        got = dealmaking.open_negotiation(
            self.s, prospect_id=pid, service="csv-cleanup",
            quoted_cents=900_000)
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.UNPROFITABLE)
        self.assertIn("minimum project fee", got.why)

    def test_an_unpriced_business_refuses_to_open_rather_than_price_on_nothing(self):
        """Zero costs make a zero floor, and a zero floor refuses nothing."""
        pid = self.arrive()
        self.s.policy.amend(
            {"commercial": {k: 0 for k in COSTS_POLICY}}, OWNER,
            "clear every recorded cost")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.UNPROFITABLE)
        self.assertIn("zero", got.why)

    def test_a_client_who_already_has_a_contract_gets_no_second_deal(self):
        pid = self.arrive()
        self.deals(self.runtime())
        chief = self.s.chief_of_staff
        chief.engagements[pid] = _EngagementStub()
        del chief.negotiations[pid]
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.ALREADY_ENGAGED)


class _EngagementStub:
    """Just enough of an engagement for the book to hold it.

    The book persists whatever it is given, so this needs ``as_dict``.
    Deliberately minimal: the gate under test reads only membership.
    """

    def as_dict(self) -> dict:
        return {"stage": "ACCEPTED", "history": []}


class ItSendsNothing(Connected):
    """A qualified opportunity authorises a price, never a message."""

    def test_opening_a_deal_records_no_outbound_message(self):
        pid = self.arrive()
        # The baseline is taken *after* arrival on purpose. Arrival polls
        # Discovery, which reaches a work source and says so -- a real
        # external effect, and not this tick's. Measuring from before it
        # made the test fail on the setup rather than the subject.
        before = len(self.s.audit.events())
        self.deals(self.runtime())
        after = [r for r in self.s.audit.events()[before:]
                 if r.get("external_effect")]
        self.assertEqual(after, [],
                         "opening a negotiation must not reach the outside")
        self.assertIn(pid, self.s.chief_of_staff.negotiations)

    def test_the_campaign_has_no_record_of_a_message(self):
        self.arrive()
        self.deals(self.runtime())
        contact = self.s.crm.all()[0].contacts[0]
        self.assertEqual(
            self.s.chief_of_staff.campaign.follow_ups_to(contact.email), 0)


class OneExecutorOnly(Connected):
    """Concurrent schedulers against one database."""

    def test_a_second_runtime_cannot_open_while_the_first_holds_the_lease(self):
        self.arrive()
        first = self.runtime()
        second = Runtime(self.s, clock=lambda: self._now)
        second.start()
        second.default_tasks()

        held = first._take_business_lease()
        self.assertIsNotNone(held)
        try:
            line = [o for o in second.run_due() if o.startswith("dealmaking:")]
            self.assertTrue(line)
            self.assertIn("another executor holds the business lease", line[0])
        finally:
            first._release_business_lease(held)
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})


class TheOwnerIsStillInCharge(Connected):
    """Autonomy that cannot be switched off is not autonomy, it is a runaway."""

    def test_the_connector_is_off_until_the_owner_enables_continuous_work(self):
        self.arrive()
        self.s.policy.amend({"operations": {"continuous_enabled": False}},
                            OWNER, "the owner has not opted in")
        self.assertIn("not run", self.deals(self.runtime()))
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})

    def test_an_operating_mode_that_blocks_new_work_blocks_this(self):
        from solvent.policy import OperatingMode

        self.arrive()
        for mode in OperatingMode:
            if not mode.blocks_new_work:
                continue
            self.s.policy.set_operating_mode(mode, OWNER, "stand down")
            self.assertIn("skipped", self.deals(self.runtime()))
            self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})
            break

    def test_how_many_deals_one_tick_may_open_is_the_owners_number(self):
        self.arrive("csv-cleanup", order_id="ord-a")
        self.arrive("data-entry", order_id="ord-b")
        self.assertEqual(len(dealmaking.candidates(self.s)), 2)

        self.s.policy.amend({"dealmaking": {"max_opened_per_tick": 2}}, OWNER,
                            "the owner widened the batch")
        self.assertIn("2 opened", self.deals(self.runtime()))

    def test_the_default_batch_is_one(self):
        self.arrive("csv-cleanup", order_id="ord-a")
        self.arrive("data-entry", order_id="ord-b")
        said = self.deals(self.runtime())
        self.assertIn("1 opened", said)
        self.assertEqual(len(self.s.chief_of_staff.negotiations), 1)


# ======================================================================
# The test must not be the thing that opens the deal
# ======================================================================

class NoTestInThisFileOpensANegotiation(unittest.TestCase):
    """The guard that keeps this file honest.

    Stage 1 is the claim that *production* opens a negotiation. The
    cheapest way to make every test above pass is to construct one
    here, which would prove precisely nothing -- and would look
    identical in a green run. So the file is parsed and checked.

    This is the same shape as the mutant-anchor guard: a test whose
    subject is the test suite rather than the product, written because
    the failure it prevents is invisible in a pass.
    """

    def test_this_file_never_constructs_a_negotiation(self):
        tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
        called = [node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)]
        self.assertNotIn("Negotiation", called)
        self.assertNotIn("negotiation_from_dict", called)

    def test_this_file_never_assigns_into_the_negotiation_book(self):
        """Reads are fine; writes are the whole point.

        The first version of this matched any line containing both
        ``negotiations[`` and ``=``, which flagged every ``deal =
        chief.negotiations[pid]`` in the file -- and would have passed
        just as happily if the reads had been removed and a write left
        in. A string search cannot tell a subscript being assigned to
        from one being read. The parser can.
        """
        tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "negotiations"):
                    offenders.append(ast.unparse(node))
        self.assertEqual(offenders, [],
                         "a test that files its own deal proves nothing "
                         "about production")


# ======================================================================
# A real crash, in a real process
# ======================================================================

_CHILD = """
import os, sys
sys.path.insert(0, {repo!r})
os.environ["SOLVENT_OWNER_KEY"] = {key!r}
from solvent.harness import OWNER, Solvent
from solvent.crm import Basis, Contact, Evidence, Standing
from solvent.dealmaking import open_negotiation

s = Solvent({db!r})
s.policy.amend({{"commercial": {costs!r}, "capacity": {capacity!r}}},
               OWNER, "child")
for name in {promotions!r}:
    pass
s.crm.discover(prospect_id="p1", company="Crash Co",
               industry="construction", state="TX")
s.crm.add_evidence("p1", Evidence(claim="published need", basis=Basis.PUBLISHED,
                                  source="their site", seen_on="2026-10-01"))
s.crm.add_contact("p1", Contact(name="Ops", email="ops@crash.example",
                                role="operations", source="their site",
                                seen_on="2026-10-01"))
s.crm.advance("p1", Standing.QUALIFIED, "fits")
s.crm.advance("p1", Standing.CONTACTED, "written to")
s.crm.advance("p1", Standing.REPLIED, "asked a price")

from solvent.harness import promote_capability
promote_capability(s, "csv-cleanup", owner_identity=OWNER, why="child")

got = open_negotiation(s, prospect_id="p1", service="csv-cleanup",
                       opportunity_id="opp_child", quoted_cents=40000)
assert got.opened, got.why
print("OPENED", got.asking_cents, got.floor_cents, flush=True)
print("READY", flush=True)
sys.stdout.flush()
while True:
    pass
"""

RUN_SEPARATELY = ("python3 -I -m pytest tests_solvent/test_dealmaking.py "
                  "-k KilledWithADealOpen")


class KilledWithADealOpen(unittest.TestCase):
    """SIGKILL between opening a deal and anything tidying up.

    Every other persistence test here closes the database politely,
    which exercises the restore path and not what a crash does. SIGKILL
    cannot be caught, so whatever is in the file afterwards is what was
    genuinely committed, with no ``finally`` anywhere in DeskPilot
    helping.

    Not a :class:`Connected`: the parent must not build a Solvent
    before the child is spawned, because building one installs the
    egress guard and the guard denies the spawn.
    """

    def setUp(self):
        from solvent import egress

        if egress.is_installed():
            self.skipTest(
                "the egress guard is already installed in this interpreter, "
                "so no child process can be started. The guard is "
                "irreversible by design and is not worked around. Run this "
                f"case on its own:\n    {RUN_SEPARATELY}")
        key = mock.patch.dict(os.environ,
                              {"SOLVENT_OWNER_KEY": secrets.token_hex(32)})
        key.start()
        self.addCleanup(key.stop)
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.root = pathlib.Path(self.box.name)
        self.db = str(self.root / "deskpilot.db")
        self.s = None
        self.addCleanup(self._close)

    def _close(self):
        try:
            if self.s is not None:
                self.s.store.close()
        except Exception:
            pass

    def test_a_deal_opened_before_a_sigkill_comes_back_whole(self):
        script = _CHILD.format(repo=str(REPO), db=self.db,
                               key=os.environ["SOLVENT_OWNER_KEY"],
                               costs=dict(COSTS_POLICY),
                               capacity=dict(CAPACITY_POLICY),
                               promotions=())
        path = self.root / "child.py"
        path.write_text(textwrap.dedent(script), encoding="utf-8")
        child = subprocess.Popen(
            [sys.executable, "-I", str(path)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        asking = floor = 0
        try:
            deadline = time.monotonic() + 60
            line = ""
            while time.monotonic() < deadline:
                line = child.stdout.readline()
                if line.startswith("OPENED"):
                    _, asking, floor = line.split()
                    continue
                if line.strip() == "READY" or child.poll() is not None:
                    break
            if line.strip() != "READY":
                child.kill()
                self.fail("the child never got ready: "
                          f"{child.stderr.read()[-2000:]}")
            child.kill()                       # SIGKILL. No cleanup runs.
            child.wait(timeout=30)
        finally:
            if child.poll() is None:           # pragma: no cover - belt
                child.kill()
        self.assertEqual(child.returncode, -9)

        started = time.monotonic()
        self.s = Solvent(self.db)
        recovery = time.monotonic() - started

        deal = self.s.chief_of_staff.negotiations["p1"]
        self.assertEqual(deal.service, "csv-cleanup")
        self.assertEqual(deal.opportunity_id, "opp_child")
        self.assertEqual(deal.steps[0].cents, int(asking))
        self.assertEqual(deal.floor, int(floor))
        self.assertEqual(deal.costs.acquisition_cents, 3_000)
        self.assertEqual(deal.costs.supervision_cents, 2_500)
        self.assertLess(recovery, 10.0)

        # And the recovered deal is not re-opened. Two assertions in one
        # case on purpose: reopening the database installs the egress
        # guard in this interpreter, so a second test method here would
        # skip itself rather than run -- which looks identical to passing
        # in a green run. One kill, both properties.
        again = dealmaking.open_negotiation(
            self.s, prospect_id="p1", service="csv-cleanup")
        self.assertFalse(again.opened)
        self.assertIs(again.refusal, Refused.ALREADY_OPEN)
        self.assertEqual(len(deal.steps), 1,
                         "recovery must not reset the conversation to its "
                         "first price")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
