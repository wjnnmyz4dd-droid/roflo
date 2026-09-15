"""Attacks that succeeded during review, kept as regressions.

Each test here corresponds to a real hole found by attacking the implementation
rather than reading it. They are the reason the fixes exist, so they stay.
"""

import unittest

from solvent.errors import FailClosed
from solvent.gate import ActionGate, approval
from solvent.orchestrator import JobOrchestrator
from solvent.types import (
    ActionClass, CostCategory, GovernorVerdict, JobState, Jurisdiction,
    LocationSignals, OperatingMode, PaymentState, PrivacyClass, VerificationTier, money,
)
from tests_solvent.fixtures import OWNER, Rig


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.load_labor_fixtures()
        self.rig.relax_caps()
        self.orch = JobOrchestrator(self.rig.store, self.rig.audit, self.rig.policy,
                                    self.rig.governor, self.rig.ledger)
        self.gate = ActionGate(self.rig.store, self.rig.audit, self.rig.policy,
                               self.rig.governor)

    def estimate(self, job_id, hours=0.1, dollars="900"):
        return self.rig.governor.estimate(
            job_id=job_id, revenue_cents=money(dollars),
            signals=LocationSignals(project=Jurisdiction(state="NC")),
            quantities={CostCategory.ON_SITE_LABOR: hours})


class A1_EstimateLaundering(Base):
    """Price a trivial job, then use that estimate to approve an expensive one."""

    def test_an_estimate_may_only_decide_its_own_job(self):
        cheap = self.estimate("JOB_CHEAP")
        with self.assertRaises(FailClosed) as ctx:
            self.rig.governor.decide(job_id="JOB_EXPENSIVE",
                                     revenue_cents=money("900"), estimate=cheap,
                                     conformance_ok=True)
        self.assertIn("may only decide its own job", str(ctx.exception))

    def test_the_matching_job_still_decides_normally(self):
        estimate = self.estimate("JOB_CHEAP")
        verdict, _ = self.rig.governor.decide(
            job_id="JOB_CHEAP", revenue_cents=money("900"), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PROFITABLE)


class A2_GrantLaundering(Base):
    """Spend one job's authorised budget on another job's work."""

    def test_a_grant_may_only_be_spent_by_its_own_job(self):
        grant = self.rig.governor.grant_budget(job_id="JOB_A", decision_id="d",
                                               authorized_cents=100_000)
        ok, reason = self.rig.governor.authorize_spend(
            grant_id=grant, amount_cents=5_000, job_id="JOB_B",
            what="work for a different job")
        self.assertFalse(ok)
        self.assertIn("belongs to job", reason)

    def test_the_owning_job_may_still_spend(self):
        grant = self.rig.governor.grant_budget(job_id="JOB_A", decision_id="d",
                                               authorized_cents=100_000)
        ok, _ = self.rig.governor.authorize_spend(
            grant_id=grant, amount_cents=5_000, job_id="JOB_A", what="its own work")
        self.assertTrue(ok)

    def test_the_gate_binds_the_grant_to_the_job(self):
        self.rig.policy.amend({"egress": {"allowlist": [{"host": "api.example.com"}]}},
                              OWNER, "test host")
        grant = self.rig.governor.grant_budget(job_id="JOB_A", decision_id="d",
                                               authorized_cents=100_000)
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="paid call", job_id="JOB_B",
            amount_cents=1_000, grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("belongs to job", result.reason)


class A3_UnbackedCompletion(Base):
    """Declare a job complete without any money having arrived."""

    def _deliverable_job(self):
        job_id = self.orch.intake(
            title="T", client_id="C1", quoted_cents=money("600"),
            signals=LocationSignals(project=Jurisdiction(state="NC")),
            initiator=OWNER)
        for target in (JobState.QUALIFYING, JobState.ACCEPTED, JobState.EXECUTING,
                       JobState.VERIFYING):
            self.orch._transition(job_id, target, why="setup", initiator="test")
        self.rig.audit.record_verification(
            subject_ref=job_id, tier=VerificationTier.T1_DETERMINISTIC, method="m",
            executor_identity="e", verifier_identity="v", verdict=True,
            raw_output="", consequence=self.orch.job(job_id).consequence)
        self.orch.mark_verified(job_id=job_id, initiator="v")
        self.orch.record_delivery(job_id=job_id, initiator="x",
                                  gate_request_id="act")
        return job_id

    def test_completion_consults_the_ledger_not_its_caller(self):
        job_id = self._deliverable_job()
        with self.assertRaises(FailClosed) as ctx:
            self.orch.complete(job_id=job_id, initiator="attacker")
        self.assertIn("no externally verified collected revenue", str(ctx.exception))
        self.assertIs(self.orch.job(job_id).state, JobState.AWAITING_PAYMENT)

    def test_an_invoice_is_still_not_completion(self):
        job_id = self._deliverable_job()
        payment = self.rig.ledger.open_payment(job_id=job_id,
                                               amount_cents=money("600"), rail="t")
        self.rig.ledger.set_payment_state(payment, PaymentState.INVOICED)
        with self.assertRaises(FailClosed):
            self.orch.complete(job_id=job_id, initiator="x")

    def test_verified_payment_completes_the_job(self):
        job_id = self._deliverable_job()
        payment = self.rig.ledger.open_payment(job_id=job_id,
                                               amount_cents=money("600"), rail="t")
        self.rig.ledger.set_payment_state(
            payment, PaymentState.PAID, collected_cents=money("600"),
            verification_method="rail_webhook")
        self.orch.complete(job_id=job_id, initiator="ledger")
        self.assertIs(self.orch.job(job_id).state, JobState.COMPLETE)


