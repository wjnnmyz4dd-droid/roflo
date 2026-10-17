# DeskPilot — P0 remediation: supervision and storage

**Implementation and test of the two identified P0 corrections.** No live
customer acquisition, no payment provider, no production deployment, no
marketplace automation, no money moved, MT5 untouched.

Date 2026-10-17. Branch `claude/solvent-architecture-audit-8ot9et`,
baseline verified at `84f6fc4` (HEAD confirmed, not assumed).
Evidence: `docs/evidence/p0-supervision-and-storage-remediation.json`.

---

## A. Executive summary

**P0-A was real, is fixed, and the fix is nine lines of wiring.**
Of the eight genuine failures that previously produced zero incidents
and eight health panels reading `HEALTHY`, the **seven on the business
cycle** now each produce an incident, a `DEGRADED` panel, flight frames,
a routed notification and an intact audit chain — at no measurable
performance cost. The eighth, on the customer-service path, is not on
the supervised cycle: it still raises rather than losing the client's
message, and still records no incident (section P).

**P0-B was not real as stated, and I am reporting that rather than
implementing a fix for it.** The "~0.45 MB per cycle" came from a
benchmark harness that built a *fresh 462 KB SQLite database for every
cycle*. Measured the way production actually runs — one long-lived
system, 500 cycles — growth is **5.08 KB/cycle**, projecting **38 MB a
year at 20 jobs/day, not 3 GB**. Most of it is append-only tables that
SQLite triggers forbid deleting, deliberately.

What P0-B did surface, once measured properly, are three smaller real
things, all now addressed: an unbounded **processed-intent spool** (now
pruned, safely), a **disk check using percentage only** (now also
absolute, because 10% of a 64 GB VPS is not 10% of a 2 TB disk), and a
**recorder outage reading as health** (now a monitoring failure).

**Still blocked / not done:** no recovery of a failed job is attempted
or claimed — 7 of 7 failures are **contained and unresolved**, not
recovered. MT5 coexistence remains **uncertified**: every number here
is Linux and the VPS is Windows.

## B. Original root causes

**P0-A, confirmed against current source rather than the prior report's
line numbers.** `resilience.record_incident` had exactly one production
caller: `harness.py`, inside `supervised()`. `supervised()` had **zero
call sites in `solvent/`** — `grep` exits non-zero — and six in
`tests_solvent/test_crash_matrix.py`. The production entry point,
`cli.py:161`, called `run_business_cycle` bare. `diagnostics.py` only
ever *read* the incident table, so the dashboard reported health from a
table nothing could write.

That is why 4,744 passing tests never caught it: the one suite
exercising supervision supplied the integration it was meant to verify.

**P0-B, measured.** Reproducing the growth showed the earlier harness
calling `build()` — a whole new `Solvent` and database — inside its own
per-cycle loop. 11.3 MB over 20 cycles was 20 databases, not
accumulation.

## C. Code changes

Three files, 279 insertions, 19 deletions. No authority gained a new
power; no control was removed.

| file | change |
|---|---|
| `solvent/harness.py` | `run_business_cycle` split into a supervised entry point and `_run_business_cycle`; `_late()` resolves identifiers at failure time; a recorder outage is audited and re-raises the original error; `prune_processed_intents` added |
| `solvent/diagnostics.py` | `incidents()` refuses to report health while the recorder is broken (`_incident_recording_broken`); `disk()` gained absolute free-space floors |
| `tests_solvent/test_production_supervision.py` | new: 22 tests, 14 subtests, all through the production entry point |

## D. Production wiring

The wrapper is at the single composition point rather than at each call
site, so a caller cannot opt out by forgetting:

```python
cycle = BusinessCycle()
with supervised(s, component="harness", operation="business-cycle",
                job_id=lambda: cycle.job_id,
                checkpoint="business-cycle"):
    return _run_business_cycle(s, cycle, ...)
```

`job_id` is a callable because a cycle is supervised from before it has
one; `_late()` resolves it when the failure happens, and swallows its
own errors so identifier lookup can never replace the real exception.

**Proof it is production, not test scaffolding:** `cli.py:161` calls
`run_business_cycle` and nothing else changed there. Every test in the
new suite goes through the same function. Nothing in the new suite calls
`supervised()` directly — that prohibition is stated in the module
docstring, because calling it directly is precisely the mistake that hid
this defect for so long.

## E. Incident recording

Seven delivery-path seams, a fresh system each, real faults at real
authority methods:

| check | result |
|---|---|
| failure actually occurred | **7/7** |
| contained, nothing delivered wrongly | **7/7** |
| incident recorded | **7/7** |
| health state changed | **7/7 DEGRADED** |
| flight frames written | 2 per failure |
| notification routed | 7, each with a dedupe key |
| audit chain intact afterwards | **7/7** |
| mean detection | **0.042 s** |

The original exception still reaches the caller in every case.
Supervision records and re-raises; it decides nothing.

