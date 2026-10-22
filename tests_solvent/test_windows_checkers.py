"""The CI checkers, checked.

``tools/windows_install_check.py`` and ``tools/windows_restart_check.py``
are the only things standing between "a real Windows installation
happened" and "a report said so". They had no tests, and it showed: the
install checker's first and broadest check asked every row of the
engine's report for a key named ``ok``, while the engine writes
``outcome``. ``s.get("ok") is False`` was therefore never true for any
report the engine has ever produced. The line printed

    PASS  every installation step reported success: 14 step(s), 0 failed

over a report whose last row read ``FAIL``, which carried a whole
``failure`` block, and whose engine had exited 1. Two CI runs were read
as clean installations on the strength of it.

That is the kind of defect a test of the production code cannot find,
because the production code was right. These tests drive the checkers
the way CI drives them -- as a subprocess, over a report file on disk --
and the first of them fails if a FAIL row ever again reads as a pass.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "windows_install_check.py"
INSTALLER = ROOT / "windows" / "installer"
sys.path.insert(0, str(INSTALLER))

from deskpilot_installer.layout import Layout                # noqa: E402
from deskpilot_installer.report import Outcome               # noqa: E402


def _row(name: str, outcome: Outcome, detail: str = "") -> dict:
    """A row shaped exactly as :meth:`Report.to_json` writes one."""
    return {"name": name, "outcome": outcome.value, "detail": detail}


def _check(report: dict, root: str) -> subprocess.CompletedProcess:
    """Run the checker over ``report``, the way the workflow does."""
    with tempfile.TemporaryDirectory() as box:
        path = pathlib.Path(box) / "install-report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-I", str(CHECKER),
             "--root", root, "--report", str(path)],
            capture_output=True, text=True, check=False, timeout=300.0)


class TheInstallCheckerReadsTheEnginesVocabulary(unittest.TestCase):
    """The report's own words, not a key the engine never writes."""

    #: A root that does not exist. Every check past the report is then
    #: a FAIL for a reason these tests are not about, so each test
    #: below asserts on the report lines by name rather than on the
    #: exit code -- which is non-zero either way.
    ABSENT = r"C:\no-such-deskpilot-root"

    def lines(self, report: dict) -> dict[str, str]:
        done = _check(report, self.ABSENT)
        out = {}
        for line in done.stdout.splitlines():
            if ":" in line and line[:4].strip() in ("PASS", "FAIL", "WARN"):
                mark, rest = line.split(None, 1)
                name, detail = rest.split(":", 1)
                out[name.strip()] = f"{mark} {detail.strip()}"
        return out

    def test_a_failed_step_is_not_reported_as_success(self):
        """The defect. Two CI runs were misread because of this line."""
        report = {"phase": "install", "may_continue": False,
                  "rows": [_row("Verifying package", Outcome.PASS),
                           _row("Restricting the control centre",
                                Outcome.FAIL, "the file was not found")]}
        said = self.lines(report)
        self.assertIn("every installation step reported success", said)
        self.assertTrue(
            said["every installation step reported success"].startswith("FAIL"),
            said["every installation step reported success"])

    def test_a_blocked_step_is_not_reported_as_success_either(self):
        report = {"phase": "install", "rows": [
            _row("Verifying package", Outcome.BLOCKED, "wrong digest")]}
        said = self.lines(report)
        self.assertTrue(
            said["every installation step reported success"].startswith("FAIL"))

    def test_an_all_pass_report_passes(self):
        """The check has to be able to pass, or it is a different bug."""
        report = {"phase": "install", "may_continue": True,
                  "rows": [_row("Verifying package", Outcome.PASS),
                           _row("Creating directories", Outcome.PASS)]}
        said = self.lines(report)
        self.assertTrue(
            said["every installation step reported success"].startswith("PASS"))
        self.assertTrue(said["the engine did not stop part-way"]
                        .startswith("PASS"))

    def test_a_warning_does_not_fail_the_installation(self):
        report = {"phase": "install", "rows": [
            _row("Owner configuration", Outcome.WARN, "items remain")]}
        said = self.lines(report)
        self.assertTrue(
            said["every installation step reported success"].startswith("PASS"))

    def test_the_failure_block_is_read_even_with_no_failing_row(self):
        """An engine can stop before it writes the row that says why."""
        report = {"phase": "install", "may_continue": False,
                  "rows": [_row("Verifying package", Outcome.PASS)],
                  "failure": {"what_failed": "Restricting the control centre",
                              "why": "the file was not found",
                              "changed": [], "not_changed": [],
                              "safe_next_action": "nothing"}}
        said = self.lines(report)
        self.assertTrue(said["the engine did not stop part-way"]
                        .startswith("FAIL"))
        self.assertIn("Restricting", said["the engine did not stop part-way"])

    def test_rows_in_a_shape_this_check_cannot_grade_are_a_finding(self):
        """The failure mode itself, caught by name rather than by luck.

        If the engine's report shape changes again, this is the line
        that says so -- instead of a grading check that silently
        grades nothing and reports zero failures.
        """
        report = {"phase": "install",
                  "rows": [{"name": "Verifying package", "verdict": "fine"}]}
        said = self.lines(report)
        self.assertTrue(
            said["every installation step is graded by this check"]
            .startswith("FAIL"))

    def test_every_outcome_the_engine_can_write_is_classified(self):
        """No outcome falls through to "not a failure" by default."""
        for outcome in Outcome:
            with self.subTest(outcome.value):
                said = self.lines({"phase": "install",
                                   "rows": [_row("A step", outcome)]})
                line = said["every installation step reported success"]
                self.assertEqual(line.startswith("FAIL"),
                                 outcome.stops_installation, line)


class EveryRuleTheHardeningAppliesHasSomethingToApplyTo(unittest.TestCase):
    """The installation failed because ``icacls`` ran before ``mkdir``.

    ``Restricting the control centre`` applies a required rule to the
    request spool. Nothing created the spool: the runtime makes it on
    first write, which is long after the installer finishes. On a real
    Windows runner the step failed with "The system cannot find the
    file specified", the installation stopped there, and the autostart
    tasks registered after it never existed -- so a missing directory
    read on screen as a broken security boundary.
    """

    def setUp(self):
        from deskpilot_installer import isolation
        self.layout = Layout(root=r"C:\DeskPilot")
        self.rules = isolation.rules(self.layout)

    def test_the_spool_is_a_directory_the_installer_creates(self):
        self.assertIn(self.layout.spool, self.layout.all_dirs)

    def test_every_required_rule_targets_something_installed(self):
        """Generalised past the one defect, so the next one is caught."""
        made_by_hand = {self.layout.env_file, self.layout.web_env_file,
                        self.layout.db}
        for rule in self.rules:
            if not getattr(rule, "required", True):
                continue
            with self.subTest(rule.path):
                self.assertTrue(
                    rule.path in self.layout.all_dirs
                    or rule.path in made_by_hand,
                    f"{rule.path} is hardened by a required rule and "
                    f"nothing in the installation creates it")

    def test_the_spool_is_not_promised_to_survive_an_uninstall(self):
        """It holds in-flight requests, not records. Kept separate."""
        self.assertNotIn(self.layout.spool, self.layout.owner_dirs)


if __name__ == "__main__":
    unittest.main()
