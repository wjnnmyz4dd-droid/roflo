"""Every Windows scenario the installer has to survive, one test each.

The list is §23 of the installer contract, in order, with nothing dropped. Each
scenario builds a machine that differs from a working one in exactly one way
and asserts the specific thing the owner should get: an installation, or a
refusal that names the cause and says what was left alone.

What these tests are *not* is a substitute for running on Windows. They prove
the installer reaches the right decision when the machine reports a given
state, which is where the bugs of judgement live. They cannot prove that
``schtasks.exe`` accepts the XML, because nothing on this side of the seam
executes Windows. That division is deliberate and stated here so nobody reads
a green matrix as a green installation.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import (  # noqa: E402
    CERTIFIED_ENTRIES, FakeWindows, Handler, SYSTEM_PYTHON, certified_payload,
    fresh_windows, install_ready, pinned_to, zip_bytes, _bootstrap_bytes,
)

from deskpilot_installer import lifecycle, python_runtime  # noqa: E402
from deskpilot_installer.engine import (  # noqa: E402
    Components, Engine, InstallState, OwnerAnswers,
)
from deskpilot_installer.installlog import Log  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402
from deskpilot_installer.probe import Settings, system_check  # noqa: E402
from deskpilot_installer.report import Outcome, Secret  # noqa: E402

GB = 1024 ** 3
PACKAGE = r"C:\Media\DeskPilot-certified.zip"
VENV_PYTHON = r"C:\DeskPilot\venv\Scripts\python.exe"


def answers(**kwargs) -> OwnerAnswers:
    base = dict(owner_identity="Jo Owner", business_email="jo@example.com",
                web_password=Secret("correct-horse-battery"),
                ai_model_tag="qwen2.5:14b-instruct")
    base.update(kwargs)
    return OwnerAnswers(**base)


def settings(digest: str, **kwargs) -> Settings:
    return Settings(layout=Layout(), package_path=PACKAGE,
                    expected_digest=digest, **kwargs)


class Scenario(unittest.TestCase):
    """Shared helpers. Every scenario reports through one of these."""

    def install(self, host: FakeWindows, digest: str, *,
                owner: OwnerAnswers | None = None,
                setting: Settings | None = None):
        log = Log(host)
        engine = Engine(host, setting or settings(digest),
                        owner or answers(), log)
        return engine, engine.install(), log

    def assertInstalled(self, host, report):
        self.assertTrue(report.may_continue,
                        [f"{r.name}: {r.detail}" for r in report.blockers]
                        + ([report.failure.why] if report.failure else []))
        self.assertTrue(host.exists(Layout().db))

    def assertRefused(self, report, *, because: str):
        self.assertFalse(report.may_continue)
        haystack = " ".join([r.detail for r in report.rows]
                            + ([report.failure.why,
                                report.failure.what_failed]
                               if report.failure else [])).lower()
        self.assertIn(because.lower(), haystack)


class FreshAndPython(Scenario):

    def test_01_fresh_windows_host(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)

    def test_02_python_already_installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest, system_python=True)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        step = next(r for r in report.rows
                    if r.name == "Installing Python runtime")
        self.assertIn("reusing", step.detail)

    def test_03_python_missing_is_downloaded_and_verified(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest, system_python=False)
        with pinned_to(_bootstrap_bytes()):
            engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        self.assertTrue(any("curl.exe" in argv[0] for argv in host.commands))

    def test_04_a_tampered_python_download_is_refused(self):
        """The pin is not patched here, so the stand-in fails the comparison."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest, system_python=False)
        engine, report, _log = self.install(host, digest)
        self.assertRefused(report, because="rejected")
        self.assertFalse(host.exists(Layout().db))

    def test_04b_a_same_size_python_download_is_still_refused(self):
        """The substitution a length check cannot see.

        An attacker who can serve a different file can serve one of the right
        length. With the size pre-filter satisfied, only the digest comparison
        stands between the owner and an installer running arbitrary code as
        Administrator, so that comparison is pinned on its own here.
        """
        from tests_solvent.installer_fake_host import pinned_size_only
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest, system_python=False)
        with pinned_size_only(_bootstrap_bytes()):
            engine, report, _log = self.install(host, digest)
        self.assertFalse(report.may_continue)
        self.assertIn("does not match the digest", report.failure.why)
        self.assertEqual(
            [argv for argv in host.commands
             if argv[0].endswith("-amd64.exe")], [],
            "a download failing the digest must never be executed")

    def test_05_python_wrong_version_is_replaced_not_reused(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest, system_python=False)
        host.add_program("python.exe", r"C:\Python39\python.exe")
        host.on(r"C:\Python39\python.exe", "sys.version_info", stdout="3 9 True")
        found = python_runtime.discover(host)
        self.assertTrue(found)
        self.assertIsNone(python_runtime.choose(found))
        self.assertIn("3.11 or newer", found[0].rejected)

    def test_06_a_32_bit_python_is_refused(self):
        host = fresh_windows()
        host.add_program("python.exe", r"C:\Python313\python.exe")
        host.on(r"C:\Python313\python.exe", "sys.version_info",
                stdout="3 13 False")
        found = python_runtime.discover(host)
        self.assertIn("32-bit", found[0].rejected)
        self.assertIsNone(python_runtime.choose(found))

    def test_07_a_python_that_does_not_run_is_refused(self):
        host = fresh_windows()
        host.add_program("python.exe", r"C:\Python313\python.exe")
        host.on(r"C:\Python313\python.exe", "sys.version_info", returncode=1)
        found = python_runtime.discover(host)
        self.assertIn("did not run", found[0].rejected)


