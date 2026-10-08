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
import ntpath
from dataclasses import dataclass

from .host import Host
from .report import Failure, Outcome, Row

#: DeskPilot's declared floor. An existing interpreter at or above this is reused.
MIN_VERSION = (3, 11)

#: What the installer fetches when nothing suitable is present.
#:
#: Only reached when no usable interpreter exists. An owner who already has
#: 3.11 or newer keeps it -- which is the whole point of the search above, and
#: the thing the previous version of this installer got wrong.
BOOTSTRAP_VERSION = "3.13.12"
BOOTSTRAP_URL = ("https://www.python.org/ftp/python/3.13.12/"
                 "python-3.13.12-amd64.exe")
#: SHA-256 of the file at :data:`BOOTSTRAP_URL`, computed from that URL over
#: HTTPS on 2026-10-08. The fail-closed gate before the installer is executed.
BOOTSTRAP_SHA256 = ("96159fcb523ae404b707186a75b4104ee23851e476a5e838e14584cf"
                    "1e03f981")
#: Published by python.org on the release page. Recorded so the owner can
#: cross-check this pin against the publisher without trusting this installer.
BOOTSTRAP_MD5 = "7588c11aecd9b64fc6213525c4a13c85"
BOOTSTRAP_BYTES = 29096776

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


#: Minor versions worth looking for, newest first. Patch level never appears
#: in an installation path -- 3.13.0 and 3.13.12 both live in ``Python313`` --
#: so searching by minor version covers every patch release of it.
MINORS = ("313", "314", "312", "311")

#: All-users install roots. What ``InstallAllUsers=1`` produces.
_MACHINE_ROOTS = (r"C:\Program Files", r"C:\Program Files (x86)", "C:\\")

#: Per-user install root, relative to a profile directory.
#:
#: This is where python.org's installer puts Python **by default**. Omitting it
#: was the defect that made a working interpreter invisible: the owner installs
#: Python, it works in their shell, and an installer that only searched the
#: all-users roots reports "WILL INSTALL" and offers to install a second copy.
_USER_SUFFIX = r"AppData\Local\Programs\Python"

#: The per-machine launcher, which can enumerate every registered install.
_LAUNCHER = r"C:\Windows\py.exe"


def machine_candidates() -> list[str]:
    """All-users installation paths, newest minor version first."""
    out: list[str] = []
    for minor in MINORS:
        for root in _MACHINE_ROOTS:
            out.append(ntpath.join(root, f"Python{minor}", "python.exe"))
    return out


def profile_candidates(host: Host) -> list[str]:
    """Per-user installation paths, for every profile on the machine.

    Every profile, not just the current one, and that is the point. The
    installer runs elevated, so ``%LOCALAPPDATA%`` may resolve to the
    administrator's profile while the Python the owner installed sits in
    theirs. Looking only at the current profile finds nothing on exactly the
    machine this is meant to fix.

    ``C:\\Users`` is assumed and ``USERPROFILE``'s parent is added, which
    covers a machine whose profiles were relocated to another drive. An earlier
    version derived the root from ``LOCALAPPDATA`` by stripping two components,
    which yields the *profile* directory rather than the directory holding the
    profiles -- it searched inside one user instead of across all of them.
    """
    roots = {"C:\\Users"}
    profile = host.environ().get("USERPROFILE", "")
    if profile:
        roots.add(ntpath.dirname(profile))
    out: list[str] = []
    for users_dir in sorted(roots):
        if not users_dir or not host.is_dir(users_dir):
            continue
        for entry in host.listdir(users_dir):
            base = ntpath.join(users_dir, entry, _USER_SUFFIX)
            if not host.is_dir(base):
                continue
            for minor in MINORS:
                out.append(ntpath.join(base, f"Python{minor}", "python.exe"))
    return out


def registry_candidates(host: Host) -> list[str]:
    """Interpreters that registered themselves under PEP 514.

    The authoritative source on Windows, and the one that does not care where
    the installer chose to put things or whether PATH was ever touched.
    """
    return list(host.registry_python_paths())


def launcher_candidates(host: Host) -> list[str]:
    """Interpreters the ``py`` launcher knows about.

    ``py -0p`` lists every registered installation with its path. A last
    resort: it covers a distribution that registered itself in a way the
    registry sweep did not anticipate, and it costs one process.
    """
    if not host.exists(_LAUNCHER):
        return []
    result = host.run([_LAUNCHER, "-0p"], timeout=60.0)
    if not result.ok:
        return []
    out: list[str] = []
    for line in result.output.splitlines():
        # Lines look like " -V:3.13 *        C:\path\to\python.exe"; the path
        # is whatever follows the last run of two or more spaces.
        text = line.strip()
        if not text or text.lower().startswith("-v:") is False and ":" not in text:
            continue
        marker = text.rfind("  ")
        candidate = text[marker:].strip() if marker > 0 else ""
        if candidate.lower().endswith("python.exe"):
            out.append(candidate)
    return out


def path_candidates(host: Host) -> list[str]:
    """Whatever ``python`` means on this process's PATH, if anything."""
    out = []
    for program in ("python.exe", "python3.exe", "python"):
        found = host.which(program)
        if found:
            out.append(found)
    return out


#: Kept as a name because tests and the generated wizard list refer to it.
SEARCH = tuple(machine_candidates())


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
    # Registry first: it is the authoritative record and it is right about
    # machines the path lists are wrong about. The rest are fallbacks, in
    # descending order of how much they are to be trusted.
    candidates = (list(extra)
                  + registry_candidates(host)
                  + path_candidates(host)
                  + launcher_candidates(host)
                  + machine_candidates()
                  + profile_candidates(host))
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


def search_report(host: Host) -> dict[str, list[str]]:
    """Every place that was looked, grouped by how it was looked up.

    Exists because of a specific failure: an owner with a working Python was
    told the installer would install one, and nothing on the screen said where
    it had looked. A list of locations turns "it did not find my Python" into
    a question with an answer.
    """
    return {
        "registry (PEP 514)": registry_candidates(host),
        "PATH": path_candidates(host),
        "py launcher": launcher_candidates(host),
        "all-users locations": machine_candidates(),
        "per-user locations": profile_candidates(host),
    }


def row(found: list[Interpreter], chosen: Interpreter | None,
        host: Host | None = None) -> Row:
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
    facts: dict = {}
    detail = f"WILL INSTALL {BOOTSTRAP_VERSION} from python.org"
    if host is not None:
        searched = search_report(host)
        looked = sum(len(v) for v in searched.values())
        detail += (f" - no existing Python was found in {looked} checked "
                   f"locations, including the registry")
        facts["searched"] = {name: paths for name, paths in searched.items()}
    return Row("Python", Outcome.WILL_INSTALL, detail, facts=facts)


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
