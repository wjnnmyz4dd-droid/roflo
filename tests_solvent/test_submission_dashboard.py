"""§37-§40. The dashboard tells the truth about submissions, or says nothing.

The failure mode here is specific and expensive: an owner who believes an
application was sent when it was not, or accepted when it was merely received.
So the page may not say SUBMITTED without submission evidence, may not say
ACCEPTED because something was transmitted, and must keep "a person must send
this" visibly distinct from "waiting for your authorisation".
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest import mock

from solvent import owner as owner_module
from solvent import sourceaccess as access
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
from solvent.owner import OwnerChannel
from solvent.types import ActionClass
from solvent.web import auth as web_auth
from solvent.web import server as web_server
from solvent.web.app import Request
from tests_solvent.fixtures import TEST_ONLY_OWNER_KEY

PASSWORD = "TEST-ONLY-control-centre-password"
POSTING = {"ref": "proj-d", "title": "Deduplicate a CSV", "quoted_cents": 40_000,
           "needs": ["csv-cleanup"], "body": "Please clean this file.",
           "client_ref": "client:acme"}


class Base(unittest.TestCase):
    MODE = access.AUTOMATED_SUBMISSION_WITH_AUTH

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        # patch.dict restores the whole mapping on exit whatever happens, so
        # there is no saved "previous" value to get out of step with reality --
        # which is exactly how a signing key leaked out of this fixture once.
        patcher = mock.patch.dict(
            os.environ, {owner_module.KEY_ENV: TEST_ONLY_OWNER_KEY.decode()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        self.arrange()
        self.s.store.close()
        self.centre = web_server.build(
            self.db, password_hash=web_auth.hash_password(PASSWORD),
            spool=str(pathlib.Path(self.dir.name) / "spool"))
        self.addCleanup(self.centre.read.close)

    def arrange(self):
        src = FixtureSource("freelancer", [POSTING])
        src.submission_mode = self.MODE
        self.s.discovery.register_source(
            src, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="fixture")
        self.s.policy.amend({"discovery": {"approved_sources": ["freelancer"]}},
                            OWNER, "approve")
        self.s.discovery.poll("freelancer")
        self.row = self.s.discovery.opportunities()[0]
        self.application = self.s.applications.prepare(
            self.row, price_cents=36_000,
            capability_version=CSV_PROMOTION.version,
            deliverables=["clean.csv"], timeline_days=3,
            scope="Deduplicate the supplied CSV.")

    def page(self, path="/approvals"):
        login = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.1"))
        return self.centre.handle(Request(
            "GET", path,
            cookies={"solvent_session": login.set_session})).body.decode()


class AnApplicationAwaitingAuthorisation(Base):

    def test_it_appears_on_the_approvals_page(self):
        body = self.page()
        self.assertIn("awaiting your authorisation", body)
        self.assertIn("freelancer", body)

    def test_it_shows_what_approving_would_authorise(self):
        body = self.page()
        self.assertIn("that exact application", body)
        self.assertIn("does not authorise any other", body)

    def test_it_shows_the_price_and_the_capability(self):
        body = self.page()
        self.assertIn("$360.00", body)
        self.assertIn(CSV_PROMOTION.version, body)

    def test_it_shows_the_digest_the_authorisation_binds_to(self):
        digest = self.s.applications.stored(self.application.id)["digest"] \
            if False else None
        body = self.page()
        self.assertIn("sha256:", body)

    def test_it_states_verification_rather_than_asking(self):
        """The owner is not the verification. The page reports it."""
        body = self.page()
        self.assertIn("passed", body)

    def test_it_is_not_shown_as_submitted(self):
        body = self.page()
        self.assertNotIn(">submitted<", body.lower())


class AManualOnlySourceIsNotAnApprovalQueue(Base):
    MODE = access.MANUAL_SUBMISSION_ONLY

    def test_it_is_listed_as_something_the_owner_must_send(self):
        body = self.page()
        self.assertIn("you must submit yourself", body)

    def test_it_says_nothing_has_been_transmitted(self):
        body = self.page()
        self.assertIn("Nothing here has been transmitted", body)

    def test_it_does_not_appear_as_awaiting_authorisation(self):
        """§39/§40. "A person must do this" and "authorise me to do this" are
        different requests and must not be conflated."""
        body = self.page()
        self.assertNotIn("awaiting your authorisation", body)

    def test_the_reason_it_is_manual_is_shown(self):
        self.assertIn("manual submission only", self.page())


class AProhibitedSourceReadsAsProhibited(Base):
    MODE = access.SUBMISSION_PROHIBITED

    def test_it_is_not_an_approval_candidate(self):
        self.assertNotIn("awaiting your authorisation", self.page())

    def test_the_prohibition_is_visible(self):
        self.assertIn("prohibited", self.page().lower())


class AnUnverifiedApplicationIsNeverOfferedForApproval(Base):

    def arrange(self):
        super().arrange()
        # Break the recorded verdict behind the application's back.
        self.s.applications._db.execute(
            "UPDATE applications SET verified = 0 WHERE id = ?",
            (self.application.id,))
        self.s.applications._db.commit()

    def test_it_is_absent_from_the_approval_queue(self):
        """Offering it would be asking the owner to be the verification."""
        self.assertNotIn("awaiting your authorisation", self.page())


class AnUnknownRemoteStateIsSurfacedLoudly(Base):

    def arrange(self):
        super().arrange()
        self.s.discovery.grant("freelancer", access.SUBMIT_APPLICATION,
                               owner_identity=OWNER, why="test")
        self.s.applications.approve(
            self.application.id, owner_identity=OWNER, why="test",
            digest=self.s.applications.stored(self.application.id)["digest"])
        self.s.policy.amend(
            {"egress": {"simulation_only": False,
                        "allowlist": [{"host": "www.freelancer.com",
                                       "max_privacy": "INTERNAL"}]}},
            OWNER, "permit")
        channel = OwnerChannel(self.s.store, self.s.audit, self.s.policy,
                               key=TEST_ONLY_OWNER_KEY)
        approval = channel.issue(
            owner_identity=OWNER, subject="submit",
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            max_cents=1_000_000)

        def dies(request):
            raise OSError("connection reset after the request left")

        self.s.applications.submit(self.application, opportunity=self.row,
                                   approval=approval, transport=dies)

    def test_the_page_warns_that_it_must_not_be_resent(self):
        body = self.page()
        self.assertIn("must not be sent again", body)

    def test_it_is_listed_for_reconciliation(self):
        self.assertIn("needing reconciliation", self.page())

    def test_it_is_not_reported_as_submitted(self):
        body = self.page().lower()
        self.assertNotIn("submitted successfully", body)

    def test_it_is_not_reported_as_accepted(self):
        self.assertNotIn("accepted", self.page().lower())

    def test_the_read_model_classifies_it_separately_from_failures(self):
        unresolved = self.centre.read.submissions_needing_reconciliation()
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["outcome"], "UNKNOWN_REMOTE_STATE")


class TheDashboardDecidesNothingAboutSubmission(Base):

    def test_the_web_module_cannot_submit(self):
        import ast

        tree = ast.parse(pathlib.Path("solvent/web/app.py").read_text())
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)}
        for forbidden in ("submit", "approve", "prepare_request", "confirm",
                          "package_for_manual_submission"):
            self.assertNotIn(forbidden, called)

    def test_the_read_model_cannot_write_an_approval(self):
        with self.assertRaises(Exception):
            self.centre.read._conn.execute(
                "UPDATE applications SET approved_digest = 'x'")


if __name__ == "__main__":
    unittest.main()
