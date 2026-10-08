"""Everything that touches the machine, behind one seam.

The installer makes decisions; this module is the only thing that reads or
changes a real computer. Keeping the two apart is what makes the decisions
testable: a test supplies a different host and gets a different machine,
without a Windows VM and without mocking a library out from under the code.

Two rules are enforced by the shape of the interface rather than by review.

**No shell, ever.** :meth:`Host.run` takes a list of arguments and the real
implementation passes ``shell=False``. There is no overload that takes a
string. Command injection and the unquoted-path bug are then not things to
remember to avoid; the API has no way to express them. For the same reason the
installer reaches Windows through ``schtasks.exe`` and ``netsh.exe`` directly
rather than through PowerShell, so PowerShell argument quoting -- a notorious
source of injection -- is not in the normal path at all.

**No secret on a command line.** Arguments are visible to every other user on
the machine through the process list, which is why Solvent's own
``web-password`` command prompts rather than accepting an argument. Values
needing secrecy go through :meth:`Host.run`'s ``stdin`` parameter, and
:class:`~deskpilot_installer.report.Secret` refuses to format itself into an
argument by accident.

The fake host used by the tests lives in ``tests_solvent/``, not here. A
production package that ships a way to simulate its own environment ships a way
to simulate its own environment, however carefully it is named.
"""

from __future__ import annotations

import abc
import ntpath
import os
import pathlib
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .report import Secret


def _subkeys(winreg, key) -> list[str]:
    """Names of a registry key's immediate children. Never raises."""
    names: list[str] = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            break
        index += 1
    return names


def _install_path(winreg, company_key, tag: str, access: int,
                  value_name: str | None) -> str:
    """One registration's interpreter path, or ``""``.

    ``value_name`` of ``None`` reads the key's default value, which PEP 514
    defines as the installation *directory*; ``python.exe`` is joined onto it.
    A named value is taken as the executable itself.
    """
    try:
        key = winreg.OpenKey(company_key, tag + r"\\InstallPath", 0, access)
    except OSError:
        return ""
    with key:
        try:
            raw, _kind = winreg.QueryValueEx(key, value_name)
        except OSError:
            return ""
    text = str(raw or "").strip().strip('"')
    if not text:
        return ""
    if value_name is None:
        return ntpath.join(text, "python.exe")
    return text


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a command did. ``argv`` is kept for the log, which is redacted."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Both streams, for display. Some tools report success on stderr."""
        return (self.stdout + ("\n" if self.stdout and self.stderr else "")
                + self.stderr)


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """One running process, as much as the installer is entitled to know."""

    name: str
    pid: int
    #: Full path to the image, when the host could determine it.
    image: str = ""


@dataclass(frozen=True, slots=True)
class HostFacts:
    """The machine's shape, gathered once so a check cannot drift mid-run."""

    os_name: str
    os_version: str
    is_windows: bool
    is_64bit: bool
    is_admin: bool
    cpu_count: int
    total_ram_bytes: int
    #: Free bytes keyed by the drive or mount the installer asked about.
    free_bytes: Mapping[str, int] = field(default_factory=dict)


