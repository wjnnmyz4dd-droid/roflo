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
from deskpilot_installer import integration  # noqa: E402
from deskpilot_installer.layout import (  # noqa: E402
    DEFAULT_ROOT, WEB_PORT,
)
from tests_solvent.installer_acl_model import AclModel  # noqa: E402

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
    #: Computes ``(returncode, stdout, stderr)`` from the fake's state instead
    #: of answering the same way every time. ``schtasks /Query`` needs this: a
    #: handler that always succeeded meant the fake could not tell a registered
    #: task from a missing one, so a test could not tell either.
    answer: Callable[["FakeWindows", Sequence[str]],
                     tuple[int, str, str]] | None = None


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
    #: Normalised path -> the spelling it was created with.
    #:
    #: Windows compares paths case-insensitively but *reports* names with
    #: their original case. Code that enumerates a directory and builds a
    #: path from what it finds therefore gets real casing, and a fake that
    #: returned lowercase names would make such code look broken when it is
    #: not -- or hide a genuine bug behind a fake-only mismatch.
    display: dict[str, str] = field(default_factory=dict)
    #: Interpreters registered under PEP 514, as the registry would report.
    registry_paths: list[str] = field(default_factory=list)

    procs: list[ProcessInfo] = field(default_factory=list)
    listening: dict[int, str] = field(default_factory=dict)
    path_programs: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    handlers: list[Handler] = field(default_factory=list)
    #: Every argv passed to :meth:`run`, in order.
    commands: list[tuple[str, ...]] = field(default_factory=list)
    #: Scheduled tasks on this machine: name -> is it running right now.
    #: Real state, because "registered" and "running" are different questions
    #: and the installer asks both.
    scheduled_tasks: dict[str, bool] = field(default_factory=dict)
    #: The DACLs ``icacls`` has been asked to set. Consulted when the
    #: isolation probe runs, so that the probe's answers come from the rules
    #: the installer actually applied rather than from a mock that cannot be
    #: wrong. See :mod:`tests_solvent.installer_acl_model` for what it is and,
    #: more importantly, for what it is not.
    acl: AclModel = field(default_factory=AclModel)
    #: Seconds the installer asked to wait for. Recorded instead of slept.
    slept: list[float] = field(default_factory=list)
    #: Which identity the isolation probe is answered as. The control centre's
    #: by default; a test can point it at SYSTEM to prove the probe would
    #: notice a boundary that was not there.
    web_identity: str = "S-1-5-19"
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
        self._remember(path)
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
        self._remember(path)
        parent = ntpath.dirname(key)
        while parent and parent not in self.dirs:
            self.dirs.add(parent)
            nxt = ntpath.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
        return self

    def _remember(self, path: str) -> None:
        """Record the original spelling of every segment of ``path``."""
        cleaned = path.replace("/", "\\")
        parts = [p for p in cleaned.split("\\") if p]
        built = ""
        for part in parts:
            built = part if not built else built + "\\" + part
            self.display.setdefault(norm(built), part)

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

    def schedule_task(self, name: str, *, running: bool = True
                      ) -> "FakeWindows":
        """Say this machine already has that task, as an upgrade would find."""
        self.scheduled_tasks[name] = running
        return self

    def on(self, *must_contain: str, returncode: int = 0, stdout: str = "",
           stderr: str = "",
           effect: Callable[["FakeWindows", Sequence[str]], None] | None = None,
           answer: Callable[["FakeWindows", Sequence[str]],
                            tuple[int, str, str]] | None = None
           ) -> "FakeWindows":
        self.handlers.append(Handler(tuple(must_contain), returncode, stdout,
                                     stderr, effect, answer))
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
                names.add(self.display.get(candidate,
                                           ntpath.basename(candidate)))
        return sorted(names)

    def remove_file(self, path: str) -> None:
        key = norm(path)
        self.files.pop(key, None)
        self.mtimes.pop(key, None)

    def sleep(self, seconds: float) -> None:
        """Recorded, not taken. A real wait here is time nobody gets back."""
        self.slept.append(seconds)

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
                if handler.answer is not None:
                    code, out, err = handler.answer(self, args)
                    return CommandResult(args, code, out, err)
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

    def registry_python_paths(self) -> list[str]:
        return list(self.registry_paths)


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

        # Matched on the generic shape rather than the pinned version: the
        # bootstrap version moves, and a fixture naming it goes stale the day
        # it does.
        host.on("curl.exe", "python.org", effect=arrived)
        host.on("-amd64.exe", "/quiet", effect=installed)

    def venv_made(h: FakeWindows, argv) -> None:
        h.add_program("venvpython", venv_python)
        h.add_dir(site_packages)
        h.on(venv_python, "site.getsitepackages", stdout=site_packages)
        h.on(venv_python, "hash_password", stdout="pbkdf2$600000$aabb$ccdd")
        # These handlers mirror what the real `solvent` CLI actually does,
        # verified by running it. The previous versions encoded assumptions
        # instead, and three installer-blocking defects hid behind them: a
        # `doctor` that was expected to create a database, a `--owner` value
        # the capability authority refuses, and a `Solvent` class imported from
        # the wrong module. A fake that is kinder than reality is a fake that
        # certifies a broken installer.

        def provision_db(hh: FakeWindows, _argv) -> None:
            """`setup check` constructs a Store, which creates the schema."""
            if not hh.exists(db_path):
                hh.add_file(db_path, b"SQLite format 3\x00")

        # `doctor` deliberately refuses to open a database that does not
        # exist, so that it cannot report on one it just made. Modelled with
        # its real exit status and its real message.
        def doctor(hh: FakeWindows, argv) -> None:
            pass

        # Faithful to the real command in both directions: it refuses an
        # absent database rather than creating one, and fails on a file it
        # cannot open as a database. A handler that always succeeded hid the
        # fact that a garbage file at the database path would have been
        # accepted as a provisioned database.
        h.handlers.append(Handler(
            (venv_python, "solvent.cli", "doctor"), 0,
            "DeskPilot readiness\n  guard installed : True\n", ""))
        # Exits 1, like the real command: it is a view of configuration
        # completeness, and a fresh installation always has items outstanding.
        # The previous handler returned 0, which hid two defects -- a
        # provisioning step that trusted the exit code, and a verification
        # that would have reported NOT READY on every machine.
        h.on(venv_python, "setup", "check", returncode=1,
             stdout="OWNER_KEY  OK\nFULL_ACTIVATION  BLOCKED\n",
             effect=provision_db)
        h.on(venv_python, "setup", "contracting",
             stdout="contracting structure: INDIVIDUAL\n")
        h.on(venv_python, "setup", "capability",
             stdout="registered csv-cleanup/1.0 as proven, covering 11 "
                    "certified check(s).\n")
        h.on(venv_python, "setup", "model",
             stdout="cleared qwen2.5:14b-instruct (Apache-2.0) for commercial "
                    "use.\n")
        h.on(venv_python, "setup", "firewall",
             stdout="host network posture: default-deny\n")
        h.on(venv_python, "solvent.cli", "health",
             stdout="Startup checks (LOCAL)\n  [ok   ] audit chain: chain "
                    "intact\n")
        h.on(venv_python, "solvent.cli", "readiness",
             stdout="First-revenue readiness\n  [READY] acquisition "
                    "pipeline\n")
        h.on(venv_python, "may_deploy",
             stdout='[{"name": "csv-cleanup", "proven": true, '
                    '"may_deploy": true, "why": "csv-cleanup is approved and '
                    'proven"}]')

        # Modelled from the real refusal. `Capability.decide` accepts only an
        # identity that `policy.is_owner` recognises -- the `owner:` prefix AND
        # membership of a policy-held list -- so a business name typed into the
        # wizard is refused every time. Registered here as a handler that
        # matches *before* the success case, so an installer that reintroduces
        # `--owner <typed name>` fails its tests instead of its owner.
        h.handlers.insert(0, Handler(
            (venv_python, "setup", "capability", "--owner"), 1, "",
            "refused: only a registered owner decides capability growth; "
            "Solvent may ask, never answer"))

    # Matched on the *system* interpreter, not just "-m venv": the venv
    # directory is called "venv", so a looser matcher also catches every
    # later command whose interpreter path contains it.
    host.on(SYSTEM_PYTHON, "-m", "venv", effect=venv_made)
    host.on("icacls.exe", effect=icacls_applied)
    # The DACL C:\DeskPilot starts with, inherited from the root of the drive:
    # Administrators and SYSTEM full, and Users read and execute. The installer
    # has to cut that away, and seeding it is what lets a test tell a rule that
    # was applied from one that was not.
    host.acl.seed_default_root(DEFAULT_ROOT)
    host.on("netsh.exe", "advfirewall", "firewall", "add")
    host.on("netsh.exe", "advfirewall", "firewall", "delete")
    # schtasks, with the state it actually keeps. These used to answer "yes"
    # to everything, which meant ``task_exists`` was true on a machine with no
    # tasks at all and no test could notice a service that was never
    # registered -- exactly the gap that let the control centre go missing.
    host.on("schtasks.exe", "/Create", effect=task_created)
    host.on("schtasks.exe", "/Query", answer=task_queried)
    host.on("schtasks.exe", "/Run", answer=task_run)
    host.on("schtasks.exe", "/End", answer=task_ended)
    host.on("schtasks.exe", "/Delete", answer=task_deleted)
    host.add_program("ollama.exe", r"C:\Program Files\Ollama\ollama.exe")
    host.on("ollama.exe", "list", stdout=OLLAMA_LIST)
    host.on("ollama.exe", "run", stdout="ok\n")
    host.listen(11434, "ollama.exe")
    return host


