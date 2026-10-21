# DeskPilot — pre-installation system audit

Date: 2026-10-21 · Target: Windows 11 x64 · Audited from Linux x86_64,
CPython 3.11.17 · Branch `claude/solvent-architecture-audit-8ot9et`

Twenty components, traced from the real entry points rather than
listed from the repository. The instruction that shaped this audit is
the one worth repeating: *a function existing in the repository does
not mean the application actually calls it.*

---

## 1. Repository state

| | |
|---|---|
| Repository | `wjnnmyz4dd-droid/roflo` |
| Branch | `claude/solvent-architecture-audit-8ot9et` |
| Working tree | clean at audit start |
| Test suite | 174 test modules |
| Interpreter | CPython 3.11.17, Linux x86_64 |

Previous certifications were **not** assumed sufficient; every control
cited below was re-run for this audit.

## 2. Reachability

Import reachability was walked outward from the four real entry points
— `solvent.runtime`, `solvent.cli`, `solvent.harness`,
`solvent.web.server` — reaching **76 modules**.

Import reachability is a weaker claim than call reachability: an
imported module can still hold functions nobody calls. It is strong in
the negative direction, which is the one that matters here — a module
nothing imports is certainly not running.

| # | Component | Module | Reached |
|---|---|---|---|
| 1 | Finder | `solvent.finder` | yes |
| 2 | Brain — memory | `solvent.memory` | yes |
| 2 | Brain — inference | `solvent.inference` | **no** |
| 3 | Governor | `solvent.governor` | yes |
| 4 | Action Gate | `solvent.gate` | yes |
| 5 | Job admission | `solvent.qualification` | yes |
| 6 | Capacity | `solvent.capacity` | yes |
| 7 | Worker execution — bounded loop | `solvent.operation` | **no** |
| 8 | Independent verification | `solvent.independent` | yes |
| 9 | Owner approval | `solvent.owner` | yes |
| 10 | Contract management | `solvent.application` | yes |
| 11 | Delivery authorization | `solvent.submission` | yes |
| 12 | Invoice generation | `solvent.ledger` | yes |
| 13 | Payment reconciliation | `solvent.paymentmonitor` | yes |
| 14 | Recovery | `solvent.resilience` | yes |
| 15 | Persistent storage | `solvent.store` | yes |
| 16 | Audit journal | `solvent.audit` | yes |
| 17 | Website frontend | `solvent.web.render` | yes |
| 18 | Website backend | `solvent.web.app` | yes |
| 19 | Notification system | `solvent.notify` | yes |
| 20 | Integration interfaces | `solvent.marketplaces` | **no** |

## 3. The finding: the installed service would never do a job

`Runtime.default_tasks` registers **eight** timers:

```
heartbeat  health  reconciliation  discovery
diagnostics  owner_intents  owner_briefing  supervision
```

**None of them runs the business cycle.** `run_business_cycle` is
reachable from exactly two places:

* `solvent/cli.py` — the one-shot `solvent cycle` command, run by hand;
* `solvent/operation.py` — the bounded operating loop, which nothing
  imports.

`operation.py` is not a stub. Its docstring describes precisely what is
missing: *"Limits the owner set, checked before each job rather than
after: how many jobs per run, how much may be committed in total, how
long the run may last, and how many consecutive failures end it. A
reason for stopping, always recorded. A halt that is checked every
iteration."* It calls `run_business_cycle`, adds bounds around it, and
sits unimported.

**So an installed DeskPilot would start, write its heartbeat,
reconcile, run discovery, brief the owner, pass every health check —
and never execute a job.** It is not broken. It is inert, and inert in
a way that looks healthy from every monitoring surface it has.

**Not repaired, deliberately.** Wiring the loop in is the difference
between a machine that waits to be told and one that goes looking for
work. The standing instruction for this round is not to deploy, not to
go live, and not to install on the owner's laptop; switching on
autonomous operation unilaterally would be the wrong reading of that.

It is **pinned** instead, by `tests_solvent/test_service_does_the_work.py`:
the eight task names are asserted exactly, and a test fails the moment
`run_business_cycle` appears in `runtime.py`. The gap cannot close — or
widen — without somebody saying so in writing.

This is the fifth instance in this engagement of correct machinery that
nothing calls. `supervised()`, `Capacity.reserve`,
`skillslab.record_need` and `recover_after_restart` were the earlier
four; the Finder's profitability triage is a sixth, reachable only from
a CLI readiness report.

## 4. The other two unreached modules are dormant on purpose

Both say so in their own first lines, and both were checked rather
than assumed:

* **`solvent.marketplaces`** — *"One adapter so far, and it does not
  run yet. Both facts are the point."* Of six platforms, five have no
  permitted machine route; the sixth has a verified route whose terms
  nobody has read, so discovery refuses the source until the owner
  records a determination. Dormant and documented.
* **`solvent.inference`** — advisory routing only. *"The five
  commercial skills call no model at all."* The work it would route —
  summarising an ambiguity, drafting a reply — currently stops and
  waits for a person, which is the safe state. Unwired means those
  things keep waiting for a person.

Neither is a defect. Both are recorded so that "unreached" is not read
as "broken".

## 5. Per-component verification

For each reached component: source present, dependencies stdlib-only
(§ dependency audit), error handling fail-closed, persistence through
the append-only store, recovery exercised by the crash matrix, and
security boundaries enforced rather than documented.

The boundary worth singling out is `solvent/egress.py`: a CPython audit
hook denying `socket.connect`, `socket.getaddrinfo`, `socket.sendto`,
`socket.bind`, `subprocess.Popen`, `os.system`, `os.exec` and
`os.posix_spawn` for the whole interpreter, removable by nothing,
firing below application code, with the only way through a window the
Action Gate opens around one approved call and closes in a `finally`.
Its own docstring states the two residual risks it does not close.

Evidence it behaves like an enforcement point rather than a
convention: six tests written during this engagement passed alone and
failed in the suite because an earlier test had armed the guard and
they shelled out to `git`. The tests were wrong and were rewritten.

## 6. Defects found in this audit

| # | Defect | Status |
|---|---|---|
| 1 | Five state paths drive-relative on Windows | **repaired** |
| 2 | The service schedules no business cycle | **reported, pinned, not repaired** — activation decision |
| 3 | T2 verification readers are an undocumented prerequisite the installer does not install | **reported** — see the dependency audit |

Defects 1 and the three installer defects from the preceding round
(3010, exit codes, ARM64) are repaired with tests and mutants.

## 7. Status

**PRE-INSTALL ENGINEERING: PASS**, with defect 2 as a blocker on
*useful* operation rather than on installation, and defect 3 as a
documented prerequisite.
