"""The installer's laws, as tests.

The installer is allowed to orchestrate DeskPilot and forbidden to become part
of it. That boundary is easy to state and easy to erode one convenient import
at a time, so these tests read the source with ``ast`` and assert the shape
rather than trusting the docstrings that describe it.

Four laws, each with a specific failure it prevents:

* **Standard library only.** DeskPilot's whole dependency story is that it has
  none; an installer that needed a package would reintroduce the pip step this
  project exists to delete.
* **No test double in the product.** The fake Windows host must not be
  importable from the shipped package, for the same reason
  ``tests_solvent/faults.py`` is kept out of ``solvent/``.
* **No shell.** Every subprocess goes through ``Host.run`` with an argument
  list. A ``shell=True`` anywhere, or a ``cmd.exe /c``, reintroduces command
  injection and the unquoted-path bug as live possibilities.
* **No second authority.** The installer must not compute what Solvent decides.
  It may run ``solvent readiness``; it may not decide readiness.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "windows" / "installer"
PACKAGE = INSTALLER / "deskpilot_installer"
sys.path.insert(0, str(INSTALLER))


def modules() -> dict[str, ast.Module]:
    return {p.stem: ast.parse(p.read_text(), filename=str(p))
            for p in sorted(PACKAGE.glob("*.py"))}


def imported_names(tree: ast.Module) -> set[str]:
    """Top-level package names this module imports from outside itself."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


class ThePackageIsSelfContained(unittest.TestCase):

    def test_there_is_an_installer_package(self):
        self.assertTrue(PACKAGE.is_dir(), f"{PACKAGE} is missing")
        self.assertGreater(len(list(PACKAGE.glob("*.py"))), 5)

    def test_every_module_imports_only_the_standard_library(self):
        allowed = set(sys.stdlib_module_names)
        offenders = []
        for name, tree in modules().items():
            external = imported_names(tree) - allowed
            if external:
                offenders.append(f"{name}.py imports non-stdlib: "
                                 f"{sorted(external)}")
        self.assertEqual(offenders, [])

    def test_the_installer_does_not_import_the_application(self):
        """It runs ``solvent`` as a subprocess; it never imports it.

        Importing would mean the installer had to be on the same Python as the
        application and would couple its start-up to the application's. Running
        the CLI keeps the authority where it is and the coupling at the process
        boundary.
        """
        offenders = [name for name, tree in modules().items()
                     if "solvent" in imported_names(tree)]
        self.assertEqual(offenders, [])

    def test_the_fake_host_is_not_reachable_from_the_product(self):
        offenders = []
        for name, tree in modules().items():
            names = imported_names(tree)
            if any(n.startswith("installer_fake") or n == "tests_solvent"
                   for n in names):
                offenders.append(name)
        self.assertEqual(offenders, [],
                         "the shipped installer must not import its test double")

    def test_the_fake_host_is_not_mentioned_in_the_product_source(self):
        """Caught by text as well as by import: a lazy import inside a function
        is still a dependency, and ``ast`` on its own would miss a string
        passed to ``importlib``."""
        offenders = [p.name for p in PACKAGE.glob("*.py")
                     if "installer_fake_host" in p.read_text()]
        self.assertEqual(offenders, [])


class NothingReachesAShell(unittest.TestCase):

    def test_no_module_passes_shell_true(self):
        offenders = []
        for name, tree in modules().items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "shell":
                        literal = getattr(kw.value, "value", None)
                        if literal is not False:
                            offenders.append(f"{name}.py passes shell="
                                             f"{ast.dump(kw.value)}")
        self.assertEqual(offenders, [])

    def test_no_module_invokes_a_command_interpreter(self):
        banned = ("cmd.exe", "cmd /c", "/bin/sh", "powershell.exe",
                  "pwsh.exe", "os.system")
        offenders = []
        for path in PACKAGE.glob("*.py"):
            text = path.read_text()
            for needle in banned:
                # The docstrings explain *why* PowerShell is avoided, so only
                # count occurrences outside comments and docstrings.
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"'):
                        continue
                    if needle in line and "powershell" not in stripped.lower():
                        offenders.append(f"{path.name}: {needle}")
                    elif needle in line and needle.startswith("powershell"):
                        offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [])

    def test_the_host_run_signature_takes_a_sequence_not_a_string(self):
        from deskpilot_installer.host import Host
        import inspect
        sig = inspect.signature(Host.run)
        self.assertIn("argv", sig.parameters)
        annotation = str(sig.parameters["argv"].annotation)
        self.assertIn("Sequence", annotation,
                      "run() must take an argument list, never a command string")

    def test_running_with_no_program_is_refused(self):
        from deskpilot_installer.host import WindowsHost
        with self.assertRaises(ValueError):
            WindowsHost().run([])