class Host(abc.ABC):
    """The complete set of ways the installer may touch a computer."""

    # -- facts -----------------------------------------------------------
    @abc.abstractmethod
    def facts(self) -> HostFacts:
        """The machine's shape. Implementations should gather, not cache."""

    @abc.abstractmethod
    def free_bytes(self, path: str) -> int:
        """Free space on the volume holding ``path``."""

    # -- filesystem ------------------------------------------------------
    @abc.abstractmethod
    def exists(self, path: str) -> bool: ...

    @abc.abstractmethod
    def is_dir(self, path: str) -> bool: ...

    @abc.abstractmethod
    def read_bytes(self, path: str) -> bytes: ...

    @abc.abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None: ...

    @abc.abstractmethod
    def makedirs(self, path: str) -> None: ...

    @abc.abstractmethod
    def listdir(self, path: str) -> list[str]: ...

    @abc.abstractmethod
    def remove_tree(self, path: str) -> None:
        """Delete a directory and its contents. Callers must be explicit."""

    @abc.abstractmethod
    def move(self, src: str, dst: str) -> None: ...

    # -- processes and network -------------------------------------------
    @abc.abstractmethod
    def run(self, argv: Sequence[str], *, stdin: Secret | str | None = None,
            cwd: str | None = None, env: Mapping[str, str] | None = None,
            timeout: float = 300.0) -> CommandResult:
        """Run a program. ``argv[0]`` is a program, never a shell command."""

    @abc.abstractmethod
    def processes(self) -> list[ProcessInfo]: ...

    @abc.abstractmethod
    def port_in_use(self, port: int) -> bool: ...

    # -- environment -----------------------------------------------------
    @abc.abstractmethod
    def environ(self) -> Mapping[str, str]: ...

    @abc.abstractmethod
    def which(self, program: str) -> str:
        """Absolute path to ``program``, or ``""``."""

    # -- registry ---------------------------------------------------------
    def registry_python_paths(self) -> list[str]:
        """Interpreters registered under PEP 514, newest-looking first.

        Concrete rather than abstract, returning nothing by default, because a
        host that has no registry is a legitimate host -- but on Windows this
        is the *authoritative* answer and a path list is only a fallback. A
        python.org installer records itself here whether it installed for one
        user or all of them, whether it was added to PATH or not, and wherever
        it put itself. Reading it is the difference between finding the owner's
        Python and telling them to install one they already have.
        """
        return []

    # -- optional ---------------------------------------------------------
    def port_owner(self, port: int) -> str:
        """Name of the process holding ``port``, or ``""`` if not knowable.

        Concrete rather than abstract because it is an enrichment, not a
        control: the installer refuses a conflicting port whether or not it can
        name the culprit. Naming it turns "port 443 is in use" into "IIS is
        using port 443", which is the difference between a dead end and an
        instruction.
        """
        return ""

    def mtime(self, path: str) -> int:
        """Modification time, used only for the MetaTrader fingerprint."""
        return 0


