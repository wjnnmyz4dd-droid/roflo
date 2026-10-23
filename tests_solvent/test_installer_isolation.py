"""The control centre's Windows identity, and whether the boundary holds.

Two kinds of test live here, and the difference matters.

The first kind checks what the installer *does*: which identity each scheduled
task logs on as, which files the control centre's launcher opens, that the
probe task does not outlive the installation. Those are facts about this
repository and they are settled here.

The second kind checks whether the access controls the installer applies would
actually refuse the control centre. That is answered through
:mod:`tests_solvent.installer_acl_model`, a small reimplementation of the parts
of NTFS access control the installer depends on. A pass means *the rules imply
the refusals*. It does not mean Windows agrees, and no test on a Linux machine
can mean that. What settles it is the installer's own probe, which runs on the
owner's computer as the real identity and refuses to report success otherwise.

The most important test in the file is
:meth:`TheProbeWouldNoticeAMissingBoundary.test_an_install_whose_rules_were_not_applied_is_refused`.
Without it, every other test here could pass against a probe that answered
"denied" to everything, which is the mistake a previous FakeHost made with
``schtasks /Query`` -- and that one hid a service that had never been
registered.
"""

from __future__ import annotations

import json
import ntpath
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from deskpilot_installer import integration, isolation  # noqa: E402
from deskpilot_installer.engine import Engine  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402

from tests_solvent import installer_acl_model as acl  # noqa: E402
from tests_solvent import installer_fake_host as fake  # noqa: E402
from tests_solvent.test_installer_matrix import (  # noqa: E402
    Scenario, certified_payload, install_ready,
)


class EachServiceLogsOnAsItsOwnIdentity(Scenario):

    def installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        return host, engine, report

    def test_the_isolation_step_ran_and_passed_every_check(self):
        _host, engine, report = self.installed()
        row = report.row("Restricting the control centre")
        self.assertIsNotNone(row, "the installer did not confine anything")
        self.assertEqual(row.outcome.value, "PASS", row.detail)
        self.assertEqual(len(engine.isolation_findings),
                         len(isolation.PROBE_CHECKS))
        self.assertTrue(all(f.held for f in engine.isolation_findings))

    def test_every_required_check_was_actually_attempted(self):
        """A probe that silently skipped a check would leave the installer
        reporting a boundary nobody tested. ``judge`` counts an unreported
        check as a failure, and this is the test that keeps it honest."""
        _host, engine, _report = self.installed()
        attempted = {f.name for f in engine.isolation_findings}
        self.assertEqual(attempted, {n for n, _, _ in isolation.PROBE_CHECKS})

    def test_the_probe_task_does_not_outlive_the_installation(self):
        host, _engine, _report = self.installed()
        self.assertNotIn(Engine.PROBE_TASK_NAME, host.scheduled_tasks,
                         "a task running as LOCAL SERVICE was left behind")

    def test_the_probes_own_files_are_cleaned_up(self):
        """Including the request it plants, which is a fake intent sitting in
        the real spool. The runtime would otherwise try to carry it out."""
        host, _engine, _report = self.installed()
        layout = Layout()
        for leftover in (ntpath.join(layout.spool, "probe-planted.intent"),
                         ntpath.join(layout.spool, "probe-submission.intent"),
                         ntpath.join(layout.config, "isolation-probe.xml")):
            self.assertFalse(host.exists(leftover), leftover)

    def test_the_fake_recognises_the_task_the_engine_registers(self):
        """Kept in step deliberately: a fake that stopped recognising the
        probe task would stop answering it, the installer would time out, and
        the only visible symptom would be a slow install."""
        self.assertEqual(fake.PROBE_TASK_NAME, Engine.PROBE_TASK_NAME)