class TheInstallerIsNotASecondAuthority(unittest.TestCase):
    """It may report what Solvent decided. It may not decide instead."""

    #: Verbs that would mean the installer had formed its own opinion about
    #: something Solvent owns.
    FORBIDDEN_FUNCTION_NAMES = (
        "certify", "promote_capability", "decide_readiness", "compute_readiness",
        "approve", "sign_approval", "set_policy", "grant",
    )

    def test_no_module_defines_a_decision_solvent_owns(self):
        offenders = []
        for name, tree in modules().items():
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in self.FORBIDDEN_FUNCTION_NAMES:
                        offenders.append(f"{name}.py defines {node.name}()")
        self.assertEqual(offenders, [])

    def test_capability_and_readiness_are_read_by_running_the_application(self):
        """The two verdicts most tempting to recompute are both delegated.

        Matched against the source with its whitespace collapsed, because the
        argument lists this looks for are wrapped across lines by the formatter
        and a test that breaks on reflowing is a test nobody keeps.
        """
        engine = " ".join((PACKAGE / "engine.py").read_text().split())
        self.assertIn('"setup", "capability"', engine,
                      "capability promotion must be delegated to solvent")
        self.assertIn('("readiness",)', engine,
                      "readiness must be read from solvent, not computed")
        self.assertIn("may_deploy", engine,
                      "deployability must be read back from the capability "
                      "authority")

    def test_the_installer_does_not_hash_passwords_itself(self):
        """Solvent's ``hash_password`` is the only authority for the stored form.

        A second implementation with the same parameters would be correct on
        the day it was written and silently wrong the day either was tuned.
        """
        offenders = []
        for path in PACKAGE.glob("*.py"):
            text = path.read_text()
            if "pbkdf2_hmac" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [],
                         "password hashing belongs to solvent.web.auth")

    def test_the_owner_key_floor_agrees_with_the_application(self):
        """The installer's own floor must not drift below Solvent's.

        Stated as an equality against the real constant: a generated key that
        the application would refuse is a provisioning failure the owner would
        only discover at their first approval.
        """
        sys.path.insert(0, str(ROOT))
        from solvent.owner import MIN_KEY_BYTES
        from deskpilot_installer.secrets_env import (
            MIN_KEY_BYTES as INSTALLER_FLOOR,
        )
        self.assertEqual(INSTALLER_FLOOR, MIN_KEY_BYTES)

    def test_the_password_floor_agrees_with_the_application(self):
        sys.path.insert(0, str(ROOT))
        from deskpilot_installer.secrets_env import MIN_PASSWORD_LENGTH
        from solvent.web import auth
        import inspect
        source = inspect.getsource(auth.hash_password)
        self.assertIn(f"len(password) < {MIN_PASSWORD_LENGTH}", source,
                      "the installer's minimum password length must match the "
                      "one solvent.web.auth enforces")


class ThePackagedEntryPointIsTestedSeparately(unittest.TestCase):
    """The subprocess suite exists, is real, and names its own command.

    Those tests cannot run in a combined interpreter: DeskPilot's egress guard
    denies ``subprocess.Popen`` once installed, and an audit hook cannot be
    removed. Rather than weaken the guard, that suite skips with the command
    to run it on its own -- and these tests make sure it cannot quietly become
    an empty suite that always "passes" by being skipped.
    """

    SUITE = (pathlib.Path(__file__).resolve().parent
             / "test_installer_packaged_entrypoint.py")

    def test_the_suite_exists(self):
        self.assertTrue(self.SUITE.is_file(),
                        "the packaged entry point must be tested by execution")

    def test_it_declares_how_to_run_it(self):
        text = self.SUITE.read_text()
        self.assertIn("RUN_SEPARATELY", text)
        self.assertIn("test_installer_packaged_entrypoint", text)

    def test_it_actually_starts_subprocesses(self):
        """A suite that stopped executing anything would prove nothing."""
        text = self.SUITE.read_text()
        self.assertIn("subprocess.run", text)
        self.assertIn('"-I"', text)

    def test_it_covers_the_launcher_and_the_real_application(self):
        text = self.SUITE.read_text()
        for required in ("deskpilot-engine.py", "solvent.cli",
                          "attempted relative import", "selfcheck"):
            self.assertIn(required, text,
                          f"the suite no longer covers {required}")

    def test_it_does_not_open_an_egress_window(self):
        """Impersonating the Action Gate would be weakening a control.

        Checked against the parsed syntax tree, not the text: the suite
        explains in prose why it does not call ``egress.window()``, and a
        substring search cannot tell an explanation from a call.
        """
        tree = ast.parse(self.SUITE.read_text())
        forbidden = {"window", "posix_spawn", "execv", "execvp", "fork",
                     "addaudithook"}
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Attribute):
                    called.add(target.attr)
                elif isinstance(target, ast.Name):
                    called.add(target.id)
        leaked = called & forbidden
        self.assertEqual(leaked, set(),
                         f"the suite must not bypass the egress guard: "
                         f"{sorted(leaked)}")


