# DeskPilot on Windows — repair, certification and installation

**Repository** `wjnnmyz4dd-droid/roflo`
**Branch** `claude/solvent-architecture-audit-8ot9et`
**Windows available to this work** GitHub Actions `windows-latest`,
Python 3.11.9 and 3.13.16, x64. No physical Windows machine and no
self-hosted runner. Everything claimed about Windows below was
executed there; nothing was inferred from Linux.

---

## Phase 1 — the environment, verified

| | |
|---|---|
| Session platform | Linux 6.18.44, Python 3.11.17, cloud container |
| Real Windows computer | **No** |
| Self-hosted Windows runner | **No** |
| Windows CI | **Yes** — `.github/workflows/windows-preinstall.yml`, `windows-latest` |
| Installer implementation | `windows/installer/` — NSIS wizard, a Python engine, a certified payload |

This is case C of the three the assignment names, so GitHub Actions
is the only real Windows in reach, and it is used for every Windows
claim here.

---

## Phase 2 and 3 — what was actually wrong

The previous report said eight test files failed individually and the
full suite stopped without a summary. Both halves of that turned out
to be the measuring apparatus rather than the system.

### The suite never crashed. PowerShell was eating the output.

Two earlier attempts stopped at 60–65% with no pytest summary — **and
also lost the step's own `Write-Output` of the exit code afterwards**.
A shell that loses its own echo after the command it ran is a shell
whose buffer went with it, not a dead interpreter.

Running the suite through `cmd.exe`, whose `>` is an OS-level handle
on the child process with nothing in between, produced a complete run
with a summary and an exit code:

```
24 failed, 5362 passed, 47 skipped, 22 warnings, 10670 subtests passed in 34:28
```

The same job demonstrated the loss again in a different step: a
PowerShell loop printed six `NONZERO` lines and then nothing, though
it ran for a further eight minutes and exited non-zero — so at least
one more failing file's line was lost, and so was every traceback the
step was written to print. Every diagnostic step now writes to a file
that is uploaded, and `--junitxml` records each test as it finishes,
so the answer no longer depends on a console surviving.

The earlier hypothesis — "a deterministic stop with no Python-level
error is the interpreter dying" — was reasonable from what was
visible and is **wrong**. It is retracted here and in
`tests_solvent/conftest.py`, where it was also written down.

One honest caveat: the shell changed from pwsh to cmd *and* the
encoding repair removed failures whose output was being written
around the point the run used to stop. Two things moved together. The
runs since have been stable.

### The twenty-four failures, and what each one was

| # | Failures | What it was | Product or test |
|---|---|---|---|
| 1 | 5 | **CRLF on checkout.** Both sealed exams hash source with `read_bytes`, and Git's Windows default `core.autocrlf=true` gives every file CRLF, so twelve modules read as "changed after the freeze" when nothing changed. | Repository |
| 2 | 1 | **A CSV fixture corrupting its own input.** `EMBEDDED_NEWLINE` carries a line break inside a quoted field — a two-line address — and the fixture wrote it with `write_text`, so Windows turned it into CRLF before DeskPilot saw the file. | Test |
| 3 | 4 | **Windows ships no IANA timezone database.** `ZoneInfo("America/New_York")` raises, `briefing` falls back to UTC, correctly and visibly, and the owner's 06:50 summary still arrives at some other hour. | Product |
| 4 | 6 | **A Windows-only bug in the fixture written to prevent Windows bugs.** `conftest` matched handle paths with `startswith`; Windows paths are case-insensitive and `abspath` does not normalise case, so databases under `C:\Users\…` were not closed and cleanup failed with WinError 32. | Test harness |
| 5 | 1 | **`os.kill(pid, 0)` does not raise `ProcessLookupError` on Windows.** It raises `OSError` with `winerror 87`, so a mutation claim left by a dead run raised out of `active_claim` instead of reading as stale. | Tooling |
| 6 | 3 | **Tests asserting POSIX mechanics.** `socket.AF_UNIX` does not exist on Windows; `ctypes.CDLL(None)` raises `TypeError` there. | Test |
| 7 | 2 | **Tests measuring the environment.** One asserted the clone points at `.githooks`, which a fresh CI checkout never does; one asserted `DEFAULT_DB` starts with `/var/lib/solvent/`, contradicting the Windows test that asserts it names a drive. | Test |
| 8 | 2 | Knock-on failures of #3 in the same file. | Product |

**Three of the twenty-four were product issues and one of those was
real**: Windows has no tz database. The CSV one *looked* like
DeskPilot mangling a client's address and was not — `csvwork`,
`csvverify`, `entrywork` and `independent` all open with
`newline=""`, the contract the `csv` module documents, and have had
it right all along.

### The repairs

* **`.gitattributes`** pins the working tree to LF on every platform.
  Proved directly rather than assumed: cloning this repository with
  `core.autocrlf=true` — Git for Windows' default — now yields no
  CRLF in any sealed file and all eight digests match the repository.
* **Four CSV fixture writers** use `newline=""`, so a fixture writes
  the bytes it says it writes.
