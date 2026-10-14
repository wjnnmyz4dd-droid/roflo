"""The five skills, measured — and what those measurements may be used for.

A benchmark run is a real execution. It is still not a client job, and
the distinction decides whether DeskPilot may promise a delivery time
from it. These tests pin that distinction, because getting it wrong is
not a reporting error: it is DeskPilot quoting a millisecond deadline
on a file it has never seen.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.finder import ExecutionHistory, Finder
from solvent.harness import CAPABILITIES, OWNER, Solvent
from tools.benchmark_skills import SKILLS, eligibility, one_run


class TheFiveAreTheFive(unittest.TestCase):

    def test_the_benchmark_covers_every_certified_skill(self):
        self.assertEqual(set(SKILLS), set(CAPABILITIES))

    def test_there_are_five_of_them(self):
        self.assertEqual(len(SKILLS), 5)


class EverySkillRunsAndIsChecked(unittest.TestCase):
    """One run each, through the real execute and verify paths."""

    def test_each_skill_executes_and_verifies(self):
        import pathlib

        with tempfile.TemporaryDirectory() as name:
            box = pathlib.Path(name)
            for skill in SKILLS:
                with self.subTest(skill=skill):
                    timing = one_run(skill, 0, box)
                    self.assertGreater(timing["execute_seconds"], 0)
                    self.assertGreater(timing["produced_bytes"], 0)
                    self.assertGreater(
                        timing["checks"], 0,
                        "no check ran, so the verification timing is a "
                        "measurement of nothing")


class ABenchmarkIsNotAClientJob(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/bench.db"
        self.s = Solvent(self.db)
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def record(self, outcome, n=7, capability="csv-cleanup"):
        for i in range(n):
            self.s.orchestrator.record_execution(
                f"{outcome}:{i}", capability=capability,
                wall_seconds=0.0007, cpu_seconds=0.0007, units=605,
                outcome=outcome)

    def history(self, **kw):
        return ExecutionHistory.from_store(self.s.store, **kw)

    def test_benchmark_runs_do_not_enter_the_pricing_history(self):
        self.record("BENCHMARK")
        self.assertEqual(self.history().count("csv-cleanup"), 0)

    def test_benchmark_runs_are_still_visible_when_asked_for(self):
        """Excluded from pricing, not hidden from the owner."""
        self.record("BENCHMARK")
        self.assertEqual(
            self.history(include_benchmarks=True).count("csv-cleanup"), 7)

    def test_client_work_does_enter_the_pricing_history(self):
        self.record("DELIVERED")
        self.assertEqual(self.history().count("csv-cleanup"), 7)

    def test_a_skill_with_only_benchmarks_cannot_be_bid_on(self):
        self.record("BENCHMARK")
        finder = Finder(self.s.discovery, self.s.policy, self.s.capability,
                        self.s.orchestrator, self.s.store)
        estimate = finder.completion_estimate("csv-cleanup",
                                              history=self.history())
        self.assertFalse(estimate.known)
        self.assertEqual(finder.promised_seconds(estimate)[0], 0.0)

    def test_the_same_runs_as_client_work_would_be_bid_on(self):
        """The exclusion is about provenance, not about the numbers."""
        self.record("DELIVERED")
        finder = Finder(self.s.discovery, self.s.policy, self.s.capability,
                        self.s.orchestrator, self.s.store)
        estimate = finder.completion_estimate("csv-cleanup",
                                              history=self.history())
        self.assertTrue(estimate.known)

    def test_eligibility_reports_what_was_excluded_and_why(self):
        self.record("BENCHMARK")
        self.s.store.close()
        row = eligibility(self.db)["csv-cleanup"]
        self.s = Solvent(self.db)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["samples"], 0)
        self.assertEqual(row["benchmark_runs_excluded"], 7)

    def test_a_fresh_install_can_bid_on_nothing(self):
        for skill, row in eligibility("").items():
            with self.subTest(skill=skill):
                self.assertFalse(row["eligible"])


if __name__ == "__main__":
    unittest.main()