class ExistingInstallations(Scenario):

    def _installed(self, *, with_data=True):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        if with_data:
            host.add_file(Layout().db, b"SQLite format 3\x00BUSINESS-RECORDS")
        return host, digest

    def test_08_deskpilot_already_installed_is_detected(self):
        host, digest = self._installed()
        row = system_check(host, settings(digest)).row("Existing DeskPilot")
        self.assertIn("UPDATE AVAILABLE", row.detail)
        self.assertIn("preserved", row.detail)

    def test_09_upgrade_an_existing_installation(self):
        host, digest = self._installed()
        new = zip_bytes({**CERTIFIED_ENTRIES, "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new)
        report = lifecycle.upgrade(
            host, Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                           expected_digest=hashlib.sha256(new).hexdigest()),
            answers(), Log(host))
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00BUSINESS-RECORDS")
        self.assertTrue(host.exists(r"C:\DeskPilot\App\solvent\v2.py"))

    def test_10_repair_an_installation(self):
        host, digest = self._installed()
        host.remove_tree(r"C:\DeskPilot\App")
        report = lifecycle.repair(host, settings(digest), answers(), Log(host))
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        self.assertTrue(host.exists(r"C:\DeskPilot\App\solvent\cli.py"))
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00BUSINESS-RECORDS")

    def test_11_uninstall_preserving_data(self):
        host, digest = self._installed()
        report = lifecycle.uninstall(host, Layout(), log=Log(host))
        self.assertTrue(report.may_continue)
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00BUSINESS-RECORDS")
        self.assertFalse(host.exists(r"C:\DeskPilot\App"))
        kept = report.row("Data preservation")
        self.assertEqual(kept.outcome, Outcome.PASS)

    def test_12_a_reinstall_after_uninstall_finds_the_old_database(self):
        """The point of preserving it: the next installation reuses it."""
        host, digest = self._installed()
        lifecycle.uninstall(host, Layout(), log=Log(host))
        row = system_check(host, settings(digest)).row("Existing DeskPilot")
        self.assertIn("UPDATE AVAILABLE", row.detail)


class AiScenarios(Scenario):

    def test_13_ollama_installed_and_working(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        verified = engine.verify(probe_inference=True)
        self.assertEqual(verified.row("AI backend").outcome, Outcome.PASS)

    def test_14_ollama_missing_does_not_block_installation(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.path_programs.pop("ollama.exe", None)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        row = engine.verify(probe_inference=False).row("AI backend")
        self.assertEqual(row.outcome, Outcome.ACTION)
        self.assertIn("not installed", row.detail)

    def test_15_ollama_installed_but_unreachable(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.handlers.insert(0, Handler(("ollama.exe", "list"), 1, "",
                                        "could not connect"))
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        row = engine.verify(probe_inference=False).row("AI backend")
        self.assertEqual(row.outcome, Outcome.ACTION)
        self.assertIn("did not answer", row.detail)

    def test_16_the_model_is_missing(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.handlers.insert(0, Handler(("ollama.exe", "list"), 0,
                                        "NAME  ID  SIZE  MODIFIED\n", ""))
        engine, report, _log = self.install(host, digest)
        row = engine.verify(probe_inference=False).row("AI backend")
        self.assertIn("has not been downloaded", row.detail)

    def test_17_insufficient_memory_for_the_chosen_model(self):
        """4 GB with a trading terminal running: nothing local fits.

        MetaTrader is part of the scenario, not decoration. At 4 GB with
        nothing else running the smallest commercially licensed model does
        fit, and recommending it is the right answer; it is the terminal's
        reserve that takes the machine below what any local model can use. That
        is also the owner's real configuration, so it is the case worth pinning.
        """
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.ram_bytes = 4 * GB
        host.add_dir(r"C:\Program Files\MetaTrader 5")
        host.add_process("terminal64.exe", 4242)
        row = system_check(host, settings(digest)).row("AI model")
        self.assertEqual(row.outcome, Outcome.ACTION)
        self.assertIn("cloud", row.detail.lower())

    def test_17b_a_small_machine_without_metatrader_gets_the_small_model(self):
        """The companion case, so the test above cannot pass for the wrong reason."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.ram_bytes = 4 * GB
        row = system_check(host, settings(digest)).row("AI model")
        self.assertEqual(row.outcome, Outcome.PASS)
        self.assertIn("0.5B", row.detail)


class NetworkAndHostScenarios(Scenario):

    def test_18_a_port_conflict_on_the_control_centre_blocks(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.listen(8765, "someapp.exe")
        report = system_check(host, settings(digest))
        self.assertFalse(report.may_continue)
        self.assertIn("CONFLICT", report.row("Ports").detail)
        self.assertIn("someapp.exe", report.row("Ports").detail)

    def test_19_iis_on_80_and_443_is_a_warning_not_a_blocker(self):
        """DeskPilot's backend is loopback; a proxy owning 443 is normal."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.listen(80, "w3wp.exe").listen(443, "w3wp.exe")
        report = system_check(host, settings(digest, public_https=True))
        self.assertTrue(report.may_continue)
        self.assertEqual(report.row("Ports").outcome, Outcome.WARN)
        self.assertIn("w3wp.exe", report.row("Ports").detail)

    def test_20_no_domain_still_completes_a_local_installation(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        web = next(r for r in report.rows
                   if r.name == "Configuring web control centre")
        self.assertIn("loopback", web.detail)
        for row in report.rows:
            self.assertNotIn("https://", row.detail,
                             "the installer must not invent a public URL")

    def test_21_the_firewall_being_enabled_changes_nothing_else(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        netsh = [argv for argv in host.commands
                 if argv[0].lower().endswith("netsh.exe")]
        self.assertEqual(len(netsh), 1)
        self.assertIn("add", netsh[0])

    def test_22_a_path_with_spaces_is_handled(self):
        """Not by quoting: the argument list never becomes a string."""
        data, digest = certified_payload()
        root = r"D:\Program Files\DeskPilot Server"
        host = install_ready(package=data, digest=digest, root=root)
        host.free_overrides[r"d:\\"] = 200 * GB
        layout = Layout(root=root)
        setting = Settings(layout=layout, package_path=PACKAGE,
                           expected_digest=digest)
        log = Log(host)
        engine = Engine(host, setting, answers(), log)
        report = engine.install()
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        self.assertTrue(host.exists(layout.db))
        for argv in host.commands:
            for arg in argv:
                self.assertNotIn('"', arg,
                                 "a path must never arrive pre-quoted")

    def test_23_low_disk_space_blocks_before_anything_is_written(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.free_bytes_default = 1 * GB
        report = system_check(host, settings(digest))
        self.assertFalse(report.may_continue)
        self.assertEqual(report.row("Disk").outcome, Outcome.BLOCKED)

    def test_24_running_out_of_disk_during_installation_is_reported(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)

        def fill(h, _argv):
            h.disk_full = True

        # Injected on the command that *precedes* a write, and still answering
        # it correctly. Replacing a command whose output a later step needs
        # would fail the scenario as an unexpected command instead of as a
        # full disk.
        host.handlers.insert(0, Handler(
            (VENV_PYTHON, "site.getsitepackages"), 0,
            r"C:\DeskPilot\venv\Lib\site-packages", "", fill))
        engine, report, _log = self.install(host, digest)
        self.assertFalse(report.may_continue)
        self.assertIsNotNone(report.failure)
        self.assertIn("no space", report.failure.why.lower())

    def test_25_not_administrator_blocks_with_an_instruction(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.admin = False
        report = system_check(host, settings(digest))
        self.assertFalse(report.may_continue)
        self.assertIn("Run as administrator",
                      report.row("Administrator").detail)


class InterruptionScenarios(Scenario):

    def test_26_an_interrupted_installation_can_be_run_again(self):
        """Stop partway, then install again. The second attempt completes."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.handlers.insert(0, Handler((VENV_PYTHON, "setup", "capability"),
                                        1, "", "interrupted"))
        engine, first, _log = self.install(host, digest)
        self.assertFalse(first.may_continue)

        host.handlers.pop(0)
        engine2, second, _log2 = self.install(host, digest)
        self.assertTrue(second.may_continue,
                        [r.detail for r in second.blockers])

    def test_27_an_interrupted_upgrade_rolls_back(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        host.add_file(Layout().db, b"SQLite format 3\x00RECORDS")
        new = zip_bytes({**CERTIFIED_ENTRIES, "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new)
        host.handlers.insert(0, Handler((VENV_PYTHON, "solvent.cli", "doctor"),
                                        1, "", "interrupted"))
        result = lifecycle.upgrade(
            host, Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                           expected_digest=hashlib.sha256(new).hexdigest()),
            answers(), Log(host))
        self.assertFalse(result.may_continue)
        self.assertFalse(host.exists(r"C:\DeskPilot\App\solvent\v2.py"))
        self.assertTrue(host.exists(r"C:\DeskPilot\App\solvent\cli.py"))
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00RECORDS")

    def test_28_a_populated_database_is_never_replaced(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.add_file(Layout().db, b"SQLite format 3\x00YEARS-OF-RECORDS")
        engine, report, _log = self.install(host, digest)
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00YEARS-OF-RECORDS")

    def test_29_a_corrupt_database_is_reported_not_overwritten(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.add_file(Layout().db, b"this is not a database at all")
        host.handlers.insert(0, Handler((VENV_PYTHON, "solvent.cli", "doctor"),
                                        1, "", "file is not a database"))
        engine, report, _log = self.install(host, digest)
        self.assertFalse(report.may_continue)
        self.assertIn("not a database", report.failure.why)
        self.assertEqual(host.read_bytes(Layout().db),
                         b"this is not a database at all",
                         "a database that could not be opened must be left as "
                         "it is, not replaced")

    def test_30_a_reboot_is_survived_by_the_scheduled_task(self):
        """What persistence means here: the boot trigger is registered."""
        from deskpilot_installer import integration
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        created = [argv for argv in host.commands
                   if argv[0].lower().endswith("schtasks.exe")
                   and "/Create" in argv]
        self.assertEqual(len(created), 2)
        xml = host.read_bytes(
            integration.InstalledScripts.under(Layout()).task_xml
        ).decode("utf-8")
        self.assertIn("<BootTrigger>", xml)
        self.assertIn("S-1-5-18", xml)

    def test_31_a_crashed_process_is_restarted_by_the_task(self):
        from deskpilot_installer import integration
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        self.install(host, digest)
        xml = host.read_bytes(
            integration.InstalledScripts.under(Layout()).task_xml
        ).decode("utf-8")
        self.assertIn("<RestartOnFailure>", xml)
        self.assertIn("<Count>10</Count>", xml)

    def test_32_components_can_be_declined(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(
            host, digest,
            owner=answers(components=Components(autostart=False,
                                                backups=False)))
        self.assertInstalled(host, report)
        self.assertFalse(any("/Create" in argv for argv in host.commands))
        row = engine.verify(probe_inference=False).row("Autostart")
        self.assertEqual(row.outcome, Outcome.WARN)
        self.assertIn("will not start after a reboot", row.detail)


if __name__ == "__main__":
    unittest.main()
