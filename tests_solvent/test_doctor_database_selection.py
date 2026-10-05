"""§60. `solvent doctor` must report the database it was told to report on.

Written after the Windows deployment review found that `doctor` had no `--db`
at all, and after reproducing it found the reason it mattered: `cmd_doctor`
opened ``Solvent()`` with no path, which is ``:memory:``. Every run built an
empty DeskPilot and printed its governance and evidence as the deployment's.
The output looked right because a fresh database seeds the same safe defaults,
which is what made it dangerous rather than obvious.

So the tests here are not about parsing. Each one gives two databases
deliberately different diagnostic state and insists the output belong to the
one that was asked for. A test that only proved the flag was accepted would
have passed against the broken version.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from solvent import runtime as rt
from solvent.cli import main
from solvent.harness import OWNER, Solvent
from solvent.types import OperatingMode

#: Three distinguishable postures. No two share a margin floor, so a line of
#: doctor's output names exactly one database.
HALTED = (OperatingMode.HALT, 0.55)
PAUSED = (OperatingMode.PAUSE_SPEND, 0.42)
DECOY = (OperatingMode.SAFE_MODE, 0.37)


def provision(path: str, posture) -> str:
    """A real DeskPilot database at ``path``, carrying a posture we can name."""
    mode, floor = posture
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    solvent = Solvent(path)
    try:
        solvent.policy.amend({"financial": {"margin_floor": floor}}, OWNER,
                             "distinguishable state for a doctor test")
        solvent.policy.set_operating_mode(mode, OWNER, "likewise")
    finally:
        solvent.store.close()
    return path


def doctor(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        with contextlib.redirect_stderr(buffer):
            code = main(["doctor", *argv])
    return code, buffer.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)
        # A decoy at the compiled-in default. Nothing here may report it, and
        # pointing the default at a file we control means the test does not
        # depend on whether this host happens to have /var/lib/solvent.
        self.decoy = provision(str(self.root / "decoy" / "solvent.db"), DECOY)
        patcher = mock.patch.object(rt, "DEFAULT_DB", self.decoy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, *parts) -> str:
        return str(self.root.joinpath(*parts))


class DoctorReadsTheDatabaseItWasGiven(Base):
    """§6. The positive canary. Without this the refusals below prove nothing
    -- they would all pass against a doctor that never opened anything."""

    def test_it_reports_the_posture_of_the_named_database(self):
        db = provision(self.path("a", "solvent.db"), HALTED)
        code, out = doctor("--db", db)
        self.assertEqual(code, 0, out)
        self.assertIn("HALT", out)
        self.assertIn("0.55", out)

    def test_it_reports_a_different_database_differently(self):
        a = provision(self.path("a", "solvent.db"), HALTED)
        b = provision(self.path("b", "solvent.db"), PAUSED)
        _, first = doctor("--db", a)
        _, second = doctor("--db", b)
        self.assertIn("0.55", first)
        self.assertIn("0.42", second)
        self.assertNotEqual(first, second,
                            "two different databases produced one report")


class DoctorDoesNotReadSomeOtherDatabase(Base):
    """§7. The wrong-database canary: an argument that parses and is then
    ignored is the failure this guards. The decoy sits at DEFAULT_DB, so a
    doctor that quietly falls back to it is caught by its margin floor."""

    def test_the_named_database_is_read_and_the_default_is_not(self):
        db = provision(self.path("chosen", "solvent.db"), HALTED)
        code, out = doctor("--db", db)
        self.assertEqual(code, 0, out)
        self.assertIn("0.55", out)
        self.assertNotIn("0.37", out,
                         "doctor reported the default database instead")
        self.assertNotIn("SAFE_MODE", out)

    def test_a_third_database_does_not_leak_in(self):
        chosen = provision(self.path("chosen", "solvent.db"), HALTED)
        other = provision(self.path("other", "solvent.db"), PAUSED)
        code, out = doctor("--db", chosen)
        self.assertEqual(code, 0, out)
        self.assertIn("0.55", out)
        for stranger in ("0.42", "PAUSE_SPEND", "0.37", "SAFE_MODE"):
            self.assertNotIn(stranger, out, f"{stranger} came from elsewhere")
        self.assertTrue(pathlib.Path(other).exists())

    def test_the_default_is_still_the_default_when_nothing_is_named(self):
        """§8. The other half: omitting --db must reach the deployment
        database, which is the whole reason the in-memory default was wrong."""
        code, out = doctor()
        self.assertEqual(code, 0, out)
        self.assertIn("0.37", out)
        self.assertIn("SAFE_MODE", out)


class WindowsStylePathsSurviveTheCli(Base):
    """§5. The runner here is Linux, so these are not Windows paths -- they are
    byte sequences a Windows owner would type, used as literal filenames.
    Backslashes, a drive-letter colon and spaces are all legal in a POSIX
    filename, which makes the mangling question answerable deterministically on
    either platform: whatever was typed must arrive intact.
    """

    WINDOWS_SHAPED = (
        r"C:\DeskPilot\Data\solvent.db",
        r"C:\Program Data\DeskPilot\solvent.db",
        r"C:\DeskPilot\Data Files\solvent.db",
    )

    def test_each_windows_shaped_name_is_opened_as_given(self):
        for name in self.WINDOWS_SHAPED:
            with self.subTest(name=name):
                db = provision(str(self.root / name), HALTED)
                code, out = doctor("--db", db)
                self.assertEqual(code, 0, out)
                self.assertIn("0.55", out)
                self.assertNotIn("0.37", out)

    def test_the_path_reaches_the_store_unmodified(self):
        """The property that holds on both platforms: the CLI is a conduit.
        Asserted against the string the store is actually constructed with,
        because an output assertion cannot see a path that was normalised on
        the way through.
        """
        for name in self.WINDOWS_SHAPED:
            with self.subTest(name=name):
                db = provision(str(self.root / name), HALTED)
                seen = []
                real = Solvent.__init__

                def spy(self, path=":memory:", *, _real=real, _seen=seen):
                    _seen.append(path)
                    _real(self, path)

                with mock.patch.object(Solvent, "__init__", spy):
                    code, out = doctor("--db", db)
                self.assertEqual(code, 0, out)
                self.assertEqual(seen, [db],
                                 f"the path changed on its way through: {seen}")
                self.assertIn("\\", seen[0])


class AnExplicitDatabaseFailsHonestly(Base):
    """§9. Having asked for one database, doctor may not quietly report
    another -- nor invent an empty one and report that."""

    def test_a_path_that_does_not_exist_is_refused(self):
        missing = self.path("nowhere", "solvent.db")
        code, out = doctor("--db", missing)
        self.assertEqual(code, 1)
        self.assertIn("no database at", out)
        self.assertFalse(pathlib.Path(missing).exists(),
                         "doctor created the database it was asked about")

    def test_the_refusal_does_not_fall_back_to_the_default(self):
        code, out = doctor("--db", self.path("nowhere", "solvent.db"))
        self.assertEqual(code, 1)
        self.assertNotIn("0.37", out, "it reported the default anyway")
        self.assertNotIn("FAIL-CLOSED", out,
                         "it produced a posture report for a missing database")

    def test_a_windows_path_that_does_not_exist_is_refused_too(self):
        code, out = doctor("--db", str(self.root / r"C:\Typo\solvent.db"))
        self.assertEqual(code, 1)
        self.assertIn("no database at", out)

    def test_an_unreadable_database_is_explained_not_traced(self):
        broken = self.path("broken", "solvent.db")
        pathlib.Path(broken).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(broken).write_bytes(b"this is not a database")
        code, out = doctor("--db", broken)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out)

    def test_the_memory_sentinel_is_still_allowed(self):
        """`:memory:` is sqlite's own word for a throwaway, not a path that is
        missing. Asking for one explicitly is a choice, not a typo."""
        code, out = doctor("--db", ":memory:")
        self.assertEqual(code, 0, out)
        self.assertIn("FAIL-CLOSED", out)
        self.assertNotIn("0.37", out, "the sentinel reached the default")


class DoctorRemainsTheOnlyDoctor(Base):
    """§10. The fix must not have produced a second implementation."""

    def test_there_is_one_doctor_command_and_one_implementation(self):
        import inspect
        import re

        from solvent import cli as cli_module

        source = inspect.getsource(cli_module)
        self.assertEqual(len(re.findall(r"^def cmd_doctor\(", source, re.M)), 1)
        self.assertEqual(
            len(re.findall(r'add_parser\(\s*"doctor"', source)), 1)
        self.assertNotIn("windows", source.lower().replace("windows-style", ""))

    def test_doctor_opens_the_database_through_the_shared_opener(self):
        import inspect
        import re

        from solvent import cli as cli_module

        source = inspect.getsource(cli_module.cmd_doctor)
        self.assertIn("_open(args)", source,
                      "doctor stopped using the one shared opener")
        # The docstring describes the old defect, so it says ``Solvent()`` on
        # purpose. Match the code, not the account of what used to be wrong.
        body = source.replace(cli_module.cmd_doctor.__doc__ or "", "")
        self.assertFalse(
            re.search(r"Solvent\(\s*\)", body),
            "doctor constructs a Solvent with no path again")

    def test_the_diagnostics_authority_is_untouched_by_this_fix(self):
        from solvent.store import TABLE_OWNER

        self.assertEqual(TABLE_OWNER["diagnostic_reports"], "diagnostics")


if __name__ == "__main__":
    unittest.main()
