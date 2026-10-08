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
PACKAGE = ROOT / "windows" / "installer" / "deskpilot_installer"


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
           '''        for title, args in (("Health", ("health",)),
                            ("Readiness", ("readiness",)),
                            ("Diagnostics", ("doctor",)),
                            ("Owner configuration", ("setup", "check"))):
            result = self.solvent_cli(*args, "--db", self.layout.db)''',
           '''        for title, args in (("Health", ("health",)),
                            ("Readiness", ("readiness",)),
                            ("Diagnostics", ("doctor",)),
                            ("Owner configuration", ("setup", "check"))):
            result = self.solvent_cli(*args)''',
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
    path = PACKAGE / mutant.module
    original = path.read_text()
    occurrences = original.count(mutant.old)
    if occurrences != 1:
        return False, original
    path.write_text(original.replace(mutant.old, mutant.new, 1))
    return path.read_text() != original, original


def restore(mutant: Mutant, original: str) -> None:
    (PACKAGE / mutant.module).write_text(original)


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
