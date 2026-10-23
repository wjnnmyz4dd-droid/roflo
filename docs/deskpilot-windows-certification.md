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

### Phase 4 — the second measurement, and what it found

Twenty-four became **eight on 3.13 (5,406 passed, 53 skipped, 29:04)
and a crash on 3.11** — a different crash from the one that was
retracted above, and a real one this time.

**3.11: the guard denied the test runner's own report.**
`platform.uname()` on Windows under CPython 3.11 shells out to read
the OS version and caches the answer for the life of the process.
3.12 reads the registry instead, so this is invisible on 3.12, 3.13
and every Linux. The suite installs the egress guard early, five
thousand tests later pytest's `--junitxml` writer calls
`platform.node()` from `pytest_sessionfinish`, the guard denied the
spawn — correctly — and the session died **after every test had run
and before the report was written**. No summary, no junit.xml,
nothing to certify, and every test had passed.

The repair is in `solvent/egress.py`: `install` fills that cache
immediately before adding the hook. The hook is unchanged and still
denies every spawn afterwards, which a test asserts; exempting
`platform` inside the hook would have been the hole, because an
exemption keyed on a stack frame is one anything can arrange. It is
also a product repair rather than a test one — any DeskPilot process
on 3.11 Windows that called `platform.release()` after the guard
armed would have got the same refusal.

**3.13: eight failures, seven of them in tests.**

| # | Failures | What it was | Product or test |
|---|---|---|---|
| 1 | 4 | **`self.addCleanup(lambda: None)`** in `test_bidder_console` — a cleanup that cleans nothing, beside the line that opens a `ReadModel`. The reader stayed open and the temporary directory could not be removed. WinError 32, in teardown, in four tests. | Test |
| 2 | 1 | **A deliberately corrupt database stayed open.** `conftest._release` swallowed the exception from closing a handle over a file that is not a database, so the handle — and the file — survived into the deletion. It now closes the connection underneath, which succeeds where the layer above cannot. | Test harness |
| 3 | 1 | **Python 3.13 strips indentation from docstrings at compile time.** A test removed a docstring with `source.replace(func.__doc__, "")`, which from 3.13 replaces nothing — so the test reported the docstring's own cautionary example as a defect in the code. Nothing to do with Windows; it would fail on 3.13 anywhere. The docstring is now removed by parsing. | Test |
| 4 | 1 | **`os.utime(..., follow_symlinks=False)` is not implemented on Windows.** The test's only guard was around `symlink_to`. It now asks `os.supports_follow_symlinks` and ages the target where it cannot age the link — the assertion that the target survives is what carries the security property, and that runs on both. | Test |
| 5 | 1 | **A platform-dependent assertion about a platform-specific document.** The owner manual is the *Linux* deployment guide — `systemctl`, `/etc/solvent/solvent.env` — and the test demanded it name `harness.PAYMENT_SPOOL`, which on Windows resolves to `C:\ProgramData\…`. It now names the platform, which also makes it stronger: it pins the document's content on every host instead of pinning nothing in particular on each. | Test |

Also repaired, and neither of them a test failure:

* **The per-file sweep could not find a failure.** Its inline
  PowerShell printed *"junit records no failing test"* over a report
  whose own summary said `failures="8"`, so the one run with failures
  to investigate produced no tracebacks. The reading now lives in
  `tools/junit_failing_files.py`, which eight tests drive over
  hand-written reports — it could previously be exercised nowhere but
  on a Windows runner that was already failing.
* **A green exit code was the only thing the suite step required.**
  It now requires four: a zero exit code, a junit summary, a
  plausible test count inside it, and no failures or errors. A
  partial run is not a certification, and until now a partial run
  that happened to exit zero would have read as one.

Again: nothing skipped, no assertion weakened, and the one skip that
exists — the libc bypass demonstration — says in its own message that
it is not evidence the hook is complete on Windows.

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


---

## Phase 9 — the whole thing, on Windows

Run 35, commit `c0a429d`. **Four jobs, four green.**

| Job | Result |
|---|---|
| Suite on Windows 3.11 | 5,492 passed, 53 skipped, 52:50. **junit: 16,469 cases, exit code 0** |
| Suite on Windows 3.13 | **junit: 16,473 cases, 53 skipped, exit code 0** |
| Install on Windows 3.11 | 17 steps 0 failed; 13/13 required checks; 12/12 restart checks |
| Install on Windows 3.13 | the same |

A complete junit summary and a zero exit code are both required, and
so are a plausible case count and no failures or errors — four
conditions, because a partial run that happened to exit zero would
otherwise have read as a certification.

