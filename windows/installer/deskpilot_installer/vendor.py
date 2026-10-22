"""The independent (T2) readers, put on disk without pip.

DeskPilot needs a second opinion on a client's file from a reader it
did not write -- openpyxl for a workbook, python-docx for a document,
CleverCSV for a CSV, markdown-it-py for Markdown. Without them the
only remaining producer of a T2 verdict is the owner signing each
review by hand, and the business cycle declines such a job while it
is still a quote.

Getting them onto a fresh Windows machine was the last open
dependency question, and the two obvious answers were both wrong.

**pip at install time** would undo the thing the installer exists for.
``engine.py`` says it plainly: no pip, because an installation that
resolves and compiles packages on the owner's machine is an
installation that can fail in ways nobody can reproduce. It would
also mean the installer reaches the network, which the security model
does not allow.

**Wheels committed to the repository** would put 12.6 MB of binaries
under version control and make every upgrade a binary diff.

So the wheels are fetched at **build** time against the hashes in
``windows/installer/t2-windows-x64.txt``, placed inside the payload,
and the payload's own SHA-256 is compiled into the installer. By the
time an owner runs anything, the readers are bytes that were verified
twice -- once by pip against the manifest on the build machine, once
by :func:`verify` against the same manifest here -- inside an archive
whose digest the installer checks before unpacking a single file.
Nothing is downloaded on the owner's machine, and there is nowhere
for an unverified wheel to enter.

**Why extraction rather than installation.** A wheel is a ZIP whose
top-level directories are the importable packages. Unpacking it into
a directory on ``sys.path`` is what ``pip install`` does for a wheel
with no build step, minus the metadata bookkeeping DeskPilot does not
read. The compiled extensions (``lxml``, ``regex``, ``chardet``,
``clevercsv``) ship as ``.pyd`` files that land beside their package
and import the same way.

**Why a wheel set per interpreter.** Four of the eleven are compiled
against a specific CPython ABI, so ``cp311`` and ``cp313`` need
different files. Loading the wrong set is not a subtle failure --
the extension refuses to import -- but it is a confusing one, so
:func:`wheels_for` picks by tag and refuses rather than guessing.
"""

from __future__ import annotations

import hashlib
import ntpath
import posixpath
import re
import zipfile
from dataclasses import dataclass

from .host import Host
from .report import Failure, Outcome, Row

#: Where the payload carries them, relative to the application root.
#: One subdirectory per interpreter tag underneath -- ``cp311``,
#: ``cp313`` -- because four of the wheels are compiled against a
#: specific CPython ABI and the two sets cannot share a directory.
WHEELS = "vendor/wheels"

#: The interpreter minor versions the payload carries a wheel set for,
#: newest first. One list, read by three things that must not drift
#: apart: ``build.py`` fetches exactly these, the manifest beside the
#: wheels pins exactly these, and
#: :func:`.python_runtime.choose` prefers an interpreter on this list
#: over a newer one that is not.
#:
#: That last reader is the repair for a defect a real Windows runner
#: found twice. ``choose`` took the newest usable Python on the
#: machine; the payload carried cp311-cp314; the runner had 3.15.1 on
#: it as well as the 3.11 the job had set up. So the installation
#: landed on 3.15, found no cp315 wheel set, and installed no
#: independent readers at all -- on the machine that had the *most*
#: Python available. The first time this happened the newest was 3.14
#: and the answer was to add a fourth wheel set; that answer loses
#: again every October.
#:
#: cp315 is not on this list and cannot be: ``clevercsv`` 0.8.5
#: publishes no cp315 wheel, and no release of it does. A payload
#: cannot promise a set for a CPython its readers have not been built
#: for yet, so the chooser avoids landing there instead.
SERVED_MINORS = ((3, 14), (3, 13), (3, 12), (3, 11))

