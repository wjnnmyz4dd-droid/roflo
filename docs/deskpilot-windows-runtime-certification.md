# DeskPilot — Windows runtime certification

Date: 2026-10-21 · Target: Windows 11 x64 · Branch
`claude/solvent-architecture-audit-8ot9et`

**WINDOWS EXECUTION: NOT PERFORMED.**
**WINDOWS CERTIFICATION: PENDING.**

No Windows 11 machine, no Windows virtual machine and no Windows CI
runner was available to this session, which runs in a Linux container.
Nothing in this document is a Windows result, and no Linux result is
offered in place of one.

---

## 1. What was traced, and what that is worth

The startup path was read, not run on Windows:

| Step | Traced | Status |
|---|---|---|
| 1. DeskPilot starts | `Runtime.start` → `startup_checks` | source-traced |
| 2. Backend initializes | `Solvent` construction, authorities wired | source-traced |
| 3. Database opens | `Store.__init__`, WAL, `busy_timeout`, migrations | source-traced, exercised on Linux |
| 4. Recovery executes | `Runtime.start` calls `recover_after_restart` | exercised on Linux by the crash matrix |
| 5. Five skill pipelines initialize | per-skill capacity pools | exercised on Linux |
| 6. Website server starts | `solvent.web.server`, `DEFAULT_HOST = 127.0.0.1` | source-traced |
| 7. Local browser connects | — | **UNVERIFIED ON WINDOWS** |
| 8. Dashboard loads | — | **UNVERIFIED ON WINDOWS** |
| 9. Status endpoints respond | exercised on Linux | **UNVERIFIED ON WINDOWS** |
| 10. Logs generated | `installlog`, append-only, secret-scrubbed | source-traced |

Step 4 is worth a note: recovery runs at start because it was wired in
during an earlier round, having previously existed and never been
called. That is the same failure class as §3 of the system audit.

## 2. UNVERIFIED ON WINDOWS — the full list

Every case the brief names under §7, none of which has been executed
on Windows:

First launch · second launch · restart · interrupted startup ·
database already exists · port already occupied · missing
configuration · invalid configuration · backend crash · browser closed
· **laptop sleep** · **laptop wake** · internet disconnected ·
internet restored.

And from the compatibility audit: filesystem permissions and ACL
inheritance under `%PROGRAMDATA%`, file locking and sharing
violations, **Windows Defender** and real-time scanning against the
venv and the database, **firewall** behaviour on first loopback bind,
browser launching, process termination, scheduled-task restart, and
SQLite WAL across a Windows restart.

## 3. The one thing that would be true on any OS

Even on a Windows machine where every item above passed, the installed
service would **not do a job**: `Runtime` schedules eight timers and
none runs the business cycle. See `deskpilot-preinstall-system-audit.md`
§3. A Windows runtime certification that confirmed a healthy, idle
DeskPilot would be accurate and almost worthless, so that finding is
repeated here rather than left in another document.

## 4. What closes this

`.github/workflows/windows-preinstall.yml`, added in this round, runs
on `windows-latest` for CPython 3.11 and 3.13 and covers: the full
suite, the Windows invariant tests, the installer's bootstrap and
discovery suites, an assertion that all four state paths resolve to a
drive letter, Python discovery against the real machine via
`search_report`, and the loopback default.

It has **never been run**. It is not evidence; it is the means of
getting some.

What it still cannot cover: laptop sleep and wake, Defender on a real
desktop, a real browser, a real restart of a real scheduled task, and
uninstallation. Those need the Windows 11 test machine the brief lists
first.

## 5. Status

| | |
|---|---|
| WINDOWS RUNTIME | **UNVERIFIED** |
| WEBSITE STARTUP | **UNVERIFIED** |
| WINDOWS EXECUTION | **NOT PERFORMED** |
| WINDOWS CERTIFICATION | **PENDING** |
