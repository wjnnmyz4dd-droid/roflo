"""The ten invariants that stop DeskPilot promising more than it can do.

Finding a hundred jobs is not being able to do a hundred jobs. Each
test below is named for the invariant it pins, and each one is written
so that removing the control makes it fail -- which is what the mutants
for this module check.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import capacity as cap
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from tests_solvent.fixtures import source_names


def capacity_identifiers() -> set:
    return source_names("solvent/capacity.py")

BUDGET = {"max_owed_seconds": 600.0, "max_concurrent_jobs": 3,
          "max_open_bids": 2, "cpu_share": 0.5,
          "min_free_disk_bytes": 0, "halted": False}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/capacity.db"
        self.s = Solvent(self.db)
        self.addCleanup(self.close)
        self.configure()

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def configure(self, **over):
        budget = dict(BUDGET)
        budget.update(over)
        self.s.policy.amend({"capacity": budget}, OWNER, "test: budget")

    def reserve(self, seconds, *, kind=cap.EXECUTE, job="job", key=None):
        return self.s.capacity.reserve(
            seconds, kind=kind, job_id=job,
            idempotency_key=key or f"{kind}:{job}:{seconds}")


class Invariant1_NoJobWithoutCapacity(Base):

    def test_an_unconfigured_budget_accepts_nothing(self):
        """Zero is not "no limit". A gate with no number is not a gate."""
        bare = Solvent(f"{self.box.name}/bare.db")
        self.addCleanup(lambda: bare.store.close())
        decision = bare.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.NOT_CONFIGURED)

    def test_work_within_the_budget_is_allowed(self):
        self.assertTrue(self.reserve(100).allowed)

    def test_work_past_the_budget_is_refused(self):
        self.assertTrue(self.reserve(500, job="a", key="a").allowed)
        decision = self.reserve(200, job="b", key="b")
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.NO_ROOM)
        self.assertIn("600s", decision.why)

    def test_the_concurrent_job_limit_is_separate_from_the_seconds(self):
        """Three tiny jobs can exhaust a job limit without touching the
        time budget. Both limits exist because both failures are real."""
        for n in range(3):
            self.assertTrue(self.reserve(1, job=f"j{n}", key=f"k{n}").allowed)
        decision = self.reserve(1, job="j4", key="k4")
        self.assertFalse(decision.allowed)
        self.assertIn("pipeline", decision.why)

    def test_a_halt_stops_new_commitments(self):
        self.configure(halted=True)
        self.assertIs(self.reserve(1).refusal, cap.Refusal.HALTED)


class Invariant2_NoBidBeyondBudget(Base):

    def test_a_bid_consumes_capacity(self):
        self.reserve(100, kind=cap.BID, job="b1", key="b1")
        self.assertEqual(self.s.capacity.owed_seconds(), 100)

    def test_bids_are_limited_in_number(self):
        self.assertTrue(self.reserve(10, kind=cap.BID, job="b1", key="b1").allowed)
        self.assertTrue(self.reserve(10, kind=cap.BID, job="b2", key="b2").allowed)
        decision = self.reserve(10, kind=cap.BID, job="b3", key="b3")
        self.assertFalse(decision.allowed)
        self.assertIn("outstanding", decision.why)

    def test_bids_and_work_share_one_budget(self):
        """Three bids that all win are three jobs. They cannot each be
        measured against an empty queue."""
        self.reserve(550, kind=cap.BID, job="b1", key="b1")
        decision = self.reserve(100, kind=cap.EXECUTE, job="j1", key="j1")
        self.assertFalse(decision.allowed)

    def test_a_lost_bid_gives_the_room_back(self):
        held = self.reserve(500, kind=cap.BID, job="b1", key="b1")
        self.s.capacity.release(held.reservation_id, "the client chose another")
        self.assertEqual(self.s.capacity.owed_seconds(), 0)
        self.assertTrue(self.reserve(500, job="j1", key="j1").allowed)


class Invariant3_AcceptedWorkBeatsDiscovery(Base):

    def test_obligations_are_visible_to_the_scheduler(self):
        self.assertFalse(self.s.capacity.has_obligations())
        self.reserve(10, job="j1", key="j1")
        self.assertTrue(self.s.capacity.has_obligations())

    def test_an_open_bid_is_not_an_obligation_to_deliver(self):
        """A bid is contingent. It limits new bids and counts against
        the time budget, but it is not work owed to anybody yet."""
        self.reserve(10, kind=cap.BID, job="b1", key="b1")
        self.assertFalse(self.s.capacity.has_obligations())


class Invariant4_VerificationAndRevisionsCost(Base):

    def test_verification_consumes_capacity(self):
        self.reserve(100, kind=cap.VERIFY, job="j1", key="v1")
        self.assertEqual(self.s.capacity.owed_seconds(), 100)

    def test_a_revision_consumes_capacity(self):
        self.reserve(100, kind=cap.REVISE, job="j1", key="r1")
        self.assertEqual(self.s.capacity.owed_seconds(), 100)

    def test_execution_verification_and_revision_add_up(self):
        self.reserve(200, kind=cap.EXECUTE, job="j1", key="e1")
        self.reserve(200, kind=cap.VERIFY, job="j1", key="v1")
        self.reserve(200, kind=cap.REVISE, job="j1", key="r1")
        self.assertEqual(self.s.capacity.owed_seconds(), 600)
        self.assertFalse(self.reserve(1, job="j2", key="x").allowed)

    def test_several_revisions_can_exhaust_capacity(self):
        """The failure this invariant exists for: room for the work,
        none for the changes the client is entitled to ask for."""
        self.reserve(300, kind=cap.EXECUTE, job="j1", key="e1")
        # 300 of headroom, revisions of 100: the first three fit exactly
        # and the fourth does not. My first version asked for three and
        # called it a failure when they fitted, which was arithmetic
        # rather than a finding.
        for n in range(4):
            decision = self.reserve(100, kind=cap.REVISE, job="j1",
                                    key=f"rev{n}")
            if not decision.allowed:
                self.assertEqual(n, 3)
                self.assertIn("owed", decision.why)
                return
        self.fail("four revisions fitted in 300s of headroom")


class Invariant5_ReservationsSurviveRestarts(Base):

    def test_a_held_reservation_is_still_held_after_a_restart(self):
        self.reserve(400, job="j1", key="j1")
        self.s.store.close()
        self.s = Solvent(self.db)
        self.assertEqual(self.s.capacity.owed_seconds(), 400)

    def test_the_budget_is_still_enforced_after_a_restart(self):
        self.reserve(500, job="j1", key="j1")
        self.s.store.close()
        self.s = Solvent(self.db)
        self.assertFalse(self.reserve(200, job="j2", key="j2").allowed)

    def test_a_consumed_reservation_does_not_come_back(self):
        held = self.reserve(400, job="j1", key="j1")
        self.s.capacity.consume(held.reservation_id)
        self.s.store.close()
        self.s = Solvent(self.db)
        self.assertEqual(self.s.capacity.owed_seconds(), 0)


class Invariant6_NoDuplicateCommitments(Base):

    def test_the_same_event_twice_reserves_once(self):
        first = self.reserve(100, job="j1", key="marketplace-event-77")
        second = self.reserve(100, job="j1", key="marketplace-event-77")
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertIs(second.refusal, cap.Refusal.DUPLICATE)
        self.assertEqual(self.s.capacity.owed_seconds(), 100)

    def test_the_duplicate_names_the_reservation_it_already_made(self):
        first = self.reserve(100, job="j1", key="evt")
        second = self.reserve(100, job="j1", key="evt")
        self.assertEqual(second.reservation_id, first.reservation_id)

    def test_a_reservation_without_a_key_is_refused(self):
        with self.assertRaises(FailClosed):
            self.s.capacity.reserve(10, job_id="j1", idempotency_key="")

    def test_duplicates_survive_a_restart(self):
        self.reserve(100, job="j1", key="evt")
        self.s.store.close()
        self.s = Solvent(self.db)
        again = self.reserve(100, job="j1", key="evt")
        self.assertIs(again.refusal, cap.Refusal.DUPLICATE)


class Invariant7_UnknownOutcomesBlockCommitments(Base):

    def test_an_unreconciled_attempt_stops_new_commitments(self):
        self.s.capacity._applications = type(
            "Unresolved", (), {"unresolved": lambda self: [{"id": "a"}]})()
        decision = self.s.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.UNRECONCILED)

    def test_books_that_cannot_be_read_also_stop_commitments(self):
        """A failure to check is not a clean check."""
        def explode(self):
            raise RuntimeError("the attempts table is unreadable")

        self.s.capacity._applications = type(
            "Broken", (), {"unresolved": explode})()
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)


class Invariant8_NoGateIsBypassed(Base):

    def test_capacity_owns_no_other_decision(self):
        """It answers one question. Everything else stays where it was."""
        surface = {n for n in dir(self.s.capacity) if not n.startswith("_")}
        self.assertEqual(
            surface,
            # Everything here either answers "is there room" or
            # reports what is already owed. committed_ceiling and
            # exposure are the second kind: exposure counts committed
            # jobs and outstanding bids separately and says what the
            # worst case comes to, and deciding nothing is the point
            # of it -- the gate reads it, the Finder reads it, and
            # neither is told what to do about it.
            {"budget", "committed_seconds", "outstanding", "owed_seconds",
             "jobs_in_flight", "executing", "pipelines", "open_bids",
             "committed_ceiling", "exposure", "lifecycle",
             "effective_workers", "deadline_problem",
             "has_obligations", "room_for", "reserve", "admit",
             "reservation_for", "start", "consume", "release",
             "pressure", "report"})

    def test_capacity_cannot_approve_permit_or_price(self):
        """Over identifiers, not over the file's text.

        The first version grepped the source and failed on the word
        "approve" inside a docstring saying capacity cannot approve
        anything -- a test that cannot tell a promise from its breach.
        That is the third time this session; hence the AST.
        """
        for forbidden in ("may_deploy", "authorize_spend", "request",
                          "approve", "grant", "amend", "transport",
                          "record_promotion", "set_operating_mode"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, capacity_identifiers())

    def test_the_identifier_scan_would_notice_a_violation(self):
        """Absences only mean something if the scan finds anything."""
        names = capacity_identifiers()
        self.assertIn("reserve", names)
        self.assertIn("room_for", names)


class Invariant9_MT5IsProtected(Base):

    def test_the_budget_is_a_share_never_the_whole_machine(self):
        self.configure(cpu_share=1.5)
        self.assertIn("MetaTrader", self.s.capacity.budget().problem())

    def test_a_zero_share_is_not_configured(self):
        self.configure(cpu_share=0.0)
        self.assertTrue(self.s.capacity.budget().problem())

    def test_nothing_here_names_or_touches_a_trading_process(self):
        """Over identifiers and real strings, never over prose.

        This module's docstring says MetaTrader 5 by name, because
        saying why the budget is a share is the point of it. Grepping
        the text would catch that sentence and miss an actual call.
        """
        names = capacity_identifiers()
        for forbidden in ("terminal64", "terminal64.exe", "MetaTrader5",
                          "taskkill", "psutil", "kill", "terminate",
                          "Popen", "subprocess", "os", "signal"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)


class Invariant10_NoOptimisticDeadlines(Base):

    def finder(self):
        from solvent.finder import Finder

        return Finder(self.s.discovery, self.s.policy, self.s.capability,
                      self.s.orchestrator, self.s.store)

    def estimate(self, *seconds):
        from solvent.finder import ExecutionHistory

        return self.finder().completion_estimate(
            "csv-cleanup", history=ExecutionHistory({"csv-cleanup": list(seconds)}))

    def test_the_promise_is_longer_than_the_median(self):
        promised, basis = self.finder().promised_seconds(self.estimate(*[20.0] * 5))
        self.assertGreater(promised, 20.0)
        self.assertIn("not the 20s median", basis)

    def test_the_queue_ahead_is_part_of_the_promise(self):
        plain, _ = self.finder().promised_seconds(self.estimate(20.0))
        queued, _ = self.finder().promised_seconds(self.estimate(20.0),
                                                   queued_seconds=300)
        self.assertEqual(queued - plain, 300)

    def test_an_unmeasured_capability_promises_nothing(self):
        promised, basis = self.finder().promised_seconds(
            self.finder().completion_estimate("never-run"))
        self.assertEqual(promised, 0.0)
        self.assertIn("not known", basis)

    def test_the_safety_factor_is_never_below_one(self):
        """A factor under one would promise faster than measured."""
        self.s.policy.amend({"capacity": {"deadline_safety_factor": 0.1}},
                            OWNER, "test: an optimistic owner")
        promised, _ = self.finder().promised_seconds(self.estimate(20.0))
        self.assertGreaterEqual(promised, 20.0)


class ThreeJobsByDefaultAndFourAtMost(Base):
    """The owner's limit, demonstrated rather than described."""

    def budget_without_a_job_limit(self):
        """A fresh install, configured in every way but this one.

        It has to be fresh. The shared fixture amends the budget with
        a job limit of three, which looks like the default working and
        is actually the fixture -- a mutant that changed the default to
        nine survived behind it.
        """
        self.s.store.close()
        self.s = Solvent(f"{self.box.name}/untouched.db")
        self.s.policy.amend({"capacity": {
            "max_owed_seconds": 100_000.0, "max_open_bids": 2,
            "cpu_share": 0.5, "min_free_disk_bytes": 0,
            "max_execution_workers": 0, "revision_headroom_fraction": 0.0,
            "halted": False}}, OWNER, "test: no job limit stated")

    def admit(self, n):
        """Offer n distinct jobs, return how many were taken."""
        return sum(1 for i in range(n)
                   if self.s.capacity.admit(
                       f"job{i}", execute_seconds=10,
                       idempotency_key=f"a{i}").allowed)

    def test_the_default_is_three(self):
        self.assertEqual(cap.JOBS_PER_AGENT_DEFAULT, 3)
        self.budget_without_a_job_limit()
        self.assertEqual(self.s.capacity.budget().max_concurrent_jobs, 3)

    def test_the_default_is_stated_in_exactly_one_place(self):
        """Mutation found the constant was dead: policy set its own 3.

        Two threes beside each other look like agreement until one of
        them is changed, so the policy table now derives from the
        constant and this checks they cannot drift apart again.
        """
        from solvent.policy import DEFAULT_POLICY

        self.assertIs(DEFAULT_POLICY["capacity"]["max_concurrent_jobs"],
                      cap.JOBS_PER_AGENT_DEFAULT)

    def test_three_jobs_are_admitted_and_the_fourth_is_not(self):
        self.budget_without_a_job_limit()
        self.assertEqual(self.admit(10), 3)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 3)

    def test_four_is_configurable(self):
        self.configure(max_concurrent_jobs=4, max_owed_seconds=100_000.0)
        self.assertEqual(self.admit(10), 4)

    def test_five_is_refused_as_a_configuration_error(self):
        self.configure(max_concurrent_jobs=5, max_owed_seconds=100_000.0)
        decision = self.s.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.NOT_CONFIGURED)
        self.assertIn("four is the most", decision.why)

    def test_more_workers_than_skills_is_allowed_but_jobs_per_skill_is_not(self):
        """Workers are counted separately from jobs per pipeline.

        This asserted that six execution workers was a configuration
        error, which was right while the job limit was global and is
        wrong now: the owner's model is five workers, each holding up
        to four jobs. The ceiling bounds a pipeline, not the payroll.
        """
        self.configure(max_concurrent_jobs=4, max_execution_workers=6,
                       max_owed_seconds=100_000.0)
        self.assertTrue(self.s.capacity.room_for(1.0).allowed)
        self.configure(max_concurrent_jobs=5, max_execution_workers=5,
                       max_owed_seconds=100_000.0)
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)

    def test_a_pipeline_target_above_the_ceiling_is_refused(self):
        self.configure(max_concurrent_jobs=4, pipeline_target=9,
                       replenish_at=2, max_owed_seconds=100_000.0)
        decision = self.s.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)
        self.assertIn("target nothing can reach", decision.why)

    def test_replenishing_at_the_target_is_refused_as_a_loop(self):
        self.configure(max_concurrent_jobs=4, pipeline_target=4,
                       replenish_at=4, max_owed_seconds=100_000.0)
        decision = self.s.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)
        self.assertIn("loop rather than a rule", decision.why)

    def test_the_ceiling_is_not_a_settings_field(self):
        """A form cannot raise a ceiling, only choose within one.

        The bound here is the *global* ceiling, not the per-pipeline
        one, because the count this preference filters against is
        shop-wide. Clamping it at four stopped the Finder assessing
        anything at all once one skill was full.
        """
        from solvent.types import GLOBAL_COMMITTED_CEILING
        from solvent.web.intents import WORK_PREFERENCES

        _, _, low, high = WORK_PREFERENCES["max_concurrent_jobs"]
        self.assertEqual(high, GLOBAL_COMMITTED_CEILING)
        self.assertGreaterEqual(low, 1)

    def test_the_triage_filter_cannot_exceed_the_gate(self):
        """Two settings share this name; they may not disagree.

        qualification.max_concurrent_jobs filters what is worth looking
        at and capacity.max_concurrent_jobs decides whether there is
        room. The gate binds either way, but a filter set to fifty
        would tell the owner they had raised a limit they had not.
        """
        from solvent.types import GLOBAL_COMMITTED_CEILING

        self.s.policy.amend({"qualification": {"max_concurrent_jobs": 50}},
                            OWNER, "test: an optimistic filter")
        qualification = self.s.qualification
        # The gate's ceiling for a shop-wide count is the shop-wide
        # one. Comparing a global count against the per-pipeline four
        # was the defect, not the clamp.
        qualification._committed_count = lambda: GLOBAL_COMMITTED_CEILING
        self.assertTrue(
            qualification._at_capacity(),
            "the triage filter still had room at the gate's ceiling, so "
            "the owner's fifty was treated as a real limit")

    def test_the_triage_filter_still_binds_below_the_ceiling(self):
        """Clamping must not turn the filter off."""
        self.s.policy.amend({"qualification": {"max_concurrent_jobs": 2}},
                            OWNER, "test: a tighter filter")
        qualification = self.s.qualification
        qualification._committed_count = lambda: 1
        self.assertFalse(qualification._at_capacity())
        qualification._committed_count = lambda: 2
        self.assertTrue(qualification._at_capacity())


