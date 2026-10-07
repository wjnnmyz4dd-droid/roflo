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
from dataclasses import dataclass

from .host import Host
from .layout import Layout

#: The scheduled task that starts DeskPilot at boot.
TASK_NAME = "DeskPilot"
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
DB = r"{db}"
SPOOL = r"{spool}"


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
        os.environ[name.strip()] = value.strip()
        count += 1
    return count


def main():
    load_env(ENV_FILE)
    sys.path.insert(0, APP)
    sys.argv = ["solvent", "run", "--db", DB]
    if SPOOL:
        sys.argv += ["--spool", SPOOL]
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


def launcher_source(layout: Layout) -> str:
    return LAUNCHER_SOURCE.format(app=layout.app, env_file=layout.env_file,
                                  db=layout.db, spool=layout.spool)


def backup_source(layout: Layout, keep: int = BACKUP_RETENTION) -> str:
    return BACKUP_SOURCE.format(db=layout.db, backups=layout.backups,
                                app=layout.app, keep=int(keep))


# ---------------------------------------------------------------------------
# Autostart
# ---------------------------------------------------------------------------

def task_xml(python_exe: str, script: str, *, at_startup: bool,
             daily_at: str = "03:17:00") -> str:
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
        "    <UserId>S-1-5-18</UserId>\n"
        "    <RunLevel>HighestAvailable</RunLevel>\n"
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
    backup: str
    task_xml: str
    backup_task_xml: str

    @classmethod
    def under(cls, layout: Layout) -> "InstalledScripts":
        return cls(
            launcher=ntpath.join(layout.app, "deskpilot-service.py"),
            backup=ntpath.join(layout.app, "deskpilot-backup.py"),
            task_xml=ntpath.join(layout.config, "deskpilot-task.xml"),
            backup_task_xml=ntpath.join(layout.config,
                                        "deskpilot-backup-task.xml"),
        )
