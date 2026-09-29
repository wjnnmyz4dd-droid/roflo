"""§27, §28, §60, §61. The dashboard shows what is true, and decides nothing.

Two failure modes, opposite in shape. A dashboard that **hard-codes** what it
shows tells the owner about a system that no longer exists. A dashboard that
**decides** anything becomes a second authority, and the owner's browser
becomes part of the security boundary.

So: change the backend, and the page changes without anyone editing it.
Attack the page, and nothing changes at all.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from solvent import skillslab as lab
from solvent.capability import Capability
from solvent.harness import OWNER, Solvent
from solvent.web import auth as web_auth
from solvent.web import server as web_server
from solvent.web.app import Request
from test_skills_lab import verified_project

PASSWORD = "TEST-ONLY-control-centre-password"
APP = pathlib.Path("solvent/web/app.py")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
        self.covers = frozenset({"extract_pdf_text"})
        self.arrange()
        self.s.store.close()
        self.centre = web_server.build(
            self.db, password_hash=web_auth.hash_password(PASSWORD),
            spool=str(pathlib.Path(self.dir.name) / "spool"))
        self.addCleanup(self.centre.read.close)

    def arrange(self):
        pass

    def page(self, path="/skills"):
        login = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.1"))
        return self.centre.handle(Request(
            "GET", path,
            cookies={"solvent_session": login.set_session})).body.decode()


class AnEmptySystemSaysSo(Base):
    def test_it_does_not_invent_a_skill(self):
        body = self.page()
        self.assertIn("No skill has been registered or certified yet", body)
        self.assertNotIn("csv-cleanup", body)


class TheInventoryFollowsTheRegistry(Base):
    def arrange(self):
        self.s.capability.register(
            Capability(name="pdf-extract", covers=self.covers, proven=True,
                       version="pdf-extract/1.0",
                       verifiable_by=tuple(self.covers),
                       proven_levels=("L1", "L2", "L3"), fixtures_passed=25,
                       fixtures_total=25, false_completions=0,
                       evidence_ref="tests/evidence"),
            owner_identity=OWNER)

    def test_the_registered_skill_appears(self):
        body = self.page()
        self.assertIn("pdf-extract", body)
        self.assertIn("pdf-extract/1.0", body)

    def test_it_is_shown_as_proven(self):
        self.assertIn("proven", self.page())

    def test_a_version_bump_is_reflected_without_editing_the_page(self):
        """§27. The whole point: 1.0 becomes 1.1 because the registry says so."""
        before = self.page()
        self.assertIn("pdf-extract/1.0", before)
        self.assertNotIn("pdf-extract/1.1", before)

        solvent = Solvent(self.db)
        solvent.capability.register(
            Capability(name="pdf-extract", covers=self.covers, proven=True,
                       version="pdf-extract/1.1",
                       verifiable_by=tuple(self.covers),
                       proven_levels=("L1", "L2", "L3"), fixtures_passed=30,
                       fixtures_total=30, false_completions=0,
                       evidence_ref="tests/evidence-1.1"),
            owner_identity=OWNER)
        solvent.store.close()

        after = self.page()
        self.assertIn("pdf-extract/1.1", after)

    def test_no_skill_name_is_written_into_the_page_source(self):
        """A hard-coded card would have to name a skill. None does."""
        source = APP.read_text()
        for name in ("pdf-extract", "csv-cleanup", "report-builder"):
            self.assertNotIn(name, source)


class ValidationStateReachesTheDashboard(Base):
    """§57. Truthful workflow state, including the unflattering ones."""

    def arrange(self):
        self.project = verified_project(self.s, skill="pdf-extract",
                                        covers=self.covers)
        self.s.skillslab.mark_specified(project_id=self.project,
                                        target_covers=self.covers,
                                        target_version="pdf-extract/1.0")
        self.s.skillslab.mark_built(project_id=self.project,
                                    fingerprint="sha256:aa")

    def advance(self, fn):
        solvent = Solvent(self.db)
        try:
            fn(solvent)
        finally:
            solvent.store.close()

    def test_a_built_candidate_is_not_shown_as_validated(self):
        body = self.page()
        self.assertIn("built", body.lower())
        self.assertNotIn("validation requested", body.lower())

    def test_requesting_validation_shows_on_the_page(self):
        self.advance(lambda s: s.skillslab.request_validation(
            project_id=self.project, fingerprint="sha256:aa"))
        self.assertIn("validation requested", self.page().lower())

    def test_a_failed_validation_shows_as_failed(self):
        def fail(s):
            s.skillslab.request_validation(project_id=self.project,
                                           fingerprint="sha256:aa")
            s.skillslab.record_validation(
                project_id=self.project, validator="external:v", passed=False,
                fingerprint="sha256:aa", detail="returned empty text")
        self.advance(fail)
        self.assertIn("validation failed", self.page().lower())

    def test_a_failed_validation_is_not_dressed_up_as_progress(self):
        def fail(s):
            s.skillslab.request_validation(project_id=self.project,
                                           fingerprint="sha256:aa")
            s.skillslab.record_validation(
                project_id=self.project, validator="external:v", passed=False,
                fingerprint="sha256:aa", detail="returned empty text")
        self.advance(fail)
        body = self.page()
        self.assertNotIn("certified", body.lower().split("validation failed")[0]
                         [-200:])

    def test_a_passed_validation_shows_certified(self):
        def pass_it(s):
            s.skillslab.request_validation(project_id=self.project,
                                           fingerprint="sha256:aa")
            s.skillslab.record_validation(
                project_id=self.project, validator="external:v", passed=True,
                fingerprint="sha256:aa")
        self.advance(pass_it)
        self.assertIn("certified", self.page().lower())

    def test_the_certified_artifact_is_shown(self):
        """So the owner can see that what they are approving is what was
        tested, rather than taking it on faith."""
        def pass_it(s):
            s.skillslab.request_validation(project_id=self.project,
                                           fingerprint="sha256:aa")
            s.skillslab.record_validation(
                project_id=self.project, validator="external:v", passed=True,
                fingerprint="sha256:aa")
        self.advance(pass_it)
        self.assertIn("sha256:aa", self.page())


class TheDashboardDecidesNothing(Base):
    """§61. Backend authority, whatever the browser sends."""

    def test_the_web_module_cannot_validate(self):
        """Asserted on calls rather than substrings, for the same reason as
        below: "capability.registered" is an audit event this page renders in
        plain English, and a substring match cannot tell a label from a call.
        """
        import ast

        forbidden = {"record_validation", "request_validation",
                     "record_certification", "record_promotion", "register",
                     "decide", "amend", "promote"}
        tree = ast.parse(APP.read_text())
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        self.assertEqual(called & forbidden, set())

    def test_the_web_module_cannot_reach_the_lab_to_write(self):
        """Asserted on calls, not on the substring.

        "skillslab." also appears in the audit event names this page renders
        as plain English, so the bare substring is true on a page that calls
        nothing -- which would make this assertion vacuous in the direction
        that matters.
        """
        import ast

        tree = ast.parse(APP.read_text())
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                value = node.func.value
                if isinstance(value, ast.Attribute) and value.attr == "skillslab":
                    calls.append(node.func.attr)
                elif isinstance(value, ast.Name) and value.id == "skillslab":
                    calls.append(node.func.attr)
        self.assertEqual(calls, [])

    def test_the_read_model_cannot_write(self):
        with self.assertRaises(Exception):
            self.centre.read._conn.execute(
                "UPDATE skill_projects SET stage = 'PROMOTED'")


if __name__ == "__main__":
    unittest.main()
