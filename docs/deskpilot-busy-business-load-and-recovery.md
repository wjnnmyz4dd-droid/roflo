# DeskPilot under a busy business load

**Testing and simulation only.** No production file was modified this
round, no real job accepted, no proposal submitted, no payment
activated, no browser automation deployed, the VPS untouched, MT5
untouched, no dependency added, the Action Gate unchanged.

Date 2026-10-17. Baseline `a7763a5`, working tree verified clean before
and after. Evidence:
`docs/evidence/busy-business-load-and-recovery.json`.

`max_concurrent_jobs` was raised **only inside disposable test systems**,
never in shipped configuration — the shipped default of 3 cannot be busy,
and the brief forbids changing production capacity.

---

## The headline

**DeskPilot cannot run a busy business. It delivers `max_concurrent_jobs`
jobs in its entire lifetime and then stops for good.**

100 jobs offered to one shop sized to the five-skills × four-committed
rule: **7 delivered, 80 rejected, and the shop seized permanently at
job 20.** Not slowed — seized. Every subsequent opportunity, forever,
is refused with *"not selected: capacity is already committed
elsewhere"*.

The cause is three lines in `types.py`:

```python
@property
def is_terminal(self) -> bool:
    return self in (JobState.REJECTED, JobState.FAILED)
```

`COMPLETE` is not terminal. `qualification._committed_count()` counts
every job that is not terminal and not INTAKE/QUALIFYING — so a
finished, delivered, paid job **holds its capacity slot forever**. A
rejected job releases capacity; a successful one never does.

Measured directly, shipped ceiling of 3:

| cycle | delivered | occupying capacity | states |
|---:|---|---:|---|
| 0–2 | yes | 1, 2, 3 | `COMPLETE` ×3 |
| 3–5 | **no** | 3 | `COMPLETE` ×3 + `REJECTED` |

**Success is what breaks it.** The better DeskPilot does, the sooner it
stops.

## What the five skills actually do (100 jobs)

Via the repository's own `skillmatrix`, 20 jobs per skill, a virgin
system each so capacity never bites:

| skill | graded | delivered | expected mix | s/job | one-lane jobs/hr |
|---|---:|---:|---|---:|---:|
| csv-cleanup | **20/20** | 7 | 7 DELIVER, 13 ASK | 0.030 | 118,325 |
| data-entry | **20/20** | 20 | 20 DELIVER | 0.062 | 58,275 |
| document-processing | **20/20** | 20 | 20 DELIVER | 0.032 | 111,861 |
| report-builder | **20/20** | 20 | 20 DELIVER | 0.025 | 145,299 |
| spreadsheet-automation | **20/20** | 20 | 20 DELIVER | 0.089 | 40,328 |
| **total** | **100/100** | 87 | — | — | — |

**100 of 100 passed.** The 13 csv-cleanup non-deliveries are scenarios
where asking the client a question is the *correct* outcome, and it
asked. I first reported this as "87/100 delivered" and that was the
wrong metric — delivery is not the goal when the source is ambiguous.

## Three independent controls close the shop

Running the busy shop surfaced three separate refusals, and only the
first is a defect:

1. **Capacity never released** (above) — a defect.
2. **Unpaid exposure limit.** With 13 jobs in `AWAITING_PAYMENT` the
   shop refuses more work: *"exposure or risk beyond policy limits"*.
   **Correct behaviour** — it stops working for a client who is not
   paying.
3. **No invoice can be issued at all.** Every delivered job in every
   run stopped at the invoice stage: *"INVOICE needs contracting detail
   the owner has not provided"*. **Correct behaviour** — an invoice
   needs a legal party, and the owner has not supplied one.

I verified (3) against a crash-free control run: 8 of 8 clean jobs
stopped identically. It is not crash damage and not a defect; it is a
**second first-dollar blocker alongside the payment rail**, and it was
not in any previous report.

## The four-per-skill rule is not implemented as pools

`JOBS_PER_AGENT_CEILING = 4` × `CERTIFIED_SKILL_COUNT = 5` gives a
global 20, but `_committed_count()` counts **all jobs regardless of
skill**. There are no per-skill pools.

Measured consequence: offering the backlog grouped by skill, csv-cleanup
consumed all 20 global slots and **four of the five skills delivered
nothing at all**.

