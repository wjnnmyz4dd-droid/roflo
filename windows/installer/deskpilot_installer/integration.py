"""Enacting on Windows what Solvent only records: autostart, firewall, backups.

Solvent has authorities for its own state -- ``setup firewall`` records what the
host's network posture *is*, the policy store records what was decided -- but
none of them can make Windows do anything. That gap is what this module fills,
and the split is deliberate: Solvent remains the record of what is true, and
this module is the only thing that changes the machine to match. Neither
duplicates the other, so there is no second opinion to drift.

**Autostart is a scheduled task, not a Windows service.** A real service must
speak the Service Control Manager protocol -- respond to start and stop within
the SCM's timeouts -- and Python cannot do that without ``pywin32`` or a
wrapper such as ``nssm``. Both are third-party packages, and DeskPilot's entire
dependency story is that it has none: its test suite passes with site-packages
switched off, so there is no pip step in a normal installation. Adding one to
get a nicer entry in ``services.msc`` would trade the property that makes this
installer simple for a cosmetic gain. Task Scheduler is native, needs nothing
installed, starts at boot without a logged-in user, and -- through the XML
definition used below rather than the shorter command-line form -- restarts the
process when it dies. That is every requirement §14 actually states.

**The firewall change is one rule, and it opens nothing.** DeskPilot's control
centre binds loopback. Windows does not filter loopback traffic, so no inbound
allow rule is needed for it to work, and adding one would be the installer
quietly making an internal service reachable. The single rule created here is a
*block*: inbound, public profile, DeskPilot's own interpreter. It changes
nothing today and it means a later mistake -- someone binding 0.0.0.0, a
profile flipping to Public -- does not silently expose the owner's business.
Nothing else is touched, and uninstall removes that one rule by its exact name.

**Backups mirror ``deploy/backup.sh`` rather than reinventing it.** The Linux
script is the authority for what a DeskPilot backup means: SQLite's online
backup API so a live WAL database is captured without tearing, an integrity
check, a verification of Solvent's own audit hash chain inside the copy,
compression, and a retention count. The Windows job does the same steps in the
same order for the same reasons, including excluding the secrets file.
"""

from __future__ import annotations

import ntpath
from collections.abc import Callable
from dataclasses import dataclass

from . import isolation, secrets_env
from .host import Host
from .layout import Layout, WEB_HOST, WEB_PORT

#: The scheduled task that starts DeskPilot's runtime at boot.
TASK_NAME = "DeskPilot"
#: The scheduled task that starts the owner's control centre at boot.
WEB_TASK_NAME = "DeskPilot Control Centre"
#: The scheduled task that takes a daily backup.
BACKUP_TASK_NAME = "DeskPilot Backup"
#: The one firewall rule this installer creates.
FIREWALL_RULE_NAME = "DeskPilot control centre (loopback only)"

#: Backups kept, matching ``SOLVENT_BACKUP_KEEP`` in ``deploy/backup.sh``.
BACKUP_RETENTION = 14


# ---------------------------------------------------------------------------
# The launcher
# ---------------------------------------------------------------------------
#
# Written to disk at install time and run by the scheduled task. It exists
# because the secrets live in a file and Solvent reads them from the
# environment: something has to bridge the two, and a ``.cmd`` file doing it
# with ``for /f`` would be shell quoting around the owner's signing key. This
# is a Python file instead, parsed by Python, with no shell anywhere.
#
# One template, two services. The command it runs is data -- ``ARGV``, supplied
# per service from :data:`BOOT_SERVICES` -- because loading the owner's secrets
# into the environment is one responsibility and it should have one
# implementation. The previous version hardcoded the runtime's arguments, and
# the only way to add the control centre would have been a second copy of the
# same credential-loading code.
LAUNCHER_SOURCE = '''\
"""Start DeskPilot with the owner's environment loaded. Written by the installer.

Reads the secrets file, puts its values in the environment, and hands control
to Solvent's own entry point. Nothing is printed: this process's output goes to
the Windows task log, and the values it handles must not appear there.
"""

import os
import runpy
import sys

APP = r"{app}"
ENV_FILE = r"{env_file}"
ARGV = {argv}
ENV_ALLOW = {env_allow}


def load_env(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if ENV_ALLOW is not None and name not in ENV_ALLOW:
            continue
        os.environ[name] = value.strip()
        count += 1
    return count


def main():
    load_env(ENV_FILE)
    sys.path.insert(0, APP)
    sys.argv = list(ARGV)
    runpy.run_module("solvent.cli", run_name="__main__")


if __name__ == "__main__":
    main()
'''

