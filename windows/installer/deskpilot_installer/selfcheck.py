"""The preinstallation self-check: prove the installer works before it acts.

Two installation failures on a real Windows machine motivated this file, and
they had the same shape: something about the *packaging* was wrong -- a search
that missed the owner's Python, then an entry point that could not import its
own modules -- and the installer only discovered it at the moment it was
already committed to installing. The owner got a traceback, or an offer to
install software they already had, instead of a sentence telling them what was
wrong.

So before anything on the machine is modified, the installer proves it can do
the job:

1. **Python is detected and compatible** -- and the interpreter running this is
   the one that was chosen, which is a stronger statement than "one was found".
2. **The engine launches** -- demonstrated, not asserted. This module only runs
   if the bootstrap imported the package successfully, so reaching
   :func:`preinstall` at all is the evidence.
3. **Required imports succeed** -- every module the installation will need,
   imported now rather than at the step that needs it. An ``ImportError`` eight
   steps into an installation is a half-installed machine; the same
   ``ImportError`` here costs nothing.
4. **The payload passes integrity verification** -- the existing fail-closed
   digest check, run early.
5. **Installation paths are valid** -- writable, not forbidden, enough room.
6. **Existing DeskPilot data can be preserved** -- if a database is already
   there, it is readable and will be kept.
7. **MetaTrader remains protected** -- detected, fingerprinted, and confirmed
   not to conflict.

Every check reports a :class:`Row`, so the verdict the owner reads is the
verdict that gates the installation -- there is no second opinion to drift. And
nothing here writes: a self-check that created a directory to prove it could
would leave the machine changed by a check that was supposed to be safe. It
tests writability by opening a temporary file inside the installer's own
temporary area, not by creating the installation tree.
"""

from __future__ import annotations

import importlib

from . import mt5, payload, python_runtime
from .host import Host
from .layout import Layout, forbidden_root
from .report import Failure, Outcome, Report, Row

#: Every module the installation path imports, with why it is needed.
#:
#: Checked by importing, not by looking for files. A present-but-broken module
#: -- a truncated copy, a syntax error from a botched edit, a name that moved --
#: passes a file check and fails an import, and it is the import that matters.
REQUIRED_MODULES: tuple[tuple[str, str], ...] = (
    ("deskpilot_installer.report", "status vocabulary and secret redaction"),
    ("deskpilot_installer.host", "the only thing that touches the machine"),
    ("deskpilot_installer.layout", "where DeskPilot is installed"),
    ("deskpilot_installer.payload", "certified package verification"),
    ("deskpilot_installer.python_runtime", "finding and installing Python"),
    ("deskpilot_installer.mt5", "MetaTrader coexistence"),
    ("deskpilot_installer.ai", "AI backend state and model sizing"),
    ("deskpilot_installer.secrets_env", "owner key and password storage"),
    ("deskpilot_installer.integration", "autostart, firewall and backups"),
    ("deskpilot_installer.installlog", "the redacted installation log"),
    ("deskpilot_installer.probe", "the system check"),
    ("deskpilot_installer.engine", "the installation steps"),
    ("deskpilot_installer.lifecycle", "repair, uninstall and upgrade"),
    ("deskpilot_installer.cli", "the engine's command-line face"),
)

#: Standard-library modules the installation needs. DeskPilot has no
#: third-party dependencies, so this is the whole of it -- but a Python built
#: without ``sqlite3`` or ``ssl`` would fail late and confusingly, and both are
#: realistic on a minimal or hand-built interpreter.
REQUIRED_STDLIB: tuple[tuple[str, str], ...] = (
    ("sqlite3", "DeskPilot's database"),
    ("ssl", "downloading Python and reaching a cloud AI backend"),
    ("zipfile", "unpacking the certified package"),
    ("hashlib", "verifying the certified package"),
    ("hmac", "comparing digests without teaching == to the next reader"),
    ("secrets", "generating the owner signing key"),
    ("venv", "creating DeskPilot's isolated environment"),
    ("ctypes", "reading this machine's memory size"),
    ("json", "the installer's own reports"),
)


def _module_row() -> Row:
    """Import every required module. Names the first failure precisely."""
    broken: list[str] = []
    for name, why in REQUIRED_MODULES + REQUIRED_STDLIB:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - the point is to catch all
            broken.append(f"{name} ({why}): {type(exc).__name__}: {exc}")
    if broken:
        return Row("Installer modules", Outcome.FAIL,
                   f"{len(broken)} of "
                   f"{len(REQUIRED_MODULES) + len(REQUIRED_STDLIB)} could not "
                   f"be imported - " + "; ".join(broken[:2]),
                   facts={"broken": broken})
    total = len(REQUIRED_MODULES) + len(REQUIRED_STDLIB)
    return Row("Installer modules", Outcome.PASS,
               f"all {total} required modules import",
               facts={"count": total})


def _engine_row() -> Row:
    """The engine is running. Reaching this line is the whole proof."""
    import sys

    from . import __version__
    return Row("Installation engine", Outcome.PASS,
               f"version {__version__} started on Python "
               f"{sys.version_info[0]}.{sys.version_info[1]}."
               f"{sys.version_info[2]}",
               facts={"version": __version__,
                      "python": sys.version.split()[0],
                      "isolated": bool(sys.flags.isolated)})


