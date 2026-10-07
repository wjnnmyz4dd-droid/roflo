"""A Windows machine that does not exist, for testing the installer's decisions.

**Test-only, and structurally so.** Nothing under ``windows/installer`` imports
this module and a test asserts that. A production installer that ships the
means to simulate its own environment ships the means to simulate its own
environment, and the reason the real :class:`WindowsHost` is so thin is that
all the judgement lives above the seam, where this fake can reach it.

The fidelity that matters is not breadth, it is the handful of Windows
behaviours that change an answer:

* **Paths are case-insensitive and separator-insensitive.** ``C:\\DeskPilot``,
  ``c:\\deskpilot`` and ``C:/DeskPilot`` are one directory. A fake that treated
  them as three would let a test pass while the real installer created a
  duplicate tree.
* **A directory exists if anything is under it.** Windows has no inode for an
  empty concept; a test that writes ``C:\\A\\B\\c.txt`` must then find
  ``C:\\A\\B`` to be a directory, and the layout code depends on that.
* **Commands are matched, not mocked.** Handlers are registered against the
  program and its arguments, so a test says "``schtasks.exe /Create`` fails"
  rather than patching a method, and an unexpected command is an explicit
  failure instead of a silent ``None``.

Every command is recorded. Several tests assert on what was *not* run -- that
no MetaTrader process was signalled, that no secret reached a command line --
and that is only possible because the fake keeps the whole transcript.
"""

from __future__ import annotations

import contextlib
import hashlib
import ntpath
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / "windows" / "installer"))

from deskpilot_installer.host import (  # noqa: E402
    CommandResult, Host, HostFacts, ProcessInfo,
)
from deskpilot_installer.report import Secret  # noqa: E402

GB = 1024 ** 3


def norm(path: str) -> str:
    """One canonical spelling for a Windows path: lowercase, backslashes."""
    if not path:
        return ""
    return ntpath.normpath(path.replace("/", "\\")).lower().rstrip("\\")


@dataclass
class Handler:
    """A command the fake knows how to answer."""

    #: Every fragment here must appear in the joined argv for this to match.
    must_contain: tuple[str, ...]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    #: Called with the argv when matched, for side effects on the fake machine.
    effect: Callable[["FakeWindows", Sequence[str]], None] | None = None


