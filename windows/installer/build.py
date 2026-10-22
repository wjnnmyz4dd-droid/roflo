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
import re
import shutil
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
ENGINE = HERE / "deskpilot_installer"
NSI = HERE / "DeskPilot-Setup.nsi"

# Imported at module scope, not inside a function like the rest, because
# WHEEL_PYTHONS below is a module constant and the whole point of reading
# it from the engine is that there is no second copy to fall out of date.
sys.path.insert(0, str(HERE))
from deskpilot_installer import vendor  # noqa: E402

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


def check_register(exe_digest: str, payload_digest: str, size: int,
                   commit: str) -> list[str]:
    """Does ARTIFACTS.md describe what was just built? Returns complaints.

    The register is hand-written, and its whole value is that a reader can
    trust it. A digest typed from a truncated screenful of output is worse
    than no digest at all: it looks like provenance and is not. This compares
    what the register claims against what the build actually produced, so the
    mistake is a line of output rather than a document nobody can rely on.

    Advisory, not fatal. The register is updated after a build by definition
    -- the installer's own hash cannot be in the commit it is built from -- so
    a mismatch on the build that precedes the update is expected.
    """
    register = REPO / "windows" / "installer" / "ARTIFACTS.md"
    if not register.is_file():
        return ["ARTIFACTS.md is missing"]
    text = register.read_text(encoding="utf-8")
    current = text.split("## Superseded")[0]
    complaints = []
    for label, value in (("installer SHA-256", exe_digest),
                         ("payload SHA-256", payload_digest),
                         ("size", format(size, ",")),
                         ("source commit", commit)):
        if value not in current:
            complaints.append(f"the register's Current section does not "
                              f"name this build's {label} ({value})")
    return complaints


def write_provenance(out_dir: pathlib.Path, commit: str, text: str,
                     dirty: str = "") -> pathlib.Path:
    """Write ``BUILD.txt`` and a copy the next build will not overwrite.

    ``BUILD.txt`` alone was the only record of a delivered installer's hash,
    and ``dist/`` is not in the repository, so every build destroyed its
    predecessor's provenance. makensis embeds a build timestamp, so the lost
    hash could not be recomputed from the same commit either -- which left the
    artifact register pointing at a file that no longer described the artifact
    it named. Returns the path of the preserved copy.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "BUILD.txt").write_text(text, encoding="utf-8")
    # A throwaway build keeps its record under a different name. Both builds
    # are at the same commit when one is dirty, so a single commit-keyed name
    # let a --allow-dirty build overwrite the record of the clean build that
    # was actually delivered -- destroying exactly what this function exists
    # to preserve.
    suffix = f"{commit}-dirty" if dirty else commit
    kept = out_dir / f"BUILD-{suffix}.txt"
    kept.write_text(text, encoding="utf-8")
    return kept


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


#: Which interpreters the vendored readers are built for.
#:
#: Read from :data:`.vendor.SERVED_MINORS` rather than written here,
#: and that is the point: the same tuple decides what this build
#: fetches, what the manifest beside the wheels pins, and which
#: interpreter ``python_runtime.choose`` prefers on the owner's
#: machine. Those three were three separate lists, and a Windows
#: runner carrying a 3.15 the payload had no set for is what proved
#: they drift -- the installation chose 3.15 and shipped no readers.
WHEEL_PYTHONS = tuple(f"{major}.{minor}"
                      for major, minor in reversed(vendor.SERVED_MINORS))

#: The hash-pinned manifest for those readers.
T2_MANIFEST = HERE / "t2-windows-x64.txt"


def fetch_wheels(into: pathlib.Path) -> dict:
    """Download the T2 readers against the manifest's hashes.

    ``--require-hashes`` makes pip refuse any file whose SHA-256 is
    not listed, which is the check that matters; the loop afterwards
    repeats it here rather than taking pip's word, because this is
    the one step in the build that reaches the network and a build
    that trusts its downloader is a build with one check in it.

    This is the only network access in the whole installation story.
    It happens on the build machine, and what reaches an owner is the
    payload -- whose own digest is compiled into the installer.
    """
    allowed = set(re.findall(r"--hash=sha256:([0-9a-f]{64})",
                             T2_MANIFEST.read_text(encoding="utf-8")))
    if not allowed:
        raise SystemExit(f"{T2_MANIFEST} lists no hashes; refusing to fetch")
    counts = {}
    for version in WHEEL_PYTHONS:
        tag = "cp" + version.replace(".", "")
        dest = into / tag
        dest.mkdir(parents=True, exist_ok=True)
        run([sys.executable, "-m", "pip", "download",
             "--disable-pip-version-check", "--no-cache-dir",
             "--require-hashes", "--only-binary=:all:",
             "--platform", "win_amd64", "--python-version", version,
             "--implementation", "cp", "--dest", str(dest),
             "-r", str(T2_MANIFEST)])
        got = sorted(dest.glob("*.whl"))
        for wheel in got:
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            if digest not in allowed:
                raise SystemExit(
                    f"{wheel.name} hashes to {digest}, which {T2_MANIFEST.name} "
                    "does not list. Nothing is packaged.")
        counts[tag] = len(got)
    return counts


def build_payload(out: pathlib.Path, commit: str) -> tuple[str, int]:
    """``git archive`` the application, plus the verified readers.

    Two sources, and the second one is new. The archive is still
    exactly what the repository holds at ``commit``; the wheels are
    appended afterwards under ``vendor/`` because they are not in the
    repository and should not be -- 12.6 MB of binaries under version
    control would make every upgrade a binary diff.

    Determinism survives. ``git archive <commit>`` stamps each entry
    with the commit date, and each wheel is pinned to one SHA-256, so
    two builds of one commit still produce the same bytes and the
    same digest.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "archive", "--format=zip", "-9",
            f"--output={out}", commit, *PAYLOAD_PATHS]
    run(argv, cwd=REPO)

    staged = out.parent / "vendor"
    if staged.exists():
        shutil.rmtree(staged)
    counts = fetch_wheels(staged / "wheels")
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.write(T2_MANIFEST, "vendor/t2-windows-x64.txt")
        for wheel in sorted((staged / "wheels").rglob("*.whl")):
            archive.write(wheel,
                          f"vendor/wheels/{wheel.parent.name}/{wheel.name}")
    print("readers : " + ", ".join(f"{tag}={n}" for tag, n in counts.items()))

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