### What the installation actually did

Seventeen steps, every one PASS, on a machine with no DeskPilot on
it:

```
[PASS] Installing Python runtime: reusing 3.14 at C:\hostedtoolcache\...\3.14.9
[PASS] Installing independent verification readers: 12 verified package(s),
       1382 file(s), into ...\App\vendor\site
[PASS] Creating persistent database: ...\Data\db\solvent.db
[PASS] Restricting the control centre: the control centre runs as LOCAL
       SERVICE; 15 access checks were run as that identity and every one
       behaved as required
[PASS] Configuring autostart: 2 services run at boot and restart on failure:
       DeskPilot as SYSTEM, DeskPilot Control Centre as LOCAL SERVICE
[PASS] Configuring backups: daily, newest 14 kept, secrets excluded
```

Then, read back from outside the installation by `python -I`, by
path, inheriting nothing:

* the five certified skills load — csv-cleanup, data-entry,
  document-processing, report-builder, spreadsheet-automation
* independent (T2) readers usable for `csv, docx, markdown, md, xlsx`
* the dashboard renders both connection cards, and **email and
  Stripe read NOT CONNECTED**
* **simulation on, live actions off**
* the scheduler registers ten tasks, runs all ten, and the Chief of
  Staff ticks
* the control centre answers on `http://127.0.0.1:8765`
* `Health: HEALTHY — governed, nothing outstanding`, `May start work:
  yes`
* the complete-job harness reaches `final state : COMPLETE` with
  `real revenue: $0.00`

**Restart recovery, 12/12.** Four separate interpreters against one
installation with a real close between each: a contract and a cleared
20% deposit written, read back, the same payment event delivered
again as a rail would, and read a third time. The deposit survived at
20000 with one payment row, not two; the stage is the one it reached
(`DEPOSIT_RECEIVED`); `work_authorised` is **recomputed from the
restored payments rather than restored**; and the next step is the
one it stopped at rather than an earlier one.

**Uninstall keeps the records.** `KEPT: ...\Data\db\solvent.db
(454656 bytes, was 454656)` — the byte count is compared across the
uninstall rather than the file merely tested for presence, because a
truncated file is also present.

---

## Phase 11 — the verdicts

Each line is what was measured, not what was configured.

| | |
|---|---|
| **WINDOWS PYTHON 3.11** | **PASS.** 16,469 cases, 53 skipped, exit code 0, complete junit summary |
| **WINDOWS PYTHON 3.13** | **PASS.** 16,473 cases, 53 skipped, exit code 0, complete junit summary |
| **T2 DEPENDENCIES** | **RESOLVED.** Twelve packages vendored, hash-pinned, every licence permissive; installed and usable for five formats on a real installation |
| **WINDOWS INSTALLER** | **BUILT.** `DeskPilot-Setup.exe`, 25,077,329 bytes, NSIS 3.11. **Compiled but not executed** — see the blocker below |
| **CLEAN INSTALLATION** | **PASS.** 17 steps, 0 failed, on both interpreters, driven through the engine the wizard drives |
| **APPLICATION STARTUP** | **PASS.** HEALTHY, may start work, control centre answering on loopback |
| **DASHBOARD** | **PASS.** Both connection cards render; email and Stripe NOT CONNECTED |
| **CHIEF OF STAFF** | **PASS.** Scheduler registers ten tasks and ticks the Chief of Staff on the installed tree |
| **FIVE SKILLS** | **PASS.** All five load in the installed environment |
| **END-TO-END WINDOWS SIMULATION** | **PASS, SIMULATED.** `final state : COMPLETE`, `real revenue: $0.00`. No money moved and no figure is revenue |
| **RESTART RECOVERY** | **PASS.** 12/12, four interpreters, real closes, replay not double-counted |
| **SECURITY** | **PASS.** 15 access checks run *as* LOCAL SERVICE, every one as required. Uninstall preserved Data, Config, Logs, Backups |
| **REGRESSION** | **PASS.** 5,495 passed, 0 failed, 50 skipped, 10,918 subtests on Linux |
| **MUTATION** | **PASS.** Installer 203/203 killed. Solvent 234/234 killed, 0 survived — after repairing one stale anchor the run itself exposed |

### Mutation: both batteries, and the one that was not guarding anything

**Installer: 203 mutants, 203 killed.**

**Solvent: 234 mutants, 234 killed, 0 survived** — but the first
full run reported *233 killed, 0 survived, 1 not applied*, and zero
survivors reads like a clean sheet when it is not one.