# ---------------------------------------------------------------------------
# schtasks
# ---------------------------------------------------------------------------
#: The installer's temporary probe task. Named here so the fake can
#: recognise it and play the part of Windows running it as LOCAL SERVICE.
#: Kept in step with ``Engine.PROBE_TASK_NAME`` by a test, because a fake
#: that silently stopped recognising it would report a boundary nobody
#: had checked.
PROBE_TASK_NAME = "DeskPilot Isolation Probe"

#: What real schtasks prints when the named task is not there.
_NO_SUCH_TASK = "ERROR: The system cannot find the file specified.\n"


def task_name_in(argv: Sequence[str]) -> str:
    """The value after ``/TN``, or empty if the command did not name one."""
    for index, arg in enumerate(argv):
        if arg.upper() == "/TN" and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def icacls_applied(host: "FakeWindows", argv: Sequence[str]) -> None:
    """Record the DACL change so the isolation probe can be answered from it.

    Without this, ``icacls`` succeeded and changed nothing a test could see --
    so a step that applied no rules at all and a step that applied the right
    ones were indistinguishable.
    """
    host.acl.icacls(list(argv))


def run_isolation_probe(host: "FakeWindows", argv: Sequence[str]) -> None:
    """Answer the installer's isolation probe from the recorded DACLs.

    The real probe is a script Windows runs as LOCAL SERVICE. Here the FakeHost
    plays that part: it works out, from the rules the installer applied, what
    that identity would be able to do, and writes the result file the installer
    waits for. What this proves is that the applied rules imply the required
    refusals. Whether Windows implements those rules the way
    :mod:`tests_solvent.installer_acl_model` says it does is settled on the
    owner's machine, by the same probe, and nowhere else.
    """
    import json

    from tests_solvent import installer_acl_model as acl

    script = ""
    for index, arg in enumerate(argv):
        if arg.upper() == "/TN" and index + 1 < len(argv):
            break
    # The probe script's path and its result file are both inside the
    # installation, and the script itself names them. Read them back out of
    # the script the installer wrote rather than recomputing the layout,
    # which would let a test pass while the installer wrote the wrong paths.
    for path, data in host.files.items():
        if path.endswith("isolation-probe.py"):
            script = data.decode("utf-8", "replace")
            break
    if not script:
        return

    def literal(name: str) -> str:
        marker = f"{name} = r\""
        start = script.find(marker)
        if start < 0:
            return ""
        start += len(marker)
        return script[start:script.index('"', start)]

    sid = host.web_identity
    paths = {name: literal(name) for name in
             ("OWNER_KEY_FILE", "WEB_ENV_FILE", "STATE_FILE", "DB", "SPOOL",
              "APP", "ENGINE", "BACKUPS", "PLANTED", "RESULT")}

    def allowed(path: str, capability: str, *, is_dir: bool = False,
                created_by: str | None = None) -> tuple[bool, str]:
        if not path:
            return False, "the probe did not name a path for this check"
        if not is_dir and created_by is None and not host.exists(path):
            return False, "FileNotFoundError: no such file"
        ok = host.acl.may(path, sid, capability, is_dir=is_dir,
                          created_by=created_by)
        return ok, "succeeded" if ok else "PermissionError: access is denied"

    own = ntpath.join(paths["SPOOL"], "probe-submission.intent")
    results = {
        "read_owner_key": allowed(paths["OWNER_KEY_FILE"], acl.READ),
        "read_web_password_hash": allowed(paths["WEB_ENV_FILE"], acl.READ),
        "read_install_state": allowed(paths["STATE_FILE"], acl.READ),
        "read_database": allowed(paths["DB"], acl.READ),
        "write_database": allowed(paths["DB"], acl.WRITE),
        "write_write_ahead_log": allowed(paths["DB"] + "-wal", acl.WRITE),
        "list_spool": allowed(paths["SPOOL"], acl.LIST, is_dir=True),
        "add_to_spool": allowed(paths["SPOOL"], acl.ADD_FILE, is_dir=True),
        # Reading back a file this identity created: it exists, it is owned by
        # that identity, and the inherited OWNER RIGHTS entry is what decides
        # whether ownership carries read with it.
        "read_submitted_request": allowed(paths["PLANTED"], acl.READ),
        "modify_submitted_request": allowed(paths["PLANTED"], acl.WRITE),
        "delete_submitted_request": allowed(paths["PLANTED"], acl.DELETE),
        "read_own_submission": allowed(own, acl.READ, created_by=sid),
        # Creating a file needs add-file on the *directory*: the file does
        # not exist yet, so no ACE on it can be consulted. Asking for WRITE on
        # the absent path instead was a bug in this model, not a gap in the
        # rules, and it showed up as a boundary failure on a correct install.
        "write_application_code": allowed(paths["APP"], acl.ADD_FILE,
                                          is_dir=True),
        "read_engine": allowed(paths["ENGINE"], acl.LIST, is_dir=True),
        "read_backups": allowed(paths["BACKUPS"], acl.LIST, is_dir=True),
    }
    # The one check whose own creation is the point: adding a file must work,
    # and reading it back must not. The model decides both.
    host.add_file(paths["RESULT"], json.dumps(
        {name: list(value) for name, value in results.items()}))