## F. Health reporting, before and after

| | before | after |
|---|---|---|
| after a real failure | `HEALTHY: no open incidents` | `DEGRADED: 1 open incident(s)` |
| after a failure nobody could record | `HEALTHY: no open incidents` | `FAILED: the incident recorder could not be written… Nothing here can be trusted to be empty` |
| on a clean system | `HEALTHY` | `HEALTHY` (unchanged) |

Health states are the module's existing ones. No new vocabulary was
introduced, because equivalents already existed — the brief's
NORMAL/WARNING/CRITICAL map onto `HEALTHY`/`DEGRADED`/`FAILED`, and
`UNKNOWN` already existed for missing telemetry.

The second row is the one that matters most. An empty incident table has
two possible meanings — nothing went wrong, or nothing could be written
down — and they must never render the same.

## G. Recovery testing

**Reported as the brief requires: contained and unresolved, not
recovered.**

| injected failure | contained | recorded | recovered |
|---|---|---|---|
| intake, requirements, artifact_registration, verification, delivery_gate, delivery, ledger | 7/7 | 7/7 | **0/7** |

Every incident's status is `DETECTED`. DeskPilot does not attempt to
resume a failed job, and I have not made it do so: that is a separate
change with real risk (a resume that skipped verification would be worse
than a stop), and it was not one of the two authorised P0s.

Compound failures:

| scenario | outcome |
|---|---|
| incident store write fails mid-failure | original `InjectedFault` reaches the caller, `RecorderDown` preserved as its cause, outage audited, panel `FAILED` |
| notification sink fails | incident still recorded, panel `DEGRADED` |
| worker and checker both fail | one incident, `DEGRADED`, audit chain intact |
| repeated transient failure, 6 attempts | no retry storm — supervision never retries |
| customer-service fault | raises rather than losing the client's message; **records no incident** (not on the supervised path) |

## H. Notification behaviour

The owner's policy is unchanged: two immediate routine notifications
(job obtained, payment received) and the 06:50 / 16:30 America/New_York
summaries. Nothing in this work touched that routing.

Incidents use the existing escalation path, which maps severity to
channel and carries `dedupe_key="incident:<fingerprint>"`. Measured:
**three identical failures in one system produce one conversation, not
three**; distinct failures get their own.

**A change in volume the owner should know about.** Before this fix, a
contained failure notified nobody, because no incident existed. Now a
`DEGRADED` incident routes at `ACTION_REQUIRED`, which reaches website
and SMS. That is the intended behaviour for a job that has stopped and
will not resume, and the dedupe key bounds it — but it is a real change
from silence, and it is the one item here worth the owner's review
before unattended running.

## I. Disk retention

`prune_processed_intents` removes handled owner intents from `applied/`
and `refused/`, default 90 days.

**Age is the second condition; the record is the first.** A file is
removed only when the append-only audit log is shown to hold the
`owner.intent_processed` event for it. Deleting on age alone would let a
recording bug and a cleanup bug combine into silent data loss.

Safeguards, each with a test: only `.intent` files directly inside the
two named folders; never a symlink (a planted link cannot make it delete
what the link points at); never a path that resolves outside the spool;
never a directory; a missing spool is not an error; interrupted halfway
it leaves a consistent spool, because each file is independent; failures
are audited and never raise, since maintenance that can take the
business down is worse than a spool that grows.

**Not pruned, deliberately:** `audit_log`, `ledger_entries`,
`payment_events`, `artifacts`, `verification_evidence` and the incident
timeline are append-only by SQLite trigger — `DELETE` is refused by the
engine. That is a designed guarantee and defeating it to save 38 MB a
year would be a bad trade.

## J. Capacity enforcement

Already present and correct, and I did not rebuild it:
`capacity._disk_problem` refuses new work below `min_free_disk_bytes`
and `_unreadable` fails conservatively when the host will not report.
The graded model (`TIGHT`/`FULL`/`UNMEASURED`) already drives worker
count down.

What was missing was absolute free space in the **health check**.
`Disk` previously used percentage alone, so it read `HEALTHY` with 1 GB
free on a 2 GB disk — 50% used. Now the worse of the two readings
decides, with floors at 5 GB (warn) and 2 GB (fail), stated in bytes
because that is what MetaTrader 5 can actually use.

**`min_free_disk_bytes` still defaults to 0, and I left it there on
purpose.** `capacity.py` states that the budget is "a configured share
of a machine, never an inference about the whole of it". Guessing the
owner's number would contradict that. It belongs in section R.

## K. MT5 protection

**NOT CERTIFIED, and nothing here changes that.** Every measurement is
Linux; the VPS is Windows.

Measured: peak RSS **33.8 MB**, unchanged from the pre-fix baseline;
0.57 cores busy at full load; storage growth 5.08 KB/cycle. No MT5 file
was read, written or named; `capacity.py` still names no MT5 process.

