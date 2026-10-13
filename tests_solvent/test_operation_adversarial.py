"""Attacking continuous operation and claiming.

A loop that runs unattended and a method that signs binding commitments
are the two places where a mistake is expensive and unsupervised. These
are the attacks that would matter.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from solvent.operation import Bounds, Operator, StopReason
from solvent import sourceaccess as access
from tests_solvent.test_claiming import Claiming as ClaimCase
from tests_solvent.test_submission import Base as SubmissionBase
from tests_solvent.test_submission import OWNER as CLAIM_OWNER


class Fake:
    def __init__(self, completed=True, invoiced_cents=10_000):
        self.completed = completed
        self.invoiced_cents = invoiced_cents
        self.stopped_at = ""
        self.why_stopped = ""


class Attack(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/adv.db")
        self.addCleanup(self.close)
        self.calls = 0

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def enable(self, **over):
        settings = {"continuous_enabled": True, "max_jobs_per_run": 3,
                    "max_committed_cents": 100_000, "max_run_seconds": 30,
                    "max_consecutive_failures": 2}
        settings.update(over)
        self.s.policy.amend({"operations": settings}, OWNER, "test")

    def counting(self, **kwargs):
        def run():
            self.calls += 1
            return Fake(**kwargs)
        return run


class RunningLongerThanAllowed(Attack):

    def test_a_caller_cannot_widen_the_job_limit(self):
        self.enable(max_jobs_per_run=2)
        report = Operator(self.s).run(self.counting(),
                                      bounds=Bounds(max_jobs=1_000,
                                                    max_seconds=9_999))
        self.assertEqual(self.calls, 2)
        self.assertIs(report.stopped, StopReason.JOB_LIMIT)

    def test_a_caller_cannot_widen_the_spend_ceiling(self):
        self.enable(max_jobs_per_run=100, max_committed_cents=20_000)
        report = Operator(self.s).run(
            self.counting(invoiced_cents=10_000),
            bounds=Bounds(max_jobs=100, max_committed_cents=10_000_000,
                          max_seconds=30))
        self.assertIs(report.stopped, StopReason.SPEND_LIMIT)
        self.assertLessEqual(report.committed_cents, 20_000)

    def test_a_cycle_that_lies_about_completing_still_counts_against_spend(self):
        """A cycle reporting a huge invoice does not get extra iterations."""
        self.enable(max_jobs_per_run=50, max_committed_cents=50_000)
        report = Operator(self.s).run(
            self.counting(invoiced_cents=1_000_000))
        self.assertEqual(self.calls, 1)
        self.assertIs(report.stopped, StopReason.SPEND_LIMIT)

    def test_a_cycle_that_raises_does_not_hang_the_loop(self):
        self.enable()

        def exploding():
            self.calls += 1
            raise RuntimeError("the database went away")

        with self.assertRaises(RuntimeError):
            Operator(self.s).run(exploding)
        self.assertEqual(self.calls, 1,
                         "it retried a cycle that raised, unattended")


class TurningItOffMustAlwaysWork(Attack):

    def test_the_halt_beats_an_enabled_flag(self):
        self.enable(halted=True)
        report = Operator(self.s).run(self.counting())
        self.assertEqual(self.calls, 0)

    def test_the_halt_beats_a_caller_supplied_bound(self):
        self.enable(halted=True)
        report = Operator(self.s).run(self.counting(),
                                      bounds=Bounds(max_jobs=100,
                                                    max_seconds=600))
        self.assertEqual(self.calls, 0)
        self.assertIs(report.stopped, StopReason.NOT_PERMITTED)

    def test_disabling_mid_run_stops_the_next_iteration(self):
        self.enable(max_jobs_per_run=10)
        engaged = {"yet": False}

        def after_two():
            self.calls += 1
            if self.calls >= 2:
                engaged["yet"] = True
            return Fake()

        report = Operator(self.s, halted=lambda: engaged["yet"]).run(after_two)
        self.assertEqual(self.calls, 2)
        self.assertIs(report.stopped, StopReason.HALTED)


class FailureInjection(Attack):
    """What happens when the things underneath stop working."""

    def test_a_cycle_returning_nothing_ends_the_run_cleanly(self):
        self.enable()
        report = Operator(self.s).run(lambda: None)
        self.assertIs(report.stopped, StopReason.NOTHING_TO_DO)

    def test_a_cycle_returning_a_malformed_result_does_not_crash(self):
        """A result missing the fields the loop reads is treated as a
        failure, not as a success with zero revenue."""
        self.enable(max_consecutive_failures=1)

        class Nothing:
            pass

        report = Operator(self.s).run(lambda: Nothing())
        self.assertIs(report.stopped, StopReason.CONSECUTIVE_FAILURES)

    def test_a_policy_read_that_fails_does_not_grant_permission(self):
        broken = Operator(self.s)
        original = self.s.policy.get

        def exploding(*args, **kwargs):
            raise RuntimeError("the policy table is unreadable")

        self.s.policy.get = exploding
        self.addCleanup(setattr, self.s.policy, "get", original)
        with self.assertRaises(RuntimeError):
            broken.run(self.counting())
        self.assertEqual(self.calls, 0,
                         "it ran a job while unable to read its own limits")

    def test_every_run_records_why_it_stopped_even_under_failure(self):
        self.enable(max_consecutive_failures=1)
        for cycle in (lambda: None, lambda: Fake(completed=False),
                      self.counting()):
            with self.subTest(cycle=cycle):
                self.calls = 0
                report = Operator(self.s).run(cycle)
                self.assertIsNotNone(report.stopped)
                self.assertTrue(report.why)


class ClaimingUnderAttack(ClaimCase):
    """Attacks on the binding commitment itself.

    The existing claiming tests ask whether each gate refuses. These ask
    what happens when the things around the gates misbehave -- a failing
    transport, a source answering nonsense, a permission withdrawn between
    the bid and the acceptance.
    """

    def accept_with(self, application, opportunity, transport, *,
                    cents=50_000):
        """Everything the gate needs, so the transport is actually reached."""
        self.open_the_gate()
        grant = self.s.governor.grant_budget(
            job_id=application.id, decision_id="test-decision",
            authorized_cents=cents)
        return self.s.applications.claim(
            application, offered_cents=cents, opportunity=opportunity,
            transport=transport, approval=self.signed(), grant_id=grant)

    def test_a_transport_failure_does_not_leak_a_credential_into_the_log(self):
        """Injected failure, real shape.

        An HTTP client's exception quotes the request it was making, and a
        marketplace request carries a token. The attempt log is append-only,
        so anything written there cannot be scrubbed afterwards.
        """
        secret = "ghp_" + "A1b2C3d4" * 5
        application, opportunity = self.prepared()

        def exploding(request):
            raise ConnectionError(
                f"POST https://api.example.test/accept?token={secret} failed")

        claimed, why = self.accept_with(application, opportunity, exploding)
        self.assertFalse(claimed)
        attempts = self.s.applications.attempts(application.id)
        self.assertTrue(
            any(a["outcome"] == "UNKNOWN_REMOTE_STATE" for a in attempts),
            "the transport was never reached, so this proves nothing")
        logged = " ".join(str(a["detail"]) for a in attempts)
        self.assertNotIn(secret, logged,
                         "a credential reached the append-only attempt log")

    def test_recovery_after_an_injected_transport_failure(self):
        """The recovery path end to end, not a pre-seeded row.

        The failure leaves the remote state unknown. The system must then
        refuse to try again by itself -- an obligation it cannot see is
        worse than no obligation -- and must surface the attempt for a
        person.
        """
        application, opportunity = self.prepared()

        def dies(request):
            raise TimeoutError("the connection died mid-accept")

        claimed, why = self.accept_with(application, opportunity, dies)
        self.assertFalse(claimed)
        self.assertIn("unknown", why)

        self.assertTrue(self.s.applications.unresolved(),
                        "the unknown attempt was not surfaced for a person")

        reached = []
        claimed, why = self.accept_with(
            application, opportunity,
            lambda r: reached.append(r) or self.accepted(r))
        self.assertFalse(claimed)
        self.assertEqual(reached, [],
                         "it retried a commitment whose outcome is unknown")
        self.assertIn("second obligation", why)

    def test_a_source_answering_nonsense_is_not_an_accepted_contract(self):
        """Failure injection at the far end: an answer that means nothing.

        A proxy error page, a login redirect, a changed API -- none of them
        is a contract, and reading an unrecognised answer as acceptance
        would create an obligation nobody agreed to.
        """
        for body in ({}, {"status": "???"}, {"error": "please log in"},
                     "<html>maintenance</html>", None, []):
            with self.subTest(body=body):
                application, opportunity = self.prepared()
                claimed, _ = self.accept_with(application, opportunity,
                                              lambda r: body)
                self.assertFalse(
                    claimed, f"{body!r} was read as an accepted contract")

    def test_permission_withdrawn_after_the_bid_stops_the_acceptance(self):
        """Time passes between bidding and accepting, and the answer to
        "may a machine accept here?" can change in between."""
        application, opportunity = self.prepared()
        self.s.discovery.revoke("freelancer", access.ACCEPT_CONTRACT,
                                owner_identity=CLAIM_OWNER,
                                why="test: the owner changed their mind")
        claimed, why = self.accept_with(application, opportunity,
                                        self.accepted)
        self.assertFalse(claimed)
        self.assertIn("ACCEPT_CONTRACT", why)


class SubmittingUnderAttack(SubmissionBase):
    """The same leak, on the bid path.

    Separate class because the claiming fixture overrides ``prepared``;
    this one needs the submission fixture's own ``authorised``.
    """

    def test_the_same_leak_is_closed_on_the_submitting_path(self):
        """The bid path transmits through the same kind of client, so it
        has the same exception text and the same append-only log."""
        secret = "ghp_" + "Z9y8X7w6" * 5
        app, row = self.authorised()
        self.open_the_gate()

        def exploding(request):
            raise ConnectionError(
                f"POST https://api.example.test/bid?key={secret} failed")

        sent, why = self.s.applications.submit(
            app, opportunity=row, transport=exploding,
            approval=self.signed())
        self.assertFalse(sent)
        attempts = self.s.applications.attempts(app.id)
        self.assertTrue(
            any(a["outcome"] == "UNKNOWN_REMOTE_STATE" for a in attempts),
            "the transport was never reached, so this proves nothing")
        logged = " ".join(str(a["detail"]) for a in attempts)
        self.assertNotIn(secret, logged,
                         "a credential reached the append-only attempt log")


if __name__ == "__main__":
    unittest.main()
