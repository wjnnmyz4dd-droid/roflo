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
wheel per interpreter, so the payload carries four sets — `cp311`,
`cp312`, `cp313`, `cp314` — of twelve wheels each.

**Which four, and why not five.** This went wrong twice and the
second time is the one that mattered.

`python_runtime.choose` returned the newest usable Python on the
machine. A GitHub Windows runner has the interpreter the job set up
*and* whatever else is in the hosted tool cache, and in October 2026
that includes 3.15.1. So the installation chose 3.15, found no
`cp315` wheel set, and finished with **no independent readers at
all** — on the machine with the most Python available on it. The
first time this happened the newest was 3.14 and the answer was to
add a fourth wheel set; that answer loses again every October.

It cannot be taken a second time in any case: **`clevercsv`
publishes no `cp315` wheel, for any release of itself.** A payload
cannot promise a set for a CPython its readers have not been built
for yet. So the repair is on the other side — `choose` now prefers
an interpreter the payload can serve, and falls back to an unserved
newer one only when it is the only usable Python on the machine.

`vendor.SERVED_MINORS` is the one list this is written in.
`build.py` fetches exactly those, the manifest pins one hash per
package per set, and `choose` prefers them; three tests in
`test_installer_vendored_readers.py` fail if any of the three drifts
from the others, and `MINORS` — the *search* list — deliberately
still includes 3.15, because finding a Python and preferring it are
different jobs.

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

### What the real installation found

Four defects, and three of them were in the verification rather than
in the product. That ratio is the finding.

**1. `icacls` ran before `mkdir`, and took the installation with
it.** `Restricting the control centre` applies a required ACL rule to
the request spool. Nothing created the spool: the runtime makes it on
first write, which is long after an installation ends. On the runner
the step failed with *"The system cannot find the file specified"*,
the installation stopped there — correctly, fail-closed, with a
complete change ledger — and the autostart tasks registered *after*
it were therefore never created. So the verify phase then reported
`[FAIL] Autostart: missing: DeskPilot, DeskPilot Control Centre`, and
a missing `mkdir` read on screen as a broken security boundary and a
broken autostart. The spool is now in `Layout.all_dirs`, and a test
asserts that **every** required hardening rule targets something the
installation creates, not just this one.

**2. The install checker's broadest check could not fail.** It read
each row of the engine's report for a key named `ok`; the engine
writes `outcome`. `s.get("ok") is False` was never true for any
report the engine has ever produced, so the line printed

```
PASS  every installation step reported success: 14 step(s), 0 failed
```

over a report whose last row read `FAIL`, which carried a whole
`failure` block, and whose engine had exited 1. **I read two CI runs
as clean installations on the strength of that line.** Both were
failures. The checker now reads the engine's own `Outcome`
vocabulary, imported from the engine rather than spelled again; reads
`may_continue` and `failure` as well, because a report can stop
before it writes the row that says why; and reports a row it cannot
grade as a finding in its own right. `tests_solvent/test_windows_checkers.py`
drives it as a subprocess over synthetic reports — the checkers had
no tests at all, which is how a control that cannot fire survived.

**3. Two workflow steps proved the wrong database.** They spelled the
path `data\deskpilot.db`; the installation writes `Data\solvent.db`.
Windows is case-insensitive about the directory and not about the
filename, so those steps quietly *created* a second, empty database
and then proved that it survived the uninstall. The path now comes
from `Layout`, the step fails if the installation created no database
there, and the uninstall check compares the byte count across the
uninstall instead of only testing for presence.

**4. No `DeskPilot-Setup.exe` was being built.** The hosted Windows
image no longer ships NSIS, and the step said so and carried on. The
workflow now installs NSIS 3.11 through Chocolatey — a build-machine
tool, pinned and checksummed by its own package manager, in the same
category as the `pip download` in `build.py`, with no byte of it
reaching an owner's machine or the payload. The installer's "no
downloads during an installation" rule is untouched and is still
asserted on every run. The compile is followed by two assertions:
the output is a PE executable, and it carries the SHA-256 of the
payload it refuses to install without.

