"""Where DeskPilot lives on Windows, and where it refuses to live.

One directory tree, chosen rather than asked about. The owner is not made to
pick an installation path during a normal install, because the only reason to
ask would be to let them answer badly: every alternative they might pick is on
the refusal list below.

The separation inside the tree is the part that matters later. ``App`` is
replaceable -- an upgrade overwrites it and an uninstall deletes it. ``Data``,
``Config``, ``Logs`` and ``Backups`` are the owner's, and nothing in the
upgrade or uninstall path may remove them without an explicit instruction. That
boundary is what makes "uninstall but keep my business records" expressible at
all, so it is drawn here, once, rather than re-derived by each operation.
"""

from __future__ import annotations

import ntpath
from dataclasses import dataclass

#: The default root. Not configurable during a normal installation.
DEFAULT_ROOT = r"C:\DeskPilot"

#: Loopback port the control centre binds. Matches ``solvent web --port``.
WEB_PORT = 8765


@dataclass(frozen=True, slots=True)
class Layout:
    """Every path the installer writes, derived from one root."""

    root: str = DEFAULT_ROOT

    def _join(self, *parts: str) -> str:
        return ntpath.join(self.root, *parts)

    # -- replaceable -----------------------------------------------------
    @property
    def app(self) -> str:
        """The extracted application. Overwritten by upgrade, removed by uninstall."""
        return self._join("App")

    @property
    def venv(self) -> str:
        """DeskPilot's own Python environment. Never another application's."""
        return self._join("venv")

    @property
    def engine(self) -> str:
        """The installation engine, kept for repair, upgrade and uninstall.

        Separate from ``App``, which the payload replaces wholesale on every
        install and upgrade. The engine used to live only in the installer's
        temporary directory, which Windows deletes when the installer exits --
        so the uninstaller pointed at a path that had never existed and the
        clean uninstall path could not run.
        """
        return self._join("Engine")

    @property
    def engine_launcher(self) -> str:
        """The script the uninstaller and a repair run."""
        return ntpath.join(self.engine, "deskpilot-engine.py")

    # -- the owner's -----------------------------------------------------
    @property
    def data(self) -> str:
        return self._join("Data")

    @property
    def config(self) -> str:
        return self._join("Config")

    @property
    def logs(self) -> str:
        return self._join("Logs")

    @property
    def backups(self) -> str:
        return self._join("Backups")

    # -- individual files ------------------------------------------------
    @property
    def db(self) -> str:
        """The one real database. Every authority is pointed at this path."""
        return ntpath.join(self.data, "solvent.db")

    @property
    def spool(self) -> str:
        return ntpath.join(self.data, "spool")

    @property
    def heartbeat(self) -> str:
        return ntpath.join(self.data, "heartbeat")

    @property
    def env_file(self) -> str:
        """Holds the owner key and the web password hash. Never backed up."""
        return ntpath.join(self.config, "solvent.env")

    @property
    def install_log(self) -> str:
        return ntpath.join(self.logs, "install.log")

    @property
    def state_file(self) -> str:
        """Records what this installer did, for repair, upgrade and rollback."""
        return ntpath.join(self.config, "install-state.json")

    @property
    def owner_dirs(self) -> tuple[str, ...]:
        """Directories an uninstall preserves unless told otherwise."""
        return (self.data, self.config, self.logs, self.backups)

    @property
    def replaceable_dirs(self) -> tuple[str, ...]:
        """Directories an upgrade or uninstall may remove outright."""
        return (self.app, self.venv, self.engine)

    @property
    def all_dirs(self) -> tuple[str, ...]:
        return self.replaceable_dirs + self.owner_dirs


#: Path fragments DeskPilot must never install into, and why.
#:
#: MetaTrader is first because it is the one that would do real harm: writing
#: into a broker's terminal directory is the failure this whole installer is
#: built to avoid. The rest are places that look writable and are not durable.
_FORBIDDEN = (
    ("metatrader", "MetaTrader's own directory - DeskPilot never writes there"),
    ("metaquotes", "MetaQuotes application data - DeskPilot never writes there"),
    ("\\downloads", "the Downloads folder, which the owner will clear one day"),
    ("\\desktop", "the Desktop, which is not where a service belongs"),
    ("\\temp\\", "a temporary directory, which Windows may delete"),
    ("\\tmp\\", "a temporary directory, which Windows may delete"),
    ("\\appdata\\local\\temp", "a temporary extraction directory"),
    ("\\windows\\", "the Windows directory itself"),
    ("\\program files\\metatrader", "MetaTrader's program directory"),
)


def forbidden_root(path: str) -> str:
    """Why DeskPilot must not be installed at ``path``, or ``""``.

    Matched case-insensitively on the normalised path with a trailing separator
    appended, so a check for ``"\\temp\\"`` also catches a root that *is* a
    temp directory rather than only one inside it.
    """
    if not path or not path.strip():
        return "an empty installation path"
    probe = ntpath.normpath(path).lower()
    if not probe.endswith("\\"):
        probe += "\\"
    for fragment, why in _FORBIDDEN:
        if fragment in probe:
            return why
    return ""
