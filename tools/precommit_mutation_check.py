"""Refuse a commit that would capture a mutation run's deliberate defect.

Run by ``.githooks/pre-commit``. Two questions, because they fail
differently:

1. **Is a run holding this tree?** ``.mutation-claim.json`` says so, and a
   claim left behind by a killed process still counts -- that is the case
   where the source may be mid-patch with nobody left to put it back.
2. **Is a mutant in what is about to be committed?** Asked of the *staged*
   content rather than the working tree, because ``git add`` takes a
   snapshot: the file on disk can be correct again by the time the commit
   runs, and the index would still hold the defect. That is exactly what
   happened.

Exit 0 lets the commit through. Exit 1 refuses it and says which mutant,
in which file. ``--no-verify`` still works, deliberately: a person who has
read the reason may commit a mutant on purpose, and a hook that cannot be
overridden gets uninstalled.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import mutation_guard  # noqa: E402


def staged_text() -> dict[str, str]:
    """What the index holds for each staged file, keyed by absolute path."""
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    staged = {}
    for name in (n for n in names.split("\0") if n):
        blob = subprocess.run(["git", "show", f":{name}"], cwd=ROOT,
                              capture_output=True, check=False)
        if blob.returncode == 0:
            staged[str(ROOT / name)] = blob.stdout.decode("utf-8", "replace")
    return staged


def main(staged=None) -> int:
    """``staged`` supplies the index contents instead of asking git.

    The hook never passes it. The tests do: DeskPilot's egress guard
    denies ``subprocess.Popen`` process-wide once any test installs it,
    and a check that can only be tested by spawning git is a check that
    goes untested in the suite that matters.
    """
    held = mutation_guard.active_claim()
    if held is not None:
        state = "still running" if not held.get("stale") else (
            "gone -- the run was interrupted and may have left source patched")
        print(f"refused: a mutation run claims this tree (pid "
              f"{held.get('pid')}, {state}).", file=sys.stderr)
        for name in sorted(held.get("files", {})):
            print(f"         owns {name}", file=sys.stderr)
        print(f"\n  Wait for the run, or check the files back against the "
              f"digests in\n  {mutation_guard.CLAIM.name} and delete it.",
              file=sys.stderr)
        return 1

    applied = mutation_guard.mutated_files(
        staged_text() if staged is None else staged)
    if applied:
        print("refused: the staged content contains mutation-test defects.",
              file=sys.stderr)
        for name, where in applied:
            print(f"         {name}  in  {where}", file=sys.stderr)
        print("\n  These are deliberate defects from tools/mutate_*.py, not "
              "code.\n  Restore the files and stage them again.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
