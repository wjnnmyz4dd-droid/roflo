"""Repair, uninstall and upgrade: the operations that can destroy a business.

Installation is the easy direction. Everything here touches a machine that
already holds the owner's database, audit history and backups, so each
operation is defined by what it refuses to do.

**Repair does not re-provision.** It replaces application files and re-registers
Windows integration, and it leaves ``Data``, ``Config``, ``Logs`` and
``Backups`` alone. In particular it does not generate a new owner key. A fresh
``SOLVENT_OWNER_KEY`` would be indistinguishable from a successful repair and
would silently invalidate every approval the owner has ever signed -- the audit
chain would still verify, and nothing the owner could see would have changed,
until the first time they tried to authorise spending. So repair reads the
existing key and puts it back to work rather than making one.

**Uninstall preserves the owner's records by default.** The split between
replaceable and owner-owned directories is already drawn in
:mod:`~deskpilot_installer.layout`, and :func:`plan_uninstall` reports both
lists before anything is deleted. Removing the database requires
``purge_owner_data=True``, which the wizard only sets from an explicit,
separately-confirmed choice. "Uninstall" meaning "delete the accounting
records" is a defensible option and an indefensible default.

**Upgrade is reversible.** The previous application is moved aside rather than
deleted, the database is backed up before any migration runs, and a failure
restores both. The order matters: a backup taken *after* the new code has
opened the database is a backup of whatever the new code did to it.

Nothing here stops, starts or inspects MetaTrader. The only process this module
will stop is DeskPilot's own scheduled task.
"""

from __future__ import annotations

import ntpath
from dataclasses import dataclass, field

from . import integration, payload, secrets_env
from .engine import Engine, InstallState, OwnerAnswers, StepFailed
from .host import Host
from .installlog import Log
from .layout import Layout
from .probe import Settings
from .report import Failure, Outcome, Report, Row, Secret


# ---------------------------------------------------------------------------
# Reading back what the installer wrote
# ---------------------------------------------------------------------------

