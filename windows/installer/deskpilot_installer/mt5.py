"""MetaTrader coexistence. Detection only, and a way to prove it stayed that way.

MetaTrader is not part of DeskPilot and DeskPilot is not part of MetaTrader.
The only relationship is that the owner may run both on one VPS, and the owner's
trading terminal is the thing on that machine with real money attached to it.
So this module is written around a single rule: **look, never touch.**

There is no function here that stops, starts, restarts, configures, or writes
anything belonging to MetaTrader, and there is no code path that could be given
a flag to make it do so. :func:`detect` reads. :func:`fingerprint` reads.
:func:`conflict` reaches a verdict and returns it to the caller, who is expected
to abandon the installation rather than resolve the conflict by force.

:func:`fingerprint` exists for the holdout test in particular. A promise that
the installer does not disturb MetaTrader is worth exactly as much as the
evidence behind it, so the test takes a snapshot before installing, installs,
snapshots again, and compares. The snapshot deliberately covers the three
things that would matter to a trader -- the process is the same process, the
files are the same bytes, the firewall rules are the same rules -- rather than
only the easy one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .host import Host

#: Executables that mean MetaTrader is running. Lowercased for comparison.
PROCESS_NAMES = ("terminal64.exe", "terminal.exe", "metaeditor64.exe",
                 "metaeditor.exe", "metatester64.exe", "metatester.exe")

#: Where MetaTrader usually is. Presence of any of these means "installed".
INSTALL_HINTS = (
    r"C:\Program Files\MetaTrader 5",
    r"C:\Program Files (x86)\MetaTrader 5",
    r"C:\Program Files\MetaTrader 4",
    r"C:\Program Files (x86)\MetaTrader 4",
)

#: Per-user data, which is where EAs and broker settings actually live.
DATA_HINT_SUFFIX = r"AppData\Roaming\MetaQuotes"


@dataclass(frozen=True, slots=True)
class Presence:
    """What was found. ``running`` and ``installed`` are separate questions."""

    installed: bool
    running: bool
    directories: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.installed or self.running

    @property
    def summary(self) -> str:
        if not self.present:
            return "not detected"
        if self.running:
            return ("MetaTrader detected and running - PROTECTED, "
                    "no changes will be made")
        return "MetaTrader detected - PROTECTED, no changes will be made"


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A snapshot of everything about MetaTrader the installer must not alter.

    Compared by equality. Any difference at all is a failure of the holdout,
    which is the correct sensitivity: the installer has no business causing
    *any* of these to change, so there is no tolerance to tune.
    """

    processes: tuple[tuple[str, int], ...] = ()
    #: ``(path, sha256, mtime)`` for every file under a MetaTrader directory.
    #:
    #: Content-hashed rather than sized. A size-and-mtime snapshot misses the
    #: realistic case: a configuration value edited in place to another value
    #: of the same length, with the modification time preserved. For a broker
    #: config holding an account number that is the change that matters most,
    #: and the hash costs nothing here because determining the size already
    #: required reading the whole file.
    files: tuple[tuple[str, str, int], ...] = ()
    #: Firewall rule names that mention MetaTrader.
    firewall_rules: tuple[str, ...] = ()

    def differences(self, other: "Fingerprint") -> list[str]:
        """Human-readable list of what changed. Empty means untouched."""
        out: list[str] = []
        if self.processes != other.processes:
            before = dict(self.processes)
            after = dict(other.processes)
            for name, pid in self.processes:
                if name not in after:
                    out.append(f"MetaTrader process {name} (pid {pid}) is gone")
                elif after[name] != pid:
                    out.append(f"MetaTrader process {name} restarted: "
                               f"pid {pid} -> {after[name]}")
            for name, pid in other.processes:
                if name not in before:
                    out.append(f"a new MetaTrader process appeared: {name} "
                               f"(pid {pid})")
        if self.files != other.files:
            was = {p: (d, m) for p, d, m in self.files}
            now = {p: (d, m) for p, d, m in other.files}
            for path in sorted(set(was) | set(now)):
                if path not in now:
                    out.append(f"a MetaTrader file was deleted: {path}")
                elif path not in was:
                    out.append(f"a file was added to MetaTrader: {path}")
                elif was[path] != now[path]:
                    out.append(f"a MetaTrader file was modified: {path}")
        if self.firewall_rules != other.firewall_rules:
            out.append(f"MetaTrader firewall rules changed: "
                       f"{list(self.firewall_rules)} -> "
                       f"{list(other.firewall_rules)}")
        return out


