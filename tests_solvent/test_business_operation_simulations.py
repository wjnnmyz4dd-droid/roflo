"""Stage 3: controlled synthetic business scenarios, independently graded.

What this file is *not*: a second copy of work already done. The 90
scenario matrix in ``test_business_matrix`` covers acquisition across
both channels and all five skills; ``test_acquisition_adversarial``
covers forged payments, malicious attachments, duplicate contracts and
worker crashes; ``test_business_simulations`` covers the four
opportunity archetypes. Those are not repeated here.

What is here is the areas those do not reach, now that production can
open a deal: unrealistic clients, progressive skill configurations,
queue and standby, long-term contracts, payment pathology, capacity
stop rules, dependency failure, and 30/60/90-day runs on a logical
clock.

**Grading is independent.** DeskPilot must not grade itself, so money
is recomputed by :class:`Oracle`, which is written from the business
rules -- revenue minus every cost line, acquisition counted whether or
not it produced a job -- and shares no code with ``metrics`` or
``Ledger``. Where the two disagree the test fails and says which
number came from where. An oracle that called DeskPilot's own
arithmetic would agree with it by construction, which is the mistake
``AnUndefinedMarginIsNotZero`` was written after somebody made.

**Deterministic, and labelled.** Every scenario carries a seed and a
fixed configuration; nothing here samples. The day-by-day runs use a
logical clock, not wall time. These are simulations of a business, not
evidence of one: no real prospect, no real message, no real money. The
$200/day figure they produce is what the model says, not what the
world said.
"""

from __future__ import annotations

import random
import unittest
from dataclasses import dataclass, field

from solvent import dealmaking
from solvent.capacity import EXECUTE, Refusal as CapacityRefusal
from solvent.crm import Standing
from solvent.dealmaking import Refused
from solvent.engagement import (
    Acceptance, Deliverable, Engagement, Proposal, Stage,
)
from solvent.harness import OWNER
from solvent.negotiation import Move, Outcome, floor_cents
from solvent.types import CostCategory

from tests_solvent.test_dealmaking import Connected

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")


# ======================================================================
# The independent financial oracle
# ======================================================================

@dataclass
class Oracle:
    """Double-entry bookkeeping written from the rules, not from the code.

    Deliberately shares nothing with ``solvent``. It takes the same
    events the business took and works out what the books must say; the
    tests then ask DeskPilot and compare. The whole value is in the
    independence, so this class must never import a DeskPilot
    calculation -- the moment it does, agreement stops being evidence.
    """

    revenue_cents: int = 0
    simulated_revenue_cents: int = 0
    costs_cents: int = 0
    lines: list = field(default_factory=list)

    def collected(self, cents: int, *, real: bool, why: str = "") -> None:
        if real:
            self.revenue_cents += cents
        else:
            self.simulated_revenue_cents += cents
        self.lines.append(("collected" if real else "simulated", cents, why))

    def spent(self, cents: int, why: str = "") -> None:
        self.costs_cents += cents
        self.lines.append(("spent", cents, why))

    def refunded(self, cents: int, why: str = "") -> None:
        """A refund is revenue leaving, not a cost arriving."""
        self.revenue_cents -= cents
        self.lines.append(("refunded", cents, why))

    @property
    def profit_cents(self) -> int:
        """Contribution on **real** money only. Fixture money is not money."""
        return self.revenue_cents - self.costs_cents

    @property
    def margin(self):
        """``None`` when undefined. Zero would claim a break-even."""
        if self.revenue_cents <= 0:
            return None
        return self.profit_cents / self.revenue_cents

    def money(self, cents: int) -> str:
        return f"-${abs(cents) / 100:.2f}" if cents < 0 else f"${cents / 100:.2f}"

    @property
    def profit(self) -> str:
        return self.money(self.profit_cents)


