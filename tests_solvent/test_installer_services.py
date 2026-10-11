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

    def test_each_launcher_loads_its_secrets_from_the_file(self):
        """Credentials reach the process through the environment, not argv.

        Each service gets what it reads and not what it does not: the split
        itself is asserted below. What is common to both is that nothing
        arrives on a command line, where every account on the machine could
        read it out of the process list.
        """
        for service in integration.BOOT_SERVICES:
            with self.subTest(service=service.task_name):
                argv = self.argv_of(service)
                environ = run_launcher.last_environ
                self.assertTrue(
                    any(value in ("not-a-real-key", "not-a-real-hash")
                        for value in environ.values()),
                    f"{service.task_name} loaded no credentials at all")
                for secret in ("not-a-real-key", "not-a-real-hash"):
                    self.assertNotIn(secret, " ".join(argv))

    def test_the_control_centre_is_not_given_the_owner_signing_key(self):
        """``solvent.web`` says it never reads the key. Nor is it handed it.

        The web surface states as a security property that
        ``SOLVENT_OWNER_KEY`` "is never read here, never sent to a browser,
        and never needed to display anything" -- and it reads exactly one
        environment variable, the password hash. A launcher that loaded the
        whole secrets file would put the key in the process environment
        anyway, so a compromised control centre could read what the design
        keeps away from it.
        """
        web = integration.BOOT_SERVICES[1]
        self.argv_of(web)
        environ = run_launcher.last_environ
        self.assertIsNone(environ.get("SOLVENT_OWNER_KEY"),
                          "the control centre was handed the owner's key")
        self.assertEqual(environ.get("SOLVENT_WEB_PASSWORD_HASH"),
                         "not-a-real-hash",
                         "the control centre cannot authenticate the owner")

    def test_the_runtime_is_given_the_owner_signing_key(self):
        """It signs, so it needs it. Least privilege, not no privilege."""
        self.argv_of(integration.BOOT_SERVICES[0])
        environ = run_launcher.last_environ
        self.assertEqual(environ.get("SOLVENT_OWNER_KEY"), "not-a-real-key")

    def test_each_service_declares_what_it_may_read(self):
        runtime, web = integration.BOOT_SERVICES
        self.assertIsNone(runtime.env_allow)
        self.assertEqual(web.env_allow, ("SOLVENT_WEB_PASSWORD_HASH",))

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

    def test_every_task_starts_at_boot_and_restarts_after_a_crash(self):
        host, _engine, _report = self.installed()
        layout = Layout()
        for service in integration.BOOT_SERVICES:
            xml = host.read_bytes(service.xml_path(layout)).decode("utf-8")
            with self.subTest(service=service.task_name):
                self.assertIn("<BootTrigger>", xml)
                self.assertIn("<RestartOnFailure>", xml)
                self.assertIn("<Count>10</Count>", xml)
                self.assertIn(
                    "<MultipleInstancesPolicy>IgnoreNew"
                    "</MultipleInstancesPolicy>", xml,
                    "a second copy could start alongside the first")

    def test_the_two_services_do_not_run_as_the_same_identity(self):
        """This test used to assert that both ran as SYSTEM, and it passed.

        That was the defect, not the test: the control centre listens on a
        socket and renders the owner's data, and running it with the same
        token as the runtime meant every control it was subject to was a
        control enforced by its own code.
        """
        host, _engine, _report = self.installed()
        layout = Layout()
        identities = {}
        for service in integration.BOOT_SERVICES:
            xml = host.read_bytes(service.xml_path(layout)).decode("utf-8")
            identities[service.task_name] = xml

        runtime = next(s for s in integration.BOOT_SERVICES
                       if s.task_name == integration.TASK_NAME)
        web = next(s for s in integration.BOOT_SERVICES
                   if s.task_name == integration.WEB_TASK_NAME)
        self.assertIn("<UserId>S-1-5-18</UserId>", identities[runtime.task_name])
        self.assertIn("<RunLevel>HighestAvailable</RunLevel>",
                      identities[runtime.task_name])
        self.assertIn("<UserId>S-1-5-19</UserId>", identities[web.task_name])
        self.assertIn("<RunLevel>LeastPrivilege</RunLevel>",
                      identities[web.task_name])
        self.assertNotIn("S-1-5-18", identities[web.task_name],
                         "the control centre's task still mentions SYSTEM")

    def test_every_task_logs_on_without_a_stored_credential(self):
        """``ServiceAccount`` means the service control manager logs the
        identity on. An interactive logon type would need a password stored
        somewhere, which is a new secret invented to protect a secret."""
        host, _engine, _report = self.installed()
        layout = Layout()
        for service in integration.BOOT_SERVICES:
            xml = host.read_bytes(service.xml_path(layout)).decode("utf-8")
            with self.subTest(service=service.task_name):
                self.assertIn("<LogonType>ServiceAccount</LogonType>", xml)

    def test_the_control_centre_launcher_never_opens_the_owner_key_file(self):
        """The launcher reads its environment file whole and filters by name
        afterwards. While both services shared one file, the web process read
        the owner signing key into its own memory on every boot and then
        declined to use it -- which is a Python ``if``, not a boundary."""
        layout = Layout()
        web = next(s for s in integration.BOOT_SERVICES
                   if s.task_name == integration.WEB_TASK_NAME)
        source = integration.launcher_source(layout, web)
        self.assertIn(layout.web_env_file, source)
        self.assertNotIn(layout.env_file, source)

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


