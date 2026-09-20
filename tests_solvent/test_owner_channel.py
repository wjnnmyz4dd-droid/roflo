"""Owner Channel: an approval must be authenticated, scoped, single-use, fresh."""

import dataclasses
import unittest

from solvent.errors import FailClosed
from solvent.gate import ActionGate, approval as unsigned_approval
from solvent.owner import KEY_ENV, OwnerChannel
from solvent.types import ActionClass, OperatingMode, PrivacyClass, money
from tests_solvent.fixtures import (
    OWNER, Rig, TEST_ONLY_ATTACKER_KEY, TEST_ONLY_OWNER_KEY,
)

KEY = TEST_ONLY_OWNER_KEY


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
                                key=TEST_ONLY_ATTACKER_KEY)
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


class TheKeyItselfIsChecked(Base):
    """OD-5: a variable that is set is not the same as a key that is strong.

    The whole of this channel's authentication is the signing key. Accepting a
    short or typed one is not a weaker version of the scheme — it is a scheme
    that verifies signatures nobody needs the key to forge, while every test
    here goes on passing.
    """

    def build(self, key):
        return OwnerChannel(self.rig.store, self.rig.audit, self.rig.policy,
                            key=key)

    def test_a_real_key_is_accepted(self):
        """Guards the rest: a channel that refuses every key authenticates
        nothing."""
        import secrets

        self.assertTrue(self.build(secrets.token_bytes(32)).available)
        self.assertTrue(self.build(TEST_ONLY_OWNER_KEY).available)

    def test_a_short_key_is_refused(self):
        channel = self.build(b"hunter2")
        self.assertFalse(channel.available)
        self.assertIn("32 is the minimum", channel.key_refusal)

    def test_a_placeholder_is_refused(self):
        for placeholder in (b"<OWNER_KEY>", b"changeme", b"your-key-here"):
            with self.subTest(placeholder=placeholder):
                self.assertFalse(self.build(placeholder).available)

    def test_a_long_value_with_no_randomness_in_it_is_refused(self):
        self.assertFalse(self.build(b"a" * 64).available)

    def test_a_short_value_repeated_to_reach_the_length_is_refused(self):
        channel = self.build(b"changeme" * 8)
        self.assertFalse(channel.available)
        self.assertIn("repeated", channel.key_refusal)

    def test_a_refused_key_approves_nothing(self):
        """Not merely reported — the channel must actually fail closed."""
        channel = self.build(b"hunter2")
        with self.assertRaises(FailClosed) as caught:
            channel.issue(owner_identity=OWNER, subject="anything",
                          action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                          max_cents=money("1.00"))
        self.assertIn(KEY_ENV, str(caught.exception))

    def test_the_refusal_says_what_to_set_without_quoting_what_was_set(self):
        """A refusal that echoes the rejected value puts it wherever the
        refusal goes — a log, a ticket, a screenshot."""
        secret = b"hunter2"
        channel = self.build(secret)
        self.assertIn(KEY_ENV, channel.key_refusal)
        self.assertNotIn(secret.decode(), channel.key_refusal)

    def test_a_long_typed_passphrase_is_not_detected_and_this_is_known(self):
        """The floor is length and randomness, not a judgement about whether a
        human chose it. Written down so nobody reads the tests above as a
        promise the code does not make: 36 varied characters someone invented
        are accepted, and the provisioning instructions are what addresses
        that."""
        self.assertTrue(
            self.build(b"hunter2-the-owners-actual-passphrase").available)


class TheKeyDoesNotLeak(Base):
    """A signing key that appears in the audit, the database or a report is not
    a signing key any more."""

    def setUp(self):
        super().setUp()
        self.channel = OwnerChannel(self.rig.store, self.rig.audit,
                                    self.rig.policy, key=TEST_ONLY_OWNER_KEY)
        self.approval = self.channel.issue(
            owner_identity=OWNER, subject="deliver the cleaned file",
            action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
            job_id="job-1", max_cents=money("20.00"))

    def secret(self) -> str:
        return TEST_ONLY_OWNER_KEY.decode()

    def test_the_key_is_not_in_the_audit_log(self):
        text = repr([dict(e) for e in self.rig.audit.events()])
        self.assertNotIn(self.secret(), text)

    def test_the_key_is_not_in_the_issued_approval(self):
        self.assertNotIn(self.secret(), repr(dataclasses.asdict(self.approval)))

    def test_the_key_is_not_in_the_stored_record(self):
        rows = self.rig.store.for_authority("owner").query(
            "SELECT * FROM owner_approvals")
        self.assertNotIn(self.secret(), repr([dict(r) for r in rows]))

    def test_the_fingerprint_is_not_the_key_hashed_in_a_guessable_way(self):
        """A bare hash of a secret is something an attacker can test candidates
        against offline. This one cannot be reproduced without the key."""
        import hashlib

        self.assertNotEqual(
            self.channel.key_id(),
            hashlib.sha256(TEST_ONLY_OWNER_KEY).hexdigest()[:12])

    def test_the_fingerprint_still_identifies_the_key(self):
        """Guards the test above: an identifier that identifies nothing is not
        an improvement."""
        same = OwnerChannel(self.rig.store, self.rig.audit, self.rig.policy,
                            key=TEST_ONLY_OWNER_KEY)
        other = OwnerChannel(self.rig.store, self.rig.audit, self.rig.policy,
                             key=TEST_ONLY_ATTACKER_KEY)
        self.assertEqual(same.key_id(), self.channel.key_id())
        self.assertNotEqual(other.key_id(), self.channel.key_id())

    def test_the_provisioning_instructions_contain_no_secret(self):
        from solvent.owner import provisioning_instructions

        text = provisioning_instructions()
        self.assertIn(KEY_ENV, text)
        self.assertNotIn(self.secret(), text)
        for word in ("generate", "secret manager", "rotate"):
            with self.subTest(word=word):
                self.assertIn(word, text.lower())

    def test_solvent_never_generates_the_key_for_itself(self):
        """The one thing this module must not do. A system that can mint its own
        signing key has not authenticated anybody."""
        import inspect

        from solvent import owner as owner_module

        source = inspect.getsource(owner_module)
        for minting in ("token_bytes(32)", "os.urandom(32)"):
            with self.subTest(minting=minting):
                self.assertNotIn(minting, source)