class WindowsHost(Host):
    """The real machine. Thin on purpose: logic belongs in the caller.

    Every method here is a translation of one operating-system fact into the
    vocabulary above. There are no decisions in this class, so there is nothing
    in it that a test on another platform needed to cover.
    """

    def facts(self) -> HostFacts:
        import ctypes
        import platform
        import sys

        is_windows = os.name == "nt"
        is_admin = False
        total_ram = 0
        if is_windows:  # pragma: no cover - requires Windows
            try:
                is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                is_admin = False
            try:
                class _MemoryStatus(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong),
                                ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong),
                                ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong),
                                ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong),
                                ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

                status = _MemoryStatus()
                status.dwLength = ctypes.sizeof(_MemoryStatus)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                total_ram = int(status.ullTotalPhys)
            except Exception:
                total_ram = 0
        else:
            try:
                total_ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            except (ValueError, OSError, AttributeError):
                total_ram = 0
            is_admin = hasattr(os, "geteuid") and os.geteuid() == 0
        return HostFacts(
            os_name=platform.system() or os.name,
            os_version=platform.version(),
            is_windows=is_windows,
            is_64bit=sys.maxsize > 2 ** 32,
            is_admin=is_admin,
            cpu_count=os.cpu_count() or 1,
            total_ram_bytes=total_ram,
        )

    def free_bytes(self, path: str) -> int:
        probe = pathlib.Path(path)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            return shutil.disk_usage(str(probe)).free
        except OSError:
            return 0

    def exists(self, path: str) -> bool:
        return pathlib.Path(path).exists()

    def is_dir(self, path: str) -> bool:
        return pathlib.Path(path).is_dir()

    def read_bytes(self, path: str) -> bytes:
        return pathlib.Path(path).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def makedirs(self, path: str) -> None:
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)

    def listdir(self, path: str) -> list[str]:
        try:
            return sorted(p.name for p in pathlib.Path(path).iterdir())
        except OSError:
            return []

    def remove_tree(self, path: str) -> None:
        shutil.rmtree(path, ignore_errors=True)

    def move(self, src: str, dst: str) -> None:
        pathlib.Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)

    def run(self, argv: Sequence[str], *, stdin: Secret | str | None = None,
            cwd: str | None = None, env: Mapping[str, str] | None = None,
            timeout: float = 300.0) -> CommandResult:
        args = [str(a) for a in argv]
        if not args:
            raise ValueError("run() needs a program to run")
        payload = stdin.reveal() if isinstance(stdin, Secret) else stdin
        merged = dict(os.environ)
        if env:
            merged.update(env)
        try:
            proc = subprocess.run(
                args, input=payload, capture_output=True, text=True,
                cwd=cwd, env=merged, timeout=timeout,
                shell=False,  # structural: see the module docstring
            )
        except subprocess.TimeoutExpired:
            return CommandResult(tuple(args), 1, "", "timed out", timed_out=True)
        except OSError as exc:
            return CommandResult(tuple(args), 1, "", str(exc))
        return CommandResult(tuple(args), proc.returncode,
                             proc.stdout or "", proc.stderr or "")

    def processes(self) -> list[ProcessInfo]:
        if os.name != "nt":
            return []
        # pragma: no cover - requires Windows
        result = self.run(["tasklist.exe", "/FO", "CSV", "/NH"], timeout=60.0)
        found: list[ProcessInfo] = []
        if not result.ok:
            return found
        import csv
        import io
        for parts in csv.reader(io.StringIO(result.stdout)):
            if len(parts) >= 2:
                try:
                    found.append(ProcessInfo(name=parts[0], pid=int(parts[1])))
                except ValueError:
                    continue
        return found

    def port_in_use(self, port: int) -> bool:
        """Bind-test rather than parse ``netstat``.

        A bind that fails with EADDRINUSE is the same question the server will
        ask later, which makes this the check that matches what happens next.
        """
        for family, addr in ((socket.AF_INET, ("127.0.0.1", port)),
                             (socket.AF_INET, ("0.0.0.0", port))):
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.bind(addr)
            except OSError:
                return True
            finally:
                sock.close()
        return False

    def environ(self) -> Mapping[str, str]:
        return dict(os.environ)

    def which(self, program: str) -> str:
        return shutil.which(program) or ""

    def registry_python_paths(self) -> list[str]:
        """Read PEP 514 registrations from both hives and both registry views.

        Four sweeps are needed and each omission is a real machine missed.
        ``HKEY_CURRENT_USER`` holds a per-user install, which is what
        python.org does by default. ``HKEY_LOCAL_MACHINE`` holds an all-users
        install. And both have to be read through the 64-bit *and* 32-bit
        views, because a 32-bit process reading the plain path is silently
        redirected into ``WOW6432Node`` and would miss every 64-bit
        registration -- which is exactly the set we want.

        ``ExecutablePath`` is preferred over joining ``python.exe`` onto
        ``InstallPath``: PEP 514 allows a distribution to put the executable
        somewhere else, and the value exists to be used.
        """
        if os.name != "nt":
            return []
        # pragma: no cover - requires Windows
        import winreg

        found: list[str] = []
        seen: set[str] = set()
        subkey = r"Software\\Python"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (getattr(winreg, "KEY_WOW64_64KEY", 0),
                         getattr(winreg, "KEY_WOW64_32KEY", 0)):
                for path in self._sweep(winreg, hive, subkey, view):
                    key = path.lower()
                    if key not in seen:
                        seen.add(key)
                        found.append(path)
        return found

    @staticmethod
    def _sweep(winreg, hive, subkey: str, view: int) -> list[str]:
        """Every ``ExecutablePath`` under one hive and one registry view."""
        out: list[str] = []
        access = winreg.KEY_READ | view
        try:
            root = winreg.OpenKey(hive, subkey, 0, access)
        except OSError:
            return out
        with root:
            for company in _subkeys(winreg, root):
                if company.lower() == "pylauncher":
                    continue
                try:
                    company_key = winreg.OpenKey(root, company, 0, access)
                except OSError:
                    continue
                with company_key:
                    for tag in _subkeys(winreg, company_key):
                        for value in ("ExecutablePath", None):
                            path = _install_path(winreg, company_key, tag,
                                                 access, value)
                            if path:
                                out.append(path)
                                break
        # Newest tag last in the registry is not guaranteed, so sort by the
        # version-looking part of the path descending and let the caller's own
        # version check have the final say.
        return sorted(out, reverse=True)

    def port_owner(self, port: int) -> str:
        """Map a listening port to a process name via netstat and tasklist.

        Best effort by design. Two commands are needed because netstat gives a
        PID and the owner needs a name; if either is unavailable the caller
        still has a correct refusal, just a less helpful one.
        """
        if os.name != "nt":
            return ""
        # pragma: no cover - requires Windows
        listing = self.run(["netstat.exe", "-ano"], timeout=60.0)
        if not listing.ok:
            return ""
        pid = 0
        for line in listing.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5 or not fields[0].upper().startswith("TCP"):
                continue
            if fields[3].upper() != "LISTENING":
                continue
            local = fields[1]
            if local.rsplit(":", 1)[-1] != str(port):
                continue
            try:
                pid = int(fields[4])
            except ValueError:
                continue
            break
        if not pid:
            return ""
        for proc in self.processes():
            if proc.pid == pid:
                return proc.name
        return f"pid {pid}"

    def mtime(self, path: str) -> int:
        try:
            return int(pathlib.Path(path).stat().st_mtime)
        except OSError:
            return 0
