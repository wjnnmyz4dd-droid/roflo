"""§2. One authoritative source for diagnostic findings, proved structurally.

The website used to carry its own copy of all eighteen checks, because the
process that serves a port can never hold the authorities (constructing a
``Solvent`` installs the egress hook, which denies ``socket.bind``). Two copies
of one judgement is two answers to one question, and they drifted: the website's
copy had a notification-delivery check the authority lacked, and the authority's
capability wording had moved on without the website.

Deleting one copy is not enough. These tests are the thing that stops a future
change from quietly reintroducing the second, so they assert the *structure*
(the website decides nothing) as well as the *behaviour* (what it renders is
exactly what the authority recorded).
"""

from __future__ import annotations

import ast
import pathlib
import tempfile
import unittest

from solvent import diagnostics as diag
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
from solvent.web import auth as web_auth
from solvent.web import server as web_server
from solvent.web.app import Request

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "solvent" / "web" / "app.py"
PASSWORD = "TEST-ONLY-control-centre-password"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.solvent = Solvent(self.db)
        self.arrange(self.solvent)
        self.report = self.solvent.diagnostics.record()
        self.solvent.store.close()
        self.centre = web_server.build(
            self.db, password_hash=web_auth.hash_password(PASSWORD),
            spool=str(pathlib.Path(self.dir.name) / "spool"))
        self.addCleanup(self.centre.read.close)

    def arrange(self, solvent):
        """Override to change what the authority will find."""

    def page(self, path="/diagnostics"):
        login = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.1"))
        return self.centre.handle(Request(
            "GET", path,
            cookies={"solvent_session": login.set_session})).body.decode()


class TheWebsiteRendersAndDoesNotDecide(Base):

    def test_every_recorded_finding_reaches_the_page(self):
        body = self.page()
        for finding in self.report.findings:
            self.assertIn(finding.name, body)
            self.assertIn(finding.detail[:60].split("(")[0].strip()[:40], body)

    def test_the_page_shows_no_check_the_authority_did_not_record(self):
        """The direction that catches a *re-added* second copy: a row on the
        page that no recorded finding accounts for."""
        read = self.centre.read.latest_diagnostics()
        self.assertEqual([f["name"] for f in read["findings"]],
                         [f.name for f in self.report.findings])

    def test_the_states_shown_are_the_states_recorded(self):
        read = self.centre.read.latest_diagnostics()
        self.assertEqual({f["name"]: f["state"] for f in read["findings"]},
                         {f.name: f.state for f in self.report.findings})

    def test_the_web_module_contains_no_diagnostic_state_literals(self):
        """The structural guard. If someone starts deciding states in the web
        layer again, the words have to appear there -- so they may not.

        ``UNKNOWN`` is exempt: the read model uses it to refuse to pass a stale
        report off as a current verdict, which is the opposite of deciding what
        a check means.
        """
        source = APP.read_text()
        for state in ("HEALTHY", "DEGRADED", "OWNER_ACTION_REQUIRED",
                      "UNSAFE", "FAILED", "WARNING"):
            occurrences = [line for line in source.splitlines()
                           if f'"{state}"' in line or f"'{state}'" in line]
            # The banner compares the authority's own summary; it does not
            # compute one. That read is allowed; constructing findings is not.
            constructing = [ln for ln in occurrences
                            if "out.append" in ln or "Finding(" in ln]
            self.assertEqual(constructing, [], f"{state}: {constructing}")

    def test_the_web_module_has_no_function_that_builds_findings(self):
        tree = ast.parse(APP.read_text())
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("_diagnostic_findings", names)

    def test_the_website_never_imports_the_checks(self):
        """It may import the *constants* it renders with. It may not run the
        checks: it cannot, and pretending otherwise would hide the reason the
        report has to be recorded at all."""
        source = APP.read_text()
        self.assertNotIn("Diagnostics(", source)


