"""The certified payload: proving it is the right bytes, then unpacking it safely.

This module is the installer's front door, and it is built to refuse. Two
different attacks meet here and they need different answers.

**The wrong payload.** An installer that unpacks whatever it was given is a
delivery mechanism for whatever it was given. :func:`verify` compares the
payload's SHA-256 against the digest compiled into the installer and returns a
verdict; :func:`require_verified` turns a bad verdict into a refusal. The
comparison uses :func:`hmac.compare_digest` -- not because a timing attack on a
published hash is a realistic threat, but because the one place in the codebase
where a digest is compared should not be the place that teaches the next reader
to use ``==``.

**A hostile archive.** A ZIP entry is a string chosen by whoever built the
archive, and ``ZipFile.extractall`` will happily follow one out of the
destination directory. On Windows there are more ways out than on POSIX, and
each is checked for by name in :func:`unsafe_member`: a leading slash, a
``..`` segment, a drive letter, a UNC prefix, and -- the one a POSIX-minded
reader forgets -- a backslash, which ``zipfile`` treats as an ordinary
character in a filename but Windows treats as a directory separator. So
``a\\..\\..\\evil`` passes every POSIX check and still escapes. It is rejected
here.

Extraction is all-or-nothing: the archive is inspected completely before a
single byte is written, so a malicious entry in the middle of an otherwise
sound archive cannot leave a half-unpacked tree behind.
"""

from __future__ import annotations

import hashlib
import hmac
import ntpath
import posixpath
import zipfile
from dataclasses import dataclass

from .host import Host
from .report import Failure, Outcome, Row

#: Read size for hashing. Large enough to be fast, small enough that a payload
#: bigger than memory is still hashable.
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Verdict:
    """Why a payload may or may not be installed."""

    verified: bool
    expected: str
    actual: str
    path: str
    size: int = 0
    #: Set when the payload could not even be read or parsed.
    unreadable: str = ""

    @property
    def headline(self) -> str:
        return "PACKAGE VERIFIED" if self.verified else "PACKAGE VERIFICATION FAILED"


def digest_of(host: Host, path: str) -> str:
    """Streaming SHA-256, lowercase hex. ``""`` if the file cannot be read."""
    try:
        data = host.read_bytes(path)
    except (OSError, KeyError):
        return ""
    sha = hashlib.sha256()
    for start in range(0, len(data), _CHUNK):
        sha.update(data[start:start + _CHUNK])
    return sha.hexdigest()


def verify(host: Host, path: str, expected: str) -> Verdict:
    """Compare the payload against the digest this installer was built for."""
    expected = (expected or "").strip().lower()
    if not expected:
        # An installer built without a digest cannot verify anything, and must
        # say so rather than treating "nothing to compare" as a pass.
        return Verdict(False, "", "", path,
                       unreadable="this installer was built without an "
                                  "expected package digest")
    if not host.exists(path):
        return Verdict(False, expected, "", path,
                       unreadable=f"the package is not at {path}")
    actual = digest_of(host, path)
    if not actual:
        return Verdict(False, expected, "", path,
                       unreadable=f"the package at {path} could not be read")
    try:
        size = len(host.read_bytes(path))
    except OSError:
        size = 0
    matched = hmac.compare_digest(actual, expected)
    return Verdict(matched, expected, actual, path, size=size)


def row(verdict: Verdict) -> Row:
    """The owner-facing line. Never prints the digest in the headline."""
    if verdict.verified:
        return Row("DeskPilot package", Outcome.PASS, "VERIFIED",
                   facts={"sha256": verdict.actual, "bytes": verdict.size})
    detail = verdict.unreadable or "the package does not match its expected digest"
    return Row("DeskPilot package", Outcome.FAIL,
               f"VERIFICATION FAILED - {detail}",
               facts={"expected": verdict.expected, "actual": verdict.actual})


