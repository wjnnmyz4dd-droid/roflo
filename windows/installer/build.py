#!/usr/bin/env python3
"""Build DeskPilot-Setup.exe.

Two artifacts, in order. First the certified payload: a ZIP of the application
at a given commit, built with ``git archive`` so its contents are exactly what
the repository holds and nothing from the working tree leaks in. Then the
installer: ``makensis`` compiles the wizard with the payload's digest baked in
as a compile-time constant.

Baking the digest in is the point of doing it this way round. The installer
cannot be talked into accepting a different payload, because the value it
compares against is in its own code section rather than in a file beside it
that an attacker could edit. If the payload changes, the installer must be
rebuilt, and rebuilding is what produces a new digest to publish.

Deterministic where it can be: ``git archive <commit>`` stamps every entry
with that commit's committer date, so two builds of one commit produce the same
payload bytes and therefore the same digest. (An earlier version of this note
claimed a ``--mtime`` flag was passed for the purpose. None is: the property is
real but it comes from the commit date, and a comment describing a mechanism
that is not there is worse than no comment.) The installer executable itself is
not byte-reproducible -- makensis embeds a build timestamp -- so its digest is
published per build rather than claimed to be derivable.

**The payload and the engine come from different places**, and the recorded
HEAD has to mean both. The payload is ``git archive`` of a commit; the engine
is copied from the working tree, because it is the installer's own code and not
part of the application being installed. If the tree carried uncommitted engine
changes, "source HEAD" would describe the payload while the engine shipped was
something no commit contains. :func:`require_clean_engine` refuses to build in
that state, so the recorded commit describes the whole artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
ENGINE = HERE / "deskpilot_installer"
NSI = HERE / "DeskPilot-Setup.nsi"

#: Paths from the repository that make up the application payload.
PAYLOAD_PATHS = ("solvent", "roflo", "tests_solvent", "tests", "deploy",
                 "docs", "tools", "pyproject.toml", "roflo.toml", "README.md",
                 "SOLVENT.md", "OWNER_DECISIONS.md")


def run(argv: list[str], cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise SystemExit(f"failed: {' '.join(argv)}\n{result.stderr.strip()}")
    return result.stdout


def head_commit() -> str:
    return run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO).strip()


def require_clean_engine(allow_dirty: bool = False) -> str:
    """Refuse to build a release whose engine is not in the recorded commit.

    The payload is archived from a commit, but the engine is copied from the
    working tree. A dirty tree therefore produces an artifact whose recorded
    HEAD is true of its payload and false of its installer code -- which is
    exactly the sort of half-true provenance a hash is supposed to prevent.

    Returns the uncommitted changes, empty when the tree is clean. The caller
    needs them: this function used to warn on screen and return nothing, and
    the provenance file went on stating "verified clean" underneath the
    warning. A warning that scrolls past while the written record says the
    opposite is worse than no check, because the record is what outlives the
    terminal.
    """
    dirty = run(["git", "status", "--porcelain", "--",
                 "windows/installer"], cwd=REPO).strip()
    if not dirty:
        return ""
    message = ("the installer source has uncommitted changes, so the build's "
               "recorded commit would not describe the engine it ships:\n"
               + dirty + "\n\nCommit them, or pass --allow-dirty for a "
               "throwaway build that must not be published.")
    if not allow_dirty:
        raise SystemExit(message)
    print("WARNING: " + message)
    return dirty


def engine_source(commit: str, dirty: str) -> str:
    """The provenance file's account of what the engine was built from."""
    if not dirty:
        return (f"engine source       : the working tree at {commit}, "
                "verified clean\n"
                "                      (payload is git archive of that "
                "commit)\n\n")
    # ``git status --porcelain`` indents unstaged changes by one column, so
    # the lines do not align under a fixed prefix unless they are stripped.
    files = "\n".join(f"                        {line.strip()}"
                       for line in dirty.splitlines())
    return ("engine source       : THE WORKING TREE WAS NOT CLEAN. The commit\n"
            f"                      above describes the payload, not the\n"
            f"                      engine shipped beside it. Uncommitted at\n"
            f"                      build time:\n" + files + "\n\n"
            "NOT FOR RELEASE     : this build's recorded commit does not\n"
            "                      describe its own installer code. Use it for\n"
            "                      local testing and publish nothing from it.\n\n")


def build_payload(out: pathlib.Path, commit: str) -> tuple[str, int]:
    """``git archive`` the application. Returns ``(sha256, bytes)``."""
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "archive", "--format=zip", "-9",
            f"--output={out}", commit, *PAYLOAD_PATHS]
    run(argv, cwd=REPO)
    data = out.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def write_python_search(stage: pathlib.Path) -> int:
    """Generate the wizard's Python fallback list from the engine's own.

    The wizard has to find an existing interpreter before the engine can run,
    so it needs its own search. Two hand-maintained lists is two sources of
    truth, and the one that drifts is the one nobody is testing -- which is
    precisely how a working Python came to be invisible to the installer while
    the engine could see it.

    So the list is emitted here from :mod:`deskpilot_installer.python_runtime`
    at build time. Adding a location to the engine adds it to the wizard, and a
    test asserts the generated file matches what the module would produce.
    """
    sys.path.insert(0, str(HERE))
    from deskpilot_installer import python_runtime as pr

    lines = [
        "; Generated by windows/installer/build.py -- do not edit.",
        "; Source of truth: deskpilot_installer/python_runtime.py",
        "",
        f'!define DP_PYTHON_BOOTSTRAP_VERSION "{pr.BOOTSTRAP_VERSION}"',
        f'!define DP_PYTHON_BOOTSTRAP_URL "{pr.BOOTSTRAP_URL}"',
        f'!define DP_PYTHON_BOOTSTRAP_SHA256 "{pr.BOOTSTRAP_SHA256}"',
        f'!define DP_PYTHON_USER_SUFFIX "{pr._USER_SUFFIX}"',
        "",
        "; Minor versions, newest first. A patch level never appears in an",
        "; installation path, so these cover every patch release.",
        "!macro DP_EACH_MINOR _m",
    ]
    for minor in pr.MINORS:
        lines.append(f'  !insertmacro ${{_m}} "{minor}"')
    lines += ["!macroend", "",
              "; All-users fallback locations, in the engine's own order.",
              "!macro DP_EACH_MACHINE_PATH _m"]
    for candidate in pr.machine_candidates():
        lines.append(f'  !insertmacro ${{_m}} "{candidate}"')
    lines += ["!macroend", ""]
    target = stage / "python-search.nsh"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(pr.machine_candidates())


