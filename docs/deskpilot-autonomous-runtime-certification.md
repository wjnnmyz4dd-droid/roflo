# DeskPilot — autonomous runtime certification

Date: 2026-10-21 · Linux x86_64, CPython 3.11.17 · Branch
`claude/solvent-architecture-audit-8ot9et` · Base commit `694867d`

The business engine is connected to normal startup. It runs in
simulation only, it is off until the owner turns it on, and four
separate conditions stop it.

---

## 1. The defect, and the repair

`Runtime` scheduled eight tasks and none ran the business cycle.
`run_business_cycle` was reachable only from the one-shot CLI and from
`solvent/operation.py` — the bounded loop, imported by nothing. An
installed DeskPilot would start, heartbeat, reconcile, discover, brief
the owner and pass every health check while never doing a job.

A ninth scheduled task, `business`, now drives that loop.

**No second engine.** The tick owns no Finder, no Governor, no Action
Gate and no admission rule. It calls `run_business_cycle` through
`operation.Operator`, which is where the owner's limits already live.
A second engine here would make "what decides whether DeskPilot takes
a job?" a question with two answers, and tests assert the tick
constructs none of those authorities.

## 2. Four refusals, each sufficient alone

| Condition | Behaviour |
|---|---|
| **Simulation** | refused unless `egress.simulation_only`. The authorisation for this loop is simulation-only, and a loop that keeps running when simulation is switched off has gone live without being asked. |
| **Opt-in** | `Operator.permitted` is false unless `operations.continuous_enabled`, which defaults **off**. Wiring this in changes nothing until the owner says so. |
| **The halt** | checked before each job, never after: a limit tested on the way out has already been exceeded once. |
| **One executor** | an audit-log lease. |

`Task` never overlaps with itself, but that is one process. The lease
stops a hand-run `solvent serve`, a scheduled task that fired twice, or
an operator debugging beside the service from running a second business
loop against the same database. It lives in the append-only audit log —
already durable, already backed up — and a lease older than an hour is
treated as abandoned and says so, because a process killed mid-run must
not lock the business out for ever. An unreadable timestamp reads as
`None` rather than "just now", so it cannot make every lease look fresh.

## 3. Acceptance, through the service path

The rule was that a manually invoked CLI cycle does not count. Nothing
in the harness calls `run_business_cycle`. It runs
`Runtime.start → default_tasks → run_due`, which is `serve()` without
the real-time sleep. The only thing injected is the *supply of work*,
because a Finder reaching a real marketplace is what is not authorised;
everything downstream is the production path.

```
offered                       100   (20 per skill, five skills)
manual CLI cycles               0
scheduler ticks                13
bounded business runs          13   each stopping with a reason
restarts mid-run                1
delivered                      53
```

| Skill | Attempted | Delivered |
|---|---|---|
| csv-cleanup | 20 | 2 |
| data-entry | 20 | 10 |
| document-processing | 20 | 11 |
| report-builder | 20 | 10 |
| spreadsheet-automation | 20 | 20 |

| Invariant | Result |
|---|---|
| Skills delivering nothing | none |
| Unauthorized deliveries | 0 (53 deliveries, 53 consumed approvals) |
| Duplicate invoices | 0 |
| Fabricated payments | 0 |
| Delivered but unbilled | 0 |
| Stranded jobs | 0 |
| Audit chain | intact |
| Simulation | on throughout |
| Real revenue | 0 cents |

Census: 53 COMPLETE, 40 REJECTED, 7 BLOCKED. The rejections are
qualification refusing work it should refuse and the blocks are
approval and verification holding — a run delivering 100 of 100 would
mean the gates had stopped gating.

## 4. Operational health

A heartbeat says the process is alive. That is all it says, and it is
why a disconnected business cycle went unnoticed.

`Runtime.business_status()` distinguishes ten states —
`SERVICE_RUNNING`, `SCHEDULER_RUNNING`, `SCHEDULER_PAUSED`,
`CYCLE_EXECUTING`, `NO_ELIGIBLE_OPPORTUNITIES`, `WORKER_FAILURE`,
`RECOVERY_REQUIRED`, `HALTED`, `SIMULATION`, `EXTERNAL_DISABLED` — and
records seven figures: last cycle started, last completed, last
successful, last failure, queue depth, active workers, executor lease.