class TheWizardDelegatesToTheEngine(unittest.TestCase):
    """The native script must not grow its own copy of a decision."""

    def setUp(self):
        self.nsi = (INSTALLER / "DeskPilot-Setup.nsi").read_text()

    def test_the_wizard_exists_and_is_built_from_the_engine(self):
        """It runs the launcher beside the package, never a module inside it.

        This assertion used to require the opposite, and that is precisely the
        invocation that could never work: a package module run as a script has
        no parent package, so its relative imports fail immediately.
        """
        self.assertIn("deskpilot-engine.py", self.nsi)
        self.assertNotIn("deskpilot_installer\\cli.py", self.nsi)

    def test_the_wizard_does_not_choose_a_model(self):
        """Model sizing lives in ``ai.py``. A copy in the wizard would be a
        second recommender, and the two would diverge."""
        for tag in ("qwen2.5:14b", "qwen2.5:7b", "qwen2.5:3b", "qwen2.5:32b"):
            self.assertNotIn(tag, self.nsi,
                             f"the wizard must not name {tag}; the engine "
                             f"decides which model fits")

    def test_the_wizard_asks_for_administrator(self):
        self.assertIn("RequestExecutionLevel admin", self.nsi)

    def test_the_wizard_verifies_the_payload_before_installing(self):
        self.assertIn("PAYLOAD_SHA256", self.nsi)
        self.assertIn("PACKAGE VERIFICATION FAILED", self.nsi)

    def test_the_wizard_never_puts_a_secret_on_a_command_line(self):
        """The password reaches the engine through the environment."""
        self.assertIn("DESKPILOT_SETUP_ANSWERS", self.nsi)
        for line in self.nsi.splitlines():
            if "cli.py" in line and "nsExec" in line:
                self.assertNotIn("$WebPassword", line)
                self.assertNotIn("$AiApiKey", line)

    def test_the_wizard_clears_the_answers_after_installing(self):
        self.assertIn('SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t "")',
                      self.nsi)

    def test_the_wizard_never_touches_metatrader(self):
        """Detection strings are allowed; anything that acts on it is not."""
        lowered = self.nsi.lower()
        self.assertIn("will not be modified", lowered)
        for forbidden in ("taskkill", "/im terminal64", "stop-process",
                          "net stop"):
            self.assertNotIn(forbidden, lowered)

    def test_the_uninstaller_keeps_owner_data_by_default(self):
        self.assertIn("--purge-owner-data", self.nsi)
        self.assertIn('${NSD_SetState} $UnKeepRadio 1', self.nsi,
                      "the keep-my-data option must be preselected")


if __name__ == "__main__":
    unittest.main()