class TheProbeWouldNoticeAMissingBoundary(Scenario):
    """The negative controls. Each removes one protection and expects refusal.

    These are what make the rest of this file worth reading.
    """

    def install_without(self, *paths: str):
        """Install with ``icacls`` silently ignored for the given paths."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        skip = {ntpath.normcase(ntpath.normpath(p)) for p in paths}
        original = host.acl.icacls

        def selective(argv):
            if len(argv) > 1 and ntpath.normcase(
                    ntpath.normpath(argv[1])) in skip:
                return True          # the command "succeeded" and did nothing
            return original(argv)

        host.acl.icacls = selective    # type: ignore[method-assign]
        engine, report, _log = self.install(host, digest)
        return host, engine, report

    def test_an_install_whose_rules_were_not_applied_is_refused(self):
        layout = Layout()
        _host, _engine, report = self.install_without(
            layout.config, layout.web_env_file, layout.data, layout.db,
            layout.spool, layout.app, layout.venv, layout.engine,
            layout.backups, layout.logs)
        self.assertFalse(report.may_continue,
                         "an installation with no access controls applied "
                         "reported success")
        self.assertRefused(report, because="did not hold when tested")

    def install_with_extra(self, *grants: tuple[str, str]):
        """Install, then let one over-permissive grant through as well.

        The other direction from :meth:`install_without`, and the one that
        matches most real mistakes: a rule that grants too much rather than a
        rule nobody ran. Skipping the spool's rule, for instance, does not
        open the spool -- it takes away the add-file grant and breaks the
        control centre, which the probe also notices but for another reason.
        """
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        original = host.acl.icacls

        def generous(argv):
            applied = original(argv)
            for path, spec in grants:
                if len(argv) > 1 and ntpath.normcase(
                        ntpath.normpath(argv[1])) == ntpath.normcase(
                        ntpath.normpath(path)):
                    original(["icacls.exe", path, "/grant", spec])
            return applied

        host.acl.icacls = generous   # type: ignore[method-assign]
        engine, report, _log = self.install(host, digest)
        return host, engine, report

    def test_leaving_the_owner_key_readable_is_refused_by_name(self):
        """The key is protected twice: once by the Config directory's rule and
        once by the secrets file's own lockdown. Both have to be defeated, and
        that it takes both is worth knowing."""
        layout = Layout()
        _host, _engine, report = self.install_without(layout.config,
                                                      layout.env_file)
        self.assertFalse(report.may_continue)
        self.assertRefused(report, because="read_owner_key")

    def test_a_spool_that_can_be_listed_is_refused_by_name(self):
        _host, _engine, report = self.install_with_extra(
            (Layout().spool, f"{isolation.WEB_SID}:(OI)(CI)(RD)"))
        self.assertFalse(report.may_continue)
        self.assertRefused(report, because="list_spool")

    def test_a_submitted_request_that_can_be_read_back_is_refused(self):
        """What the OWNER RIGHTS entry is for. Grant the creator its implicit
        rights back and the control centre can read what it submitted."""
        _host, _engine, report = self.install_with_extra(
            (Layout().spool, f"{isolation.OWNER_RIGHTS_SID}:(OI)(IO)(RD,WD)"))
        self.assertFalse(report.may_continue)
        self.assertRefused(report, because="read_own_submission")

    def test_a_database_left_writable_is_refused_by_name(self):
        _host, _engine, report = self.install_with_extra(
            (Layout().db, f"{isolation.WEB_SID}:(WD)"))
        self.assertFalse(report.may_continue)
        self.assertRefused(report, because="write_database")

    def test_application_code_left_writable_is_refused_by_name(self):
        _host, _engine, report = self.install_with_extra(
            (Layout().app, f"{isolation.WEB_SID}:(OI)(CI)(M)"))
        self.assertFalse(report.may_continue)
        self.assertRefused(report, because="write_application_code")

    def test_a_probe_that_produces_nothing_is_not_a_pass(self):
        """Silence is the failure mode to fear: a probe that never ran looks
        exactly like a probe that found nothing wrong."""
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        # Inserted at the front, because ``on`` appends and the handler that
        # actually runs the probe is already registered.
        host.handlers.insert(0, fake.Handler(
            ("schtasks.exe", "/Run"), 0, "", ""))
        engine, report, _log = self.install(host, digest)
        self.assertFalse(report.may_continue)
        self.assertRefused(report, because="nothing was proved")


class WhatTheRulesImply(unittest.TestCase):
    """The access-control rules read directly, without an installation.

    Each case is one sentence from the requirement, turned into a question the
    model can answer.
    """

    def setUp(self):
        self.layout = Layout()
        self.model = acl.AclModel()
        for rule in isolation.rules(self.layout):
            self.model.icacls(rule.argv())
        self.web = isolation.WEB_SID

    def may(self, path, capability, **kwargs):
        return self.model.may(path, self.web, capability, **kwargs)

    def test_the_web_process_cannot_read_the_owner_signing_key(self):
        self.assertFalse(self.may(self.layout.env_file, acl.READ))

    def test_the_web_process_can_read_its_own_password_hash(self):
        self.assertTrue(self.may(self.layout.web_env_file, acl.READ))

    def test_the_web_process_cannot_write_its_own_password_hash(self):
        """Reading a hash is how it checks a password. Writing one would be
        how it sets a new password without the owner."""
        self.assertFalse(self.may(self.layout.web_env_file, acl.WRITE))

    def test_the_web_process_cannot_modify_the_database(self):
        self.assertFalse(self.may(self.layout.db, acl.WRITE))

    def test_the_web_process_can_read_the_database(self):
        self.assertTrue(self.may(self.layout.db, acl.READ))

    def test_the_web_process_cannot_append_to_the_write_ahead_log(self):
        """Appending committed frames to the WAL is writing rows by another
        name, so a read grant on the database is worth nothing without this."""
        self.assertFalse(self.may(self.layout.db + "-wal", acl.WRITE))

    def test_the_web_process_cannot_list_the_spool(self):
        self.assertFalse(self.may(self.layout.spool, acl.LIST, is_dir=True))

    def test_the_web_process_can_add_a_request_to_the_spool(self):
        self.assertTrue(self.may(self.layout.spool, acl.ADD_FILE, is_dir=True))

    def test_the_web_process_cannot_delete_a_pending_request(self):
        self.assertFalse(self.may(self.layout.spool, acl.DELETE_CHILD,
                                  is_dir=True))
        self.assertFalse(self.may(ntpath.join(self.layout.spool, "a.intent"),
                                  acl.DELETE))

    def test_the_web_process_cannot_read_a_request_it_submitted(self):
        """The OWNER RIGHTS entry is the whole mechanism. A process owns what
        it creates, and an owner holds implicit read and write unless an
        OWNER RIGHTS ace replaces them."""
        submitted = ntpath.join(self.layout.spool, "mine.intent")
        self.assertFalse(self.may(submitted, acl.READ, created_by=self.web))
        self.assertFalse(self.may(submitted, acl.WRITE, created_by=self.web))

    def test_without_the_owner_rights_entry_it_could_read_it_back(self):
        """The negative control for the rule above: this is what the model
        says happens when that one ACE is missing, which is why it is there."""
        bare = acl.AclModel()
        bare.icacls(["icacls.exe", self.layout.spool, "/inheritance:r",
                     "/grant:r", f"{isolation.WEB_SID}:(WD,AD,X,RA)"])
        submitted = ntpath.join(self.layout.spool, "mine.intent")
        self.assertTrue(bare.may(submitted, isolation.WEB_SID, acl.READ,
                                 created_by=isolation.WEB_SID))

    def test_the_web_process_cannot_alter_the_application_code(self):
        self.assertFalse(self.may(self.layout.app, acl.ADD_FILE, is_dir=True))
        self.assertFalse(self.may(ntpath.join(self.layout.app, "cli.py"),
                                  acl.WRITE))

    def test_the_web_process_can_run_the_application_code(self):
        self.assertTrue(self.may(ntpath.join(self.layout.app, "cli.py"),
                                 acl.READ))
        self.assertTrue(self.may(ntpath.join(self.layout.venv, "python.exe"),
                                 acl.EXECUTE))

    def test_the_web_process_cannot_reach_the_engine_or_the_backups(self):
        self.assertFalse(self.may(self.layout.engine, acl.LIST, is_dir=True))
        self.assertFalse(self.may(self.layout.backups, acl.LIST, is_dir=True))

    def test_the_web_process_cannot_read_the_install_state(self):
        self.assertFalse(self.may(self.layout.state_file, acl.READ))

    def test_the_web_process_can_write_its_own_log(self):
        self.assertTrue(self.may(self.layout.logs, acl.ADD_FILE, is_dir=True))

    def test_every_rule_names_its_identity_by_sid(self):
        for rule in isolation.rules(self.layout):
            for token in rule.args:
                if ":(" not in token:
                    continue
                self.assertTrue(token.startswith("*S-1-"),
                                f"{token} names an account by name, and a "
                                f"localised Windows calls it something else")

    def test_every_rule_explains_itself(self):
        for rule in isolation.rules(self.layout):
            self.assertTrue(rule.why.strip(), rule.path)


class TheJudgeIsNotGenerous(unittest.TestCase):

    def test_a_check_the_probe_never_reported_is_a_failure(self):
        findings, broken = isolation.judge({})
        self.assertEqual(len(findings), len(isolation.PROBE_CHECKS))
        self.assertEqual(len(broken), len(isolation.PROBE_CHECKS))

    def test_an_operation_that_should_be_refused_and_succeeded_fails(self):
        _findings, broken = isolation.judge(
            {"read_owner_key": (True, "succeeded")})
        self.assertIn("read_owner_key", broken)

    def test_an_operation_that_should_work_and_was_refused_fails(self):
        """A boundary that also broke the control centre would be reported as
        a success by anything that only looked for refusals."""
        _findings, broken = isolation.judge(
            {"read_database": (False, "PermissionError")})
        self.assertIn("read_database", broken)

    def test_a_malformed_entry_is_a_failure_rather_than_a_crash(self):
        _findings, broken = isolation.judge({"read_owner_key": "denied"})
        self.assertIn("read_owner_key", broken)


class TheProbeScriptIsRunnable(unittest.TestCase):

    def test_the_probe_compiles(self):
        source = isolation.probe_script(
            Layout(), planted=r"C:\x\planted.intent", result=r"C:\x\r.json")
        compile(source, "isolation-probe.py", "exec")

    def test_the_probe_names_this_installations_paths(self):
        layout = Layout()
        source = isolation.probe_script(layout, planted=r"C:\x\p.intent",
                                        result=r"C:\x\r.json")
        for path in (layout.env_file, layout.web_env_file, layout.db,
                     layout.spool, layout.app, layout.engine,
                     layout.state_file, layout.backups):
            self.assertIn(path, source)

    def test_the_probe_writes_no_secret_into_its_result(self):
        """It reports whether a read succeeded, never what was read."""
        source = isolation.probe_script(Layout(), planted="p", result="r")
        self.assertIn("handle.read(16)", source)
        self.assertNotIn("return handle.read()", source)


if __name__ == "__main__":
    unittest.main()


class TheHardeningDoesNotLockOutItsOwnVerifier(unittest.TestCase):
    """The probe must be able to read the script it is asked to run.

    It could not. ``verify_isolation`` wrote ``isolation-probe.py``
    into ``layout.engine``, and ``rules`` denies the control centre's
    identity that directory outright -- "the installation engine can
    install; the website cannot run it". So the task registered,
    started, could not read its own script, died, and wrote nothing.
    The step reported "the probe produced no result, so nothing was
    proved", which was true and said nothing about why; the
    hardening had locked out its own verifier, and three CI runs
    went past it.

    The checks below are about where the probe's files go relative
    to what the identity under test may touch, which is a property
    of the pair and is what went wrong.
    """

    def setUp(self):
        from deskpilot_installer import isolation
        from deskpilot_installer.engine import Engine, OwnerAnswers
        from deskpilot_installer.installlog import Log
        from deskpilot_installer.probe import Settings
        from deskpilot_installer.report import Secret
        from tests_solvent.installer_fake_host import fresh_windows

        self.isolation = isolation
        self.layout = Layout(root=r"C:\DeskPilot")
        host = fresh_windows()
        self.engine = Engine(host, Settings(layout=self.layout),
                             OwnerAnswers(owner_identity="Jo",
                                          web_password=Secret("correct-horse")),
                             Log(host))

    def granted(self, path: str) -> str:
        """The rights the control centre's identity has at ``path``.

        Taken from the rule that names the path, or from the nearest
        rule above it, because an ``icacls`` grant with ``(OI)(CI)``
        reaches the files underneath.
        """
        best, rights = "", ""
        for rule in self.isolation.rules(self.layout):
            if not (path == rule.path
                    or path.lower().startswith(rule.path.lower() + "\\")):
                continue
            if len(rule.path) < len(best):
                continue
            best = rule.path
            rights = ""
            args = list(rule.args)
            for index, arg in enumerate(args[:-1]):
                # ``lstrip`` on the argument, not only on the SID:
                # icacls grants are written ``*S-1-5-19:(M)`` and
                # comparing one spelling against the other matched
                # nothing, so an earlier version of this reported
                # that every path grants the web identity nothing.
                if arg != "/grant:r":
                    continue
                grant = args[index + 1].lstrip("*")
                if grant.startswith(self.isolation.WEB_SID.lstrip("*")):
                    rights = grant.split(":", 1)[1]
        return rights

    def test_the_probe_script_is_somewhere_its_runner_can_read(self):
        script = self.probe_path("isolation-probe.py")
        rights = self.granted(script)
        self.assertTrue(rights, f"no rule grants the web identity {script}")
        self.assertIn("R", rights,
                      f"the probe runs as the web identity and {script} "
                      f"grants it {rights!r}")

    def test_the_probe_script_is_not_in_the_engine_directory(self):
        """Named as the defect so it cannot be reintroduced quietly."""
        script = self.probe_path("isolation-probe.py")
        self.assertFalse(
            script.lower().startswith(self.layout.engine.lower() + "\\"),
            "the engine directory is denied to the identity the probe runs as")

    def test_the_probe_can_write_its_result_where_it_is_asked_to(self):
        result = self.probe_path("isolation-probe.json")
        rights = self.granted(result)
        self.assertTrue(any(letter in rights for letter in ("M", "W", "F")),
                        f"the probe writes its result to {result} and is "
                        f"granted {rights!r} there")

    def probe_path(self, leaf: str) -> str:
        """Where ``verify_isolation`` puts one of its files.

        Read out of the source rather than restated, so this test
        moves when the code does instead of agreeing with a copy.
        """
        import inspect
        import re

        source = inspect.getsource(self.engine.verify_isolation)
        # The whole leaf, extension included. Matching only the stem
        # found the .json line when asked for the .py one, because
        # both are called isolation-probe.
        found = re.search(
            r'ntpath\.join\(self\.layout\.(\w+),\s*"'
            + re.escape(leaf) + r'"\)',
            source)
        self.assertIsNotNone(
            found, f"verify_isolation no longer joins a path for {leaf}")
        return __import__("ntpath").join(
            getattr(self.layout, found.group(1)), leaf)

    def test_the_probe_removes_the_script_it_wrote(self):
        """It did not, and left a generated file in the installed tree."""
        import inspect

        source = inspect.getsource(self.engine.verify_isolation)
        finally_at = source.index("finally:")
        self.assertIn("remove_file(script)", source[finally_at:],
                      "the probe script is left behind after every install")


class RepairRestoresTheConfinement(Scenario):
    """A repair restores the boundary, not only the files.

    An installation repaired without re-applying the access controls would be
    a control centre running as LOCAL SERVICE without the grants that identity
    needs -- so it would start and fail -- or, if the directories had been
    recreated with inherited permissions, one running with no boundary at all.
    Neither is a repaired installation.
    """

    PACKAGE = r"C:\Media\DeskPilot-certified.zip"

    def installed(self):
        data, digest = certified_payload()
        host = install_ready(package=data, digest=digest)
        engine, report, _log = self.install(host, digest)
        self.assertInstalled(host, report)
        return host, digest

    def repair(self, host, digest):
        from deskpilot_installer import lifecycle
        from deskpilot_installer.engine import Settings
        from deskpilot_installer.installlog import Log

        from tests_solvent.test_installer_matrix import answers

        return lifecycle.repair(
            host,
            Settings(layout=Layout(), package_path=self.PACKAGE,
                     expected_digest=digest),
            answers(), Log(host))

    def test_a_repair_applies_the_access_controls_again(self):
        host, digest = self.installed()
        before = len([c for c in host.commands
                      if c and c[0].lower().endswith("icacls.exe")])
        report = self.repair(host, digest)
        self.assertTrue(report.may_continue,
                        [row.detail for row in report.blockers]
                        + ([report.failure.why] if report.failure else []))
        after = len([c for c in host.commands
                     if c and c[0].lower().endswith("icacls.exe")])
        self.assertGreater(after, before,
                           "a repair applied no access controls at all")
        row = report.row("Restoring the control centre's confinement")
        self.assertIsNotNone(row, "a repair did not restore the confinement")
        self.assertEqual(row.outcome.value, "PASS", row.detail)

    def test_a_repair_that_cannot_confine_the_control_centre_refuses(self):
        """Fails closed, like a secrets file that cannot be restricted. The
        alternative is a repair that reports success and leaves the website
        running with whatever the directories happened to inherit."""
        host, digest = self.installed()
        host.handlers.insert(0, fake.Handler(
            ("icacls.exe",), 1, "", "Access is denied"))
        report = self.repair(host, digest)
        self.assertFalse(report.may_continue,
                         "a repair that could not confine anything reported "
                         "success")

    def test_a_repair_restores_the_confinement_before_the_services(self):
        """Order matters: the control centre's identity cannot read the
        database or its own password hash until the grants exist, so a service
        registered first would start a process that could not work."""
        host, digest = self.installed()
        self.repair(host, digest)
        order = [c for c in host.commands if c and (
            c[0].lower().endswith("icacls.exe")
            or (c[0].lower().endswith("schtasks.exe") and "/Create" in c))]
        creates = [i for i, c in enumerate(order)
                   if c[0].lower().endswith("schtasks.exe")
                   and integration.WEB_TASK_NAME in c]
        self.assertTrue(creates, "the control centre was never re-registered")
        icacls_before = [i for i, c in enumerate(order)
                         if c[0].lower().endswith("icacls.exe")
                         and i < creates[-1]]
        self.assertTrue(icacls_before,
                        "the control centre's task was registered before any "
                        "access control was applied")
