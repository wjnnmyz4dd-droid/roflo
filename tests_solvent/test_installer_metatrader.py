"""MetaTrader is left alone, and that is proved rather than asserted.

The owner's trading terminal is the program on this machine with money
attached to it. DeskPilot has no relationship with it beyond sharing a
computer, so the installer's obligation is entirely negative: do not stop it,
do not restart it, do not read or write its files, do not touch its firewall
rules, do not adopt its Python, and do not install anywhere inside it.

A negative obligation is awkward to test by listing forbidden actions, because
the list is never complete -- the next way to disturb MetaTrader is the one
nobody thought of. So the central test here is a holdout: snapshot everything
about MetaTrader that a trader would care about, run a complete installation,
snapshot again, and require the two to be identical. That catches actions
nobody enumerated, which is the point.

The enumerated tests remain alongside it, because a holdout tells you
*something* changed and a named test tells you *what* is wrong.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import (  # noqa: E402
    FakeWindows, certified_payload, install_ready,
)

from deskpilot_installer import lifecycle, mt5  # noqa: E402
from deskpilot_installer.engine import Engine, OwnerAnswers  # noqa: E402
from deskpilot_installer.installlog import Log  # noqa: E402
from deskpilot_installer.layout import Layout, forbidden_root  # noqa: E402
from deskpilot_installer.probe import Settings, system_check  # noqa: E402
from deskpilot_installer.report import Outcome, Secret  # noqa: E402

PACKAGE_PATH = r"C:\Media\DeskPilot-certified.zip"
MT5_DIR = r"C:\Program Files\MetaTrader 5"
MT5_DATA = r"C:\Users\Admin\AppData\Roaming\MetaQuotes"


def trading_host(**kwargs) -> tuple[FakeWindows, str]:
    """A machine with MetaTrader installed, running, and holding real files."""
    data, digest = certified_payload()
    host = install_ready(package=data, digest=digest, **kwargs)
    host.add_file(MT5_DIR + r"\terminal64.exe", b"MZ-terminal", mtime=5000)
    host.add_file(MT5_DIR + r"\config\common.ini",
                  b"[Common]\nLogin=12345\n", mtime=5001)
    host.add_file(MT5_DATA + r"\MQL5\Experts\MyRobot.ex5", b"EA-bytes",
                  mtime=5002)
    host.add_file(MT5_DATA + r"\terminal.ini", b"[Settings]\n", mtime=5003)
    host.add_process("terminal64.exe", 4242, MT5_DIR + r"\terminal64.exe")
    # Replace the no-MetaTrader firewall listing registered by fresh_windows:
    # handlers match in registration order, so the earlier empty one would win.
    host.handlers = [h for h in host.handlers
                     if "show" not in h.must_contain]
    host.on("netsh.exe", "show", "rule",
            stdout="Rule Name:  MetaTrader 5 inbound\n"
                   "----------\n"
                   "Enabled:    Yes\n"
                   "Rule Name:  Core Networking\n")
    return host, digest


def answers() -> OwnerAnswers:
    return OwnerAnswers(owner_identity="Jo",
                        web_password=Secret("correct-horse-battery"),
                        ai_model_tag="qwen2.5:14b-instruct")


def settings(digest: str) -> Settings:
    return Settings(layout=Layout(), package_path=PACKAGE_PATH,
                    expected_digest=digest)


class TheHoldout(unittest.TestCase):
    """MetaTrader running before, during and after a complete installation."""

    def setUp(self):
        self.host, self.digest = trading_host()
        self.before = mt5.fingerprint(self.host)

    def test_the_fingerprint_captured_something_worth_comparing(self):
        """A holdout against an empty snapshot proves nothing."""
        self.assertTrue(self.before.processes)
        self.assertGreaterEqual(len(self.before.files), 4)
        self.assertIn("MetaTrader 5 inbound", self.before.firewall_rules)

    def test_a_complete_installation_changes_nothing_about_metatrader(self):
        report = Engine(self.host, settings(self.digest), answers(),
                        Log(self.host)).install()
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        after = mt5.fingerprint(self.host)
        self.assertEqual(self.before.differences(after), [])
        self.assertEqual(self.before, after)

    def test_metatrader_is_still_the_same_running_process(self):
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        running = [p for p in self.host.processes()
                   if p.name == "terminal64.exe"]
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0].pid, 4242,
                         "the same process, not a restarted one")

    def test_no_command_named_a_metatrader_process_or_path(self):
        """The enumerated half of the holdout: inspect the whole transcript."""
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        forbidden = ("terminal64", "terminal.exe", "metaeditor", "metatrader",
                     "metaquotes")
        offenders = []
        for argv in self.host.commands:
            joined = " ".join(argv).lower()
            # Reading the firewall rule list is allowed; it names MetaTrader in
            # its *output*, never in the command.
            for needle in forbidden:
                if needle in joined:
                    offenders.append(joined)
        self.assertEqual(offenders, [])

    def test_no_process_was_signalled(self):
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        for argv in self.host.commands:
            program = argv[0].lower()
            self.assertNotIn("taskkill", program)
            self.assertNotIn("tskill", program)
            for arg in argv:
                self.assertNotEqual(arg.lower(), "stop-process")

    def test_the_only_firewall_change_is_deskpilots_own_rule(self):
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        changes = [argv for argv in self.host.commands
                   if argv[0].lower().endswith("netsh.exe")
                   and any(a in ("add", "delete", "set", "reset")
                           for a in argv)]
        self.assertEqual(len(changes), 1,
                         f"expected exactly one firewall change, got {changes}")
        self.assertIn("name=DeskPilot control centre (loopback only)",
                      changes[0])
        self.assertIn("action=block", changes[0],
                      "the rule must not open anything")

    def test_the_firewall_policy_is_never_reset(self):
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        for argv in self.host.commands:
            joined = " ".join(argv).lower()
            self.assertNotIn("advfirewall reset", joined)
            self.assertNotIn("set allprofiles", joined)

    def test_an_upgrade_also_leaves_metatrader_alone(self):
        """The riskiest operation: it stops a service and moves files."""
        import hashlib
        from tests_solvent.installer_fake_host import (CERTIFIED_ENTRIES,
                                                       zip_bytes)
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        before = mt5.fingerprint(self.host)
        new = zip_bytes({**CERTIFIED_ENTRIES, "solvent/v2.py": b"# v2\n"})
        self.host.add_file(r"C:\Media\v2.zip", new)
        report = lifecycle.upgrade(
            self.host,
            Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                     expected_digest=hashlib.sha256(new).hexdigest()),
            answers(), Log(self.host))
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        self.assertEqual(before.differences(mt5.fingerprint(self.host)), [])

    def test_an_uninstall_also_leaves_metatrader_alone(self):
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        before = mt5.fingerprint(self.host)
        lifecycle.uninstall(self.host, Layout(), purge_owner_data=True,
                            log=Log(self.host))
        self.assertEqual(before.differences(mt5.fingerprint(self.host)), [])


class TheHoldoutCanFail(unittest.TestCase):
    """A holdout that cannot fail is decoration. These prove it detects.

    Each case mutates the machine the way a careless installer would and
    confirms the comparison notices. Without this, a fingerprint that silently
    captured nothing would make every holdout above pass.
    """

    def setUp(self):
        self.host, self.digest = trading_host()
        self.before = mt5.fingerprint(self.host)

    def test_a_restarted_terminal_is_detected(self):
        self.host.procs = [p for p in self.host.procs
                           if p.name != "terminal64.exe"]
        self.host.add_process("terminal64.exe", 9999)
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("restarted" in d for d in diffs), diffs)

    def test_a_stopped_terminal_is_detected(self):
        self.host.procs = []
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("is gone" in d for d in diffs), diffs)

    def test_a_modified_configuration_file_is_detected(self):
        self.host.add_file(MT5_DIR + r"\config\common.ini",
                           b"[Common]\nLogin=99999\n", mtime=5001)
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("was modified" in d for d in diffs), diffs)

    def test_a_touched_file_is_detected_even_with_identical_contents(self):
        """Same bytes, new mtime. A copy-over counts as a change."""
        self.host.add_file(MT5_DIR + r"\config\common.ini",
                           b"[Common]\nLogin=12345\n", mtime=6000)
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("was modified" in d for d in diffs), diffs)

    def test_a_deleted_expert_advisor_is_detected(self):
        del self.host.files[
            (MT5_DATA + r"\MQL5\Experts\MyRobot.ex5").lower()]
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("was deleted" in d for d in diffs), diffs)

    def test_an_added_file_is_detected(self):
        self.host.add_file(MT5_DATA + r"\MQL5\Experts\Injected.ex5", b"x")
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("was added" in d for d in diffs), diffs)

    def test_a_changed_firewall_rule_is_detected(self):
        self.host.handlers = [h for h in self.host.handlers
                              if "show" not in h.must_contain]
        self.host.on("netsh.exe", "show", "rule", stdout="Rule Name: Nothing\n")
        diffs = self.before.differences(mt5.fingerprint(self.host))
        self.assertTrue(any("firewall rules changed" in d for d in diffs), diffs)


class DetectionIsOnlyDetection(unittest.TestCase):

    def test_metatrader_present_does_not_block_installation(self):
        host, digest = trading_host()
        report = system_check(host, settings(digest))
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])

    def test_the_owner_is_told_it_is_protected(self):
        host, digest = trading_host()
        row = system_check(host, settings(digest)).row("Protected applications")
        self.assertEqual(row.outcome, Outcome.PASS)
        self.assertIn("PROTECTED", row.detail)
        self.assertIn("no changes will be made", row.detail)

    def test_a_genuine_port_conflict_stops_installation_rather_than_resolving_it(self):
        host, digest = trading_host()
        host.listen(8765, "terminal64.exe")
        report = system_check(host, settings(digest))
        self.assertFalse(report.may_continue)
        clash = report.row("MetaTrader coexistence")
        self.assertIsNotNone(clash)
        self.assertIn("will not reconfigure or restart MetaTrader",
                      clash.detail)

    def test_installing_inside_metatrader_is_refused(self):
        for candidate in (MT5_DIR, MT5_DIR + r"\MQL5",
                          r"C:\Users\Admin\AppData\Roaming\MetaQuotes\Terminal"):
            self.assertNotEqual(forbidden_root(candidate), "",
                                f"{candidate} must be refused")

    def test_metatraders_python_is_never_adopted(self):
        """Sharing an interpreter would mean sharing site-packages."""
        from deskpilot_installer import python_runtime
        host, digest = trading_host(system_python=False)
        host.add_program("python.exe", MT5_DIR + r"\Python\python.exe")
        found = python_runtime.discover(host)
        self.assertTrue(found)
        for interpreter in found:
            if "metatrader" in interpreter.path.lower():
                self.assertFalse(interpreter.usable)
                self.assertIn("MetaTrader", interpreter.rejected)
        self.assertIsNone(python_runtime.choose(found))

    def test_a_conflict_is_not_reported_when_another_program_holds_the_port(self):
        """Only MetaTrader triggers the coexistence refusal."""
        presence = mt5.Presence(installed=True, running=True)
        self.assertEqual(
            mt5.conflict(presence, (8765,), {8765: "w3wp.exe"}), "")

    def test_no_conflict_when_metatrader_is_absent(self):
        presence = mt5.Presence(installed=False, running=False)
        self.assertEqual(
            mt5.conflict(presence, (8765,), {8765: "terminal64.exe"}), "")


if __name__ == "__main__":
    unittest.main()
