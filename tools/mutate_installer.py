#!/usr/bin/env python3
"""Mutation testing for the Windows installer's load-bearing controls.

A test suite that passes proves the code does what the tests check. It does not
prove the tests would notice if the code stopped doing it. This harness breaks
one control at a time and requires a named test to fail, which is the only
evidence that the control is actually guarded.

A kill is counted only when all four conditions hold, in this order:

1. **Baseline green.** The designated tests pass before the mutation. A test
   that was already failing cannot be said to have caught anything.
2. **Mutation applied.** The file on disk really changed. A mutant whose
   pattern no longer matches the source is reported ``NOT_APPLIED`` rather than
   quietly counted as a kill -- this is the failure mode that makes mutation
   reports worthless, because a stale pattern looks exactly like a strong test.
3. **Designated test newly fails.** Not any test: the one named for this
   mutant. A control guarded only by an unrelated collapse elsewhere is not
   guarded.
4. **Attributable.** The failure disappears when the mutation is reverted.

Anything else is reported honestly: ``SURVIVED`` when the control can be broken
with the suite still green, ``NOT_APPLIED`` when the mutant is stale, and
``HARNESS_ERROR`` when the harness itself could not reach a verdict.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "windows" / "installer"
PACKAGE = INSTALLER / "deskpilot_installer"


def source_path(module: str) -> pathlib.Path:
    """Where a mutant's ``module`` lives.

    Mutants used to name a module under ``deskpilot_installer`` and nothing
    else, so the battery could not touch the NSIS wizard -- the half of the
    installer where all three real-world failures actually happened. A name
    with a separator, or one that is not in the package, resolves against
    ``windows/installer`` instead.
    """
    inside = PACKAGE / module
    if inside.is_file():
        return inside
    beside = INSTALLER / module
    if beside.is_file():
        return beside
    # A path relative to the repository root, for the Solvent modules the
    # installer ships but does not own. The runtime is one of them, and a
    # defect in it reaches the owner through this installer like any other.
    return ROOT / module


@dataclasses.dataclass(frozen=True)
class Mutant:
    """One deliberate defect, and the test that must notice it."""

    name: str
    #: Module under ``deskpilot_installer``.
    module: str
    #: Exact source to replace. Must appear exactly once.
    old: str
    #: What to replace it with.
    new: str
    #: The test that must newly fail. ``Class.method`` or a class name.
    designated: str
    #: What control this breaks, for the report.
    control: str


#: The control-centre entry in :data:`BOOT_SERVICES`, as source text.
WEB_ENTRY_OLD = (
    '    BootService(task_name=WEB_TASK_NAME,\n'
    '                script="deskpilot-web.py",\n'
    '                xml="deskpilot-web-task.xml",\n'
    '                argv=web_argv,\n'
    '                purpose="the owner' + chr(39) + 's control centre",\n'
    '                env_allow=(secrets_env.WEB_PASSWORD_HASH_ENV,),\n'
    '                identity=isolation.WEB_SID,\n'
    '                elevated=False,\n'
    '                env_file=lambda layout: layout.web_env_file),\n'
)


MUTANTS: tuple[Mutant, ...] = (
    Mutant("hash-always-matches", "payload.py",
           "matched = hmac.compare_digest(actual, expected)",
           "matched = True",
           "tests_solvent.test_installer_payload.TheDigestIsCompared",
           "certified payload integrity"),
    Mutant("missing-digest-passes", "payload.py",
           '''        return Verdict(False, "", "", path,
                       unreadable="this installer was built without an "
                                  "expected package digest")''',
           '''        return Verdict(True, "", "", path)''',
           "tests_solvent.test_installer_payload.TheDigestIsCompared"
           ".test_an_installer_built_without_a_digest_cannot_verify_anything",
           "fail-closed when nothing can be compared"),
    Mutant("allow-backslash-entries", "payload.py",
           '        return "a backslash, which Windows reads as a directory separator"',
           '        pass',
           "tests_solvent.test_installer_payload.AHostileArchiveIsNotUnpacked"
           ".test_a_backslash_entry_is_refused",
           "Windows path traversal via backslash"),
    # Removes both overlapping guards. Deleting only the '..' check leaves
    # the trailing-dot check, which stops the same traversal for a
    # different reason; a mutant that survives because a redundant guard
    # caught it reports a test gap that does not exist.
    Mutant("allow-parent-traversal", "payload.py",
           '        return "a \'..\' segment that climbs out of the destination"\n    if any(p.endswith(" ") or p.endswith(".") for p in parts):',
           '    if False:',
           "tests_solvent.test_installer_payload.AHostileArchiveIsNotUnpacked"
           ".test_a_parent_traversal_entry_is_refused",
           "archive path traversal"),
    Mutant("extract-despite-bad-entries", "payload.py",
           "    safe, bad = inspect(host, archive)\n    if bad:",
           "    safe, bad = inspect(host, archive)\n    if False:",
           "tests_solvent.test_installer_payload.AHostileArchiveIsNotUnpacked",
           "all-or-nothing extraction"),
    Mutant("no-metatrader-conflict", "mt5.py",
           '                    f"take it.")',
           '                    f"take it.") if False else ""',
           "tests_solvent.test_installer_metatrader.DetectionIsOnlyDetection"
           ".test_a_genuine_port_conflict_stops_installation_rather_than_resolving_it",
           "MetaTrader coexistence refusal"),
    Mutant("fingerprint-ignores-contents", "mt5.py",
           "        return hashlib.sha256(host.read_bytes(path)).hexdigest()",
           "        return str(len(host.read_bytes(path)))",
           "tests_solvent.test_installer_metatrader.TheHoldoutCanFail"
           ".test_a_modified_configuration_file_is_detected",
           "MetaTrader file tamper detection"),
    Mutant("admin-is-only-a-warning", "probe.py",
           '    return Row("Administrator", Outcome.BLOCKED,',
           '    return Row("Administrator", Outcome.WARN,',
           "tests_solvent.test_installer_matrix.NetworkAndHostScenarios"
           ".test_25_not_administrator_blocks_with_an_instruction",
           "privilege precondition"),
    Mutant("low-disk-is-only-a-warning", "probe.py",
           '        return Row("Disk", Outcome.BLOCKED,',
           '        return Row("Disk", Outcome.WARN,',
           "tests_solvent.test_installer_matrix.NetworkAndHostScenarios"
           ".test_23_low_disk_space_blocks_before_anything_is_written",
           "disk precondition"),
    Mutant("port-conflict-never-blocks", "probe.py",
           "    blocking = [p for p in taken if p in settings.needed_ports]",
           "    blocking = []",
           "tests_solvent.test_installer_matrix.NetworkAndHostScenarios"
           ".test_18_a_port_conflict_on_the_control_centre_blocks",
           "port conflict refusal"),
    Mutant("any-install-location-allowed", "layout.py",
           "    for fragment, why in _FORBIDDEN:\n        if fragment in probe:",
           "    for fragment, why in _FORBIDDEN:\n        if False:",
           "tests_solvent.test_installer_metatrader.DetectionIsOnlyDetection"
           ".test_installing_inside_metatrader_is_refused",
           "forbidden install locations"),
    Mutant("non-commercial-model-allowed", "ai.py",
           "        if not option.commercial:",
           "        if False:",
           "tests_solvent.test_installer_ai.TheNonCommercialModelIsRefused",
           "model licence gate"),
    Mutant("configured-reads-as-ready", "ai.py",
           '''        return done(Level.CONFIGURED,
                    "Ollama is configured but is not installed on this "
                    "computer. Nothing will answer until it is.")''',
           '''        return done(Level.READY,
                    "Ollama is configured but is not installed on this "
                    "computer. Nothing will answer until it is.")''',
           "tests_solvent.test_installer_ai.TheLadderIsClimbedOneRungAtATime"
           ".test_configured_but_not_installed_says_so",
           "AI backend honesty"),
    Mutant("echo-backend-is-ready", "ai.py",
           '''        return done(Level.CONFIGURED,
                    "the echo backend is a test double, not a language model; "''',
           '''        return done(Level.READY,
                    "the echo backend is a test double, not a language model; "''',
           "tests_solvent.test_installer_ai.TheLadderIsClimbedOneRungAtATime"
           ".test_the_echo_backend_is_never_ready",
           "test double is not a model"),
    Mutant("unprobed-inference-reads-as-ready", "ai.py",
           '''        return done(Level.REACHABLE,
                    f"{model_tag} is present; inference was not tested")''',
           '''        return done(Level.READY,
                    f"{model_tag} is present; inference was not tested")''',
           "tests_solvent.test_installer_ai.TheLadderIsClimbedOneRungAtATime"
           ".test_skipping_the_inference_probe_stops_short_of_ready",
           "untested inference is not readiness"),
    Mutant("uninstall-purges-by-default", "lifecycle.py",
           "                   purge_owner_data: bool = False) -> UninstallPlan:",
           "                   purge_owner_data: bool = True) -> UninstallPlan:",
           "tests_solvent.test_installer_security.BusinessRecordsSurvive",
           "owner data preserved by default"),
    # Inserted *after* the environment is rebuilt, so the step succeeds and
    # genuinely replaces the key. Placed before it, the step fails for want of
    # an interpreter and the mutant models a broken repair rather than a
    # rotated credential -- a different defect, and one the test would catch
    # for the wrong reason.
    Mutant("repair-rotates-the-owner-key", "lifecycle.py",
           '        ("Rebuilding the Python environment", engine.step_venv),',
           '        ("Rebuilding the Python environment", engine.step_venv),\n'
           '        ("Rotating credentials", engine.step_secrets),',
           # Designated against the structural assertion, not the byte
           # comparison. step_secrets now keeps an owner key that already
           # exists, so inserting it into repair no longer changes the file
           # and the comparison cannot see this any more.
           "tests_solvent.test_installer_security.BusinessRecordsSurvive"
           ".test_repair_does_not_run_the_credential_step_at_all",
           "repair preserves signing identity"),
    Mutant("upgrade-skips-the-backup", "lifecycle.py",
           "        record.database_backup = _backup_database(host, layout)",
           '        record.database_backup = ""',
           "tests_solvent.test_installer_security.BusinessRecordsSurvive"
           ".test_a_failed_upgrade_restores_the_database",
           "upgrade rollback integrity"),
    Mutant("upgrade-acts-before-verifying", "lifecycle.py",
           "    if not verdict.verified:",
           "    if False:",
           "tests_solvent.test_installer_security.BusinessRecordsSurvive"
           ".test_an_upgrade_with_a_bad_package_never_stops_the_running_service",
           "verify before acting on an upgrade"),
    Mutant("venv-rebuilt-from-inside-itself", "engine.py",
           "        if self.python_exe.lower().startswith(self.layout.venv.lower()):",
           "        if False:",
           "tests_solvent.test_installer_verify.TheEnvironmentRebuildGuard",
           "venv rebuild guard"),
    Mutant("verify-uses-the-default-database", "engine.py",
           '            result = self.solvent_cli(*args, "--db", self.layout.db)',
           '            result = self.solvent_cli(*args)',
           "tests_solvent.test_installer_verify.VerificationTargetsTheRealDatabase",
           "doctor runs against the real database"),
    Mutant("capability-failure-ignored", "engine.py",
           '''        if not result.ok:
            raise StepFailed(f"the certified capability could not be "
                             f"registered: {result.output.strip()[:300]}")''',
           '''        if False:
            raise StepFailed("unreachable")''',
           "tests_solvent.test_installer_matrix.InterruptionScenarios"
           ".test_26_an_interrupted_installation_can_be_run_again",
           "capability registration must succeed"),
    Mutant("python-download-unverified", "python_runtime.py",
           "    if not hmac.compare_digest(actual, BOOTSTRAP_SHA256):",
           "    if False:",
           "tests_solvent.test_installer_matrix.FreshAndPython"
           ".test_04b_a_same_size_python_download_is_still_refused",
           "Python download integrity"),
    # The detection defect, guarded one route at a time. Each route is removed
    # on its own: a mutant that removed them all together would be killed by
    # any single surviving route and would prove nothing about the others.
    Mutant("registry-not-consulted", "python_runtime.py",
           "                  + registry_candidates(host)\n", "",
           "tests_solvent.test_installer_python_discovery"
           ".EachRouteWorksOnItsOwn.test_the_registry_alone_is_enough",
           "PEP 514 registry discovery"),
    Mutant("per-user-locations-not-searched", "python_runtime.py",
           "                  + profile_candidates(host))", ")",
           "tests_solvent.test_installer_python_discovery"
           ".TheOwnersMachineAsReported"
           ".test_it_is_found_without_the_registry_entry_too",
           "per-user install discovery (the reported defect)"),
    Mutant("path-not-consulted", "python_runtime.py",
           "                  + path_candidates(host)\n", "",
           "tests_solvent.test_installer_python_discovery"
           ".EachRouteWorksOnItsOwn.test_path_alone_is_enough",
           "PATH discovery"),
    Mutant("py-launcher-not-consulted", "python_runtime.py",
           "                  + launcher_candidates(host)\n", "",
           "tests_solvent.test_installer_python_discovery"
           ".EachRouteWorksOnItsOwn.test_the_py_launcher_alone_is_enough",
           "py launcher discovery"),
    Mutant("only-the-current-profile-is-swept", "python_runtime.py",
           "        for entry in host.listdir(users_dir):",
           '        for entry in [ntpath.basename(\n'
           '                host.environ().get("USERPROFILE", ""))]:',
           "tests_solvent.test_installer_python_discovery"
           ".EachRouteWorksOnItsOwn.test_another_users_profile_is_searched_too",
           "every profile is swept, not just the elevated one"),
    Mutant("older-minor-versions-not-searched", "python_runtime.py",
           'MINORS = ("313", "314", "312", "311")',
           'MINORS = ("313",)',
           "tests_solvent.test_installer_python_discovery"
           ".EachRouteWorksOnItsOwn.test_every_supported_minor_version_is_searched",
           "every supported minor version is searched"),
    # The packaging and execution defects found on the real VPS. Each guards
    # a control that a function-level test cannot reach, because the failures
    # were in how the engine is *launched* and how the application's commands
    # actually behave -- not in any function's logic.
    Mutant("provisioning-trusts-the-exit-code", "engine.py",
           '        if not self.host.exists(self.layout.db):\n            raise StepFailed(\n                f"the DeskPilot database at {self.layout.db} was not created. "',
           '        if not result.ok:\n            raise StepFailed(\n                f"the DeskPilot database at {self.layout.db} was not created. "',
           "tests_solvent.test_installer_matrix.ExistingInstallations"
           ".test_09_upgrade_an_existing_installation",
           "database provisioning judged by the database, not an exit code"),
    Mutant("provisioning-skips-the-open-check", "engine.py",
           '        opened = self.solvent_cli("doctor", "--db", self.layout.db)',
           '        opened = self.solvent_cli("health", "--db", self.layout.db)',
           "tests_solvent.test_installer_matrix.InterruptionScenarios"
           ".test_29_a_corrupt_database_is_reported_not_overwritten",
           "a file at the database path is confirmed to be a database"),
    Mutant("owner-configuration-fails-verification", "engine.py",
           '                ("Owner configuration", ("setup", "check"), True)):',
           '                ("Owner configuration", ("setup", "check"), False)):',
           "tests_solvent.test_installer_verify"
           ".VerificationTargetsTheRealDatabase"
           ".test_owner_configuration_is_informational_not_a_gate",
           "an incomplete owner configuration does not fail verification"),
    Mutant("typed-name-used-as-a-governance-identity", "engine.py",
           '                "capability", "--db", self.layout.db]',
           '                "capability", "--db", self.layout.db,\n                "--owner", self.answers.owner_identity]',
           "tests_solvent.test_installer_security.CodeExecutionIsRefused"
           ".test_an_owner_name_containing_quotes_survives_as_one_argument",
           "the owner's typed name is never a governance identity"),
    Mutant("capability-read-from-the-wrong-module", "engine.py",
           '            "from solvent.harness import Solvent\\n"',
           '            "from solvent.services import Solvent\\n"',
           "tests_solvent.test_installer_packaged_entrypoint"
           ".TheRealApplicationCommandsWork"
           ".test_08_the_engines_capability_script_uses_the_right_module",
           "the capability read-back imports Solvent from where it lives"),
    # The credential hand-over. Each of these is the third real-world
    # failure, or the silence that made it look like the owner's fault.
    Mutant("handover-error-swallowed", "cli.py",
           '        return AnswersTransfer(\n            OwnerAnswers(), source,\n            f"the owner\'s answers did not arrive as readable JSON "',
           '        return AnswersTransfer(\n            OwnerAnswers(), source,\n            "" if True else\n            f"the owner\'s answers did not arrive as readable JSON "',
           "tests_solvent.test_installer_security"
           ".TheOwnersAnswersArriveOrSayWhyNot"
           ".test_the_exact_observed_payload_is_reported_not_swallowed",
           "a broken hand-over is reported, not defaulted away"),
    Mutant("handover-failure-does-not-block-install", "cli.py",
           '    if transfer.error and args.phase in ("selfcheck", "install"):',
           '    if transfer.error and args.phase in ("selfcheck",):',
           "tests_solvent.test_installer_security"
           ".TheOwnersAnswersArriveOrSayWhyNot"
           ".test_the_install_phase_refuses_a_bad_handover",
           "a broken hand-over stops the installation"),
    Mutant("owner-details-check-removed", "selfcheck.py",
           '    if transfer.error:\n        return Row("Owner details", Outcome.FAIL,',
           '    if False:\n        return Row("Owner details", Outcome.FAIL,',
           "tests_solvent.test_installer_security"
           ".TheOwnersAnswersArriveOrSayWhyNot"
           ".test_the_self_check_fails_on_a_broken_handover",
           "the self-check verifies the owner details arrived"),
    # Raised by the two advisory review gates, and by auditing the stages the
    # third failure was not in. Each is a real defect that was present.
    Mutant("upgrade-mixes-two-engines", "DeskPilot-Setup.nsi",
           '  RMDir /r "$INSTDIR\\Engine"\n  SetOutPath "$INSTDIR\\Engine"',
           '  SetOutPath "$INSTDIR\\Engine"',
           "tests_solvent.test_installer_wizard_static"
           ".TheUpgradeDoesNotMixTwoEngines"
           ".test_the_engine_directory_is_cleared_before_it_is_filled",
           "an upgrade does not leave the previous engine's modules behind"),
    Mutant("upgrade-deletes-the-owners-data", "DeskPilot-Setup.nsi",
           '  RMDir /r "$INSTDIR\\Engine"\n  SetOutPath',
           '  RMDir /r "$INSTDIR\\Engine"\n  RMDir /r "$INSTDIR\\Data"\n  SetOutPath',
           "tests_solvent.test_installer_wizard_static"
           ".TheUpgradeDoesNotMixTwoEngines"
           ".test_the_install_deletes_nothing_the_owner_owns",
           "an upgrade never removes the owner's database or backups"),
    Mutant("answers-cleared-before-the-document-is-built",
           "DeskPilot-Setup.nsi",
           '  StrCpy $AnswersJson \'{"owner_identity"',
           '  StrCpy $AnsPassword ""\n  StrCpy $AnswersJson \'{"owner_identity"',
           "tests_solvent.test_installer_wizard_static"
           ".TheEscapedCopiesDoNotOutliveTheDocument"
           ".test_the_document_is_built_before_the_copies_are_cleared",
           "the password reaches the document before it is forgotten"),
    Mutant("a-refusal-with-no-reason", "engine.py",
           '                why="; ".join(problems),\n', '',
           "tests_solvent.test_installer_architecture"
           ".EveryRefusalStatesItsReason"
           ".test_no_refusal_omits_what_failed_why_or_what_to_do",
           "a refusal cites the rule it was refused under"),
    Mutant("a-refusal-with-a-placeholder-reason", "engine.py",
           '                why="; ".join(problems),',
           '                why="failed",',
           "tests_solvent.test_installer_architecture"
           ".EveryRefusalStatesItsReason"
           ".test_no_refusal_states_a_placeholder_reason",
           "a refusal's reason says something"),
    Mutant("provenance-not-preserved-per-build", "build.py",
           '    kept = out_dir / f"BUILD-{suffix}.txt"\n    kept.write_text(text, encoding="utf-8")\n    return kept',
           '    kept = out_dir / "BUILD.txt"\n    return kept',
           "tests_solvent.test_installer_architecture"
           ".EachBuildKeepsItsOwnProvenance"
           ".test_a_later_build_does_not_destroy_an_earlier_record",
           "a delivered installer's hash stays recoverable after later builds"),
    Mutant("provenance-flatters-a-dirty-tree", "build.py",
           '    if not dirty:\n        return (f"engine source',
           '    if True:\n        return (f"engine source',
           "tests_solvent.test_installer_architecture"
           ".TheProvenanceFileCannotFlatter"
           ".test_a_dirty_tree_is_never_recorded_as_clean",
           "the written record does not claim a clean tree it did not have"),
    # The two services. Both of these shipped, and both were invisible to a
    # green suite because nothing executed a generated launcher or compared
    # its command to the Solvent CLI that has to accept it.
    Mutant("runtime-passes-the-web-commands-spool-flag", "integration.py",
           'return ("solvent", "run", "--db", layout.db, "--always-on")',
           'return ("solvent", "run", "--db", layout.db, "--spool", layout.spool)',
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_the_runtime_does_not_pass_the_web_commands_spool_flag",
           "the runtime command is one the Solvent CLI accepts"),
    Mutant("runtime-not-always-on", "integration.py",
           'return ("solvent", "run", "--db", layout.db, "--always-on")',
           'return ("solvent", "run", "--db", layout.db)',
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_the_runtime_runs_in_always_on_mode",
           "a boot-started service refuses non-durable storage"),
    Mutant("control-centre-is-not-a-boot-service", "integration.py",
           WEB_ENTRY_OLD, "",
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_there_are_two_services_and_they_are_the_expected_two",
           "the control centre is started by something"),
    Mutant("services-registered-but-never-started", "engine.py",
           "            if integration.start_task(self.host, service.task_name):",
           "            if False and integration.start_task(self.host, service.task_name):",
           "tests_solvent.test_installer_services"
           ".BothServicesStartAtBootAndNow"
           ".test_both_services_are_running_when_the_installer_finishes",
           "a fresh install does not need a reboot to answer"),
    Mutant("control-centre-binds-every-interface", "layout.py",
           'WEB_HOST = "127.0.0.1"',
           'WEB_HOST = "0.0.0.0"',
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_the_control_centre_serves_http_on_loopback_only",
           "the control centre is reachable only from this machine"),
    Mutant("uninstall-forgets-a-task", "integration.py",
           'ALL_TASK_NAMES: tuple[str, ...] = (\n    tuple(service.task_name for service in BOOT_SERVICES)\n    + (BACKUP_TASK_NAME,)\n)',
           'ALL_TASK_NAMES: tuple[str, ...] = (TASK_NAME, BACKUP_TASK_NAME)',
           "tests_solvent.test_installer_security"
           ".BusinessRecordsSurvive"
           ".test_uninstall_removes_only_its_own_scheduled_tasks",
           "uninstall removes every task the install created"),
    Mutant("verification-trusts-the-task-instead-of-the-answer", "engine.py",
           "        if self.host.port_in_use(WEB_PORT):",
           "        if True:",
           "tests_solvent.test_installer_services"
           ".VerificationCallsTheControlCentreRatherThanTrustingIt"
           ".test_a_silent_control_centre_is_not_reported_as_ready",
           "readiness means the control centre answered, not that a task "
           "exists"),
    Mutant("a-silent-control-centre-blocks-a-good-install", "engine.py",
           '        return Row("Control centre", Outcome.WARN,\n'
           '                   f"not answering on {WEB_HOST}:{WEB_PORT} yet.',
           '        return Row("Control centre", Outcome.FAIL,\n'
           '                   f"not answering on {WEB_HOST}:{WEB_PORT} yet.',
           "tests_solvent.test_installer_services"
           ".VerificationCallsTheControlCentreRatherThanTrustingIt"
           ".test_a_silent_control_centre_does_not_fail_a_good_install",
           "a service that may still be starting does not fail the install"),
    # Repairing an installation the previous build made. Each of these would
    # make the documented repair procedure destructive or useless.
    Mutant("reinstall-rotates-the-owner-key", "engine.py",
           "        if kept is not None and not secrets_env.self_check(kept):",
           "        if False:",
           "tests_solvent.test_installer_services"
           ".RepairingAnInstallationMadeByTheDefectiveBuild"
           ".test_reinstalling_over_it_keeps_the_owner_key",
           "a reinstall over owner data keeps the key that authorises it"),
    Mutant("first-install-reuses-nothing", "engine.py",
           "            key = secrets_env.generate_owner_key()",
           "            key = kept",
           "tests_solvent.test_installer_services"
           ".RepairingAnInstallationMadeByTheDefectiveBuild"
           ".test_a_first_installation_still_generates_a_key",
           "a first installation generates an owner key"),
    Mutant("upgrade-leaves-the-old-launchers", "lifecycle.py",
           "        if had_services or record.tasks_were_running:",
           "        if False:",
           "tests_solvent.test_installer_services"
           ".RepairingAnInstallationMadeByTheDefectiveBuild"
           ".test_upgrading_it_rewrites_the_launchers_too",
           "an upgrade rewrites the commands belonging to the new version"),
    Mutant("upgrade-imposes-autostart", "lifecycle.py",
           "        if had_services or record.tasks_were_running:",
           "        if True:",
           "tests_solvent.test_installer_services"
           ".RepairingAnInstallationMadeByTheDefectiveBuild"
           ".test_an_upgrade_does_not_give_autostart_to_someone_who_declined_it",
           "an upgrade does not add autostart the owner declined"),
    # Least privilege between the two services, which only became a question
    # once the control centre was actually started by something.
    Mutant("control-centre-gets-the-owner-key", "integration.py",
           "                env_allow=(secrets_env.WEB_PASSWORD_HASH_ENV,),\n"
           "                identity=isolation.WEB_SID,\n"
           "                elevated=False,\n"
           "                env_file=lambda layout: layout.web_env_file),",
           "                env_allow=None,\n"
           "                identity=isolation.SYSTEM_SID,\n"
           "                elevated=True,\n"
           "                env_file=lambda layout: layout.env_file),",
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_the_control_centre_is_not_given_the_owner_signing_key",
           "the control centre is not handed the owner's signing key"),
    Mutant("the-allowlist-is-not-applied", "integration.py",
           "        if ENV_ALLOW is not None and name not in ENV_ALLOW:\n"
           "            continue\n",
           "",
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_the_control_centre_is_not_given_the_owner_signing_key",
           "a declared allowlist is actually enforced"),
    Mutant("the-runtime-loses-its-key", "integration.py",
           "                env_allow=None),",
           "                env_allow=()),",
           "tests_solvent.test_installer_services"
           ".TheGeneratedCommandsAreAcceptedByTheRealCli"
           ".test_the_runtime_is_given_the_owner_signing_key",
           "the runtime keeps the key it signs with"),
    # One executable that installs or repairs, and a last screen that cannot
    # claim more than was checked.
    Mutant("reinstall-is-the-default-over-a-working-install",
           "DeskPilot-Setup.nsi",
           '    StrCpy $InstallMode "repair"\n  ${EndIf}',
           '    StrCpy $InstallMode "install"\n  ${EndIf}',
           "tests_solvent.test_installer_wizard_static"
           ".OneExecutableInstallsOrRepairs"
           ".test_repair_is_the_default_when_an_installation_is_found",
           "repair is offered rather than a full reinstall"),
    Mutant("a-repair-asks-the-owner-to-retype", "DeskPilot-Setup.nsi",
           "Function OwnerSetupPage\n  Call SkipWhenRepairing",
           "Function OwnerSetupPage",
           "tests_solvent.test_installer_wizard_static"
           ".OneExecutableInstallsOrRepairs"
           ".test_the_questions_a_repair_already_knows_are_not_asked",
           "a repair does not ask for what is already on disk"),
    Mutant("the-finish-screen-claims-success-regardless",
           "DeskPilot-Setup.nsi",
           '!define MUI_FINISHPAGE_TITLE "$FinishTitle"',
           '!define MUI_FINISHPAGE_TITLE "DeskPilot is installed"',
           "tests_solvent.test_installer_wizard_static"
           ".TheLastScreenCannotClaimMoreThanWasChecked"
           ".test_the_finish_title_is_not_a_fixed_claim",
           "the last screen reflects what the checks found"),
    Mutant("verification-is-never-run", "DeskPilot-Setup.nsi",
           '--headline "$HeadlineFile"',
           '--headline ""',
           "tests_solvent.test_installer_wizard_static"
           ".TheLastScreenCannotClaimMoreThanWasChecked"
           ".test_the_headline_comes_from_the_engine_not_the_script",
           "the readiness verdict comes from the engine"),
    Mutant("repair-takes-no-backup", "lifecycle.py",
           '        record.database_backup = _backup_database(host, layout, "repair")',
           '        record.database_backup = ""',
           "tests_solvent.test_installer_services"
           ".RepairKeepsEverythingAndCanBeUndone"
           ".test_a_repair_backs_the_database_up_first",
           "a repair copies the database before replacing anything"),
    Mutant("a-failed-repair-cannot-be-undone", "lifecycle.py",
           "            restored = _rollback(host, layout, record, log)",
           "            restored = True",
           "tests_solvent.test_installer_services"
           ".RepairKeepsEverythingAndCanBeUndone"
           ".test_a_failed_repair_puts_the_application_back",
           "a repair that fails partway puts back what it moved"),
    # Two guards share a condition now, in two functions. Each is anchored
    # on the line it guards: an anchor that matched both stopped being
    # applied, and a mutant that cannot be applied was never killed.
    Mutant("repair-imposes-autostart", "lifecycle.py",
           "        if not had_services and state is not None:\n"
           '            return StepResult("autostart", "Restoring '
           'autostart", True,',
           "        if False:\n"
           '            return StepResult("autostart", "Restoring '
           'autostart", True,',
           "tests_solvent.test_installer_services"
           ".RepairKeepsEverythingAndCanBeUndone"
           ".test_a_repair_does_not_impose_autostart_on_someone_who_declined",
           "a repair does not add autostart the owner declined"),
    Mutant("repair-confines-nothing", "lifecycle.py",
           '        ("Restoring the control centre\'s confinement", '
           "restore_isolation),\n",
           "",
           "tests_solvent.test_installer_isolation"
           ".RepairRestoresTheConfinement"
           ".test_a_repair_applies_the_access_controls_again",
           "a repair restores the boundary as well as the service"),
    # The runtime the installer ships. Both of these shipped, and together
    # they meant the runtime died before its first tick on every launch.
    Mutant("heartbeat-has-no-cadence", "solvent/runtime.py",
           '    "heartbeat": 60.0,         # the watchdog fails it at 180s; stay well inside\n',
           '',
           "tests_solvent.test_runtime"
           ".TheDefaultTasksCanActuallyBeRegistered"
           ".test_every_task_the_runtime_registers_has_a_cadence",
           "every registered task has a cadence"),
    Mutant("a-missing-cadence-kills-the-runtime", "solvent/runtime.py",
           "        if name not in DEFAULT_INTERVALS:",
           "        if False:",
           "tests_solvent.test_runtime"
           ".TheDefaultTasksCanActuallyBeRegistered"
           ".test_a_task_with_no_cadence_anywhere_does_not_kill_the_runtime",
           "registration is no stricter than running"),
    Mutant("the-heartbeat-tick-cannot-write", "solvent/runtime.py",
           "from pathlib import Path\n",
           "",
           "tests_solvent.test_runtime"
           ".TheDefaultTasksCanActuallyBeRegistered"
           ".test_every_default_task_can_be_run",
           "every default task actually runs"),
    Mutant("serving-records-no-diagnostics", "solvent/runtime.py",
           "        report = self.solvent.diagnostics.record()",
           "        report = self.solvent.diagnostics.run()",
           "tests_solvent.test_runtime"
           ".ServingRunsTheTasksAndRecordsDiagnostics"
           ".test_serving_records_a_diagnostics_report",
           "the diagnostics tick persists what it found"),
    # What the owner is told about safety, and the silence that let it lie.
    Mutant("the-projection-swallows-its-own-sql-errors",
           "solvent/web/readmodel.py",
           '            if "no such table" in str(exc):\n'
           "                return []",
           "            return []",
           "tests_solvent.test_web_control_centre"
           ".EveryProjectionQueryMatchesTheRealSchema"
           ".test_a_query_naming_a_column_that_does_not_exist_is_not_silence",
           "a broken query is an error, not an empty page"),
    Mutant("the-policy-document-is-read-from-the-wrong-table",
           "solvent/web/readmodel.py",
           '            "SELECT d.doc AS doc FROM policy_current c "\n'
           '            "JOIN policy_versions d ON d.version = c.version '
           'WHERE c.id = 1")',
           '            "SELECT doc FROM policy_versions WHERE version = -1")',
           # Deliberately not the HALT test: the projection's fail-safe default
           # *is* HALT, so a read that returns nothing at all still satisfies
           # it. The test that notices a dead read is one whose truth differs
           # from the default in the dangerous direction.
           "tests_solvent.test_web_control_centre"
           ".ThePostureOnThePageIsThePostureInThePolicy"
           ".test_leaving_simulation_does_not_still_read_as_simulated",
           "every page reads the policy the authority actually wrote"),
    Mutant("the-operating-mode-is-read-from-a-path-nothing-writes",
           "solvent/web/readmodel.py",
           '        mode = self.policy_value("operating_mode",\n'
           "                                 default=OperatingMode.HALT.value)",
           '        mode = self.policy_value("operating", "mode",\n'
           '                                 default="NORMAL")',
           "tests_solvent.test_web_control_centre"
           ".ThePostureOnThePageIsThePostureInThePolicy"
           ".test_a_halted_business_does_not_read_as_normal",
           "a halted business does not read as running"),
    Mutant("an-unreadable-mode-defaults-to-running",
           "solvent/web/readmodel.py",
           "                                 default=OperatingMode.HALT.value)",
           '                                 default="NORMAL")',
           "tests_solvent.test_web_control_centre"
           ".ThePostureOnThePageIsThePostureInThePolicy"
           ".test_an_unreadable_mode_reads_as_halted_not_as_running",
           "the projection fails safe exactly as the authority does"),
    Mutant("job-evidence-is-queried-by-a-column-that-does-not-exist",
           "solvent/web/readmodel.py",
           '            "SELECT * FROM verification_evidence WHERE subject_ref = ? "\n'
           '            "ORDER BY rowid", (job_id,))',
           '            "SELECT * FROM verification_evidence WHERE job_id = ? "\n'
           '            "ORDER BY rowid", (job_id,))',
           "tests_solvent.test_web_control_centre"
           ".EveryProjectionQueryMatchesTheRealSchema"
           ".test_every_projection_method_runs_against_the_real_schema",
           "the verification section of a job page is not silently empty"),
    Mutant("the-owner-key-step-claims-what-it-cannot-see",
           "solvent/web/app.py",
           '             DONE if setup["owner_key_evidence"] == "PROVEN" else UNKNOWN,',
           "             DONE,",
           "tests_solvent.test_web_control_centre"
           ".TheSetupPagesReflectAConfiguredBusiness"
           ".test_the_owner_key_step_says_it_cannot_tell_rather_than_accusing",
           "a fact this page cannot establish is not reported as done"),
    Mutant("unverifiable-steps-are-counted-as-cleared",
           "solvent/web/app.py",
           '        unverifiable = [s for s in steps[:5] if s[2] == UNKNOWN]',
           "        unverifiable = []",
           "tests_solvent.test_web_control_centre"
           ".TheSetupPagesReflectAConfiguredBusiness"
           ".test_the_banner_does_not_claim_everything_is_in_place",
           "the all-clear banner cannot be reached past an unchecked step"),
    # The Windows boundary, and the probe that is the only reason to believe
    # it. Every one of these was a real possible state of this code.
    Mutant("the-control-centre-runs-as-system-again", "isolation.py",
           'WEB_SID = "*S-1-5-19"',
           'WEB_SID = "*S-1-5-18"',
           "tests_solvent.test_installer_services"
           ".BothServicesStartAtBootAndNow"
           ".test_the_two_services_do_not_run_as_the_same_identity",
           "the website does not share the runtime's token"),
    Mutant("the-spool-can-be-listed", "isolation.py",
           '              "/grant:r", f"{WEB_SID}:(WD,AD,X,RA)",',
           '              "/grant:r", f"{WEB_SID}:(RD,WD,AD,X,RA)",',
           "tests_solvent.test_installer_isolation.WhatTheRulesImply"
           ".test_the_web_process_cannot_list_the_spool",
           "submitted requests cannot be enumerated"),
    Mutant("submitted-requests-can-be-read-back", "isolation.py",
           '              "/grant:r", f"{OWNER_RIGHTS_SID}:(OI)(IO)(RA)"),',
           '              "/grant:r", f"{OWNER_RIGHTS_SID}:(OI)(IO)(RA,RD)"),',
           "tests_solvent.test_installer_isolation.WhatTheRulesImply"
           ".test_the_web_process_cannot_read_a_request_it_submitted",
           "owning a file does not carry the right to read it"),
    # Three rules in isolation.py are deliberately NOT mutated here, because a
    # mutation of any one of them provably cannot change behaviour and would
    # be INVALID rather than a survivor:
    #
    #   * /inheritance:r on Data and on the spool. Either cut alone is enough
    #     to stop the drive's Users:(OI)(CI)(RX) reaching a submitted
    #     request, so each is redundant while the other stands.
    #   * the (X,RA) traverse grant to the control centre on Data. Windows
    #     grants Everyone SeChangeNotifyPrivilege by default, which skips the
    #     traverse check on intermediate directories, so this grant is belt
    #     and braces for a host where that privilege has been removed.
    #
    # Each is recorded in isolation.py as redundant rather than quietly
    # treated as covered. Mutating a redundant control to make the numbers
    # look better would be the kind of result this harness exists to prevent.
    Mutant("the-database-is-granted-write", "isolation.py",
           '        Rule(layout.db,\n'
           '             ("/grant:r", f"{WEB_SID}:(R)"),',
           '        Rule(layout.db,\n'
           '             ("/grant:r", f"{WEB_SID}:(R,W)"),',
           "tests_solvent.test_installer_isolation.WhatTheRulesImply"
           ".test_the_web_process_cannot_modify_the_database",
           "the website cannot change what it reports"),
    Mutant("a-check-the-probe-skipped-counts-as-held", "isolation.py",
           '            findings.append(Finding(name, expected, False,',
           '            findings.append(Finding(name, expected, True,',
           "tests_solvent.test_installer_isolation.TheJudgeIsNotGenerous"
           ".test_a_check_the_probe_never_reported_is_a_failure",
           "a check nobody ran is not a check that passed"),
    Mutant("an-allowed-operation-that-was-refused-counts-as-held",
           "isolation.py",
           "        held = succeeded if expected == ALLOWED else not succeeded",
           "        held = True if expected == ALLOWED else not succeeded",
           "tests_solvent.test_installer_isolation.TheJudgeIsNotGenerous"
           ".test_an_operation_that_should_work_and_was_refused_fails",
           "a boundary that broke the website is not a success"),
    Mutant("a-boundary-that-did-not-hold-does-not-stop-the-install",
           "engine.py",
           '            raise StepFailed(\n'
           '                "the access controls were applied but did not hold when "',
           '            return StepResult("isolation", title, True,\n'
           '                "the access controls were applied but did not hold when "',
           "tests_solvent.test_installer_isolation"
           ".TheProbeWouldNoticeAMissingBoundary"
           ".test_an_install_whose_rules_were_not_applied_is_refused",
           "an unenforced boundary refuses the installation"),
    Mutant("the-control-centre-reads-the-owner-key-file-again",
           "integration.py",
           "                env_file=lambda layout: layout.web_env_file),",
           "                env_file=lambda layout: layout.env_file),",
           "tests_solvent.test_installer_services"
           ".BothServicesStartAtBootAndNow"
           ".test_the_control_centre_launcher_never_opens_the_owner_key_file",
           "the web launcher never opens the file holding the key"),
    # The drop box: the surface writes intents and holds no reader.
    Mutant("the-surface-is-handed-a-reader", "solvent/web/server.py",
           "        intent_writer=intents_module.WriteOnlySpool(",
           "        intent_writer=intents_module.IntentWriter(",
           "tests_solvent.test_web_control_centre"
           ".TheControlCentreCannotReadBackWhatItAskedFor"
           ".test_the_surface_is_handed_a_spool_it_cannot_read",
           "the web process cannot read back what the owner asked for"),
    Mutant("the-approvals-page-lists-the-directory", "solvent/web/app.py",
           "        pending_intents = self.read.queued_owner_intents()",
           "        pending_intents = intents_module.IntentWriter(\n"
           "            self.intents.spool).pending()",
           "tests_solvent.test_web_control_centre"
           ".TheControlCentreCannotReadBackWhatItAskedFor"
           ".test_a_dropped_file_does_not_reach_the_approvals_page",
           "a planted spool file cannot put a row on the approvals page"),
    Mutant("finished-requests-still-look-like-they-are-waiting",
           "solvent/web/readmodel.py",
           '            "AND input_ref NOT IN ("\n'
           '            "  SELECT input_ref FROM audit_log "\n'
           '            "  WHERE event = \'owner.intent_processed\' '
           'AND input_ref IS NOT NULL"\n'
           '            ") ORDER BY seq DESC LIMIT ?", (limit,))',
           '            "ORDER BY seq DESC LIMIT ?", (limit,))',
           "tests_solvent.test_owner_intent_completion"
           ".WhatTheOwnerSeesWaitingComesFromTheRuntime"
           ".test_a_request_the_runtime_completed_stops_showing_as_waiting",
           "the waiting list clears once the runtime has acted"),
    Mutant("processed-intents-are-audited-anonymously", "solvent/harness.py",
           "            input_ref=intent_id,\n"
           '            decision=record["verb"] or "unreadable",',
           '            decision=record["verb"] or "unreadable",',
           "tests_solvent.test_owner_intent_completion"
           ".WhatTheOwnerSeesWaitingComesFromTheRuntime"
           ".test_a_request_the_runtime_completed_stops_showing_as_waiting",
           "a processed intent is identifiable, so waiting can be told from done"),
    # The control centre's intents, and the boundary that makes draining them
    # on a timer safe.
    Mutant("nothing-drains-the-owner-intent-spool", "solvent/runtime.py",
           '        self.add_task("owner_intents", self._tick_owner_intents)\n',
           '',
           "tests_solvent.test_runtime"
           ".OwnerIntentsReachTheAuthoritiesWithoutATerminal"
           ".test_a_safe_intent_takes_effect_with_nobody_at_a_terminal",
           "an owner action from the browser reaches the authorities"),
    Mutant("the-drain-overstates-what-it-applied", "solvent/runtime.py",
           '        parts = [f"{len(applied)} applied"]',
           '        parts = [f"{len(results)} applied"]',
           "tests_solvent.test_runtime"
           ".DrainingTheSpoolDoesNotWidenWhatTheWebsiteCanCause"
           ".test_nothing_dangerous_was_applied",
           "the tick counts what it applied, not what it saw"),
    Mutant("terminal-only-verbs-stop-being-refused", "solvent/harness.py",
           "    return TERMINAL_ONLY",
           "    return frozenset()",
           "tests_solvent.test_runtime"
           ".DrainingTheSpoolDoesNotWidenWhatTheWebsiteCanCause"
           ".test_the_terminal_only_verbs_were_refused_outright",
           "a spool file cannot carry the three verbs that need a terminal"),
    # The wizard itself. Nothing here can run the script, so these prove the
    # static checks notice a wizard that says the wrong thing -- the three
    # real-world failures were all a wizard saying the wrong thing while a
    # green Python suite watched.
    Mutant("handover-reads-the-wrong-register", "DeskPilot-Setup.nsi",
           'SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t R5)',
           'SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t r5)',
           "tests_solvent.test_installer_wizard_static"
           ".TheSystemPluginRegisterConvention"
           ".test_no_system_call_uses_a_lowercase_register",
           "the hand-over reads the register it wrote (the third failure)"),
    Mutant("handover-register-not-cleared", "DeskPilot-Setup.nsi",
           '    StrCpy $R5 ""\n    ; The document held the password',
           '    ; The document held the password',
           "tests_solvent.test_installer_wizard_static"
           ".TheSystemPluginRegisterConvention"
           ".test_the_handover_clears_the_register_and_the_variable",
           "the password does not outlive the hand-over"),
    Mutant("find-python-leaves-r5-unsaved", "DeskPilot-Setup.nsi",
           "  Push $R4\n  Push $R5\n  Push $R6",
           "  Push $R4\n  Push $R6",
           "tests_solvent.test_installer_wizard_static"
           ".RegistersAreSavedByFunctionsThatUseThem"
           ".test_find_python_saves_the_whole_register_file",
           "a helper leaves its caller's registers as it found them"),
    Mutant("selfcheck-abort-keeps-the-password", "DeskPilot-Setup.nsi",
           '    Call ForgetSecrets\n    DetailPrint "STOPPED BEFORE ANY CHANGE WAS MADE."',
           '    DetailPrint "STOPPED BEFORE ANY CHANGE WAS MADE."',
           "tests_solvent.test_installer_wizard_static"
           ".EverySecretIsForgottenOnEveryExit"
           ".test_no_abort_after_the_hand_over_skips_it",
           "every exit path forgets the owner's password"),
    Mutant("forget-secrets-misses-the-password", "DeskPilot-Setup.nsi",
           '  StrCpy $WebPassword ""\n  StrCpy $WebPassword2 ""\n  StrCpy $AiApiKey ""\n  Pop $R9',
           '  StrCpy $WebPassword2 ""\n  StrCpy $AiApiKey ""\n  Pop $R9',
           "tests_solvent.test_installer_wizard_static"
           ".EverySecretIsForgottenOnEveryExit"
           ".test_the_authority_clears_every_secret_variable",
           "the forgetting authority forgets all of it"),
    Mutant("forget-secrets-clobbers-its-caller", "DeskPilot-Setup.nsi",
           "Function ForgetSecrets\n  Push $R9",
           "Function ForgetSecrets",
           "tests_solvent.test_installer_wizard_static"
           ".EverySecretIsForgottenOnEveryExit"
           ".test_the_authority_does_not_disturb_its_caller",
           "clearing secrets does not corrupt the install's exit code"),
    Mutant("uninstaller-guesses-at-python-again", "DeskPilot-Setup.nsi",
           '  ReadRegStr $PythonExe HKLM "${REGKEY}" "PythonExe"',
           '  StrCpy $PythonExe "C:\\Program Files\\Python313\\python.exe"',
           "tests_solvent.test_installer_wizard_static"
           ".TheUninstallerDoesNotGuessAtPython"
           ".test_the_uninstaller_hardcodes_no_interpreter_path",
           "the uninstaller reads the recorded interpreter, not a guess"),
    Mutant("interpreter-never-recorded", "DeskPilot-Setup.nsi",
           '  WriteRegStr HKLM "${REGKEY}" "PythonExe" "$PythonExe"',
           '  WriteRegStr HKLM "${REGKEY}" "PythonVersion" "${PYTHON_VERSION}"',
           "tests_solvent.test_installer_wizard_static"
           ".TheUninstallerDoesNotGuessAtPython"
           ".test_the_installer_records_the_interpreter_it_used",
           "the install records which interpreter it used"),
    Mutant("stale-recorded-interpreter-is-trusted", "DeskPilot-Setup.nsi",
           '  ${AndIf} ${FileExists} "$PythonExe"\n    Return',
           '    Return',
           "tests_solvent.test_installer_wizard_static"
           ".TheUninstallerDoesNotGuessAtPython"
           ".test_a_recorded_path_that_no_longer_exists_is_not_used",
           "a recorded interpreter that has since gone is not run"),
    Mutant("metatraders-python-adopted", "python_runtime.py",
           "    for fragment in _FOREIGN:\n        if fragment in low:",
           "    for fragment in _FOREIGN:\n        if False:",
           "tests_solvent.test_installer_metatrader.DetectionIsOnlyDetection"
           ".test_metatraders_python_is_never_adopted",
           "never adopt another application's interpreter"),
    Mutant("secrets-written-before-lockdown", "secrets_env.py",
           '''    ran: list[str] = []
    host.write_bytes(path, header.encode("utf-8"))''',
           '''    ran: list[str] = []
    host.write_bytes(path, (header + "\\n".join(contents.lines())
                            + "\\n").encode("utf-8"))''',
           "tests_solvent.test_installer_security.SecretsDoNotLeak"
           ".test_the_secrets_file_is_restricted_before_it_holds_a_secret",
           "secrets file private before it holds secrets"),
    # Two files now, each locked down by the same loop in its own function.
    # Anchored on the argument that differs, so each mutant disables exactly
    # one of them: a single mutant would have stopped applying the moment the
    # second function was written, and read as a pass.
    Mutant("permissions-failure-ignored", "secrets_env.py",
           """    for argv in _lockdown_commands(path, readers):
        result = host.run(list(argv), timeout=60.0)
        ran.append(" ".join(argv))
        if not result.ok:""",
           """    for argv in _lockdown_commands(path, readers):
        result = host.run(list(argv), timeout=60.0)
        ran.append(" ".join(argv))
        if False:""",
           "tests_solvent.test_installer_security.SecretsDoNotLeak"
           ".test_a_failure_to_restrict_the_file_aborts_the_installation",
           "refuse rather than leave the owner key readable"),
    Mutant("web-env-permissions-failure-ignored", "secrets_env.py",
           """    for argv in _lockdown_commands(path, (WEB_SID,)):
        result = host.run(list(argv), timeout=60.0)
        ran.append(" ".join(argv))
        if not result.ok:""",
           """    for argv in _lockdown_commands(path, (WEB_SID,)):
        result = host.run(list(argv), timeout=60.0)
        ran.append(" ".join(argv))
        if False:""",
           "tests_solvent.test_installer_security.SecretsDoNotLeak"
           ".test_a_failure_to_restrict_the_control_centre_file_aborts_it",
           "refuse rather than leave the control centre's file world-readable"),
    Mutant("log-scrubber-disabled", "installlog.py",
           "    out = text\n    for pattern, replacement in _PATTERNS:",
           "    out = text\n    for pattern, replacement in []:",
           "tests_solvent.test_installer_security.SecretsDoNotLeak",
           "secret redaction in logs"),
    Mutant("secret-renders-itself", "report.py",
           '    def __str__(self) -> str:\n        return MASK',
           '    def __str__(self) -> str:\n        return self._value',
           "tests_solvent.test_installer_security.SecretsCannotBeRendered",
           "secrets cannot be printed"),
    Mutant("firewall-rule-opens-instead-of-blocks", "integration.py",
           '"dir=in", "action=block", "profile=public",',
           '"dir=in", "action=allow", "profile=public",',
           "tests_solvent.test_installer_security"
           ".ThePrivilegeBoundaryIsRespected"
           ".test_the_firewall_rule_blocks_rather_than_allows",
           "firewall rule opens nothing"),
    Mutant("task-restart-removed", "integration.py",
           '''    restart = ("<RestartOnFailure><Interval>PT1M</Interval>"
               "<Count>10</Count></RestartOnFailure>") if at_startup else ""''',
           '''    restart = ""''',
           "tests_solvent.test_installer_matrix.InterruptionScenarios"
           ".test_31_a_crashed_process_is_restarted_by_the_task",
           "process recovery after failure"),
    Mutant("failure-omits-metatrader-reassurance", "engine.py",
           '        out = ["MetaTrader was not touched: no process of its was stopped, "\n               "none of its files changed, and none of its firewall rules "\n               "altered"]',
           '        out = []',
           "tests_solvent.test_installer_security"
           ".AFailureReportStatesWhatSurvived"
           ".test_the_failure_says_metatrader_was_untouched",
           "failure report states what was untouched"),
)


def purge_bytecode() -> None:
    """Delete cached bytecode for the package under mutation.

    Not housekeeping -- correctness. CPython validates a ``.pyc`` against the
    source's size and modification time, and a mutation that replaces a string
    with one of the same length (``action=block`` for ``action=allow``) changes
    neither when the write lands in the same second. The interpreter then loads
    the cached bytecode and the test runs code that is not on disk.

    That cuts both ways and both are fatal to a mutation report: a stale cache
    during the mutated run reports SURVIVED for a control that is in fact
    guarded, and a stale cache after the restore reports a failure that cannot
    be reproduced. Purging before every run removes the whole class.
    """
    for cache in PACKAGE.rglob("__pycache__"):
        for compiled in cache.glob("*.pyc"):
            compiled.unlink(missing_ok=True)


def run_tests(target: str) -> tuple[bool, str]:
    purge_bytecode()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", target],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return result.returncode == 0, (result.stdout + result.stderr)[-3000:]


def apply_mutation(mutant: Mutant) -> tuple[bool, str]:
    """Replace the source. Returns ``(applied, original_text)``."""
    path = source_path(mutant.module)
    original = path.read_text()
    occurrences = original.count(mutant.old)
    if occurrences != 1:
        return False, original
    path.write_text(original.replace(mutant.old, mutant.new, 1))
    return path.read_text() != original, original


def restore(mutant: Mutant, original: str) -> None:
    source_path(mutant.module).write_text(original)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="run one mutant by name")
    args = parser.parse_args()

    selected = [m for m in MUTANTS
                if not args.only or m.name == args.only]
    if not selected:
        print(f"no mutant named {args.only!r}")
        return 2

    verdicts: list[tuple[str, str, str]] = []
    for index, mutant in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {mutant.name} "
              f"({mutant.module}) ... ", end="", flush=True)

        baseline_ok, baseline_out = run_tests(mutant.designated)
        if not baseline_ok:
            print("HARNESS_ERROR (baseline not green)")
            verdicts.append((mutant.name, "HARNESS_ERROR",
                             "the designated test was failing before the "
                             "mutation: " + baseline_out.strip()[-300:]))
            continue

        applied, original = apply_mutation(mutant)
        if not applied:
            print("NOT_APPLIED (pattern did not match exactly once)")
            verdicts.append((mutant.name, "NOT_APPLIED",
                             f"the pattern appears "
                             f"{original.count(mutant.old)} times in "
                             f"{mutant.module}; the mutant is stale"))
            restore(mutant, original)
            continue

        try:
            mutated_ok, mutated_out = run_tests(mutant.designated)
        finally:
            restore(mutant, original)

        if mutated_ok:
            print("SURVIVED")
            verdicts.append((mutant.name, "SURVIVED",
                             f"{mutant.control} can be broken with "
                             f"{mutant.designated} still green"))
            continue

        restored_ok, _ = run_tests(mutant.designated)
        if not restored_ok:
            print("HARNESS_ERROR (did not recover)")
            verdicts.append((mutant.name, "HARNESS_ERROR",
                             "the test still failed after reverting"))
            continue

        print("KILLED")
        verdicts.append((mutant.name, "KILLED", mutant.control))

    print()
    print("=" * 72)
    counts: dict[str, int] = {}
    for _name, verdict, _detail in verdicts:
        counts[verdict] = counts.get(verdict, 0) + 1
    for verdict in ("KILLED", "SURVIVED", "NOT_APPLIED", "HARNESS_ERROR"):
        if verdict in counts:
            print(f"{verdict:14} {counts[verdict]}")
    print("=" * 72)
    for name, verdict, detail in verdicts:
        if verdict != "KILLED":
            print(f"\n{verdict}: {name}\n  {detail}")
    return 0 if counts.get("KILLED", 0) == len(verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
