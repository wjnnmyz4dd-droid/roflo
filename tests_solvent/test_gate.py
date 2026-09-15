"""Action Gate: the only path out, and what it refuses."""

import unittest

from solvent.errors import EgressDenied
from solvent.gate import ActionGate, approval
from solvent.types import ActionClass, OperatingMode, PrivacyClass
from tests_solvent.fixtures import OWNER, Rig


class GateRig(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.relax_caps()
        self.gate = ActionGate(self.rig.store, self.rig.audit, self.rig.policy,
                               self.rig.governor)

    def allow_host(self, host="api.example.com", **entry):
        self.rig.policy.amend(
            {"egress": {"allowlist": [{"host": host, **entry}]}}, OWNER, "test host")


class DefaultPosture(GateRig):
    def test_nothing_may_leave_by_default(self):
        """An empty allowlist permits nothing. P0 ships closed."""
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="fetch")
        self.assertFalse(result.allowed)
        self.assertIn("not on the egress allowlist", result.reason)

    def test_approved_destination_is_simulated_not_transmitted(self):
        """Simulation is labelled, never passed off as a real external effect."""
        self.allow_host()
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="fetch")
        self.assertTrue(result.allowed)
        self.assertTrue(result.simulated)
        self.assertIn("SIMULATED", result.reason)

    def test_internal_reads_need_no_gate_approval(self):
        result = self.gate.request(
            action_class=ActionClass.C0_INTERNAL_READ, destination="local",
            privacy=PrivacyClass.INTERNAL, purpose="read own state")
        self.assertTrue(result.allowed)


class PrivacyOverridesConvenience(GateRig):
    def test_payload_above_the_destination_cap_is_refused(self):
        self.allow_host(max_privacy="INTERNAL")
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.CLIENT_CONFIDENTIAL, purpose="send client doc")
        self.assertFalse(result.allowed)
        self.assertIn("accepts at most INTERNAL", result.reason)

    def test_destination_may_restrict_which_action_classes_it_accepts(self):
        self.allow_host(classes=["C1_EXTERNAL_READ"])
        result = self.gate.request(
            action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="message")
        self.assertFalse(result.allowed)


class PermissionEnforcement(GateRig):
    def test_prohibited_class_is_refused_at_any_value(self):
        """Solvent may never expand its own authority, however small the ask."""
        self.allow_host()
        result = self.gate.request(
            action_class=ActionClass.C5_AUTHORITY_CHANGE, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="grant myself more authority")
        self.assertFalse(result.allowed)
        self.assertIn("PROHIBITED", result.reason)

    def test_owner_approval_required_class_needs_an_approval(self):
        self.allow_host()
        result = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="accept job", job_id="J1", amount_cents=1_000)
        self.assertFalse(result.allowed)
        self.assertIn("requires owner approval", result.reason)

    def test_approval_is_scoped_to_one_job_and_one_class(self):
        self.allow_host()
        grant = self.rig.governor.grant_budget(job_id="J2", decision_id="d",
                                               authorized_cents=10_000)
        apv = approval(owner_identity=OWNER, job_id="J1",
                       action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                       max_cents=5_000)
        result = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="wrong job", job_id="J2", amount_cents=1_000, approval=apv,
            grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("scoped to job", result.reason)

    def test_approval_ceiling_is_enforced(self):
        self.allow_host()
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=100_000)
        apv = approval(owner_identity=OWNER, job_id="J1",
                       action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                       max_cents=5_000)
        result = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="over ceiling", job_id="J1", amount_cents=9_000, approval=apv,
            grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("ceiling", result.reason)

    def test_expired_approval_is_refused(self):
        self.allow_host()
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=10_000)
        expired = approval(owner_identity=OWNER, job_id="J1",
                           action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                           max_cents=5_000, ttl_minutes=-1)
        result = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="late", job_id="J1", amount_cents=1_000, approval=expired,
            grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("expired", result.reason)

    def test_approval_is_single_use(self):
        self.allow_host()
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=100_000)
        apv = approval(owner_identity=OWNER, job_id="J1",
                       action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                       max_cents=5_000)
        kwargs = dict(action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                      destination="api.example.com", privacy=PrivacyClass.PUBLIC,
                      purpose="accept", job_id="J1", amount_cents=1_000,
                      approval=apv, grant_id=grant)
        self.assertTrue(self.gate.request(**kwargs).allowed)
        self.assertFalse(self.gate.request(**kwargs).allowed)

    def test_a_denied_request_does_not_burn_the_approval(self):
        """Otherwise a caller could exhaust approvals by failing a later check."""
        self.allow_host()
        apv = approval(owner_identity=OWNER, job_id="J1",
                       action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                       max_cents=5_000)
        denied = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="no grant yet", job_id="J1", amount_cents=1_000, approval=apv)
        self.assertFalse(denied.allowed)
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=10_000)
        retried = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="with grant", job_id="J1", amount_cents=1_000, approval=apv,
            grant_id=grant)
        self.assertTrue(retried.allowed, retried.reason)

    def test_non_owner_identity_on_an_approval_is_refused(self):
        self.allow_host()
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=10_000)
        forged = approval(owner_identity="execution",
                          job_id="J1",
                          action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                          max_cents=5_000)
        result = self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="api.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="self-approved", job_id="J1", amount_cents=1_000,
            approval=forged, grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("not an owner", result.reason)


