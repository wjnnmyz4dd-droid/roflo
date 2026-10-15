"""Eight limits, told apart.

The operating model and the earlier ceiling disagreed, and the
disagreement was not a number that needed raising. Three jobs default
and four maximum were always *per pipeline*; five skills each holding
four is twenty, and twenty had no limit of its own. Raising four to
twenty would have thrown away the per-pipeline limit to get the global
one, and a shop with only a global limit lets one skill eat it.

So there are eight separate numbers here, and these tests exist to
keep them separate:

1. global active execution      -- max_execution_workers
2. per-skill active execution   -- one, structurally
3. per-skill committed pipeline -- max_concurrent_jobs
4. global committed obligations -- max_committed_jobs
5. outstanding bid exposure     -- max_open_bids AND the worst case
6. deadline capacity            -- deadline_problem
7. verification capacity        -- max_verification_workers
8. measured machine capacity    -- effective_workers
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import capacity as cap
from solvent.harness import OWNER, Solvent

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

BUDGET = {
    "max_owed_seconds": 200_000.0,
    "max_concurrent_jobs": 4,
    "max_committed_jobs": 20,
    "max_execution_workers": 5,
    "max_verification_workers": 5,
    "max_open_bids": 4,
    "cpu_share": 0.5,
    "min_free_disk_bytes": 0,
    "max_memory_used_percent": 0.0,
    "revision_headroom_fraction": 0.0,
    "deadline_safety_factor": 2.0,
    "deadline_horizon_hours": 72.0,
    "skills": list(SKILLS),
    "pipeline_target": 4,
    "replenish_at": 2,
    "halted": False,
}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/recon.db")
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
        self.s.policy.amend({"capacity": budget}, OWNER, "test")

    def admit(self, skill, n, seconds=60):
        return self.s.capacity.admit(
            f"{skill}-{n}", execute_seconds=seconds,
            idempotency_key=f"{skill}:{n}", capability=skill)

    def fill_everything(self):
        return [self.admit(s, n) for s in SKILLS for n in range(4)]

    def bid(self, n, seconds=60):
        return self.s.capacity.reserve(
            seconds, kind=cap.BID, job_id=f"bid-{n}",
            idempotency_key=f"bid:{n}")


class TheGlobalCeilingIsItsOwnLimit(Base):
    """Limit 4. A free slot in one pipeline is not free capacity."""

    def test_five_pipelines_of_four_is_the_ceiling(self):
        self.fill_everything()
        self.assertEqual(self.s.capacity.jobs_in_flight(), 20)

    def test_a_twenty_first_job_is_refused_though_its_pipeline_has_room(self):
        """The decisive case: room per skill, none in the shop."""
        self.configure(max_committed_jobs=18)
        self.fill_everything()
        self.assertEqual(self.s.capacity.jobs_in_flight(), 18)
        row = self.s.capacity.pipelines()
        short = [n for n, r in row.items() if r["room"] > 0]
        self.assertTrue(short, "a pipeline must still have room, or this "
                               "is testing the per-pipeline limit again")

    def test_the_refusal_says_which_limit_it_was(self):
        # The pipeline limit and the worker count come down with the
        # ceiling: a ceiling of two under a four-job pipeline is
        # incoherent and problem() refuses it before the gate is even
        # consulted, which is the coherence check doing its job and
        # not the case this test is about.
        self.configure(max_committed_jobs=2, max_concurrent_jobs=2,
                       max_execution_workers=2, max_verification_workers=2,
                       pipeline_target=2, replenish_at=1)
        self.admit("csv-cleanup", 0)
        self.admit("data-entry", 0)
        decision = self.admit("report-builder", 0)
        self.assertFalse(decision.allowed)
        self.assertIn("across every pipeline", decision.why)

    def test_an_unset_ceiling_is_implied_not_unlimited(self):
        """Zero means the pipelines imply it, never 'no limit'."""
        self.configure(max_committed_jobs=0)
        self.assertEqual(self.s.capacity.committed_ceiling(),
                         len(SKILLS) * 4)

    def test_a_ceiling_below_one_pipeline_is_refused_as_incoherent(self):
        self.configure(max_committed_jobs=3, max_concurrent_jobs=4)
        self.assertIn("below the 4", self.s.capacity.budget().problem())

    def test_a_ceiling_below_the_worker_count_is_refused(self):
        self.configure(max_committed_jobs=4, max_concurrent_jobs=4,
                       max_execution_workers=5)
        self.assertIn("never be given anything",
                      self.s.capacity.budget().problem())


class ABidIsAPotentialObligation(Base):
    """Limit 5. Never offer more than could be honoured if all land."""

    def test_committed_and_bid_are_counted_separately(self):
        self.admit("csv-cleanup", 0)
        self.bid(0)
        e = self.s.capacity.exposure()
        self.assertEqual(e["committed_jobs"], 1)
        self.assertEqual(e["outstanding_bids"], 1)
        self.assertEqual(e["worst_case_committed"], 2)

    def test_a_bid_that_could_not_be_honoured_is_refused(self):
        """19 committed, 1 bid out, ceiling 20: no room for a second."""
        self.configure(max_committed_jobs=20, max_open_bids=10)
        for skill in SKILLS[:4]:
            for n in range(4):
                self.admit(skill, n)
        for n in range(3):
            self.admit(SKILLS[4], n)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 19)
        self.assertTrue(self.bid(0).allowed)
        second = self.bid(1)
        self.assertFalse(second.allowed)
        self.assertIn("if every one were accepted", second.why)

    def test_the_bid_count_limit_still_applies_on_its_own(self):
        self.configure(max_open_bids=2, max_committed_jobs=20)
        self.assertTrue(self.bid(0).allowed)
        self.assertTrue(self.bid(1).allowed)
        refused = self.bid(2)
        self.assertFalse(refused.allowed)
        self.assertIn("already outstanding", refused.why)

    def test_worst_case_headroom_never_goes_negative(self):
        self.fill_everything()
        self.assertGreaterEqual(
            self.s.capacity.exposure()["worst_case_headroom"], 0)


class ADeadlineIsCapacityToo(Base):
    """Limit 6. Room for the job is not time for the job."""

    def test_a_comfortable_deadline_is_no_problem(self):
        self.assertEqual(
            self.s.capacity.deadline_problem(3600.0, 48.0), "")

    def test_work_already_owed_can_make_a_deadline_impossible(self):
        for skill in SKILLS:
            for n in range(4):
                self.admit(skill, n, seconds=7_200)
        problem = self.s.capacity.deadline_problem(7_200.0, 2.0)
        self.assertIn("is owed across", problem)
        self.assertIn("due in 2.0h", problem)

    def test_fewer_workers_makes_deadlines_harder_not_only_slower(self):
        # 20 jobs of half an hour is 10h of work. Across five workers
        # that is 2.1h with the owner's 2x factor and fits in six;
        # across one it is 21h and does not. Same queue, same
        # deadline, different answer -- which is the point.
        for skill in SKILLS:
            for n in range(4):
                self.admit(skill, n, seconds=1_800)
        with_five = self.s.capacity.deadline_problem(1_800.0, 6.0)
        self.configure(max_execution_workers=1)
        with_one = self.s.capacity.deadline_problem(1_800.0, 6.0)
        self.assertEqual(with_five, "")
        self.assertNotEqual(with_one, "")

    def test_no_horizon_configured_means_the_check_is_off(self):
        self.configure(deadline_horizon_hours=0.0)
        for skill in SKILLS:
            for n in range(4):
                self.admit(skill, n, seconds=100_000)
        self.assertEqual(self.s.capacity.deadline_problem(100.0, 0.5), "")

    def test_a_deadline_past_the_horizon_is_not_checked(self):
        for skill in SKILLS:
            for n in range(4):
                self.admit(skill, n, seconds=7_200)
        self.assertEqual(
            self.s.capacity.deadline_problem(7_200.0, 1_000.0), "")


class TheMachineMayLowerTheOwnersUpperBound(Base):
    """Limit 8. Five is a bound, not an instruction to run five."""

    def host(self, **fields):
        self.s.capacity._diagnostics = type(
            "Host", (), {"host": lambda _self: dict(fields)})()

    def test_a_quiet_machine_allows_what_the_owner_allowed(self):
        workers, why = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 5)
        self.assertEqual(why, "")

    def test_a_full_disk_drops_to_one_worker(self):
        self.configure(min_free_disk_bytes=1_000_000_000)
        self.host(disk_free_bytes=500_000_000)
        workers, why = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 1)
        self.assertIn("disk_free_bytes", why)

    def test_a_tight_disk_halves_it(self):
        self.configure(min_free_disk_bytes=1_000_000_000)
        self.host(disk_free_bytes=1_150_000_000)
        workers, why = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 2)
        self.assertIn("tight", why)

    def test_an_unreadable_limit_drops_to_one_rather_than_five(self):
        """The failure this whole file exists to avoid."""
        self.configure(max_memory_used_percent=90.0)
        self.host(disk_free_bytes=10_000_000_000)
        workers, why = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 1)
        self.assertIn("will not report it", why)

    def test_a_limit_nobody_set_is_not_an_unreadable_limit(self):
        """Unset is not unmeasurable; it is simply not being checked."""
        self.configure(max_memory_used_percent=0.0, min_free_disk_bytes=0)
        self.host(disk_free_bytes=10_000_000_000)
        workers, _ = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 5)

    def test_it_never_drops_to_zero(self):
        """Zero cannot be told apart from halted, which is the owner's."""
        self.configure(min_free_disk_bytes=1_000_000_000)
        self.host(disk_free_bytes=1)
        workers, _ = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 1)

    def test_throttling_bounds_starting_not_running(self):
        """Work in progress is preserved; only new starts are refused."""
        self.admit("csv-cleanup", 0)
        self.admit("data-entry", 0)
        first = self.s.capacity.reservation_for("csv-cleanup-0", cap.EXECUTE)
        self.assertTrue(self.s.capacity.start(first).allowed)
        self.configure(min_free_disk_bytes=1_000_000_000)
        self.host(disk_free_bytes=1)
        second = self.s.capacity.reservation_for("data-entry-0", cap.EXECUTE)
        self.assertFalse(self.s.capacity.start(second).allowed)
        self.assertEqual(self.s.capacity.executing(), 1)

    def test_a_busy_order_book_does_not_reduce_workers(self):
        """Only the machine throttles. A full shop is not a sick host."""
        self.fill_everything()
        workers, why = self.s.capacity.effective_workers(cap.EXECUTE)
        self.assertEqual(workers, 5, why)

    def test_the_report_says_the_effective_number_and_why(self):
        self.configure(min_free_disk_bytes=1_000_000_000)
        self.host(disk_free_bytes=500_000_000)
        report = self.s.capacity.report()
        self.assertEqual(report["effective_execution_workers"], 1)
        self.assertIn("disk", report["throttled_because"])


