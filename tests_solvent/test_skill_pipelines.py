"""Five skill pipelines, five workers, and the difference between them.

The owner's shop is five skills, each holding up to four jobs and
running one. Two counts, not one: a pipeline is full at four and a
worker is busy at one, and conflating them makes a five-worker shop
read as full the moment any four jobs exist.

The three-job default and four-job ceiling are per pipeline. Five
pipelines at four is twenty committed and five running, and that is
the configuration these tests pin.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import capacity as cap
from solvent.harness import OWNER, Solvent
from solvent.types import JOBS_PER_AGENT_CEILING

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

BUDGET = {
    "max_owed_seconds": 200_000.0,
    "max_concurrent_jobs": 4,          # per pipeline
    "max_execution_workers": 5,        # one per skill
    "max_verification_workers": 5,
    "max_open_bids": 4,
    "cpu_share": 0.5,
    "min_free_disk_bytes": 0,
    "revision_headroom_fraction": 0.0,
    "skills": list(SKILLS),
    "pipeline_target": 4,
    "replenish_at": 2,
    "halted": False,
}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/pipelines.db"
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
        self.s.policy.amend({"capacity": budget}, OWNER, "test: five queues")

    def admit(self, skill, n, seconds=60):
        return self.s.capacity.admit(
            f"{skill}-{n}", execute_seconds=seconds,
            idempotency_key=f"{skill}:{n}", capability=skill)

    def fill(self, skill, upto=4):
        return [self.admit(skill, n) for n in range(upto)]

    def start(self, skill, n):
        held = self.s.capacity.reservation_for(f"{skill}-{n}", cap.EXECUTE)
        self.assertTrue(held, f"no execute reservation for {skill}-{n}")
        return self.s.capacity.start(held)


class FivePipelinesOfFour(Base):

    def test_each_skill_holds_four(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                taken = [d.allowed for d in self.fill(skill, upto=6)]
                self.assertEqual(taken.count(True), 4)

    def test_twenty_jobs_are_committed_in_all(self):
        for skill in SKILLS:
            self.fill(skill)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 20)

    def test_one_full_pipeline_does_not_block_another(self):
        """The failure the global count would have caused."""
        self.fill("csv-cleanup")
        self.assertEqual(self.s.capacity.jobs_in_flight("csv-cleanup"), 4)
        self.assertTrue(self.admit("data-entry", 0).allowed)

    def test_the_default_is_three_per_pipeline(self):
        # The target has to come down with it: a target of four jobs
        # in a pipeline that may hold three is refused, which is the
        # validation working rather than a problem here.
        self.configure(max_concurrent_jobs=3, pipeline_target=3,
                       replenish_at=1)
        taken = [d.allowed for d in self.fill("csv-cleanup", upto=6)]
        self.assertEqual(taken.count(True), 3)

    def test_five_per_pipeline_is_refused_as_a_configuration_error(self):
        self.configure(max_concurrent_jobs=JOBS_PER_AGENT_CEILING + 1)
        decision = self.s.capacity.room_for(1.0, capability="csv-cleanup")
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.NOT_CONFIGURED)

    def test_the_refusal_names_the_pipeline_that_is_full(self):
        self.fill("csv-cleanup")
        decision = self.admit("csv-cleanup", 9)
        self.assertFalse(decision.allowed)
        self.assertIn("csv-cleanup's pipeline", decision.why)


class AWorkerTakesOneJobAtATime(Base):

    def test_a_full_pipeline_is_not_a_busy_worker(self):
        self.fill("csv-cleanup")
        self.assertEqual(self.s.capacity.jobs_in_flight("csv-cleanup"), 4)
        self.assertEqual(self.s.capacity.executing("csv-cleanup"), 0)

    def test_starting_one_occupies_the_skill_worker(self):
        self.fill("csv-cleanup")
        self.assertTrue(self.start("csv-cleanup", 0).allowed)
        self.assertEqual(self.s.capacity.executing("csv-cleanup"), 1)

    def test_a_second_job_on_the_same_skill_will_not_start(self):
        self.fill("csv-cleanup")
        self.start("csv-cleanup", 0)
        second = self.start("csv-cleanup", 1)
        self.assertFalse(second.allowed)
        self.assertIn("takes one at a time", second.why)

    def test_five_skills_run_five_jobs_at_once(self):
        for skill in SKILLS:
            self.fill(skill)
        started = sum(1 for skill in SKILLS
                      if self.start(skill, 0).allowed)
        self.assertEqual(started, 5)
        self.assertEqual(self.s.capacity.executing(), 5)

    def test_a_sixth_worker_is_refused(self):
        self.configure(max_execution_workers=3)
        for skill in SKILLS:
            self.fill(skill, upto=1)
        started = [self.start(skill, 0).allowed for skill in SKILLS]
        self.assertEqual(started.count(True), 3)

    def test_finishing_frees_the_worker_for_the_next_job(self):
        self.fill("csv-cleanup")
        self.start("csv-cleanup", 0)
        held = self.s.capacity.reservation_for("csv-cleanup-0", cap.EXECUTE)
        self.s.capacity.consume(held, "delivered")
        self.assertEqual(self.s.capacity.executing("csv-cleanup"), 0)
        self.assertTrue(self.start("csv-cleanup", 1).allowed)

    def test_only_execution_occupies_a_worker(self):
        from solvent.errors import FailClosed

        self.fill("csv-cleanup", upto=1)
        check = self.s.capacity.reserve(
            10, kind=cap.VERIFY, job_id="csv-cleanup-0",
            idempotency_key="v1", capability="csv-cleanup")
        with self.assertRaises(FailClosed):
            self.s.capacity.start(check.reservation_id)

    def test_running_work_still_fills_its_pipeline_slot(self):
        """Starting a job moves it; it does not finish it."""
        self.fill("csv-cleanup")
        self.start("csv-cleanup", 0)
        row = self.s.capacity.pipelines()["csv-cleanup"]
        self.assertEqual(row["committed"], 4)
        self.assertEqual(row["room"], 0)
        self.assertEqual(self.s.capacity.jobs_in_flight("csv-cleanup"), 4)

    def test_running_work_is_still_owed(self):
        self.fill("csv-cleanup", upto=1)
        before = self.s.capacity.owed_seconds()
        self.start("csv-cleanup", 0)
        self.assertEqual(self.s.capacity.owed_seconds(), before)


class ReplenishmentIsReportedNotDecided(Base):

    def test_an_empty_pipeline_asks_for_work(self):
        for skill, row in self.s.capacity.pipelines().items():
            with self.subTest(skill=skill):
                self.assertTrue(row["needs_replenishment"])
                self.assertEqual(row["committed"], 0)

    def test_a_full_pipeline_does_not(self):
        self.fill("csv-cleanup")
        row = self.s.capacity.pipelines()["csv-cleanup"]
        self.assertFalse(row["needs_replenishment"])
        self.assertEqual(row["room"], 0)

    def test_it_asks_again_at_the_threshold(self):
        self.fill("csv-cleanup")
        for n in range(2):
            held = self.s.capacity.reservation_for(f"csv-cleanup-{n}",
                                                   cap.EXECUTE)
            self.s.capacity.consume(held, "delivered")
        row = self.s.capacity.pipelines()["csv-cleanup"]
        self.assertEqual(row["committed"], 2)
        self.assertTrue(row["needs_replenishment"])

    def test_three_is_above_the_threshold(self):
        self.fill("csv-cleanup")
        held = self.s.capacity.reservation_for("csv-cleanup-0", cap.EXECUTE)
        self.s.capacity.consume(held, "delivered")
        self.assertFalse(
            self.s.capacity.pipelines()["csv-cleanup"]["needs_replenishment"])

    def test_every_approved_skill_has_a_pipeline_even_when_empty(self):
        self.assertEqual(set(self.s.capacity.pipelines()), set(SKILLS))

    def test_no_skills_configured_means_no_pipelines(self):
        self.configure(skills=[])
        self.assertEqual(self.s.capacity.pipelines(), {})

    def test_reporting_commits_nothing(self):
        self.fill("csv-cleanup", upto=1)
        before = self.s.capacity.owed_seconds()
        for _ in range(3):
            self.s.capacity.pipelines()
        self.assertEqual(self.s.capacity.owed_seconds(), before)


class ThePipelinesSurviveARestart(Base):

    def restart(self):
        self.s.store.close()
        self.s = Solvent(self.db)

    def test_committed_work_keeps_its_skill(self):
        self.fill("csv-cleanup", upto=3)
        self.fill("data-entry", upto=2)
        self.restart()
        self.assertEqual(self.s.capacity.jobs_in_flight("csv-cleanup"), 3)
        self.assertEqual(self.s.capacity.jobs_in_flight("data-entry"), 2)

    def test_a_running_job_is_still_running_afterwards(self):
        self.fill("csv-cleanup", upto=1)
        self.start("csv-cleanup", 0)
        self.restart()
        self.assertEqual(self.s.capacity.executing("csv-cleanup"), 1)

    def test_the_worker_is_not_freed_by_a_restart(self):
        """A reboot must not look like the job finished."""
        self.fill("csv-cleanup", upto=2)
        self.start("csv-cleanup", 0)
        self.restart()
        self.assertFalse(self.start("csv-cleanup", 1).allowed)


if __name__ == "__main__":
    unittest.main()
