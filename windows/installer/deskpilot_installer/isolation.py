"""Windows access control for the control centre, and the proof that it holds.

The control centre is the part of DeskPilot most likely to be attacked: it
listens on a socket, it renders the owner's data, and it is the one component a
stranger could ever reach. The design has always said that taking it over does
not take over the business. Until this module, that sentence rested on Python:
the web process ran as SYSTEM alongside the runtime, its spool object simply
had no read method, and its launcher read the file containing the owner signing
key and then declined to use it.

Those are application-level controls. They are worth having -- defence in depth
is made of them -- but none of them is a boundary, because every one of them is
enforced by the code an attacker would already be running.

This module moves the same rules down to where Windows enforces them:

* the control centre runs as **LOCAL SERVICE**, not SYSTEM;
* the owner signing key lives in a file whose DACL does not mention that
  identity at all;
* the database is granted read, never write;
* the spool is a true drop box -- add a file, and nothing else.

Everything here is expressed as ``icacls`` arguments and a Task Scheduler
principal, and nothing here is believed. :func:`probe_script` builds a script
that the installer runs **as the control centre's own identity** and that tries
each forbidden thing in turn. An installation reports isolation as verified
only when that probe comes back with every expectation met.

Identities are named by SID rather than by name, throughout. ``icacls
... /grant:r SYSTEM:(F)`` fails on a Windows whose locale calls that account
something else, and a VPS is exactly where a non-English Windows turns up. A
SID is the same string everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from .host import Host
from .layout import Layout

#: Well-known SIDs, used instead of names so a localised Windows behaves.
SYSTEM_SID = "*S-1-5-18"
ADMINISTRATORS_SID = "*S-1-5-32-544"

#: ``NT AUTHORITY\LOCAL SERVICE``. The control centre's identity.
#:
#: Chosen over a purpose-made local account because a local account needs a
#: password, and a password the installer generates has to be stored somewhere
#: for Task Scheduler to log on with -- which would add a new secret to protect
#: in order to protect a secret. LOCAL SERVICE needs none: Task Scheduler logs
#: it on with ``ServiceAccount`` and no credential exists to steal. It is also
#: not a member of Administrators, which is the property being bought.
WEB_SID = "*S-1-5-19"

#: ``OWNER RIGHTS``. Present in a DACL, it *replaces* the implicit rights the
#: owner of an object otherwise has.
#:
#: This is the ACE that makes the drop box a drop box. A process that creates a
#: file becomes its owner, and an owner implicitly holds READ_CONTROL and
#: WRITE_DAC -- so the control centre could re-permission its own submission
#: and read it back, whatever the inherited ACEs said. An inherit-only OWNER
#: RIGHTS ACE granting read-attributes and nothing else takes that away.
OWNER_RIGHTS_SID = "*S-1-3-4"


@dataclass(frozen=True)
class Rule:
    """One ``icacls`` invocation, and why it exists."""

    path: str
    #: ``icacls`` arguments after the path.
    args: tuple[str, ...]
    #: For the installation log and the report.
    why: str
    #: False for a path that may legitimately be absent (the WAL sidecars are
    #: not there until SQLite creates them).
    required: bool = True

    def argv(self) -> list[str]:
        return ["icacls.exe", self.path, *self.args]


def rules(layout: Layout) -> tuple[Rule, ...]:
    """Every access-control change an isolated installation needs.

    Ordered so that a directory is reset before the files inside it are
    granted anything: ``/inheritance:r`` on a parent after granting a child
    would leave the child's inherited entries in place, which is the mistake
    :mod:`secrets_env` documents for the secrets file.
    """
    full = f"{SYSTEM_SID}:(OI)(CI)(F)"
    admins_full = f"{ADMINISTRATORS_SID}:(OI)(CI)(F)"
    return (
        # -- Config: the runtime's. The key lives here. -------------------
        Rule(layout.config,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full),
             "the owner signing key and install state are the runtime's alone"),
        # The one file in Config the control centre may read. Its own password
        # hash: it has to check the password somebody typed against something.
        Rule(layout.web_env_file,
             ("/inheritance:r",
              "/grant:r", f"{SYSTEM_SID}:(R,W)",
              "/grant:r", f"{ADMINISTRATORS_SID}:(R,W)",
              "/grant:r", f"{WEB_SID}:(R)"),
             "the control centre reads its own password hash and nothing else"),
        # -- Engine: not the control centre's business at all. -------------
        Rule(layout.engine,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full),
             "the installation engine can install; the website cannot run it"),
        Rule(layout.backups,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full),
             "a backup is a copy of everything, so it is the runtime's alone"),
        # -- Data: read the database, drop into the spool, nothing else. ---
        # Two of the three parts of this rule are redundant, and saying so is
        # worth more than leaving a reader to work it out:
        #
        #   * ``/inheritance:r`` here and on the spool each stop the drive's
        #     default ``Users:(OI)(CI)(RX)`` from reaching a submitted request.
        #     Either alone is enough; both are applied because the cost is
        #     nothing and the failure mode of getting it wrong is silent.
        #   * ``(X,RA)`` is belt and braces. Windows grants Everyone
        #     SeChangeNotifyPrivilege by default, which skips the traverse
        #     check on intermediate directories, so the control centre reaches
        #     the database and the spool without it on an ordinary host. It is
        #     here for a host where that privilege has been taken away.
        #
        # Neither is mutation-tested, because a mutation of a redundant control
        # cannot change behaviour and would be an invalid mutant rather than a
        # survivor. ``tools/mutate_installer.py`` records the same thing.
        Rule(layout.data,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full,
              "/grant:r", f"{WEB_SID}:(X,RA)"),
             "traverse only: the control centre cannot list the owner's data"),
        # The database's own directory. Traverse only, exactly as
        # ``Data`` is: an inheritable read/write grant here was the
        # first attempt and it is wrong, because it reaches the
        # write-ahead log as well as the shared index, and a writable
        # -wal is a writable database -- a frame's checksums are
        # computed from a salt the attacker can read. The suite
        # caught that within a minute, which is what it is for.
        #
        # So the sidecars get explicit entries instead, and the
        # installation creates them so there is something to put an
        # entry on. See :func:`sidecars` and WAL_SHM_NOTE.
        Rule(layout.db_dir,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full,
              "/grant:r", f"{WEB_SID}:(X,RA)"),
             "traverse only: the control centre reaches the database and "
             "cannot list what is beside it"),
        # And the one permanent file inside it, read-only. Explicit
        # entries only: an inherited allow and an explicit allow are a
        # union in Windows, so granting read here while the directory
        # grants read/write would leave the database writable. Cutting
        # inheritance on the file is what makes the narrower grant the
        # whole of it, without a deny entry.
        Rule(layout.db,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full,
              "/grant:r", f"{WEB_SID}:(R)"),
             "the control centre reads the database and cannot write it"),
        # The sidecars, now required, because the installation
        # creates them rather than hoping they are there.
        #
        # They were ``required=False`` and that is what broke the
        # boundary. SQLite deletes both on the last clean close, so
        # after an installation neither file existed, both rules were
        # skipped for want of a target, and the sidecars SQLite made
        # on the next open inherited a traverse-only entry. The
        # control centre could not open the database at all -- not on
        # a runner, and not on an owner's machine either. The probe
        # reported ``read_database`` refused, which is exactly what
        # it is for.
        Rule(layout.db + "-wal", ("/grant:r", f"{WEB_SID}:(R)"),
             "the write-ahead log is readable so a reader sees committed rows"),
        Rule(layout.db + "-shm", ("/grant:r", f"{WEB_SID}:(R,W)"),
             "SQLite's shared index; no reader can open a WAL database "
             "without it"),
        # The liveness signal. A file whose only content is the fact that it
        # was recently written, so the control centre may read it -- and has
        # to, because "is the runtime alive *now*" cannot be answered from a
        # database row the runtime wrote when it started.
        Rule(layout.heartbeat, ("/grant:r", f"{WEB_SID}:(R)"),
             "the control centre reads the runtime's liveness signal",
             required=False),
        # -- the drop box --------------------------------------------------
        Rule(layout.spool,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full,
              # WD: add a file. AD: add a subdirectory. X: traverse.
              # RA: stat the directory. Deliberately absent: RD (list), DC
              # (delete a file in it), D (delete it).
              "/grant:r", f"{WEB_SID}:(WD,AD,X,RA)",
              # Inherit-only, so it lands on each submitted file and not on
              # the directory: the creator owns what it wrote and gets to
              # read its attributes, which is all.
              "/grant:r", f"{OWNER_RIGHTS_SID}:(OI)(IO)(RA)"),
             "a drop box: add a request, and never read, change or remove one"),
        # -- code: readable and runnable, never writable -------------------
        #
        # ``/inheritance:r`` here, and it was missing. The grant alone
        # says what the control centre is *given*; it says nothing
        # about what the application directory already inherits from
        # whatever it was installed under. On a host whose install
        # root sits somewhere broadly writable the inherited entries
        # remain, and the probe proved it: ``write_application_code``
        # was expected to be denied and succeeded -- the control
        # centre could drop a file into the directory holding the
        # runtime's code.
        #
        # An install into ``C:\DeskPilot`` would not have shown this,
        # which is the point. A boundary that holds because of where
        # you installed is not a boundary; after this the grant is
        # the whole story, as it already was for Config, Engine and
        # Data.
        Rule(layout.app,
             ("/inheritance:r", "/grant:r", full, "/grant:r", admins_full,
              "/grant:r", f"{WEB_SID}:(OI)(CI)(RX)"),
             "the control centre runs DeskPilot's code and cannot alter it"),
        Rule(layout.venv,
             ("/grant:r", f"{WEB_SID}:(OI)(CI)(RX)"),
             "the control centre runs Python and cannot alter it"),
        # -- logs: it has to be able to say what happened ------------------
        Rule(layout.logs,
             ("/grant:r", f"{WEB_SID}:(OI)(CI)(M)"),
             "the control centre writes its own log"),
    )


def sidecars(layout: Layout) -> tuple[str, ...]:
    """The two files SQLite makes beside a WAL database.

    Created empty by the installation, before the access controls are
    applied, so that each can be given the entry it needs. A
    zero-length ``-wal`` is a valid empty write-ahead log and a
    zero-length ``-shm`` is initialised on first open, so creating
    them costs nothing and changes no SQLite behaviour.

    **The residual gap, stated rather than buried.** SQLite deletes
    both on the last clean close of the database. If the runtime
    stops cleanly and starts again, the files it recreates inherit
    the database directory's traverse-only entry and the control
    centre cannot read the database until the entries are applied
    again. ``Engine.apply_isolation`` does exactly that and is
    already the repair path; what does not yet exist is anything
    that calls it when the runtime restarts. That is named in
    docs/deskpilot-windows-certification.md as a remaining blocker
    rather than papered over here, because the alternative -- an
    inheritable read/write grant on the directory -- would make the
    write-ahead log writable, and a writable -wal is a writable
    database.
    """
    return (layout.db + "-wal", layout.db + "-shm")


#: Stated in the report rather than buried, because it is the one place the
#: boundary is weaker than the requirement.
WAL_SHM_NOTE = (
    "The database is in WAL mode, and SQLite cannot open a WAL database for "
    "reading without write access to its -shm shared index. The control centre "
    "is therefore granted read on solvent.db and solvent.db-wal, and read/write "
    "on solvent.db-shm. Writing -shm cannot add, change or remove a row: the "
    "data lives in the main file and the write-ahead log, and both are read-only "
    "to that identity. What a hostile write to -shm could do is confuse or stall "
    "other readers, which is a denial of service against the website itself. The "
    "probe proves solvent.db and solvent.db-wal are not writable."
)


def apply(host: Host, layout: Layout) -> tuple[list[str], list[str]]:
    """Apply every rule. Returns (what ran, what failed).

    A failure is returned rather than raised: the caller decides whether a
    missing sidecar matters, and the probe decides whether the result is a
    boundary. An installer that trusted this function's success would be
    making exactly the assumption this module exists to stop.
    """
    ran: list[str] = []
    failed: list[str] = []
    for rule in rules(layout):
        if not rule.required and not host.exists(rule.path):
            continue
        result = host.run(rule.argv(), timeout=60.0)
        ran.append(" ".join(rule.argv()))
        if not result.ok:
            failed.append(f"{rule.path}: {result.output.strip()[:160]}")
    return ran, failed


# ---------------------------------------------------------------------------
# Proving it, as the identity itself
# ---------------------------------------------------------------------------
#
# The only honest test of an access control is to hold the credential that is
# supposed to be refused and be refused. So the installer registers a scheduled
# task running as LOCAL SERVICE, runs this script under it, and reads back what
# happened. Nothing in the installer's own process can establish this: the
# installer is SYSTEM, and SYSTEM is allowed everything.

#: What the probe tries, as ``(name, expectation)``. ``DENIED`` means the
#: operation must fail; ``ALLOWED`` means it must succeed. Both directions are
#: checked, because a boundary that also broke the website would be reported as
#: a success by a test that only looked for refusals.
DENIED, ALLOWED = "DENIED", "ALLOWED"

PROBE_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("read_owner_key", DENIED,
     "the owner signing key is the one secret that must never be readable here"),
    ("read_web_password_hash", ALLOWED,
     "the control centre checks a typed password against this"),
    ("read_install_state", DENIED,
     "the installer's record of what it did is not the website's business"),
    ("read_database", ALLOWED,
     "every figure the owner sees is read from the database"),
    ("write_database", DENIED,
     "the control centre must not be able to change what it reports"),
    ("write_write_ahead_log", DENIED,
     "appending to the WAL would be adding committed rows by another name"),
    ("list_spool", DENIED,
     "listing submitted requests would disclose what the owner has asked for"),
    ("add_to_spool", ALLOWED,
     "the control centre's one way to ask the runtime for anything"),
    ("read_submitted_request", DENIED,
     "a request carries the reason the owner typed"),
    ("modify_submitted_request", DENIED,
     "changing a queued request after submitting it would change what is approved"),
    ("delete_submitted_request", DENIED,
     "deleting a pending request would discard an ask the owner had not seen"),
    ("read_own_submission", DENIED,
     "the creator owns the file it wrote; OWNER RIGHTS is what takes that back"),
    ("write_application_code", DENIED,
     "a website that can rewrite the runtime's code is the runtime"),
    ("read_engine", DENIED,
     "the installation engine can change the machine"),
    ("read_backups", DENIED,
     "a backup contains everything the database contains"),
)

PROBE_SOURCE = '''\
"""Try everything the control centre must not be able to do. Written by the
installer, run once as the control centre's own identity, then deleted.

