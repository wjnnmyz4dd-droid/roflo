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

Deterministic where it can be: ``git archive`` is given a fixed mtime so two
builds of the same commit produce the same payload bytes, and therefore the
same digest. The installer executable itself is not byte-reproducible --
makensis embeds a build timestamp -- so its digest is published per build
rather than claimed to be derivable.
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


def build_payload(out: pathlib.Path, commit: str) -> tuple[str, int]:
    """``git archive`` the application. Returns ``(sha256, bytes)``."""
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "archive", "--format=zip", "-9",
            f"--output={out}", commit, *PAYLOAD_PATHS]
    run(argv, cwd=REPO)
    data = out.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


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
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="")
    parser.add_argument("--out-dir", default=str(HERE / "dist"))
    parser.add_argument("--payload-only", action="store_true")
    args = parser.parse_args()

    commit = args.commit or head_commit()
    out_dir = pathlib.Path(args.out_dir)
    stage = out_dir / "stage"
    stage.mkdir(parents=True, exist_ok=True)

    payload = stage / f"DeskPilot-certified-{commit}.zip"
    digest, size = build_payload(payload, commit)
    print(f"payload : {payload.name}")
    print(f"  commit: {commit}")
    print(f"  bytes : {size:,}")
    print(f"  sha256: {digest}")

    modules = stage_engine(stage)
    print(f"engine  : {modules} modules staged")

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
        f"-DSETUP_VERSION={__version__}",
        f"-DOUT_FILE={exe}",
        str(NSI),
    ]
    print("compiling:", " ".join(argv[:2]), "...")
    print(run(argv))
    if not exe.exists():
        raise SystemExit("makensis reported success but produced no installer")
    data = exe.read_bytes()
    print(f"installer: {exe}")
    print(f"  version: {__version__}")
    print(f"  bytes  : {len(data):,}")
    print(f"  sha256 : {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