It deliberately has **no `operational` or `healthy` field**. It reports
parts; whoever renders them says what they total. A single cheerful
boolean is how the disconnection went unnoticed in the first place, and
adding one back would reintroduce the failure this fixes.

Reads are wrapped: a status call is a page render, not a transaction,
and a database hiccup must not take the dashboard down.

## 5. Three defects I introduced, and what they cost

Recorded because the pattern matters more than the bugs.

**The tick referenced `fmt` without importing it.** Every business run
raised `NameError` after the work completed, and the task reported
FAILED each time — three strikes from logging itself DEGRADED.
**Twenty-two tests passed against it**, because every one read the
function's *source* for the strings "Operator" and "simulation_only"
rather than executing it. The hundred-job acceptance caught it.
`TheTickActuallyRuns` now executes the tick; removing the import again
fails that class and not the source-reading ones, which was checked
rather than assumed. A test that greps a function cannot tell whether
the function works.

**The harness read `j.capability`,** a field `Job` does not have, and
reported all five skills delivering nothing while 53 jobs had been
delivered. The skill is now recorded where it is known.

**A regression run was launched and then edited underneath.** Three
failures appeared that were not the product's. The same class of error
as committing from under a mutation run — which this engagement built a
guard against, for the repository, and which does nothing for a report.

A fourth, in reporting rather than code: a stale `mut_final.txt` from
an earlier round was matched by a monitor and briefly reported as a
current result. Scratchpad filenames are now checked by timestamp
before any figure is quoted.

## 5a. The T2 blocker is now visible at startup

Three of the five skills produce formats whose independent review needs
a reader the installer does not install: `.xlsx`, `.docx`, `.csv`
dialect detection. A fresh machine can therefore take C_LOW and C_MED
work and must decline C_HIGH work in those formats.

That was already safe — `independent.available()` reports what the host
can actually produce and the business cycle declines such a job **while
it is still a quote**, rather than discovering it at delivery. But it
was invisible: the owner learned about it from jobs quietly not being
taken.

`startup_checks` now names it. For each format with a differential
reader it asks whether any producer is `usable` and not
`needs_a_person`, and reports which formats are covered and which are
not, with the remedy. It is **non-fatal**: a machine that cannot do T2
should still run and do T1 work.

It **downgrades nothing**. T2 stays T2. The check reports capability; it
does not relax a requirement to match it, which is the failure mode the
brief named.

The second half of W3 — actually installing those readers — is **not
done and is not mine to decide**. It needs either pip in the
installer's normal path, which is the one thing the installer was built
to avoid, or vendored wheels, which brings LGPL `chardet` transitively
through `clevercsv`. Recorded as a blocker in
`deskpilot-windows-dependency-audit.md` §2.

## 6. Verification

Both halves of the chain ran against a settled, committed tree
(`b0a8c06`, clean) and neither was edited underneath.

```
regression   5,080 passed, 46 skipped, 0 failed, 10,335 subtests
             written 16:17:26, exit 0
mutation     212/212 killed  (0 survived, 0 not applied, 0 harness)
             written 16:40:46, exit 0, isolated copy
iFixAi       CLEAR, 0 violations, 3 of 3 injected controls caught
```

5,080 is up from the 5,022 baseline by the 58 tests this round adds.

**Source integrity after testing.** `mutate_solvent.py` patches a copy —
`isolated: patching a copy of the working tree at
/tmp/deskpilot-mutation-kap58vib` — so the repository is never mutated.
Confirmed after the battery: working tree clean, `mutated_files()`
empty, no active claim, the copy removed. That guard exists because an
earlier round in this engagement committed a disabled profitability
guard to origin from under an in-place run.

Figures and the full mutant list are in
`docs/evidence/deskpilot-autonomous-runtime.json`.

## 7. What this does not say

* **Nothing about Windows.** Executed on Linux. Windows runtime, the
  installer and the website remain UNVERIFIED, and the CI workflow has
  not been run.
* **Nothing about live operation.** Simulation is mandatory and
  enforced in code; `continuous_enabled` is off by default.
* **Nothing about real money.** 0 cents, synthetic contracts, a test
  approver with a throwaway key.
* **It does not say the loop should be switched on.** It says that if
  the owner switches it on, it runs bounded and stops for a reason.
