# Confining the Control Centre on Windows

**Status: implemented, and not verified on Windows.** Everything below is
built, exercised against a model of NTFS access control, and mutation-tested on
Linux. No part of it has run on a Windows machine. The installer tests the
boundary itself, on your computer, as the real identity, and refuses to report
success if it does not hold — that test is what settles this, and it has not
been run yet.

---

## 1. What was wrong

The design has always said that taking over the control centre does not take
over the business. Until now that sentence rested entirely on Python.

Both services ran as `S-1-5-18` — SYSTEM, full privilege, the same token. So:

* **The web process read the owner signing key on every boot.** The generated
  launcher opens its environment file, reads every line, and *then* filters by
  name:

  ```python
  name, _, value = line.partition("=")
  if ENV_ALLOW is not None and name not in ENV_ALLOW:
      continue
  ```

  One file served both services, and that file holds `SOLVENT_OWNER_KEY`. The
  key was in the address space of the one process a stranger could reach,
  excluded by an `if`.

* **The write-only spool was a class, not a permission.** `WriteOnlySpool` has
  no read method; the directory it writes to was readable, writable and
  deletable by that process.

* **The read-only database was a connection string.** `mode=ro` stops the code
  in `solvent/web` from writing. It does not stop the process.

Each of those is worth having. None of them is a boundary, because each is
enforced by the code an attacker would already be running.

## 2. What it is now

| | Runtime | Control centre |
|---|---|---|
| Identity | `S-1-5-18` (SYSTEM) | `S-1-5-19` (`NT AUTHORITY\LOCAL SERVICE`) |
| Run level | `HighestAvailable` | `LeastPrivilege` |
| Logon type | `ServiceAccount` | `ServiceAccount` |
| Environment file | `Config\solvent.env` | `Config\web.env` |
| Owner signing key | reads it | **ACL does not mention this identity** |

`LOCAL SERVICE` rather than a purpose-made local account, because a local
account needs a password for Task Scheduler to log on with — and a password the
installer generates has to be stored somewhere, which means inventing a new
secret in order to protect a secret. `LOCAL SERVICE` has no credential to
steal and is not a member of Administrators, which is the property being
bought.

### The access controls

Applied with `icacls`, naming every identity by SID. `SYSTEM:(R,W)` is a name
lookup, and a Windows installed in another language calls that account
something else — the grant fails, the installation refuses, and the machine was
perfectly capable of running DeskPilot. A VPS is exactly where a non-English
Windows turns up.

| Path | Control centre may | and may not |
|---|---|---|
| `Config\solvent.env` | *nothing* | read the owner key |
| `Config\web.env` | read | write |
| `Config\install-state.json` | *nothing* | — |
| `Data\solvent.db` | read | write |
| `Data\solvent.db-wal` | read | append |
| `Data\solvent.db-shm` | read, write | — (see §4) |
| `Data\spool` | add a file, traverse | list, delete a child |
| a submitted request | *nothing* | read, change, delete |
| `App`, `venv` | read, execute | write |
| `Engine`, `Backups` | *nothing* | — |
| `Logs` | modify | — |

### The drop box, and why it is one

Three of the requirements are about the same directory: the control centre must
not be able to read another request, change one it submitted, or delete a
pending one. The spool's DACL grants it `(WD,AD,X,RA)` — add a file, traverse,
read attributes. No `RD`, so it cannot list. No `DC`, so it cannot delete a
child.

That leaves the awkward case: a process that creates a file **owns** it, and an
owner implicitly holds `READ_CONTROL` and `WRITE_DAC`, so it could
re-permission its own submission and read it back. The answer is an
inherit-only ACE for `OWNER RIGHTS` (`S-1-3-4`) granting read-attributes and
nothing else. When that SID appears in a DACL it *replaces* the owner's
implicit rights. A test asserts the refusal, and another asserts that without
that one ACE the read succeeds — which is why it is there.

### One consequence worth knowing

`IntentWriter` used to write a temporary file and rename it, so a reader never
saw half an intent. It now writes under the final name in one call, because
`os.replace` needs DELETE permission on the source — and a process holding
DELETE in that directory can remove a *pending request* as easily as its own
partial. The permission was worth more than the atomicity.

So a reader can catch a partly written file. That is handled where it is read:
`consume_owner_intents` leaves an unparseable intent alone while it is newer
than ten seconds and refuses it once it is older. A truncated file is either a
write in progress or a broken request, and which one it is depends only on how
long it has been there.

## 3. Nothing here is believed

Applying `icacls` tells you the commands returned zero. It does not tell you
the control centre is refused anything.

