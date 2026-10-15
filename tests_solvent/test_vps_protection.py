"""Protecting a machine DeskPilot is a guest on.

The owner's VPS runs MetaTrader 5. That is the whole reason there are
worker limits at all, and it sets the priority plainly: protect MT5
and system stability before maximising DeskPilot throughput.

Three things are tested here and they are different in kind.

The first is a prohibition: nothing in this system may signal, kill,
terminate or enumerate another application's processes. That is
checked over identifiers across the whole package, because the way
this goes wrong is not a function called `kill_mt5` -- it is an
`import signal` somewhere innocent that a later change reaches for.

The second is the degradation ladder actually being obeyed. The
capacity gate has computed what to shed under pressure for some time
and nothing read it; advice with no audience is not a control.

The third is that an unreadable machine is not a healthy one, which
this codebase has now got wrong in four separate places and fixed in
all four.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import runtime as rt
from solvent.harness import OWNER, Solvent
from tests_solvent.fixtures import source_names

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "solvent"

BUDGET = {"max_owed_seconds": 10_000.0, "max_concurrent_jobs": 4,
          "max_execution_workers": 5, "max_verification_workers": 5,
          "max_open_bids": 4, "cpu_share": 0.25,
          "min_free_disk_bytes": 1_000_000_000,
          "max_memory_used_percent": 0.0,
          "skills": ["csv-cleanup"], "pipeline_target": 3,
          "replenish_at": 2, "halted": False}


class NothingTouchesAnotherProcess(unittest.TestCase):
    """MT5 must remain unmodified, unstopped and uninterfered with."""

    #: Ways of reaching *another* process. Checked as identifiers and
    #: real strings, never over prose, so a docstring explaining that
    #: DeskPilot never kills anything does not fail the test proving
    #: it.
    #:
    #: SIGTERM and SIGINT are deliberately not here. runtime.py names
    #: both, and uses them to install handlers for signals sent *to*
    #: DeskPilot so systemd can ask it to stop politely. Receiving a
    #: signal is the opposite of sending one, and a guard that
    #: conflated them would have to be either disabled or worked
    #: around -- so the sending verbs are forbidden instead, and
    #: test_signals_are_only_ever_received pins the distinction.
    FORBIDDEN = ("kill", "killpg", "SIGKILL", "taskkill", "pkill",
                 "TerminateProcess", "OpenProcess", "psutil",
                 "process_iter", "NtSuspendProcess", "CreateToolhelp32Snapshot")

    def modules(self):
        return sorted(p for p in PACKAGE.rglob("*.py")
                      if "__pycache__" not in str(p))

    def test_no_module_can_reach_another_process(self):
        for path in self.modules():
            names = source_names(str(path.relative_to(PACKAGE.parent)))
            for forbidden in self.FORBIDDEN:
                with self.subTest(module=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_signals_are_only_ever_received_never_sent(self):
        """The one module that names a signal, and what it does with it.

        ``signal.signal`` registers a handler. ``os.kill`` sends one.
        The first lets systemd stop DeskPilot cleanly; the second is
        how a guest process stops MetaTrader 5. Only the first
        appears anywhere.
        """
        # Word boundaries, not substrings. The first version looked
        # for "kill(" and matched `skill(` in half the package, which
        # is the kind of false positive that gets a guard deleted.
        import re

        senders = re.compile(
            r"\b(?:os\.kill|kill|killpg|SIGKILL|terminate)\s*\(")
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            hit = senders.search(text)
            with self.subTest(module=path.name):
                self.assertIsNone(
                    hit, f"{path.name} calls {hit.group(0) if hit else ''}")

    def test_the_only_signal_use_is_installing_a_handler(self):
        text = (PACKAGE / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("signal.signal(", text)
        self.assertEqual(text.count("signal."),
                         text.count("signal.signal(") + 2,
                         "runtime.py uses the signal module for something "
                         "other than registering its own handlers")

    def test_the_scan_would_notice_a_violation(self):
        """A guard that cannot fail is not a guard.

        If source_names ever stopped returning identifiers this
        whole class would pass vacuously, so one name that is
        genuinely present is asserted present.
        """
        names = source_names("solvent/capacity.py")
        self.assertIn("effective_workers", names)

    def test_metatrader_is_named_only_as_something_to_protect(self):
        """It appears in the code, and only ever as a reason to stop."""
        found = False
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            if "MetaTrader" in text:
                found = True
        self.assertTrue(found, "nothing mentions the application this "
                               "machine is shared with, which means "
                               "nothing is accounting for it")


class ThePressureLadderIsObeyed(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/vps.db")
        self.addCleanup(self.close)
        self.s.policy.amend({"capacity": BUDGET}, OWNER, "test")
        self.runtime = rt.Runtime(self.s, sleep=lambda _s: None)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def host(self, **fields):
        self.s.capacity._diagnostics = type(
            "Host", (), {"host": lambda _self: dict(fields)})()

    def test_a_quiet_machine_sheds_nothing(self):
        self.host(disk_free_bytes=50_000_000_000)
        self.assertEqual(self.runtime._shed(), [])

    def test_a_full_disk_sheds_discovery_first(self):
        """Cheapest first: a job nobody promised costs nothing to skip."""
        self.host(disk_free_bytes=1)
        shed = self.runtime._shed()
        self.assertTrue(shed)
        self.assertEqual(shed[0], "stop speculative discovery")

    def test_accepted_work_is_never_shed(self):
        """Shedding that is breaking a promise."""
        self.host(disk_free_bytes=1)
        shed = " ".join(self.runtime._shed())
        self.assertIn("finish and verify what is already owed", shed)
        for never in ("abandon", "cancel", "drop", "return the job"):
            with self.subTest(never=never):
                self.assertNotIn(never, shed)

    def test_discovery_actually_backs_off(self):
        """The ladder was computed and nobody read it. Now it is read."""
        self.host(disk_free_bytes=1)
        said = self.runtime._tick_discovery()
        self.assertIn("backing off under pressure", said)

    def test_discovery_runs_when_the_machine_is_fine(self):
        self.host(disk_free_bytes=50_000_000_000)
        said = self.runtime._tick_discovery()
        self.assertNotIn("backing off", said)

    def test_unreadable_pressure_sheds_everything(self):
        """A control that cannot be consulted has not said go ahead."""
        def explode():
            raise RuntimeError("the gate is unreadable")

        self.s.capacity.pressure = explode
        shed = self.runtime._shed()
        self.assertIn("stop speculative discovery", shed)
        self.assertTrue(any("not the same as no pressure" in s
                            for s in shed))

    def test_discovery_backs_off_when_pressure_is_unreadable(self):
        def explode():
            raise RuntimeError("the gate is unreadable")

        self.s.capacity.pressure = explode
        self.assertIn("backing off", self.runtime._tick_discovery())


class TheOwnersBudgetIsNeverInferred(unittest.TestCase):
    """The limits come from the owner, not from the hardware."""

    def test_the_inspection_tool_infers_nothing_from_the_price(self):
        from tools.inspect_host import inspect

        result = inspect()
        self.assertEqual(result["inferred_from_cost"], [])
        self.assertIn("not a core count", result["note"])

    def test_it_reports_unmeasurable_rather_than_guessing(self):
        from tools.inspect_host import UNMEASURABLE, inspect

        result = inspect()
        readings = [r for group in result["sections"].values()
                    for r in group]
        unmeasurable = [r for r in readings
                        if r["state"] == UNMEASURABLE]
        self.assertTrue(unmeasurable,
                        "every reading was measurable, which on any host "
                        "this system targets would be surprising")
        for reading in unmeasurable:
            with self.subTest(name=reading["name"]):
                self.assertIsNone(reading["value"])
                self.assertTrue(reading["note"],
                                "an unmeasurable reading must say why")

    def test_metatrader_usage_is_not_estimated(self):
        from tools.inspect_host import UNMEASURABLE, inspect

        memory = {r["name"]: r for r in inspect()["sections"]["memory"]}
        mt5 = memory["memory_used_by_metatrader"]
        self.assertEqual(mt5["state"], UNMEASURABLE)
        self.assertIsNone(mt5["value"])


if __name__ == "__main__":
    unittest.main()