class A5_JurisdictionForgery(unittest.TestCase):
    """Craft a jurisdiction component that forges a different jurisdiction's key."""

    def test_the_path_separator_is_rejected_inside_a_component(self):
        with self.assertRaises(ValueError):
            Jurisdiction(state="CA>US>CT")

    def test_ordinary_jurisdictions_are_unaffected(self):
        self.assertEqual(Jurisdiction(state="CA", city="Fresno").path(),
                         "US>CA>Fresno")


class A7_OwnerIdentity(Base):
    """Mint an owner identity by choosing a convincing name."""

    def test_an_unregistered_owner_string_cannot_amend_policy(self):
        with self.assertRaises(FailClosed) as ctx:
            self.rig.policy.amend({"financial": {"margin_floor": 0.0}},
                                  "owner:not_really_the_owner", "lower the floor")
        self.assertIn("registered owner", str(ctx.exception))

    def test_an_unregistered_owner_cannot_approve_a_job(self):
        job_id = self.orch.intake(
            title="T", client_id="C1", quoted_cents=money("600"),
            signals=LocationSignals(), initiator=OWNER)
        self.orch._transition(job_id, JobState.QUALIFYING, why="x", initiator="t")
        self.orch._transition(job_id, JobState.AWAITING_OWNER_APPROVAL, why="x",
                              initiator="t")
        with self.assertRaises(FailClosed):
            self.orch.owner_approves(job_id=job_id,
                                     owner_identity="owner:impostor", why="approve")

    def test_an_unregistered_owner_cannot_mint_a_gate_approval(self):
        self.rig.policy.amend({"egress": {"allowlist": [{"host": "api.example.com"}]}},
                              OWNER, "test host")
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=10_000)
        forged = approval(owner_identity="owner:impostor", job_id="J1",
                          action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                          max_cents=10**9)
        result = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="self-approved", job_id="J1", amount_cents=1_000,
            approval=forged, grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("not a registered owner", result.reason)

    def test_an_unregistered_owner_cannot_ingest_pricing_data(self):
        with self.assertRaises(FailClosed):
            self.rig.pricing.ingest(
                category=CostCategory.ON_SITE_LABOR,
                jurisdiction=Jurisdiction(state="CA"),
                tier=__import__("solvent.types", fromlist=["PricingTier"]).PricingTier.MARKET_DATA,
                unit_cents=1, unit="hour", source="FIXTURE",
                effective_date="2026-06-01", retrieved_date="2026-09-01",
                confidence=1.0, owner_identity="owner:impostor")


class PolicyDegradation(Base):
    """Remove governance and see whether Solvent keeps running."""

    def test_deleting_the_kill_switch_fails_closed_to_halt(self):
        doc = self.rig.policy.doc()
        doc.pop("operating_mode", None)
        self.rig.policy._write(doc, OWNER, "remove the kill switch")
        self.assertIs(self.rig.policy.operating_mode, OperatingMode.HALT)

    def test_a_halted_system_refuses_spending_and_egress(self):
        doc = self.rig.policy.doc()
        doc.pop("operating_mode", None)
        self.rig.policy._write(doc, OWNER, "remove the kill switch")
        grant = self.rig.governor.grant_budget(job_id="J", decision_id="d",
                                               authorized_cents=10_000)
        ok, _ = self.rig.governor.authorize_spend(grant_id=grant, amount_cents=1,
                                                  job_id="J", what="anything")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