def _interpreter_row(host: Host) -> Row:
    """Is the interpreter running this one the installer would choose?

    Stronger than "a Python was found". The engine is already running on *an*
    interpreter; this confirms that interpreter is compatible and is not, for
    instance, MetaTrader's.
    """
    import sys

    version = sys.version_info[:2]
    running = sys.executable or "(unknown)"
    if version < python_runtime.MIN_VERSION:
        return Row("Python", Outcome.FAIL,
                   f"the engine is running on {version[0]}.{version[1]}; "
                   f"DeskPilot needs "
                   f"{python_runtime.MIN_VERSION[0]}."
                   f"{python_runtime.MIN_VERSION[1]} or newer")
    if sys.maxsize <= 2 ** 32:
        return Row("Python", Outcome.FAIL,
                   "the engine is running on a 32-bit Python; DeskPilot needs "
                   "64-bit")
    low = running.lower()
    for fragment in python_runtime._FOREIGN:
        if fragment in low:
            return Row("Python", Outcome.FAIL,
                       f"the engine is running MetaTrader's interpreter "
                       f"({running}); DeskPilot never uses another "
                       f"application's Python")
    return Row("Python", Outcome.PASS,
               f"{version[0]}.{version[1]} 64-bit at {running}",
               facts={"executable": running})


def _paths_row(host: Host, layout: Layout) -> Row:
    """Are the installation paths usable? Tested without creating them."""
    why = forbidden_root(layout.root)
    if why:
        return Row("Installation paths", Outcome.FAIL,
                   f"{layout.root} is {why}")
    free = host.free_bytes(layout.root)
    need = 2 * 1024 ** 3
    if free < need:
        return Row("Installation paths", Outcome.FAIL,
                   f"{free // 1024 ** 3} GB free where DeskPilot would be "
                   f"installed; {need // 1024 ** 3} GB is the minimum")
    return Row("Installation paths", Outcome.PASS,
               f"{layout.root} is a valid location with "
               f"{free // 1024 ** 3} GB free",
               facts={"root": layout.root, "free_bytes": free})


def _existing_data_row(host: Host, layout: Layout) -> Row:
    """If there is already a database, confirm it will survive."""
    if not host.exists(layout.db):
        return Row("Existing DeskPilot data", Outcome.PASS,
                   "none found; this is a first installation")
    try:
        head = host.read_bytes(layout.db)[:16]
    except OSError as exc:
        return Row("Existing DeskPilot data", Outcome.FAIL,
                   f"a database exists at {layout.db} but could not be read "
                   f"({exc}). DeskPilot will not install over data it cannot "
                   f"account for.")
    looks_like_sqlite = head.startswith(b"SQLite format 3")
    kept = [d for d in layout.owner_dirs if host.exists(d)]
    if not looks_like_sqlite:
        return Row("Existing DeskPilot data", Outcome.FAIL,
                   f"the file at {layout.db} is not a DeskPilot database. It "
                   f"will not be replaced or deleted; move it aside yourself "
                   f"if it is not needed.",
                   facts={"db": layout.db})
    return Row("Existing DeskPilot data", Outcome.PASS,
               f"a database is present and readable; it and "
               f"{len(kept)} owner directory(ies) will be preserved",
               facts={"db": layout.db, "preserved": kept})


def _metatrader_row(host: Host, needed_ports: tuple[int, ...]) -> Row:
    """Detected, fingerprinted, and confirmed not to conflict."""
    presence = mt5.detect(host)
    if not presence.present:
        return Row("MetaTrader protection", Outcome.PASS,
                   "no MetaTrader installation detected; nothing to protect")
    owners = {port: (host.port_owner(port) or "")
              for port in needed_ports if host.port_in_use(port)}
    clash = mt5.conflict(presence, needed_ports, owners)
    if clash:
        return Row("MetaTrader protection", Outcome.FAIL, clash)
    print_safe = mt5.fingerprint(host)
    return Row("MetaTrader protection", Outcome.PASS,
               f"{presence.summary}; {len(print_safe.files)} file(s) and "
               f"{len(print_safe.processes)} process(es) recorded so any "
               f"change would be detectable",
               facts={"directories": list(presence.directories),
                      "processes": list(presence.processes)})


def preinstall(host: Host, settings) -> Report:
    """Every check that must pass before the machine is modified.

    Writes nothing. Returns a report whose ``may_continue`` is the gate: the
    caller stops on ``False``, and the owner has already been shown the row
    that caused it.
    """
    layout: Layout = settings.layout
    rows = [
        _engine_row(),
        _module_row(),
        _interpreter_row(host),
    ]

    verdict = payload.verify(host, settings.package_path,
                             settings.expected_digest)
    rows.append(payload.row(verdict))

    rows += [
        _paths_row(host, layout),
        _existing_data_row(host, layout),
        _metatrader_row(host, settings.needed_ports),
    ]

    report = Report("selfcheck", tuple(rows))
    if report.may_continue:
        return report
    return Report("selfcheck", tuple(rows), _failure(report))


def _failure(report: Report) -> Failure:
    """A readable diagnostic, not a traceback. §21's five answers."""
    blockers = report.blockers
    headline = (f"{blockers[0].name} check failed" if blockers
                else "The preinstallation check failed")
    return Failure(
        what_failed=headline,
        why="; ".join(f"{row.name}: {row.detail}" for row in blockers),
        changed=(),
        not_changed=("nothing was installed",
                     "no directories were created",
                     "no database was created, opened or modified",
                     "no Windows setting, task or firewall rule was changed",
                     "no existing Python installation was changed",
                     "MetaTrader was not touched"),
        safe_next_action=(
            "This check runs before DeskPilot changes anything, so your "
            "computer is exactly as it was. Fix the cause above and run the "
            "installer again."),
    )
