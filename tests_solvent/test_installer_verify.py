"""Verification examines the installed database, not a convenient empty one.

The defect this file exists to prevent is already in DeskPilot's history:
``doctor`` opened a default database path and reported on that instead of the
deployment's, so the verification passed because it had examined nothing. A
check that cannot fail is worse than no check, because it is mistaken for one.

So every assertion here is about the *target* of a command rather than its
result. The installed database path has to appear on each authoritative
invocation, the default path must never appear, and a missing database has to
be reported as a failure rather than quietly created and declared healthy.

The second half covers the other way a verification can lie: reporting a
capability as deliverable without asking the capability authority whether it
would actually deploy. ``may_deploy`` is read back from Solvent, and a
capability that is proven but refused is a failure, not a pass.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import (  # noqa: E402
    FakeWindows, Handler, certified_payload, install_ready,
)

from deskpilot_installer.engine import (  # noqa: E402
    Engine, OwnerAnswers, readiness_headline,
)
from deskpilot_installer.installlog import Log  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402
from deskpilot_installer.probe import Settings  # noqa: E402
from deskpilot_installer.report import Outcome, Secret  # noqa: E402

PACKAGE = r"C:\Media\DeskPilot-certified.zip"
VENV_PYTHON = r"C:\DeskPilot\venv\Scripts\python.exe"

#: What Solvent's own default would be if the installer forgot to say otherwise.
SOLVENT_DEFAULT_DB = "/var/lib/solvent/solvent.db"


def answers() -> OwnerAnswers:
    return OwnerAnswers(owner_identity="Jo",
                        web_password=Secret("correct-horse-battery"),
                        ai_model_tag="qwen2.5:14b-instruct")


def installed(root: str = r"C:\DeskPilot") -> tuple[FakeWindows, Engine]:
    data, digest = certified_payload()
    host = install_ready(package=data, digest=digest, root=root)
    layout = Layout(root=root)
    engine = Engine(host, Settings(layout=layout, package_path=PACKAGE,
                                   expected_digest=digest), answers(),
                    Log(host))
    report = engine.install()
    assert report.may_continue, [r.detail for r in report.blockers]
    return host, engine


class VerificationTargetsTheRealDatabase(unittest.TestCase):

    def setUp(self):
        self.host, self.engine = installed()
        self.layout = Layout()
        self.host.commands.clear()
        self.report = self.engine.verify(probe_inference=False)

    def _authoritative(self) -> list[tuple[str, ...]]:
        """Commands that asked Solvent something about the deployment."""
        wanted = ("health", "readiness", "doctor", "check")
        return [argv for argv in self.host.commands
                if any(w in argv for w in wanted)]

    def test_the_authoritative_commands_were_actually_run(self):
        """A test about targets is vacuous if nothing ran."""
        self.assertGreaterEqual(len(self._authoritative()), 4)

    def test_every_authoritative_command_names_the_installed_database(self):
        for argv in self._authoritative():
            self.assertIn("--db", argv, f"no --db in {' '.join(argv)}")
            self.assertEqual(argv[argv.index("--db") + 1], self.layout.db,
                             f"wrong database in {' '.join(argv)}")

    def test_no_command_falls_back_to_the_applications_default_path(self):
        for argv in self.host.commands:
            self.assertNotIn(SOLVENT_DEFAULT_DB, " ".join(argv))

    def test_health_readiness_and_doctor_are_all_reported(self):
        for name in ("Health", "Readiness", "Diagnostics",
                     "Owner configuration"):
            row = self.report.row(name)
            self.assertIsNotNone(row, f"{name} was not reported")
            self.assertEqual(row.outcome, Outcome.PASS)
            self.assertEqual(row.facts.get("db"), self.layout.db)

    def test_a_non_default_install_root_is_followed_too(self):
        """The path must come from the layout, not from a constant."""
        host, engine = installed(root=r"D:\Apps\DeskPilot")
        host.commands.clear()
        engine.verify(probe_inference=False)
        expected = Layout(root=r"D:\Apps\DeskPilot").db
        targets = [argv[argv.index("--db") + 1] for argv in host.commands
                   if "--db" in argv]
        self.assertTrue(targets)
        for target in targets:
            self.assertEqual(target, expected)

    def test_a_missing_database_is_a_failure_not_a_fresh_start(self):
        host, engine = installed()
        host.remove_tree(Layout().data)
        report = engine.verify(probe_inference=False)
        self.assertFalse(report.may_continue)
        self.assertEqual(report.row("Database").outcome, Outcome.FAIL)
        self.assertIn("no database at", report.row("Database").detail)

    def test_a_missing_database_runs_no_further_checks(self):
        """It must not proceed to report health against nothing."""
        host, engine = installed()
        host.remove_tree(Layout().data)
        host.commands.clear()
        engine.verify(probe_inference=False)
        self.assertEqual(host.commands, [])

    def test_a_failing_authority_fails_the_verification(self):
        host, engine = installed()
        host.handlers.insert(0, Handler((VENV_PYTHON, "readiness"), 1, "",
                                        "HALTED: the kill switch is thrown"))
        report = engine.verify(probe_inference=False)
        self.assertFalse(report.may_continue)
        self.assertEqual(report.row("Readiness").outcome, Outcome.FAIL)

    def test_the_headline_names_the_blocker(self):
        host, engine = installed()
        host.handlers.insert(0, Handler((VENV_PYTHON, "readiness"), 1, "",
                                        "HALTED"))
        headline = readiness_headline(engine.verify(probe_inference=False))
        self.assertTrue(headline.startswith("NOT READY"))
        self.assertIn("Readiness", headline)

    def test_the_headline_is_ready_only_when_everything_passed(self):
        self.assertEqual(readiness_headline(
            self.engine.verify(probe_inference=True)), "DESKPILOT READY")


class TheCapabilityIsReadBackNotAssumed(unittest.TestCase):

    def test_a_deliverable_capability_passes(self):
        host, engine = installed()
        row = engine.verify(probe_inference=False).row("Certified capability")
        self.assertEqual(row.outcome, Outcome.PASS)
        self.assertIn("may_deploy=True", row.detail)

    def test_a_proven_but_undeployable_capability_fails(self):
        """Proven is not deliverable. The installer must ask, not infer."""
        host, engine = installed()
        host.handlers.insert(0, Handler(
            (VENV_PYTHON, "may_deploy"), 0,
            '[{"name": "csv-cleanup", "proven": true, "may_deploy": false, '
            '"why": "the verifier fingerprint no longer matches"}]', ""))
        report = engine.verify(probe_inference=False)
        row = report.row("Certified capability")
        self.assertEqual(row.outcome, Outcome.FAIL)
        self.assertIn("fingerprint no longer matches", row.detail)
        self.assertFalse(report.may_continue)

    def test_no_capability_at_all_fails(self):
        host, engine = installed()
        host.handlers.insert(0, Handler((VENV_PYTHON, "may_deploy"), 0,
                                        "[]", ""))
        row = engine.verify(probe_inference=False).row("Certified capability")
        self.assertEqual(row.outcome, Outcome.FAIL)

    def test_an_unreadable_capability_list_fails_rather_than_passes(self):
        host, engine = installed()
        host.handlers.insert(0, Handler((VENV_PYTHON, "may_deploy"), 0,
                                        "not json at all", ""))
        row = engine.verify(probe_inference=False).row("Certified capability")
        self.assertEqual(row.outcome, Outcome.FAIL)

    def test_the_capability_is_queried_against_the_installed_database(self):
        host, engine = installed()
        host.commands.clear()
        engine.verify(probe_inference=False)
        queries = [argv for argv in host.commands
                   if any("may_deploy" in a for a in argv)]
        self.assertTrue(queries)
        for argv in queries:
            self.assertIn(Layout().db, argv)

    def test_the_installer_never_registers_the_capability_itself(self):
        """It asks Solvent to, and Solvent re-runs the verifier."""
        host, engine = installed()
        registrations = [argv for argv in host.commands
                         if "capability" in argv]
        self.assertTrue(registrations)
        for argv in registrations:
            self.assertIn("setup", argv)
            self.assertIn("solvent.cli", argv)


if __name__ == "__main__":
    unittest.main()


class TheEnvironmentRebuildGuard(unittest.TestCase):
    """A guard with no live caller, tested directly rather than deleted.

    Upgrade used to seed its interpreter from the recorded install state, which
    is the virtual environment's own Python, and then try to rebuild that
    environment with it. The caller was fixed to re-discover a system Python
    first, so nothing reaches this guard today. It is kept because the mistake
    is easy to reintroduce and the error Windows gives for it is obscure -- and
    it is tested here because a guard no test covers is a guard that silently
    stops working.
    """

    def test_rebuilding_a_venv_with_its_own_interpreter_is_refused(self):
        from deskpilot_installer.engine import StepFailed
        host, engine = installed()
        engine.python_exe = r"C:\DeskPilot\venv\Scripts\python.exe"
        with self.assertRaises(StepFailed) as caught:
            engine.step_venv()
        self.assertIn("cannot be rebuilt using the interpreter inside it",
                      caught.exception.detail)

    def test_a_system_interpreter_is_accepted(self):
        host, engine = installed()
        engine.python_exe = r"C:\Program Files\Python313\python.exe"
        result = engine.step_venv()
        self.assertTrue(result.ok)