#: The same list as ABI tags, which is how the wheel filenames and the
#: per-interpreter directories spell it.
SERVED_TAGS = tuple(f"cp{major}{minor}" for major, minor in SERVED_MINORS)


def served(version: tuple[int, ...]) -> bool:
    """True when the payload carries a wheel set for this interpreter."""
    return tuple(version[:2]) in SERVED_MINORS


def wheel_dir(app_root: str, python_tag: str) -> str:
    """Where this interpreter's wheels are, inside the application.

    A function rather than a constant because the set is per
    interpreter, and the first version of this module forgot that:
    it listed ``vendor/wheels`` itself, saw two directory names,
    matched neither against a wheel filename, and reported that the
    payload carried no readers. The payload carried twenty-two. The
    CI job that installs for real is what found it.
    """
    return ntpath.join(app_root, *WHEELS.split("/"), python_tag)

#: Where they are unpacked to, relative to the application root. A
#: directory of its own rather than the application's: an upgrade
#: replaces the application tree, and the readers are not part of it.
SITE = "vendor/site"

#: The manifest, relative to the application root. Beside the wheels
#: under ``vendor/``, which is where ``build.py`` puts it -- not at
#: its repository path. This constant said the repository path for
#: three commits, so the step looked somewhere the payload never
#: writes and reported that a payload carrying twenty-two wheels
#: carried none. A real Windows installation is what caught it, and
#: only because the message had started naming the path it checked.
MANIFEST = "vendor/t2-windows-x64.txt"

#: What a wheel filename promises. ``py3-none-any`` serves every
#: interpreter; ``cp311-cp311-win_amd64`` serves exactly one.
_WHEEL = re.compile(
    r"^(?P<name>[A-Za-z0-9._]+)-(?P<version>[^-]+)"
    r"(?:-(?P<build>\d[^-]*))?"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$")

#: Hashes in the manifest. One package may list several -- one per
#: interpreter -- and any of them may be the file in front of us.
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


class ReaderRefused(Exception):
    """Raised instead of unpacking. Carries the owner-facing explanation.

    Modelled on :class:`~.payload.PayloadRefused`, and raised for the
    same reason: :class:`~.report.Failure` is a record of five things
    the owner needs to read, not an exception, and raising the record
    directly would have been a ``TypeError`` at the one moment this
    module is supposed to be refusing clearly.
    """

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.what_failed)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class Wheel:
    """One file, and the one thing that makes it the right one."""

    name: str
    digest: str
    universal: bool

    @property
    def package(self) -> str:
        return (_WHEEL.match(self.name) or _nomatch(self.name)).group("name")


def _nomatch(name: str):
    raise ReaderRefused(Failure(
        what_failed="reading the vendored reader set",
        why=(f"{name!r} is not a wheel filename. The set is built from "
             f"{MANIFEST} and nothing else belongs in it."),
        changed=(),
        not_changed=("the application", "the database", "the owner's data"),
        safe_next_action=("Reinstall from a published DeskPilot-Setup.exe. "
                          "This payload has been altered since it was "
                          "built.")))


def allowed_digests(manifest_text: str) -> set[str]:
    """Every digest the manifest vouches for.

    A set rather than a mapping from package to digest, because a
    wheel's filename is chosen by whoever built the archive and is
    therefore not evidence of anything. What is checked is that the
    bytes in front of us are bytes the manifest named; the filename
    only decides which interpreter they are for.
    """
    return set(_HASH.findall(manifest_text))


def wheels_for(names: list[str], *, python_tag: str) -> list[str]:
    """The subset of ``names`` this interpreter can load.

    ``python_tag`` is ``cp311``, ``cp313`` and so on. A pure-Python
    wheel (``py3-none-any``) is taken for every interpreter; a
    compiled one only when its tag matches exactly. Refusing an
    almost-right tag is deliberate: a cp311 extension on cp313 fails
    at import, deep inside a verification run, rather than here.
    """
    chosen = []
    for name in sorted(names):
        match = _WHEEL.match(name)
        if match is None:
            continue
        if match.group("platform") == "any":
            chosen.append(name)
        elif match.group("abi") == python_tag:
            chosen.append(name)
    return chosen


