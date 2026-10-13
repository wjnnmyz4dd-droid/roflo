"""Can this machine produce a second opinion, and does it say so in advance?

Blocker 7 from the previous report: `openpyxl` and `python-docx` appear in
neither the project's declared dependencies nor the Windows payload, so on a
fresh Windows install there is no independent reader, no T2, and every job
above the high-value threshold stops.

The fix is three things, and none of them is "pretend".

* **Declare it.** A `verification` extra, so an install can provision the
  readers deliberately.
* **Probe it.** The installer's self-check reports what the target machine
  actually has. It installs nothing and claims nothing it has not observed.
* **Surface it.** A readiness check says so before the owner goes looking
  for four-figure work, rather than at delivery with a client waiting.

The installer itself stays standard-library only. A reader it needs would
be a dependency it must not have.
"""

from __future__ import annotations

import builtins
import contextlib
import importlib
import sys
import tempfile
import unittest

from solvent import independent
from solvent.harness import OWNER, Solvent


@contextlib.contextmanager
def readers_missing(*names):
    """Make named imports fail, as they would on a fresh Windows install."""
    blocked = set(names)
    real_import = builtins.__import__
    saved = {n: sys.modules.pop(n, None) for n in blocked}

    def fake_import(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in blocked:
            raise ModuleNotFoundError(f"No module named {root!r}")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        yield
    finally:
        builtins.__import__ = real_import
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


class TheProjectDeclaresWhatItNeeds(unittest.TestCase):

    def pyproject(self) -> str:
        import pathlib

        return pathlib.Path("pyproject.toml").read_text(encoding="utf-8")

    def test_the_readers_are_a_declared_extra(self):
        text = self.pyproject()
        self.assertIn("verification = [", text)
        self.assertIn("openpyxl", text)
        self.assertIn("python-docx", text)

    def test_solvent_is_actually_packaged(self):
        """The console script pointed into a package setuptools excluded."""
        text = self.pyproject()
        self.assertIn('solvent = "solvent.cli:main"', text)
        self.assertIn("solvent*", text,
                      "the `solvent` command is declared but its package is "
                      "not included, so the installed command cannot import "
                      "its own module")

    def test_the_readers_are_not_required_at_import_time(self):
        """Solvent must install and run without them."""
        with readers_missing("openpyxl", "docx"):
            importlib.reload(independent)
            report = independent.available(extension="xlsx")
            self.assertFalse(report.automated)
        importlib.reload(independent)


class TheInstallerProbesWithoutInstalling(unittest.TestCase):

    def selfcheck(self):
        import pathlib

        root = str(pathlib.Path("windows/installer").resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        from deskpilot_installer import selfcheck

        return selfcheck

    def test_it_reports_available_when_the_readers_are_there(self):
        module = self.selfcheck()
        row = module._verification_row()
        self.assertEqual(row.outcome.name, "PASS")
        self.assertIn("independent readers are available", row.detail)

    def test_it_warns_rather_than_blocks_when_they_are_missing(self):
        """DeskPilot installs and runs fine without them, so this is a WARN.

        Failing the install would be worse than the problem: jobs below the
        threshold need no second opinion at all.
        """
        module = self.selfcheck()
        with readers_missing("openpyxl", "docx"):
            row = module._verification_row()
        self.assertEqual(row.outcome.name, "WARN")
        self.assertNotEqual(row.outcome.name, "FAIL")
        self.assertIn("declined", row.detail)
        self.assertIn("verification", row.detail)
        self.assertEqual(len(row.facts["missing"]), 2)

    def test_it_names_which_reader_is_missing(self):
        module = self.selfcheck()
        with readers_missing("docx"):
            row = module._verification_row()
        self.assertEqual(row.outcome.name, "WARN")
        missing = " ".join(row.facts["missing"])
        self.assertIn("docx", missing)
        self.assertNotIn("openpyxl", missing)

    def test_the_installer_still_depends_on_nothing(self):
        """The property that makes the probe a probe and not a dependency."""
        module = self.selfcheck()
        for name, _why in module.REQUIRED_STDLIB:
            with self.subTest(module=name):
                self.assertIn(name, sys.stdlib_module_names)
        for name, _what in module.VERIFICATION_READERS:
            with self.subTest(reader=name):
                self.assertNotIn(
                    name, [m for m, _ in module.REQUIRED_MODULES],
                    "a third-party reader has been made an installer "
                    "requirement; the installer must import nothing it does "
                    "not ship")


class ReadinessSaysSoBeforeAJobIsTaken(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/ready.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def checks(self):
        from solvent.readiness import first_revenue_readiness

        report = first_revenue_readiness(
            policy=self.s.policy, ledger=self.s.ledger,
            capability=self.s.capability, discovery=self.s.discovery,
            owner_channel=self.s.owner)
        return {c.name: c for c in report.checks}

    def test_the_check_exists_and_passes_here(self):
        check = self.checks().get("independent review is producible")
        self.assertIsNotNone(check, "no readiness check covers T2")
        self.assertTrue(check.ready, check.detail)

    def test_it_fails_and_explains_when_nothing_can_review(self):
        with readers_missing("openpyxl", "docx"):
            importlib.reload(independent)
            check = self.checks().get("independent review is producible")
        importlib.reload(independent)
        self.assertIsNotNone(check)
        self.assertFalse(check.ready)
        self.assertIn("OD-18", check.owner_decision)
        self.assertIn("declined", check.owner_decision)

    def test_a_supervised_trial_is_not_blocked_by_it(self):
        """A trial produces one file for the owner at a value they choose."""
        from solvent.readiness import _NOT_USED_BY_A_SUPERVISED_TRIAL

        self.assertIn("independent review is producible",
                      _NOT_USED_BY_A_SUPERVISED_TRIAL)


if __name__ == "__main__":
    unittest.main()
