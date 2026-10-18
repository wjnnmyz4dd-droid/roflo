"""Owner signing and delivery authorisation.

Measured defect: with SOLVENT_OWNER_KEY configured, 0 of 100 contracts
delivered -- T1 work included -- because the cycle minted an *unsigned*
approval for the C2 delivery effect and the Action Gate refuses one
once an Owner Channel exists. The gate was right; the cycle offered it
nothing else.

Nothing here manufactures owner consent. DeskPilot cannot sign for
itself -- the key is the owner's -- so every signed approval below is
minted by an isolated random test key standing in for the owner's own
tooling, which is how a real deployment hands one in.
"""

from __future__ import annotations

import dataclasses
import os
import secrets
import unittest

from solvent.harness import OWNER
from solvent.types import ActionClass
from tests_solvent.test_business_cycle import Cycle


class SigningBase(Cycle):
    """A shop whose owner has a signing key, as a deployment would."""

    def setUp(self):
        os.environ["SOLVENT_OWNER_KEY"] = secrets.token_hex(32)
        self.addCleanup(os.environ.pop, "SOLVENT_OWNER_KEY", None)
        super().setUp()
        self.assertTrue(self.s.owner.available,
                        "the test key did not reach the Owner Channel")

    def issue(self, *, job_id, ttl=20, action=None):
        return self.s.owner.issue(
            owner_identity=OWNER, subject="deliver the reviewed file",
            action_class=action or ActionClass.C2_EXTERNAL_COMMUNICATION,
            job_id=job_id, ttl_minutes=ttl)

    def approver(self, **issue_kwargs):
        """The owner's tooling: hands back a signed approval per delivery."""
        def approve(*, job_id, artifact=None):
            return self.issue(job_id=job_id, **issue_kwargs)
        return approve

    def preauthorise(self):
        """The owner's other option: a bounded standing grant."""
        self.s.policy.amend({"preauthorizations": [
            {"action_class": ActionClass.C2_EXTERNAL_COMMUNICATION.value,
             "max_cents": 0}]}, OWNER, "test: routine delivery preauthorised")


class AConfiguredKeyMustNotBlockRoutineWork(SigningBase):
    """Requirement 24: T1 work does not stop because a key exists."""

    def test_without_an_approver_or_preauthorisation_delivery_fails_closed(self):
        """The measured defect, reproduced. This refusal is correct."""
        cycle = self.runCycle()
        self.assertFalse(cycle.delivered)
        self.assertIn("owner", (cycle.why_stopped or "").lower())

    def test_a_preauthorisation_lets_routine_delivery_through(self):
        """The owner's answer to 'I will not sign every routine job'."""
        self.preauthorise()
        cycle = self.runCycle()
        self.assertTrue(cycle.delivered, cycle.why_stopped)

    def test_a_signed_approval_lets_delivery_through(self):
        cycle = self.runCycle(delivery_approver=self.approver())
        self.assertTrue(cycle.delivered, cycle.why_stopped)


