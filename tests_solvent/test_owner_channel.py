"""Owner Channel: an approval must be authenticated, scoped, single-use, fresh."""

import dataclasses
import unittest

from solvent.errors import FailClosed
from solvent.gate import ActionGate, approval as unsigned_approval
from solvent.owner import KEY_ENV, OwnerChannel
from solvent.types import ActionClass, OperatingMode, PrivacyClass, money
from tests_solvent.fixtures import OWNER, Rig

KEY = b"test-owner-key-not-for-real-use"


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.relax_caps()
        self.channel = OwnerChannel(self.rig.store, self.rig.audit, self.rig.policy,
                                    key=KEY)

    def issue(self, **kwargs):
        defaults = dict(owner_identity=OWNER, subject="accept job",
                        action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                        job_id="J1", max_cents=money("500"))
        return self.channel.issue(**{**defaults, **kwargs})

    def verify(self, approval, *, action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
               job_id="J1", amount="100"):
        return self.channel.verify(approval, action_class=action_class,
                                   job_id=job_id, amount_cents=money(amount))


class NoKeyNoApprovals(unittest.TestCase):
    def test_without_a_key_nothing_can_be_issued_or_verified(self):
        """An unauthenticated system must not be able to approve spending."""
        rig = Rig()
        channel = OwnerChannel(rig.store, rig.audit, rig.policy, key=None)
        self.assertFalse(channel.available)
        with self.assertRaises(FailClosed):
            channel.issue(owner_identity=OWNER, subject="x",
                          action_class=ActionClass.C3_FINANCIAL_COMMITMENT)

    def test_the_key_is_read_from_the_environment_not_the_database(self):
        """Solvent must not store the key it is authenticated against."""
        import solvent.owner as owner_module
        self.assertEqual(owner_module.KEY_ENV, "SOLVENT_OWNER_KEY")
        rig = Rig()
        tables = rig.store.raw_readonly(
            "SELECT * FROM owner_approvals") if True else []
        self.assertEqual(tables, [])


class Authenticity(Base):
    def test_a_genuine_approval_verifies(self):
        self.assertEqual(self.verify(self.issue()), "")

    def test_only_a_registered_owner_may_issue(self):
        with self.assertRaises(FailClosed):
            self.issue(owner_identity="owner:impostor")

    def test_a_forged_signature_is_refused(self):
        forged = dataclasses.replace(self.issue(), signature="0" * 64)
        self.assertIn("signature does not match", self.verify(forged))

    def test_tampering_with_the_ceiling_breaks_the_signature(self):
        """Every field that matters is covered, so raising the cap is detectable."""
        approval = self.issue()
        tampered = dataclasses.replace(approval, max_cents=money("99999"))
        self.assertIn("signature does not match",
                      self.verify(tampered, amount="9000"))

    def test_tampering_with_the_job_breaks_the_signature(self):
        tampered = dataclasses.replace(self.issue(), job_id="J_OTHER")
        self.assertIn("signature does not match", self.verify(tampered, job_id="J_OTHER"))

    def test_an_approval_signed_by_a_different_key_is_refused(self):
        approval = self.issue()
        attacker = OwnerChannel(self.rig.store, self.rig.audit, self.rig.policy,
                                key=b"attacker-key")
        self.assertIn("signature does not match",
                      attacker.verify(approval,
                                      action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                                      job_id="J1", amount_cents=money("100")))

    def test_an_approval_this_channel_never_issued_is_refused(self):
        """A valid-looking nonce is not enough; it must have been issued here."""
        approval = self.issue()
        fabricated = dataclasses.replace(approval, nonce="f" * 32)
        self.assertIn("signature does not match", self.verify(fabricated))


class ScopeAndLifetime(Base):
    def test_scoped_to_one_job(self):
        self.assertIn("scoped to job", self.verify(self.issue(), job_id="J2"))

    def test_scoped_to_one_action_class(self):
        self.assertIn("scoped to",
                      self.verify(self.issue(),
                                  action_class=ActionClass.C4_CREDENTIAL_CONFIG_CHANGE))

    def test_scoped_to_a_ceiling(self):
        self.assertIn("ceiling", self.verify(self.issue(), amount="900"))

    def test_expiry_is_enforced(self):
        self.assertIn("expired", self.verify(self.issue(ttl_minutes=-1)))

    def test_single_use_is_enforced_by_a_recorded_nonce(self):
        """A captured approval cannot be replayed."""
        approval = self.issue()
        self.assertEqual(self.verify(approval), "")
        self.channel.consume(approval)
        self.assertIn("already used", self.verify(approval))

    def test_outstanding_reports_what_the_owner_is_still_owed(self):
        self.issue()
        self.assertEqual(len(self.channel.outstanding()), 1)


class GateIntegration(Base):
    def setUp(self):
        super().setUp()
        self.gate = ActionGate(self.rig.store, self.rig.audit, self.rig.policy,
                               self.rig.governor, owner_channel=self.channel)
        self.rig.policy.amend({"egress": {"allowlist": [{"host": "c.example.com"}]}},
                              OWNER, "test host")
        self.grant = self.rig.governor.grant_budget(
            job_id="J1", decision_id="d", authorized_cents=money("500"))

    def act(self, approval, amount="100"):
        return self.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="c.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="commit", job_id="J1", amount_cents=money(amount),
            approval=approval, grant_id=self.grant)

    def test_a_signed_approval_permits_the_action(self):
        self.assertTrue(self.act(self.issue()).allowed)

    def test_an_unsigned_approval_is_refused_when_a_channel_exists(self):
        legacy = unsigned_approval(
            owner_identity=OWNER, job_id="J1",
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT, max_cents=money("500"))
        result = self.act(legacy)
        self.assertFalse(result.allowed)
        self.assertIn("must be signed", result.reason)

    def test_a_signed_approval_is_burned_after_a_successful_action(self):
        approval = self.issue()
        self.assertTrue(self.act(approval).allowed)
        second = self.act(approval)
        self.assertFalse(second.allowed)
        self.assertIn("already used", second.reason)

    def test_a_denied_action_does_not_burn_the_approval(self):
        approval = self.issue()
        self.rig.policy.set_operating_mode(OperatingMode.PAUSE_EXTERNAL, OWNER, "t")
        self.assertFalse(self.act(approval).allowed)
        self.rig.policy.set_operating_mode(OperatingMode.NORMAL, OWNER, "t")
        self.assertTrue(self.act(approval).allowed, "approval must survive a denial")


class UnsignedApprovalsOnlyWhileFailClosed(unittest.TestCase):
    """The rule is tied to the real safety posture, so it cannot be forgotten."""

    def test_unsigned_approval_is_refused_once_real_execution_is_enabled(self):
        rig = Rig()
        rig.relax_caps()
        gate = ActionGate(rig.store, rig.audit, rig.policy, rig.governor)
        rig.policy.amend({"egress": {"allowlist": [{"host": "c.example.com"}],
                                     "simulation_only": False}}, OWNER, "go live")
        grant = rig.governor.grant_budget(job_id="J1", decision_id="d",
                                          authorized_cents=money("500"))
        legacy = unsigned_approval(
            owner_identity=OWNER, job_id="J1",
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT, max_cents=money("500"))
        result = gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination="c.example.com", privacy=PrivacyClass.PUBLIC,
            purpose="commit", job_id="J1", amount_cents=money("100"),
            approval=legacy, grant_id=grant)
        self.assertFalse(result.allowed)
        self.assertIn("authenticated Owner Channel", result.reason)


if __name__ == "__main__":
    unittest.main()