| skill | delivered of 20 offered |
|---|---:|
| csv-cleanup | 7 |
| spreadsheet-automation | **0** |
| document-processing | **0** |
| data-entry | **0** |
| report-builder | **0** |

One skill can starve the other four. The rule the brief asked me to
maintain does not exist in the code as a per-skill rule.

## Replenishment: none

A deliberate test — fill a ceiling-4 shop, then try to settle the jobs
holding the slots:

- 4 delivered, 4 slots held, 2 rejected.
- `orchestrator.complete()` **refused all four** with `FailClosed`:
  simulated money is not collected revenue, which is correct.
- Slots occupied after settlement attempt: **still 4**.
- Further jobs delivered: **0**.

And with a *real* rail the result is the same, because `COMPLETE` is
not terminal either. **Both paths seize.**

## Crashing each component

Seven components crashed while the shop was working, each at a real
authority method, with a job that was expected to deliver:

| component crashed | fault fired | incident | panel | next job works |
|---|---|---:|---|---|
| worker (during execution) | yes | 1 | DEGRADED | **yes** |
| checker (during verification) | yes | 1 | DEGRADED | **yes** |
| brain (memory recall) | yes | 1 | DEGRADED | **yes** |
| queue (intake) | yes | 1 | DEGRADED | **yes** |
| supervisor (while a job fails) | yes | 0 | **FAILED** | **yes** |
| ledger | yes | 1 | DEGRADED | **yes** |
| delivery gate | yes | 1 | DEGRADED | **yes** |

- **0 jobs delivered despite a crash.** Nothing wrong escaped.
- **Every audit chain intact.**
- **No component takes another down** — the next job ran in all seven.
- The supervisor row records no incident because it *is* the recorder;
  the panel reads `FAILED` instead, which is the fix from the previous
  round working.

A first version of this table was wrong and I corrected it: the jobs I
used were csv-cleanup scenarios that stop on an ambiguity question
*before* reaching the seam, so four faults never fired. Those rows
proved nothing and were re-run with jobs that reach the seam.

## Crashing the whole application

`SIGKILL` to a child process holding the database, then reopen.

**Between jobs** (killed after 4 deliveries):

| | |
|---|---|
| jobs accounted for | **4 of 4** |
| lost or failed | **0** |
| restart open | 0.018 s |
| first post-crash delivery | 0.12 s |
| audit chain | intact |

**Mid-cycle** — five kill points at 0.9 s to 3.2 s, landing inside
active work:

| | |
|---|---|
| databases that reopened | **5 of 5** |
| mean reopen | **0.014 s** |
| audit chains intact | **5 of 5** |
| jobs stranded mid-flight | **1–2, left in `VERIFYING`** |
| corruption | none observed |

**What resumes, what pauses, what fails.** Nothing resumes. Jobs that
had reached `AWAITING_PAYMENT` survive correctly. Jobs killed during
verification are **stranded in `VERIFYING` permanently** — no resume,
no timeout, no escalation — and they hold a capacity slot, so each
mid-cycle crash permanently shrinks the shop.

Three trials refused new work after restart, and I checked rather than
assumed: the reason was the **exposure limit**, not crash damage.

## Brain on versus Brain off

Same backlog, same clients, twice — once with T3-verified client
history in memory, once without:

| | Brain off | Brain on |
|---|---:|---:|
| delivered | 13 | **13** |
| from the paid-on-time client | 7 | **7** |
| from the disputed client | 6 | **6** |

**Zero difference, on every measure.**

The reason is structural. `run_business_cycle` calls
`qualification.rank([decision])` — **a list of exactly one**. The
Brain's only operational surface, `_client_factor`, is a multiplier on
a relative score, and a single candidate has nothing to be relative to.
The multi-candidate ranking path at `harness.py:607` is a report
generator, not the cycle.

This sharpens my earlier finding rather than contradicting it. I
previously measured `_client_factor` by calling the ranker directly
with three candidates and reported "changes 2 of 3 decisions, 3×
spread". That is true of the function and **irrelevant to the
business**, because production never gives it a choice to make.

## An accelerated 24-hour day

| | as shipped | if capacity released |
|---|---:|---:|
| jobs offered | 100 | 100 |
| **jobs delivered in 24 h** | **7** | 87 |
| shop seizes after | job 20 | — |
| wall time to do it | 2.3 s | 21.2 s |
| one-lane jobs/hour | — | 14,815 |
| quoted value | $2,800 | $34,800 |

