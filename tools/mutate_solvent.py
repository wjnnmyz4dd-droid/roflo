"""Mutation testing for Solvent's load-bearing controls.

A passing suite proves the tests ran. It does not prove they would notice if
the control they are named after stopped working. This breaks one control at
a time and insists the designated test *newly* fails -- then puts it back and
insists the failure goes away.

Three outcomes matter:

``KILLED``
    The designated test passed before, failed with the mutant, and passed
    again after the revert. The control is pinned.
``SURVIVED``
    The test passed anyway. The control is not pinned by that test, whatever
    the test's name claims. This is the finding.
``NOT_APPLIED``
    The old text was not found -- the file moved on and the mutant is stale.
    **Not a kill.** Counting it as one is how a mutation report becomes a
    decoration.

Usage::

    python3 tools/mutate_solvent.py                 # every mutant
    python3 tools/mutate_solvent.py --only t2       # name substring
    python3 tools/mutate_solvent.py --list
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclasses.dataclass(frozen=True)
class Mutant:
    name: str
    module: str
    old: str
    new: str
    test: str
    breaks: str


MUTANTS: tuple[Mutant, ...] = (

    # ---- T2 independent review -------------------------------------
    # The contract asks C_HIGH for "T1 + T2 with verifier != executor".
    # Each mutant below removes one of the things that makes the second
    # opinion a second opinion.

    Mutant(
        "t2-review-takes-any-signed-subject",
        "solvent/independent.py",
        "    if approval.subject != expected:",
        "    if False:",
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_a_review_signed_for_a_different_artifact_is_refused",
        "an approval signed for a different artifact would be accepted as a "
        "review of this one"),

    Mutant(
        "t2-review-needs-no-signature",
        "solvent/independent.py",
        "    if approval is None:",
        "    if False:",
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_an_unsigned_review_is_refused",
        "an unsigned claim of review would be recorded as owner evidence"),

    Mutant(
        "t2-worker-may-review-itself",
        "solvent/audit.py",
        '        if reviewer_identity in ("worker", "execution"):',
        "        if False:",
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_the_executor_cannot_review_its_own_work",
        "the executor could sign off its own work as the independent review"),

    Mutant(
        "t2-differential-ignores-disagreement",
        "solvent/independent.py",
        "    if disagreements:",
        "    if False:",
        "tests_solvent.test_independent_review.TheSecondOpinionIsAboutTheFigures"
        ".test_a_wrong_figure_is_caught_by_the_independent_reader",
        "a deliverable whose figures the independent reader disputes would "
        "still be recorded as reviewed"),

    Mutant(
        "t2-parsing-counts-as-review",
        "solvent/independent.py",
        "    if not findings:",
        "    if False:",
        "tests_solvent.test_independent_review.TheSecondOpinionIsAboutTheFigures"
        ".test_a_brief_with_nothing_recomputable_is_refused_not_passed",
        "'the file opens' would be recorded as an independent review of the "
        "figures"),

    Mutant(
        "t2-any-reader-will-do",
        "solvent/independent.py",
        "    if module is None:",
        "    if False:",
        "tests_solvent.test_independent_review.TheSecondOpinionIsAboutTheFigures"
        ".test_a_format_with_no_independent_reader_is_refused",
        "a format with no independently authored reader would fall back to "
        "Solvent reading its own output twice"),

    Mutant(
        "t2-eligibility-never-blocks",
        "solvent/harness.py",
        "    if not cycle.tier_reachable:",
        "    if False:",
        "tests_solvent.test_independent_review.TheTierIsCheckedBeforeTheMoney"
        ".test_a_job_nobody_can_review_stops_before_any_work",
        "a job whose required tier nothing here can produce would still be "
        "budgeted and executed before the refusal was discovered"),

    Mutant(
        "t2-evidence-needs-no-artifact",
        "solvent/audit.py",
        '        if not artifact_digest:\n'
        '            raise FailClosed(\n'
        '                "independent review evidence must name the artifact '
        'digest it "',
        '        if False:\n'
        '            raise FailClosed(\n'
        '                "independent review evidence must name the artifact '
        'digest it "',
        "tests_solvent.test_independent_review.APersonsReviewNeedsTheirSignature"
        ".test_evidence_without_a_requirement_or_digest_is_refused",
        "review evidence could not be invalidated when the file it reviewed "
        "is corrected"),
)


def run(test: str) -> tuple[bool, str]:
    done = subprocess.run(
        [sys.executable, "-m", "unittest", test, "-v"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800)
    return done.returncode == 0, (done.stdout + done.stderr)[-400:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    chosen = [m for m in MUTANTS if args.only in m.name]
    if args.list:
        for m in chosen:
            print(f"{m.name:40s} {m.module}")
        return 0

    results = []
    for mutant in chosen:
        path = ROOT / mutant.module
        original = path.read_text()

        if original.count(mutant.old) != 1:
            found = original.count(mutant.old)
            print(f"NOT_APPLIED  {mutant.name}  "
                  f"(old text found {found} times, need exactly 1)")
            results.append((mutant, "NOT_APPLIED", ""))
            continue

        green, _ = run(mutant.test)
        if not green:
            print(f"HARNESS      {mutant.name}  "
                  f"(baseline is not green; nothing to prove)")
            results.append((mutant, "HARNESS", ""))
            continue

        path.write_text(original.replace(mutant.old, mutant.new))
        try:
            still_green, tail = run(mutant.test)
        finally:
            path.write_text(original)

        back_green, _ = run(mutant.test)
        if not back_green:
            print(f"HARNESS      {mutant.name}  (did not recover on revert)")
            results.append((mutant, "HARNESS", ""))
            continue

        verdict = "SURVIVED" if still_green else "KILLED"
        print(f"{verdict:12s} {mutant.name}")
        if verdict == "SURVIVED":
            print(f"             would mean: {mutant.breaks}")
        results.append((mutant, verdict, tail))

    killed = sum(1 for _, v, _ in results if v == "KILLED")
    print(f"\n{killed}/{len(results)} killed"
          f"  ({sum(1 for _, v, _ in results if v == 'SURVIVED')} survived, "
          f"{sum(1 for _, v, _ in results if v in ('NOT_APPLIED', 'HARNESS'))}"
          f" not applied or harness)")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
