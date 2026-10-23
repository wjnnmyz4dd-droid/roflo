#!/usr/bin/env python3
"""Does this installer carry the digest of the payload it was built for?

The installer's whole supply-chain promise is that it refuses to
install any payload whose SHA-256 is not the one compiled into it. A
build that produced an executable without that constant would be a
build that had quietly dropped the promise, so CI asserts it --
against the digest the build itself recorded in ``BUILD.txt``, not
against a file re-hashed afterwards.

**The encoding is the whole difficulty, and the first attempt got it
wrong.** A PowerShell step searched the executable's bytes for the
digest as ASCII, found nothing, and failed a build that was correct:
NSIS compiles its string constants as UTF-16LE. So both encodings are
searched, and case is ignored because a ``!define`` may be
upper-cased on the way in.

Exit code 0 when the digest is there, 1 when it is not, 2 when the
arguments are wrong. The reason is printed either way.
"""

from __future__ import annotations

import pathlib
import re
import sys

#: What ``build.py`` writes into BUILD.txt for the payload it embedded.
_PROVENANCE = re.compile(r"payload SHA-256\s*:\s*([0-9a-fA-F]{64})")


def payload_digest(provenance_text: str) -> str:
    """The payload SHA-256 a BUILD.txt records, or ``""``."""
    found = _PROVENANCE.search(provenance_text)
    return found.group(1).lower() if found else ""


def carries(blob: bytes, digest: str) -> str:
    """Which encoding the digest was found in, or ``""`` for neither."""
    wanted = digest.lower()
    for label, encoding in (("ascii", "ascii"), ("utf-16-le", "utf-16-le")):
        for spelling in (wanted, wanted.upper()):
            if spelling.encode(encoding) in blob:
                return label
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: installer_carries_digest.py <setup.exe> <BUILD.txt>",
              file=sys.stderr)
        return 2
    exe, provenance = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    for path in (exe, provenance):
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 2
    blob = exe.read_bytes()
    if blob[:2] != b"MZ":
        print(f"{exe} is not a Windows executable", file=sys.stderr)
        return 1
    digest = payload_digest(provenance.read_text(encoding="utf-8"))
    if not digest:
        print(f"{provenance} records no payload SHA-256", file=sys.stderr)
        return 1
    where = carries(blob, digest)
    if not where:
        print(f"{exe.name} does not carry the payload digest {digest}",
              file=sys.stderr)
        return 1
    print(f"{exe.name} is a PE of {len(blob)} bytes carrying the payload "
          f"digest {digest} ({where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