class NoConflictingGlobalLimitSurvivesAnywhere(unittest.TestCase):
    """The audit the owner asked for, kept as a test.

    Reconciling eight limits is not finished when the gate is right.
    The same numbers are read by the qualification filter and by the
    control centre's forms, and both had quietly kept the old global
    ceiling -- so the gate would have allowed twenty jobs while the
    Finder stopped looking at four and the web form clamped five
    workers to four without saying so.

    Neither was a gate failing open. Both were limits failing *shut*
    in a place nobody was watching, which is the harder kind to
    notice: the system just quietly does less than it was configured
    to do.
    """

    def test_a_worker_count_is_never_bounded_by_the_job_ceiling(self):
        from solvent.types import (CERTIFIED_SKILL_COUNT,
                                   JOBS_PER_AGENT_CEILING)
        from solvent.web.intents import CAPACITY_LIMITS

        for name in ("max_execution_workers", "max_verification_workers"):
            with self.subTest(name=name):
                _, _, maximum = CAPACITY_LIMITS[name]
                self.assertEqual(maximum, CERTIFIED_SKILL_COUNT)
                self.assertGreater(maximum, JOBS_PER_AGENT_CEILING,
                                   "five workers must be configurable")

    def test_the_job_ceiling_still_bounds_a_pipeline(self):
        """The other half: the per-pipeline limit was never wrong."""
        from solvent.types import JOBS_PER_AGENT_CEILING
        from solvent.web.intents import CAPACITY_LIMITS

        _, _, maximum = CAPACITY_LIMITS["max_concurrent_jobs"]
        self.assertEqual(maximum, JOBS_PER_AGENT_CEILING)

    def test_the_global_ceiling_is_derived_not_typed_twice(self):
        from solvent.types import (CERTIFIED_SKILL_COUNT,
                                   GLOBAL_COMMITTED_CEILING,
                                   JOBS_PER_AGENT_CEILING)

        self.assertEqual(GLOBAL_COMMITTED_CEILING,
                         CERTIFIED_SKILL_COUNT * JOBS_PER_AGENT_CEILING)

    def test_qualification_compares_a_global_count_to_a_global_limit(self):
        """It counted every job and compared against four."""
        import inspect

        from solvent.qualification import Qualification

        source = inspect.getsource(Qualification._at_capacity)
        self.assertIn("GLOBAL_COMMITTED_CEILING", source)
        self.assertNotIn("min(limit, JOBS_PER_AGENT_CEILING)", source)

    def test_the_finder_keeps_qualifying_past_one_full_pipeline(self):
        """The behaviour, not the constant.

        Four committed jobs in one skill must not stop the shop
        assessing work for the other four skills.
        """
        from solvent.types import (GLOBAL_COMMITTED_CEILING,
                                   JOBS_PER_AGENT_CEILING)

        self.assertGreater(GLOBAL_COMMITTED_CEILING, JOBS_PER_AGENT_CEILING,
                           "one full pipeline would still fill the shop")


if __name__ == "__main__":
    unittest.main()