class VerificationCallsTheControlCentreRatherThanTrustingIt(Scenario):
    """A registered task is not an answering service.

    Verification reported autostart PASS because a scheduled task existed. It
    existed on the installation whose control centre nothing ever started, so
    the owner was told DESKPILOT READY and then met ERR_CONNECTION_REFUSED.
    """

    def installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        return host, engine

    def test_a_listening_control_centre_is_reported_as_answering(self):
        _host, engine = self.installed()
        row = engine._control_centre_row()
        self.assertEqual(row.outcome.name, "PASS")
        self.assertIn(f"{WEB_HOST}:{WEB_PORT}", row.detail)

    def test_a_silent_control_centre_is_not_reported_as_ready(self):
        host, engine = self.installed()
        host.listening.pop(WEB_PORT, None)      # it died, or never bound
        row = engine._control_centre_row()
        self.assertNotEqual(row.outcome.name, "PASS")

    def test_a_silent_control_centre_does_not_fail_a_good_install(self):
        """It may simply still be binding. Guessing either way is wrong."""
        host, engine = self.installed()
        host.listening.pop(WEB_PORT, None)
        row = engine._control_centre_row()
        self.assertEqual(row.outcome.name, "WARN")
        self.assertFalse(row.outcome.stops_installation)

    def test_the_warning_says_what_to_do_next(self):
        host, engine = self.installed()
        host.listening.pop(WEB_PORT, None)
        row = engine._control_centre_row()
        self.assertIn("http://", row.detail)
        self.assertIn(integration.WEB_TASK_NAME, row.detail)

    def test_the_verify_report_carries_the_row(self):
        _host, engine = self.installed()
        report = engine.verify(probe_inference=False)
        self.assertIsNotNone(report.row("Control centre"),
                             "verification does not mention the control centre")

    def test_stopping_the_task_stops_the_answer(self):
        """The fake is only useful here if starting and stopping differ."""
        host, engine = self.installed()
        self.assertEqual(engine._control_centre_row().outcome.name, "PASS")
        host.run(["schtasks.exe", "/End", "/TN", integration.WEB_TASK_NAME])
        self.assertEqual(engine._control_centre_row().outcome.name, "WARN")


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


