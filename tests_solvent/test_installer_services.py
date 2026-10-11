"""DeskPilot's two services: the commands they run, and that they run.

Two defects shipped in the same place, and both were invisible to a green
suite because nothing here ever read the generated launcher's command and
nothing compared it to the Solvent CLI that has to accept it.

* The runtime's launcher passed ``--spool`` to ``solvent run``. That is a
  ``solvent web`` flag. ``solvent run`` rejects unrecognised arguments and
  exits 2, so the scheduled task failed at every boot, retried ten times a
  minute apart, and gave up. DeskPilot never started.
* Nothing started the control centre at all. The installer added a firewall
  rule for it, set a password for it, and the last screen offered its address
  --- but the only boot task ran the runtime, and ``solvent run`` serves no
  HTTP. The browser said ERR_CONNECTION_REFUSED, correctly.

So these tests do the two things that were missing. They **execute** each
generated launcher, in process, with ``runpy.run_module`` replaced by a
recorder, which proves what command the file actually builds rather than what
its template appears to say. Then they hand that command to the **real**
``solvent.cli`` parser, which is the thing that rejected it on Windows.

Nothing here starts a server or a runtime. The launcher is stopped at the
moment it would hand over, which is the last point at which the question is
still "is this command right".
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from deskpilot_installer import integration, lifecycle  # noqa: E402
from deskpilot_installer.layout import Layout, WEB_HOST, WEB_PORT  # noqa: E402
from deskpilot_installer.installlog import Log  # noqa: E402
from solvent.cli import build_parser  # noqa: E402

from tests_solvent.test_installer_matrix import (  # noqa: E402
    CERTIFIED_ENTRIES, Scenario, certified_payload, install_ready, zip_bytes,
)


def run_launcher(source: str, env_file: pathlib.Path) -> list[str]:
    """Execute a generated launcher and return the argv it hands to Solvent.

    In process and with the hand-over replaced, so no runtime starts and no
    port is bound. ``os.environ`` and ``sys.argv`` are restored, because the
    launcher's whole job is to change both.
    """
    captured: dict[str, object] = {}

    class Recorder:
        @staticmethod
        def run_module(name, run_name=None):
            captured["module"] = name
            captured["run_name"] = run_name
            captured["argv"] = list(sys.argv)
            captured["environ"] = dict(os.environ)

    namespace: dict[str, object] = {"__name__": "launcher"}
    exec(compile(source, "<generated launcher>", "exec"), namespace)
    namespace["ENV_FILE"] = str(env_file)
    namespace["runpy"] = Recorder

    before_argv, before_env = list(sys.argv), dict(os.environ)
    try:
        namespace["main"]()
    finally:
        sys.argv = before_argv
        os.environ.clear()
        os.environ.update(before_env)

    assert captured.get("module") == "solvent.cli", captured
    assert captured.get("run_name") == "__main__", captured
    run_launcher.last_environ = captured["environ"]  # type: ignore[attr-defined]
    return list(captured["argv"])


class TheGeneratedCommandsAreAcceptedByTheRealCli(unittest.TestCase):
    """The check whose absence let a boot-breaking flag ship.

    The parser here is Solvent's own, not a copy of what it is thought to
    accept. That is the whole point: the installer's idea of the command and
    the CLI's idea of the command were allowed to drift, and only Windows
    noticed.
    """

    def setUp(self):
        self.layout = Layout()
        self.parser = build_parser()
        self.box = pathlib.Path(
            __file__).resolve().parent / "__launcher_env__"
        self.box.mkdir(exist_ok=True)
        self.env_file = self.box / "solvent.env"
        self.env_file.write_text(
            "SOLVENT_OWNER_KEY=not-a-real-key\n"
            "SOLVENT_WEB_PASSWORD_HASH=not-a-real-hash\n", encoding="utf-8")

    def tearDown(self):
        self.env_file.unlink(missing_ok=True)
        with __import__("contextlib").suppress(OSError):
            self.box.rmdir()

    def argv_of(self, service) -> list[str]:
        source = integration.launcher_source(self.layout, service)
        return run_launcher(source, self.env_file)

    def test_there_are_two_services_and_they_are_the_expected_two(self):
        self.assertEqual(
            [service.task_name for service in integration.BOOT_SERVICES],
            ["DeskPilot", "DeskPilot Control Centre"])

    def test_every_service_command_parses(self):
        """An unrecognised flag fails here instead of at the owner's boot."""
        for service in integration.BOOT_SERVICES:
            with self.subTest(service=service.task_name):
                argv = self.argv_of(service)
                self.assertEqual(argv[0], "solvent")
                try:
                    self.parser.parse_args(argv[1:])
                except SystemExit as exit_code:
                    self.fail(f"the real Solvent CLI rejected the command "
                              f"generated for {service.task_name}: "
                              f"{' '.join(argv)} (exit {exit_code.code})")

    def test_the_runtime_does_not_pass_the_web_commands_spool_flag(self):
        """The first defect, named.

        ``--spool`` belongs to ``solvent web``. The runtime does not read the
        spool: owner intents are consumed by ``solvent intents`` under the
        owner's explicit authorisation, never on the runtime's timer.
        """
        runtime = integration.BOOT_SERVICES[0]
        argv = self.argv_of(runtime)
        self.assertEqual(argv[1], "run")
        self.assertNotIn("--spool", argv)

    def test_the_old_runtime_command_would_fail_this_suite(self):
        """Proof the check has teeth, using the command that actually shipped."""
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()) as complaint:
            with self.assertRaises(SystemExit) as rejected:
                self.parser.parse_args(["run", "--db", self.layout.db,
                                        "--spool", self.layout.spool])
        self.assertEqual(rejected.exception.code, 2)
        self.assertIn("unrecognized arguments: --spool",
                      complaint.getvalue())

    def test_the_runtime_runs_in_always_on_mode(self):
        """A boot-started service on a host that stays awake is always-on.

        It makes the startup checks refuse in-memory storage rather than
        appearing to work and losing everything at the next restart.
        ``deploy/solvent.service`` passes it for the same reason.
        """
        argv = self.argv_of(integration.BOOT_SERVICES[0])
        self.assertIn("--always-on", argv)
        parsed = self.parser.parse_args(argv[1:])
        self.assertTrue(parsed.always_on)

    def test_the_control_centre_serves_http_on_loopback_only(self):
        argv = self.argv_of(integration.BOOT_SERVICES[1])
        self.assertEqual(argv[1], "web")
        parsed = self.parser.parse_args(argv[1:])
        self.assertEqual(parsed.host, WEB_HOST)
        self.assertEqual(parsed.host, "127.0.0.1")
        self.assertEqual(parsed.port, WEB_PORT)
        self.assertFalse(parsed.insecure_cookies)

    def test_the_control_centre_is_told_where_owner_intents_go(self):
        argv = self.argv_of(integration.BOOT_SERVICES[1])
        parsed = self.parser.parse_args(argv[1:])
        self.assertEqual(parsed.spool, self.layout.spool)

    def test_both_services_use_the_same_database(self):
        seen = {self.parser.parse_args(self.argv_of(service)[1:]).db
                for service in integration.BOOT_SERVICES}
        self.assertEqual(seen, {self.layout.db})

    def test_the_two_services_are_not_the_same_process_twice(self):
        """Requirement: no duplicate runtime."""
        subcommands = [self.argv_of(service)[1]
                       for service in integration.BOOT_SERVICES]
        self.assertEqual(len(set(subcommands)), len(subcommands),
                         f"two services run the same command: {subcommands}")

    def test_each_launcher_loads_the_secrets_from_the_file(self):
        """Credentials reach the process through the environment, not argv."""
        for service in integration.BOOT_SERVICES:
            with self.subTest(service=service.task_name):
                argv = self.argv_of(service)
                environ = run_launcher.last_environ
                self.assertEqual(environ.get("SOLVENT_OWNER_KEY"),
                                 "not-a-real-key")
                self.assertEqual(environ.get("SOLVENT_WEB_PASSWORD_HASH"),
                                 "not-a-real-hash")
                for secret in ("not-a-real-key", "not-a-real-hash"):
                    self.assertNotIn(secret, " ".join(argv))

    def test_a_missing_secrets_file_does_not_stop_the_launcher(self):
        """It is Solvent's job to refuse, with a reason the owner can read."""
        self.env_file.unlink()
        for service in integration.BOOT_SERVICES:
            with self.subTest(service=service.task_name):
                argv = self.argv_of(service)
                self.assertEqual(argv[0], "solvent")

    def test_every_launcher_is_valid_python_and_has_no_shell(self):
        for service in integration.BOOT_SERVICES:
            source = integration.launcher_source(self.layout, service)
            ast.parse(source)
            for forbidden in ("subprocess", "os.system", "shell=True",
                              "cmd.exe", "/c "):
                self.assertNotIn(forbidden, source)


