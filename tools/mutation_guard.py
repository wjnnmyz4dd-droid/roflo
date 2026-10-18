"""Keep a mutation run's deliberate defects out of the repository.

A mutation runner works by writing a defect into real source, running one
test against it, and writing the source back. For the seconds in between,
the working tree holds code nobody wants. That is fine while it stays in
the working tree -- and it did not.

``de94386`` was staged with ``git add -A`` while a run held
``solvent/finder.py`` patched. The snapshot captured the mutant, so two
pushed commits carried ``if False:`` where the Finder declines work that
costs more than it earns. Nothing noticed: the runner restored the file a
second later, the working tree looked right, and only an inverted diff
weeks later gave it away.

Three defences, because each covers a case the others do not:

**Isolation.** :func:`isolated_tree` copies the working tree -- tracked
files and untracked ones git would keep -- into a temporary directory and
lets the run patch *that*. Production source is never written to at all,
so there is nothing for a commit to capture. This is the real fix; the
other two exist for runs that cannot use it.

**A claim.** :class:`Claim` writes ``.mutation-claim.json`` naming every
file a run may touch and the SHA-256 each had before it started. The file
is removed only after every hash is checked back. A run killed mid-patch
leaves the claim behind, which is the point: the evidence outlives the
process that was interrupted.

**A scan.** :func:`mutated_files` asks the question directly -- is a
mutant's replacement text sitting in source where its original is gone?
It needs no claim and no cooperation from the runner, so it catches a
mutation applied by hand, by an editor, or by a runner that crashed
before writing its claim.

The pre-commit hook in ``.githooks/pre-commit`` runs the last two. None of
this is hygiene: a disabled financial control that reaches ``origin``
unnoticed is the failure mode, and it has happened once already.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Where a live run records what it owns. At the repository root so that a
#: hook, a human and a certification script all look in the same place.
CLAIM = ROOT / ".mutation-claim.json"


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RestorationUnproven(Exception):
    """A run finished without being able to show it put the source back.

    Deliberately not a warning. The one thing worse than a mutant left in
    the tree is a mutant left in the tree by a process that reported
    success.
    """


# --------------------------------------------------------------- the claim

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:          # someone else's process, but running
        return True
    return True


def active_claim() -> dict | None:
    """The claim of a run that is still going, or ``None``.

    A claim whose process is gone is still returned, with ``stale`` set:
    that is the interrupted-run case, and it must not read as "all clear".
    """
    if not CLAIM.exists():
        return None
    try:
        held = json.loads(CLAIM.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"pid": 0, "files": {}, "stale": True,
                "why": "the claim file is unreadable, so what a run owned "
                       "cannot be established"}
    held["stale"] = not _alive(int(held.get("pid") or 0))
    return held


class Claim:
    """Announce the files a run may write to, and prove it put them back.

    Used as a context manager. On exit every file named is re-hashed
    against the value recorded on entry; a mismatch raises
    :class:`RestorationUnproven` and the claim is deliberately left on
    disk, so the next commit is refused until a person has looked.
    """

    def __init__(self, paths, *, root: pathlib.Path = ROOT, reason: str = ""):
        self.root = root
        self.reason = reason
        self.before = {
            str(pathlib.Path(p)): digest(root / p)
            for p in sorted({str(pathlib.Path(p)) for p in paths})
        }

    def __enter__(self) -> "Claim":
        existing = active_claim()
        if existing is not None:
            raise RestorationUnproven(
                "another mutation run already claims this tree "
                f"(pid {existing.get('pid')}, "
                f"{'gone' if existing.get('stale') else 'running'}). Two runs "
                "at once overwrite each other's source; resolve the existing "
                f"claim in {CLAIM} first")
        CLAIM.write_text(json.dumps({
            "pid": os.getpid(), "reason": self.reason, "files": self.before,
        }, indent=2) + "\n", encoding="utf-8")
        return self

    def verify(self) -> list[str]:
        """Which claimed files no longer hash to what they started as."""
        return [name for name, before in self.before.items()
                if digest(self.root / name) != before]

    def __exit__(self, *exc) -> bool:
        moved = self.verify()
        if moved:
            raise RestorationUnproven(
                "a mutation run finished without restoring: "
                + ", ".join(moved)
                + f". The claim is left at {CLAIM} on purpose -- it names "
                "the original digests, and nothing should be committed from "
                "this tree until the files match them again")
        CLAIM.unlink(missing_ok=True)
        return False


# ----------------------------------------------------------- the isolation

def tracked_files(root: pathlib.Path = ROOT) -> list[str]:
    """Everything git would carry: tracked, plus untracked it would add."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, check=True).stdout
    return [name for name in out.split("\0") if name]


@contextlib.contextmanager
def isolated_tree(root: pathlib.Path = ROOT, names=None):
    """A throwaway copy of the working tree, for a run to patch instead.

    Copies the *working tree*, not a commit: mutation testing is almost
    always run against uncommitted work, and a ``git worktree`` of HEAD
    would quietly test different code from the one being certified.

    ``names`` supplies the file list instead of asking git for it. The
    runners never pass it; the tests do, because DeskPilot's own egress
    guard denies ``subprocess.Popen`` process-wide once any test has
    installed it, and a test that cannot run is not coverage.
    """
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="deskpilot-mutation-"))
    try:
        for name in (tracked_files(root) if names is None else names):
            source = root / name
            if not source.is_file():          # a submodule or a dead link
                continue
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ----------------------------------------------------------------- the scan

def _mutant_sets():
    """Every (file, original, replacement) both runners know about."""
    import importlib
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for name, resolver in (("tools.mutate_solvent", None),
                           ("tools.mutate_installer", "source_path")):
        module = importlib.import_module(name)
        resolve = getattr(module, resolver) if resolver else None
        for mutant in module.MUTANTS:
            path = resolve(mutant.module) if resolve else ROOT / mutant.module
            yield mutant.name, path, mutant.old, mutant.new


def mutated_files(texts=None) -> list[tuple[str, str]]:
    """``(mutant name, path)`` for every mutant currently applied.

    A replacement on its own proves nothing -- ``return []`` occurs
    legitimately all over. What says *mutated* is that the original is
    gone and the replacement is there, which is exactly the state a
    runner leaves between its two writes.

    ``texts`` maps a path to content to check instead of reading from
    disk, so the pre-commit hook can ask the question of what is
    *staged* rather than of the working tree.
    """
    found = []
    for name, path, old, new in _mutant_sets():
        key = str(path)
        if texts is not None:
            if key not in texts:
                continue
            body = texts[key]
        else:
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
        if old not in body and new and new in body:
            found.append((name, key))
    return found


def report() -> tuple[bool, str]:
    """``(safe, why)`` for a commit, a certification run or a human."""
    held = active_claim()
    if held is not None:
        state = "interrupted" if held.get("stale") else "running"
        return False, (
            f"a mutation run claims this tree ({state}, pid "
            f"{held.get('pid')}): {', '.join(sorted(held.get('files', {})))}. "
            + (held.get("why") or
               "Nothing may be committed from underneath a mutation run."))
    applied = mutated_files()
    if applied:
        return False, ("mutated code is in the source tree: "
                       + "; ".join(f"{n} in {p}" for n, p in applied))
    return True, "no mutation claim, and no mutant is applied"


if __name__ == "__main__":
    safe, why = report()
    print(("OK: " if safe else "REFUSED: ") + why)
    raise SystemExit(0 if safe else 1)
