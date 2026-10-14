"""The operator's bidder controls, and the direction they may move in.

Lowering a capacity limit can only make DeskPilot take on less, which
is safe enough to apply from a browser without a signature. Raising
one is a promise to do more work than a moment ago, and goes through
an owner approval like every other consequential change. These tests
pin that line, and pin that the page cannot show a limit the gate is
not using.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from solvent.harness import OWNER, Solvent, consume_owner_intents
from solvent.types import JOBS_PER_AGENT_CEILING
from solvent.web.intents import (CAPACITY_LIMITS, INTENTS, IntentWriter,
                                 classify)
from solvent.web.readmodel import ReadModel

KEY_ENV = "SOLVENT_OWNER_KEY"
TEST_KEY = "0" * 64

BUDGET = {"max_owed_seconds": 3600.0, "max_concurrent_jobs": 3,
          "max_open_bids": 4, "cpu_share": 0.5,
          "max_execution_workers": 2, "max_verification_workers": 2,
          "revision_headroom_fraction": 0.4, "min_free_disk_bytes": 0,
          "deadline_safety_factor": 2.0, "halted": False}


class Spooled(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)
        self.spool = str(self.root / "intents")
        self.db = str(self.root / "solvent.db")
        previous = os.environ.get(KEY_ENV)
        os.environ[KEY_ENV] = TEST_KEY
        self.addCleanup(lambda: (os.environ.__setitem__(KEY_ENV, previous)
                                 if previous is not None
                                 else os.environ.pop(KEY_ENV, None)))
        self.s = Solvent(self.db)
        self.addCleanup(self.close)
        self.s.policy.amend({"capacity": dict(BUDGET)}, OWNER, "test")
        self.writer = IntentWriter(self.spool)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def ask(self, verb, **params):
        why = params.pop("why", "because the owner said so")
        self.writer.write(verb=verb, requested_by="owner:control-centre",
                          why=why, subject="", params=params)
        return consume_owner_intents(self.s, spool=self.spool)

    def budget(self):
        return self.s.capacity.budget()

    def outcomes(self, results):
        """Everything the runtime said, including why it refused."""
        return " ".join(
            f"{r.get('outcome', '')} {r.get('why', '')}" for r in results)


class LoweringALimitIsSafe(Spooled):

    def test_a_limit_can_be_lowered_from_the_browser(self):
        self.ask("tighten_capacity", max_owed_seconds="1200")
        self.assertEqual(self.budget().max_owed_seconds, 1200.0)

    def test_several_at_once(self):
        self.ask("tighten_capacity", max_owed_seconds="1200",
                 max_concurrent_jobs="1")
        self.assertEqual(self.budget().max_concurrent_jobs, 1)
        self.assertEqual(self.budget().max_owed_seconds, 1200.0)

    def test_bidding_can_be_stopped(self):
        self.ask("tighten_capacity", halted="true")
        decision = self.s.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)

    def test_the_verb_is_classified_safe(self):
        self.assertEqual(classify("tighten_capacity"), "SAFE")


class RaisingALimitIsNot(Spooled):

    def test_an_increase_is_refused_on_the_safe_verb(self):
        results = self.ask("tighten_capacity", max_owed_seconds="99999")
        self.assertEqual(self.budget().max_owed_seconds, 3600.0)
        self.assertIn("not a tightening", self.outcomes(results))

    def test_the_refusal_names_the_verb_that_would_work(self):
        results = self.ask("tighten_capacity", max_concurrent_jobs="4")
        self.assertIn("set_capacity_budget", self.outcomes(results))

    def test_an_increase_is_refused_rather_than_clamped(self):
        """Clamping would write a number nobody asked for and succeed."""
        self.ask("tighten_capacity", max_owed_seconds="99999")
        self.assertEqual(self.budget().max_owed_seconds, 3600.0)

    def test_restarting_bidding_is_not_a_tightening(self):
        self.ask("tighten_capacity", halted="true")
        results = self.ask("tighten_capacity", halted="false")
        self.assertIn("owner approval", self.outcomes(results))
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)

    def test_the_raising_verb_is_consequential(self):
        self.assertEqual(classify("set_capacity_budget"), "CONSEQUENTIAL")

    def test_the_consequential_verb_does_nothing_without_an_approval(self):
        results = self.ask("set_capacity_budget", max_owed_seconds="99999")
        self.assertEqual(self.budget().max_owed_seconds, 3600.0)
        self.assertTrue(self.outcomes(results).strip())


class TheCeilingIsNotAFormField(Spooled):

    def test_neither_verb_may_exceed_the_job_ceiling(self):
        for verb in ("tighten_capacity", "set_capacity_budget"):
            with self.subTest(verb=verb):
                self.ask(verb, max_concurrent_jobs="9")
                self.assertLessEqual(self.budget().max_concurrent_jobs,
                                     JOBS_PER_AGENT_CEILING)

    def test_the_declared_bound_is_the_ceiling(self):
        _, _, high = CAPACITY_LIMITS["max_concurrent_jobs"]
        self.assertEqual(high, JOBS_PER_AGENT_CEILING)
        _, _, workers = CAPACITY_LIMITS["max_execution_workers"]
        self.assertEqual(workers, JOBS_PER_AGENT_CEILING)

    def test_an_unknown_limit_is_refused_by_name(self):
        results = self.ask("tighten_capacity", egress="everything")
        self.assertIn("not a capacity limit", self.outcomes(results))

    def test_both_verbs_are_in_the_closed_list(self):
        self.assertIn("tighten_capacity", INTENTS)
        self.assertIn("set_capacity_budget", INTENTS)


class ThePageShowsWhatTheGateUses(Spooled):

    def read(self):
        self.s.store.close()
        model = ReadModel(self.db)
        self.addCleanup(lambda: None)
        return model

    def test_the_budget_shown_is_the_budget_in_force(self):
        shown = self.read().capacity_budget()
        self.assertEqual(shown["max_owed_seconds"]["value"], 3600.0)
        self.assertEqual(shown["max_concurrent_jobs"]["value"], 3)

    def test_what_is_held_is_shown(self):
        self.s.capacity.admit("j1", execute_seconds=100, verify_seconds=20,
                              idempotency_key="k1")
        held = self.read().capacity_in_flight()
        self.assertEqual(held["jobs"], 1)
        self.assertEqual(held["owed_seconds"], 160.0)   # 100 x 1.4 + 20

    def test_a_skill_with_no_client_work_reads_as_blocked(self):
        self.s.orchestrator.record_execution(
            "b:1", capability="csv-cleanup", wall_seconds=0.001,
            cpu_seconds=0.001, units=10, outcome="BENCHMARK")
        row = self.read().bid_eligibility()["csv-cleanup"]
        self.assertFalse(row["eligible"])
        self.assertEqual(row["benchmark_runs"], 1)
        self.assertEqual(row["client_runs"], 0)

    def test_client_work_reads_as_biddable(self):
        for n in range(5):
            self.s.orchestrator.record_execution(
                f"j{n}", capability="csv-cleanup", wall_seconds=1.0,
                cpu_seconds=1.0, units=10, outcome="DELIVERED")
        row = self.read().bid_eligibility()["csv-cleanup"]
        self.assertTrue(row["eligible"])
        self.assertTrue(row["confident"])

    def test_the_page_does_not_import_the_composition_root(self):
        """Already asserted elsewhere; repeated because this page tempted it.

        The obvious way to list the five skills is to import
        CAPABILITIES from the harness, which would give the web
        package a handle on everything the harness wires together.
        """
        from tests_solvent.fixtures import source_names

        self.assertNotIn("solvent.harness",
                         source_names("solvent/web/readmodel.py"))


if __name__ == "__main__":
    unittest.main()