def write_services(stage: pathlib.Path) -> int:
    """Generate the uninstaller's task list from the engine's own.

    The uninstaller has a path that runs without Python -- for a machine whose
    interpreter has been removed since installation -- and on that path NSIS
    has to delete the scheduled tasks itself, by name. Those names were written
    out in the script, so adding the control-centre service would have left a
    task behind that kept starting a control centre for an application that had
    been removed. Emitted here instead, from the one tuple that defines them.
    """
    sys.path.insert(0, str(HERE))
    from deskpilot_installer import integration

    lines = [
        "; Generated by windows/installer/build.py -- do not edit.",
        "; Source of truth: deskpilot_installer/integration.py",
        "",
        "; Every scheduled task a DeskPilot installation can have.",
        "!macro DP_EACH_TASK_NAME _m",
    ]
    for name in integration.ALL_TASK_NAMES:
        lines.append(f'  !insertmacro ${{_m}} "{name}"')
    lines += ["!macroend", ""]
    (stage / "services.nsh").write_text("\n".join(lines) + "\n",
                                        encoding="utf-8")
    return len(integration.ALL_TASK_NAMES)


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
    tasks = write_services(stage)
    print(f"search  : python-search.nsh generated "
          f"({fallbacks} all-users fallback paths)")
    print(f"services: services.nsh generated ({tasks} task names)")

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
        f"-DSERVICES_NSH={stage / 'services.nsh'}",
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
    text = (
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
        "    certutil -hashfile DeskPilot-Setup.exe SHA256\n")
    kept = write_provenance(out_dir, commit, text, dirty)
    print(f"  provenance: {provenance}")
    print(f"  preserved : {kept}")
    for complaint in check_register(digest, digest_of_payload, len(data),
                                    commit):
        print(f"  register  : {complaint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