def verify(host: Host, wheel_dir: str, names: list[str],
           allowed: set[str]) -> list[Wheel]:
    """Hash every file before anything is unpacked. Refuses on the first bad one.

    All-or-nothing on purpose, and the same reasoning as
    :mod:`.payload`: a set of readers with one substituted file is
    worse than no readers, because the verification it then performs
    looks exactly like a real one.
    """
    out = []
    for name in names:
        blob = host.read_bytes(ntpath.join(wheel_dir, name))
        digest = hashlib.sha256(blob).hexdigest()
        if digest not in allowed:
            raise ReaderRefused(Failure(
                what_failed=f"verifying the independent reader {name}",
                why=(f"it hashes to sha256 {digest[:16]}..., which "
                     f"{MANIFEST} does not list. Nothing outside the "
                     f"manifest is unpacked."),
                changed=(),
                not_changed=("the application", "the database",
                             "the owner's data"),
                safe_next_action=("Reinstall from a published "
                                  "DeskPilot-Setup.exe. A reader that is "
                                  "not the one this build vouches for is "
                                  "worse than no reader, because the "
                                  "verification it performs would look "
                                  "like a real one.")))
        match = _WHEEL.match(name)
        out.append(Wheel(name, digest, match.group("platform") == "any"))
    return out


def unsafe_member(name: str) -> str:
    """Why this ZIP entry must not be written, or ``""``.

    The same checks :mod:`.payload` applies to the payload archive,
    for the same reason and against the same list. A wheel from PyPI
    is not expected to contain a traversal, but "not expected to" is
    what every extraction bug has in common, and the digest above
    proves the file is the one the manifest named -- not that the
    manifest named a safe one.
    """
    if not name or name.endswith("/"):
        return ""
    if name.startswith("/") or name.startswith("\\"):
        return "absolute path"
    if ntpath.splitdrive(name)[0]:
        return "drive letter"
    if "\\" in name:
        # zipfile treats a backslash as an ordinary character; Windows
        # treats it as a separator, so `a\..\..\evil` passes every
        # POSIX check and still escapes.
        return "backslash, which Windows reads as a separator"
    if any(part == ".." for part in name.split("/")):
        return "parent directory segment"
    return ""


#: The only top-level names that may be written into the import
#: directory. An allowlist rather than a denylist, and not a tidiness
#: measure: CleverCSV's wheel ships top-level ``docs``, ``src``,
#: ``man``, ``notes``, ``auxiliary``, ``stubs``, ``build``,
#: ``example`` and ``wheelhouse``, and its own ``top_level.txt``
#: declares every one of them. Unpacking a wheel as published would
#: put a directory called ``src`` and a directory called ``docs`` on
#: DeskPilot's ``sys.path``, where either could shadow a real module.
#:
#: Derived from what ``solvent.independent`` actually imports and
#: what those four packages depend on. A reader added to the manifest
#: without being added here is not unpacked, which fails visibly --
#: the import does not resolve and ``available()`` says the format is
#: not covered -- rather than silently widening the import path.
IMPORTABLE = frozenset({
    # The four readers themselves, by import name.
    "openpyxl", "docx", "clevercsv", "markdown_it",
    # What they need.
    "chardet", "et_xmlfile", "lxml", "mdurl", "packaging", "regex",
    "typing_extensions",
    # Not a reader: the IANA timezone database, which Windows lacks.
    "tzdata",
})


def importable(top: str) -> bool:
    """May this top-level name go on the import path?"""
    return top.removesuffix(".py") in IMPORTABLE


