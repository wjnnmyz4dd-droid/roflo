#!/usr/bin/env python3
"""Check an installer against the payload its build recorded.

The installer's supply-chain promise is that it refuses to install
any payload whose SHA-256 is not the one compiled into it, so CI
should assert that promise is intact rather than assume it.

**What cannot be checked here, and why the first two attempts were
wrong.** The constant is an NSIS ``!define`` and the script sets
``SetCompressor /SOLID lzma``, so it lives inside the installer's
compressed data block. The first version searched the executable's
bytes for the digest in ASCII and failed a correct build; the second
added UTF-16LE, reasoning that NSIS writes its strings that way, and
failed a correct build again. No byte search can succeed: the string
table is LZMA-compressed. Both versions were asserting something
about the file that is not true of any NSIS installer.

So this checks what is actually checkable, and says so rather than
implying more:

1. the output is a Windows executable;
2. the build recorded a payload SHA-256 in ``BUILD.txt``;
3. that digest is the SHA-256 of the staged payload, recomputed here
   rather than taken from the build's word for it;
4. the installer script compares against ``${PAYLOAD_SHA256}`` before
   unpacking and hands it to the engine as ``--expect-sha256``.

(4) is what makes the digest load-bearing, and the behaviour behind
it -- that the engine refuses a payload whose digest does not match
-- is exercised directly by the installer suite, on a fake host,
against real mismatched bytes. That is a stronger test of the
refusal than any inspection of a compiled binary would be.

Exit code 0 when all four hold, 1 when one does not, 2 for bad
arguments. The reason is printed either way.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

#: What ``build.py`` writes into BUILD.txt for the payload it embedded.
_PROVENANCE = re.compile(r"payload SHA-256\s*:\s*([0-9a-fA-F]{64})")


def payload_digest(provenance_text: str) -> str:
    """The payload SHA-256 a BUILD.txt records, or ``""``."""
    found = _PROVENANCE.search(provenance_text)
    return found.group(1).lower() if found else ""


def payload_name(provenance_text: str) -> str:
    """The payload filename a BUILD.txt records, or ``""``."""
    found = re.search(r"payload\s*:\s*(\S+\.zip)", provenance_text)
    return found.group(1) if found else ""


def script_uses_the_digest(nsi_text: str) -> list[str]:
    """What the installer script is missing, or an empty list.

    Two uses, and they are not redundant. The comparison is what
    stops a swapped payload being unpacked at all; the
    ``--expect-sha256`` is what makes the engine check it again
    itself, so that a repair or a silent reinstall is held to the
    same digest as a first installation.
    """
    missing = []
    if not re.search(r'==\s*"\$\{PAYLOAD_SHA256\}"', nsi_text):
        missing.append("the script does not compare the payload against "
                       "${PAYLOAD_SHA256} before unpacking it")
    if "--expect-sha256 \"${PAYLOAD_SHA256}\"" not in nsi_text:
        missing.append("the script does not hand ${PAYLOAD_SHA256} to the "
                       "engine as --expect-sha256")
    return missing


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: installer_carries_digest.py "
              "<setup.exe> <BUILD.txt> <DeskPilot-Setup.nsi>", file=sys.stderr)
        return 2
    exe, provenance, nsi = (pathlib.Path(a) for a in argv[1:4])
    for path in (exe, provenance, nsi):
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 2

    blob = exe.read_bytes()
    if blob[:2] != b"MZ":
        print(f"{exe} is not a Windows executable", file=sys.stderr)
        return 1

    text = provenance.read_text(encoding="utf-8")
    digest = payload_digest(text)
    if not digest:
        print(f"{provenance} records no payload SHA-256", file=sys.stderr)
        return 1

    name = payload_name(text)
    staged = exe.parent / "stage" / name if name else None
    if staged is None or not staged.exists():
        print(f"{provenance} names no payload this check can find "
              f"(looked for {staged})", file=sys.stderr)
        return 1
    actual = hashlib.sha256(staged.read_bytes()).hexdigest()
    if actual != digest:
        print(f"the build recorded {digest} and {staged.name} hashes to "
              f"{actual}", file=sys.stderr)
        return 1

    missing = script_uses_the_digest(nsi.read_text(encoding="utf-8"))
    if missing:
        for complaint in missing:
            print(complaint, file=sys.stderr)
        return 1

    print(f"{exe.name}: a PE of {len(blob)} bytes, built against "
          f"{staged.name} whose digest {digest} this check recomputed; "
          f"the script compares it before unpacking and hands it to the "
          f"engine. The compiled constant itself is inside an LZMA block "
          f"and is not inspectable from here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