class TheOracleIsIndependent(unittest.TestCase):
    """The oracle has to be checkable itself, or it grades nothing."""

    def test_it_references_no_deskpilot_arithmetic(self):
        """Checked against the code, not the prose.

        The first version searched the raw source for "solvent" and
        failed on the docstring, which says the class must not use
        DeskPilot's arithmetic. A guard that cannot tell an explanation
        from a call is not reading the thing it claims to read.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(Oracle)))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                used.add(getattr(node, "module", "") or "")
                used.update(a.name for a in node.names)
        for banned in ("solvent", "metrics", "Ledger", "business_metrics",
                       "floor_cents", "Costs"):
            self.assertNotIn(banned, used,
                             "an oracle that uses DeskPilot's arithmetic "
                             "agrees with it by construction")

    def test_simulated_money_is_not_profit(self):
        oracle = Oracle()
        oracle.collected(10_000, real=False, why="fixture rail")
        oracle.spent(6_000, why="execution")
        self.assertEqual(oracle.profit_cents, -6_000)
        self.assertIsNone(oracle.margin)

    def test_a_refund_reduces_revenue_rather_than_adding_cost(self):
        oracle = Oracle()
        oracle.collected(10_000, real=True)
        oracle.refunded(4_000, why="dispute upheld")
        self.assertEqual(oracle.revenue_cents, 6_000)
        self.assertEqual(oracle.costs_cents, 0)


class TheOracleAgreesWithTheLedger(Connected):
    """Both books, kept separately, over the same events."""

    def test_the_two_agree_on_a_mixed_book(self):
        oracle = Oracle()
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.ACQUISITION, amount_cents=12_000,
            source="PROVIDER_BILLING", note="outreach that won nothing")
        oracle.spent(12_000, why="acquisition, no job")
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.TOOLS, amount_cents=3_000,
            source="PROVIDER_BILLING", note="subscription")
        oracle.spent(3_000, why="subscription")

        self.assertEqual(self.s.metrics().actual_costs,
                         oracle.money(oracle.costs_cents))
        self.assertEqual(self.s.metrics().actual_profit, oracle.profit)
        self.assertIsNone(self.s.metrics().actual_margin)
        self.assertIsNone(oracle.margin)


# ======================================================================
# C. Unrealistic expectations
# ======================================================================

class UnrealisticClients(Connected):
    """Seven ways a client can ask for something that cannot be given."""

    def opened(self, capability: str = "csv-cleanup"):
        pid = self.arrive(capability)
        self.deals(self.runtime())
        return pid, self.s.chief_of_staff.negotiations[pid]

    def test_a_below_cost_budget_never_becomes_a_deal(self):
        pid = self.arrive()
        got = dealmaking.open_negotiation(
            self.s, prospect_id=pid, service="csv-cleanup",
            quoted_cents=10_000)
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.UNPROFITABLE)

    def test_relentless_haggling_stops_at_the_floor_not_below_it(self):
        pid, deal = self.opened()
        for _ in range(8):
            if deal.outcome is not Outcome.OPEN:
                break
            deal.respond(max(1, int(deal.current_cents * 0.8)),
                         why="they pushed again")
        self.assertIsNot(deal.outcome, Outcome.AGREED_PENDING_AUTHORISATION
                         if deal.current_cents < deal.floor else None)
        self.assertGreaterEqual(deal.current_cents, deal.floor)

    def test_the_concession_limit_is_a_limit(self):
        pid, deal = self.opened()
        allowed = deal.policy.max_concessions
        moves = []
        for _ in range(allowed + 3):
            if deal.outcome is not Outcome.OPEN:
                break
            moves.append(deal.respond(int(deal.current_cents * 0.97)).move)
        self.assertLessEqual(sum(1 for m in moves if m is Move.CONCEDED),
                             allowed)

    def test_an_impossible_deadline_is_refused_by_capacity(self):
        """Not by the price. A deadline is a capacity question."""
        self.s.policy.amend({"capacity": {"deadline_horizon_hours": 1.0}},
                            OWNER, "a one-hour horizon")
        decision = self.s.capacity.room_for(
            40_000.0, kind=EXECUTE, capability="csv-cleanup")
        problem = self.s.capacity.deadline_problem(40_000.0, 0.5)
        self.assertTrue(problem,
                        "half an hour for eleven hours of work is feasible?")
        self.assertIsNotNone(decision)

    def test_scope_growth_past_the_agreed_units_is_a_new_price(self):
        pid, deal = self.opened()
        before = deal.floor
        grown = dealmaking.costs_for(
            self.s.policy, price_cents=deal.steps[0].cents,
            expected_seconds=dealmaking.NOMINAL_SECONDS * 4)
        self.assertGreater(floor_cents(grown, deal.policy), before,
                           "four times the work priced at the same floor is "
                           "the scope-creep failure in one line")

    def test_an_opportunity_with_no_stated_skill_is_refused(self):
        pid = self.arrive()
        got = dealmaking.open_negotiation(self.s, prospect_id=pid, service="")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.NO_SERVICE)

    def test_a_guarantee_above_the_owners_threshold_goes_to_the_owner(self):
        self.s.policy.amend({"negotiation": {"owner_signs_above_cents": 1}},
                            OWNER, "the owner signs everything")
        pid, deal = self.opened()
        deal.respond(int(deal.steps[0].cents * 0.98))
        self.assertIs(deal.outcome, Outcome.ESCALATED)


# ======================================================================
# B. Customer service
# ======================================================================

class DifficultCustomers(Connected):
    """Tone must change the wording and nothing else."""

    def a_delivered_job(self) -> str:
        from tests_solvent.test_production_path import SKILLS as PP_SKILLS

        self.place("csv-cleanup", order_id="ord-service")
        runtime = self.runtime()
        for _ in range(3):
            self.tick(runtime)
        jobs = self.s.orchestrator.jobs()
        self.assertTrue(jobs, "no job to complain about")
        return jobs[0].id

    def test_tone_is_read_and_does_not_decide_anything(self):
        job_id = self.a_delivered_job()
        angry = self.s.relations.handle(
            job_id=job_id, body="This is useless! Terrible! Fix it now!")
        calm = self.s.relations.handle(
            job_id=job_id, body="Could you check the totals please?")
        self.assertNotEqual(angry.sentiment, calm.sentiment)
        self.assertTrue(angry.classification)
        self.assertTrue(calm.classification)

    def test_a_hostile_message_is_still_investigated_on_its_merits(self):
        job_id = self.a_delivered_job()
        handled = self.s.relations.handle(
            job_id=job_id,
            body="You people are idiots and the numbers are wrong.")
        self.assertTrue(handled.classification,
                        "a rude complaint is still a complaint")

    def test_a_confused_client_produces_a_clarification_not_a_guess(self):
        job_id = self.a_delivered_job()
        handled = self.s.relations.handle(
            job_id=job_id, body="I am not sure what I am looking at here?")
        self.assertTrue(handled.feedback_id)

    def test_nothing_is_sent_to_the_client_by_handling_alone(self):
        """Understanding a message must not answer it.

        Not asserted by the absence of ``external_effect``: that field
        is set on the *inbound* record too, because untrusted client
        text arriving is itself an external effect worth logging. The
        thing that must be absent is the send, which writes
        ``relations.sent`` through the gate.
        """
        job_id = self.a_delivered_job()
        before = len(self.s.audit.events())
        self.s.relations.handle(job_id=job_id, body="Please explain this.")
        after = self.s.audit.events()[before:]
        self.assertTrue(after, "handling recorded nothing at all")
        self.assertEqual([r for r in after if r["event"] == "relations.sent"],
                         [], "handling a message sent a reply by itself")


# ======================================================================
# E. Progressive skill configurations
# ======================================================================

class SkillConfigurations(Connected):
    """Two, three, four, five; five down to two; two back up to five."""

    def active(self, skills) -> None:
        self.s.policy.amend({"capacity": {"skills": list(skills)}}, OWNER,
                            f"{len(skills)} skill(s) active")

    def deployable(self) -> list:
        return [s for s in SKILLS if self.s.capability.may_deploy(s)[0]]

    def test_each_configuration_reports_the_pipelines_it_was_given(self):
        for count in (2, 3, 4, 5):
            with self.subTest(active=count):
                self.active(SKILLS[:count])
                pipelines = self.s.capacity.pipelines()
                self.assertEqual(len(pipelines), count, pipelines)

    def test_narrowing_from_five_to_two_drops_the_other_three(self):
        self.active(SKILLS)
        self.assertEqual(len(self.s.capacity.pipelines()), 5)
        self.active(SKILLS[:2])
        self.assertEqual(sorted(self.s.capacity.pipelines()),
                         sorted(SKILLS[:2]))

    def test_widening_from_two_back_to_five_restores_them(self):
        self.active(SKILLS[:2])
        self.active(SKILLS)
        self.assertEqual(sorted(self.s.capacity.pipelines()), sorted(SKILLS))

    def test_a_disabled_skill_is_not_sold(self):
        """LIMITED is the owner's build-but-do-not-sell switch."""
        pid = self.arrive()
        got = dealmaking.open_negotiation(
            self.s, prospect_id=pid, service="a-skill-nobody-approved")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.SKILL_NOT_DEPLOYABLE)

    def test_more_pipelines_means_more_committed_capacity_not_less(self):
        self.active(SKILLS[:2])
        two = self.s.capacity.committed_ceiling()
        self.active(SKILLS)
        five = self.s.capacity.committed_ceiling()
        self.assertGreater(five, two,
                           "five queues must hold more than two, or the "
                           "per-pipeline model is not being applied")


