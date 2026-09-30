"""§58. Written after the submission layer looked finished, to attack it.

Aimed at the seams: the boundary between a determination and a client, between
an approval and an artifact, between "no answer" and "no effect". None of these
shaped the implementation.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from solvent import owner as owner_module
from solvent import sourceaccess as access
from solvent import submission as tx
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.errors import FailClosed
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
from solvent.owner import OwnerChannel
from solvent.types import ActionClass
from tests_solvent.fixtures import TEST_ONLY_OWNER_KEY

POSTING = {"ref": "proj-h", "title": "Deduplicate a CSV", "quoted_cents": 40_000,
           "needs": ["csv-cleanup"], "body": "Please clean this file.",
           "client_ref": "client:acme"}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self._previous = os.environ.get(owner_module.KEY_ENV)
        os.environ[owner_module.KEY_ENV] = TEST_ONLY_OWNER_KEY.decode()
        self.addCleanup(self._restore)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        self.addCleanup(self.close)
        self.sent = []

    def _restore(self):
        if self._previous is None:
            os.environ.pop(owner_module.KEY_ENV, None)
        else:
            os.environ[owner_module.KEY_ENV] = self._previous

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def ready(self, *, mode=access.AUTOMATED_SUBMISSION_WITH_AUTH, **overrides):
        src = FixtureSource("freelancer", [POSTING])
        src.submission_mode = mode
        self.s.discovery.register_source(
            src, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="fixture")
        self.s.policy.amend({"discovery": {"approved_sources": ["freelancer"]}},
                            OWNER, "approve")
        self.s.discovery.poll("freelancer")
        row = self.s.discovery.opportunities()[0]
        kwargs = dict(price_cents=36_000,
                      capability_version=CSV_PROMOTION.version,
                      deliverables=["clean.csv"], timeline_days=3,
                      scope="Deduplicate the supplied CSV.")
        kwargs.update(overrides)
        application = self.s.applications.prepare(row, **kwargs)
        self.s.discovery.grant("freelancer", access.SUBMIT_APPLICATION,
                               owner_identity=OWNER, why="holdout")
        self.s.applications.approve(
            application.id, owner_identity=OWNER, why="holdout",
            digest=self.s.applications.stored(application.id)["digest"])
        self.s.policy.amend(
            {"egress": {"simulation_only": False,
                        "allowlist": [{"host": "www.freelancer.com",
                                       "max_privacy": "INTERNAL"}]}},
            OWNER, "holdout: permit the destination")
        return application, row

    def signed(self):
        channel = OwnerChannel(self.s.store, self.s.audit, self.s.policy,
                               key=TEST_ONLY_OWNER_KEY)
        return channel.issue(owner_identity=OWNER, subject="submit",
                             action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
                             max_cents=1_000_000)

    def transport(self, response=None, *, raises=None):
        def send(request):
            self.sent.append(request)
            if raises is not None:
                raise raises
            return response
        return send


class TheSeamBetweenDeterminationAndClient(Base):

    def test_canary_a_submittable_source_does_transmit(self):
        """Without this, every refusal below could hold on a pipeline that
        never transmits at all."""
        application, row = self.ready()
        sent, why = self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertTrue(sent, why)

    def test_a_registry_determination_of_prohibited_overrides_a_real_client(self):
        """The determination is the registry's, not the client's. A client that
        *can* submit must not be able to submit to a source recorded as
        forbidding it."""
        application, row = self.ready(mode=access.SUBMISSION_PROHIBITED)
        sent, why = self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertFalse(sent)
        self.assertIn("forbid", why)
        self.assertEqual(self.sent, [])

    def test_an_undetermined_source_does_not_transmit(self):
        application, row = self.ready(mode=access.SUBMISSION_UNDETERMINED)
        sent, _ = self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertFalse(sent)
        self.assertEqual(self.sent, [])

    def test_a_nonsense_mode_does_not_transmit(self):
        application, row = self.ready(mode="TOTALLY_FINE_HONESTLY")
        sent, why = self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertFalse(sent)
        self.assertIn("not a submission mode", why)

    def test_a_source_with_no_client_prepares_a_package_instead(self):
        application, row = self.ready(mode=access.MANUAL_SUBMISSION_ONLY)
        result = self.s.applications.package_for_manual_submission(
            application, row)
        self.assertEqual(result.outcome, tx.MANUAL_REQUIRED)
        self.assertTrue(result.manual_instructions)

    def test_an_unverified_application_is_not_handed_over_either(self):
        """A package given to a person should not be one with known problems."""
        application, row = self.ready(mode=access.MANUAL_SUBMISSION_ONLY)
        broken = dict(row, deadline="2020-01-01T00:00:00+00:00")
        with self.assertRaises(FailClosed):
            self.s.applications.package_for_manual_submission(
                application, broken)


class TheSeamBetweenApprovalAndArtifact(Base):

    def test_changing_the_scope_after_approval_voids_it(self):
        application, row = self.ready()
        changed = self.s.applications.prepare(
            row, price_cents=36_000,
            capability_version=CSV_PROMOTION.version,
            deliverables=["clean.csv"], timeline_days=3,
            scope="Something else entirely.")
        sent, why = self.s.applications.submit(
            changed, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertFalse(sent)
        self.assertIn("not been approved", why)

    def test_changing_the_deliverables_after_approval_voids_it(self):
        application, row = self.ready()
        changed = self.s.applications.prepare(
            row, price_cents=36_000,
            capability_version=CSV_PROMOTION.version,
            deliverables=["clean.csv", "and a report nobody agreed to"],
            timeline_days=3, scope="Deduplicate the supplied CSV.")
        sent, _ = self.s.applications.submit(
            changed, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertFalse(sent)

    def test_re_pointing_an_approved_application_at_another_opportunity_voids_it(self):
        """The nastiest shape: same application, different client. The digest
        covers the opportunity precisely so this cannot happen."""
        application, row = self.ready()
        other = dict(row, id="opp_somebody_else", external_ref="proj-other")
        sent, why = self.s.applications.submit(
            application, opportunity=other, approval=self.signed(),
            transport=self.transport({"status_code": 200,
                                      "result": {"id": 1}}))
        self.assertFalse(sent)
        self.assertEqual(self.sent, [])

    def test_an_approval_is_not_reusable_for_a_second_send(self):
        application, row = self.ready()
        good = {"status_code": 200, "result": {"id": 42}}
        self.assertTrue(self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport(good))[0])
        again, why = self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport(good))
        self.assertFalse(again)
        self.assertEqual(len(self.sent), 1)


class TheSeamBetweenNoAnswerAndNoEffect(Base):

    def test_a_timeout_is_unknown_not_failed(self):
        application, row = self.ready()
        sent, _ = self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport(raises=TimeoutError("read timed out")))
        self.assertFalse(sent)
        self.assertEqual(
            self.s.applications.attempts(application.id)[-1]["outcome"],
            tx.UNKNOWN_REMOTE_STATE)

    def test_a_none_response_is_unknown_not_failed(self):
        application, row = self.ready()
        self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport(None))
        self.assertEqual(
            self.s.applications.attempts(application.id)[-1]["outcome"],
            tx.UNKNOWN_REMOTE_STATE)

    def test_no_transport_at_all_sent_nothing_and_says_so(self):
        """Distinct from unknown: nothing left, so it is safe to try again."""
        application, row = self.ready()
        sent, _ = self.s.applications.submit(
            application, opportunity=row, approval=self.signed())
        self.assertFalse(sent)
        attempt = self.s.applications.attempts(application.id)[-1]
        self.assertEqual(attempt["outcome"], tx.NOT_SENT)
        self.assertNotIn(attempt["outcome"], tx.NO_RETRY)

    def test_an_html_error_page_is_a_source_change_not_a_success(self):
        application, row = self.ready()
        self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport("<html>sign in</html>"))
        attempt = self.s.applications.attempts(application.id)[-1]
        self.assertNotEqual(attempt["outcome"], tx.ACCEPTED)
        self.assertEqual(attempt["failure"], tx.SOURCE_CHANGED)

    def test_a_rejection_is_recorded_with_the_sources_own_code(self):
        application, row = self.ready()
        self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 403,
                                      "error_code": "InvalidCredentials",
                                      "message": "token expired"}))
        attempt = self.s.applications.attempts(application.id)[-1]
        self.assertEqual(attempt["failure"], tx.AUTH_FAILED)
        self.assertEqual(attempt["remote_status"], "InvalidCredentials")

    def test_the_response_digest_is_recorded_for_every_outcome(self):
        for response in ({"status_code": 200, "result": {"id": 7}},
                         {"status_code": 400, "error_code": "X"},
                         "garbage"):
            with self.subTest(response=response):
                application, row = self.ready()
                self.s.applications.submit(
                    application, opportunity=row, approval=self.signed(),
                    transport=self.transport(response))
                attempt = self.s.applications.attempts(application.id)[-1]
                self.assertTrue(attempt["response_digest"].startswith("sha256:"))
                self.close()
                self.setUp()


class TheSeamAroundCredentials(Base):

    def test_no_credential_value_reaches_the_attempt_record(self):
        application, row = self.ready()
        self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200, "result": {"id": 5}}))
        for attempt in self.s.applications.attempts(application.id):
            blob = " ".join(str(v) for v in attempt.values()).lower()
            self.assertNotIn(TEST_ONLY_OWNER_KEY.decode().lower(), blob)

    def test_no_credential_value_reaches_the_audit(self):
        application, row = self.ready()
        self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200, "result": {"id": 5}}))
        for event in self.s.audit.events():
            blob = " ".join(str(v) for v in event.values()).lower()
            self.assertNotIn(TEST_ONLY_OWNER_KEY.decode().lower(), blob)

    def test_the_request_names_the_credential_without_carrying_it(self):
        application, row = self.ready()
        self.s.applications.submit(
            application, opportunity=row, approval=self.signed(),
            transport=self.transport({"status_code": 200, "result": {"id": 5}}))
        request = self.sent[0]
        self.assertEqual(request["requires_credential"],
                         "FREELANCER_OAUTH_TOKEN")
        self.assertNotIn(TEST_ONLY_OWNER_KEY.decode(), str(request))


if __name__ == "__main__":
    unittest.main()
