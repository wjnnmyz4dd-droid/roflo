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

import io
import json
import pathlib
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


def _check(report: dict, root: str) -> str:
    """Run the checker over ``report`` and return what it printed.

    In this process, not as a subprocess. The first version of this
    file ran the real command line -- which is what CI runs, and
    looked like the more honest test -- and it passed on its own and
    failed twelve times inside the full suite on Windows. The reason
    is the thing being tested elsewhere in this very suite: the
    egress guard is process-wide and irreversible, and it denies
    ``subprocess.Popen``. A test that spawns cannot live in a suite
    that forbids spawning, and ``tools/verify_egress.py`` already
    says so about itself.

    What is lost is the argument parsing, so the one test that
    exercises ``main`` keeps doing that -- in-process, through
    ``main(argv)``, which is the same entry point the command line
    reaches.
    """
    import contextlib
    import importlib

    sys.path.insert(0, str(ROOT / "tools"))
    checker = importlib.import_module("windows_install_check")
    with tempfile.TemporaryDirectory() as box:
        path = pathlib.Path(box) / "install-report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            checker.main(["--root", root, "--report", str(path)])
        return printed.getvalue()


class TheInstallCheckerReadsTheEnginesVocabulary(unittest.TestCase):
    """The report's own words, not a key the engine never writes."""

    #: A root that does not exist. Every check past the report is then
    #: a FAIL for a reason these tests are not about, so each test
    #: below asserts on the report lines by name rather than on the
    #: exit code -- which is non-zero either way.
    ABSENT = r"C:\no-such-deskpilot-root"

    def lines(self, report: dict) -> dict[str, str]:
        out = {}
        for line in _check(report, self.ABSENT).splitlines():
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


class TheSweepCanFindWhatItIsSweepingFor(unittest.TestCase):
    """The per-file sweep reported no failures over a report with eight.

    The step exists to re-run each failing file alone and print its
    traceback. Its PowerShell found no failing testcase in a junit.xml
    whose own summary line said ``failures="8"``, printed "junit
    records no failing test", and exited 0 -- so the one run with
    failures to investigate produced no investigation. It could not be
    tested anywhere but on a Windows runner mid-failure, which is why
    the reading now lives in ``tools/junit_failing_files.py`` and is
    driven from here.
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from junit_failing_files import failing_files
        self.read = failing_files

    def test_a_failure_and_an_error_are_both_found(self):
        self.assertEqual(
            self.read(_junit([("tests_solvent/test_a.py", "failure"),
                              ("tests_solvent/test_b.py", "error")])),
            ["tests_solvent/test_a.py", "tests_solvent/test_b.py"])

    def test_a_skip_is_not_a_failure(self):
        self.assertEqual(
            self.read(_junit([("tests_solvent/test_a.py", "skipped")])), [])

    def test_a_passing_case_is_not_a_failure(self):
        self.assertEqual(
            self.read(_junit([("tests_solvent/test_a.py", None)])), [])

    def test_the_file_is_recovered_from_classname_when_absent(self):
        """pytest has written junit both ways; the sweep must read both."""
        xml = ('<testsuite tests="1" failures="1">'
               '<testcase classname="tests_solvent.test_bidder_console.C" '
               'name="t"><failure>boom</failure></testcase></testsuite>')
        self.assertEqual(self.read(xml),
                         ["tests_solvent/test_bidder_console.py"])

    def test_a_report_with_no_testsuites_wrapper_is_read(self):
        xml = ('<testsuite tests="1" failures="1">'
               '<testcase classname="tests_solvent.test_a.C" name="t" '
               'file="tests_solvent/test_a.py"><failure/></testcase>'
               '</testsuite>')
        self.assertEqual(self.read(xml), ["tests_solvent/test_a.py"])

    def test_each_file_is_named_once_however_many_cases_failed(self):
        four = [("tests_solvent/test_bidder_console.py", "failure")] * 4
        self.assertEqual(self.read(_junit(four)),
                         ["tests_solvent/test_bidder_console.py"])

    def test_windows_separators_are_normalised(self):
        xml = _junit([(r"tests_solvent\test_a.py", "failure")])
        self.assertEqual(self.read(xml), ["tests_solvent/test_a.py"])

    def test_the_eight_failures_of_run_28_would_have_been_found(self):
        """The report that defeated the old reading, in its own shape."""
        names = ["tests_solvent/test_bidder_console.py"] * 4 + [
            "tests_solvent/test_doctor_database_selection.py"] * 2 + [
            "tests_solvent/test_owner_setup.py",
            "tests_solvent/test_production_supervision.py"]
        found = self.read(_junit([(n, "failure") for n in names]))
        self.assertEqual(len(found), 4)
        self.assertIn("tests_solvent/test_production_supervision.py", found)


def _junit(cases: list) -> str:
    """A junit document in the shape pytest's ``--junitxml`` writes."""
    body = []
    for index, (name, outcome) in enumerate(cases):
        inner = f"<{outcome}>detail</{outcome}>" if outcome else ""
        dotted = name.replace("\\", "/").replace("/", ".")[:-3]
        body.append(f'<testcase classname="{dotted}.C" name="t{index}" '
                    f'file="{name}">{inner}</testcase>')
    failures = sum(1 for _n, o in cases if o in ("failure", "error"))
    return (f'<testsuites><testsuite name="pytest" tests="{len(cases)}" '
            f'failures="{failures}" errors="0" skipped="0">'
            + "".join(body) + "</testsuite></testsuites>")