class BothServicesStartAtBootAndNow(Scenario):
    """Registered with a boot trigger, and started without waiting for one."""

    def installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        return host, engine, report

    def test_both_services_are_registered(self):
        host, _engine, _report = self.installed()
        for service in integration.BOOT_SERVICES:
            self.assertIn(service.task_name, host.scheduled_tasks,
                          f"{service.task_name} was never registered")

    def test_both_services_are_running_when_the_installer_finishes(self):
        """The owner should not have to reboot to open the control centre."""
        host, _engine, _report = self.installed()
        for service in integration.BOOT_SERVICES:
            self.assertTrue(host.scheduled_tasks.get(service.task_name),
                            f"{service.task_name} is registered but was not "
                            f"started, so it is down until the next boot")

    def test_both_launchers_are_written_where_the_tasks_point(self):
        host, _engine, _report = self.installed()
        layout = Layout()
        for service in integration.BOOT_SERVICES:
            script = service.script_path(layout)
            xml = host.read_bytes(service.xml_path(layout)).decode("utf-8")
            self.assertIn(script, xml,
                          f"{service.task_name} does not point at its script")
            host.read_bytes(script)   # raises if the installer never wrote it

    def test_every_task_restarts_after_a_crash_and_runs_as_the_system(self):
        host, _engine, _report = self.installed()
        layout = Layout()
        for service in integration.BOOT_SERVICES:
            xml = host.read_bytes(service.xml_path(layout)).decode("utf-8")
            with self.subTest(service=service.task_name):
                self.assertIn("<BootTrigger>", xml)
                self.assertIn("<RestartOnFailure>", xml)
                self.assertIn("<Count>10</Count>", xml)
                self.assertIn("S-1-5-18", xml)
                self.assertIn(
                    "<MultipleInstancesPolicy>IgnoreNew"
                    "</MultipleInstancesPolicy>", xml,
                    "a second copy could start alongside the first")

    def test_no_secret_reaches_a_task_definition(self):
        host, _engine, _report = self.installed()
        layout = Layout()
        for service in integration.BOOT_SERVICES:
            xml = host.read_bytes(service.xml_path(layout)).decode("utf-8")
            for secret in ("SOLVENT_OWNER_KEY", "PASSWORD", "password"):
                self.assertNotIn(secret, xml)

    def test_declining_autostart_registers_nothing(self):
        """The owner's choice stands; it is not overridden for convenience."""
        from tests_solvent.test_installer_matrix import Components, answers
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        self.install(host, digest,
                     owner=answers(components=Components(autostart=False,
                                                         backups=False)))
        for service in integration.BOOT_SERVICES:
            self.assertNotIn(service.task_name, host.scheduled_tasks)


