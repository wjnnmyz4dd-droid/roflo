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

    def test_health_distinguishes_alive_from_able(self):
        code, out = run("health")
        self.assertEqual(code, 0)
        self.assertIn("Health:", out)
        self.assertIn("May start work:", out)

    def test_halt_then_health_reports_halted_and_refuses_work(self):
        import pathlib as _p, tempfile as _t
        db = str(_p.Path(_t.mkdtemp()) / "s.db")
        self.assertEqual(run("halt", "--db", db)[0], 0)
        code, out = run("health", "--db", db)
        self.assertEqual(code, 2, "a halted Solvent must not report success")
        self.assertIn("HALTED", out)

    def test_recover_reports_nothing_to_do_on_a_clean_system(self):
        code, out = run("recover")
        self.assertEqual(code, 0)
        self.assertIn("nothing outstanding", out)

    def test_run_refuses_always_on_mode_without_durable_storage(self):
        import contextlib as _c, io as _io
        from solvent.cli import main
        err = _io.StringIO()
        with _c.redirect_stderr(err), _c.redirect_stdout(_io.StringIO()):
            code = main(["run", "--db", ":memory:", "--always-on", "--max-ticks", "1"])
        self.assertEqual(code, 1)
        self.assertIn("did not start", err.getvalue())

    def test_an_unreadable_database_is_explained_not_traced(self):
        """systemd gets an exit code; the operator needs a sentence."""
        import contextlib as _c, io as _io, pathlib as _p, tempfile as _t
        from solvent.cli import main
        from solvent.harness import Solvent

        db = str(_p.Path(_t.mkdtemp()) / "s.db")
        Solvent(db).store.close()
        torn = _p.Path(db)
        torn.write_bytes(torn.read_bytes()[: len(torn.read_bytes()) // 2])

        err = _io.StringIO()
        with _c.redirect_stderr(err), _c.redirect_stdout(_io.StringIO()):
            code = main(["health", "--db", db])
        self.assertEqual(code, 1)
        self.assertIn("cannot open", err.getvalue())
        self.assertIn("backup", err.getvalue())

    def test_status_runs_on_an_empty_business(self):
        code, out = run("status")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())


class NoCommandSellsWork(unittest.TestCase):
    def test_there_is_no_command_that_performs_a_real_external_action(self):
        """Every subcommand is inspection, a fixture run, or a local record.

        The list is written out so that adding a command *fails this test*, and
        somebody has to say out loud what the new one does. `setup` and
        `payments` were added that way: each was reviewed below before being
        listed here.
        """
        from solvent.cli import build_parser

        parser = build_parser()
        actions = [a for a in parser._subparsers._group_actions[0].choices]
        self.assertEqual(
            set(actions),
            {"doctor", "laws", "acquire", "services", "metrics", "status",
             "readiness", "demo", "state", "run", "health", "recover", "halt",
             "resume", "setup", "payments", "relay"},
            "a new subcommand was added without reviewing it for external effect",
        )

    def test_no_setup_command_takes_a_secret_as_an_argument(self):
        """A secret on a command line is in shell history and in the process
        list, where every other user on the machine can read it. The owner key
        and the webhook secret reach Solvent through the environment file and
        through nothing else."""
        from solvent.cli import build_parser

        parser = build_parser()
        setup = parser._subparsers._group_actions[0].choices["setup"]
        for name, sub in setup._subparsers._group_actions[0].choices.items():
            for action in sub._actions:
                with self.subTest(command=name, option=action.dest):
                    self.assertNotIn(
                        action.dest,
                        {"key", "owner_key", "secret", "webhook_secret",
                         "api_key", "password", "token", "tax_reference"})

    def test_the_tax_reference_flag_stores_presence_not_a_value(self):
        from solvent.cli import build_parser

        parser = build_parser()
        setup = parser._subparsers._group_actions[0].choices["setup"]
        contracting = setup._subparsers._group_actions[0].choices["contracting"]
        flag = next(a for a in contracting._actions
                    if a.dest == "tax_reference_provisioned")
        self.assertEqual(flag.nargs, 0, "this flag must not accept a value")

    def test_the_setup_commands_reach_no_network(self):
        """Reviewed individually: contracting, work-source, model, stripe and
        capability write to the local database; check reads it. None constructs
        a source with a host or a request, and none calls the Action Gate."""
        import inspect

        from solvent import cli

        for name in ("cmd_setup_contracting", "cmd_setup_work_source",
                     "cmd_setup_model", "cmd_setup_stripe",
                     "cmd_setup_capability", "cmd_setup_check"):
            source = inspect.getsource(getattr(cli, name))
            with self.subTest(command=name):
                for forbidden in ("gate.request", "urlopen", "socket", "http"):
                    self.assertNotIn(forbidden, source)

    def test_ingesting_payments_makes_no_outbound_call(self):
        """It reads files a receiver left on disk and verifies them locally.
        Inbound data is not an external effect, and this cannot create money:
        an event that does not verify never reaches the Ledger."""
        import inspect

        from solvent import harness

        source = inspect.getsource(harness.ingest_payment_spool)
        for forbidden in ("gate.request", "urlopen", "socket.", "requests."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
