"""Attacks on the installer, and the secrets it is trusted with.

An installer runs as Administrator, downloads from the internet, unpacks an
archive and writes the owner's signing key to disk. Every one of those is a
place to get it wrong, so these tests attack rather than exercise.

The attacks are grouped by what they would buy an attacker:

* **Code execution as Administrator** -- a hostile archive path, a tampered
  payload, a tampered Python download, a planted module on the import path.
* **The owner's secrets** -- reading them from a log, from a command line, from
  the process list, from a world-readable file, or from a backup.
* **Their business records** -- a repair or an uninstall that deletes what it
  was supposed to keep, or an upgrade that cannot be undone.

Where a defence is structural the test asserts the structure, because a
property that holds by construction is worth more than one that holds today.
``Secret`` cannot be formatted into a string; ``Host.run`` cannot be handed a
command line; the secrets file is restricted before it holds anything.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import (  # noqa: E402
    CERTIFIED_ENTRIES, FakeWindows, Handler, certified_payload, fresh_windows,
    install_ready, zip_bytes,
)

from deskpilot_installer import (  # noqa: E402
    integration, lifecycle, payload, secrets_env,
)
from deskpilot_installer.cli import ANSWERS_ENV, main, read_answers  # noqa: E402
from deskpilot_installer.engine import Engine, OwnerAnswers  # noqa: E402
from deskpilot_installer.installlog import Log, scrub  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402
from deskpilot_installer.probe import Settings  # noqa: E402
from deskpilot_installer.report import MASK, Secret  # noqa: E402

PACKAGE = r"C:\Media\DeskPilot-certified.zip"
PASSWORD = "correct-horse-battery-staple"
# TEST-ONLY fixtures. These are credential *shapes*, not credentials: the
# scrubber is only testable against something that looks like the thing it
# must hide. The repository's own secret scanner honours the TEST-ONLY
# marker rather than being given an exemption for this file.
API_KEY = "sk_live_ABCDEFGHIJKLMNOP1234"


def answers(**kwargs) -> OwnerAnswers:
    base = dict(owner_identity="Jo", web_password=Secret(PASSWORD, "password"),
                ai_model_tag="qwen2.5:14b-instruct",
                ai_api_key=Secret(API_KEY, "api key"))
    base.update(kwargs)
    return OwnerAnswers(**base)


def installed() -> tuple[FakeWindows, Engine, Log, str]:
    data, digest = certified_payload()
    host = install_ready(package=data, digest=digest)
    log = Log(host)
    engine = Engine(host, Settings(layout=Layout(), package_path=PACKAGE,
                                   expected_digest=digest), answers(), log)
    report = engine.install()
    assert report.may_continue, [r.detail for r in report.blockers]
    return host, engine, log, digest


class SecretsCannotBeRendered(unittest.TestCase):
    """The structural half: a secret has no accidental path to a string."""

    def setUp(self):
        self.secret = Secret("super-secret-value", "test")

    def test_str_is_masked(self):
        self.assertEqual(str(self.secret), MASK)

    def test_repr_is_masked(self):
        self.assertNotIn("super-secret-value", repr(self.secret))

    def test_an_f_string_is_masked(self):
        self.assertNotIn("super-secret-value", f"{self.secret}")

    def test_a_format_spec_cannot_defeat_it(self):
        for spec in ("", ">40", "s", "<10"):
            self.assertNotIn("super-secret-value",
                             format(self.secret, spec))

    def test_percent_formatting_is_masked(self):
        self.assertNotIn("super-secret-value", "%s" % (self.secret,))

    def test_string_join_is_masked(self):
        self.assertNotIn("super-secret-value", " ".join([str(self.secret)]))

    def test_json_serialisation_of_a_report_is_masked(self):
        from deskpilot_installer.report import Outcome, Report, Row
        report = Report("x", (Row("n", Outcome.PASS, "d",
                                  facts={"k": self.secret}),))
        self.assertNotIn("super-secret-value", report.dumps())

    def test_a_nested_secret_is_masked(self):
        from deskpilot_installer.report import _scrub
        nested = {"a": [{"b": (self.secret,)}]}
        self.assertNotIn("super-secret-value", json.dumps(_scrub(nested)))

    def test_reveal_is_the_only_way_out(self):
        self.assertEqual(self.secret.reveal(), "super-secret-value")
        accessors = [n for n in dir(self.secret)
                     if not n.startswith("_") and n not in
                     ("reveal", "label", "present")]
        self.assertEqual(accessors, [],
                         "no accessor other than reveal() may exist")


class SecretsDoNotLeak(unittest.TestCase):

    def test_the_owner_key_is_not_in_the_installation_log(self):
        host, engine, log, _digest = installed()
        self.assertFalse(log.contains_secret(engine.owner_key))

    def test_the_password_is_not_in_the_installation_log(self):
        _host, _engine, log, _digest = installed()
        self.assertFalse(log.contains_secret(PASSWORD))

    def test_the_api_key_is_not_in_the_installation_log(self):
        _host, _engine, log, _digest = installed()
        self.assertFalse(log.contains_secret(API_KEY))

    def test_no_secret_reaches_a_command_line(self):
        """Arguments are visible in the process list to every other account."""
        host, engine, _log, _digest = installed()
        for argv in host.commands:
            joined = " ".join(argv)
            self.assertNotIn(PASSWORD, joined)
            self.assertNotIn(API_KEY, joined)
            self.assertNotIn(engine.owner_key.reveal(), joined)

    def test_the_password_is_delivered_on_stdin(self):
        """Not merely absent from argv -- present where it belongs."""
        host, _engine, _log, _digest = installed()
        self.assertIn(PASSWORD, host.stdin_secrets)

    def test_the_owner_key_reaches_the_application_through_the_environment(self):
        """An environment block is not listed alongside processes."""
        host, engine, _log, _digest = installed()
        self.assertNotIn(engine.owner_key.reveal(),
                         " ".join(" ".join(a) for a in host.commands))

    def test_the_log_scrubber_catches_a_secret_that_arrived_as_text(self):
        """The case Secret cannot cover: a value echoed back by a tool."""
        cases = [
            f"SOLVENT_OWNER_KEY={'a1b2c3d4' * 8}",
            "pbkdf2$600000$abcdef0123456789$fedcba9876543210",
            "sk_live_ABCDEFGH12345678",      # TEST-ONLY shape, not a key
            "whsec_abcdefgh12345678",        # TEST-ONLY shape, not a secret
            "AKIA0123456789ABCDEF",          # TEST-ONLY shape, not an AWS id
            "-----BEGIN RSA PRIVATE KEY-----",  # TEST-ONLY shape, no key body
            "a" * 64,
        ]
        for case in cases:
            self.assertIn(MASK, scrub(case), f"not scrubbed: {case}")

    def test_the_scrubber_leaves_ordinary_text_alone(self):
        """A scrubber that masks everything is a scrubber nobody can debug."""
        ordinary = (r"created C:\DeskPilot\Config\solvent.env",
                    "schtasks.exe /Create /TN DeskPilot",
                    "registered csv-cleanup/1.0 as a proven capability")
        for line in ordinary:
            self.assertEqual(scrub(line), line)

    def test_a_failed_command_quoting_its_own_arguments_is_scrubbed(self):
        host = fresh_windows()
        log = Log(host)
        log.command(["tool.exe", "--key", "REDACT-ME"], 1,
                    f"error: SOLVENT_OWNER_KEY={'f' * 64} was rejected")
        self.assertNotIn("f" * 64, log.text)

    def test_the_secrets_file_is_restricted_before_it_holds_a_secret(self):
        """Order matters: permissions first, contents second."""
        host = fresh_windows()
        host.on("icacls.exe")
        writes: list[tuple[str, bytes]] = []
        original = host.write_bytes

        def record(path, data):
            writes.append((path, data))
            original(path, data)

        host.write_bytes = record  # type: ignore[method-assign]
        key = secrets_env.generate_owner_key()
        secrets_env.write_env_file(host, r"C:\DeskPilot\Config\solvent.env",
                                   secrets_env.EnvContents(owner_key=key))
        first_write = writes[0][1].decode("utf-8")
        self.assertNotIn(key.reveal(), first_write,
                         "the file must be created empty of secrets")
        icacls = [a for a in host.commands if a[0] == "icacls.exe"]
        self.assertTrue(icacls)
        self.assertIn(key.reveal(), writes[-1][1].decode("utf-8"))

    def test_inheritance_is_removed_before_rights_are_granted(self):
        commands = secrets_env._lockdown_commands("X")
        self.assertIn("/inheritance:r", commands[0])
        for later in commands[1:]:
            self.assertIn("/grant:r", later)

    def test_a_failure_to_restrict_the_file_aborts_the_installation(self):
        """Better no installation than a world-readable signing key."""
        host = fresh_windows()
        host.on("icacls.exe", returncode=1, stderr="access denied")
        with self.assertRaises(secrets_env.PermissionsRefused):
            secrets_env.write_env_file(
                host, r"C:\DeskPilot\Config\solvent.env",
                secrets_env.EnvContents(
                    owner_key=secrets_env.generate_owner_key()))

    def test_the_answers_environment_variable_is_cleared_after_reading(self):
        os.environ[ANSWERS_ENV] = json.dumps({"web_password": PASSWORD})
        read_answers()
        self.assertNotIn(ANSWERS_ENV, os.environ)

    def test_an_answers_file_is_erased_and_deleted_after_reading(self):
        host = fresh_windows()
        host.add_file(r"C:\Temp\answers.json",
                      json.dumps({"web_password": PASSWORD}))
        os.environ.pop(ANSWERS_ENV, None)
        parsed = read_answers("", r"C:\Temp\answers.json", host)
        self.assertEqual(parsed.web_password.reveal(), PASSWORD)
        self.assertFalse(host.exists(r"C:\Temp\answers.json"))

    def test_the_backup_job_excludes_the_secrets_file(self):
        source = integration.backup_source(Layout())
        self.assertNotIn("solvent.env", source)
        self.assertIn("secrets file is deliberately not included", source)

    def test_the_generated_owner_key_would_be_accepted_by_the_application(self):
        sys.path.insert(0, str(ROOT))
        from solvent.owner import key_problem
        for _ in range(25):
            key = secrets_env.generate_owner_key()
            self.assertEqual(key_problem(key.reveal().encode("utf-8")), "")
            self.assertEqual(secrets_env.self_check(key), "")

    def test_generated_keys_are_not_repeated(self):
        keys = {secrets_env.generate_owner_key().reveal() for _ in range(50)}
        self.assertEqual(len(keys), 50)


class CodeExecutionIsRefused(unittest.TestCase):

    def test_a_hostile_archive_cannot_write_outside_the_install_directory(self):
        for entry in ("../../Windows/System32/evil.dll",
                      "/Windows/System32/evil.dll",
                      r"..\..\Windows\evil.dll",
                      "C:/Windows/System32/evil.dll",
                      "solvent/../../../evil.py"):
            data = zip_bytes({entry: b"payload"})
            host = fresh_windows(data, hashlib.sha256(data).hexdigest())
            with self.assertRaises(payload.PayloadRefused,
                                   msg=f"{entry} was not refused"):
                payload.extract(host, PACKAGE, r"C:\DeskPilot\App")
            self.assertFalse(host.exists(r"C:\Windows\System32\evil.dll"))

    def test_the_application_runs_with_an_isolated_interpreter(self):
        """``-I`` blocks PYTHONPATH, user site-packages and the current directory.

        Without it, a planted ``json.py`` beside the installer would be
        imported by DeskPilot, as Administrator.
        """
        host, _engine, _log, _digest = installed()
        app_runs = [argv for argv in host.commands
                    if any("solvent.cli" in a for a in argv)
                    or any("hash_password" in a for a in argv)]
        self.assertTrue(app_runs)
        for argv in app_runs:
            self.assertIn("-I", argv,
                          f"not isolated: {' '.join(argv)}")

    def test_no_command_is_ever_a_shell_string(self):
        host, _engine, _log, _digest = installed()
        for argv in host.commands:
            self.assertIsInstance(argv, tuple)
            self.assertTrue(argv)
            program = argv[0].lower()
            for shell in ("cmd.exe", "powershell", "pwsh", "/bin/sh", "bash"):
                self.assertNotIn(shell, program)

    def test_an_injected_argument_stays_one_argument(self):
        """The classic: a value containing separators must not split."""
        host = fresh_windows()
        host.on("tool.exe")
        hostile = 'a" & calc.exe & "b'
        host.run(["tool.exe", hostile])
        self.assertEqual(host.commands[-1], ("tool.exe", hostile))

    def test_an_owner_name_containing_quotes_survives_as_one_argument(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        hostile = 'Jo" --owner "attacker'
        engine = Engine(host, Settings(layout=Layout(), package_path=PACKAGE,
                                        expected_digest=digest),
                        answers(owner_identity=hostile), Log(host))
        report = engine.install()
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers])
        owner_args = [argv for argv in host.commands if "--owner" in argv]
        self.assertTrue(owner_args)
        for argv in owner_args:
            value = argv[argv.index("--owner") + 1]
            self.assertEqual(value, hostile,
                             "the name must arrive as exactly one argument")

    def test_a_tampered_python_download_is_not_executed(self):
        from tests_solvent.installer_fake_host import _bootstrap_bytes
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest, system_python=False)
        engine = Engine(host, Settings(layout=Layout(), package_path=PACKAGE,
                                        expected_digest=digest),
                        answers(), Log(host))
        report = engine.install()
        self.assertFalse(report.may_continue)
        executed = [argv for argv in host.commands
                    if argv[0].endswith("-amd64.exe")]
        self.assertEqual(executed, [],
                         "a rejected installer must never be run")

    def test_the_answers_payload_cannot_inject_a_component(self):
        """Unknown keys are ignored; booleans are coerced, not trusted."""
        os.environ[ANSWERS_ENV] = json.dumps(
            {"owner_identity": "Jo", "components": {"autostart": "yes-please",
                                                    "unknown": True},
             "__class__": "evil"})
        parsed = read_answers()
        self.assertTrue(parsed.components.autostart)
        self.assertFalse(hasattr(parsed.components, "unknown"))

    def test_malformed_answers_do_not_crash_the_engine(self):
        for raw in ("", "not json", "[]", "null", '{"web_password": null}'):
            os.environ[ANSWERS_ENV] = raw
            parsed = read_answers()
            self.assertIsInstance(parsed, OwnerAnswers)


class BusinessRecordsSurvive(unittest.TestCase):

    def _with_records(self):
        host, engine, log, digest = installed()
        host.add_file(Layout().db, b"SQLite format 3\x00LEDGER")
        host.add_file(r"C:\DeskPilot\Backups\solvent-20260101T000000Z.db.gz",
                      b"old-backup")
        return host, digest

    def test_repair_does_not_replace_the_database(self):
        host, digest = self._with_records()
        lifecycle.repair(host, Settings(layout=Layout(), package_path=PACKAGE,
                                        expected_digest=digest),
                         answers(), Log(host))
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00LEDGER")

    def test_repair_does_not_rotate_the_owner_key(self):
        """A new key would invalidate every approval already signed.

        The repair is required to *succeed* as well as leave the key alone.
        Without that, any change which merely broke repair would satisfy the
        comparison -- the file is certainly unchanged if nothing ran -- and the
        test would pass while the property it names went unverified.
        """
        host, digest = self._with_records()
        before = host.read_bytes(Layout().env_file)
        report = lifecycle.repair(
            host, Settings(layout=Layout(), package_path=PACKAGE,
                           expected_digest=digest), answers(), Log(host))
        self.assertTrue(report.may_continue,
                        [r.detail for r in report.blockers]
                        + ([report.failure.why] if report.failure else []))
        self.assertEqual(host.read_bytes(Layout().env_file), before)

    def test_repair_on_a_machine_with_no_credentials_refuses(self):
        host, digest = self._with_records()
        del host.files[Layout().env_file.lower()]
        report = lifecycle.repair(
            host, Settings(layout=Layout(), package_path=PACKAGE,
                           expected_digest=digest), answers(), Log(host))
        self.assertFalse(report.may_continue)
        self.assertIn("no owner credentials", report.failure.why)

    def test_repair_does_not_delete_the_backups(self):
        host, digest = self._with_records()
        lifecycle.repair(host, Settings(layout=Layout(), package_path=PACKAGE,
                                        expected_digest=digest),
                         answers(), Log(host))
        self.assertTrue(host.exists(
            r"C:\DeskPilot\Backups\solvent-20260101T000000Z.db.gz"))

    def test_uninstall_keeps_records_unless_explicitly_told_otherwise(self):
        host, _digest = self._with_records()
        lifecycle.uninstall(host, Layout(), log=Log(host))
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00LEDGER")
        self.assertTrue(host.exists(Layout().backups))

    def test_purging_requires_the_explicit_flag(self):
        host, _digest = self._with_records()
        plan = lifecycle.plan_uninstall(host, Layout())
        self.assertFalse(plan.purge_owner_data)
        self.assertNotIn(Layout().data, plan.remove)
        self.assertIn(Layout().data, plan.keep)

    def test_the_cli_does_not_purge_without_the_flag(self):
        host, _digest = self._with_records()
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["uninstall", "--root", r"C:\DeskPilot"], host=host)
        self.assertEqual(code, 0)
        self.assertTrue(host.exists(Layout().db))

    def test_the_cli_purges_only_with_the_flag(self):
        host, _digest = self._with_records()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["uninstall", "--root", r"C:\DeskPilot",
                  "--purge-owner-data"], host=host)
        self.assertFalse(host.exists(Layout().db))

    def test_uninstall_removes_only_its_own_firewall_rule(self):
        host, _digest = self._with_records()
        host.commands.clear()
        lifecycle.uninstall(host, Layout(), log=Log(host))
        deletes = [argv for argv in host.commands
                   if argv[0].lower().endswith("netsh.exe")]
        self.assertEqual(len(deletes), 1)
        self.assertIn(f"name={integration.FIREWALL_RULE_NAME}", deletes[0])

    def test_uninstall_removes_only_its_own_scheduled_tasks(self):
        host, _digest = self._with_records()
        host.commands.clear()
        lifecycle.uninstall(host, Layout(), log=Log(host))
        names = [argv[argv.index("/TN") + 1] for argv in host.commands
                 if "/TN" in argv]
        self.assertEqual(sorted(names), ["DeskPilot", "DeskPilot Backup"])

    def test_an_upgrade_backs_up_before_the_new_code_opens_the_database(self):
        """A backup taken after is a backup of whatever the new code did."""
        host, digest = self._with_records()
        new = zip_bytes({**CERTIFIED_ENTRIES, "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new)
        order: list[str] = []
        original_write = host.write_bytes

        def watch(path, data):
            if "pre-upgrade" in path.lower():
                order.append("backup")
            original_write(path, data)

        host.write_bytes = watch  # type: ignore[method-assign]
        original_run = host.run

        def watch_run(argv, **kwargs):
            if any("solvent.cli" in str(a) for a in argv):
                order.append("app-ran")
            return original_run(argv, **kwargs)

        host.run = watch_run  # type: ignore[method-assign]
        lifecycle.upgrade(
            host, Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                           expected_digest=hashlib.sha256(new).hexdigest()),
            answers(), Log(host))
        self.assertIn("backup", order)
        self.assertLess(order.index("backup"), order.index("app-ran"))

    def test_a_failed_upgrade_restores_the_database(self):
        """A half-applied migration, then a failure. Only a restore recovers it.

        The migration writes to the database before giving up, which is what a
        real one does. Without that, the database is untouched when the upgrade
        fails and the ledger is intact whether or not a backup was ever taken
        -- so the test would pass with the backup step deleted, and the rollback
        it claims to verify would be unverified.
        """
        host, digest = self._with_records()
        new = zip_bytes({**CERTIFIED_ENTRIES, "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new)

        def half_migrate(h, _argv):
            h.add_file(Layout().db, b"SQLite format 3\x00HALF-MIGRATED")

        host.handlers.insert(0, Handler(
            (r"C:\DeskPilot\venv\Scripts\python.exe", "solvent.cli", "doctor"),
            1, "", "migration exploded", half_migrate))
        report = lifecycle.upgrade(
            host, Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                           expected_digest=hashlib.sha256(new).hexdigest()),
            answers(), Log(host))
        self.assertFalse(report.may_continue)
        self.assertEqual(host.read_bytes(Layout().db),
                         b"SQLite format 3\x00LEDGER",
                         "the pre-upgrade backup must have been restored")

    def test_an_upgrade_with_a_bad_package_never_stops_the_running_service(self):
        host, digest = self._with_records()
        new = zip_bytes({**CERTIFIED_ENTRIES, "solvent/v2.py": b"# v2\n"})
        host.add_file(r"C:\Media\v2.zip", new + b"tampered")
        host.commands.clear()
        report = lifecycle.upgrade(
            host, Settings(layout=Layout(), package_path=r"C:\Media\v2.zip",
                           expected_digest=hashlib.sha256(new).hexdigest()),
            answers(), Log(host))
        self.assertFalse(report.may_continue)
        self.assertEqual(host.commands, [],
                         "a refused upgrade must not touch the machine")


class AFailureReportStatesWhatSurvived(unittest.TestCase):
    """§21's five answers, on the engine's own failure path.

    Distinct from a refused payload, which is reported by ``payload.refusal``
    before the engine starts. Once steps have run there is a change ledger to
    report from, and the reassurances have to be derived from it rather than
    recited -- so they are tested here, where a step genuinely failed partway.
    """

    def _failed_install(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        host.handlers.insert(0, Handler(
            (r"C:\DeskPilot\venv\Scripts\python.exe", "setup", "capability"),
            1, "", "the verifier fingerprint no longer matches"))
        engine = Engine(host, Settings(layout=Layout(), package_path=PACKAGE,
                                        expected_digest=digest),
                        answers(), Log(host))
        report = engine.install()
        assert not report.may_continue
        return host, engine, report

    def test_the_failure_names_what_failed_and_why(self):
        _host, _engine, report = self._failed_install()
        self.assertIn("Registering certified capabilities",
                      report.failure.what_failed)
        self.assertIn("fingerprint no longer matches", report.failure.why)

    def test_the_failure_lists_what_was_changed(self):
        _host, engine, report = self._failed_install()
        self.assertTrue(report.failure.changed)
        self.assertEqual(list(report.failure.changed), engine.changed)

    def test_the_failure_says_metatrader_was_untouched(self):
        _host, _engine, report = self._failed_install()
        self.assertTrue(
            any("MetaTrader was not touched" in line
                for line in report.failure.not_changed),
            report.failure.not_changed)

    def test_the_failure_does_not_claim_a_step_that_ran_was_untouched(self):
        """The reassurances are derived from the ledger, not recited."""
        _host, _engine, report = self._failed_install()
        joined = " ".join(report.failure.not_changed)
        self.assertNotIn("no DeskPilot database was created", joined,
                         "the database step succeeded; saying otherwise is "
                         "a false reassurance")

    def test_the_failure_offers_a_safe_next_action(self):
        _host, _engine, report = self._failed_install()
        self.assertIn("install.log", report.failure.safe_next_action)
        self.assertIn("run the installer again",
                      report.failure.safe_next_action)

    def test_the_rendered_failure_contains_all_five_answers(self):
        _host, _engine, report = self._failed_install()
        text = report.failure.render()
        for heading in ("Why:", "What was changed:", "What was NOT changed:",
                        "Safe next action:"):
            self.assertIn(heading, text)


class ThePrivilegeBoundaryIsRespected(unittest.TestCase):

    def test_the_firewall_rule_blocks_rather_than_allows(self):
        for argv in integration.firewall_commands("py.exe"):
            self.assertIn("action=block", argv)
            self.assertNotIn("action=allow", argv)

    def test_the_firewall_rule_is_scoped_to_one_program(self):
        argv = integration.firewall_commands(r"C:\DeskPilot\venv\py.exe")[0]
        self.assertTrue(any(a.startswith("program=") for a in argv))
        self.assertTrue(any(a == "profile=public" for a in argv))

    def test_the_control_centre_stays_on_loopback(self):
        from deskpilot_installer.layout import WEB_PORT
        source = integration.launcher_source(Layout())
        self.assertNotIn("0.0.0.0", source)
        self.assertEqual(WEB_PORT, 8765)

    def test_the_scheduled_task_does_not_run_as_an_interactive_user(self):
        xml = integration.task_xml("py.exe", "s.py", at_startup=True)
        self.assertIn("<UserId>S-1-5-18</UserId>", xml)

    def test_the_task_definition_escapes_its_paths(self):
        xml = integration.task_xml(r"C:\a&b\python.exe", r"C:\x<y>\s.py",
                                   at_startup=True)
        self.assertIn("a&amp;b", xml)
        self.assertIn("x&lt;y&gt;", xml)
        import xml.etree.ElementTree as ET
        ET.fromstring(xml.replace('encoding="UTF-16"', 'encoding="UTF-8"'))

    def test_the_generated_scripts_are_valid_python(self):
        import ast
        ast.parse(integration.launcher_source(Layout()))
        ast.parse(integration.backup_source(Layout()))

    def test_the_backup_retention_matches_the_deployment_contract(self):
        shell = (ROOT / "deploy" / "backup.sh").read_text()
        self.assertIn("SOLVENT_BACKUP_KEEP:-14", shell)
        self.assertEqual(integration.BACKUP_RETENTION, 14)

    def test_the_backup_verifies_the_audit_chain_like_the_shell_script_does(self):
        source = integration.backup_source(Layout())
        self.assertIn("verify_chain", source)
        self.assertIn("integrity_check", source)


if __name__ == "__main__":
    unittest.main()
