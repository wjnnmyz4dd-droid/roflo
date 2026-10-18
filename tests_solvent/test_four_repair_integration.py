"""The four engineering repairs, exercised together on one shop.

Each repair has its own tests. Passing separately is not the same claim:
the repairs touch the same path, in order -- qualification reserves a
per-skill slot, the T2 job needs an independent reading of a .csv, the
delivery gate needs the owner's signature, and a restart has to pick the
work back up without re-sending anything or losing the slot. This file is
the one scenario where all four have to hold at once.

Nothing here reaches the network, no real customer action is taken, and
the signing key is generated per test and thrown away with the shop.
"""

from __future__ import annotations

import os
import secrets
import unittest

from solvent.harness import OWNER, recover_after_restart
from solvent.types import ActionClass, BlockedOn, JobState
from tests_solvent.test_business_cycle import AGREED, CSV_POSTING, Cycle, req


class FourRepairs(Cycle):
    """One shop with a signing key, per-skill pools and a T2 csv job."""

    #: $1,500 puts the job in C_HIGH, the consequence tier that demands an
    #: independent review. Below it the cycle never asks for one and the
    #: csv reader repair is not on the path.
    posting = {**CSV_POSTING, "ref": "four-repairs",
               "quoted_cents": 150_000, "platform_fee_cents": 15_000}

    def setUp(self):
        os.environ["SOLVENT_OWNER_KEY"] = secrets.token_hex(32)
        self.addCleanup(os.environ.pop, "SOLVENT_OWNER_KEY", None)
        super().setUp()
        self.assertTrue(self.s.owner.available)
        # Exact duplicates rather than the suite's whitespace ones: the
        # protected-value check compares against the source's *distinct*
        # rows, and two rows that differ only in whitespace are distinct
        # until the worker trims them. That interaction is a separate
        # question from the four repairs, and this file is not the place
        # to answer it.
        self.source.write_text(
            "Name,Email,Amount\n"
            "Acme Ltd,a@acme.test,100.00\n"
            "Acme Ltd,a@acme.test,100.00\n"
            "Beta Co,b@beta.test,250.50\n", encoding="utf-8")
        self.s.policy.amend({"capacity": {
            "max_owed_seconds": 20 * 3600, "max_concurrent_jobs": 4,
            "pipeline_target": 4, "replenish_at": 2, "max_committed_jobs": 20,
            "cpu_share": 0.5, "max_execution_workers": 5,
            "max_verification_workers": 5}},
            OWNER, "test: four-way integration, four slots per pipeline")

    def approver(self):
        """The owner's tooling: one explicit signed approval per delivery."""
        def approve(*, job_id, artifact=None):
            return self.s.owner.issue(
                owner_identity=OWNER, subject="deliver the reviewed file",
                action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
                job_id=job_id)
        return approve

    #: The cleanup brief plus one requirement a second implementation can
    #: actually recompute. Without it the reviewer refuses -- correctly:
    #: "drop duplicates, trim whitespace, parses as csv" gives an
    #: independent reader nothing to disagree about beyond the file
    #: parsing, which is not a second opinion. Repair 2 gives csv a
    #: genuinely separate parser; it does not invent checkable content.
    agreed = AGREED + [req("R4", "preserve_columns",
                           {"columns": ["Name", "Email"]})]

    def full_cycle(self, **kwargs):
        return self.runCycle(delivery_approver=self.approver(),
                             agreed_requirements=self.agreed, **kwargs)


class AllFourHoldOnOneJob(FourRepairs):

    def setUp(self):
        super().setUp()
        self.cycle = self.full_cycle()

    def test_the_job_delivered(self):
        self.assertTrue(self.cycle.delivered, self.cycle.why_stopped)

    def test_repair_1_the_slot_was_taken_and_given_back(self):
        self.assertEqual(self.s.capacity.jobs_in_flight("csv-cleanup"), 0,
                         "a settled job is still holding its pipeline slot")
        self.assertIn("qualify", self.cycle.stages)

    def test_repair_2_a_csv_was_read_independently(self):
        self.assertIn("independent-review", self.cycle.stages)
        self.assertIn("independently authored",
                      self.cycle.independent_review or "")

    def test_repair_3_delivery_consumed_a_signed_approval(self):
        used = [e for e in self.s.audit.events(self.cycle.job_id)
                if e.get("event") == "owner.approval_consumed"]
        self.assertTrue(used, "the delivery went out without a signed approval")

    def test_repair_4_nothing_is_stranded_and_nothing_is_doubled(self):
        states = {j.state for j in self.s.orchestrator.jobs()}
        self.assertNotIn(JobState.VERIFYING, states)
        self.assertNotIn(JobState.EXECUTING, states)
        invoiced = self.s.ledger.invoiced_job_ids()
        self.assertEqual(len(invoiced), len(set(invoiced)),
                         "one job produced two invoices")