def _mt5_dirs(host: Host) -> list[str]:
    found = [d for d in INSTALL_HINTS if host.is_dir(d)]
    profile = host.environ().get("USERPROFILE", "")
    if profile:
        candidate = profile.rstrip("\\") + "\\" + DATA_HINT_SUFFIX
        if host.is_dir(candidate):
            found.append(candidate)
    appdata = host.environ().get("APPDATA", "")
    if appdata:
        candidate = appdata.rstrip("\\") + "\\MetaQuotes"
        if host.is_dir(candidate) and candidate not in found:
            found.append(candidate)
    return found


def detect(host: Host) -> Presence:
    """Is MetaTrader here, and is it running? Reads only."""
    running = []
    for proc in host.processes():
        if proc.name.lower() in PROCESS_NAMES:
            running.append(proc.name)
    dirs = _mt5_dirs(host)
    return Presence(installed=bool(dirs), running=bool(running),
                    directories=tuple(dirs), processes=tuple(sorted(running)))


def fingerprint(host: Host) -> Fingerprint:
    """Snapshot MetaTrader's processes, files and firewall rules."""
    procs = tuple(sorted((p.name, p.pid) for p in host.processes()
                         if p.name.lower() in PROCESS_NAMES))
    files: list[tuple[str, int, int]] = []
    for root in _mt5_dirs(host):
        files.extend(_walk(host, root))
    rules = tuple(sorted(_firewall_rules_mentioning_mt5(host)))
    return Fingerprint(processes=procs, files=tuple(sorted(files)),
                       firewall_rules=rules)


def _walk(host: Host, root: str,
          depth: int = 0) -> list[tuple[str, str, int]]:
    """Every file under ``root`` with a digest of its contents and its mtime.

    Bounded depth.

    Depth is capped because a broker's terminal directory can be enormous and
    the installer is not entitled to spend minutes reading it; the shallow
    layers are where configuration and EAs live, which is what matters.
    """
    if depth > 4:
        return []
    out: list[tuple[str, str, int]] = []
    for name in host.listdir(root):
        path = root.rstrip("\\") + "\\" + name
        if host.is_dir(path):
            out.extend(_walk(host, path, depth + 1))
        else:
            out.append((path, _digest(host, path), _mtime(host, path)))
    return out


def _digest(host: Host, path: str) -> str:
    """SHA-256 of a file's contents, or a marker for one that cannot be read.

    An unreadable file gets a distinct marker rather than an empty string, so
    "this file became unreadable" is a difference the holdout reports instead
    of two unreadable files comparing equal.
    """
    try:
        return hashlib.sha256(host.read_bytes(path)).hexdigest()
    except (OSError, KeyError):
        return "unreadable"


def _mtime(host: Host, path: str) -> int:
    getter = getattr(host, "mtime", None)
    if getter is None:
        return 0
    try:
        return int(getter(path))
    except (OSError, KeyError, TypeError, ValueError):
        return 0


def _firewall_rules_mentioning_mt5(host: Host) -> list[str]:
    result = host.run(["netsh.exe", "advfirewall", "firewall", "show", "rule",
                       "name=all"], timeout=120.0)
    if not result.ok:
        return []
    names: list[str] = []
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        if label.strip().lower().startswith("rule name"):
            text = value.strip()
            low = text.lower()
            if "metatrader" in low or "terminal64" in low or "metaquotes" in low:
                names.append(text)
    return names


def conflict(presence: Presence, needed_ports: tuple[int, ...],
             port_owner: dict[int, str]) -> str:
    """Why DeskPilot cannot be installed without disturbing MetaTrader.

    Returns ``""`` when the two can coexist, which is the normal answer: they
    share a machine, not a port and not a directory. When it does return a
    reason the caller must stop. Resolving the conflict is not on offer,
    because every way of resolving it involves touching MetaTrader.
    """
    if not presence.present:
        return ""
    for port in needed_ports:
        owner = (port_owner.get(port) or "").lower()
        if any(name in owner for name in PROCESS_NAMES) or "metatrader" in owner:
            return (f"MetaTrader is using the port DeskPilot needs ({port}). "
                    f"DeskPilot will not reconfigure or restart MetaTrader to "
                    f"take it.")
    return ""
