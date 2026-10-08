"""The installer under deliberate faults, at every step that can fail.

The matrix covers scenarios the owner might have. This covers the machine
misbehaving mid-operation: a command that hangs, a disk that fills, a
permission that is denied, a tool that vanishes, output that is garbage. Those
are the conditions under which an installer either reports honestly or leaves a
half-built installation and a confused owner.

Each test injects one fault at one step and asserts three things, because any
one of them alone is satisfiable by accident:

1. The installation **stops** — no step after the fault reports success.
2. The owner gets §21's five answers, and the "what was not changed" list is
   **derived from what actually ran**, not recited.
3. Whatever the fault, **MetaTrader is untouched and no owner data is lost**.

The faults are injected at the host seam, where a real failure would arrive,
rather than at a hook added for testing. A fault injected somewhere nothing
uses tests the harness instead of the system.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import (  # noqa: E402
    FakeWindows, Handler, SYSTEM_PYTHON, certified_payload, install_ready,
)

from deskpilot_installer import mt5  # noqa: E402
from deskpilot_installer.engine import Engine, OwnerAnswers  # noqa: E402
from deskpilot_installer.installlog import Log  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402
from deskpilot_installer.probe import Settings  # noqa: E402
from deskpilot_installer.report import Outcome, Secret  # noqa: E402

PACKAGE = r"C:\Media\DeskPilot-certified.zip"
VENV_PYTHON = r"C:\DeskPilot\venv\Scripts\python.exe"
MT5_DIR = r"C:\Program Files\MetaTrader 5"


def answers() -> OwnerAnswers:
    return OwnerAnswers(owner_identity="Jo",
                        web_password=Secret("correct-horse-battery"),
                        ai_model_tag="qwen2.5:14b-instruct")


class FaultInjection(unittest.TestCase):
    """Base: a machine with MetaTrader running, ready to install."""

    def setUp(self):
        data, self.digest = certified_payload()
        self.host = install_ready(package=data, digest=self.digest)
        self.host.add_file(MT5_DIR + r"\terminal64.exe", b"MZ", mtime=500)
        self.host.add_file(MT5_DIR + r"\config\common.ini", b"[C]\n", mtime=501)
        self.host.add_process("terminal64.exe", 4242)
        self.host.handlers = [h for h in self.host.handlers
                              if "show" not in h.must_contain]
        self.host.on("netsh.exe", "show", "rule", stdout="")
        self.before_mt5 = mt5.fingerprint(self.host)
        self.settings = Settings(layout=Layout(), package_path=PACKAGE,
                                 expected_digest=self.digest)

    def install(self):
        log = Log(self.host)
        engine = Engine(self.host, self.settings, answers(), log)
        return engine, engine.install(), log

    def inject(self, *fragments, returncode=1, stdout="", stderr="injected",
               effect=None):
        self.host.handlers.insert(0, Handler(tuple(fragments), returncode,
                                             stdout, stderr, effect))

    def assertStoppedCleanly(self, engine, report, *, expect_in_why=""):
        self.assertFalse(report.may_continue, "the installation must stop")
        self.assertIsNotNone(report.failure, "a failure must be reported")
        if expect_in_why:
            self.assertIn(expect_in_why.lower(), report.failure.why.lower())
        # Exactly one failing row, and it is the last one: nothing after the
        # fault may report success.
        outcomes = [r.outcome for r in report.rows]
        self.assertEqual(outcomes.count(Outcome.FAIL), 1, outcomes)
        self.assertIs(outcomes[-1], Outcome.FAIL)
        # The five answers.
        self.assertTrue(report.failure.what_failed)
        self.assertTrue(report.failure.why)
        self.assertTrue(report.failure.not_changed)
        self.assertTrue(report.failure.safe_next_action)
        # MetaTrader, whatever happened.
        self.assertEqual(
            self.before_mt5.differences(mt5.fingerprint(self.host)), [])
        self.assertTrue(any("MetaTrader was not touched" in line
                            for line in report.failure.not_changed))

    def assertLedgerIsHonest(self, engine, report):
        """What it claims it changed is what it recorded changing."""
        self.assertEqual(list(report.failure.changed), engine.changed)
        # Matched on the exact claims, not on keywords. The MetaTrader line
        # also mentions firewall rules -- its own, truthfully -- and a loose
        # keyword match reads that as a claim about DeskPilot's rule and fails
        # a correct ledger.
        claims = {c.lower() for c in report.failure.not_changed}
        pairs = (
            ("no deskpilot database was created or modified", "database"),
            ("no firewall rule was added or changed", "firewall rule"),
            ("no windows scheduled task was created", "scheduled task"),
            ("no python installation on this computer was changed", "Python"),
        )
        for claim, ledger_word in pairs:
            if claim in claims:
                self.assertFalse(
                    any(ledger_word.lower() in c.lower()
                        for c in engine.changed),
                    f"claimed {claim!r} after recording a change to it: "
                    f"{engine.changed}")


class TheVirtualEnvironmentStep(FaultInjection):

    def test_venv_creation_failing_is_reported(self):
        self.inject(SYSTEM_PYTHON, "-m", "venv", stderr="Error: No such file")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="could not be created")
        self.assertLedgerIsHonest(engine, report)
        self.assertFalse(self.host.exists(Layout().db))

    def test_venv_reporting_no_site_packages_is_reported(self):
        self.inject(VENV_PYTHON, "site.getsitepackages", returncode=0,
                    stdout="")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="did not report its package")

    def test_a_disk_that_fills_while_writing_the_pth_is_reported(self):
        def fill(h, _argv):
            h.disk_full = True

        self.inject(VENV_PYTHON, "site.getsitepackages", returncode=0,
                    stdout=r"C:\DeskPilot\venv\Lib\site-packages", effect=fill)
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report, expect_in_why="no space")


class TheCredentialStep(FaultInjection):

    def test_a_hashing_failure_is_reported_without_echoing_the_password(self):
        self.inject(VENV_PYTHON, "hash_password", stderr="MemoryError")
        engine, report, log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="could not be hashed")
        self.assertFalse(log.contains_secret("correct-horse-battery"))
        self.assertNotIn("correct-horse-battery", report.failure.why)

    def test_hashing_returning_something_unexpected_is_refused(self):
        """A plausible-looking non-hash must not be stored as one."""
        self.inject(VENV_PYTHON, "hash_password", returncode=0,
                    stdout="correct-horse-battery")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="something unexpected")

    def test_a_permission_denied_on_the_secrets_file_stops_everything(self):
        self.host.unwritable.add(r"C:\DeskPilot\Config")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report)
        self.assertFalse(self.host.exists(Layout().env_file))

    def test_icacls_failing_stops_rather_than_leaving_a_readable_key(self):
        self.inject("icacls.exe", stderr="access is denied")
        engine, report, log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="could not restrict access")
        self.assertFalse(log.contains_secret(engine.owner_key)
                         if engine.owner_key else False)

    def test_a_vanished_icacls_is_reported_not_ignored(self):
        """A missing tool is a failure, not an excuse to continue."""
        self.inject("icacls.exe", returncode=1, stderr="not recognised")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report)


class TheDatabaseStep(FaultInjection):
    """Faults at `setup check`, the command that provisions the database.

    Not `doctor`. `doctor` refuses to open a database that does not exist --
    deliberately, so it cannot report on one it just created -- which is why
    using it to provision failed on every fresh machine. These tests inject at
    the command that actually does the work.
    """

    def test_the_application_failing_to_open_the_database_is_reported(self):
        self.inject(VENV_PYTHON, "setup", "check",
                    stderr="sqlite3.DatabaseError: file is not a database")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="not a database")

    def test_a_silent_success_with_no_database_is_caught(self):
        """Exit zero and no file is the lie this check exists for."""
        self.inject(VENV_PYTHON, "setup", "check", returncode=0,
                    stdout="all good")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="was not created")

    def test_an_existing_database_is_not_destroyed_by_a_later_failure(self):
        self.host.add_file(Layout().db, b"SQLite format 3\x00RECORDS")
        self.inject(VENV_PYTHON, "setup", "capability", stderr="refused")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report)
        self.assertEqual(self.host.read_bytes(Layout().db),
                         b"SQLite format 3\x00RECORDS")

    def test_a_timeout_is_reported_as_a_failure(self):
        """A command that never returns must not read as success."""
        from deskpilot_installer.host import CommandResult

        original = self.host.run

        def timeout(argv, **kwargs):
            if "check" in [str(a) for a in argv] and "setup" in [
                    str(a) for a in argv]:
                self.host.commands.append(tuple(str(a) for a in argv))
                return CommandResult(tuple(str(a) for a in argv), 1, "",
                                     "timed out", timed_out=True)
            return original(argv, **kwargs)

        self.host.run = timeout  # type: ignore[method-assign]
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report)


class TheCapabilityStep(FaultInjection):

    def test_a_refused_promotion_stops_the_installation(self):
        self.inject(VENV_PYTHON, "setup", "capability",
                    stderr="refused: the verifier fingerprint no longer matches")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="fingerprint no longer matches")
        self.assertLedgerIsHonest(engine, report)

    def test_the_database_is_reported_as_changed_because_it_was(self):
        """The ledger must not claim the database is untouched here."""
        self.inject(VENV_PYTHON, "setup", "capability", stderr="refused")
        engine, report, _log = self.install()
        self.assertTrue(any("database" in c for c in engine.changed))
        self.assertFalse(
            any("no DeskPilot database was created" in line
                for line in report.failure.not_changed),
            "a false reassurance: the database had already been created")


class TheWindowsIntegrationSteps(FaultInjection):

    def test_a_firewall_rule_that_cannot_be_added_is_reported(self):
        self.inject("netsh.exe", "advfirewall", "firewall", "add",
                    stderr="The requested operation requires elevation")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="could not be added")

    def test_recording_the_posture_failing_is_reported(self):
        self.inject(VENV_PYTHON, "setup", "firewall", stderr="refused")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="could not be recorded")

    def test_a_scheduled_task_that_cannot_be_created_is_reported(self):
        self.inject("schtasks.exe", "/Create", stderr="Access is denied")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report)
        self.assertIn("task", report.failure.why.lower())

    def test_a_failed_task_does_not_leave_the_firewall_rule_unreported(self):
        self.inject("schtasks.exe", "/Create", stderr="Access is denied")
        engine, report, _log = self.install()
        self.assertTrue(any("firewall rule" in c for c in engine.changed))
        self.assertLedgerIsHonest(engine, report)


class ThePythonBootstrapStep(FaultInjection):

    def setUp(self):
        super().setUp()
        # Remove every interpreter so the bootstrap path is taken.
        self.host.path_programs.pop("python.exe", None)
        for candidate in list(self.host.files):
            if candidate.endswith("python313\\python.exe"):
                del self.host.files[candidate]

    def test_a_download_that_fails_is_reported_with_a_manual_fallback(self):
        self.inject("curl.exe", stderr="curl: (6) Could not resolve host")
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report)
        self.assertFalse(self.host.exists(Layout().db))

    def test_a_download_that_does_not_arrive_is_reported(self):
        self.inject("curl.exe", returncode=0)
        engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report, expect_in_why="rejected")

    def test_an_installer_that_fails_is_reported(self):
        from tests_solvent.installer_fake_host import (_bootstrap_bytes,
                                                       pinned_to)

        def arrived(h, argv):
            h.add_file(argv[argv.index("-o") + 1], _bootstrap_bytes())

        self.inject("curl.exe", returncode=0, effect=arrived)
        self.inject("python-3", "-amd64.exe", "/quiet",
                    stderr="installation failed: 0x80070643")
        with pinned_to(_bootstrap_bytes()):
            engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="reported failure")

    def test_an_installer_that_succeeds_but_leaves_nothing_is_reported(self):
        from tests_solvent.installer_fake_host import (_bootstrap_bytes,
                                                       pinned_to)

        def arrived(h, argv):
            h.add_file(argv[argv.index("-o") + 1], _bootstrap_bytes())

        self.inject("curl.exe", returncode=0, effect=arrived)
        self.inject("python-3", "-amd64.exe", "/quiet", returncode=0)
        with pinned_to(_bootstrap_bytes()):
            engine, report, _log = self.install()
        self.assertStoppedCleanly(engine, report,
                                  expect_in_why="could not then be found")

    def test_nothing_was_installed_when_python_could_not_be(self):
        self.inject("curl.exe", stderr="network unreachable")
        engine, report, _log = self.install()
        for directory in (Layout().app, Layout().venv):
            self.assertFalse(self.host.exists(directory))
        self.assertTrue(any("no Python installation on this computer was "
                            "changed" in line
                            for line in report.failure.not_changed))


if __name__ == "__main__":
    unittest.main()
