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
    return inside if inside.is_file() else INSTALLER / module


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
           "tests_solvent.test_installer_security.BusinessRecordsSurvive"
           ".test_repair_does_not_rotate_the_owner_key",
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
    Mutant("provenance-flatters-a-dirty-tree", "build.py",
           '    if not dirty:\n        return (f"engine source',
           '    if True:\n        return (f"engine source',
           "tests_solvent.test_installer_architecture"
           ".TheProvenanceFileCannotFlatter"
           ".test_a_dirty_tree_is_never_recorded_as_clean",
           "the written record does not claim a clean tree it did not have"),
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
           '  StrCpy $R5 ""\n  ; The document held the password',
           '  ; The document held the password',
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
           '    Call ForgetSecrets\n    DetailPrint "INSTALLATION STOPPED BEFORE ANY CHANGE WAS MADE."',
           '    DetailPrint "INSTALLATION STOPPED BEFORE ANY CHANGE WAS MADE."',
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
    host.write_bytes(path, _HEADER.encode("utf-8"))''',
           '''    ran: list[str] = []
    host.write_bytes(path, (_HEADER + "\\n".join(contents.lines())
                            + "\\n").encode("utf-8"))''',
           "tests_solvent.test_installer_security.SecretsDoNotLeak"
           ".test_the_secrets_file_is_restricted_before_it_holds_a_secret",
           "secrets file private before it holds secrets"),
    Mutant("permissions-failure-ignored", "secrets_env.py",
           """        if not result.ok:
            raise PermissionsRefused(""",
           """        if False:
            raise PermissionsRefused(""",
           "tests_solvent.test_installer_security.SecretsDoNotLeak"
           ".test_a_failure_to_restrict_the_file_aborts_the_installation",
           "refuse rather than leave a key readable"),
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