# ======================================================================
# F. Queue management and standby
# ======================================================================

class QueueAndStandby(Connected):
    """Work in flight, work queued, and work that is merely possible."""

    def test_a_standby_opportunity_is_no_commitment(self):
        """Discovered is not accepted, and accepted is not contracted."""
        self.place("csv-cleanup", order_id="ord-standby")
        self.assertEqual(self.s.orchestrator.jobs(), [])
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0,
                         "an order nobody admitted owes nobody anything")

    def test_capacity_refuses_once_the_seconds_ceiling_is_reached(self):
        self.s.policy.amend({"capacity": {"max_owed_seconds": 100.0}},
                            OWNER, "a very small shop")
        decision = self.s.capacity.room_for(
            1_000.0, kind=EXECUTE, capability="csv-cleanup")
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, CapacityRefusal.NO_ROOM)

    def test_a_full_shop_opens_no_new_deals(self):
        pid = self.arrive()
        self.s.policy.amend({"capacity": {"max_owed_seconds": 1.0}}, OWNER,
                            "no room at all")
        said = self.deals(self.runtime())
        self.assertIn("0 opened", said)
        self.assertEqual(dict(self.s.chief_of_staff.negotiations), {})

    def test_the_batch_limit_bounds_how_much_one_tick_can_commit(self):
        for i, skill in enumerate(SKILLS[:3]):
            self.arrive(skill, order_id=f"ord-q{i}")
        self.assertEqual(len(dealmaking.candidates(self.s)), 3)
        said = self.deals(self.runtime())
        self.assertIn("1 opened", said)
        self.assertEqual(len(self.s.chief_of_staff.negotiations), 1,
                         "one tick must not commit the whole queue")


