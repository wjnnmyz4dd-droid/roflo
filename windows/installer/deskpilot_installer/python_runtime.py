"""Finding a usable Python, and fetching one when there is none.

DeskPilot is pure standard library -- its whole test suite passes with
third-party packages switched off -- so "install Python" is the entire
dependency story. There is no pip step in a normal installation and this
module is the only reason the installer touches the network at all.

Three things here are deliberate.

**MetaTrader's Python is invisible to us.** A trading terminal often ships its
own interpreter, and it is on exactly the kind of path a naive search would
find first. Adopting it would mean DeskPilot's packages and MetaTrader's
packages share a site-packages directory, which is the one way a back-office
installer could break someone's trading. :func:`discover` therefore discards
any interpreter found under a MetaTrader directory, by path, before it is even
version-checked.

**A downloaded installer is verified before it is run.** The digest below was
computed from the official python.org URL over HTTPS. It is compared with
:func:`hmac.compare_digest` and a mismatch aborts, because an installer that
runs whatever arrived is a way to run whatever arrived. What this *cannot* tell
you is whether python.org served different bytes to someone else: the digest's
provenance is one download, cross-checkable by the owner against the MD5 that
python.org publishes on the release page, and that limitation is stated rather
than dressed up.

**The version floor and the version offered are different numbers.** DeskPilot
requires 3.11. python.org no longer publishes a *Windows installer* for 3.11 or
3.12 -- both are in security-only maintenance, source only -- so the only
versions a Windows owner can actually install are 3.13 and later. The floor
stays at 3.11 so an existing 3.11 or 3.12 is reused rather than replaced; the
bootstrap offers 3.13.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from .host import Host
from .report import Failure, Outcome, Row

#: DeskPilot's declared floor. An existing interpreter at or above this is reused.
MIN_VERSION = (3, 11)

#: What the installer fetches when nothing suitable is present.
BOOTSTRAP_VERSION = "3.13.9"
BOOTSTRAP_URL = ("https://www.python.org/ftp/python/3.13.9/"
                 "python-3.13.9-amd64.exe")
#: SHA-256 of the file at :data:`BOOTSTRAP_URL`, computed from that URL over
#: HTTPS on 2026-10-07. The fail-closed gate before the installer is executed.
BOOTSTRAP_SHA256 = ("200ddff856bbff949d2cc1be42e8807c07538abd6b6966d5113a094c"
                    "f628c5c5")
#: Published by python.org on the release page. Recorded so the owner can
#: cross-check this pin against the publisher without trusting this installer.
BOOTSTRAP_MD5 = "3e86f4361963db7f02fd6615b90aab32"
BOOTSTRAP_BYTES = 28761776

#: Arguments for an unattended, all-users install that stays out of the way.
#:
#: ``InstallAllUsers=1`` because DeskPilot runs as a scheduled task that is not
#: the installing user's session. ``PrependPath=0`` because putting a new
#: Python at the front of the system PATH is how an installer breaks whatever
#: already depended on a different one -- DeskPilot uses an absolute path to
#: its own interpreter and never needs PATH. ``Include_launcher=0`` for the
#: same reason: the ``py`` launcher is global state another application may
#: already own.
BOOTSTRAP_ARGS = ("/quiet", "InstallAllUsers=1", "PrependPath=0",
                  "Include_launcher=0", "Include_test=0", "Include_doc=0",
                  "AssociateFiles=0", "Shortcuts=0")

#: Fragments that mean an interpreter belongs to something else.
_FOREIGN = ("metatrader", "metaquotes", "\\mt4\\", "\\mt5\\")


@dataclass(frozen=True, slots=True)
class Interpreter:
    """One Python found on the machine."""

    path: str
    version: tuple[int, ...]
    is_64bit: bool
    #: Why this interpreter is unusable, or ``""``.
    rejected: str = ""

    @property
    def usable(self) -> bool:
        return not self.rejected

    @property
    def version_text(self) -> str:
        return ".".join(str(p) for p in self.version) if self.version else "unknown"


#: Where to look, in order. The first usable one wins.
SEARCH = (
    r"C:\Program Files\Python313\python.exe",
    r"C:\Program Files\Python312\python.exe",
    r"C:\Program Files\Python311\python.exe",
    r"C:\Python313\python.exe",
    r"C:\Python312\python.exe",
    r"C:\Python311\python.exe",
)


def _interrogate(host: Host, path: str) -> Interpreter:
    """Ask the interpreter what it is. Never infer a version from its path."""
    low = path.lower()
    for fragment in _FOREIGN:
        if fragment in low:
            return Interpreter(path, (), False,
                               rejected="it belongs to MetaTrader; DeskPilot "
                                        "never uses another application's Python")
    probe = host.run([path, "-I", "-c",
                      "import sys;"
                      "print(sys.version_info[0], sys.version_info[1],"
                      " sys.maxsize > 2**32)"], timeout=60.0)
    if not probe.ok:
        return Interpreter(path, (), False,
                           rejected="it did not run")
    parts = probe.stdout.split()
    if len(parts) < 3:
        return Interpreter(path, (), False, rejected="it gave no version")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return Interpreter(path, (), False, rejected="it gave no version")
    sixtyfour = parts[2].strip().lower() == "true"
    version = (major, minor)
    if version < MIN_VERSION:
        return Interpreter(path, version, sixtyfour,
                           rejected=f"DeskPilot needs Python "
                                    f"{MIN_VERSION[0]}.{MIN_VERSION[1]} or newer")
    if not sixtyfour:
        return Interpreter(path, version, sixtyfour,
                           rejected="it is 32-bit; DeskPilot needs 64-bit Python")
    return Interpreter(path, version, sixtyfour)


def discover(host: Host, extra: tuple[str, ...] = ()) -> list[Interpreter]:
    """Every Python the installer considered, usable or not, in search order.

    Returns the rejects too: "there is a Python here and it is the wrong one"
    is a different message from "there is no Python", and the owner is entitled
    to the more specific one.
    """
    seen: set[str] = set()
    found: list[Interpreter] = []
    candidates = list(extra) + list(SEARCH)
    on_path = host.which("python.exe") or host.which("python")
    if on_path:
        candidates.append(on_path)
    for path in candidates:
        key = path.lower()
        if key in seen or not host.exists(path):
            continue
        seen.add(key)
        found.append(_interrogate(host, path))
    return found


def choose(found: list[Interpreter]) -> Interpreter | None:
    """The newest usable interpreter, or ``None``."""
    usable = [i for i in found if i.usable]
    if not usable:
        return None
    return max(usable, key=lambda i: i.version)


def row(found: list[Interpreter], chosen: Interpreter | None) -> Row:
    """The system-check line. ``WILL INSTALL`` is a pass, not a problem."""
    if chosen is not None:
        return Row("Python", Outcome.PASS,
                   f"{chosen.version_text} 64-bit at {chosen.path}",
                   facts={"path": chosen.path, "version": chosen.version_text})
    if found:
        why = "; ".join(f"{i.path}: {i.rejected}" for i in found[:3])
        return Row("Python", Outcome.WILL_INSTALL,
                   f"WILL INSTALL {BOOTSTRAP_VERSION} - the Python already "
                   f"here cannot be used ({why})",
                   facts={"rejected": [{"path": i.path, "why": i.rejected}
                                       for i in found]})
    return Row("Python", Outcome.WILL_INSTALL,
               f"WILL INSTALL {BOOTSTRAP_VERSION} from python.org")


def verify_download(host: Host, path: str) -> str:
    """``""`` if the downloaded installer is the expected bytes, else why not."""
    if not host.exists(path):
        return "the download did not arrive"
    data = host.read_bytes(path)
    if len(data) != BOOTSTRAP_BYTES:
        return (f"the download is {len(data)} bytes; "
                f"{BOOTSTRAP_BYTES} was expected")
    import hashlib
    actual = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, BOOTSTRAP_SHA256):
        return ("the download does not match the digest this installer was "
                "built for")
    return ""


def download_failure(why: str) -> Failure:
    """§21 explanation for a Python bootstrap that did not work."""
    return Failure(
        what_failed="Python installation failed",
        why=why,
        changed=(),
        not_changed=("DeskPilot was not installed",
                     "no existing Python on this computer was changed",
                     "MetaTrader was not touched"),
        safe_next_action=(f"Install Python {BOOTSTRAP_VERSION} (64-bit) by hand "
                          f"from python.org, ticking 'Add python.exe to PATH', "
                          f"then run this installer again. It will detect the "
                          f"Python you installed and continue."),
    )