def task_created(host: "FakeWindows", argv: Sequence[str]) -> None:
    """``/Create /F`` registers the task. Registered is not yet running."""
    name = task_name_in(argv)
    if name:
        host.scheduled_tasks.setdefault(name, False)


def task_queried(host: "FakeWindows", argv: Sequence[str]
                 ) -> tuple[int, str, str]:
    name = task_name_in(argv)
    if name not in host.scheduled_tasks:
        return (1, "", _NO_SUCH_TASK)
    return (0, f"{name}\n", "")


def task_run(host: "FakeWindows", argv: Sequence[str]
             ) -> tuple[int, str, str]:
    name = task_name_in(argv)
    if name not in host.scheduled_tasks:
        return (1, "", _NO_SUCH_TASK)
    host.scheduled_tasks[name] = True
    if name == PROBE_TASK_NAME:
        run_isolation_probe(host, argv)
        return (0, "", "")
    # Starting the control centre's task makes something listen on its port,
    # because that is what starting it does. Without this the fake could not
    # tell a control centre that came up from one that did not, which is the
    # question the installer's last screen now answers for the owner.
    if name == integration.WEB_TASK_NAME:
        host.listen(WEB_PORT, "python.exe")
    return (0, "", "")


def task_ended(host: "FakeWindows", argv: Sequence[str]
               ) -> tuple[int, str, str]:
    name = task_name_in(argv)
    if name not in host.scheduled_tasks:
        return (1, "", _NO_SUCH_TASK)
    host.scheduled_tasks[name] = False
    if name == integration.WEB_TASK_NAME:
        host.listening.pop(WEB_PORT, None)
    return (0, "", "")


def task_deleted(host: "FakeWindows", argv: Sequence[str]
                 ) -> tuple[int, str, str]:
    name = task_name_in(argv)
    if host.scheduled_tasks.pop(name, None) is None:
        return (1, "", _NO_SUCH_TASK)
    if name == integration.WEB_TASK_NAME:
        host.listening.pop(WEB_PORT, None)
    return (0, "", "")


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