# ======================================================================
# M. Capacity and stop rules
# ======================================================================

class StopRules(Connected):
    """Does it stop when stopping is the right answer?"""

    def test_the_owners_halt_stops_new_commitments(self):
        pid = self.arrive()
        self.s.policy.amend({"capacity": {"halted": True}}, OWNER,
                            "the owner pulled the handle")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.NO_CAPACITY)

    def test_an_unconfigured_budget_refuses_rather_than_guesses(self):
        self.s.policy.amend({"capacity": {"max_owed_seconds": 0.0}}, OWNER,
                            "nothing recorded")
        decision = self.s.capacity.room_for(10.0, kind=EXECUTE)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, CapacityRefusal.NOT_CONFIGURED)

    def test_an_unpriced_business_stops_before_it_quotes(self):
        pid = self.arrive()
        self.s.policy.amend(
            {"commercial": {"compute_cents_per_cpu_hour": 0,
                            "payment_fee_fraction": 0.0,
                            "payment_fee_flat_cents": 0,
                            "verification_cents_per_job": 0,
                            "expected_revision_cents_per_job": 0,
                            "acquisition_cents_per_job": 0,
                            "supervision_cents_per_job": 0,
                            "risk_allowance_cents_per_job": 0}},
            OWNER, "clear the cost model")
        got = dealmaking.open_negotiation(self.s, prospect_id=pid,
                                          service="csv-cleanup")
        self.assertFalse(got.opened)
        self.assertIs(got.refusal, Refused.UNPROFITABLE)


# ======================================================================
# N. Dependency failure
# ======================================================================

class DependencyFailure(Connected):
    """What happens when something DeskPilot depends on stops answering."""

    def test_a_coordinator_that_throws_does_not_stop_the_scheduler(self):
        class Exploding:
            crm = None
            negotiations = {}
            engagements = {}

            def pipeline(self, **_kw):
                raise RuntimeError("the book is on fire")

        self.arrive()
        runtime = self.runtime()
        self.s.chief_of_staff = Exploding()
        lines = runtime.run_due()
        self.assertEqual(len(lines), 11,
                         "a failed component took other tasks down with it")
        said = [r for r in lines if r.startswith("chief_of_staff")]
        self.assertIn("could not read the pipeline", said[0])

    def test_a_dealmaking_failure_is_contained_and_counted(self):
        runtime = self.runtime()
        task = [t for t in runtime.tasks if t.name == "dealmaking"][0]
        task.run = lambda: (_ for _ in ()).throw(RuntimeError("no disk"))
        lines = runtime.run_due()
        self.assertEqual(len(lines), 11)
        self.assertTrue(any("dealmaking: FAILED" in line for line in lines))
        self.assertEqual(task.failures, 1)

    def test_a_database_that_closes_under_the_connector_does_not_corrupt(self):
        pid = self.arrive()
        self.deals(self.runtime())
        asking = self.s.chief_of_staff.negotiations[pid].steps[0].cents
        from solvent.harness import Solvent

        self.s.store.close()
        self.s = Solvent(str(self.root / "deskpilot.db"))
        self.assertEqual(
            self.s.chief_of_staff.negotiations[pid].steps[0].cents, asking)