* **`tzdata` ships with the installer** (Apache-2.0, 348 KB, pure
  Python). `zoneinfo` reads it automatically when the system has
  none, so carrying it is the whole repair. The CI job installs the
  same package, because measuring a host no installed DeskPilot will
  ever be is not measuring anything.
* **`conftest` normalises case** on both sides of the comparison;
  `normcase` is the identity on POSIX.
* **`mutation_guard._alive` handles `winerror 87`** as "no such
  process". Any other `OSError` now reads as *alive*, which is the
  conservative direction: the expensive mistake is deciding a live
  mutation run is dead.
* **The POSIX-only tests run the same control by another route** —
  TCP to the discard port where there is no `AF_UNIX`. The libc
  bypass demonstration skips on Windows and says in the skip that
  this is **not** "the hook is complete on Windows": a Windows
  process reaches the socket API through `ws2_32.dll` by the same
  reasoning, and the host-firewall requirement is unchanged and
  still asserted on both platforms.
* **The two environment tests** assert the contract rather than one
  platform's literal.

Nothing was skipped to make a number go green, no assertion was
weakened, and no ordinary test failure was reclassified as a crash —
the reclassification went the other way.

---

## Phase 5 — the T2 reader dependencies

**Resolved, and the licence blocker was gone before I got there.**

Every report since this came up has carried "LGPL `chardet` via
`clevercsv`" as an owner decision. chardet relicensed to **0BSD** at
6.0.0. At the pinned 7.7.0 the whole set is permissive:

| Package | Version | Licence | Shape |
|---|---|---|---|
| openpyxl | 3.1.5 | MIT | pure |
| python-docx | 1.1.2 | MIT | pure |
| markdown-it-py | 3.0.0 | MIT | pure |
| mdurl | 0.1.2 | MIT | pure |
| et-xmlfile | 2.0.0 | MIT | pure |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pure |
| typing-extensions | 4.16.0 | PSF-2.0 | pure |
| tzdata | 2026.5 | Apache-2.0 | pure |
| chardet | 7.7.0 | **0BSD** | compiled |
| clevercsv | 0.8.5 | MIT | compiled |
| lxml | 6.1.3 | BSD-3-Clause | compiled |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | compiled |

**Vendored, as the assignment prefers.** The manifest
`windows/installer/t2-windows-x64.txt` is a hash-pinned pip
requirements file. Four packages are compiled and therefore need one
wheel per interpreter, so the payload carries a `cp311` set and a
`cp313` set, 6.3 MB each.

The security model is unchanged and the chain has two independent
checks:

1. **At build time**, `pip download --require-hashes` refuses any
   file whose SHA-256 is not in the manifest, and `build.py` then
   re-hashes every downloaded file itself rather than taking pip's
   word. This is the only step in the whole installation story that
   reaches the network.
2. **At install time**, `step_vendor` hashes each wheel again against
   the manifest shipped beside them, checks every ZIP entry for the
   five ways out of a destination directory — including the backslash
   one, which passes every POSIX check and still escapes on Windows —
   and unpacks all-or-nothing.

There is no pip step in an installation and nothing is downloaded on
the owner's machine. The wheels are not committed to the repository:
12.6 MB of binaries under version control would make every upgrade a
binary diff.

A missing or partial reader set is reported and never fatal.
DeskPilot runs without them and declines a job needing a second
opinion while it is still a quote — an installer that refused to
install a working product over an optional one would be the worse
failure.

Three of the eleven are exercised for real on Linux: the pure-Python
wheels are fetched against the manifest's own hashes, unpacked
through the production code path, imported out of the result, and
openpyxl reads back a workbook containing an omega.

---

## Phase 6 and 7 — the installation

The installer already existed. What this work added is the vendored
readers and a Windows job that installs for real and reads what was
left.

**Nothing in that job is a fake host.** `build.py` produces the
payload, the engine is driven through its own CLI with the phases
`DeskPilot-Setup.exe` drives, and the filesystem is the runner's.

`tools/windows_install_check.py` then reads the result the way an
owner's next login would — by path, with `python -I`, inheriting
nothing from the process that created it:

1. the application is on disk
2. the Python environment exists
3. the data directory exists
4. DeskPilot imports in its own environment
5. the five certified skills load
6. the database was created
7. the dashboard renders both connection cards
8. email and Stripe read NOT CONNECTED
9. simulation is on and live actions are off
10. the scheduler registers ten tasks and ticks

plus the independent readers, reported and optional.

`tools/windows_restart_check.py` covers the restart requirement with
four separate interpreters against one installation and a real close
between each: write a prospect, a signed contract and a cleared 20%
deposit; read them back; deliver the same payment event again as a
rail does; read again and check nothing was duplicated by being
looked at. Twelve checks, including that `work_authorised` is
recomputed from the restored payments rather than restored, and that
the next step is the one it stopped at rather than an earlier one.

Uninstall keeps the owner's records unless they explicitly ask
otherwise, and the engine proves it by checking the kept paths
*after* the deletions rather than restating the promise. The job
fails if the database is gone.