---

## Phase 8 — installation security

Vendoring is the obvious place to reintroduce exactly what the
installer exists to avoid: a dependency resolved on the owner's
machine, from a source nobody pinned, by a step that reaches the
network while running as Administrator. Eight tests assert it did
not.

| Phase 8 asks | Evidence |
|---|---|
| No hardcoded secrets | the manifest carries no `password`, `token`, `api_key`, `secret` or `authorization`; `connect_business_email` has no parameter that could hold one |
| No credentials in logs | unchanged; the install log never contains the password, owner key or any API key |
| **No uncontrolled dependency downloads** | `vendor.py` imports no `urllib`, `http`, `socket`, `subprocess` or `ssl`; no module in the package imports `requests`, `httpx` or `pip`; `pip` appears only in `build.py`, only with `--require-hashes`, and `build.py` re-hashes every file itself |
| No unauthorized outbound communication | the egress audit hook is process-wide and unchanged; an installation downloads exactly one thing, a Python runtime, and only when the machine has none |
| No automatic live business activity | simulation on and `live_actions_enabled` false, asserted on the installed tree |
| No Governor bypass | the vendored packages are readers; a test asserts none of them is `solvent`, `governor`, `gate`, `ledger` or `payments` |
| No Action Gate bypass | likewise |
| No duplicate payment system | unchanged — one rail ladder, in `policy.py` |
| Installer integrity | payload SHA-256 compiled into the installer's code section; wheels hashed at build and again at install; extraction all-or-nothing |
| Local file permissions | `isolation.py` unchanged. **On a CI runner the ACLs cannot be applied and the step says so** rather than reporting success |

The ZIP-escape checks are applied to each wheel as well as to the
payload — including the backslash case, which passes every POSIX
check and still escapes on Windows. A verified wheel is the right
bytes; it is not automatically a safe archive.

---

## Phase 10 — advisory reviews

**iFixAi: SOURCE-DERIVED.** Unchanged from the Chief of Staff
certification and for the same reason. The tool evaluates LLM agents
through a provider; DeskPilot's certified skills call no model, the
egress hook denies `socket.connect`, and there is no endpoint. The
judge-free run returns `*** RUN INVALID — measurement failure, not a
result ***` in the tool's own words, and the assignment is explicit
that native execution may not be claimed on that. Its catalogue
applied to this work:

| Inspection | The Windows repairs, checked against it |
|---|---|
| B01 tool governance | no new path to an external effect; `vendor.py` imports nothing that can reach one |
| B02 non-LLM governance | every repair is deterministic — a hash comparison, a path join, a platform test |
| B03 auditability | the install log and the engine's JSON report name every step and every refusal |
| B05 source provenance | the readers are pinned by SHA-256 to a manifest that names each licence |
| B08 privilege escalation | the installer gained no new privilege; the ACL step still fails loudly when it cannot apply them |
| B16 silent failure | the defect found this round was precisely a silent one — "this payload carries none" with no path — and every such branch now names what it looked at |
| B23 policy version traceability | unchanged |
| X11 pre-action confirmation | unchanged; connecting an account still enables nothing |

**K-LEAN: SOURCE-DERIVED.** Archived repository, needs four model
providers. Its reachability discipline, re-run over both trees:

* `windows/installer/deskpilot_installer`: **17 modules, no orphans.**
  `vendor.py` is reached by `engine.py`, `step_vendor` is in the step
  list, and both new tools are invoked by the workflow.
* `solvent/`: the same six pre-existing orphans as last time —
  `prospecting`, `revenuemodel`, `skillcandidates`, `upworkbid`,
  `inference`, `marketplaces` — plus the CLI entry points, which are
  reached from the console script rather than by import. **Nothing
  new was orphaned by this work.**

Checked for what Phase 10 names: no security regression (the table
above), no duplicate authority (the readers decide nothing), no
dependency conflict (every package pinned to one version, two wheel
sets that never share a directory), no recovery failure (12/12 on
the real installation), and one installer weakness found and
repaired — the step that looked in the wrong directory and said so
without naming it.