def stage_engine(stage: pathlib.Path) -> int:
    """Copy the installer engine next to the payload. Source only, no caches."""
    target = stage / "deskpilot_installer"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    count = 0
    for path in sorted(ENGINE.glob("*.py")):
        shutil.copy2(path, target / path.name)
        count += 1
    # The launcher sits beside the package, not inside it: it must be runnable
    # as a plain script, and a module inside the package cannot be.
    shutil.copy2(HERE / "deskpilot-engine.py", stage / "deskpilot-engine.py")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="")
    parser.add_argument("--out-dir", default=str(HERE / "dist"))
    parser.add_argument("--payload-only", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="build despite uncommitted installer changes. "
                             "The result must not be published.")
    args = parser.parse_args()

    commit = args.commit or head_commit()
    dirty = require_clean_engine(args.allow_dirty)
    out_dir = pathlib.Path(args.out_dir)
    stage = out_dir / "stage"
    stage.mkdir(parents=True, exist_ok=True)

    payload = stage / f"DeskPilot-certified-{commit}.zip"
    digest_of_payload, size = build_payload(payload, commit)
    digest = digest_of_payload
    print(f"payload : {payload.name}")
    print(f"  commit: {commit}")
    print(f"  bytes : {size:,}")
    print(f"  sha256: {digest}")

    modules = stage_engine(stage)
    print(f"engine  : {modules} modules staged")
    fallbacks = write_python_search(stage)
    print(f"search  : python-search.nsh generated "
          f"({fallbacks} all-users fallback paths)")

    if args.payload_only:
        return 0

    exe = out_dir / "DeskPilot-Setup.exe"
    sys.path.insert(0, str(HERE))
    from deskpilot_installer import __version__
    argv = [
        "makensis", "-V2", "-NOCD",
        f"-DPAYLOAD_FILE={payload}",
        f"-DPAYLOAD_NAME={payload.name}",
        f"-DPAYLOAD_SHA256={digest}",
        f"-DPAYLOAD_BYTES={size}",
        f"-DPAYLOAD_COMMIT={commit}",
        f"-DENGINE_DIR={stage / 'deskpilot_installer'}",
        f"-DENGINE_LAUNCHER={stage / 'deskpilot-engine.py'}",
        f"-DPYTHON_SEARCH_NSH={stage / 'python-search.nsh'}",
        f"-DSETUP_VERSION={__version__}",
        f"-DOUT_FILE={exe}",
        str(NSI),
    ]
    print("compiling:", " ".join(argv[:2]), "...")
    print(run(argv))
    if not exe.exists():
        raise SystemExit("makensis reported success but produced no installer")
    data = exe.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    print(f"installer: {exe}")
    print(f"  version: {__version__}")
    print(f"  bytes  : {len(data):,}")
    print(f"  sha256 : {digest}")

    # Written beside the executable rather than into a committed document.
    # An installer's own hash cannot be recorded in a file that is part of the
    # commit it is built from: adding the record changes the commit, which
    # changes the build. The register in ARTIFACTS.md therefore holds the facts
    # that are stable -- the source commit and the payload digest -- and points
    # here for the one fact that is not.
    provenance = out_dir / "BUILD.txt"
    provenance.write_text(
        # Parenthesised: adjacent string literals concatenate before `*`, so
        # without it the rule multiplied the title line as well.
        "DeskPilot Windows installer — build provenance\n"
        + ("=" * 46) + "\n\n"
        f"file                : {exe.name}\n"
        f"installer version   : {__version__}\n"
        f"source HEAD         : {commit}\n"
        f"size (bytes)        : {len(data)}\n"
        f"installer SHA-256   : {digest}\n"
        f"payload             : {payload.name}\n"
        f"payload SHA-256     : {digest_of_payload}\n"
        f"payload size        : {size}\n"
        + engine_source(commit, dirty) +
        "code signature      : NONE. No Windows code-signing certificate\n"
        "                      exists in the build environment. Windows will\n"
        "                      warn that the publisher is unknown.\n"
        "windows execution   : NOT VERIFIED. Built and tested on Linux; the\n"
        "                      compiled wizard has not been run on Windows.\n\n"
        "Verify this file against the executable before installing:\n"
        "    certutil -hashfile DeskPilot-Setup.exe SHA256\n",
        encoding="utf-8")
    print(f"  provenance: {provenance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