class AdvisoryReviewFindings(Base):
    """Three checks the iFixAi inspection taxonomy suggested.

    Most of that taxonomy probes an agent whose decisions come from a
    model, and does not apply here: this gate calls no model and its
    decision is arithmetic over a ledger. Three of its categories do
    apply, and these are them.
    """

    def test_the_same_state_always_gives_the_same_decision(self):
        """B22, decision reproducibility."""
        self.reserve(400, job="j1", key="k1")
        answers = {(d.allowed, d.refusal, round(d.owed_seconds, 3),
                    round(d.requested_seconds, 3))
                   for d in (self.s.capacity.room_for(150.0, job_id="j2")
                             for _ in range(25))}
        self.assertEqual(len(answers), 1)

    def test_the_decision_is_the_same_after_a_restart(self):
        self.reserve(400, job="j1", key="k1")
        before = self.s.capacity.room_for(150.0, job_id="j2")
        self.s.store.close()
        self.s = Solvent(self.db)
        after = self.s.capacity.room_for(150.0, job_id="j2")
        self.assertEqual((before.allowed, before.refusal, before.why),
                         (after.allowed, after.refusal, after.why))

    def test_the_budget_is_adjustable_without_a_redeploy(self):
        """B11, system controllability."""
        self.reserve(550, job="j1", key="k1")
        self.assertFalse(self.s.capacity.room_for(200.0, job_id="j2").allowed)
        self.configure(max_owed_seconds=2000.0)
        self.assertTrue(self.s.capacity.room_for(200.0, job_id="j2").allowed)

    def test_no_model_can_influence_admission(self):
        """B04, deterministic override: the gate is not advised by one."""
        names = capacity_identifiers()
        for forbidden in ("inference", "ai", "model", "prompt", "completion",
                          "llm", "advise"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