def members(archive: zipfile.ZipFile) -> tuple[list[str], list[str]]:
    """``(to_write, top_levels_refused)`` for one wheel.

    ``*.dist-info`` is kept -- a licence that ships with the code is
    the condition several of these licences attach. ``*.data`` is
    dropped. Everything else must be on :data:`IMPORTABLE`.
    """
    keep, refused = [], set()
    for name in archive.namelist():
        if name.endswith("/"):
            continue
        top = name.split("/")[0]
        if top.endswith(".data"):
            continue
        if top.endswith(".dist-info"):
            keep.append(name)
            continue
        if not importable(top):
            refused.add(top)
            continue
        keep.append(name)
    return keep, sorted(refused)


def unpack(host: Host, wheel_dir: str, into: str,
           wheels: list[Wheel]) -> list[str]:
    """Write the verified wheels into one directory. Returns what was written.

    Inspected completely before a byte is written, so a bad entry in
    the middle of an otherwise sound wheel cannot leave half a reader
    on disk.
    """
    import io

    planned: list[tuple[str, bytes]] = []
    left_out: dict[str, list[str]] = {}
    for wheel in wheels:
        blob = host.read_bytes(ntpath.join(wheel_dir, wheel.name))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            # Traversal is checked over *every* entry, before anything
            # is filtered. The allowlist ran first once, and silently
            # dropped `a\..\..\evil.py` because that is not an
            # importable top-level name -- so a tampered wheel
            # installed successfully and quietly. A hostile archive
            # must refuse, not be tidied away: selection is a
            # judgement about usefulness and has no business running
            # before a judgement about safety.
            for name in archive.namelist():
                why = unsafe_member(name)
                if why:
                    raise ReaderRefused(Failure(
                        what_failed=f"unpacking the reader {wheel.name}",
                        why=(f"it contains the entry {name!r}, which is a "
                             f"{why} and would write outside the "
                             f"destination."),
                        changed=(),
                        not_changed=("the application", "the database",
                                     "the owner's data",
                                     "every file in this wheel set"),
                        safe_next_action=("Reinstall from a published "
                                          "DeskPilot-Setup.exe. Nothing "
                                          "from this wheel set has been "
                                          "written.")))
            names, refused = members(archive)
            if refused:
                left_out[wheel.package] = refused
            for name in names:
                why = unsafe_member(name)
                if why:
                    raise ReaderRefused(Failure(
                        what_failed=f"unpacking the reader {wheel.name}",
                        why=(f"it contains the entry {name!r}, which is a "
                             f"{why} and would write outside the "
                             f"destination."),
                        changed=(),
                        not_changed=("the application", "the database",
                                     "the owner's data",
                                     "every file in this wheel set"),
                        safe_next_action=("Reinstall from a published "
                                          "DeskPilot-Setup.exe. Nothing "
                                          "from this wheel set has been "
                                          "written.")))
            for name in names:
                planned.append((posixpath.normpath(name),
                                archive.read(name)))
    written = []
    for relative, blob in planned:
        target = ntpath.join(into, *relative.split("/"))
        host.makedirs(ntpath.dirname(target))
        host.write_bytes(target, blob)
        written.append(relative)
    unpack.left_out = left_out
    return written


def row(found: int, expected: int, where: str) -> Row:
    """What the installation report says about the readers.

    A missing reader is reported, never hidden and never fatal:
    DeskPilot runs without them and ``solvent.independent.available``
    says what this host can actually produce. An installation that
    silently shipped no second opinion would be the worse outcome,
    because the first sign of it would be a declined job months later.
    """
    if found == expected and expected:
        return Row("Independent (T2) readers", Outcome.OK,
                   f"{found} verified package(s) unpacked to {where}")
    if not found:
        return Row("Independent (T2) readers", Outcome.WARN,
                   "none were installed; DeskPilot will decline work that "
                   "needs a second opinion rather than verify it itself")
    return Row("Independent (T2) readers", Outcome.WARN,
               f"{found} of {expected} unpacked; `solvent doctor` lists "
               "which formats can be independently verified")
