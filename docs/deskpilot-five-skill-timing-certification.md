# DeskPilot — five-skill timing certification

**Measurement only.** No production file was modified for this phase.
Timing is taken at the seams production already calls, via a test-side
probe, so a stage's time is the time that method took rather than the
time a hook added for the purpose took.

Date 2026-10-17. Baseline `08cd3d5`, tree clean.
Raw evidence: `docs/evidence/deskpilot-per-job-stage-timings.csv`
(100 rows, one per contract, 26 columns).
Summary: `docs/evidence/deskpilot-timing-summary.json`.

Elapsed time from `time.monotonic()`; a UTC timestamp per job for
correlation. 100 contracts across five skills, 53 delivered and timed.

---

## The number the brief warned about

The previously reported **0.048 s** per contract was
`execution_wall_seconds`, and the brief was right to distrust it. That
field is documented in the source as covering *"plan, work and
verification"* together, and it excludes discovery, qualification,
pricing, contract, delivery, invoicing, payment and reconciliation
entirely.

**Measured total end-to-end: 99.3 ms mean**, roughly double, and it
divides very differently from the old figure.

## Per skill

Means, in milliseconds. "Worker" is plan + produce; "checker" is the
independent verification pass.

| skill | n | delivered | worker | checker | delivery | financial | **total** | p95 | jobs/hr (1 lane) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| csv-cleanup | 20 | 3 | 8.8 | 17.3 | 1.7 | 2.5 | **88.3** | 153.5 | 40,755 |
| data-entry | 20 | 10 | 9.5 | 42.5 | 1.9 | 2.9 | **89.3** | 114.8 | 40,310 |
| document-processing | 20 | 10 | 8.8 | 22.8 | 1.8 | 2.9 | **65.0** | 73.8 | 55,389 |
| report-builder | 20 | 10 | 7.1 | 12.6 | 1.6 | 2.5 | **51.7** | 59.3 | 69,676 |
| spreadsheet-automation | 20 | 20 | 7.6 | 73.7 | 1.8 | 2.8 | **146.9** | 178.4 | 24,504 |

Median, p95, fastest and slowest for every stage and skill are in the
summary JSON; per-job values are in the CSV.

## The dominant cost is verification, by a wide margin

| skill | checker ÷ worker |
|---|---:|
| report-builder | 1.79× |
| csv-cleanup | 1.96× |
| document-processing | 2.58× |
| data-entry | 4.45× |
| **spreadsheet-automation** | **9.76×** |

Worker time is remarkably flat — 7.1 to 9.5 ms across all five skills.
**Everything that distinguishes the skills is on the checking side**,
and spreadsheet-automation spends nearly ten times as long verifying as
producing. Independent verification is where DeskPilot's time goes.

## Where the 99.3 ms actually goes

| stage | mean ms |
|---|---:|
| **checker** | **43.50** |
| worker (plan + produce) | 8.15 |
| qualification | 6.76 |
| contract | 4.98 |
| delivery | 1.79 |
| discovery | 1.61 |
| selection | 1.26 |
| payment state | 0.97 |
| reconcile | 0.95 |
| invoice | 0.84 |
| ranking | 0.52 |
| **sum of top-level stages** | **71.35** |
| **unattributed** | **27.95 (28%)** |

**Two nested measurements, excluded from that sum rather than added to
it.** `record_verification` runs *inside* `verify_against_baseline`,
and `register_artifact` inside the execution window. Adding them would
double-count. I did exactly that in a first pass and corrected it:

| nested | ms | inside |
|---|---:|---|
| verification_record | 16.45 | checker |
| artifact_register | 2.07 | worker |

That first figure is worth its own line: **38% of measured checker time
is the audit write that records the verification, not the verification
itself.** If checker time ever needs reducing, that is where to look
first — and it is bookkeeping, not safety.

The **28% unattributed** is orchestration between stages plus the
pricing/governor path, which was not given its own seam. I am reporting
it as unattributed rather than silently folding it into a stage.

## Three honest limits on these numbers

**Queue waiting time is 0 by construction, not by merit.** This harness
runs each cycle synchronously, so no queue exists to wait in. The
column is in the CSV as zero so it cannot be mistaken for measured, and
it is **not** evidence that a queued deployment would have none.

**Delivery time is local.** 1.8 ms is the Action Gate path with
simulation on. It is **not** marketplace submission latency, and must
not be read as one.

**Linux only.** The VPS is Windows; nothing here certifies it.

## Revision and error rates

Revision rate **0.000** and error rate **0.000** across all five skills
in this run: no delivered job needed a second verification round, and
no job raised. The 47 undelivered contracts stopped at a gate — 13 on
client ambiguity, 34 on the unresolved CSV T2 blocker — rather than
failing.

## Reproducing this

```
python3 -I run_timing.py      # writes the CSV and the summary JSON
```

The probe wraps 12 real call sites listed in `stagetimer.py`:
`discovery.insert`, `qualification.qualify/rank/select`,
`orchestrator.commit_requirements/register_artifact/record_delivery/complete`,
`audit.record_verification`, `ledger.open_payment/set_payment_state`,
and the module-level `verify_against_baseline`. The last one is the
reason checker time can be separated at all.