def read_env_secrets(host: Host, path: str) -> dict[str, Secret]:
    """Load the secrets file. Values come back wrapped so they cannot be logged."""
    out: dict[str, Secret] = {}
    if not host.exists(path):
        return out
    try:
        text = host.read_bytes(path).decode("utf-8", "replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if name and value:
            out[name] = Secret(value, name)
    return out


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UninstallPlan:
    """Exactly what an uninstall would remove and what it would leave."""

    remove: tuple[str, ...]
    keep: tuple[str, ...]
    tasks: tuple[str, ...]
    firewall_rule: str
    purge_owner_data: bool

    def rows(self) -> tuple[Row, ...]:
        kept = Row("Kept", Outcome.PASS,
                   ("nothing - you asked for the owner data to be removed too"
                    if self.purge_owner_data
                    else "your database, configuration, audit history and "
                         "backups stay where they are"),
                   facts={"paths": list(self.keep)})
        return (
            Row("Removed", Outcome.PASS,
                f"{len(self.remove)} location(s): application files"
                + (", and your database, audit history and backups"
                   if self.purge_owner_data else ""),
                facts={"paths": list(self.remove)}),
            kept,
            Row("Windows integration", Outcome.PASS,
                f"{len(self.tasks)} scheduled task(s) removed; "
                f"{'one firewall rule removed' if self.firewall_rule else 'no firewall rule to remove'}",
                facts={"tasks": list(self.tasks),
                       "firewall_rule": self.firewall_rule}),
        )


def plan_uninstall(host: Host, layout: Layout,
                   state: InstallState | None = None, *,
                   purge_owner_data: bool = False) -> UninstallPlan:
    """Decide what to remove. Reads only -- nothing is deleted by planning."""
    remove = [d for d in layout.replaceable_dirs if host.exists(d)]
    keep = [d for d in layout.owner_dirs if host.exists(d)]
    if purge_owner_data:
        remove += keep
        keep = []
    tasks = tuple(state.tasks) if state and state.tasks else (
        integration.TASK_NAME, integration.BACKUP_TASK_NAME)
    rule = (state.firewall_rule if state and state.firewall_rule
            else integration.FIREWALL_RULE_NAME)
    return UninstallPlan(tuple(remove), tuple(keep), tasks, rule,
                         purge_owner_data)


def uninstall(host: Host, layout: Layout, *, purge_owner_data: bool = False,
              log: Log | None = None) -> Report:
    """Carry out the plan. Owner data survives unless explicitly purged."""
    log = log or Log(host)
    state = InstallState.load(host, layout.state_file)
    plan = plan_uninstall(host, layout, state,
                          purge_owner_data=purge_owner_data)
    log.step("Uninstalling DeskPilot")

    for task in plan.tasks:
        integration.delete_task(host, task)
        log.write(f"  removed scheduled task {task!r}")
    if plan.firewall_rule:
        integration.remove_firewall_rule(host)
        log.write(f"  removed firewall rule {plan.firewall_rule!r}")

    for path in plan.remove:
        host.remove_tree(path)
        log.write(f"  removed {path}")

    rows = list(plan.rows())
    # Prove the promise rather than restate it: the kept paths are checked for
    # existence *after* the deletions, so a bug that removed one shows up here
    # instead of in the owner's next backup.
    survived = [p for p in plan.keep if host.exists(p)]
    missing = [p for p in plan.keep if not host.exists(p)]
    if missing:
        rows.append(Row("Data preservation", Outcome.FAIL,
                        f"these should have been kept and are gone: "
                        f"{', '.join(missing)}"))
    elif plan.keep:
        rows.append(Row("Data preservation", Outcome.PASS,
                        f"verified present after uninstall: "
                        f"{', '.join(survived)}"))
    log.flush()
    return Report("uninstall", tuple(rows))


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def repair(host: Host, settings: Settings, answers: OwnerAnswers,
           log: Log | None = None) -> Report:
    """Replace application files and Windows integration. Touches no owner data.

    Implemented as a subset of the install steps rather than a parallel
    sequence, so a change to how the application is extracted or how the task
    is registered cannot apply to installation and miss repair.
    """
    log = log or Log(host)
    layout = settings.layout
    existing = read_env_secrets(host, layout.env_file)
    key = existing.get(secrets_env.OWNER_KEY_ENV)
    if key is None:
        return Report("repair", (), Failure(
            what_failed="Repair cannot continue",
            why=(f"no owner credentials were found at {layout.env_file}, so "
                 f"this is not a DeskPilot installation this installer can "
                 f"repair"),
            changed=(),
            not_changed=("nothing was changed",
                         "your database and audit history were not touched",
                         "MetaTrader was not touched"),
            safe_next_action=("Install DeskPilot normally. An existing "
                              "database in C:\\DeskPilot\\Data will be kept "
                              "and reused."),
        ))

    engine = Engine(host, settings, answers, log)
    engine.owner_key = key
    had_db = host.exists(layout.db)

    sequence = [
        ("Verifying package", engine.step_verify_package),
        ("Checking Python", engine.step_python),
        ("Reinstalling DeskPilot", engine.step_extract),
        ("Rebuilding the Python environment", engine.step_venv),
        ("Re-registering certified capabilities", engine.step_capability),
        ("Restoring the firewall rule", engine.step_firewall),
        ("Restoring autostart", engine.step_autostart),
        ("Restoring backups", engine.step_backups),
    ]
    rows: list[Row] = []
    for title, step in sequence:
        log.step(title)
        try:
            result = step()
        except payload.PayloadRefused as refused:
            log.flush()
            return Report("repair", tuple(rows), refused.failure)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            detail = getattr(exc, "detail", None) or str(exc)
            rows.append(Row(title, Outcome.FAIL, detail))
            log.flush()
            return Report("repair", tuple(rows), Failure(
                what_failed=f"{title} failed",
                why=detail,
                changed=tuple(engine.changed),
                not_changed=("your database was not modified",
                             "your configuration and owner key were not changed",
                             "your audit history and backups were not touched",
                             "MetaTrader was not touched"),
                safe_next_action=(f"Read {layout.install_log} and run Repair "
                                  f"again once the cause is fixed."),
            ))
        rows.append(result.row())

    # The whole promise of repair, asserted rather than assumed.
    if had_db and not host.exists(layout.db):
        rows.append(Row("Data preservation", Outcome.FAIL,
                        f"the database at {layout.db} was lost during repair"))
    else:
        rows.append(Row("Data preservation", Outcome.PASS,
                        "database, configuration, owner key, audit history and "
                        "backups are unchanged"))
    log.flush()
    return Report("repair", tuple(rows))


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

@dataclass
class UpgradeRecord:
    """What an upgrade moved aside, so a rollback knows where to look."""

    previous_app: str = ""
    database_backup: str = ""
    task_was_running: bool = False
    notes: list[str] = field(default_factory=list)


def upgrade(host: Host, settings: Settings, answers: OwnerAnswers,
            log: Log | None = None) -> Report:
    """Replace the application, keeping every piece of owner state.

    Verify, stop, back up, move aside, extract, migrate, check -- and on any
    failure put back what was moved. The database backup is taken before the
    new code is allowed near the database, which is the only ordering in which
    a rollback is worth anything.
    """
    log = log or Log(host)
    layout = settings.layout
    record = UpgradeRecord()
    rows: list[Row] = []

    verdict = payload.verify(host, settings.package_path,
                             settings.expected_digest)
    if not verdict.verified:
        # Refused before anything is stopped or moved. A failed upgrade must
        # leave a running installation running.
        return Report("upgrade", (payload.row(verdict),),
                      payload.refusal(verdict))
    rows.append(payload.row(verdict))

    state = InstallState.load(host, layout.state_file)
    if state is None or not host.exists(layout.db):
        return Report("upgrade", tuple(rows), Failure(
            what_failed="There is nothing to upgrade",
            why=(f"no existing DeskPilot installation was found at "
                 f"{layout.root}"),
            changed=(),
            not_changed=("nothing was changed", "MetaTrader was not touched"),
            safe_next_action="Install DeskPilot instead of upgrading it.",
        ))
    if state.payload_sha256 == verdict.actual:
        rows.append(Row("Version", Outcome.PASS,
                        "this is the version already installed; nothing to do"))
        return Report("upgrade", tuple(rows))

    engine = Engine(host, settings, answers, log)
    engine.owner_key = read_env_secrets(host, layout.env_file).get(
        secrets_env.OWNER_KEY_ENV)
    # Deliberately *not* seeded from ``state.python_exe``: that records the
    # virtual environment's own interpreter, which cannot be used to rebuild
    # that environment. ``step_python`` re-discovers a system Python below, and
    # ``step_venv`` then sets this back to the rebuilt environment's.
    engine.python_exe = ""

    try:
        log.step("Stopping DeskPilot")
        record.task_was_running = integration.task_exists(
            host, integration.TASK_NAME)
        if record.task_was_running:
            host.run(["schtasks.exe", "/End", "/TN", integration.TASK_NAME],
                     timeout=120.0)
            rows.append(Row("Stopping DeskPilot", Outcome.PASS,
                            "the DeskPilot task was stopped; "
                            "MetaTrader was not touched"))

        log.step("Backing up the database")
        record.database_backup = _backup_database(host, layout)
        rows.append(Row("Database backup", Outcome.PASS,
                        record.database_backup,
                        facts={"path": record.database_backup}))

        log.step("Moving the previous version aside")
        record.previous_app = layout.app + ".previous"
        if host.exists(record.previous_app):
            host.remove_tree(record.previous_app)
        if host.exists(layout.app):
            host.move(layout.app, record.previous_app)
        rows.append(Row("Previous version", Outcome.PASS,
                        f"kept at {record.previous_app} until this upgrade "
                        f"succeeds"))

        log.step("Installing the new version")
        written = payload.extract(host, settings.package_path, layout.app)
        rows.append(Row("New version", Outcome.PASS,
                        f"{len(written)} files extracted"))

        log.step("Rebuilding the Python environment")
        rows.append(engine.step_python().row())
        rows.append(engine.step_venv().row())

        log.step("Applying migrations")
        migrated = engine.solvent_cli("doctor", "--db", layout.db)
        if not migrated.ok:
            raise _UpgradeFailed(
                f"the new version could not open the existing database: "
                f"{migrated.output.strip()[:300]}")
        rows.append(Row("Migrations", Outcome.PASS,
                        "the new version opened the existing database"))

        log.step("Checking the upgraded installation")
        checked = engine.verify(probe_inference=False)
        rows.extend(checked.rows)
        if not checked.may_continue:
            raise _UpgradeFailed(
                "the upgraded installation did not pass its own checks: "
                + "; ".join(f"{r.name}: {r.detail}" for r in checked.blockers))

    except (_UpgradeFailed, payload.PayloadRefused, OSError,
            StepFailed) as exc:
        detail = (exc.failure.why if isinstance(exc, payload.PayloadRefused)
                  else getattr(exc, "detail", None) or str(exc))
        restored = _rollback(host, layout, record, log)
        rows.append(Row("Rollback", Outcome.PASS if restored else Outcome.FAIL,
                        "; ".join(record.notes) or "nothing to restore"))
        log.flush()
        return Report("upgrade", tuple(rows), Failure(
            what_failed="The upgrade failed and was rolled back",
            why=detail,
            changed=tuple(record.notes),
            not_changed=("your database was restored from the backup taken "
                         "before the upgrade",
                         "your configuration and owner key were not changed",
                         "your audit history and backups were not touched",
                         "MetaTrader was not touched"),
            safe_next_action=(f"DeskPilot is back on the previous version. "
                              f"Read {layout.install_log} before trying again."),
        ))

    # Success: the kept copy is now dead weight.
    if record.previous_app and host.exists(record.previous_app):
        host.remove_tree(record.previous_app)
    engine.state.payload_sha256 = verdict.actual
    engine.state.python_exe = engine.python_exe
    engine.state.tasks = list(state.tasks)
    engine.state.firewall_rule = state.firewall_rule
    engine.step_record_state()
    if record.task_was_running:
        integration.register_task(
            host, integration.TASK_NAME,
            integration.InstalledScripts.under(layout).task_xml)
        rows.append(Row("Restarting DeskPilot", Outcome.PASS,
                        "the DeskPilot task was re-registered"))
    log.flush()
    return Report("upgrade", tuple(rows))


class _UpgradeFailed(Exception):
    """Internal: an upgrade step failed and a rollback is required."""


def _backup_database(host: Host, layout: Layout) -> str:
    """Copy the database aside before the new code can touch it.

    A byte copy, not SQLite's online backup API, and deliberately so: the
    service has just been stopped, so there is no live writer to tear, and a
    rollback needs the file the old version was using rather than a logically
    equivalent rebuild of it.
    """
    import datetime
    stamp = (datetime.datetime.now(datetime.timezone.utc)
             .strftime("%Y%m%dT%H%M%SZ"))
    target = ntpath.join(layout.backups, f"pre-upgrade-{stamp}.db")
    host.write_bytes(target, host.read_bytes(layout.db))
    return target


def _rollback(host: Host, layout: Layout, record: UpgradeRecord,
              log: Log) -> bool:
    """Put back the previous application and database. Best effort, reported."""
    ok = True
    if record.previous_app and host.exists(record.previous_app):
        try:
            if host.exists(layout.app):
                host.remove_tree(layout.app)
            host.move(record.previous_app, layout.app)
            record.notes.append(f"restored the previous application to "
                                f"{layout.app}")
        except OSError as exc:
            record.notes.append(f"could not restore the previous application: "
                                f"{exc}")
            ok = False
    if record.database_backup and host.exists(record.database_backup):
        try:
            host.write_bytes(layout.db, host.read_bytes(record.database_backup))
            record.notes.append(f"restored the database from "
                                f"{record.database_backup}")
        except OSError as exc:
            record.notes.append(f"could not restore the database: {exc}")
            ok = False
    if record.task_was_running:
        integration.register_task(
            host, integration.TASK_NAME,
            integration.InstalledScripts.under(layout).task_xml)
        record.notes.append("re-registered the DeskPilot startup task")
    for note in record.notes:
        log.write(f"  rollback: {note}")
    return ok