@dataclass
class FakeWindows(Host):
    """A simulated Windows host. Every field is a dial a scenario turns."""

    windows: bool = True
    admin: bool = True
    sixty_four: bool = True
    os_name: str = "Windows"
    os_version: str = "10.0.20348"
    ram_bytes: int = 16 * GB
    cpus: int = 4
    free_bytes_default: int = 120 * GB
    #: Per-volume overrides, keyed by normalised path prefix.
    free_overrides: dict[str, int] = field(default_factory=dict)

    #: Normalised path -> contents.
    files: dict[str, bytes] = field(default_factory=dict)
    #: Explicitly-created directories, beyond those implied by files.
    dirs: set[str] = field(default_factory=set)
    #: Normalised path -> mtime.
    mtimes: dict[str, int] = field(default_factory=dict)

    procs: list[ProcessInfo] = field(default_factory=list)
    listening: dict[int, str] = field(default_factory=dict)
    path_programs: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    handlers: list[Handler] = field(default_factory=list)
    #: Every argv passed to :meth:`run`, in order.
    commands: list[tuple[str, ...]] = field(default_factory=list)
    #: Every secret passed as stdin, so a test can prove it went there.
    stdin_secrets: list[str] = field(default_factory=list)
    #: Raise on any command with no handler, rather than returning success.
    strict: bool = True
    #: Paths that refuse to be written, simulating a full or locked disk.
    unwritable: set[str] = field(default_factory=set)
    #: Set once the disk is "full"; every subsequent write fails.
    disk_full: bool = False

    # -- construction helpers -------------------------------------------
    def add_file(self, path: str, data: bytes | str = b"", mtime: int = 1000
                 ) -> "FakeWindows":
        if isinstance(data, str):
            data = data.encode("utf-8")
        key = norm(path)
        self.files[key] = data
        self.mtimes[key] = mtime
        parent = ntpath.dirname(key)
        while parent and parent not in self.dirs:
            self.dirs.add(parent)
            nxt = ntpath.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
        return self

    def add_dir(self, path: str) -> "FakeWindows":
        key = norm(path)
        self.dirs.add(key)
        parent = ntpath.dirname(key)
        while parent and parent not in self.dirs:
            self.dirs.add(parent)
            nxt = ntpath.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
        return self

    def add_program(self, name: str, path: str) -> "FakeWindows":
        self.path_programs[name.lower()] = path
        self.add_file(path, b"MZ")
        return self

    def add_process(self, name: str, pid: int, image: str = "") -> "FakeWindows":
        self.procs.append(ProcessInfo(name=name, pid=pid, image=image))
        return self

    def listen(self, port: int, owner: str = "something.exe") -> "FakeWindows":
        self.listening[port] = owner
        return self

    def on(self, *must_contain: str, returncode: int = 0, stdout: str = "",
           stderr: str = "",
           effect: Callable[["FakeWindows", Sequence[str]], None] | None = None
           ) -> "FakeWindows":
        self.handlers.append(Handler(tuple(must_contain), returncode, stdout,
                                     stderr, effect))
        return self

    # -- Host: facts -----------------------------------------------------
    def facts(self) -> HostFacts:
        return HostFacts(
            os_name=self.os_name if self.windows else "Linux",
            os_version=self.os_version,
            is_windows=self.windows,
            is_64bit=self.sixty_four,
            is_admin=self.admin,
            cpu_count=self.cpus,
            total_ram_bytes=self.ram_bytes,
        )

    def free_bytes(self, path: str) -> int:
        key = norm(path)
        for prefix, value in sorted(self.free_overrides.items(),
                                    key=lambda kv: -len(kv[0])):
            if key.startswith(norm(prefix)):
                return value
        return self.free_bytes_default

    # -- Host: filesystem ------------------------------------------------
    def exists(self, path: str) -> bool:
        key = norm(path)
        return key in self.files or key in self.dirs

    def is_dir(self, path: str) -> bool:
        return norm(path) in self.dirs

    def read_bytes(self, path: str) -> bytes:
        key = norm(path)
        if key not in self.files:
            raise OSError(2, f"no such file: {path}")
        return self.files[key]

    def write_bytes(self, path: str, data: bytes) -> None:
        key = norm(path)
        if self.disk_full:
            raise OSError(28, f"no space left on device: {path}")
        for blocked in self.unwritable:
            if key.startswith(norm(blocked)):
                raise OSError(13, f"permission denied: {path}")
        self.add_file(path, data)

    def makedirs(self, path: str) -> None:
        if self.disk_full:
            raise OSError(28, f"no space left on device: {path}")
        for blocked in self.unwritable:
            if norm(path).startswith(norm(blocked)):
                raise OSError(13, f"permission denied: {path}")
        self.add_dir(path)

    def listdir(self, path: str) -> list[str]:
        key = norm(path)
        names: set[str] = set()
        for candidate in list(self.files) + list(self.dirs):
            if candidate == key:
                continue
            parent = ntpath.dirname(candidate)
            if parent == key:
                names.add(ntpath.basename(candidate))
        return sorted(names)

    def remove_tree(self, path: str) -> None:
        key = norm(path)
        for candidate in [c for c in self.files
                          if c == key or c.startswith(key + "\\")]:
            del self.files[candidate]
            self.mtimes.pop(candidate, None)
        for candidate in [c for c in self.dirs
                          if c == key or c.startswith(key + "\\")]:
            self.dirs.discard(candidate)

    def move(self, src: str, dst: str) -> None:
        skey, dkey = norm(src), norm(dst)
        moved = False
        for candidate in sorted([c for c in self.files
                                 if c == skey or c.startswith(skey + "\\")]):
            suffix = candidate[len(skey):]
            self.add_file(dkey + suffix, self.files[candidate],
                          self.mtimes.get(candidate, 1000))
            del self.files[candidate]
            moved = True
        for candidate in sorted([c for c in self.dirs
                                 if c == skey or c.startswith(skey + "\\")]):
            self.dirs.discard(candidate)
            self.add_dir(dkey + candidate[len(skey):])
            moved = True
        if not moved:
            raise OSError(2, f"no such path: {src}")

    def mtime(self, path: str) -> int:
        return self.mtimes.get(norm(path), 0)

    # -- Host: processes and network -------------------------------------
    def run(self, argv: Sequence[str], *, stdin: Secret | str | None = None,
            cwd: str | None = None, env: Mapping[str, str] | None = None,
            timeout: float = 300.0) -> CommandResult:
        args = tuple(str(a) for a in argv)
        self.commands.append(args)
        if isinstance(stdin, Secret):
            self.stdin_secrets.append(stdin.reveal())
        elif isinstance(stdin, str) and stdin:
            self.stdin_secrets.append(stdin)
        joined = " ".join(args)
        for handler in self.handlers:
            if all(fragment in joined for fragment in handler.must_contain):
                if handler.effect is not None:
                    handler.effect(self, args)
                return CommandResult(args, handler.returncode, handler.stdout,
                                     handler.stderr)
        if self.strict:
            raise AssertionError(
                f"the installer ran a command this scenario did not expect:\n"
                f"  {joined}\n"
                f"Add a handler with .on(...) if it is legitimate.")
        return CommandResult(args, 0, "", "")

    def processes(self) -> list[ProcessInfo]:
        return list(self.procs)

    def port_in_use(self, port: int) -> bool:
        return port in self.listening

    def port_owner(self, port: int) -> str:
        return self.listening.get(port, "")

    def environ(self) -> Mapping[str, str]:
        return dict(self.env)

    def which(self, program: str) -> str:
        return self.path_programs.get(program.lower(), "")


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def zip_bytes(entries: Mapping[str, bytes | str]) -> bytes:
    """A real ZIP archive in memory, so the payload code parses real bytes."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data if isinstance(data, bytes)
                        else str(data).encode("utf-8"))
    return buf.getvalue()


#: A minimal payload that looks like the certified tree.
CERTIFIED_ENTRIES = {
    "solvent/__init__.py": b"",
    "solvent/cli.py": b"# the CLI\n",
    "pyproject.toml": b"[project]\nname='solvent'\n",
    "HANDOFF/README.md": b"handoff\n",
}


def certified_payload() -> tuple[bytes, str]:
    """``(bytes, sha256)`` for a payload the installer should accept."""
    data = zip_bytes(CERTIFIED_ENTRIES)
    return data, hashlib.sha256(data).hexdigest()


def fresh_windows(package: bytes | None = None,
                  digest: str | None = None) -> FakeWindows:
    """A clean Windows Server with no Python, no DeskPilot and no MetaTrader.

    Handlers are registered for every command a successful installation runs,
    so a scenario that needs a *failure* overrides one rather than building the
    whole machine again.
    """
    if package is None:
        package, digest = certified_payload()
    host = FakeWindows(env={"USERPROFILE": r"C:\Users\Admin",
                            "APPDATA": r"C:\Users\Admin\AppData\Roaming",
                            "SystemRoot": r"C:\Windows"})
    host.add_dir(r"C:\Windows")
    host.add_file(r"C:\Media\DeskPilot-certified.zip", package)
    # netsh is read for the MetaTrader fingerprint even when MetaTrader is absent
    host.on("netsh.exe", "show", "rule", stdout="")
    return host


#: Output ``ollama list`` gives when the chosen model is present.
OLLAMA_LIST = ("NAME                    ID        SIZE   MODIFIED\n"
               "qwen2.5:14b-instruct    abc123    9.0 GB 2 days ago\n")


def _python_reports(version: str = "3 13 True"):
    """Handler effect: an interpreter that answers the version interrogation."""
    return version


SYSTEM_PYTHON = r"C:\Program Files\Python313\python.exe"
DB_PATH = r"C:\DeskPilot\Data\solvent.db"


def with_python(host: FakeWindows, path: str = SYSTEM_PYTHON,
                version: str = "3 13 True") -> FakeWindows:
    """Put a working system Python on the fake machine."""
    host.add_program("python.exe", path)
    host.on(path, "sys.version_info", stdout=version)
    return host


def install_ready(host: FakeWindows | None = None, *,
                  package: bytes | None = None, digest: str | None = None,
                  system_python: bool = True,
                  root: str = r"C:\DeskPilot",
                  model_tag: str = "qwen2.5:14b-instruct") -> FakeWindows:
    """A machine on which a complete installation succeeds.

    Every command the install path runs has a handler, so a scenario that wants
    one of them to *fail* overrides that single handler instead of rebuilding
    the machine. The fake is strict: any command without a handler raises, which
    is how an installer that silently started running something new gets caught.
    """
    if package is None:
        package, digest = certified_payload()
    if host is None:
        host = fresh_windows(package, digest)

    # Derived from the root rather than hardcoded: a scenario installing to a
    # path with spaces on another drive needs the handlers to follow it, and a
    # fixture that silently kept the default would fail as an unexpected
    # command rather than as the thing under test.
    venv_python = ntpath.join(root, "venv", "Scripts", "python.exe")
    site_packages = ntpath.join(root, "venv", "Lib", "site-packages")
    db_path = ntpath.join(root, "Data", "solvent.db")

    if system_python:
        with_python(host)
    else:
        # No Python: the installer downloads one, verifies it, and runs it.
        def arrived(h: FakeWindows, argv) -> None:
            target = argv[argv.index("-o") + 1]
            h.add_file(target, _bootstrap_bytes())

        def installed(h: FakeWindows, argv) -> None:
            path = SYSTEM_PYTHON
            h.add_program("python.exe", path)
            h.on(path, "sys.version_info", stdout="3 13 True")

        host.on("curl.exe", "python.org", effect=arrived)
        host.on("python-3.13.9-amd64.exe", "/quiet", effect=installed)

    def venv_made(h: FakeWindows, argv) -> None:
        h.add_program("venvpython", venv_python)
        h.add_dir(site_packages)
        h.on(venv_python, "site.getsitepackages", stdout=site_packages)
        h.on(venv_python, "hash_password", stdout="pbkdf2$600000$aabb$ccdd")
        # Creates the database if it is absent and leaves it alone otherwise.
        # An effect that rewrote the file every time would quietly erase the
        # contents a test had put there to prove an upgrade preserved them.
        def opened_db(hh: FakeWindows, _argv) -> None:
            if not hh.exists(db_path):
                hh.add_file(db_path, b"SQLite format 3\x00")

        h.on(venv_python, "solvent.cli", "doctor",
             stdout="enforcement measured\n", effect=opened_db)
        h.on(venv_python, "setup", "capability",
             stdout="registered 1.0 as proven, covering 4 certified check(s).\n")
        h.on(venv_python, "setup", "model", stdout="model clearance recorded\n")
        h.on(venv_python, "setup", "firewall", stdout="posture recorded\n")
        h.on(venv_python, "solvent.cli", "health", stdout="can clean CSV files\n")
        h.on(venv_python, "solvent.cli", "readiness",
             stdout="READY: 12 of 14 checks\n")
        h.on(venv_python, "setup", "check", stdout="OWNER_KEY  OK\n")
        h.on(venv_python, "may_deploy",
             stdout='[{"name": "csv-cleanup", "proven": true, '
                    '"may_deploy": true, "why": "certified"}]')

    # Matched on the *system* interpreter, not just "-m venv": the venv
    # directory is called "venv", so a looser matcher also catches every
    # later command whose interpreter path contains it.
    host.on(SYSTEM_PYTHON, "-m", "venv", effect=venv_made)
    host.on("icacls.exe")
    host.on("netsh.exe", "advfirewall", "firewall", "add")
    host.on("netsh.exe", "advfirewall", "firewall", "delete")
    host.on("schtasks.exe", "/Create")
    host.on("schtasks.exe", "/Query")
    host.on("schtasks.exe", "/End")
    host.on("schtasks.exe", "/Delete")
    host.add_program("ollama.exe", r"C:\Program Files\Ollama\ollama.exe")
    host.on("ollama.exe", "list", stdout=OLLAMA_LIST)
    host.on("ollama.exe", "run", stdout="ok\n")
    host.listen(11434, "ollama.exe")
    return host


def _bootstrap_bytes() -> bytes:
    """Deterministic stand-in bytes for the python.org installer.

    A fake cannot produce a SHA-256 preimage, so the success path is tested by
    aiming the installer's pin at these bytes with :func:`pinned_to` rather than
    by pretending these bytes hash to the real digest. A scenario that omits
    :func:`pinned_to` exercises the rejection path, which is the more important
    of the two.
    """
    return b"PYTHON-INSTALLER-STANDIN" * 64


@contextlib.contextmanager
def pinned_size_only(data: bytes):
    """Pin the expected *size* to ``data`` but leave the digest wrong.

    Isolates the digest comparison from the length pre-filter in front of it.
    A stand-in of the wrong length is rejected on size alone, so a test using
    it proves nothing about the hash; this models the attack that matters --
    a substituted file of exactly the right size -- without allocating the
    28 MB a real same-size forgery would need.
    """
    from deskpilot_installer import python_runtime as pr
    was_size = pr.BOOTSTRAP_BYTES
    pr.BOOTSTRAP_BYTES = len(data)
    try:
        yield
    finally:
        pr.BOOTSTRAP_BYTES = was_size


@contextlib.contextmanager
def pinned_to(data: bytes):
    """Point the installer's Python pin at ``data`` for the duration.

    Patching the expected digest, not the comparison: ``verify_download`` runs
    its real ``compare_digest`` against a real SHA-256 of real bytes. Only the
    constant it compares against is a fixture.
    """
    from deskpilot_installer import python_runtime as pr
    was_digest, was_size = pr.BOOTSTRAP_SHA256, pr.BOOTSTRAP_BYTES
    pr.BOOTSTRAP_SHA256 = hashlib.sha256(data).hexdigest()
    pr.BOOTSTRAP_BYTES = len(data)
    try:
        yield
    finally:
        pr.BOOTSTRAP_SHA256, pr.BOOTSTRAP_BYTES = was_digest, was_size
