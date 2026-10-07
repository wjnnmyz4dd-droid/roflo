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


class TheWizardDelegatesToTheEngine(unittest.TestCase):
    """The native script must not grow its own copy of a decision."""

    def setUp(self):
        self.nsi = (INSTALLER / "DeskPilot-Setup.nsi").read_text()

    def test_the_wizard_exists_and_is_built_from_the_engine(self):
        self.assertIn("deskpilot_installer\\cli.py", self.nsi)

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
