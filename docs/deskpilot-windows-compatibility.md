# DeskPilot — Windows source compatibility

Date: 2026-10-21 · Target: Windows 11 x64 · Audited from Linux x86_64,
CPython 3.11.17 · Branch `claude/solvent-architecture-audit-8ot9et`

A source audit, and nothing in it is a substitute for running on
Windows. Everything that needs a Windows machine is marked
**UNVERIFIED ON WINDOWS** in §4 and stays there until
`.github/workflows/windows-preinstall.yml` has run.

---

## 1. The defect this audit found

**Five state paths were drive-relative on Windows.** The database, the
heartbeat file, the owner-intent spool, the payment spool and the
backup directory all defaulted to `/var/lib/solvent/...` on both
platforms.

On Windows that is not an absolute path. `ntpath.splitdrive` returns
`("", "/var/lib/solvent/solvent.db")` — **no drive** — so it resolves
against whichever drive the process happens to be running from.
Reproduced:

```
POSIX   : isabs=True   /var/lib/solvent/owner-intents
Windows : isabs=True   splitdrive -> ('', '/var/lib/solvent/owner-intents')
ntpath.join(r'C:\DeskPilot', spool) -> 'C:/var/lib/solvent/owner-intents'
```

Two consequences, both silent:

* **The writer and the reader can disagree.** The control centre writes
  owner intents and the runtime reads them. Started from different
  drives, they use different spools and requests vanish.
* **The join footgun.** `ntpath.join(root, path)` discards `root`
  entirely when `path` begins with a separator, so composing a layout
  root with one of these produces a path nobody intended.

**Repaired.** All five now derive from one helper,
`solvent.types.default_state_path`:

| | POSIX (unchanged, byte for byte) | Windows |
|---|---|---|
| database | `/var/lib/solvent/solvent.db` | `%PROGRAMDATA%\DeskPilot\solvent.db` |
| heartbeat | `/var/lib/solvent/heartbeat` | `%PROGRAMDATA%\DeskPilot\heartbeat` |
| owner intents | `/var/lib/solvent/owner-intents` | `%PROGRAMDATA%\DeskPilot\owner-intents` |
| payment spool | `/var/lib/solvent/payments-inbox` | `%PROGRAMDATA%\DeskPilot\payments-inbox` |
| backups | `/var/lib/solvent/backups` | `%PROGRAMDATA%\DeskPilot\backups` |

One definition rather than five literals, because five literals drift
and a spool the writer and the reader disagree about loses requests
silently. `state_path_problem` **refuses** a drive-relative path rather
than normalising it: normalising would pick a drive on the owner's
behalf and cause exactly the divergence it exists to catch.

Severity is bounded by the installer, which already configured three of
the five explicitly (`--db`, `--heartbeat-path`,
`--owner-intent-spool`). An installed system never hit this. A
developer, or a control centre started by hand, would.

## 2. What the audit checked and found clean

| Area | Finding |
|---|---|
| POSIX-only calls | None. No `os.fork`, `os.getuid`, `SIGKILL`, `SIGHUP`, `AF_UNIX` |
| `shell=True` | Absent everywhere |
| `os.rename` | Absent — `os.replace` throughout, which is atomic on both |
| Hardcoded `/tmp`, `/bin/bash`, `/usr/bin/python` | None |
| Text IO | Every `open`/`read_text`/`write_text` in production and the installer declares an encoding, so Windows never falls back to the ANSI code page |
| SQLite | `busy_timeout` set in Python **and** as a PRAGMA, `journal_mode = WAL`, `synchronous = FULL`, `check_same_thread=False` |
| Network binding | `DEFAULT_HOST = "127.0.0.1"`; nothing binds `0.0.0.0` |
| Unicode and spaces in paths | A database opens under `Program Files/DeskPilot Ωmega/` |
| Installer path building | `ntpath` where the installer composes paths itself |
| Layout root | `C:\DeskPilot` — names a drive |

The one remaining POSIX path literal is `/proc/meminfo` in
`diagnostics.py`, and it is correct. Capacity treats a **missing**
memory reading as the ordinary Windows case and an **exception** as a
refusal: `capacity.py` says so in its own comment — "a Windows VPS
reports neither CPU nor memory through anything already installed" —
and `_memory_problem` returns no problem when the reading is absent
while `_unreadable` refuses when the host raises. Silence and failure
are distinguished, which is the distinction that makes it safe.

## 3. Enforced, not audited once

An audit read once goes stale. `tests_solvent/test_windows_compatibility.py`
turns each property above into a test — 19 tests, 87 subtests:

| Class | What it keeps true |
|---|---|
| `TheSpoolNamesADrive` | the Windows default names a drive; POSIX is unchanged; a drive-relative path is refused, not normalised; writer and reader share one definition |
| `NoPosixOnlyPathsInProduction` | no unlisted POSIX path literal, and the allowance list cannot go stale |
| `NoPosixOnlyCalls` | no unguarded POSIX-only call — a `hasattr` guard is accepted, because that is correct code |
| `TextIoDeclaresItsEncoding` | every text call names an encoding |
| `TheDatabaseSurvivesWindowsLocking` | busy timeout, WAL, and a path with spaces and non-ASCII |
| `TheControlCentreStaysOnLoopback` | loopback default; nothing binds the wildcard |
| `TheInstallerUsesWindowsPathSemantics` | ntpath where it builds paths; the layout root names a drive |

**Four false positives were found while calibrating these, and each
would have been a wrong finding reported as a defect:**

1. `os.path.join` in `integration.py` and `isolation.py` — those two
   emit scripts and probes that run **on the Windows machine**, where
   `os.path` *is* `ntpath`. Correct code.
2. `os.geteuid` in the installer's `host.py` — guarded by
   `hasattr(os, "geteuid")` inside a non-Windows branch. Correct code.
3. `0.0.0.0` in `web/exposure.py` — that module names the wildcard in
   order to **refuse** it. The opposite of a defect.
4. `write_text(json.dumps({...}), encoding="utf-8")` in `docwork.py` —
   a fixed 400-character window cut the call in half and missed the
   encoding at the end. The test now matches to the call's own closing
   bracket.

Recorded because a scanner that cries wolf gets switched off, and
because three of the four looked exactly like findings.

## 4. UNVERIFIED ON WINDOWS

Nothing below has been executed on Windows. No Linux result is offered
in its place.

* File-system permissions and ACL inheritance under `%PROGRAMDATA%`
* File locking and sharing violations, including whether an antivirus
  scanner holds the SQLite file open
* **Windows Defender and real-time scanning** interference with the
  venv, the `.pth` and the database
* **Firewall** behaviour on first bind to loopback
* Browser launching and whether the dashboard opens
* Process termination and restart, including the scheduled task
* **Laptop sleep and wake**, and what the heartbeat does across it
* Internet disconnect and restore
* Port already occupied
* Unicode and spaces in paths **as the filesystem handles them**, as
  opposed to as `ntpath` composes them
* SQLite WAL across a Windows restart
* Whether `%PROGRAMDATA%` is writable by the service account

`.github/workflows/windows-preinstall.yml` covers the suite, the
invariants, Python discovery against a real machine, and the
drive-letter assertion on `windows-latest` for CPython 3.11 and 3.13.
It cannot cover sleep, Defender or a real laptop.

## 5. Status

**WINDOWS SOURCE COMPATIBILITY: PASS** — one defect found, reproduced
and repaired; the rest clean and now enforced by tests.

**WINDOWS RUNTIME: UNVERIFIED.** Source compatibility is not runtime
compatibility, and this document does not claim it is.