class AnInvalidApprovalFailsClosed(SigningBase):
    """Every way an approval can be wrong, and none of them delivers."""

    def deliver_with(self, approver):
        return self.runCycle(delivery_approver=approver)

    def test_no_approval_at_all(self):
        self.assertFalse(self.deliver_with(lambda **_: None).delivered)

    def test_a_forged_signature(self):
        def forge(*, job_id, artifact=None):
            good = self.issue(job_id=job_id)
            return dataclasses.replace(good, signature="0" * 64)
        cycle = self.deliver_with(forge)
        self.assertFalse(cycle.delivered)
        self.assertIn("forged or altered", cycle.why_stopped or "")

    def test_an_approval_altered_after_signing(self):
        """The payload is covered by the signature, so editing it breaks it."""
        def tamper(*, job_id, artifact=None):
            good = self.issue(job_id=job_id)
            return dataclasses.replace(good, max_cents=good.max_cents + 10_000)
        cycle = self.deliver_with(tamper)
        self.assertFalse(cycle.delivered)
        self.assertIn("forged or altered", cycle.why_stopped or "")

    def test_an_approval_for_a_different_job(self):
        def wrong_job(*, job_id, artifact=None):
            return self.issue(job_id="job_someone_else")
        cycle = self.deliver_with(wrong_job)
        self.assertFalse(cycle.delivered)
        self.assertIn("scoped to job", cycle.why_stopped or "")

    def test_an_approval_for_a_different_action(self):
        def wrong_action(*, job_id, artifact=None):
            return self.issue(job_id=job_id,
                              action=ActionClass.C0_INTERNAL_READ)
        cycle = self.deliver_with(wrong_action)
        self.assertFalse(cycle.delivered)
        self.assertIn("scoped to", cycle.why_stopped or "")

    def test_an_expired_approval(self):
        cycle = self.deliver_with(self.approver(ttl=-1))
        self.assertFalse(cycle.delivered)
        self.assertIn("expired", cycle.why_stopped or "")

    def test_a_replayed_approval(self):
        """One approval, two deliveries. The second must be refused."""
        minted = {}

        def once(*, job_id, artifact=None):
            if "approval" not in minted:
                minted["approval"] = self.issue(job_id=job_id)
            return minted["approval"]

        first = self.runCycle(delivery_approver=once)
        self.assertTrue(first.delivered, first.why_stopped)
        # The same approval, offered again for a second job.
        self.register_board([{**self.posting, "ref": "second"}])
        second = self.runCycle(delivery_approver=once)
        self.assertFalse(
            second.delivered,
            "a single-use approval authorised a second delivery")

    def test_an_approver_that_raises_is_not_consent(self):
        def broken(*, job_id, artifact=None):
            raise RuntimeError("the owner's tooling is down")
        cycle = self.deliver_with(broken)
        self.assertFalse(cycle.delivered,
                         "a failure to ask the owner was treated as a yes")


class TheApprovalContractItself(SigningBase):
    """Unit-level pins for two checks no end-to-end test can see.

    Both are second lines of defence. With a key configured the Action
    Gate refuses an unsigned stand-in whatever the harness hands it, and
    ``consume`` claims the nonce atomically whatever ``verify`` said, so
    a cycle-level test passes either way. Mutation testing found exactly
    that: break the first check and the delivery is still refused -- by
    something else. A control only something else enforces is not
    pinned, and the name of a passing test is not evidence that it is.
    """

    def test_a_failed_ask_yields_no_approval_at_all(self):
        """Not a weaker approval. None."""
        from solvent.harness import _delivery_approval

        def broken(*, job_id, artifact=None):
            raise RuntimeError("the owner's tooling is down")

        self.assertIsNone(
            _delivery_approval(self.s, "job_whatever", None, broken),
            "a failure to ask the owner produced an approval anyway")

    def test_verify_itself_reports_an_approval_already_used(self):
        """``consume`` is the atomic claim; ``verify`` must still say so."""
        minted = self.issue(job_id="job_whatever")
        where = dict(action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
                     job_id="job_whatever", amount_cents=0)
        self.assertEqual("", self.s.owner.verify(minted, **where))
        self.s.owner.consume(minted)
        self.assertIn("already used", self.s.owner.verify(minted, **where))


class TheKeyItselfIsNeverExposed(SigningBase):

    def test_the_key_is_not_written_into_the_audit_log(self):
        key = os.environ["SOLVENT_OWNER_KEY"]
        self.preauthorise()
        self.runCycle()
        for event in self.s.audit.events():
            self.assertNotIn(key, str(event),
                             "the owner signing key reached the audit log")


class WithoutAKeyNothingChanges(Cycle):
    """The no-key case, so the repair cannot have broken it."""

    def test_delivery_still_works_with_no_owner_channel(self):
        os.environ.pop("SOLVENT_OWNER_KEY", None)
        cycle = self.runCycle()
        self.assertTrue(cycle.delivered, cycle.why_stopped)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
