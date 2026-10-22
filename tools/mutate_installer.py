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
import contextlib
import dataclasses
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "windows" / "installer"
sys.path.insert(0, str(ROOT))

from tools import mutation_guard  # noqa: E402
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
           'MINORS = ("315", "314", "313", "312", "311")',
           'MINORS = ("313",)',
           "tests_solvent.test_installer_python_discovery"
           ".EachRouteWorksOnItsOwn.test_every_supported_minor_version_is_searched",
           "every supported minor version is searched"),
    Mutant("newest-python-chosen-over-a-served-one", "python_runtime.py",
           '    preferred = [i for i in usable if served(i.version)]\n'
           '    return max(preferred or usable, key=lambda i: i.version)',
           '    return max(usable, key=lambda i: i.version)',
           "tests_solvent.test_installer_python_discovery"
           ".TheRunnerWithANewerPythonThanThePayloadServes"
           ".test_the_served_interpreter_is_preferred_over_a_newer_one",
           "an interpreter the payload carries readers for is preferred"),
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
           '        if setup["owner_key_evidence"] == "PROVEN":',
           "        if True:",
           "tests_solvent.test_web_control_centre"
           ".TheSetupPagesReflectAConfiguredBusiness"
           ".test_the_owner_key_step_says_it_cannot_tell_rather_than_accusing",
           "a fact this page cannot establish is not reported as done"),
    Mutant("unverifiable-steps-are-counted-as-cleared",
           "solvent/web/app.py",
           "        unknowns = [row for row in board.rows[:5]\n"
           "                    if row[1].state == status.UNKNOWN]",
           "        unknowns = []",
           "tests_solvent.test_web_control_centre"
           ".TheSetupPagesReflectAConfiguredBusiness"
           ".test_the_banner_does_not_claim_everything_is_verified",
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
    # One status vocabulary, and the two rules that keep it honest. Every
    # one of these was a real state of this surface: it reported a halted
    # business as NORMAL for the life of the project.
    Mutant("a-healthy-status-needs-no-evidence", "solvent/web/status.py",
           "        if self.state == VERIFIED_HEALTHY and not self.evidence.strip():",
           "        if False:",
           "tests_solvent.test_web_status.TheVocabularyRefusesTwoThings"
           ".test_verified_healthy_needs_evidence",
           "a green row cannot be built without something behind it"),
    Mutant("a-sixth-status-word-is-accepted", "solvent/web/status.py",
           "        if self.state not in TONE:",
           "        if False:",
           "tests_solvent.test_web_status.TheVocabularyRefusesTwoThings"
           ".test_a_sixth_status_word_is_refused",
           "the vocabulary stays closed"),
    Mutant("stale-evidence-keeps-its-verdict", "solvent/web/status.py",
           "    if age_seconds > stale_after:",
           "    if False:",
           "tests_solvent.test_web_status.StaleEvidenceIsNotEvidence"
           ".test_evidence_past_its_window_reads_unknown",
           "evidence older than its subject is not evidence"),
    Mutant("absent-evidence-reads-as-present", "solvent/web/status.py",
           "    if age_seconds is None:",
           "    if False:",
           "tests_solvent.test_web_status.StaleEvidenceIsNotEvidence"
           ".test_no_evidence_at_all_reads_unknown_rather_than_failed",
           "nothing established is UNKNOWN, not a verdict"),
    Mutant("the-summary-takes-the-best-row", "solvent/web/status.py",
           "    for state in ORDER:",
           "    for state in reversed(ORDER):",
           "tests_solvent.test_web_status.TheWorstStateWins"
           ".test_one_unsafe_row_decides_the_summary",
           "one unsafe row decides the page"),
    Mutant("an-empty-board-reads-healthy", "solvent/web/status.py",
           "    if not found:\n        return UNKNOWN",
           "    if not found:\n        return VERIFIED_HEALTHY",
           "tests_solvent.test_web_status.TheWorstStateWins"
           ".test_no_rows_at_all_is_unknown_not_healthy",
           "a board that checked nothing is not a healthy board"),
    Mutant("an-unrecognised-check-state-reads-healthy", "solvent/web/board.py",
           '    state = FROM_DIAGNOSTICS.get(str(finding.get("state", "")), st.UNKNOWN)',
           '    state = FROM_DIAGNOSTICS.get(str(finding.get("state", "")),\n'
           "                                 st.VERIFIED_HEALTHY)",
           "tests_solvent.test_web_status.TheTranslationTableIsComplete"
           ".test_an_unrecognised_state_becomes_unknown_not_healthy",
           "a state this page cannot interpret is not reported as fine"),
    Mutant("a-stale-heartbeat-reads-as-alive", "solvent/web/board.py",
           "    if age > HEARTBEAT_STALE_SECONDS:",
           "    if False:",
           "tests_solvent.test_web_status.LivenessComesFromTheHeartbeat"
           ".test_a_stale_heartbeat_is_unknown_not_dead",
           "liveness is the heartbeat being recent, not it existing"),
    Mutant("a-missing-heartbeat-is-not-noticed", "solvent/web/board.py",
           "    except FileNotFoundError:",
           "    except _NeverRaised:",
           "tests_solvent.test_web_status.LivenessComesFromTheHeartbeat"
           ".test_a_missing_heartbeat_is_unknown",
           "a runtime that never ticked is not reported as running"),
    Mutant("real-execution-with-no-allowlist-reads-fine",
           "solvent/web/board.py",
           "    if not allowlist:",
           "    if False:",
           "tests_solvent.test_web_status.ThePostureFactsReadHonestly"
           ".test_real_execution_with_no_allowlist_is_unsafe",
           "real effects with nothing scoping them is UNSAFE"),
    Mutant("an-unreadable-posture-claims-real-execution",
           "solvent/web/board.py",
           '    simulated = bool(posture.get("simulation_only", True))',
           '    simulated = bool(posture.get("simulation_only", False))',
           "tests_solvent.test_web_status.ThePostureFactsReadHonestly"
           ".test_an_absent_posture_defaults_to_simulation",
           "a projection that cannot read the policy fails safe"),
    Mutant("an-unknown-operating-mode-reads-healthy",
           "solvent/web/board.py",
           '    return st.unknown(f"the operating mode reads {mode!r}, which this page "',
           '    return st.healthy(f"the operating mode reads {mode!r}", "none") or st.unknown(f"{mode} "',
           "tests_solvent.test_web_status.ThePostureFactsReadHonestly"
           ".test_a_mode_this_page_does_not_know_is_unknown",
           "a mode this page does not recognise is not reported as running"),
    Mutant("a-stale-report-still-fills-the-board", "solvent/web/board.py",
           '        if not latest.get("stale"):',
           "        if True:",
           "tests_solvent.test_web_status.AStaleReportDoesNotFillTheBoard"
           ".test_a_stale_report_contributes_no_findings",
           "a report describing a past moment is not current state"),
    Mutant("the-old-setup-url-stops-working", "solvent/web/app.py",
           '        return "/setup"',
           '        return "", ""',
           "tests_solvent.test_web_control_centre"
           ".TheSetupPagesReflectAConfiguredBusiness"
           ".test_the_wizard_url_still_works_and_leads_to_the_one_page",
           "consolidating two pages did not break the surviving URL"),
    # The one setting the control centre may change on its own, and the
    # whitelist that is the entire reason it is allowed to.
    Mutant("work-preferences-can-write-anything", "solvent/harness.py",
           "        if name not in WORK_PREFERENCES:",
           "        if False:",
           "tests_solvent.test_owner_intent_completion"
           ".TheOwnerCanSetWorkPreferencesFromTheBrowser"
           ".test_it_cannot_write_outside_its_own_namespace",
           "a browser request writes qualification filters and nothing else"),
    # Anchored with the line above it: the capacity applier added later
    # checks its bounds the same way, and an anchor matching both is
    # NOT_APPLIED, which is not a kill.
    Mutant("work-preference-bounds-are-not-checked", "solvent/harness.py",
           """        if not low <= value <= high:
            raise FailClosed(
                f"{name} must be between {low} and {high}; {value} is outside "
                f"the range this setting is allowed to take")""",
           """        if False:
            raise FailClosed(
                f"{name} must be between {low} and {high}; {value} is outside "
                f"the range this setting is allowed to take")""",
           "tests_solvent.test_owner_intent_completion"
           ".TheOwnerCanSetWorkPreferencesFromTheBrowser"
           ".test_a_value_outside_its_range_is_refused",
           "a filter cannot be set to an absurd value"),
    # Per-platform rules. The statuses are derived, so what is mutated is the
    # order of the questions -- which is the part that decides whether a
    # later branch can talk its way past a prohibition.
    Mutant("a-platform-prohibition-can-be-talked-past",
           "solvent/sourcecatalog.py",
           "    if entry.access_method == access.UNSUPPORTED_AUTOMATION:",
           "    if False:",
           "tests_solvent.test_platform_rules.TheStatusAnswersTheQuestionsInOrder"
           ".test_a_platform_restriction_outranks_everything",
           "a prohibition outranks a credential and an adapter"),
    Mutant("a-stale-determination-still-reads-ready",
           "solvent/sourcecatalog.py",
           "    if entry.stale(today=today):",
           "    if False:",
           "tests_solvent.test_platform_rules.TheStatusAnswersTheQuestionsInOrder"
           ".test_a_stale_determination_reads_unverified_not_ready",
           "terms read years ago are not a current finding"),
    Mutant("an-unauthorised-source-reads-established",
           "solvent/sourcecatalog.py",
           "    if access.DISCOVER not in permissions:",
           "    if False:",
           "tests_solvent.test_platform_rules.TheStatusAnswersTheQuestionsInOrder"
           ".test_registering_without_discover_is_pending",
           "registering grants the read side only"),
    Mutant("a-source-with-no-adapter-reads-automated",
           "solvent/sourcecatalog.py",
           "    if not entry.adapter_implemented:",
           "    if False:",
           "tests_solvent.test_platform_rules.TheStatusAnswersTheQuestionsInOrder"
           ".test_a_permitted_platform_with_no_adapter_is_pending",
           "a row naming a source is not code that fetches from one"),
    Mutant("not-asking-about-a-credential-blocks-the-source",
           "solvent/sourcecatalog.py",
           "    if credential_present is False:",
           "    if not credential_present:",
           "tests_solvent.test_platform_rules.TheStatusAnswersTheQuestionsInOrder"
           ".test_not_asking_about_a_credential_is_not_the_same_as_absent",
           "a source needing no credential is not blocked for want of one"),
    Mutant("the-rate-limit-is-not-enforced", "solvent/discovery.py",
           "        rested, rest_why = self._rate_limit_respected(source, state)\n"
           "        if not rested:\n"
           "            return False, rest_why",
           "        pass",
           "tests_solvent.test_platform_rules.ARateLimitIsANumberAndItIsEnforced"
           ".test_a_second_poll_straight_away_is_refused",
           "a documented rate limit is a rule, not a note"),
    Mutant("a-poll-that-found-nothing-is-not-recorded",
           "solvent/discovery.py",
           "            self._record_poll(source_name)",
           "            pass",
           "tests_solvent.test_platform_rules.ARateLimitIsANumberAndItIsEnforced"
           ".test_the_poll_is_stamped_even_when_it_returns_nothing",
           "a fetch that returned nothing still spent a request"),
    Mutant("a-corrupt-poll-timestamp-permits-a-fetch",
           "solvent/discovery.py",
           "            return False, (f\"source {source.name!r} has an unreadable \"",
           "            return True, (f\"source {source.name!r} has an unreadable \"",
           "tests_solvent.test_platform_rules.ARateLimitIsANumberAndItIsEnforced"
           ".test_an_unreadable_timestamp_refuses_rather_than_polls",
           "a corrupt record is not permission to hammer an API"),
    Mutant("a-marketplace-can-claim-access-without-evidence",
           "solvent/sourcecatalog.py",
           "    if entry.access_method == access.UNDETERMINED:",
           "    if False:",
           "tests_solvent.test_platform_rules.EveryPlatformTheOwnerNamedIsCatalogued"
           ".test_no_marketplace_reads_as_established",
           "an unread platform is UNVERIFIED, never assumed"),
    # Spreadsheet automation. The verifier is the thing under test in most of
    # these: a worker whose mistakes nobody catches is the failure mode.
    Mutant("a-missing-input-becomes-a-zero", "solvent/sheetwork.py",
           "    if any(v is None for v in values):\n        return None",
           "    values = [Decimal(0) if v is None else v for v in values]",
           "tests_solvent.test_sheet_capability.TheArithmeticIsExact"
           ".test_a_missing_input_leaves_the_computed_cell_blank",
           "a gap in the inputs is a gap in the output, never a zero"),
    Mutant("a-blank-cell-is-read-as-zero", "solvent/sheetwork.py",
           "    if not text:\n        return None",
           "    if not text:\n        return Decimal(0)",
           "tests_solvent.test_sheet_capability.TheArithmeticIsExact"
           ".test_a_blank_cell_is_not_a_zero",
           "a blank in a column of money is not a zero"),
    Mutant("the-verifier-accepts-a-wrong-figure", "solvent/sheetverify.py",
           "    return stated == expected",
           "    return True",
           "tests_solvent.test_sheet_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_wrong_computed_value_is_caught",
           "a figure that does not match its inputs is a failure"),
    Mutant("the-verifier-stops-parsing-formulas", "solvent/sheetverify.py",
           "        if columns != wanted:",
           "        if False:",
           "tests_solvent.test_sheet_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_right_value_with_a_formula_on_the_wrong_column_is_caught",
           "a right value beside a wrong formula is still a defect"),
    Mutant("a-formula-may-name-another-row", "solvent/sheetverify.py",
           "        elif rows_named != {row_index}:",
           "        elif False:",
           "tests_solvent.test_sheet_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_formula_referring_to_another_row_is_caught",
           "a formula refers to its own row"),
    Mutant("an-invented-value-is-not-noticed", "solvent/sheetverify.py",
           "        if available.get(key, 0) > 0:",
           "        if True:",
           "tests_solvent.test_sheet_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_changed_preserved_column_is_caught",
           "a value that was never in the source is a changed figure"),
    Mutant("unverifiable-is-folded-into-pass", "solvent/sheetverify.py",
           "        if UNVERIFIABLE in kinds:\n            return UNVERIFIABLE",
           "        pass",
           "tests_solvent.test_sheet_capability.TheVerifierSaysWhenItCannotTell"
           ".test_unverifiable_outranks_pass_in_the_overall_verdict",
           "a job half of which could not be checked is not verified"),
    Mutant("an-empty-report-reads-as-passed", "solvent/sheetverify.py",
           "        if not kinds:\n            return UNVERIFIABLE",
           "        if not kinds:\n            return PASS",
           "tests_solvent.test_sheet_capability.TheVerifierSaysWhenItCannotTell"
           ".test_no_findings_at_all_is_unverifiable_not_pass",
           "a verifier that checked nothing has not passed anything"),
    Mutant("the-planner-accepts-any-operation", "solvent/sheetwork.py",
           "        if name not in OPERATIONS:",
           "        if False:",
           "tests_solvent.test_sheet_capability.ThePlanRefusesWhatItCannotDo"
           ".test_an_operation_it_does_not_perform_is_refused_by_name",
           "an unsupported ask is refused, not improvised"),
    Mutant("a-clients-column-can-be-overwritten", "solvent/sheetwork.py",
           "    if into.strip().lower() in [h.strip().lower() for h in header]:",
           "    if False:",
           "tests_solvent.test_sheet_capability.TheWorkerRefusesRatherThanImprovising"
           ".test_it_will_not_overwrite_a_column_the_client_supplied",
           "a column the client supplied is not replaced silently"),
    Mutant("sheets-are-stacked-by-position", "solvent/sheetwork.py",
           '        elif [h.lower() for h in this_header] != [h.lower() for h in header]:',
           "        elif False:",
           "tests_solvent.test_sheet_capability.TheWorkerRefusesRatherThanImprovising"
           ".test_sheets_with_different_columns_are_not_stacked",
           "stacking by position would move figures between columns"),
    Mutant("a-macro-workbook-is-opened-anyway", "solvent/xlsx.py",
           "        if part in names:",
           "        if False:",
           "tests_solvent.test_xlsx_format.AFileItCannotReadIsRefused"
           ".test_a_macro_enabled_workbook",
           "a file whose behaviour depends on code we ignore is refused"),
    Mutant("the-phantom-leap-day-becomes-a-date", "solvent/xlsx.py",
           "    if days == 60:",
           "    if False:",
           "tests_solvent.test_xlsx_format.ValuesAreReadAsWhatTheyAre"
           ".test_excels_phantom_leap_day_is_not_turned_into_a_date",
           "a day that did not exist is not given a name"),
    Mutant("dates-are-a-day-out", "solvent/xlsx.py",
           "    shift = days if days < 60 else days - 1",
           "    shift = days",
           "tests_solvent.test_xlsx_format.ValuesAreReadAsWhatTheyAre"
           ".test_dates_either_side_of_the_phantom_day_are_right",
           "every date in a client file is the date it was"),
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
    # ---- provisioning Python on a real Windows machine -----------------
    # Three behaviours the fake host could not previously exercise, because
    # each is a property of Windows rather than of the installer's logic.

    Mutant("reboot-required-reads-as-a-failed-install", "python_runtime.py",
           "BOOTSTRAP_OK_CODES = frozenset({0, 3010})",
           "BOOTSTRAP_OK_CODES = frozenset({0})",
           "tests_solvent.test_installer_python_bootstrap"
           ".TheExitCodeIsTheSignal"
           ".test_a_reboot_required_install_is_a_success",
           "an installation aborts on 3010, where Python installed fine and "
           "Windows merely wants a restart"),

    Mutant("the-arm64-check-never-fires", "python_runtime.py",
           "    if architecture in _ARM64_MARKERS:",
           "    if False:",
           "tests_solvent.test_installer_python_bootstrap"
           ".ArmMachinesAreToldRatherThanGuessedAt"
           ".test_an_arm64_machine_is_refused_with_its_architecture_named",
           "the amd64 Python installer is run on a Windows-on-ARM machine "
           "instead of the owner being told it cannot be served"),

    Mutant("an-arm64-machine-downloads-anyway", "engine.py",
           "        unsupported = python_runtime.bootstrap_supported(self.host)",
           "        unsupported = \"\"",
           "tests_solvent.test_installer_python_bootstrap"
           ".TheBootstrapRunsThroughTheEngine"
           ".test_an_arm64_machine_stops_before_downloading_anything",
           "29MB is fetched and an incompatible installer executed on a "
           "machine the check had already identified"),

    Mutant("the-exit-code-never-reaches-the-owner", "engine.py",
           '            raise StepFailed(f"{what} (exit code {ran.returncode}). {remedy}"',
           '            raise StepFailed(f"{what}. {remedy}"',
           "tests_solvent.test_installer_python_bootstrap"
           ".TheBootstrapRunsThroughTheEngine"
           ".test_an_unrecognised_code_still_reaches_the_owner_as_a_number",
           "the one signal a /quiet installer produces is dropped, leaving "
           "the owner a failure with no way to tell which failure it was"),

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



# ===========================================================================
# The five-skill library
# ===========================================================================
#
# Twenty-three controls, each of which is either a defect found while building
# the library or a check a client's deliverable rests on. Every "before" string
# below is the fix; the mutant restores the defect, and a named test has to
# notice.
#
# These live beside the installer's mutants rather than in a battery of their
# own because the honesty rules are the same ones and there should be one place
# that enforces them: a kill counts only when the mutation actually applied,
# the designated test newly failed, and the failure went away on revert.

_LIBRARY_MUTANTS: tuple[Mutant, ...] = (
    Mutant("requirement-check-accessor-reads-one-spelling", "solvent/types.py",
           '    for attribute in ("check", "check_name"):',
           '    for attribute in ("check_name",):',
           "tests_solvent.test_entry_capability.TheRealRequirementType"
           ".test_a_committed_requirement_plans_through_the_worker",
           "a worker reads the requirement field the orchestrator supplies"),
    Mutant("select-columns-leaves-formulas-behind", "solvent/sheetwork.py",
           "        _reposition(step, picks, len(header), formulas, formats)",
           "        pass",
           "tests_solvent.test_sheet_capability"
           ".ColumnsMoveAndFormulasMoveWithThem"
           ".test_a_reordering_selection_keeps_working_formulas",
           "formulas move with the columns a selection reorders"),
    Mutant("select-columns-drops-an-input-silently", "solvent/sheetwork.py",
           "            if index not in moved_to:\n                raise _Refused(",
           "            if index not in moved_to:\n                return match.group(0)\n                raise _Refused(",
           "tests_solvent.test_sheet_capability"
           ".ColumnsMoveAndFormulasMoveWithThem"
           ".test_a_selection_that_removes_an_input_is_refused_with_the_reason",
           "a figure whose inputs are removed is refused, not shipped"),
    Mutant("rename-planned-after-everything-that-names-a-column",
           "solvent/sheetwork.py",
           '        "consolidate_sheets", "rename_headers", "require_columns",\n        "trim_whitespace", "drop_empty_rows", "compute_column",',
           '        "consolidate_sheets", "require_columns", "trim_whitespace",\n        "drop_empty_rows", "rename_headers", "compute_column",',
           "tests_solvent.test_sheet_capability.OneNamePerColumn"
           ".test_the_rename_is_planned_before_anything_that_names_a_column",
           "one name per column, from the first step onwards"),
    Mutant("reconciliation-counts-without-tracing", "solvent/sheetverify.py",
           "    touched = _authorised_names(requirements)\n    pairs = [(out_header.index(name), in_header.index(name))",
           '    touched = _authorised_names(requirements)\n    if True:\n        return Finding(rid, "row_reconciliation", PASS, "counted")\n    pairs = [(out_header.index(name), in_header.index(name))',
           "tests_solvent.test_sheet_capability.CountingIsNotReconciling"
           ".test_a_correct_workbook_from_another_job_does_not_reconcile",
           "delivered rows trace to this source, not merely count"),
    Mutant("consolidation-only-checks-the-sheets-exist",
           "solvent/sheetverify.py",
           "    touched = _authorised_names(requirements)\n    first = source_sheets",
           '    if True:\n        return Finding(rid, "consolidate_sheets", PASS, "sheets exist")\n    touched = _authorised_names(requirements)\n    first = source_sheets',
           "tests_solvent.test_sheet_capability.WhichWorksheetsDidTheRowsComeFrom"
           ".test_one_worksheet_stacked_twice_instead_of_two_is_caught",
           "which worksheets the delivered rows came from"),
    Mutant("opens-as-workbook-left-unanswered", "solvent/sheetverify.py",
           '    if check == "opens_as_workbook":\n        # Reaching here means both files were read',
           '    if check == "opens_as_workbook" and False:\n        # Reaching here means both files were read',
           "tests_solvent.test_sheet_capability.CountingIsNotReconciling"
           ".test_opens_as_workbook_is_answered_when_a_requirement_names_it",
           "a requirement nobody answers is UNVERIFIABLE, not passed"),
    Mutant("sheet-finding-passes-on-unverifiable", "solvent/sheetverify.py",
           # Report.passed has the same body, so the anchor reaches back into
           # Finding.passed's own docstring to name one of them.
           '        verifier.\n        """\n        return self.verdict == PASS',
           '        verifier.\n        """\n        return self.verdict != FAIL',
           "tests_solvent.test_sheet_capability.CountingIsNotReconciling"
           ".test_an_unverifiable_finding_does_not_count_as_passed",
           "UNVERIFIABLE blocks delivery exactly as FAIL does"),
    Mutant("exceptions-justified-checks-one-direction",
           "solvent/entryverify.py",
           "    wrongly_held = sorted(held_rows - expected_rows)",
           "    wrongly_held = []",
           # Designated against the no-rule-named case, which is the one only
           # this check can catch: with a rule named, the comparison of stated
           # against broken rules one line down also rejects it, and this
           # mutant survived against that test for exactly that reason.
           "tests_solvent.test_entry_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_valid_record_held_with_no_rule_named_at_all",
           "a valid record held back is a short deliverable nobody can see"),
    Mutant("rejection-reason-not-compared", "solvent/entryverify.py",
           '        if sorted(entry["rules"]) != want:',
           "        if False:",
           "tests_solvent.test_entry_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_rejection_reason_that_names_the_wrong_rule",
           "a rejection reason that matches the defect it names"),
    Mutant("invented-values-by-set-membership", "solvent/entryverify.py",
           "            if position is None:\n                invented.append(f\"record {index}: {name}={text!r}\")\n                continue\n            pool.pop(position)",
           "            if position is None:\n                invented.append(f\"record {index}: {name}={text!r}\")\n                continue",
           "tests_solvent.test_entry_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_value_delivered_more_often_than_the_source_holds_it",
           "multiset containment, not set membership"),
    Mutant("traceability-compares-counts-only", "solvent/entryverify.py",
           "        if tuple(sorted(want.items())) != tuple(sorted(got.items())):",
           "        if False:",
           "tests_solvent.test_entry_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_record_grafted_together_from_two_source_records",
           "a delivered record is the source record it claims to be"),
    Mutant("reconcile-ignores-the-arithmetic", "solvent/entryverify.py",
           "    if counted != total:",
           "    if False:",
           "tests_solvent.test_entry_capability.TheVerifierCatchesAWrongWorker"
           ".test_a_record_in_neither_file_is_simply_lost",
           "every source record is accounted for exactly once"),
    Mutant("entry-unparseable-figure-becomes-zero", "solvent/entrywork.py",
           "    if not _plain_number(text):\n        return None",
           "    if not _plain_number(text):\n        return Decimal(0)",
           "tests_solvent.test_entry_capability"
           ".TwoImplementationsOfOneSpecification"
           ".test_non_numbers_are_non_numbers_on_both_sides",
           "a figure that will not parse is not zero"),
    Mutant("required-field-absence-checked-one-way", "solvent/docverify.py",
           "        if expected.get(name) not in _MARKERS and claimed[name] in _MARKERS:",
           "        if False:",
           "tests_solvent.test_document_capability.BothDirectionsOfAnAbsence"
           ".test_a_stated_field_delivered_as_not_stated_is_caught",
           "a field reported absent from a document that states it"),
    Mutant("invented-content-ignores-the-tables", "solvent/docverify.py",
           "        for position, table in enumerate(\n                _delivered_tables(extraction, delivered), start=1):",
           "        for position, table in enumerate([], start=1):",
           "tests_solvent.test_document_capability.BothDirectionsOfAnAbsence"
           ".test_a_table_row_invented_is_not_in_the_document",
           "a fabricated line item is invented content"),
    Mutant("absences-disclosed-accepts-a-filled-gap", "solvent/docverify.py",
           "        if got != want:\n            return Finding(\n                rid, check, FAIL,\n                f\"{name} is not stated in the document and the deliverable \"",
           "        if False:\n            return Finding(\n                rid, check, FAIL,\n                f\"{name} is not stated in the document and the deliverable \"",
           "tests_solvent.test_document_capability"
           ".TheVerifierCatchesAWrongWorker"
           ".test_a_gap_filled_in_with_something_plausible",
           "a gap filled with something plausible"),
    Mutant("extracted-value-needs-no-evidence", "solvent/docverify.py",
           "            if not evidence.strip():",
           "            if False:",
           "tests_solvent.test_document_capability"
           ".TheVerifierCatchesAWrongWorker"
           ".test_a_value_with_no_evidence_behind_it",
           "a value with no evidence is a value nobody should report"),
    Mutant("deleted-text-read-as-what-the-document-says", "solvent/docx.py",
           '        if tag == "t":\n            if _inside(element, node, "delText"):\n                continue',
           '        if tag in ("t", "delText"):\n            if False:\n                continue',
           "tests_solvent.test_docx_format.TrackedChanges"
           ".test_deleted_text_is_not_what_the_document_says",
           "a struck-out figure is not what the document says"),
    Mutant("pdf-read-approximately-instead-of-refused", "solvent/docwork.py",
           "    if suffix in READABLE:\n        return \"\"",
           "    if True:\n        return \"\"",
           "tests_solvent.test_document_capability.UnsupportedFormats"
           ".test_a_pdf_is_refused_with_the_reason_and_a_way_forward",
           "an unreliable extraction is worse than a visible refusal"),
    Mutant("markdown-cell-pipe-not-unescaped", "solvent/docwork.py",
           '            current.append(character if character == "|" else "\\\\" + character)',
           '            current.append("\\\\" + character)',
           "tests_solvent.test_document_capability.Conversion"
           ".test_a_pipe_inside_a_cell_survives_the_round_trip",
           "a client's pipe inside a cell survives the round trip"),
    Mutant("overlap-gate-treats-unknown-as-different",
           "solvent/skillslab.py",
           "        if not reads or not theirs:\n            return False",
           "        if not reads or not theirs:\n            return True",
           "tests_solvent.test_five_skill_library.TheSkillsLabLifecycle"
           ".test_an_unstated_domain_on_the_existing_side_is_not_a_difference",
           "a missing input domain is not a licence for a second authority"),
    Mutant("verification-loop-withholds-the-brief", "solvent/harness.py",
           '        params.setdefault("requirements", baseline)',
           "        pass",
           "tests_solvent.test_five_skill_library"
           ".TheWholePipelineRunsEachCapability"
           ".test_a_data_entry_job_is_delivered_with_its_exceptions",
           "a verifier is told what the job asked for"),
    Mutant("certifiable-scope-claims-everything", "solvent/harness.py",
           "    return (frozenset(checks_for(capability))\n            - set(UNCERTIFIABLE.get(capability, {})))",
           "    return frozenset(checks_for(capability))",
           "tests_solvent.test_five_skill_library.WhatEvidenceCannotCover"
           ".test_the_certifiable_scope_is_narrower_than_the_check_list",
           "a promotion may not claim what no evidence covers"),
    Mutant("promotion-skips-the-evidence-floor", "solvent/harness.py",
           "    verdict, reason = promotion_verdict(capability)\n    if not verdict:",
           "    verdict, reason = promotion_verdict(capability)\n    if False:",
           "tests_solvent.test_commercial_promotion"
           ".TheEvidenceIsCheckedBeforeAnythingIsRecorded"
           ".test_a_capability_with_a_false_completion_is_refused",
           "one shipped-wrong-while-reporting-success blocks a promotion"),
    Mutant("promotion-claims-checks-it-is-not-certified-for",
           "solvent/harness.py",
           '    if record["state"] != "CERTIFIED" or missing:\n        raise FailClosed(\n            f"the verifier for {name} is {record[\'state\']} and certified for "',
           '    if record["state"] != "CERTIFIED":\n        raise FailClosed(\n            f"the verifier for {name} is {record[\'state\']} and certified for "',
           "tests_solvent.test_commercial_promotion"
           ".TheEvidenceIsCheckedBeforeAnythingIsRecorded"
           ".test_a_promotion_claiming_more_than_the_evidence_is_refused",
           "number_format cannot be sold as certified"),
    Mutant("promotion-asks-nobody-who-is-asking", "solvent/harness.py",
           "    if not s.policy.is_owner(owner_identity):\n        # Asked here as well as inside `decide`",
           "    if False:\n        # Asked here as well as inside `decide`",
           "tests_solvent.test_commercial_promotion.OnlyTheOwnerDecides"
           ".test_a_component_asking_about_an_undecided_capability_writes_nothing",
           "an unauthorised attempt leaves no row behind"),
    Mutant("promotion-appends-a-second-approval-each-run",
           "solvent/harness.py",
           "    if previous != s.capability.APPROVED:",
           "    if True:",
           "tests_solvent.test_commercial_promotion"
           ".LimitedBecomesApprovedWithoutLosingTheRecord"
           ".test_promoting_twice_changes_nothing",
           "re-running the owner's command is not a second decision"),

    # --- the business pipeline: the joins between the halves ---------------
    Mutant("need-matched-against-check-names-only", "solvent/capability.py",
           "                if need == cap.name or need in cap.covers:",
           "                if need in cap.covers:",
           "tests_solvent.test_business_cycle.TheWholeTransactionCompletes"
           ".test_every_stage_ran_in_order",
           "a posting naming a service Solvent performs is not a gap"),
    Mutant("execution-answers-the-question-put-to-the-owner",
           "solvent/harness.py",
           "    if s.orchestrator.job(job_id).state is JobState.AWAITING_OWNER_APPROVAL:\n        raise FailClosed(",
           "    if False:\n        raise FailClosed(",
           "tests_solvent.test_business_cycle"
           ".SolventNeverAnswersItsOwnersQuestion"
           ".test_the_service_pipeline_cannot_step_over_a_waiting_job",
           "the pipeline never supplies its owner's decision"),
    Mutant("confirming-a-requirement-rewrites-it", "solvent/content.py",
           "    return dataclasses.replace(requirement, source=source, confirmed_by=by)",
           "    return Requirement(id=requirement.id, text=requirement.text,\n                       source=source, confirmed_by=by)",
           "tests_solvent.test_business_cycle"
           ".ConfirmingARequirementDoesNotRewriteIt"
           ".test_the_check_acceptance_params_and_criticality_all_survive",
           "vouching for a requirement does not edit what it says"),
    Mutant("a-service-name-recorded-as-a-blocking-term",
           "solvent/qualification.py",
           "        source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED,\n        criticality=Criticality.ADVISORY)",
           "        source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED,\n        criticality=Criticality.MANDATORY)",
           "tests_solvent.test_business_cycle"
           ".ThereIsNoContractWithoutAgreedTerms"
           ".test_the_postings_own_need_does_not_block_the_checklist",
           "scope is recorded; only terms block delivery"),
    Mutant("the-cycle-executes-without-being-selected", "solvent/harness.py",
           "    ranked = s.qualification.rank([decision])\n    selected, deferred = s.qualification.select(ranked)",
           "    ranked = s.qualification.rank([decision])\n    selected, deferred = ranked, []",
           "tests_solvent.test_business_cycle"
           ".SolventNeverAnswersItsOwnersQuestion"
           ".test_without_the_owners_answer_the_job_waits",
           "work is committed by selection, under the capacity limit"),
    Mutant("work-starts-without-money-authorised", "solvent/orchestrator.py",
           '        if not grant_id:\n            raise FailClosed("execution requires a Financial Governor budget grant")',
           '        if not grant_id:\n            pass',
           "tests_solvent.test_business_cycle"
           ".WorkDoesNotStartWithoutMoneyAuthorised"
           ".test_begin_execution_refuses_an_empty_grant",
           "no work begins without a Governor budget grant"),
    Mutant("the-worker-is-handed-scope-it-cannot-carry-out",
           "solvent/harness.py",
           "    instructions = [r for r in report.committed if r.check]",
           "    instructions = list(report.committed)",
           "tests_solvent.test_business_cycle"
           ".EveryClearedSkillEarnsThroughTheSameCycle"
           ".test_a_data_entry_job_earns_and_holds_its_exceptions",
           "instructions are the terms that name a check"),
    Mutant("simulated-money-booked-as-real-revenue", "solvent/harness.py",
           '        verification_method=f"{SIMULATED_PREFIX}{payment_rail}")',
           '        verification_method=payment_rail)',
           "tests_solvent.test_business_cycle.SimulatedMoneyIsNeverRevenue"
           ".test_the_ledger_reports_no_real_revenue",
           "fixture money is never counted, displayed or reported as earnings"),
    Mutant("the-journals-payload-keeps-the-clients-secrets",
           "solvent/audit.py",
           "        payload = {key: redact(value) if isinstance(value, str) else value\n                   for key, value in payload.items()}",
           "        payload = dict(payload)",
           "tests_solvent.test_business_cycle"
           ".KLeanTheJournalDoesNotKeepTheClientsSecrets"
           ".test_a_credential_in_a_posting_does_not_reach_the_audit_journal",
           "a credential in a posting's title never reaches the journal"),
    Mutant("redaction-eats-the-journals-own-evidence", "solvent/audit.py",
           "        why = redact(why)\n        payload = {key: redact(value) if isinstance(value, str) else value",
           "        why = redact(why)\n        result = redact(result)\n        payload = {key: redact(value) if isinstance(value, str) else value",
           "tests_solvent.test_business_cycle"
           ".KLeanTheJournalDoesNotKeepTheClientsSecrets"
           ".test_the_journals_own_evidence_is_not_redacted_away",
           "an artifact digest is evidence, and redaction leaves it alone"),
    # --- found by the 45-scenario matrices ---------------------------------
    Mutant("a-conversion-job-is-written-to-the-wrong-format",
           "solvent/harness.py",
           "        if types.check_of(requirement) == \"convert_format\":",
           "        if False:",
           "tests_solvent.test_skill_matrices.AnUnseenFinalExam"
           ".test_every_scenario_passes",
           "a conversion delivers the format the client asked for"),
    Mutant("the-cycle-guesses-instead-of-asking", "solvent/harness.py",
           "    for ambiguity in ambiguity_module.detect(capability, source_file, baseline):",
           "    for ambiguity in []:",
           "tests_solvent.test_skill_matrices.ADevelopmentMatrix"
           ".test_some_scenarios_expect_a_question_rather_than_a_file",
           "an ambiguous source produces a question, not a coin flip"),
    Mutant("a-date-that-reads-the-same-both-ways-is-refused",
           "solvent/csvwork.py",
           "    elif _same_day(year, first, second):",
           "    elif False:",
           "tests_solvent.test_skill_matrices.AnUnseenFinalExam"
           ".test_every_scenario_passes",
           "10/10/2025 is one day, so there is nothing to ask"),
    Mutant("consolidation-refuses-a-correctly-emptied-sheet",
           "solvent/sheetverify.py",
           "        if body and not present and not _all_removable(header, body,\n                                                       requirements):",
           "        if body and not present:",
           "tests_solvent.test_skill_matrices.AnUnseenFinalExam"
           ".test_every_scenario_passes",
           "a control that blocks correct work is broken, not safe"),
    Mutant("a-pinned-duplicate-key-is-asked-about-anyway",
           "solvent/ambiguity.py",
           '     ("drop_exact_duplicates", "row_reconciliation", "deduplicate_by"),\n     ("duplicate_key", "fields"),',
           '     ("drop_exact_duplicates", "row_reconciliation"), ("duplicate_key",),',
           "tests_solvent.test_skill_matrices.ADevelopmentMatrix"
           ".test_every_scenario_passes",
           "a brief that already says what a duplicate is is not questioned"),
    Mutant("the-journals-reasons-keep-the-clients-secrets",
           "solvent/audit.py",
           "        why = redact(why)\n        payload = {key:",
           "        payload = {key:",
           "tests_solvent.test_business_cycle"
           ".KLeanTheJournalDoesNotKeepTheClientsSecrets"
           ".test_a_credential_in_the_job_reference_does_not_reach_the_reasons",
           "a credential in a posting's reference never reaches a reason"),
)

MUTANTS = MUTANTS + _LIBRARY_MUTANTS

def purge_bytecode(where: pathlib.Path = ROOT) -> None:
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
    for cache in (where / "windows" / "installer").rglob("__pycache__"):
        for compiled in cache.glob("*.pyc"):
            compiled.unlink(missing_ok=True)


def run_tests(target: str, where: pathlib.Path = ROOT) -> tuple[bool, str]:
    purge_bytecode(where)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", target],
        cwd=where, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return result.returncode == 0, (result.stdout + result.stderr)[-3000:]


def _in(where: pathlib.Path, mutant: Mutant) -> pathlib.Path:
    """The mutant's file, inside whichever tree is being patched."""
    return where / source_path(mutant.module).relative_to(ROOT)


def apply_mutation(mutant: Mutant, where: pathlib.Path = ROOT) -> tuple[bool, str]:
    """Replace the source. Returns ``(applied, original_text)``."""
    path = _in(where, mutant)
    original = path.read_text(encoding="utf-8")
    occurrences = original.count(mutant.old)
    if occurrences != 1:
        return False, original
    path.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
    return path.read_text(encoding="utf-8") != original, original


def restore(mutant: Mutant, original: str, where: pathlib.Path = ROOT) -> None:
    _in(where, mutant).write_text(original, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="run one mutant by name")
    parser.add_argument(
        "--in-place", action="store_true",
        help="patch the real source instead of a throwaway copy of it; see "
             "tools/mutation_guard.py for why the copy is the default")
    args = parser.parse_args()

    selected = [m for m in MUTANTS
                if not args.only or m.name == args.only]
    if not selected:
        print(f"no mutant named {args.only!r}")
        return 2

    with contextlib.ExitStack() as stack:
        if args.in_place:
            where = ROOT
            stack.enter_context(mutation_guard.Claim(
                sorted({str(source_path(m.module).relative_to(ROOT))
                        for m in selected}),
                root=ROOT, reason="mutate_installer --in-place"))
            print(f"in-place: claimed in {mutation_guard.CLAIM.name}; "
                  "do not commit from this tree until the run finishes")
        else:
            where = stack.enter_context(mutation_guard.isolated_tree(ROOT))
            print(f"isolated: patching a copy of the working tree at {where}")
        return _battery(selected, where)


def _battery(selected, where: pathlib.Path) -> int:
    verdicts: list[tuple[str, str, str]] = []
    for index, mutant in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {mutant.name} "
              f"({mutant.module}) ... ", end="", flush=True)

        baseline_ok, baseline_out = run_tests(mutant.designated, where)
        if not baseline_ok:
            print("HARNESS_ERROR (baseline not green)")
            verdicts.append((mutant.name, "HARNESS_ERROR",
                             "the designated test was failing before the "
                             "mutation: " + baseline_out.strip()[-300:]))
            continue

        applied, original = apply_mutation(mutant, where)
        if not applied:
            print("NOT_APPLIED (pattern did not match exactly once)")
            verdicts.append((mutant.name, "NOT_APPLIED",
                             f"the pattern appears "
                             f"{original.count(mutant.old)} times in "
                             f"{mutant.module}; the mutant is stale"))
            restore(mutant, original, where)
            continue

        try:
            mutated_ok, mutated_out = run_tests(mutant.designated, where)
        finally:
            restore(mutant, original, where)

        if mutated_ok:
            print("SURVIVED")
            verdicts.append((mutant.name, "SURVIVED",
                             f"{mutant.control} can be broken with "
                             f"{mutant.designated} still green"))
            continue

        restored_ok, _ = run_tests(mutant.designated, where)
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