What remains unverified: Windows memory and handle behaviour, NTFS
free-space reporting under the absolute floors, and behaviour when MT5
is genuinely competing for the disk.

## L. Skillslab integration

**Not connected, and the recommendation is documented instead — this is
the brief's own fallback and I think it is the right call.**

`skillslab.record_need` has zero production callers. The natural hook
would be "we refused work because we cannot handle this input", but
that condition does not exist as a distinguishable state: `conforms()`
in `services.py` returns one refusal for both *"we understand this work
and cannot read its format"* and *"this is not work we do"*. Wiring the
lab to that would turn "prepare our tax filing" into a request to build
a tax-filing skill.

`conforms()` is also a pure predicate with no authority handle; passing
one in would couple the scope classifier to the skills lab, the same
coupling supervision was deliberately kept out of.

**Prerequisite, stated precisely:** separate the two refusal reasons, so
an unsupported *format* is distinguishable from out-of-scope *work*.
Once that exists, the hook belongs at the composition layer — where
supervision now lives — and is a few lines.

## M. Regression results

| run | result |
|---|---|
| full suite, before any change (baseline) | **4,700 passed, 44 skipped, 10,071 subtests**, 480 s |
| full suite, after all changes | **4,722 passed, 44 skipped, 10,085 subtests**, 432 s |
| new production-path suite | **22 passed, 14 subtests** |

The delta is exactly the 22 new tests and 14 new subtests. **No existing
test changed behaviour**, which matters: wrapping every production cycle
in supervision could plausibly have broken suites that count
notifications or assert on clean audit logs, and it did not.

The 44 skips are the pre-existing egress-guard subprocess suite and are
**not counted as passed**; they pass when run alone.

## N. Mutation testing

**155 mutants, 155 killed, 0 survived, 0 NOT_APPLIED.** Run after all
changes, sequentially, with no other harness running; sources verified
restored clean afterwards and the diff byte-identical to before the run.
NOT_APPLIED was not counted as a kill.

Unchanged from the pre-change run, which is the point: wiring supervision
in did not loosen any control the mutation suite pins.

## O. Performance comparison

Same harness shape, same machine, 20 cycles at full load:

| | before | after |
|---|---:|---:|
| delivered | 20/20 | **20/20** |
| CPU per cycle | 0.116 s | **0.104 s** |
| cores busy | 0.54 | 0.57 |
| peak RSS | 33.8 MB | **33.8 MB** |

**Supervision adds no measurable overhead.** The small difference is
run-to-run noise, not a speed-up; I am not claiming the fix made
DeskPilot faster. Incident recording costs one row and one audit event,
and only on the failure path.

## P. Remaining weaknesses

1. **No job resume.** 7 of 7 failures contained, 0 recovered. A job that
   fails stops and is escalated; for work already paid for that is a
   refund. Unchanged by this work and still the top reliability item.
2. **The customer-service path is unsupervised.** A fault there raises
   correctly but records no incident. Lower consequence, same shape.
3. **`min_free_disk_bytes` is unset**, so the capacity floor does not
   engage until the owner configures it (section R).
4. **Incident notification volume is new.** Bounded by dedupe, but it
   goes from zero to one SMS per distinct failure fingerprint.
5. **Windows is entirely unmeasured.**
6. **Skillslab remains disconnected**, pending the refusal-reason split.

## Q. Production readiness

**Safer than before, and not yet certified for unattended running.**

What changed honestly: DeskPilot could already fail safely; it now also
*notices*, *records*, *reports* and *tells the owner*. That is the
difference between a system that is quiet because nothing is wrong and
one that is quiet because nothing can speak.

What has not changed: it cannot recover a failed job, it has no payment
rail, it commits to zero jobs by default, and no Windows evidence exists.
Unattended operation on the MT5 VPS should not be claimed on Linux
numbers.

## R. Next steps

Smallest remaining changes before a controlled paid-job pilot:

1. **Owner sets `min_free_disk_bytes`** (suggest 5–10 GB on the shared
   VPS). One config value; the mechanism is already there.
2. **Owner reviews incident notification volume** (section H) and
   confirms `ACTION_REQUIRED` over SMS is wanted, or routes it to the
   scheduled summaries instead.
3. **Run this evidence on the Windows VPS.** Until then MT5 coexistence
   stays uncertified.
4. **Job resume** — the largest remaining reliability item, and the one
   that needs real design rather than wiring.
5. Then the unchanged commercial blockers: a payment rail that can
   receive, and `max_committed_jobs` above zero.

---

*No production behaviour was changed beyond the two authorised P0
corrections and the three smaller defects they exposed. No payment
provider integrated, no dependency installed, no marketplace automation
activated, no bid submitted, no contract accepted, no client contacted,
no money moved, no storefront published, MT5 untouched, the Action Gate
unchanged, and the Brain still advisory. Where a measurement contradicted
the brief's premise — P0-B — I reported the contradiction rather than
implementing against it.*
