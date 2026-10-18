"""A crash between acceptance and the first line of work.

Measured in the eleven-point crash matrix: killing the process inside
``begin_execution`` leaves the job in ACCEPTED, still holding its pipeline
slot, with nothing that will ever start it. Reconciliation did not touch
it (ACCEPTED is not mid-flight) and resumption did not either (ACCEPTED is
not a recompute in progress). The state timeout escalates it eventually,
two hours later, and only to a person.

ACCEPTED is a weaker position than EXECUTING: a job can be in it without
any work having been attempted. So resumption asks for durable evidence
that execution really had begun — a Governor budget grant, and a source
artifact on record — and escalates explicitly when that evidence is
missing, rather than guessing in either direction.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.harness import (MAX_RESUME_ATTEMPTS, RESUME_EVENT, Solvent,
                             recover_after_restart, resume_interrupted)
from solvent.types import BlockedOn, JobState, LocationSignals


class AcceptedJobs(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "accepted.db"))
        self.addCleanup(self.close)
        self.source = self.dir / "customers.csv"
        self.source.write_text("Name,Email\nAcme Ltd,a@acme.test\n",
                               encoding="utf-8")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def accepted_job(self, *, granted=True, with_source=True) -> str:
        """A job walked to ACCEPTED through legal transitions.

        ``granted`` and ``with_source`` are the two pieces of durable
        evidence the repair turns on, so every test can remove exactly one.
        """
        job_id = self.s.orchestrator.intake(
            title="interrupted at the start of the work", client_id="c",
            quoted_cents=5_000, signals=LocationSignals(), initiator="test")
        for step in (JobState.QUALIFYING, JobState.ACCEPTED):
            self.s.orchestrator._transition(job_id, step, why="walking",
                                            initiator="test")
        if granted:
            self.s.governor.grant_budget(job_id=job_id, decision_id="d-test",
                                         authorized_cents=5_000)
        if with_source:
            self.s.orchestrator.register_artifact(
                job_id=job_id, role="SOURCE", path=str(self.source),
                initiator="owner:demo")
        self.s.capacity.reserve(3600.0, kind="EXECUTE", job_id=job_id,
                                capability="csv-cleanup",
                                idempotency_key=f"commit:{job_id}", why="test")
        return job_id

    def resume(self, **kwargs):
        defaults = dict(source=str(self.source), workdir=self.box.name,
                        capability="csv-cleanup")
        return resume_interrupted(self.s, **{**defaults, **kwargs})

    # ------------------------------------------------------- the defect
    def test_an_interrupted_accepted_job_is_picked_up(self):
        job_id = self.accepted_job()
        records = self.resume()
        self.assertEqual([r["job_id"] for r in records], [job_id],
                         f"the job was not considered at all: {records}")
        self.assertEqual(records[0]["found_in"], "ACCEPTED")

    def test_it_does_not_stay_in_accepted(self):
        job_id = self.accepted_job()
        self.resume()
        self.assertNotEqual(self.s.orchestrator.job(job_id).state,
                            JobState.ACCEPTED,
                            "the job was left exactly where the crash left it")

    def test_nothing_is_left_in_accepted_after_the_whole_restart_path(self):
        self.accepted_job()
        recover_after_restart(self.s, source=str(self.source),
                              workdir=self.box.name, capability="csv-cleanup")
        for job in self.s.orchestrator.jobs():
            self.assertNotEqual(
                job.state, JobState.ACCEPTED,
                f"{job.id} is still in ACCEPTED, holding its slot")

    # --------------------------------------------- evidence, not guessing
    def test_an_accepted_job_the_governor_never_funded_is_escalated(self):
        """No grant means work never started. Resuming would spend
        unbudgeted compute on a job the Governor did not authorise."""
        job_id = self.accepted_job(granted=False)
        records = self.resume()
        self.assertEqual(records[0]["outcome"], "ESCALATED", records[0])
        job = self.s.orchestrator.job(job_id)
        self.assertEqual(job.state, JobState.BLOCKED)
        self.assertEqual(job.blocked_on, BlockedOn.OWNER)

    def test_an_accepted_job_with_no_source_on_record_is_escalated(self):
        """It cannot say what file it was given, so it cannot redo the work."""
        job_id = self.accepted_job(with_source=False)
        records = self.resume()
        self.assertEqual(records[0]["outcome"], "ESCALATED", records[0])
        self.assertIn("source", records[0].get("detail", "").lower())
        self.assertEqual(self.s.orchestrator.job(job_id).state,
                         JobState.BLOCKED)

    def test_an_escalated_accepted_job_is_not_discarded(self):
        job_id = self.accepted_job(granted=False)
        self.resume()
        self.assertIn(job_id, {j.id for j in self.s.orchestrator.jobs()})

    # ------------------------------------------------------ bounded retry
    def test_attempts_are_bounded(self):
        job_id = self.accepted_job()
        for _ in range(MAX_RESUME_ATTEMPTS):
            self.s.audit.record(event=RESUME_EVENT, authority="orchestrator",
                                initiator="test", why="earlier attempt",
                                job_id=job_id, decision="RESUMED")
        records = self.resume()
        self.assertEqual(records[0]["outcome"], "ESCALATED",
                         "a job past its attempt limit was started again")

    def test_the_bound_survives_repeated_restarts(self):
        """The count lives in the append-only log, not in this process."""
        job_id = self.accepted_job()
        seen = []
        for _ in range(MAX_RESUME_ATTEMPTS + 2):
            records = self.resume()
            if records:
                seen.append(records[0]["outcome"])
            # Put the job back where the next crash would leave it.
            job = self.s.orchestrator.job(job_id)
            if job.state is JobState.BLOCKED:
                break
            if job.state is not JobState.ACCEPTED:
                self.s.orchestrator._transition(
                    job_id, JobState.BLOCKED, why="pretend it crashed again",
                    initiator="test", blocked_on=BlockedOn.OWNER,
                    resume_state=JobState.ACCEPTED)
                break
        attempts = sum(1 for e in self.s.audit.events(job_id)
                       if e.get("event") == RESUME_EVENT)
        self.assertLessEqual(attempts, MAX_RESUME_ATTEMPTS,
                             f"resumed {attempts} times; outcomes {seen}")

    def test_a_crash_during_recovery_leaves_the_attempt_recorded(self):
        """The audit entry is written before the work, so a kill mid-resume
        still counts against the limit on the next restart."""
        job_id = self.accepted_job()
        from solvent import harness

        original = harness._execute_job

        def exploding(*args, **kwargs):
            raise RuntimeError("killed in the middle of the recovery")

        harness._execute_job = exploding
        self.addCleanup(setattr, harness, "_execute_job", original)
        records = self.resume()
        harness._execute_job = original
        self.assertEqual(records[0]["outcome"], "FAILED", records[0])
        attempts = sum(1 for e in self.s.audit.events(job_id)
                       if e.get("event") == RESUME_EVENT)
        self.assertEqual(attempts, 1,
                         "a resumption that died left no trace of the attempt")
        self.assertTrue(
            self.s.resilience.incidents(),
            "a resumption that died recorded no incident")

    # -------------------------------------------------- no double effects
    def test_resumption_does_not_invoice_twice(self):
        self.accepted_job()
        self.resume()
        self.resume()
        invoiced = self.s.ledger.invoiced_job_ids()
        self.assertEqual(len(invoiced), len(set(invoiced)))

    def test_the_pipeline_slot_is_not_doubled(self):
        self.accepted_job()
        self.resume()
        self.assertLessEqual(self.s.capacity.jobs_in_flight("csv-cleanup"), 1,
                             "recovery took a second slot for the same job")

    def test_the_audit_chain_survives(self):
        self.accepted_job()
        self.resume()
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