So the installer writes a probe script, registers a scheduled task running as
`S-1-5-19`, runs the probe under it, and reads back what happened. Fifteen
checks, in both directions — eleven things that must be refused and four that
must work, because a boundary that also broke the control centre would be
reported as a success by anything that only looked for refusals.

A check the probe did not report counts as a failure, not an omission: a probe
that crashed halfway would otherwise read as a clean boundary for everything it
never reached. A probe that produces no result at all fails the installation
with "nothing was proved".

**A failure here stops the installation**, for the same reason a secrets file
that cannot be restricted stops it. Carrying on would register the control
centre under a reduced identity *without* the grants that identity needs — less
access than it requires and no boundary it could rely on, which is worse than
either alternative.

The probe task is deleted afterwards, along with the request it plants and its
own task definition. A test asserts all of that, because a leftover task
running as `LOCAL SERVICE` is not something to install on somebody's machine.

## 4. Where this is weaker than the requirement

**The `-shm` grant.** The database is in WAL mode, and SQLite cannot open a WAL
database for reading without write access to its `-shm` shared index. The
control centre therefore has read on `solvent.db` and `solvent.db-wal`, and
read/write on `solvent.db-shm`.

Writing `-shm` cannot add, change or remove a row: the data lives in the main
file and the write-ahead log, and both are read-only to that identity. What a
hostile write to `-shm` could do is confuse or stall other readers, which is a
denial of service against the website itself. The probe proves `solvent.db` and
`solvent.db-wal` are not writable.

**Audit records.** The requirement asks that the web process not read protected
audit records directly. It can: the audit log is a table in the database, and
the control centre reads the database because every figure it shows the owner
comes from there. What it cannot do is write one. Separating the two would mean
either a second database or a projection the runtime publishes, and both are
larger changes than this one — recorded here as not done rather than described
as done.

**Three rules are redundant on purpose.** `/inheritance:r` on `Data` and on the
spool each stop the drive's default `Users:(OI)(CI)(RX)` from reaching a
submitted request; either alone is enough. The `(X,RA)` traverse grant on
`Data` is belt and braces, because Windows gives Everyone
`SeChangeNotifyPrivilege` by default and that skips traverse checks on
intermediate directories. None of the three is mutation-tested, because
mutating a redundant control cannot change behaviour — it would be an invalid
mutant, not a survivor. Saying so is better than counting it as covered.

## 5. What a Linux test can and cannot establish

`tests_solvent/installer_acl_model.py` is a small reimplementation of the parts
of NTFS access control this depends on: explicit ACEs, `/inheritance:r`, `OI`,
`CI`, `IO`, the simple and specific rights the installer uses, the OWNER RIGHTS
substitution, and the group memberships that matter — including that
`LOCAL SERVICE` carries `BUILTIN\Users`, which is why `/inheritance:r` has to
come first.

A passing test means **the rules the installer applies imply the required
refusals under that model**. It does not mean Windows agrees.

The model exists because the alternative was worse. A FakeHost that answered
"denied" to whatever the probe asked would let the isolation step pass with no
rules applied at all — the same mistake an earlier FakeHost made with
`schtasks /Query`, which hid a control-centre service that had never been
registered. A model that can be wrong in the same places Windows can is more
use than a mock that cannot be wrong anywhere.

It was wrong twice while being written, and both corrections are instructive:
it denied by default, so removing a protection made the result *more*
restrictive and every negative control passed for the wrong reason; and it
returned the first `OWNER RIGHTS` entry rather than accumulating them, so a
second, more generous grant went unnoticed. Seven negative controls now remove
or over-grant one protection each and assert that the installation is refused
by name.

## 6. What is still to do on Windows

1. Run the installer on the VPS. The isolation step either passes its fifteen
   checks as `LOCAL SERVICE` or stops the installation and says which check
   failed.
2. Confirm the control centre answers on `127.0.0.1:8765` under the new
   identity. It binds a high port and reads a database, so it should need no
   privilege it no longer has — but "should" is the word doing the work.
3. Confirm the runtime still drains the spool. It runs as SYSTEM and the
   directory grants SYSTEM full control, so it should; same caveat.
4. Check the Windows event log for a logon failure on the control centre's
   task. `ServiceAccount` logon for `S-1-5-19` through Task Scheduler is the
   step most likely to behave differently from the documentation.

If step 1 fails, the installation stops and nothing is left half-confined —
that path is tested. If step 2 or 3 fails, the cause is a grant that is too
narrow rather than too wide, which is the safer direction to be wrong in and is
fixed by an entry in `isolation.rules`.