class ADivergentWordingWouldBeCaught(Base):
    def arrange(self, solvent):
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)

    def test_the_capability_row_says_what_the_authority_said(self):
        recorded = next(f for f in self.report.findings
                        if f.name == "Capabilities")
        self.assertIn(recorded.detail, self.page())


class AStaleReportIsNotACurrentVerdict(Base):
    """The failure mode the persisted-report design introduces, closed.

    Diagnostics matter most when the runtime that writes them has stopped, so
    an old report showing green would be the reassuring-and-wrong answer this
    whole subsystem exists to prevent.
    """

    def _age_the_report(self, seconds):
        import sqlite3
        import time
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE diagnostic_reports SET ran_at = ?",
                           (time.time() - seconds,))
        connection.commit()
        connection.close()

    def test_a_fresh_report_reads_as_current(self):
        read = self.centre.read.latest_diagnostics()
        self.assertFalse(read["stale"])
        self.assertNotIn("These checks are stale", self.page())

    def test_an_old_report_is_marked_stale(self):
        self._age_the_report(diag.REPORT_STALE_SECONDS + 60)
        self.assertTrue(self.centre.read.latest_diagnostics()["stale"])
        self.assertIn("These checks are stale", self.page())

    def test_a_stale_reports_summary_is_not_allowed_to_read_healthy(self):
        """Even if every recorded check was HEALTHY, a stale report is not
        evidence that anything is healthy *now*."""
        self._age_the_report(diag.REPORT_STALE_SECONDS + 60)
        self.assertEqual(self.centre.read.latest_diagnostics()["worst"],
                         diag.UNKNOWN)

    def test_the_boundary_is_not_off_by_a_wide_margin(self):
        self._age_the_report(diag.REPORT_STALE_SECONDS - 60)
        self.assertFalse(self.centre.read.latest_diagnostics()["stale"])


class NoReportAtAllSaysSo(unittest.TestCase):
    def test_a_system_that_never_ran_the_checks_does_not_show_a_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(pathlib.Path(d) / "solvent.db")
            Solvent(db).store.close()
            centre = web_server.build(
                db, password_hash=web_auth.hash_password(PASSWORD),
                spool=str(pathlib.Path(d) / "spool"))
            self.addCleanup(centre.read.close)
            read = centre.read.latest_diagnostics()
            self.assertFalse(read["present"])
            self.assertEqual(read["findings"], [])
            self.assertEqual(read["worst"], diag.UNKNOWN)

            login = centre.handle(Request("POST", "/login",
                                          form={"password": PASSWORD},
                                          source="10.0.0.1"))
            body = centre.handle(Request(
                "GET", "/diagnostics",
                cookies={"solvent_session": login.set_session})).body.decode()
            self.assertIn("No diagnostics have been recorded yet", body)
            # Critically, it must not render an all-clear. Asserted on the
            # rendered element, not the class name: "banner-ok" also appears in
            # the inline stylesheet, so the bare substring is true on every
            # page and would have made this assertion vacuous.
            self.assertNotIn('<div class="banner banner-ok"', body)
            self.assertIn('<div class="banner banner-warn"', body)


class TheRuntimeKeepsItFresh(unittest.TestCase):
    def test_the_runtime_has_a_task_that_records_a_report(self):
        from solvent.runtime import DEFAULT_INTERVALS

        self.assertIn("diagnostics", DEFAULT_INTERVALS)
        self.assertLess(DEFAULT_INTERVALS["diagnostics"],
                        diag.REPORT_STALE_SECONDS,
                        "the runtime would let its own report go stale")

    def test_the_task_actually_records(self):
        with tempfile.TemporaryDirectory() as d:
            solvent = Solvent(str(pathlib.Path(d) / "solvent.db"))
            self.assertIsNone(solvent.diagnostics.latest())
            solvent.diagnostics.record()
            self.assertIsNotNone(solvent.diagnostics.latest())
            solvent.store.close()


if __name__ == "__main__":
    unittest.main()