class GovernorIsUpstream(GateRig):
    def test_spending_action_without_a_budget_grant_is_refused(self):
        self.allow_host()
        self.rig.policy.amend({"permissions": {
            ActionClass.C1_EXTERNAL_READ.value: "AUTONOMOUS"}}, OWNER, "t")
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="paid api call", job_id="J1",
            amount_cents=500)
        self.assertFalse(result.allowed)
        self.assertIn("no Financial Governor budget grant", result.reason)

    def test_gate_cannot_allow_what_the_governor_refused(self):
        self.allow_host()
        grant = self.rig.governor.grant_budget(job_id="J1", decision_id="d",
                                               authorized_cents=100)
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="over budget", job_id="J1",
            amount_cents=5_000, grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("Financial Governor refused", result.reason)


class KillSwitchAtTheGate(GateRig):
    def test_external_effects_stop_in_pause_external(self):
        self.allow_host()
        self.rig.policy.set_operating_mode(OperatingMode.PAUSE_EXTERNAL, OWNER, "test")
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="fetch")
        self.assertFalse(result.allowed)
        self.assertIn("PAUSE_EXTERNAL", result.reason)

    def test_halt_stops_external_effects(self):
        self.allow_host()
        self.rig.policy.set_operating_mode(OperatingMode.HALT, OWNER, "emergency")
        result = self.gate.request(
            action_class=ActionClass.C1_EXTERNAL_READ, destination="api.example.com",
            privacy=PrivacyClass.PUBLIC, purpose="fetch")
        self.assertFalse(result.allowed)


class Recording(GateRig):
    def test_both_allow_and_deny_are_recorded(self):
        self.gate.request(action_class=ActionClass.C1_EXTERNAL_READ,
                          destination="blocked.example.com",
                          privacy=PrivacyClass.PUBLIC, purpose="denied", job_id="J1")
        self.allow_host()
        self.gate.request(action_class=ActionClass.C1_EXTERNAL_READ,
                          destination="api.example.com", privacy=PrivacyClass.PUBLIC,
                          purpose="allowed", job_id="J1")
        rows = self.gate.requests_for("J1")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["allowed"] for r in rows}, {0, 1})
        events = [e["event"] for e in self.rig.audit.events(job_id="J1")]
        self.assertIn("gate.deny", events)
        self.assertIn("gate.allow", events)

    def test_require_raises_on_refusal(self):
        with self.assertRaises(EgressDenied):
            self.gate.require(action_class=ActionClass.C1_EXTERNAL_READ,
                              destination="nope.example.com",
                              privacy=PrivacyClass.PUBLIC, purpose="must not proceed")


if __name__ == "__main__":
    unittest.main()