def refusal(verdict: Verdict) -> Failure:
    """What the owner is told when verification fails. Installation stops."""
    return Failure(
        what_failed="Package verification failed",
        why=(verdict.unreadable or
             "the DeskPilot package does not match the digest this installer "
             "was built for, so it is not the software that was certified"),
        changed=(),
        not_changed=("nothing was installed",
                     "no directories were created",
                     "no database was created or opened",
                     "no Windows setting was changed",
                     "MetaTrader was not touched"),
        safe_next_action=("Delete the downloaded installer and obtain it again "
                          "from the owner's own source. Do not install this "
                          "copy."),
    )


def require_verified(verdict: Verdict) -> None:
    """Raise unless the payload is verified. The fail-closed choke point."""
    if not verdict.verified:
        raise PayloadRefused(refusal(verdict))


class PayloadRefused(Exception):
    """Raised instead of installing. Carries the owner-facing explanation."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.what_failed)
        self.failure = failure


def unsafe_member(name: str) -> str:
    """Why this archive entry must not be written, or ``""``.

    Checked against the raw entry name before any normalisation, because
    normalising first is how the interesting cases get lost.
    """
    if not name or name in (".", ".."):
        return "an empty or dot-only entry name"
    if "\x00" in name:
        return "a null byte in the entry name"
    if "\\" in name:
        # zipfile keeps this as part of the filename; Windows reads it as a
        # separator. Every traversal check below would pass and the write
        # would still land outside the destination.
        return "a backslash, which Windows reads as a directory separator"
    if name.startswith("/"):
        return "an absolute path"
    if ntpath.isabs(name) or posixpath.isabs(name):
        return "an absolute path"
    drive, _ = ntpath.splitdrive(name)
    if drive:
        return f"a drive or UNC prefix ({drive!r})"
    parts = [p for p in name.split("/") if p]
    # The next two checks overlap completely -- every '..' segment also
    # ends in a dot -- and both are kept. They say different things: one
    # rejects climbing out of the destination, the other rejects names
    # Windows will silently rewrite. Either alone stops the traversal,
    # and relying on that coincidence would leave whichever survives a
    # later edit carrying a meaning it was not written for.
    if any(p == ".." for p in parts):
        return "a '..' segment that climbs out of the destination"
    if any(p.endswith(" ") or p.endswith(".") for p in parts):
        # Windows silently strips these, so "evil. " and "evil" collide and an
        # entry can overwrite a file whose name passed inspection.
        return "a segment ending in a space or dot, which Windows strips"
    return ""


def inspect(host: Host, archive: str) -> tuple[list[str], list[str]]:
    """``(safe_names, rejections)`` for every entry. Writes nothing."""
    safe: list[str] = []
    bad: list[str] = []
    try:
        data = host.read_bytes(archive)
    except OSError as exc:
        return [], [f"the archive could not be read: {exc}"]
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                why = unsafe_member(info.filename)
                if why:
                    bad.append(f"{info.filename!r}: {why}")
                else:
                    safe.append(info.filename)
    except zipfile.BadZipFile as exc:
        return [], [f"the archive is not a readable ZIP: {exc}"]
    return safe, bad


def extract(host: Host, archive: str, destination: str) -> list[str]:
    """Unpack ``archive`` under ``destination``, or write nothing at all.

    Inspects every entry first. One rejected entry aborts the whole
    extraction, because a partially unpacked application is harder to reason
    about than one that was never unpacked.
    """
    safe, bad = inspect(host, archive)
    if bad:
        raise PayloadRefused(Failure(
            what_failed="The DeskPilot package contains an unsafe file path",
            why=("one or more entries would have been written outside the "
                 "installation directory: " + "; ".join(bad[:3])),
            changed=(),
            not_changed=("nothing was extracted",
                         "no file outside the installation directory was written",
                         "MetaTrader was not touched"),
            safe_next_action=("Do not install this package. Obtain the "
                              "installer again from the owner's own source."),
        ))
    import io
    written: list[str] = []
    data = host.read_bytes(archive)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in safe:
            info = zf.getinfo(name)
            target = posixpath.join(destination.replace("\\", "/"), name)
            if info.is_dir():
                host.makedirs(target)
                continue
            host.write_bytes(target, zf.read(name))
            written.append(target)
    return written