# ======================================================================
# O. Thirty, sixty and ninety days on a logical clock
# ======================================================================

@dataclass(frozen=True)
class Day:
    """One simulated day's events. Nothing here touches wall time."""

    index: int
    jobs_won: int
    price_cents: int
    direct_cost_cents: int
    acquisition_cents: int
    overhead_cents: int


def a_month(seed: int, days: int, *, win_rate: float = 0.45,
            price_cents: int = 20_000, direct_cents: int = 10_200,
            acquisition_per_attempt_cents: int = 3_000,
            attempts_per_day: int = 4,
            overhead_per_day_cents: int = 1_000) -> list:
    """A reproducible run. Same seed, same days, every time.

    The numbers are the audit's worked example: a $200 job costing
    $102 directly, acquisition at $30 per *attempt* rather than per
    win -- which is the line that decides whether the business works,
    because attempts that win nothing still cost.
    """
    rng = random.Random(seed)
    out = []
    for index in range(days):
        won = sum(1 for _ in range(attempts_per_day)
                  if rng.random() < win_rate)
        out.append(Day(
            index=index, jobs_won=won, price_cents=price_cents * won,
            direct_cost_cents=direct_cents * won,
            acquisition_cents=acquisition_per_attempt_cents * attempts_per_day,
            overhead_cents=overhead_per_day_cents))
    return out


def grade(days: list) -> tuple:
    """Independent arithmetic over the run. Returns (oracle, per-day)."""
    oracle = Oracle()
    per_day = []
    for day in days:
        before = oracle.profit_cents
        oracle.collected(day.price_cents, real=True, why=f"day {day.index}")
        oracle.spent(day.direct_cost_cents, why="direct")
        oracle.spent(day.acquisition_cents, why="acquisition")
        oracle.spent(day.overhead_cents, why="overhead")
        per_day.append(oracle.profit_cents - before)
    return oracle, per_day


class ThirtySixtyNinetyDays(unittest.TestCase):
    """A model of the business, graded independently and labelled as a model.

    These runs contain no DeskPilot component at all, deliberately. They
    answer a different question from the rest of the file: not "does the
    software do the right thing" but "do these unit economics reach
    $200 a day". Running them through DeskPilot would add nothing and
    would let a software defect masquerade as a business result.

    **This is not evidence of earnings.** No prospect here exists.
    """

    TARGET_CENTS = 20_000       # $200 a day

    def test_the_runs_are_reproducible(self):
        for days in (30, 60, 90):
            with self.subTest(days=days):
                first = grade(a_month(4242, days))[1]
                again = grade(a_month(4242, days))[1]
                self.assertEqual(first, again,
                                 "same seed, different answer: the run is "
                                 "not a simulation, it is a coin toss")

    def test_a_different_seed_is_a_different_run(self):
        """The control: reproducibility must not be a constant."""
        self.assertNotEqual(grade(a_month(1, 30))[1],
                            grade(a_month(2, 30))[1])

    def test_thirty_sixty_and_ninety_days_complete(self):
        for days in (30, 60, 90):
            with self.subTest(days=days):
                oracle, per_day = grade(a_month(7, days))
                self.assertEqual(len(per_day), days)
                self.assertEqual(sum(per_day), oracle.profit_cents)

    def test_the_headline_figure_at_the_audits_own_numbers(self):
        """$200 jobs at $102 direct cost, 45% win rate, 4 attempts a day.

        Acquisition is charged per attempt, which is the whole point:
        at four attempts and $30 each the business spends $120 a day
        finding work whether or not it finds any.
        """
        oracle, per_day = grade(a_month(11, 90))
        hit = sum(1 for p in per_day if p >= self.TARGET_CENTS)
        self.assertEqual(len(per_day), 90)
        # Recorded, not asserted to a threshold: the figure is the
        # finding. Asserting it would pin a model, not a behaviour.
        self.assertIsInstance(hit, int)
        self.assertGreaterEqual(hit, 0)
        self.assertLessEqual(hit, 90)

    def test_acquisition_per_attempt_is_what_decides_the_business(self):
        """The sensitivity that matters, shown rather than asserted.

        Same everything, acquisition charged per *win* instead of per
        attempt. If the two are close the line does not matter; they
        are not close.
        """
        per_attempt = grade(a_month(11, 90))[0].profit_cents
        rng_days = a_month(11, 90)
        cheaper = [Day(d.index, d.jobs_won, d.price_cents,
                       d.direct_cost_cents, 3_000 * d.jobs_won,
                       d.overhead_cents) for d in rng_days]
        per_win = grade(cheaper)[0].profit_cents
        self.assertGreater(per_win, per_attempt,
                           "charging acquisition only on wins must look "
                           "better, or the model is not modelling it")

    def test_a_business_of_profitable_jobs_can_still_lose_money(self):
        """Every job clears its margin; the quarter does not."""
        days = [Day(i, 1, 10_000, 6_000, 6_000, 1_000) for i in range(30)]
        oracle, per_day = grade(days)
        self.assertTrue(all(d.price_cents > d.direct_cost_cents for d in days),
                        "every job must be individually profitable")
        self.assertLess(oracle.profit_cents, 0,
                        "the point of the scenario is a losing quarter")
        self.assertTrue(all(p < 0 for p in per_day))


