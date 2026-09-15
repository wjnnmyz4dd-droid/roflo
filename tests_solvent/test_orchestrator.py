"""Job Orchestrator: legal moves only, no silent stalls, no unverified delivery."""

import unittest

from solvent.capability import Assessment
from solvent.errors import FailClosed
from solvent.orchestrator import JobOrchestrator
from solvent.types import (
    BlockedOn, CapabilityVerdict, Conformance, ConsequenceTier, CostCategory, CostLine,
    GovernorVerdict, JobState, Jurisdiction, LocationSignals, OperatingMode,
    VerificationTier, money,
)
from tests_solvent.fixtures import OWNER, Rig


def conforming(job_id="J"):
    return Assessment(id="a1", job_id=job_id, verdict=CapabilityVerdict.CAN_EXECUTE,
                      conformance=Conformance.CONFORMS, assessor=OWNER)


def nonconforming(job_id="J"):
    return Assessment(id="a2", job_id=job_id, verdict=CapabilityVerdict.REQUIRES_HUMAN,
                      conformance=Conformance.UNKNOWN, assessor="extractor")


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.load_labor_fixtures()
        self.rig.relax_caps()
        self.orch = JobOrchestrator(self.rig.store, self.rig.audit, self.rig.policy,
                                    self.rig.governor)

    def make_job(self, dollars="600", state="CA"):
        return self.orch.intake(
            title="T", client_id="C1", quoted_cents=money(dollars),
            signals=LocationSignals(project=Jurisdiction(state=state)),
            initiator=OWNER)

    def estimate_for(self, job_id, hours=1, dollars="600"):
        return self.rig.governor.estimate(
            job_id=job_id, revenue_cents=money(dollars),
            signals=LocationSignals(project=Jurisdiction(state="CA")),
            quantities={CostCategory.ON_SITE_LABOR: hours})


class Transitions(Base):
    def test_illegal_transitions_are_refused(self):
        job_id = self.make_job()
        for target in (JobState.COMPLETE, JobState.AWAITING_PAYMENT,
                       JobState.VERIFYING, JobState.EXECUTING):
            with self.subTest(target=target), self.assertRaises(FailClosed):
                self.orch._transition(job_id, target, why="skip ahead", initiator="x")

    def test_blocked_requires_a_named_blocker(self):
        """A blocker with no owner is a silent stall."""
        job_id = self.make_job()
        with self.assertRaises(FailClosed):
            self.orch._transition(job_id, JobState.BLOCKED, why="stuck", initiator="x")

    def test_block_and_resume_returns_to_the_originating_state(self):
        job_id = self.make_job()
        self.orch.block(job_id=job_id, blocked_on=BlockedOn.INFORMATION,
                        why="missing scope", initiator="x")
        job = self.orch.job(job_id)
        self.assertIs(job.state, JobState.BLOCKED)
        self.assertIs(job.blocked_on, BlockedOn.INFORMATION)
        self.orch.unblock(job_id=job_id, why="client answered", initiator="x")
        self.assertIs(self.orch.job(job_id).state, JobState.INTAKE)

    def test_new_work_is_refused_while_paused(self):
        self.rig.policy.set_operating_mode(OperatingMode.PAUSE_NEW_WORK, OWNER, "test")
        with self.assertRaises(FailClosed):
            self.make_job()

    def test_execution_requires_a_budget_grant(self):
        job_id = self.make_job()
        self.orch.qualify(job_id=job_id, assessment=conforming(job_id),
                          estimate=self.estimate_for(job_id), initiator=OWNER)
        with self.assertRaises(FailClosed):
            self.orch.begin_execution(job_id=job_id, grant_id="", initiator="x")


class Qualification(Base):
    def test_nonconforming_work_is_rejected_not_failed(self):
        """Declining is a success of the system; conflating it with failure would
        corrupt every learning statistic."""
        job_id = self.make_job()
        verdict = self.orch.qualify(job_id=job_id, assessment=nonconforming(job_id),
                                    estimate=self.estimate_for(job_id),
                                    initiator=OWNER)
        self.assertIs(verdict, GovernorVerdict.REJECT)
        self.assertIs(self.orch.job(job_id).state, JobState.REJECTED)

    def test_unknown_pricing_jurisdiction_blocks_rather_than_guesses(self):
        job_id = self.make_job()
        blind = self.rig.governor.estimate(
            job_id=job_id, revenue_cents=money("600"), signals=LocationSignals(),
            quantities={CostCategory.ON_SITE_LABOR: 1})
        verdict = self.orch.qualify(job_id=job_id, assessment=conforming(job_id),
                                    estimate=blind, initiator=OWNER)
        self.assertIs(verdict, GovernorVerdict.PRICING_LOCATION_UNKNOWN)
        job = self.orch.job(job_id)
        self.assertIs(job.state, JobState.BLOCKED)
        self.assertIs(job.blocked_on, BlockedOn.INFORMATION)

    def test_owner_approval_requires_an_owner_identity(self):
        self.rig.policy.amend({"financial": {"owner_approval_above_cents": 1}},
                              OWNER, "force approval")
        job_id = self.make_job()
        self.orch.qualify(job_id=job_id, assessment=conforming(job_id),
                          estimate=self.estimate_for(job_id), initiator=OWNER)
        self.assertIs(self.orch.job(job_id).state, JobState.AWAITING_OWNER_APPROVAL)
        with self.assertRaises(FailClosed):
            self.orch.owner_approves(job_id=job_id, owner_identity="execution",
                                     why="self-approved")
        self.orch.owner_approves(job_id=job_id, owner_identity=OWNER, why="ok")
        self.assertIs(self.orch.job(job_id).state, JobState.ACCEPTED)