The unapplied mutant was
``invoice-recovery-reinvoices-what-is-already-billed``, which quotes
a line of ``solvent/ledger.py`` that the tiebreaker sweep had edited.
Its replacement matched zero times, so the control it guards — that
a restart does not write the client a second invoice for the same
job — had no mutant behind it. The harness reported it correctly and
quietly, at the end of a forty-minute run, one line in a wall of
KILLED. Repointed, and it kills.

``tools/mutate_installer.py`` has been guarded against exactly this
since three of its anchors went stale in one afternoon.
``tools/mutate_solvent.py`` was not, which is why this took a full
run to surface rather than a second.
``tests_solvent/test_mutant_anchors.py`` now holds **both**
batteries to the same rules: every anchor appears exactly once in
the file it names, no mutation is a no-op, and every mutant names a
test module that imports. Editing a quoted line fails it.

### Defects found and repaired

Thirteen, of which **six were in the verification rather than the
product** — and that ratio is the finding of this assignment.

| # | Defect | Where |
|---|---|---|
| 1 | `icacls` ran before `mkdir`: the spool was hardened before anything created it, and the installation stopped there | Product |
| 2 | The install checker read each row for `ok`; the engine writes `outcome`. It printed "0 failed" over a report whose last row said FAIL. **Two CI runs were read as clean installations on that line** | Verification |
| 3 | Two workflow steps proved a database the installation never creates, having spelled the path by hand | Verification |
| 4 | `choose` took the newest Python; the payload serves cp311–cp314 and the runner had 3.15. The readers were installed for an interpreter nobody had | Product |
| 5 | `<LogonType>ServiceAccount</LogonType>` is in the COM enum and **not in the XML schema**, so every task definition this installer ever wrote was invalid. The test asserted the defect | Product + verification |
| 6 | The task XML declared UTF-16 and every caller wrote UTF-8; the tests decoded UTF-8 too, so both halves agreed with each other and neither with Windows | Product + verification |
| 7 | The hardening denied the control centre's identity the engine directory, and the probe wrote its own script there | Product |
| 8 | SQLite deletes the WAL sidecars on a clean close, so their rules were skipped and the control centre could not open the database at all | Product |
| 9 | The application directory never cut inheritance, so the control centre could write beside the runtime's code | Product |
| 10 | `platform.uname()` shells out on 3.11 Windows; the guard denied it and pytest's junit writer died after every test had passed | Product |
| 11 | **Fifty-one time orderings with no deterministic tiebreaker.** On a 15.6 ms clock an evidence authority decided "the latest status" by coin toss | Product |
| 12 | The per-file sweep printed "junit records no failing test" over a report whose summary said `failures="8"` | Verification |
| 13 | A green exit code was the only thing the suite step required | Verification |

Four more were mine, introduced during this work and caught here
rather than shipped: tests that spawned subprocesses inside a suite
that forbids spawning; a test that cleared `platform._uname_cache`
and so created the very condition it was written to prevent; a byte
search for an NSIS constant that sits inside an LZMA block; and a
bare `rowid` in a join, which is not a wrong order but a failed
query — 102 tests at once.

### Remaining blockers

**1. The WAL sidecars do not stay hardened across a runtime restart.**
SQLite deletes `solvent.db-wal` and `solvent.db-shm` on the last
clean close and recreates them on the next open, inheriting the
database directory's traverse-only entry. The installation creates
them and grants on them, so a fresh installation is correct and was
measured correct; a runtime that stops and starts cleanly leaves the
control centre unable to read the database until the rules are
applied again.

`Engine.apply_isolation` already does exactly that and is already the
repair path. What does not exist is anything that calls it when the
runtime restarts. The obvious alternative — an inheritable read/write
grant on the database's directory — is **rejected**, because it
reaches the write-ahead log as well as the shared index, and a
writable `-wal` is a writable database: a frame's checksums are
computed from a salt the attacker can read. Smallest safe fix: have
the runtime re-apply the two sidecar rules at startup. Not done here,
because it adds a hook to the runtime and this assignment was told
not to.

**2. `DeskPilot-Setup.exe` has been compiled and never executed.**
The payload, the engine and every installation phase are tested on
real Windows, and the wizard is the same engine behind a UI. But no
run of this workflow has double-clicked the `.exe`, so the NSIS
pages, the elevation prompt and the silent-install switches are
**unverified**. A GitHub runner can install the engine; it cannot
answer a wizard.

**3. Not certified beyond 3.11 and 3.13.** The payload carries wheel
sets for cp311–cp314, and cp315 cannot be added: `clevercsv`
publishes no cp315 wheel for any release. The chooser now prefers an
interpreter the payload can serve and says on the system-check screen
when it cannot.
