"""The CLI is the owner's window onto Solvent, so it must not lie or crash.

These are smoke tests with one substantive assertion each: the read-only
commands must run, and none of them may report a posture that is more permissive
than the one actually enforced.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from solvent.cli import main


def run(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(argv))
    return code, buffer.getvalue()


class ReadOnlyCommands(unittest.TestCase):
    def test_doctor_reports_the_fail_closed_posture(self):
        code, out = run("doctor")
        self.assertEqual(code, 0)
        self.assertIn("FAIL-CLOSED", out)

    def test_readiness_names_its_blockers(self):
        code, out = run("readiness")
        self.assertEqual(code, 0)
        self.assertIn("blocking:", out)

    def test_laws_runs(self):
        code, out = run("laws")
        self.assertEqual(code, 0)
        self.assertIn("SOLVENT", out.upper())

    def test_services_ranks_without_quoting_a_price(self):
        code, out = run("services")
        self.assertEqual(code, 0)
        self.assertIn("Recommended first service", out)
        # Selection is strategic. A price here would be a second Governor.
        self.assertNotIn("$", out)

    def test_status_runs_on_an_empty_business(self):
        code, out = run("status")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())


class NoCommandSellsWork(unittest.TestCase):
    def test_there_is_no_command_that_performs_a_real_external_action(self):
        """Every subcommand is inspection or a fixture run. Nothing goes out."""
        from solvent.cli import build_parser

        parser = build_parser()
        actions = [a for a in parser._subparsers._group_actions[0].choices]
        self.assertEqual(
            set(actions),
            {"doctor", "laws", "acquire", "services", "metrics", "status",
             "readiness", "demo", "state"},
            "a new subcommand was added without reviewing it for external effect",
        )


if __name__ == "__main__":
    unittest.main()
