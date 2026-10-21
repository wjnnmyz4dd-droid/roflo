"""Provisioning Python on a real Windows machine, not a simulated one.

Three failures that the existing suite could not see, because they are
properties of how Windows and the python.org installer actually behave
rather than of the installer's own logic:

* the installer returns 3010 -- "installed, Windows wants a restart" --
  and a plain ``returncode == 0`` test reads that as failure, aborting an
  installation where Python is now present and working;
* ``/quiet`` means the installer writes nothing worth printing, so an
  error message built from its output is an error message with no
  content, and the exit code that *is* the signal was logged but never
  shown;
* the pinned installer is the amd64 build, and nothing checked whether
  the machine was ARM64 before running it.

None of these is reachable through DeskPilot's business logic, and none
of them is touched here. Everything below is in ``windows/installer``.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from deskpilot_installer import engine as engine_module  # noqa: E402
from deskpilot_installer import python_runtime  # noqa: E402
from deskpilot_installer.engine import Engine, OwnerAnswers  # noqa: E402
from deskpilot_installer.installlog import Log  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402
from deskpilot_installer.probe import Settings  # noqa: E402
from deskpilot_installer.report import Secret  # noqa: E402
from tests_solvent.installer_fake_host import (Handler,  # noqa: E402
                                               _bootstrap_bytes,
                                               certified_payload,
                                               install_ready, pinned_to)

PACKAGE = r"C:\Media\DeskPilot-certified.zip"


def answers() -> OwnerAnswers:
    return OwnerAnswers(owner_identity="Jo",
                        web_password=Secret("correct-horse-battery"),
                        ai_model_tag="qwen2.5:14b-instruct")


class TheExitCodeIsTheSignal(unittest.TestCase):
    """`/quiet` writes nothing, so the code has to carry the message."""

    def test_a_reboot_required_install_is_a_success(self):
        """3010 is ERROR_SUCCESS_REBOOT_REQUIRED. Python is installed."""
        self.assertIn(python_runtime.REBOOT_REQUIRED_CODE,
                      python_runtime.BOOTSTRAP_OK_CODES)
        self.assertIn(0, python_runtime.BOOTSTRAP_OK_CODES)

    def test_a_cancelled_install_says_so_and_says_what_to_do(self):
        what, remedy = python_runtime.bootstrap_exit_explanation(1602)
        self.assertIn("cancelled", what)
        self.assertIn("elevation", remedy)

    def test_another_installation_in_progress_is_named(self):
        what, remedy = python_runtime.bootstrap_exit_explanation(1618)
        self.assertIn("already running", what)
        self.assertIn("Windows Update", remedy)

    def test_access_denied_points_at_elevation_not_at_python(self):
        what, remedy = python_runtime.bootstrap_exit_explanation(5)
        self.assertIn("refused access", what)
        self.assertIn("Administrator", remedy)

    def test_an_unknown_code_still_gets_a_way_forward(self):
        """The code itself is printed by the caller, once. See the engine
        test below for an unrecognised code reaching the owner."""
        what, remedy = python_runtime.bootstrap_exit_explanation(31337)
        self.assertTrue(what)
        self.assertIn("by hand", remedy)

    def test_every_explanation_gives_an_action_not_just_a_diagnosis(self):
        for code in python_runtime.BOOTSTRAP_EXIT_MEANINGS:
            with self.subTest(code=code):
                what, remedy = python_runtime.bootstrap_exit_explanation(code)
                self.assertTrue(what and remedy)
                self.assertTrue(remedy.rstrip().endswith("."),
                                "a remedy is an instruction, not a fragment")

    def test_a_success_code_has_no_explanation_entry(self):
        """Explaining a success as a failure is its own kind of wrong."""
        for code in python_runtime.BOOTSTRAP_OK_CODES:
            with self.subTest(code=code):
                self.assertNotIn(code, python_runtime.BOOTSTRAP_EXIT_MEANINGS)


class ArmMachinesAreToldRatherThanGuessedAt(unittest.TestCase):
    """The pinned installer is amd64. Saying so beats running it anyway."""

    def setUp(self):
        data, self.digest = certified_payload()
        self.host = install_ready(package=data, digest=self.digest)

    def test_an_amd64_machine_is_supported(self):
        self.host.env["PROCESSOR_ARCHITECTURE"] = "AMD64"
        self.assertEqual(python_runtime.bootstrap_supported(self.host), "")

    def test_an_arm64_machine_is_refused_with_its_architecture_named(self):
        self.host.env["PROCESSOR_ARCHITECTURE"] = "ARM64"
        why = python_runtime.bootstrap_supported(self.host)
        self.assertIn("ARM64", why)
        self.assertIn("amd64", why)

    def test_a_32_bit_process_on_an_arm64_machine_is_still_arm64(self):
        """PROCESSOR_ARCHITECTURE lies under emulation; ARCHITEW6432 does not."""
        self.host.env["PROCESSOR_ARCHITECTURE"] = "x86"
        self.host.env["PROCESSOR_ARCHITEW6432"] = "ARM64"
        self.assertIn("ARM64", python_runtime.bootstrap_supported(self.host))

    def test_an_unknown_architecture_is_not_refused(self):
        """Refusing what we cannot identify would block ordinary machines."""
        self.host.env.pop("PROCESSOR_ARCHITECTURE", None)
        self.host.env.pop("PROCESSOR_ARCHITEW6432", None)
        self.assertEqual(python_runtime.bootstrap_supported(self.host), "")


class TheBootstrapRunsThroughTheEngine(unittest.TestCase):
    """End to end through `step_python`, which is where it actually matters."""

    def setUp(self):
        data, self.digest = certified_payload()
        self.host = install_ready(package=data, digest=self.digest,
                                  system_python=False)
        self.host.env["PROCESSOR_ARCHITECTURE"] = "AMD64"
        self.settings = Settings(layout=Layout(), package_path=PACKAGE,
                                 expected_digest=self.digest)

    def _engine(self):
        return Engine(self.host, self.settings, answers(), Log(self.host))

    def _say_the_installer_returns(self, code, stderr=""):
        def arrived(h, argv):
            h.add_file(argv[argv.index("-o") + 1], _bootstrap_bytes())

        self.host.handlers.insert(0, Handler(("curl.exe",), 0, "", "",
                                             arrived, None))
        self.host.handlers.insert(0, Handler(("-amd64.exe", "/quiet"), code,
                                             "", stderr))

    def test_an_arm64_machine_stops_before_downloading_anything(self):
        self.host.env["PROCESSOR_ARCHITECTURE"] = "ARM64"
        engine = self._engine()
        with self.assertRaises(engine_module.StepFailed) as caught:
            engine.step_python()
        self.assertIn("ARM64", str(caught.exception))
        self.assertFalse(
            any("curl.exe" in " ".join(c) for c in self.host.commands),
            "nothing should be downloaded for a machine we cannot serve")

    def test_a_download_that_is_not_the_pinned_bytes_is_never_run(self):
        """The gate in front of the exit code, and it fires first.

        No `pinned_to` here, so the real digest check runs against bytes
        that are not the real installer -- which is the rejection path.
        """
        self._say_the_installer_returns(1618)
        engine = self._engine()
        with self.assertRaises(engine_module.StepFailed) as caught:
            engine.step_python()
        message = str(caught.exception)
        self.assertIn("rejected", message)
        self.assertIn("bytes", message)
        self.assertFalse(
            any("/quiet" in " ".join(c) for c in self.host.commands),
            "an installer that failed its digest must not be executed")

    def test_a_failing_install_names_its_code_and_a_remedy(self):
        """With the 29MB download stubbed, so the exit code is what is tested.

        `pinned_to` aims the pin at the fixture bytes rather than
        stubbing the check, so `verify_download` still runs its real
        compare_digest over a real SHA-256.
        """
        self._say_the_installer_returns(1618, stderr="0x80070643")
        engine = self._engine()
        with pinned_to(_bootstrap_bytes()):
            with self.assertRaises(engine_module.StepFailed) as caught:
                engine.step_python()
        message = str(caught.exception)
        self.assertIn("1618", message)
        self.assertIn("0x80070643", message,
                      "whatever the installer did say must survive")
        self.assertIn("already running", message)
        self.assertNotIn("reported failure: ", message,
                         "the old message ended in an empty quiet-mode output")

    def test_an_unrecognised_code_still_reaches_the_owner_as_a_number(self):
        self._say_the_installer_returns(31337)
        engine = self._engine()
        with pinned_to(_bootstrap_bytes()):
            with self.assertRaises(engine_module.StepFailed) as caught:
                engine.step_python()
        self.assertIn("31337", str(caught.exception))

    def test_a_reboot_required_install_continues_instead_of_aborting(self):
        """3010 used to abort an installation where Python was installed."""
        self._say_the_installer_returns(python_runtime.REBOOT_REQUIRED_CODE)
        engine = self._engine()
        with pinned_to(_bootstrap_bytes()):
            # The post-install search is what decides the step; a machine
            # that still reports no Python fails for that reason, not for
            # the exit code, and the message says which.
            with self.assertRaises(engine_module.StepFailed) as caught:
                engine.step_python()
        message = str(caught.exception)
        self.assertIn("could not then be found", message)
        self.assertNotIn("3010", message,
                         "3010 is a success code and must not be reported "
                         "as the reason anything failed")