class RepairingAnInstallationMadeByTheDefectiveBuild(Scenario):
    """The machine this change exists to fix, reconstructed and repaired.

    An installation from the previous build has: a ``DeskPilot`` task whose
    launcher passes ``--spool`` to ``solvent run`` and therefore exits 2 at
    every boot, no control-centre task at all, and an owner's database,
    credentials and backups that must come through untouched.
    """

    OLD_LAUNCHER = (
        'APP = r"C:\\DeskPilot\\App"\n'
        'ENV_FILE = r"C:\\DeskPilot\\Config\\solvent.env"\n'
        'DB = r"C:\\DeskPilot\\Data\\solvent.db"\n'
        'SPOOL = r"C:\\DeskPilot\\Data\\spool"\n'
        'def main():\n'
        '    sys.argv = ["solvent", "run", "--db", DB]\n'
        '    if SPOOL:\n'
        '        sys.argv += ["--spool", SPOOL]\n'
    )

    def broken_installation(self):
        """A good install, then damaged back into the shape that shipped."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        layout = Layout()

        runtime, control_centre = integration.BOOT_SERVICES
        # The old build's launcher, and no control centre.
        host.write_bytes(runtime.script_path(layout),
                         self.OLD_LAUNCHER.encode("utf-8"))
        host.run(["schtasks.exe", "/Delete", "/TN",
                  control_centre.task_name, "/F"])
        host.write_bytes(layout.db, b"SQLite format 3\x00THE-OWNERS-RECORDS")
        return host, layout, digest

    def key_in(self, host, layout):
        from deskpilot_installer import secrets_env
        found = secrets_env.read_env_secrets(host, layout.env_file)
        secret = found.get(secrets_env.OWNER_KEY_ENV)
        return secret.reveal() if secret is not None else None

    def test_the_reconstruction_is_actually_broken(self):
        """Otherwise the repair below proves nothing."""
        host, layout, _digest = self.broken_installation()
        runtime, control_centre = integration.BOOT_SERVICES
        shipped = host.read_bytes(runtime.script_path(layout)).decode("utf-8")
        self.assertIn("--spool", shipped)
        self.assertNotIn(control_centre.task_name, host.scheduled_tasks)

    def test_reinstalling_over_it_fixes_both_services(self):
        host, layout, digest = self.broken_installation()
        self.install(host, digest)
        runtime, control_centre = integration.BOOT_SERVICES
        fixed = host.read_bytes(runtime.script_path(layout)).decode("utf-8")
        self.assertNotIn("--spool", fixed)
        self.assertIn("--always-on", fixed)
        self.assertIn(control_centre.task_name, host.scheduled_tasks)
        self.assertTrue(host.scheduled_tasks[control_centre.task_name])

    def test_reinstalling_over_it_keeps_the_owner_key(self):
        """The credential that authorises everything already recorded."""
        host, layout, digest = self.broken_installation()
        before = self.key_in(host, layout)
        self.assertIsNotNone(before)
        self.install(host, digest)
        self.assertEqual(self.key_in(host, layout), before,
                         "the reinstall rotated the owner's signing key")

    def test_reinstalling_over_it_keeps_the_database(self):
        host, layout, digest = self.broken_installation()
        self.install(host, digest)
        self.assertEqual(host.read_bytes(layout.db),
                         b"SQLite format 3\x00THE-OWNERS-RECORDS")

    def test_a_first_installation_still_generates_a_key(self):
        """Preserving an existing key must not mean never making one."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        layout = Layout()
        self.assertIsNone(self.key_in(host, layout))
        self.install(host, digest)
        self.assertIsNotNone(self.key_in(host, layout))

    def test_upgrading_it_rewrites_the_launchers_too(self):
        """An upgrade replaces the code; the commands belong to the version."""
        import hashlib
        from deskpilot_installer.engine import Settings
        from tests_solvent.test_installer_matrix import answers

        host, layout, _digest = self.broken_installation()
        new_payload = zip_bytes({**CERTIFIED_ENTRIES,
                                 "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new_payload)
        report = lifecycle.upgrade(
            host,
            Settings(layout=layout, package_path=r"C:\Media\v2.zip",
                     expected_digest=hashlib.sha256(new_payload).hexdigest()),
            answers(), Log(host))
        self.assertTrue(report.may_continue,
                        [row.detail for row in report.blockers])

        runtime, control_centre = integration.BOOT_SERVICES
        fixed = host.read_bytes(runtime.script_path(layout)).decode("utf-8")
        self.assertNotIn("--spool", fixed,
                         "the upgrade left the previous version's command")
        self.assertIn(control_centre.task_name, host.scheduled_tasks,
                      "the upgrade did not add the control centre")

    def test_an_upgrade_does_not_give_autostart_to_someone_who_declined_it(self):
        from tests_solvent.test_installer_matrix import Components, answers
        import hashlib
        from deskpilot_installer.engine import Settings

        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        layout = Layout()
        self.install(host, digest,
                     owner=answers(components=Components(autostart=False,
                                                         backups=False)))
        new_payload = zip_bytes({**CERTIFIED_ENTRIES,
                                 "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new_payload)
        lifecycle.upgrade(
            host,
            Settings(layout=layout, package_path=r"C:\Media\v2.zip",
                     expected_digest=hashlib.sha256(new_payload).hexdigest()),
            answers(components=Components(autostart=False, backups=False)),
            Log(host))
        for service in integration.BOOT_SERVICES:
            self.assertNotIn(service.task_name, host.scheduled_tasks)


class RepairKeepsEverythingAndCanBeUndone(Scenario):
    """Repair over a working installation, and repair that fails partway.

    ``step_extract`` deleted the previous application before writing the new
    one, so a step failing after that point left a half-replaced installation
    and the working version already gone. The owner's data was never the thing
    at risk -- repair does not run the credential or database steps -- but
    "the database was not modified" was a claim resting on nothing being able
    to go wrong.
    """

    #: Where install_ready puts the certified package on the fake machine.
    PACKAGE = r"C:\Media\DeskPilot-certified.zip"

    def installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        layout = Layout()
        host.write_bytes(layout.db, b"SQLite format 3\x00THE-OWNERS-WORK")
        return host, layout, digest

    def settings_for(self, digest):
        from deskpilot_installer.engine import Settings
        return Settings(layout=Layout(), package_path=self.PACKAGE,
                        expected_digest=digest)

    def repair(self, host, digest):
        from tests_solvent.test_installer_matrix import answers
        return lifecycle.repair(host, self.settings_for(digest), answers(),
                                Log(host))

    def key_in(self, host, layout):
        from deskpilot_installer import secrets_env
        found = secrets_env.read_env_secrets(host, layout.env_file)
        secret = found.get(secrets_env.OWNER_KEY_ENV)
        return secret.reveal() if secret is not None else None

    # -- the happy path -------------------------------------------------
    def test_a_repair_succeeds_and_says_so(self):
        host, _layout, digest = self.installed()
        report = self.repair(host, digest)
        self.assertTrue(report.may_continue,
                        [row.detail for row in report.blockers]
                        + ([report.failure.why] if report.failure else []))
        self.assertEqual(report.row("Data preservation").outcome.name, "PASS")

    def test_a_repair_backs_the_database_up_first(self):
        host, layout, digest = self.installed()
        report = self.repair(host, digest)
        row = report.row("Database backup")
        self.assertIsNotNone(row, "no backup was taken before replacing files")
        copies = [name for name in host.listdir(layout.backups)
                  if "pre-repair" in name]
        self.assertTrue(copies, f"no pre-repair copy in {layout.backups}")

    def test_the_backup_is_taken_before_anything_is_replaced(self):
        host, layout, digest = self.installed()
        order = []
        original_write, original_remove = host.write_bytes, host.remove_tree

        def watch_write(path, data):
            if "pre-repair" in path.lower():
                order.append("backup")
            original_write(path, data)

        def watch_remove(path):
            if path.lower().rstrip("\\") == layout.app.lower():
                order.append("app-gone")
            original_remove(path)

        host.write_bytes = watch_write        # type: ignore[method-assign]
        host.remove_tree = watch_remove       # type: ignore[method-assign]
        self.repair(host, digest)
        self.assertIn("backup", order)
        if "app-gone" in order:
            self.assertLess(order.index("backup"), order.index("app-gone"))

    def test_a_repair_keeps_the_database_the_key_and_the_backups(self):
        host, layout, digest = self.installed()
        before_key = self.key_in(host, layout)
        before_env = host.read_bytes(layout.env_file)
        self.repair(host, digest)
        self.assertEqual(host.read_bytes(layout.db),
                         b"SQLite format 3\x00THE-OWNERS-WORK")
        self.assertEqual(self.key_in(host, layout), before_key)
        self.assertEqual(host.read_bytes(layout.env_file), before_env,
                         "repair rewrote the credentials file")

    def test_a_repair_leaves_no_previous_copy_behind(self):
        host, layout, digest = self.installed()
        self.repair(host, digest)
        self.assertFalse(host.exists(layout.app + ".previous"),
                         "the kept copy was never cleaned up")

    def test_a_repair_restores_both_services(self):
        host, _layout, digest = self.installed()
        for service in integration.BOOT_SERVICES:
            host.run(["schtasks.exe", "/Delete", "/TN",
                      service.task_name, "/F"])
        self.repair(host, digest)
        for service in integration.BOOT_SERVICES:
            self.assertIn(service.task_name, host.scheduled_tasks,
                          f"{service.task_name} was not restored")

    def test_a_repair_does_not_impose_autostart_on_someone_who_declined(self):
        from tests_solvent.test_installer_matrix import Components, answers
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        self.install(host, digest,
                    owner=answers(components=Components(autostart=False,
                                                        backups=False)))
        lifecycle.repair(host, self.settings_for(digest), answers(), Log(host))
        for service in integration.BOOT_SERVICES:
            self.assertNotIn(service.task_name, host.scheduled_tasks)

    # -- the unhappy path -----------------------------------------------
    def failing_repair(self, host, digest, step="step_venv"):
        """Break one step, the way a real machine breaks one."""
        from deskpilot_installer.engine import Engine, StepFailed
        broken = getattr(Engine, step)

        def fail(self_engine):
            raise StepFailed("the virtual environment could not be rebuilt")

        setattr(Engine, step, fail)
        try:
            return self.repair(host, digest)
        finally:
            setattr(Engine, step, broken)

    def test_a_failed_repair_puts_the_application_back(self):
        host, layout, digest = self.installed()
        before = sorted(host.listdir(layout.app))
        report = self.failing_repair(host, digest)
        self.assertFalse(report.may_continue)
        self.assertEqual(sorted(host.listdir(layout.app)), before,
                         "the previous application was not restored")
        self.assertFalse(host.exists(layout.app + ".previous"))

    def test_a_failed_repair_keeps_the_database(self):
        host, layout, digest = self.installed()
        self.failing_repair(host, digest)
        self.assertEqual(host.read_bytes(layout.db),
                         b"SQLite format 3\x00THE-OWNERS-WORK")

    def test_a_failed_repair_reports_the_rollback(self):
        host, _layout, digest = self.installed()
        report = self.failing_repair(host, digest)
        row = report.row("Rollback")
        self.assertIsNotNone(row, "a failed repair did not say what it undid")
        self.assertEqual(row.outcome.name, "PASS")

    def test_a_failed_repair_says_what_was_not_touched(self):
        host, _layout, digest = self.installed()
        report = self.failing_repair(host, digest)
        self.assertIsNotNone(report.failure)
        reassurance = " ".join(report.failure.not_changed).lower()
        for promise in ("owner key", "metatrader", "backups"):
            self.assertIn(promise, reassurance)
        self.assertTrue(report.failure.why)
        self.assertTrue(report.failure.safe_next_action)