class EveryRefusalStatesItsReason(unittest.TestCase):
    """A fifth law, raised by the iFixAi advisory gate.

    Its structural criterion for an agent's authorization layer is that an
    unknown tool must come back denied *and* cite the rule it was denied
    under -- "denying with no reason is guessing, and it fails". The installer
    refuses things too: an unverified payload, a MetaTrader directory, an
    interpreter it will not adopt, owner details that did not arrive. §21
    already gives ``Failure`` five required fields, but a required field can
    hold an empty string, and a refusal whose ``why`` is blank is exactly the
    guess the criterion rules out.

    Checked over the syntax tree, so a reason assembled from an f-string or a
    variable passes and only an absent or placeholder one fails.
    """

    SHORTEST_USEFUL_REASON = 12

    def failures(self) -> list[tuple[str, int, dict]]:
        found = []
        for path in sorted(PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (getattr(node.func, "id", None)
                        or getattr(node.func, "attr", None))
                if name == "Failure":
                    found.append((path.name, node.lineno,
                                  {k.arg: k.value for k in node.keywords}))
        return found

    def test_the_installer_refuses_things_at_all(self):
        """A law about refusals is worthless if nothing ever refuses."""
        self.assertGreaterEqual(len(self.failures()), 8)

    def test_no_refusal_omits_what_failed_why_or_what_to_do(self):
        offenders = []
        for module, line, keywords in self.failures():
            for field in ("what_failed", "why", "safe_next_action"):
                if field not in keywords:
                    offenders.append(f"{module}:{line} omits {field}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_refusal_states_a_placeholder_reason(self):
        offenders = []
        for module, line, keywords in self.failures():
            for field in ("what_failed", "why", "safe_next_action"):
                value = keywords.get(field)
                if not isinstance(value, ast.Constant):
                    continue  # built from an f-string or a variable
                if not isinstance(value.value, str):
                    offenders.append(f"{module}:{line} {field} is not text")
                elif len(value.value.strip()) < self.SHORTEST_USEFUL_REASON:
                    offenders.append(
                        f"{module}:{line} {field}={value.value!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))


class TheProvenanceFileCannotFlatter(unittest.TestCase):
    """The written record outlives the terminal, so it must not be kinder.

    ``require_clean_engine`` printed a warning when the tree was dirty and
    returned nothing, and the provenance file beneath it went on stating
    "the working tree at <commit>, verified clean". The warning scrolled away;
    the file stayed, saying the opposite. A reader a week later has only the
    file.
    """

    def build_module(self):
        sys.path.insert(0, str(INSTALLER))
        import build
        return build

    def test_a_clean_tree_is_recorded_as_clean(self):
        text = self.build_module().engine_source("abc1234", "")
        self.assertIn("verified clean", text)
        self.assertNotIn("NOT FOR RELEASE", text)

    def test_a_dirty_tree_is_never_recorded_as_clean(self):
        text = self.build_module().engine_source(
            "abc1234", " M windows/installer/DeskPilot-Setup.nsi")
        self.assertNotIn("verified clean", text)
        self.assertIn("NOT FOR RELEASE", text)

    def test_a_dirty_tree_names_the_files(self):
        """"Something was dirty" does not let a reader judge the artifact."""
        text = self.build_module().engine_source(
            "abc1234", " M windows/installer/DeskPilot-Setup.nsi\n"
                       "?? windows/installer/scratch.txt")
        self.assertIn("DeskPilot-Setup.nsi", text)
        self.assertIn("scratch.txt", text)

    def test_the_check_reports_what_it_found(self):
        """A checker that returns nothing cannot be quoted by its caller."""
        import inspect
        source = inspect.getsource(
            self.build_module().require_clean_engine)
        self.assertIn("-> str", source,
                      "require_clean_engine does not hand back what it found")


class EachBuildKeepsItsOwnProvenance(unittest.TestCase):
    """A delivered installer's hash has to stay recoverable.

    The artifact register named the current installer's hash by pointing at
    ``dist/BUILD.txt``. ``dist/`` is not in the repository and every build
    overwrote that file, so the moment a later build ran, the register pointed
    at a file describing a different executable -- and because makensis embeds
    a build timestamp, rebuilding the same commit does not reproduce the lost
    hash. The question the register exists to answer ("was I given the one
    with the defect?") became unanswerable.
    """

    def build_module(self):
        sys.path.insert(0, str(INSTALLER))
        import build
        return build

    def test_a_build_writes_both_the_pointer_and_a_kept_copy(self):
        import tempfile
        build = self.build_module()
        with tempfile.TemporaryDirectory() as box:
            out = pathlib.Path(box)
            kept = build.write_provenance(out, "abc1234", "provenance text\n")
            self.assertTrue((out / "BUILD.txt").is_file())
            self.assertEqual(kept, out / "BUILD-abc1234.txt")
            self.assertTrue(kept.is_file())
            self.assertEqual(kept.read_text(), (out / "BUILD.txt").read_text())

    def test_a_later_build_does_not_destroy_an_earlier_record(self):
        import tempfile
        build = self.build_module()
        with tempfile.TemporaryDirectory() as box:
            out = pathlib.Path(box)
            first = build.write_provenance(out, "aaaaaaa", "the first build\n")
            build.write_provenance(out, "bbbbbbb", "the second build\n")
            self.assertEqual(first.read_text(), "the first build\n",
                             "the second build overwrote the first's record")
            self.assertEqual((out / "BUILD.txt").read_text(),
                             "the second build\n")