Prints nothing and returns 0 whatever it finds: the result is the JSON file,
and a non-zero exit from a scheduled task tells the installer only that the
task failed. Each check records what actually happened, including the error,
so a refusal that turns out to be "file not found" is not read as a boundary.
"""

import json
import os
import sqlite3

OWNER_KEY_FILE = r"{env_file}"
WEB_ENV_FILE = r"{web_env_file}"
STATE_FILE = r"{state_file}"
DB = r"{db}"
SPOOL = r"{spool}"
APP = r"{app}"
ENGINE = r"{engine}"
BACKUPS = r"{backups}"
PLANTED = r"{planted}"
RESULT = r"{result}"


def attempt(fn):
    """(ok, detail). ok is True when the operation went through."""
    try:
        fn()
    except BaseException as exc:                     # noqa: BLE001
        return False, "{{}}: {{}}".format(type(exc).__name__, exc)[:200]
    return True, "succeeded"


def read_file(path):
    def go():
        with open(path, "rb") as handle:
            handle.read(16)
    return go


def main():
    results = {{}}

    results["read_owner_key"] = attempt(read_file(OWNER_KEY_FILE))
    results["read_web_password_hash"] = attempt(read_file(WEB_ENV_FILE))
    results["read_install_state"] = attempt(read_file(STATE_FILE))
    results["read_engine"] = attempt(
        lambda: os.listdir(ENGINE))
    results["read_backups"] = attempt(lambda: os.listdir(BACKUPS))

    def read_db():
        conn = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
        try:
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        finally:
            conn.close()
    results["read_database"] = attempt(read_db)

    def write_db():
        conn = sqlite3.connect(DB)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS probe_was_here (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
    results["write_database"] = attempt(write_db)

    def write_wal():
        with open(DB + "-wal", "ab") as handle:
            handle.write(b"\\x00")
    results["write_write_ahead_log"] = attempt(write_wal)

    results["list_spool"] = attempt(lambda: os.listdir(SPOOL))
    results["read_submitted_request"] = attempt(read_file(PLANTED))

    def modify_planted():
        with open(PLANTED, "r+b") as handle:
            handle.write(b"X")
    results["modify_submitted_request"] = attempt(modify_planted)
    results["delete_submitted_request"] = attempt(
        lambda: os.remove(PLANTED))

    mine = os.path.join(SPOOL, "probe-submission.intent")

    def add():
        with open(mine, "wb") as handle:
            handle.write(b'{{"verb": "probe"}}')
    results["add_to_spool"] = attempt(add)

    results["read_own_submission"] = attempt(read_file(mine))

    def write_code():
        with open(os.path.join(APP, "probe-was-here.txt"), "wb") as handle:
            handle.write(b"x")
    results["write_application_code"] = attempt(write_code)

    with open(RESULT, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
    return 0


if __name__ == "__main__":
    main()
'''


def probe_script(layout: Layout, *, planted: str, result: str) -> str:
    """The probe, with this installation's paths baked in."""
    return PROBE_SOURCE.format(
        env_file=layout.env_file, web_env_file=layout.web_env_file,
        state_file=layout.state_file, db=layout.db, spool=layout.spool,
        app=layout.app, engine=layout.engine, backups=layout.backups,
        planted=planted, result=result)


@dataclass(frozen=True)
class Finding:
    """One check, what was expected, and what the identity actually got."""

    name: str
    expected: str
    held: bool
    detail: str
    why: str


def judge(results: dict) -> tuple[list[Finding], list[str]]:
    """Compare the probe's findings against :data:`PROBE_CHECKS`.

    Returns every finding and the subset that failed. A check the probe did not
    report is a failure, not an omission: a probe that crashed halfway would
    otherwise read as a clean boundary for everything it never reached.
    """
    findings: list[Finding] = []
    for name, expected, why in PROBE_CHECKS:
        entry = results.get(name)
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            findings.append(Finding(name, expected, False,
                                    "the probe did not report this check", why))
            continue
        succeeded, detail = bool(entry[0]), str(entry[1])
        held = succeeded if expected == ALLOWED else not succeeded
        findings.append(Finding(name, expected, held, detail, why))
    return findings, [f.name for f in findings if not f.held]