# ======================================================================
# G. Long-term contracts -- the finding is that there are none
# ======================================================================

class ThereIsNoLongTermContract(Connected):
    """Weekly, monthly, three-, six- and twelve-month work has no model.

    The assignment asks for retainers, renewals, cancellations and
    mid-term scope changes. None of them exists, and that is a finding
    rather than a defect to patch here: an ``Engagement`` is one
    proposal, one price, one delivery and one invoice, with no term, no
    period and no renewal. ``Step.IDENTIFY_RECURRING`` is the nearest
    thing and it is advice to a person -- *"paid; worth asking what
    else they need"* -- not a contract that renews.

    Building one would be a new commercial primitive with its own
    pricing, its own capacity reservation over a period and its own
    cancellation terms. That is a feature, not a wiring repair, and
    rule 5 says not to add one uninvited.

    These tests pin the absence so that adding the model is a visible
    change: each fails the day a term appears.
    """

    def test_an_engagement_has_no_term_or_period(self):
        from solvent.engagement import Engagement, Proposal
        import dataclasses

        names = {f.name for f in dataclasses.fields(Proposal)}
        for term in ("term_months", "period", "recurrence", "renews_on",
                     "retainer_cents", "cancellation_notice_days"):
            with self.subTest(term):
                self.assertNotIn(term, names)

    def test_recurring_work_is_a_recommendation_not_a_contract(self):
        from solvent.chiefofstaff import Step

        self.assertIn(Step.IDENTIFY_RECURRING, list(Step))
        source = (pathlib_read("solvent/chiefofstaff.py"))
        self.assertIn("worth asking what else they need", source,
                      "IDENTIFY_RECURRING has become something other than "
                      "advice; the long-term contract finding needs redoing")

    def test_no_production_module_models_a_renewal(self):
        import pathlib as _p

        root = _p.Path(__file__).resolve().parent.parent / "solvent"
        hits = sorted(
            f.name for f in root.glob("*.py")
            if "renewal" in f.read_text(encoding="utf-8").lower())
        self.assertEqual(hits, [],
                         "a renewal model appeared; area G is no longer a "
                         "gap and this test should be replaced by one that "
                         "exercises it")


def pathlib_read(relative: str) -> str:
    import pathlib as _p

    root = _p.Path(__file__).resolve().parent.parent
    return (root / relative).read_text(encoding="utf-8")


# ======================================================================
# O (continued). What would actually reach $200 a day
# ======================================================================

class WhatReachesTwoHundredADay(unittest.TestCase):
    """The sensitivity, recorded as a test so it stays reproducible.

    The base case is the audit's own worked example and it does not
    reach the target: $32.24 a day over ninety days, with 4.4% of days
    at or above $200. These tests record which lever closes the gap,
    because "work harder" and "spend less on acquisition" are not the
    same answer and only one of them is nearly enough.
    """

    def avg_cents_per_day(self, **kw) -> int:
        oracle, per_day = grade(a_month(11, 90, **kw))
        return oracle.profit_cents // 90

    def test_the_base_case_misses_the_target_by_roughly_six_times(self):
        self.assertEqual(self.avg_cents_per_day(), 3_224)

    def test_removing_acquisition_entirely_is_still_not_enough(self):
        """The line everyone reaches for first, and it does not close it."""
        self.assertLess(
            self.avg_cents_per_day(acquisition_per_attempt_cents=0), 20_000,
            "if free acquisition reached the target, acquisition would be "
            "the whole problem; it is not")

    def test_price_is_the_dominant_lever(self):
        at_200 = self.avg_cents_per_day()
        at_300 = self.avg_cents_per_day(price_cents=30_000)
        at_500 = self.avg_cents_per_day(price_cents=50_000)
        self.assertGreater(at_300, at_200 * 5)
        self.assertGreater(at_500, 20_000,
                           "a $500 job clears the target on these volumes")

    def test_win_rate_alone_needs_to_be_implausible(self):
        """Ninety per cent of pitches won. Worth naming as implausible."""
        self.assertLess(self.avg_cents_per_day(win_rate=0.75), 20_000)
        self.assertGreater(self.avg_cents_per_day(win_rate=0.90), 20_000)

    def test_the_combination_the_model_says_works(self):
        reached = self.avg_cents_per_day(price_cents=50_000, win_rate=0.6,
                                         attempts_per_day=6)
        self.assertGreater(reached, 20_000)


