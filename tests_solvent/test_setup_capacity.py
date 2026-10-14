"""`solvent setup capacity` — the command that was missing.

max_owed_seconds starts at zero, zero means "take nothing on", and
the capacity gate therefore refuses every job on a fresh install.
There was no command to change that, so an owner who followed the
manual to the letter ended up with a business that would not take
work and no documented way to make it.
"""

from __future__ import annotations

import io
import contextlib
import tempfile
import unittest

from solvent.cli import main
from solvent.harness import Solvent
from solvent.types import JOBS_PER_AGENT_CEILING


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/s.db"

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["setup", "capacity", "--db", self.db, *args])
        return code, out.getvalue()

    def budget(self):
        solvent = Solvent(self.db)
        self.addCleanup(lambda: solvent.store.close())
        return solvent.capacity.budget()


class ItRecordsTheOwnersNumbers(Base):

    def test_a_budget_can_be_set(self):
        code, said = self.run_cli("--max-owed-seconds", "14400",
                                  "--max-concurrent-jobs", "3",
                                  "--cpu-share", "0.5")
        self.assertEqual(code, 0)
        budget = self.budget()
        self.assertEqual(budget.max_owed_seconds, 14400.0)
        self.assertEqual(budget.max_concurrent_jobs, 3)
        self.assertEqual(budget.cpu_share, 0.5)

    def test_it_reports_what_is_still_missing(self):
        code, said = self.run_cli("--max-owed-seconds", "14400")
        self.assertEqual(code, 0)
        self.assertIn("[NEED]", said)

    def test_it_confirms_when_the_budget_is_usable(self):
        _, said = self.run_cli("--max-owed-seconds", "14400",
                               "--cpu-share", "0.5")
        self.assertIn("[OK ]", said)
        self.assertIn("MetaTrader 5", said)

    def test_a_usable_budget_makes_the_gate_accept(self):
        self.run_cli("--max-owed-seconds", "14400", "--cpu-share", "0.5")
        solvent = Solvent(self.db)
        self.addCleanup(lambda: solvent.store.close())
        self.assertTrue(solvent.capacity.room_for(60.0).allowed)

    def test_nothing_passed_records_nothing(self):
        code, said = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("nothing to record", said)


class ItRefusesWhatTheGateWouldRefuse(Base):

    def refused(self, *args):
        code, said = self.run_cli(*args)
        self.assertEqual(code, 1, said)
        self.assertIn("refused", said)
        return said

    def test_more_jobs_than_the_ceiling(self):
        said = self.refused("--max-concurrent-jobs",
                            str(JOBS_PER_AGENT_CEILING + 1))
        self.assertIn("four is the most", said)

    def test_more_workers_than_the_ceiling(self):
        self.refused("--max-execution-workers",
                     str(JOBS_PER_AGENT_CEILING + 1))
        self.refused("--max-verification-workers",
                     str(JOBS_PER_AGENT_CEILING + 1))

    def test_the_whole_machine(self):
        said = self.refused("--cpu-share", "1.5")
        self.assertIn("MetaTrader 5", said)

    def test_an_optimistic_safety_factor(self):
        self.refused("--deadline-safety-factor", "0.5")

    def test_a_zero_budget_without_saying_so(self):
        said = self.refused("--max-owed-seconds", "0")
        self.assertIn("--halt", said)

    def test_a_refusal_writes_nothing(self):
        self.run_cli("--max-owed-seconds", "14400", "--cpu-share", "0.5")
        before = self.budget().max_concurrent_jobs
        self.refused("--max-concurrent-jobs", "9")
        self.assertEqual(self.budget().max_concurrent_jobs, before)


class HaltingIsSaidOutLoud(Base):

    def test_work_can_be_halted(self):
        self.run_cli("--max-owed-seconds", "14400", "--cpu-share", "0.5")
        code, said = self.run_cli("--halt")
        self.assertEqual(code, 0)
        self.assertIn("halted", said)
        solvent = Solvent(self.db)
        self.addCleanup(lambda: solvent.store.close())
        self.assertFalse(solvent.capacity.room_for(1.0).allowed)

    def test_work_can_be_resumed(self):
        self.run_cli("--max-owed-seconds", "14400", "--cpu-share", "0.5")
        self.run_cli("--halt")
        self.run_cli("--resume")
        solvent = Solvent(self.db)
        self.addCleanup(lambda: solvent.store.close())
        self.assertTrue(solvent.capacity.room_for(1.0).allowed)


class TheCommandIsReachable(unittest.TestCase):
    """The report tells the owner to type this. It must exist."""

    def test_it_is_a_setup_subcommand(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                main(["setup", "--help"])
        self.assertIn("capacity", out.getvalue())

    def test_every_command_the_owner_is_told_to_type_exists(self):
        """Each of these is named in docs/deskpilot-activation-readiness.md."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                main(["setup", "--help"])
        listed = out.getvalue()
        for command in ("capacity", "contracting", "commercial",
                        "work-source", "crypto", "stripe", "capability"):
            with self.subTest(command=command):
                self.assertIn(command, listed)


if __name__ == "__main__":
    unittest.main()