The right-hand column is measured with a virgin system per job. **It is
not a fix and no fix was made** — it is the size of the hole.

**The whole day's work finishes in 2.3 seconds and then the business is
idle for 23 hours, 59 minutes and 57 seconds.**

## Profitability

**Billed revenue in every run: $0.00.** Not a modelling choice — no
invoice was issued, because the owner has not provided contracting
details. The "$34,800 invoiced" the matrix reports is a *quoted amount
attached to cycles that stopped before invoicing*, and presenting it as
revenue would be false.

What is genuinely measured is cost, and it is tiny: $4,515.30 of
modelled operating cost across 100 jobs at the test fixture's rates
(10% platform, 2.9% + 30¢ payment, 50¢/CPU-hour) — and those fixture
rates are themselves test-only.

**Effect of crashes on profitability:** none that is measurable,
because there is no revenue to reduce. The measurable costs of a crash
are a stranded job holding a slot and the work already done on it.

## What needs improving, in order

**P0-1 — `COMPLETE` is not terminal, so capacity never returns.**
Evidence: 7 of 100 delivered; 3 of 3 slots held by finished jobs.
Impact: the business stops permanently after `max_concurrent_jobs`
lifetime successes. Fix: count settled jobs as released — either add
`COMPLETE` to `is_terminal`, or better, have `_committed_count()`
exclude settled states explicitly so "terminal" keeps its meaning.
Reuse: entirely existing code. New dependency: no. Complexity: small,
but it is capacity semantics and must be owner-authorised. Regression
risk: **medium-high** — the committed count feeds the Governor and the
capacity gate; a careless change raises real limits. Verify: the
100-job busy-shop run in this report, which must go from 7 to ~87.

**P0-2 — jobs stranded mid-flight are never recovered.** Evidence: 1–2
jobs per mid-cycle crash left in `VERIFYING` forever. Impact: each
crash permanently shrinks capacity and silently abandons paid work.
Fix: on startup, reconcile jobs found in transient states — resume or
fail them explicitly, never leave them. Reuse: the audit trail already
records the last stage. Complexity: medium. **This is the job-resume
item from the previous report, now with a measured failure count.**

**P1-1 — the four-per-skill rule has no per-skill pools.** Evidence:
four of five skills delivered zero. Fix: count commitments per
capability against `JOBS_PER_AGENT_CEILING`, keeping the global
ceiling as the outer bound. Complexity: small. Risk: low.

**P1-2 — the Brain is inert in the production path.** Evidence: zero
difference on every measure. Fix: either rank the available candidates
together so the score can discriminate, or stop describing
`_client_factor` as an operational capability. **Do not add a model** —
the gap is that nothing asks it a question, not that it answers badly.

**P2-1 — no invoice can be issued.** Owner action, not engineering:
provide contracting details. Worth stating because it is a first-dollar
blocker nobody had named.

## Definition of done

| criterion | status |
|---|---|
| all five skills tested with representative workloads | **done** — 100/100 |
| 100-job backlog exercised | **done** |
| four-commitment rule verified | **done — and it does not exist per-skill** |
| replenishment tested | **done — none occurs** |
| individual component failures injected | **done** — 7 components |
| cross-component behaviour measured | **done** — no cascade |
| complete crash and restart tested | **done** — between jobs and mid-cycle |
| queue state reconciled | **done** — 1–2 stranded per mid-cycle crash |
| incident supervision verified | **done** — 6/7 record, panel `FAILED` on the 7th |
| no incorrect deliveries escaped | **done — 0** |
| Brain on versus off compared | **done — no difference** |
| accelerated 24-hour simulation | **done** |
| profitability per skill | **partial** — cost modelled, revenue is $0 and cannot be billed |
| overall business profitability | **partial** — same reason |
| recovery outcomes documented | **done** |
| weaknesses prioritised | **done** |

**Not done, and not claimed:** no Windows evidence, so MT5 coexistence
stays uncertified. No real-world certification of any kind — every
customer, job and payment here is simulated, and simulated results are
not a substitute for it.

---

*Regression and mutation were last run at `a7763a5`: 4,722 passed, 44
skipped, 10,085 subtests; 155/155 mutants killed. No production code
changed this round, so those results stand unchanged.*