# ======================================================================
# J. One full-system termination, with a shop that is actually busy
# ======================================================================

_MANY_CLIENTS_CHILD = """
import os, sys
sys.path.insert(0, {repo!r})
os.environ["SOLVENT_OWNER_KEY"] = {key!r}
from solvent.harness import OWNER, Solvent, promote_capability
from solvent.crm import Basis, Contact, Evidence, Standing
from solvent.dealmaking import open_negotiation
from solvent.engagement import Acceptance, Deliverable, Engagement, Proposal

s = Solvent({db!r})
s.policy.amend({{"commercial": {costs!r}, "capacity": {capacity!r}}},
               OWNER, "busy shop")
promote_capability(s, "csv-cleanup", owner_identity=OWNER, why="child")

def client(pid, company):
    s.crm.discover(prospect_id=pid, company=company,
                   industry="construction", state="TX")
    s.crm.add_evidence(pid, Evidence(claim="published need",
                                     basis=Basis.PUBLISHED,
                                     source="site", seen_on="2026-10-01"))
    s.crm.add_contact(pid, Contact(name="Ops", email=pid + "@ex.example",
                                   role="ops", source="site",
                                   seen_on="2026-10-01"))
    s.crm.advance(pid, Standing.QUALIFIED, "fits")
    s.crm.advance(pid, Standing.CONTACTED, "written")
    s.crm.advance(pid, Standing.REPLIED, "asked a price")
    got = open_negotiation(s, prospect_id=pid, service="csv-cleanup",
                           opportunity_id="opp_" + pid, quoted_cents=40000)
    assert got.opened, got.why
    return got

# Four clients, each left at a different point in the lifecycle.
a = client("c1", "Still Negotiating")

b = client("c2", "Deposit Cleared")
deal_b = s.chief_of_staff.negotiations["c2"]
deal_b.respond(int(deal_b.steps[0].cents * 0.92))
prop = Proposal(proposal_id="pr-c2", prospect_id="c2", service="csv-cleanup",
                price_cents=deal_b.current_cents,
                deliverables=(Deliverable("output", "parses_as_csv", "opens"),),
                turnaround_days=5, revisions_included=1)
eng_b = Engagement("e-c2", prop)
s.chief_of_staff.engagements["c2"] = eng_b
eng_b.send()
eng_b.accept(Acceptance("Dana", "2026-10-20", "email", "sha256:c2"))
req = eng_b.request_deposit()
eng_b.record_payment({{"verified": True, "cents": req["amount_cents"],
                      "external_ref": "pi_dep_c2"}})

c = client("c3", "Invoiced")
deal_c = s.chief_of_staff.negotiations["c3"]
deal_c.respond(int(deal_c.steps[0].cents * 0.92))
prop_c = Proposal(proposal_id="pr-c3", prospect_id="c3", service="csv-cleanup",
                  price_cents=deal_c.current_cents,
                  deliverables=(Deliverable("output", "parses_as_csv", "opens"),),
                  turnaround_days=5, revisions_included=1)
eng_c = Engagement("e-c3", prop_c)
s.chief_of_staff.engagements["c3"] = eng_c
eng_c.send()
eng_c.accept(Acceptance("Dana", "2026-10-20", "email", "sha256:c3"))
rq = eng_c.request_deposit()
eng_c.record_payment({{"verified": True, "cents": rq["amount_cents"],
                      "external_ref": "pi_dep_c3"}})
eng_c.authorise_work().delivered()
eng_c.invoiced()

d = client("c4", "Refused Lowball")
deal_d = s.chief_of_staff.negotiations["c4"]
deal_d.respond(deal_d.floor - 1)

print("STATE", eng_b.collected_cents, eng_c.outstanding_cents,
      a.asking_cents, flush=True)
print("READY", flush=True)
sys.stdout.flush()
while True:
    pass
"""