class AnInterruptionIsRecoveredWithAllFourInPlace(FourRepairs):
    """The restart path, with signing on and the pool accounted for."""

    def interrupt(self):
        """A job walked to VERIFYING, as a crash mid-recompute leaves it."""
        cycle = self.full_cycle()
        self.s.orchestrator._transition(
            cycle.job_id, JobState.REVISION_REQUESTED,
            why="pretend the client reopened it", initiator="test")
        for state, why in ((JobState.EXECUTING, "work restarted"),
                           (JobState.VERIFYING, "crash point")):
            self.s.orchestrator._transition(cycle.job_id, state, why=why,
                                            initiator="test")
        return cycle

    def resume(self, **kwargs):
        defaults = dict(source=str(self.source), workdir=self.box.name,
                        capability=self.capability,
                        delivery_approver=self.approver())
        return recover_after_restart(self.s, **{**defaults, **kwargs})

    def test_the_interrupted_job_is_picked_up_and_finishes(self):
        cycle = self.interrupt()
        records = self.resume()["resumed"]
        self.assertEqual([r["job_id"] for r in records], [cycle.job_id])
        self.assertEqual(records[0]["found_in"], "VERIFYING")
        self.assertNotEqual(
            self.s.orchestrator.job(cycle.job_id).state, JobState.VERIFYING,
            "the job was left exactly where the interruption found it")

    def test_the_resumed_job_obtained_its_own_second_opinion(self):
        """Recovery re-earns the T2 evidence; it does not inherit it."""
        self.interrupt()
        records = self.resume()["resumed"]
        self.assertTrue(
            records[0].get("independent_review"),
            "a job above T1 was resumed to delivery without a review")

    def test_resumption_did_not_invoice_the_job_twice(self):
        self.interrupt()
        self.resume()
        invoiced = self.s.ledger.invoiced_job_ids()
        self.assertEqual(len(invoiced), len(set(invoiced)),
                         "the recovered job was invoiced a second time")

    def test_resumption_still_needed_the_owners_signature(self):
        """Recovery is not a way around the gate it would otherwise meet."""
        cycle = self.interrupt()
        outcome = self.resume(delivery_approver=None)
        self.assertEqual(outcome["resumed"][0]["outcome"], "HELD",
                         "a resumed job delivered without the owner")
        self.assertNotEqual(
            self.s.orchestrator.job(cycle.job_id).state, JobState.COMPLETE)

    def test_nothing_is_left_mid_flight_either_way(self):
        """Resumed or refused, no job stays in a state nothing polls."""
        self.interrupt()
        self.resume(delivery_approver=None)
        for job in self.s.orchestrator.jobs():
            self.assertNotIn(
                job.state,
                (JobState.EXECUTING, JobState.VERIFYING,
                 JobState.READY_FOR_DELIVERY),
                f"{job.id} was left in {job.state.value} after a restart")

    def test_the_slot_is_not_lost_to_the_interrupted_job(self):
        self.interrupt()
        self.resume()
        self.assertLessEqual(
            self.s.capacity.jobs_in_flight("csv-cleanup"), 4,
            "the pipeline is over its own ceiling after a recovery")

    def test_the_audit_chain_survives_the_whole_thing(self):
        self.interrupt()
        self.resume()
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)


class TheRuntimeActuallyCallsRecovery(FourRepairs):
    """Correct machinery nothing calls is the defect, not the fix.

    ``Runtime.start`` reconciled and nothing else, so automatic
    resumption would have been a function with no caller: every restart
    escalated every interrupted job to a person, exactly as before it
    was written. These two tests are what stop that happening again.
    """

    def interrupt(self):
        cycle = self.full_cycle()
        self.s.orchestrator._transition(
            cycle.job_id, JobState.REVISION_REQUESTED,
            why="pretend the client reopened it", initiator="test")
        for state, why in ((JobState.EXECUTING, "work restarted"),
                           (JobState.VERIFYING, "crash point")):
            self.s.orchestrator._transition(cycle.job_id, state, why=why,
                                            initiator="test")
        return cycle

    def start_runtime(self):
        from solvent import runtime as rt
        runtime = rt.Runtime(self.s, mode=rt.Mode.LOCAL)
        runtime.start()
        return runtime

    def test_a_restart_resumes_when_the_owner_has_named_a_work_root(self):
        cycle = self.interrupt()
        self.s.policy.amend({"recovery": {"work_root": self.box.name}},
                            OWNER, "test: resumption may write here")
        runtime = self.start_runtime()
        self.assertNotEqual(
            self.s.orchestrator.job(cycle.job_id).state, JobState.VERIFYING,
            "the runtime started and left the interrupted job where it was")
        self.assertTrue(
            any(cycle.job_id in line for line in runtime.log),
            f"the restart said nothing about the job: {runtime.log}")

    def test_without_a_work_root_the_runtime_says_so_rather_than_guessing(self):
        cycle = self.interrupt()
        runtime = self.start_runtime()
        self.assertEqual(self.s.orchestrator.job(cycle.job_id).state,
                         JobState.BLOCKED)
        self.assertTrue(
            any("automatic resumption is not configured" in line
                for line in runtime.log),
            f"a silent downgrade to escalation only: {runtime.log}")


class TheRepairsDoNotCancelEachOther(FourRepairs):
    """Each repair's refusal still works with the other three present."""

    def test_a_full_pipeline_still_refuses_a_fifth_job(self):
        for n in range(4):
            self.s.capacity.reserve(
                3600.0, kind="EXECUTE", job_id=f"job_hold{n}",
                capability="csv-cleanup", idempotency_key=f"commit:hold{n}",
                why="test")
        verdict = self.s.capacity.reserve(
            3600.0, kind="EXECUTE", job_id="job_hold4",
            capability="csv-cleanup", idempotency_key="commit:hold4",
            why="test")
        self.assertFalse(verdict.allowed)

    def test_an_unsigned_delivery_is_still_refused(self):
        cycle = self.runCycle(agreed_requirements=self.agreed)
        self.assertFalse(cycle.delivered,
                         "a key is configured and delivery went out unsigned")

    def test_an_unreviewable_job_still_blocks_rather_than_stranding(self):
        cycle = self.full_cycle(independent_reviewer=lambda **_: object())
        self.assertEqual(cycle.stopped_at, "independent-review")
        job = self.s.orchestrator.job(cycle.job_id)
        self.assertEqual(job.state, JobState.BLOCKED)
        self.assertEqual(job.blocked_on, BlockedOn.OWNER)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