class TheInstallerIsCheckedAgainstWhatItWasBuiltFrom(unittest.TestCase):
    """The provenance check, and the two versions of it that were wrong.

    The installer refuses to install any payload whose SHA-256 is
    not the one compiled into it, so CI should assert that promise
    is intact. Two attempts asserted something else.

    The first searched the executable's bytes for the digest as
    ASCII and failed a build that was correct. The second added
    UTF-16LE, reasoning that NSIS writes its strings that way, and
    failed a correct build again. Neither could ever have worked:
    DeskPilot-Setup.nsi sets ``SetCompressor /SOLID lzma``, so the
    string table is inside a compressed block. Both versions were
    asserting something untrue of every NSIS installer, and each
    rejected a 25 MB installer that was in every respect fine.

    What is checkable is checked, and what is not is said out loud.
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import installer_carries_digest as module
        self.module = module
        self.digest = ("3fb1e25a5f7bc05d809deaa3edc7e7bd"
                       "3f80cad9a0a133631167a0f8902a05ca")

    def provenance(self, digest: str = "", name: str = "p.zip") -> str:
        return ("source commit       : c8e06b5" + chr(10)
                + "payload             : " + name + chr(10)
                + "payload SHA-256     : " + (digest or self.digest) + chr(10))

    def test_the_digest_is_read_from_the_builds_own_provenance(self):
        self.assertEqual(self.module.payload_digest(self.provenance()),
                         self.digest)
        self.assertEqual(self.module.payload_name(self.provenance()), "p.zip")

    def test_provenance_naming_no_payload_digest_is_a_finding(self):
        self.assertEqual(self.module.payload_digest("nothing useful"), "")

    def test_the_real_script_uses_the_digest_in_both_places(self):
        nsi = (ROOT / "windows" / "installer"
               / "DeskPilot-Setup.nsi").read_text(encoding="utf-8")
        self.assertEqual(self.module.script_uses_the_digest(nsi), [])

    def test_a_script_that_never_compares_the_payload_is_a_finding(self):
        self.assertTrue(self.module.script_uses_the_digest(
            'nsExec::ExecToLog \'install --expect-sha256 '
            '"${PAYLOAD_SHA256}"\''))

    def test_a_script_that_never_tells_the_engine_is_a_finding(self):
        self.assertTrue(self.module.script_uses_the_digest(
            '${If} $R3 == "${PAYLOAD_SHA256}"'))

    def test_a_script_missing_both_names_both(self):
        self.assertEqual(len(self.module.script_uses_the_digest("")), 2)

    def test_a_file_that_is_not_an_executable_is_refused(self):
        with tempfile.TemporaryDirectory() as box:
            code = self._run(box, exe_bytes=b"not a PE")
            self.assertEqual(code, 1)

    def test_a_digest_that_does_not_match_the_staged_payload_is_refused(self):
        with tempfile.TemporaryDirectory() as box:
            code = self._run(box, recorded="b" * 64)
            self.assertEqual(code, 1)

    def test_a_real_looking_build_passes(self):
        with tempfile.TemporaryDirectory() as box:
            self.assertEqual(self._run(box), 0)

    def _run(self, box: str, *, exe_bytes: bytes = b"MZ\x00\x00",
             recorded: str = "") -> int:
        """Lay out a dist directory the way build.py leaves one."""
        import hashlib

        dist = pathlib.Path(box)
        (dist / "stage").mkdir()
        payload = dist / "stage" / "p.zip"
        payload.write_bytes(b"payload bytes")
        true_digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        exe = dist / "DeskPilot-Setup.exe"
        exe.write_bytes(exe_bytes)
        build_txt = dist / "BUILD.txt"
        build_txt.write_text(self.provenance(recorded or true_digest),
                             encoding="utf-8")
        nsi = dist / "DeskPilot-Setup.nsi"
        nsi.write_text('${If} $R3 == "${PAYLOAD_SHA256}"' + chr(10)
                       + 'install --expect-sha256 "${PAYLOAD_SHA256}"' + chr(10),
                       encoding="utf-8")
        return self.module.main(["x", str(exe), str(build_txt), str(nsi)])


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
        from deskpilot_installer import isolation

        # Files rather than directories, each created by a named step
        # of the installation: the two environment files, the
        # database, and the two SQLite sidecars the database step
        # creates empty so that these very rules have a target.
        made_by_hand = {self.layout.env_file, self.layout.web_env_file,
                        self.layout.db, *isolation.sidecars(self.layout)}
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