#: The backup job. A direct translation of ``deploy/backup.sh``.
BACKUP_SOURCE = '''\
"""Back up DeskPilot's durable state. Written by the installer.

A translation of ``deploy/backup.sh`` for Windows, step for step and for the
same reasons. SQLite's online backup API is used rather than a file copy
because copying a live WAL database can capture a torn one. The copy is then
verified twice -- SQLite's own integrity check, and Solvent's audit hash chain
-- because a backup nobody has checked is a hope, not a backup.

The secrets file is deliberately not included. A backup that quietly contained
the owner's signing key would turn every copy into somewhere it can leak from.
"""

import datetime
import glob
import gzip
import os
import shutil
import sqlite3
import sys

DB = r"{db}"
DEST = r"{backups}"
APP = r"{app}"
KEEP = {keep}


def main():
    if not os.path.isfile(DB):
        print("no database at " + DB)
        return 1
    os.makedirs(DEST, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    out = os.path.join(DEST, "solvent-" + stamp + ".db")

    src = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    with dst:
        src.backup(dst)
    src.close()
    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        dst.close()
        print("INTEGRITY CHECK FAILED")
        return 1
    dst.close()

    sys.path.insert(0, APP)
    try:
        from solvent.audit import AuditLog
        from solvent.store import Store
    except ImportError:
        print("  integrity: ok (audit chain not checked)")
    else:
        store = Store(out)
        ok, detail = AuditLog(store).verify_chain()
        store.close()
        print("  integrity: ok | audit chain: " + str(detail))
        if not ok:
            print("AUDIT CHAIN FAILED in the backup")
            return 1

    with open(out, "rb") as raw, gzip.open(out + ".gz", "wb") as packed:
        shutil.copyfileobj(raw, packed)
    os.remove(out)
    print("  wrote " + out + ".gz")

    existing = sorted(glob.glob(os.path.join(DEST, "solvent-*.db.gz")),
                      reverse=True)
    for stale in existing[KEEP:]:
        os.remove(stale)
    print("  keeping the newest " + str(KEEP) + " backup(s) in " + DEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ---------------------------------------------------------------------------
# The two services
# ---------------------------------------------------------------------------
#
# DeskPilot is two processes, as it is on Linux: ``deploy/solvent.service`` runs
# the runtime and ``deploy/solvent-web.service`` runs the owner's control
# centre. The Windows installer registered only the first, so the control
# centre the Finish page links to was never started and the browser reported
# ERR_CONNECTION_REFUSED on 127.0.0.1:8765.
#
# Enumerated here rather than written out at each site. Every place that
# creates, starts, stops, re-registers, verifies or removes a DeskPilot service
# reads this tuple, so adding the second one did not mean finding six places
# that each knew the first one's name.


def runtime_argv(layout: Layout) -> tuple[str, ...]:
    """What starts DeskPilot's runtime.

    ``--always-on`` is the mode a boot-started service on a host that stays
    awake is actually in, and it makes the startup checks refuse to run against
    in-memory storage instead of appearing to work and losing everything at the
    next restart. ``deploy/solvent.service`` passes it for the same reason.

    No ``--spool``: that is a ``solvent web`` flag. Passing it here is what made
    the task exit 2 on every boot, because ``solvent run`` rejects unrecognised
    arguments -- correctly. The runtime does not read the spool; owner intents
    are consumed by ``solvent intents``, under the owner's explicit
    authorisation, and never on the runtime's timer.
    """
    return ("solvent", "run", "--db", layout.db, "--always-on")


def web_argv(layout: Layout) -> tuple[str, ...]:
    """What starts the owner's control centre, bound to loopback."""
    return ("solvent", "web", "--db", layout.db,
            "--host", WEB_HOST, "--port", str(WEB_PORT),
            "--spool", layout.spool)


@dataclass(frozen=True, slots=True)
class BootService:
    """One process the installer starts at boot, and how to start it."""

    task_name: str
    #: Basename of the generated launcher, written into ``App``.
    script: str
    #: Basename of the generated task definition, written into ``Config``.
    xml: str
    #: The Solvent command, built from the layout.
    argv: "Callable[[Layout], tuple[str, ...]]"
    #: Which names from the secrets file this service may have. ``None`` means
    #: all of them. The control centre gets one, because it reads one:
    #: ``solvent.web`` states that the owner's signing key "is never read
    #: here, never sent to a browser, and never needed to display anything",
    #: and a launcher that put the whole file into its environment would have
    #: handed a compromised web process the key that design keeps away from
    #: it. The runtime does sign, so it gets everything.
    env_allow: tuple[str, ...] | None
    #: For the installation log and the readiness screen.
    purpose: str
    #: The SID this service's scheduled task logs on as. SYSTEM for the
    #: runtime, which is the only execution authority; LOCAL SERVICE for the
    #: control centre, which is the process a stranger could reach.
    identity: str = "*S-1-5-18"
    #: ``True`` for a task that needs the elevated token. The control centre
    #: binds a high port and reads a database: it needs neither.
    elevated: bool = True
    #: Which environment file the launcher reads. The control centre's own,
    #: which holds no owner secret -- the launcher reads its file whole and
    #: filters afterwards, so pointing both services at the file containing
    #: the owner signing key put that key in the web process's memory.
    env_file: "Callable[[Layout], str]" = lambda layout: layout.env_file

    def script_path(self, layout: Layout) -> str:
        return ntpath.join(layout.app, self.script)

    def xml_path(self, layout: Layout) -> str:
        return ntpath.join(layout.config, self.xml)


# Named, not positional: the fields are a path, a path, a callable, a sentence
# and a tuple, and inserting one in the middle silently reassigned two of them
# when they were positional.
BOOT_SERVICES: tuple[BootService, ...] = (
    BootService(task_name=TASK_NAME,
                script="deskpilot-service.py",
                xml="deskpilot-task.xml",
                argv=runtime_argv,
                purpose="DeskPilot's runtime",
                env_allow=None),
    BootService(task_name=WEB_TASK_NAME,
                script="deskpilot-web.py",
                xml="deskpilot-web-task.xml",
                argv=web_argv,
                purpose="the owner's control centre",
                env_allow=(secrets_env.WEB_PASSWORD_HASH_ENV,),
                identity=isolation.WEB_SID,
                elevated=False,
                env_file=lambda layout: layout.web_env_file),
)

#: Every task this installer creates. What uninstall has to remove.
ALL_TASK_NAMES: tuple[str, ...] = (
    tuple(service.task_name for service in BOOT_SERVICES)
    + (BACKUP_TASK_NAME,)
)


def launcher_source(layout: Layout, service: BootService) -> str:
    """The launcher for one service: load the secrets, then run its command."""
    return LAUNCHER_SOURCE.format(
        app=layout.app, env_file=service.env_file(layout),
        argv=repr(list(service.argv(layout))),
        env_allow=(None if service.env_allow is None
                   else repr(tuple(service.env_allow))))


def backup_source(layout: Layout, keep: int = BACKUP_RETENTION) -> str:
    return BACKUP_SOURCE.format(db=layout.db, backups=layout.backups,
                                app=layout.app, keep=int(keep))


# ---------------------------------------------------------------------------
# Autostart
# ---------------------------------------------------------------------------

def task_xml(python_exe: str, script: str, *, at_startup: bool,
             daily_at: str = "03:17:00",
             identity: str = "*S-1-5-18", elevated: bool = True) -> str:
    """A Task Scheduler definition.

    XML rather than ``schtasks /Create /TR``, because the command-line form
    cannot express restart-on-failure and the recovery behaviour §14 asks for
    is the whole reason a scheduled task is an acceptable substitute for a
    service. ``&`` and ``<`` in paths are escaped; a path containing them is
    unlikely but an installer that corrupts its own task definition on an
    unusual path is worse than one that does not.
    """
    trigger = ("<BootTrigger><Enabled>true</Enabled></BootTrigger>"
               if at_startup else
               f"<CalendarTrigger>"
               f"<StartBoundary>2026-01-01T{daily_at}</StartBoundary>"
               f"<Enabled>true</Enabled>"
               f"<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
               f"</CalendarTrigger>")
    restart = ("<RestartOnFailure><Interval>PT1M</Interval>"
               "<Count>10</Count></RestartOnFailure>") if at_startup else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>DeskPilot, installed by the DeskPilot installer."
        "</Description>\n"
        "  </RegistrationInfo>\n"
        f"  <Triggers>{trigger}</Triggers>\n"
        "  <Principals><Principal id=\"Author\">\n"
        f"    <UserId>{identity.lstrip('*')}</UserId>\n"
        # ServiceAccount, because LOCAL SERVICE and SYSTEM are logged on by the
        # service control manager and have no password for Task Scheduler to
        # hold. A task that asked for an interactive logon here would need a
        # stored credential, which is a new secret created to protect a secret.
        "    <LogonType>ServiceAccount</LogonType>\n"
        f"    <RunLevel>{'HighestAvailable' if elevated else 'LeastPrivilege'}"
        "</RunLevel>\n"
        "  </Principal></Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        f"    {restart}\n"
        "  </Settings>\n"
        "  <Actions Context=\"Author\"><Exec>\n"
        f"    <Command>{_escape(python_exe)}</Command>\n"
        f"    <Arguments>\"{_escape(script)}\"</Arguments>\n"
        "  </Exec></Actions>\n"
        "</Task>\n"
    )


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def register_task(host: Host, name: str, xml_path: str) -> bool:
    """Create or replace one scheduled task from its XML definition."""
    result = host.run(["schtasks.exe", "/Create", "/TN", name,
                       "/XML", xml_path, "/F"], timeout=120.0)
    return result.ok


def start_task(host: Host, name: str) -> bool:
    """Run a registered task now.

    A boot trigger starts a service at the next boot and not before, so a fresh
    installation left both services down until the owner rebooted -- and the
    Finish page offers the control centre's address immediately. Starting them
    here is what makes that link answer.
    """
    result = host.run(["schtasks.exe", "/Run", "/TN", name], timeout=120.0)
    return result.ok


def stop_task(host: Host, name: str) -> bool:
    """Ask a running task to stop. Used before an upgrade replaces its code."""
    result = host.run(["schtasks.exe", "/End", "/TN", name], timeout=120.0)
    return result.ok


def delete_task(host: Host, name: str) -> bool:
    result = host.run(["schtasks.exe", "/Delete", "/TN", name, "/F"],
                      timeout=120.0)
    return result.ok


def task_exists(host: Host, name: str) -> bool:
    return host.run(["schtasks.exe", "/Query", "/TN", name],
                    timeout=60.0).ok


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

def firewall_commands(python_exe: str) -> tuple[tuple[str, ...], ...]:
    """The one rule DeskPilot adds. Scoped to a program, and it blocks.

    Adding rather than replacing: ``netsh advfirewall firewall add rule``
    touches exactly the named rule. There is no ``set allprofiles`` here and no
    ``reset``, so the owner's policy and MetaTrader's rules are untouched.
    """
    return ((
        "netsh.exe", "advfirewall", "firewall", "add", "rule",
        f"name={FIREWALL_RULE_NAME}",
        "dir=in", "action=block", "profile=public",
        f"program={python_exe}",
        "description=DeskPilot binds loopback only. This rule keeps it that "
        "way if a future change tries to expose it.",
    ),)


def remove_firewall_rule(host: Host) -> bool:
    """Delete only the rule this installer created, by exact name."""
    result = host.run(["netsh.exe", "advfirewall", "firewall", "delete",
                       "rule", f"name={FIREWALL_RULE_NAME}"], timeout=120.0)
    return result.ok


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class InstalledScripts:
    """Where the generated scripts live. Inside App: replaced by an upgrade."""

    launcher: str
    web: str
    backup: str
    task_xml: str
    web_task_xml: str
    backup_task_xml: str

    @classmethod
    def under(cls, layout: Layout) -> "InstalledScripts":
        # Unpacked rather than indexed: if a third boot service is ever added,
        # this raises instead of silently leaving it out of the named paths
        # that uninstall and upgrade read.
        runtime, control_centre = BOOT_SERVICES
        return cls(
            launcher=runtime.script_path(layout),
            web=control_centre.script_path(layout),
            backup=ntpath.join(layout.app, "deskpilot-backup.py"),
            task_xml=runtime.xml_path(layout),
            web_task_xml=control_centre.xml_path(layout),
            backup_task_xml=ntpath.join(layout.config,
                                        "deskpilot-backup-task.xml"),
        )