class TheLifecycleKnowsAboutBothServices(Scenario):
    """Install, upgrade and uninstall each have two services to think about."""

    def installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        return host, digest

    def test_uninstall_removes_every_task_it_created(self):
        host, _digest = self.installed()
        host.commands.clear()
        lifecycle.uninstall(host, Layout(), log=Log(host))
        self.assertEqual(host.scheduled_tasks, {},
                         f"left behind: {sorted(host.scheduled_tasks)}")

    def test_uninstall_keeps_the_owners_data(self):
        host, _digest = self.installed()
        layout = Layout()
        before = host.read_bytes(layout.db)
        lifecycle.uninstall(host, layout, log=Log(host))
        self.assertEqual(host.read_bytes(layout.db), before,
                         "the database did not survive uninstall")

    def test_an_upgrade_stops_and_restarts_both_services(self):
        """An upgrade replaces the code both services run, so it must do both.

        Restarting only the runtime would leave the owner upgraded and the
        control centre down, with nothing saying so -- the same silence as the
        original defect, arriving later.
        """
        import hashlib
        from deskpilot_installer.engine import Settings
        from tests_solvent.test_installer_matrix import answers

        host, _digest = self.installed()
        for service in integration.BOOT_SERVICES:
            self.assertTrue(host.scheduled_tasks.get(service.task_name))

        # A genuinely different payload: an upgrade to the version already
        # installed stops at "nothing to do" and would never reach the
        # restart this test is about.
        new_payload = zip_bytes({**CERTIFIED_ENTRIES,
                                 "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new_payload)
        host.commands.clear()
        report = lifecycle.upgrade(
            host,
            Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                     expected_digest=hashlib.sha256(new_payload).hexdigest()),
            answers(), Log(host))
        self.assertTrue(report.may_continue,
                        [row.detail for row in report.blockers])

        ended = [argv[argv.index("/TN") + 1] for argv in host.commands
                 if "/End" in argv and "/TN" in argv]
        for service in integration.BOOT_SERVICES:
            with self.subTest(service=service.task_name):
                self.assertIn(service.task_name, ended,
                              "was not stopped before its code was replaced")
                self.assertTrue(
                    host.scheduled_tasks.get(service.task_name),
                    "was stopped for the upgrade and never started again")

    def test_an_upgrade_keeps_the_database(self):
        import hashlib
        from deskpilot_installer.engine import Settings
        from tests_solvent.test_installer_matrix import answers

        host, _digest = self.installed()
        layout = Layout()
        host.write_bytes(layout.db, b"the owner's records")
        new_payload = zip_bytes({**CERTIFIED_ENTRIES,
                                 "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new_payload)
        lifecycle.upgrade(
            host,
            Settings(layout=layout, package_path=r"C:\Media\v2.zip",
                     expected_digest=hashlib.sha256(new_payload).hexdigest()),
            answers(), Log(host))
        self.assertEqual(host.read_bytes(layout.db), b"the owner's records")

    def test_the_verify_step_notices_a_missing_control_centre(self):
        """Deleting one service must not leave verification saying PASS."""
        host, _digest = self.installed()
        data, digest = certified_payload()
        host2 = install_ready(package=data, digest=digest)
        engine, _report, _log = self.install(host2, digest)
        del host2.scheduled_tasks["DeskPilot Control Centre"]
        row = engine._autostart_row()
        self.assertEqual(row.outcome.name, "FAIL")
        self.assertIn("DeskPilot Control Centre", row.detail)


if __name__ == "__main__":
    unittest.main()


class StartingTheServicesAuthorisesNothing(unittest.TestCase):
    """Two services now start by themselves, which is a new thing to check.

    Autostart means DeskPilot is running without anyone watching it start, so
    the question "could booting the machine begin real business" has to have a
    flat answer. ``--always-on`` is the only new capability-shaped flag, and it
    is not capability-shaped at all: :class:`solvent.runtime.Mode` is read in
    three places, and the only one that decides anything refuses to start
    against in-memory storage. It does not reach the Action Gate, the Financial
    Governor, the egress allowlist or the payment rails.
    """

    #: Subcommands a boot service may run. Anything else is a new authority
    #: arriving through a scheduled task, which is not where authority belongs.
    ALLOWED = {"run", "web"}

    def test_the_services_run_only_the_two_expected_subcommands(self):
        used = {service.argv(Layout())[1]
                for service in integration.BOOT_SERVICES}
        self.assertEqual(used, self.ALLOWED)

    def test_no_service_command_authorises_money_or_submissions(self):
        forbidden = ("--live", "--real", "--spend", "--authorise",
                     "--authorize", "--approve", "--confirm", "--force",
                     "payments", "intents", "acquire", "relay",
                     "web-exposure", "resume")
        for service in integration.BOOT_SERVICES:
            argv = service.argv(Layout())
            for flag in forbidden:
                self.assertNotIn(flag, argv,
                                 f"{service.task_name} would run {flag}")

    def test_always_on_decides_only_whether_storage_must_be_durable(self):
        """The one new flag, read against Solvent's own runtime."""
        from solvent.runtime import Mode
        self.assertTrue(Mode.ALWAYS_ON.expects_durable_storage)
        self.assertFalse(Mode.LOCAL.expects_durable_storage)
        self.assertEqual({mode.name for mode in Mode}, {"LOCAL", "ALWAYS_ON"})

    def test_the_simulation_posture_is_not_something_a_service_sets(self):
        """Whatever the posture is, it is the policy's to say, not a flag's."""
        for service in integration.BOOT_SERVICES:
            source = integration.launcher_source(Layout(), service)
            for knob in ("simulation_only", "money_can_move",
                         "operating_mode", "capability", "halt", "resume"):
                self.assertNotIn(knob, source,
                                 f"{service.task_name} writes {knob}")