class VerificationGate(Base):
    def _to_verifying(self, dollars="600"):
        job_id = self.make_job(dollars)
        self.orch.qualify(job_id=job_id, assessment=conforming(job_id),
                          estimate=self.estimate_for(job_id, dollars=dollars),
                          initiator=OWNER)
        if self.orch.job(job_id).state is JobState.AWAITING_OWNER_APPROVAL:
            self.orch.owner_approves(job_id=job_id, owner_identity=OWNER, why="ok")
        grant = self.rig.governor.grant_budget(job_id=job_id, decision_id="d",
                                               authorized_cents=100_000)
        self.orch.begin_execution(job_id=job_id, grant_id=grant, initiator="x")
        self.orch.submit_for_verification(job_id=job_id, initiator="x")
        return job_id

    def test_delivery_is_impossible_without_evidence(self):
        job_id = self._to_verifying()
        with self.assertRaises(FailClosed):
            self.orch.mark_verified(job_id=job_id, initiator="x")
        with self.assertRaises(FailClosed):
            self.orch.record_delivery(job_id=job_id, initiator="x",
                                      gate_request_id="act_x")

    def test_evidence_below_the_required_tier_is_insufficient(self):
        """A high-consequence job needs more than a deterministic check."""
        self.rig.policy.amend(
            {"verification": {"consequence_thresholds_cents": {"C_MED": 1, "C_HIGH": 2,
                                                               "C_CRITICAL": 10**9}}},
            OWNER, "force C_HIGH")
        job_id = self._to_verifying()
        self.assertIs(self.orch.job(job_id).consequence, ConsequenceTier.C_HIGH)
        self.rig.audit.record_verification(
            subject_ref=job_id, tier=VerificationTier.T1_DETERMINISTIC, method="schema",
            executor_identity="execution", verifier_identity="qc", verdict=True,
            raw_output="ok", consequence=ConsequenceTier.C_HIGH)
        ok, reason = self.orch.verification_satisfied(job_id)
        self.assertFalse(ok)
        self.assertIn("T2_INDEPENDENT_REVIEW", reason)

    def test_sufficient_evidence_allows_delivery(self):
        job_id = self._to_verifying()
        self.rig.audit.record_verification(
            subject_ref=job_id, tier=VerificationTier.T1_DETERMINISTIC,
            method="recalculated totals", executor_identity="execution",
            verifier_identity="qc", verdict=True, raw_output="ok",
            consequence=self.orch.job(job_id).consequence)
        self.orch.mark_verified(job_id=job_id, initiator="qc")
        self.assertIs(self.orch.job(job_id).state, JobState.READY_FOR_DELIVERY)

    def test_completion_requires_externally_verified_payment(self):
        job_id = self._to_verifying()
        self.rig.audit.record_verification(
            subject_ref=job_id, tier=VerificationTier.T1_DETERMINISTIC, method="m",
            executor_identity="execution", verifier_identity="qc", verdict=True,
            raw_output="ok", consequence=self.orch.job(job_id).consequence)
        self.orch.mark_verified(job_id=job_id, initiator="qc")
        self.orch.record_delivery(job_id=job_id, initiator="x", gate_request_id="act")
        with self.assertRaises(FailClosed):
            self.orch.complete(job_id=job_id, initiator="x", collected_cents=1,
                               verification_method="")
        with self.assertRaises(FailClosed):
            self.orch.complete(job_id=job_id, initiator="x", collected_cents=0,
                               verification_method="rail")
        self.orch.complete(job_id=job_id, initiator="x", collected_cents=money("600"),
                           verification_method="rail_webhook")
        self.assertIs(self.orch.job(job_id).state, JobState.COMPLETE)


class NoSilentStalls(Base):
    def test_overdue_jobs_are_detected_and_escalated_never_advanced(self):
        job_id = self.make_job()
        self.rig.store.for_authority("orchestrator").execute(
            "UPDATE jobs SET state_entered_at = '2020-01-01T00:00:00+00:00' "
            "WHERE id = ?", (job_id,))
        self.rig.store.for_authority("orchestrator").commit()
        overdue = self.orch.overdue()
        self.assertEqual(len(overdue), 1)
        before = self.orch.job(job_id).state
        self.orch.escalate_overdue()
        self.assertIs(self.orch.job(job_id).state, before,
                      "a timeout escalates; it must never advance the job")
        events = [e["event"] for e in self.rig.audit.events(job_id=job_id)]
        self.assertIn("job.timeout", events)

    def test_terminal_jobs_are_never_reported_overdue(self):
        job_id = self.make_job()
        self.orch.qualify(job_id=job_id, assessment=nonconforming(job_id),
                          estimate=self.estimate_for(job_id), initiator=OWNER)
        self.rig.store.for_authority("orchestrator").execute(
            "UPDATE jobs SET state_entered_at = '2020-01-01T00:00:00+00:00' "
            "WHERE id = ?", (job_id,))
        self.rig.store.for_authority("orchestrator").commit()
        self.assertEqual(self.orch.overdue(), [])


if __name__ == "__main__":
    unittest.main()