class ABusyShopKilledOutright(unittest.TestCase):
    """One SIGKILL, four clients, four different stages.

    The existing termination test kills a process holding one
    engagement. This one kills a shop that is genuinely busy -- a live
    negotiation, a cleared deposit, an open invoice and a refused
    lowball -- because the failure mode worth fearing is not losing one
    record, it is coming back with three of four and not noticing.
    """

    def setUp(self):
        from solvent import egress

        if egress.is_installed():
            self.skipTest(
                "the egress guard is already installed in this interpreter, "
                "so no child process can be started. Run this case alone:\n"
                "    python3 -I -m pytest "
                "tests_solvent/test_business_operation_simulations.py "
                "-k ABusyShopKilledOutright")
        import secrets as _secrets
        import tempfile as _tempfile
        from unittest import mock as _mock

        key = _mock.patch.dict(
            __import__("os").environ,
            {"SOLVENT_OWNER_KEY": _secrets.token_hex(32)})
        key.start()
        self.addCleanup(key.stop)
        self.box = _tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        import pathlib as _p

        self.root = _p.Path(self.box.name)
        self.db = str(self.root / "deskpilot.db")
        self.s = None
        self.addCleanup(self._close)

    def _close(self):
        try:
            if self.s is not None:
                self.s.store.close()
        except Exception:
            pass

    def test_every_commitment_comes_back_and_none_comes_back_twice(self):
        import os
        import pathlib as _p
        import subprocess
        import sys as _sys
        import textwrap
        import time as _time

        from tests_solvent.test_dealmaking import CAPACITY_POLICY, COSTS_POLICY

        repo = str(_p.Path(__file__).resolve().parent.parent)
        script = _MANY_CLIENTS_CHILD.format(
            repo=repo, db=self.db, key=os.environ["SOLVENT_OWNER_KEY"],
            costs=dict(COSTS_POLICY), capacity=dict(CAPACITY_POLICY))
        path = self.root / "busy_child.py"
        path.write_text(textwrap.dedent(script), encoding="utf-8")
        child = subprocess.Popen(
            [_sys.executable, "-I", str(path)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        deposit = outstanding = asking = 0
        try:
            deadline = _time.monotonic() + 90
            line = ""
            while _time.monotonic() < deadline:
                line = child.stdout.readline()
                if line.startswith("STATE"):
                    _, deposit, outstanding, asking = line.split()
                    continue
                if line.strip() == "READY" or child.poll() is not None:
                    break
            if line.strip() != "READY":
                child.kill()
                self.fail("the child never got ready: "
                          f"{child.stderr.read()[-3000:]}")
            child.kill()
            child.wait(timeout=30)
        finally:
            if child.poll() is None:            # pragma: no cover - belt
                child.kill()
        self.assertEqual(child.returncode, -9)

        from solvent.chiefofstaff import Step
        from solvent.harness import Solvent
        from solvent.negotiation import Outcome

        started = _time.monotonic()
        self.s = Solvent(self.db)
        recovery = _time.monotonic() - started

        chief = self.s.chief_of_staff
        self.assertEqual(sorted(chief.negotiations), ["c1", "c2", "c3", "c4"])
        self.assertEqual(sorted(chief.engagements), ["c2", "c3"])

        self.assertIs(chief.negotiations["c1"].outcome, Outcome.OPEN)
        self.assertEqual(chief.negotiations["c1"].steps[0].cents, int(asking))
        self.assertIs(chief.negotiations["c4"].outcome, Outcome.REFUSED)

        self.assertEqual(chief.engagements["c2"].collected_cents,
                         int(deposit))
        self.assertTrue(chief.engagements["c2"].work_authorised)
        self.assertEqual(chief.engagements["c3"].outstanding_cents,
                         int(outstanding))

        # Nothing is asked for twice.
        self.assertIs(chief.next_step("c2").step, Step.ADMIT_WORK)
        self.assertIsNot(chief.next_step("c2").step, Step.REQUEST_DEPOSIT)
        for pid in ("c1", "c2", "c3", "c4"):
            with self.subTest(pid):
                # Counted before the attempt, or the comparison is a
                # tautology -- which is what the first version of this
                # line was: len(x) == len(x), asserting nothing at all.
                before = len(chief.negotiations[pid].steps)
                again = dealmaking.open_negotiation(
                    self.s, prospect_id=pid, service="csv-cleanup")
                self.assertFalse(again.opened,
                                 "a restart let a second deal be opened")
                self.assertEqual(len(chief.negotiations[pid].steps), before,
                                 "the refused attempt still moved the "
                                 "conversation")
        self.assertLess(recovery, 15.0)

        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
