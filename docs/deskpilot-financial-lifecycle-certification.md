# DeskPilot — full financial lifecycle certification

**Testing and simulation only.** No production file changed, no capacity
semantics altered, no job limit raised, no real invoice issued, no live
Stripe or wallet connected, no funds moved, no real customer contacted,
nothing deployed, the Action Gate and the Brain untouched.

Date 2026-10-17. Baseline `b08d52e`, working tree verified clean before
and after. Evidence:
`docs/evidence/financial-lifecycle-certification.json`.

**Nothing fake was counted as earnings.** Every payment carries
verification method `SIMULATED:fixture`, and the Ledger's own
`real_revenue_cents()` reads **0** in every single run — the system
certifying, in its own accounting, that no simulated money became
revenue.

---

## The correction that unblocked this run

My previous report said *"no invoice can be issued in any run"* and
reported **$0 billable**. That was true of my harness and I had the
cause right — policy refuses an invoice without contracting details —
but I drew too strong a conclusion from it. Supplying **synthetic**
contracting details (authorised this round) clears the gate completely:

| | as shipped | with synthetic party |
|---|---|---|
| `CLIENT_AGREEMENT` | refused: needs legal_name, email | **allowed** |
| `INVOICE` | refused: needs legal_name, address, email | **allowed** |
| `PAYMENT_RAIL` | refused: + tax_reference | **allowed** |

**Preflight: 5 of 5 skills pass**, each running all 14 cycle stages
through to `PAID`:

| skill | stages | invoiced | collected | state |
|---|---:|---:|---:|---|
| csv-cleanup | 14 | $400 | $400 | PAID |
| spreadsheet-automation | 14 | $400 | $400 | PAID |
| document-processing | 14 | $400 | $400 | PAID |
| data-entry | 14 | $400 | $400 | PAID |
| report-builder | 14 | $400 | $400 | PAID |

The financial pipeline works end to end. It simply cannot start until
the owner says who DeskPilot legally is.

## The 100 contracts

Five skills × 20, four price bands, **0 of 100 missing a mandatory
field**, prices spanning 47× from $75 to $3,500.

| | |
|---|---:|
| contracts offered and accepted for evaluation | 100 |
| **delivered, invoiced, collected** | **43** |
| quoted | $129,375.00 |
| **invoiced** | **$10,375.00** |
| **collected (simulated)** | **$10,375.00** |
| operating cost | $1,351.38 |
| **contribution profit** | **$9,023.62** |
| **real revenue** | **$0.00** |

Reconciliation, checked rather than asserted: invoiced − collected =
**0**; profit = collected − cost **exactly**; 57 undelivered contracts
contributed **$0** collected and have no payment record at all.

**Quoted is not invoiced and invoiced is not cash.** $129,375 was
quoted; $10,375 reached an invoice; $10,375 settled. The gap is the
story.

## Why only 43 of 100 — and it is not throughput

| band | n | delivered | collected | profit | margin |
|---|---:|---:|---:|---:|---:|
| small ($75) | 25 | 21 | $1,575.00 | $1,365.42 | 86.7% |
| standard ($400) | 25 | 22 | $8,800.00 | $7,658.20 | 87.0% |
| **large ($1,200)** | 25 | **0** | $0.00 | $0.00 | — |
| **premium ($3,500)** | 25 | **0** | $0.00 | $0.00 | — |

**Every large and premium contract was refused**, 17 per band at
verification-eligibility and 5 at independent review:

> *"this job needs T2_INDEPENDENT_REVIEW before it may be delivered"*

**$117,500 of the $129,375 quoted — 91% — sits in bands DeskPilot
currently cannot serve.** The revenue ceiling is set by the
**verification tier**, not by speed, capacity or skill.

The important detail: it is **T2, not T3**. T3 would be structural —
nothing here can manufacture an external fact.

I originally wrote that T2 "needs an independent reviewer, which is a
configuration the owner can provide" and called the ceiling
**unlockable**. I then tested that claim instead of leaving it, and it
does not hold: **all three routes to T2 are blocked, each for a
different reason.** See *The three T2 routes* below. I also said I had
not established whether the reviewer's rejections were real or false
positives. They are **false positives**, and that is now proven.

### The three T2 routes, tested

**Route 1 — owner review.** Needs a signed approval naming the artifact
digest, so it needs `SOLVENT_OWNER_KEY`. Unset, the refusal is explicit:
*"owner review: unavailable (no owner signing key)"*.

Setting the key does not help, and this is the part worth knowing:
**with a signing key configured, every delivery is refused** —

> *action gate refused delivery: C2_EXTERNAL_COMMUNICATION exceeds any
> preauthorization and an Owner Channel is configured, so approvals must
> be signed by it*

`harness.py` mints an **unsigned** approval for delivery and no
production path mints a signed one. Measured: with the key set, **0 of
100 contracts delivered**, against 43 without it — T1 work included. No
existing test runs a full business cycle with a signing key configured,
which is why this has not been seen before. **Blocked both ways.**

**Route 2 — differential review of a `.csv` deliverable.** The refusal
is categorical:

> *differential review (csv): unavailable (no independently authored
> reader for .csv is known to this build, so a second automated opinion
> would be Solvent reading its own output twice)*

The reader does not exist. This covers csv-cleanup, data-entry and
report-builder. **Blocked.**

**Route 3 — differential review of an `.xlsx` deliverable.** Here the
reader exists, runs, and **disagrees**:

> *the independent reader disagrees with the deliverable: Client ref
> values all came from the source: 3 value(s) the source never had*

That reads like the worker inventing data, which would be the most
serious finding in this engagement. **It is not.** The job's own
checklist requires `consolidate_sheets {'sheets': ['Q1 data', 'Q2
data']}`, and the reader compares the consolidated output against
**only the first sheet** of the source workbook:

| | Client ref values |
|---|---|
| source `Q1 data` | CR-2266, CR-3852, CR-4243, CR-8097, CR-8196 |
| source `Q2 data` | CR-4181, CR-4952, CR-5486, CR-6484, CR-8064 |
| output | the eight that survived the row filters, from **both** |
| missing from the **first sheet alone** | **3** — CR-5486, CR-6484, CR-8064 |
| missing from the **union of both sheets** | **0** |

Exactly the three the reviewer complained about, and **not one value
the worker did not take from a source sheet.** It is a **false positive
in the verifier**, and it blocks every T2-tier spreadsheet job that
consolidates sheets. The worker is correct; the reviewer is wrong.

**So the ceiling is not merely unconfigured — it is three separate
defects**, and that is why no large or premium contract completed.

Per skill, margin is flat at **87%** across all five; the only skill
that differs is csv-cleanup (3 delivered, not 10), because 13 of its 20
scenarios correctly stop to ask the client a question.

## Profit per contract, hour, day, week, month

Measured: **$209.85 mean contribution profit per billable contract**,
87% margin, 0.048 s mean execution.

**These periods are demand-bounded arithmetic, not forecasts.** They
state what the measured economics yield at an assumed arrival rate.
DeskPilot has no customers, so today's real rate is zero and every
figure below is $0.00 in practice.

| arrival rate | per hour | per 8-h day | per 5-day week | per 22-day month |
|---|---:|---:|---:|---:|
| 1 contract/hour | $209.85 | $1,678.81 | $8,394.07 | $36,933.89 |
| 4 contracts/hour | $839.41 | $6,715.25 | $33,576.26 | $147,735.55 |
| 10 contracts/hour | $2,098.52 | $16,788.13 | $83,940.65 | $369,338.87 |

The bottom row is included only for completeness and I would not plan
against it: 80 contracts a day exceeds both plausible freelance demand
and the 20-job committed ceiling.

**Compute is not the constraint at any of these rates.** 43 contracts
executed in 0.048 s each. One job an hour uses about five thousandths
of a percent of the machine.

## Crashes through the financial lifecycle

Four crashes injected at financial stages while money was already on
the books:

| crashed at | raised | earlier job's money | real revenue | incident | panel | audit chain |
|---|---|---|---|---|---|---|
| delivery | InjectedFault | **unchanged** | $0 | 1 | DEGRADED | intact |
| ledger | InjectedFault | **unchanged** | $0 | 1 | DEGRADED | intact |
| delivery gate | InjectedFault | **unchanged** | $0 | 1 | DEGRADED | intact |
| verification | InjectedFault | **unchanged** | $0 | 1 | DEGRADED | intact |

**No crash moved, created or destroyed money.** A crash mid-lifecycle
cost the in-flight contract and nothing else.

## Restart: the books survive exactly

Six contracts booked, the application closed, the database reopened:

| | before | after |
|---|---:|---:|
| jobs | 6 | **6** |
| invoiced job ids | 1 | **1** |
| collected | $75.00 | **$75.00** |
| real revenue | $0 | **$0** |
| audit chain | intact | **intact** |

**Every figure survived identically.** Contracts, invoices, payment
records and commitments all reconcile across a restart.

## The capacity defect, priced

The defect from the previous report — `COMPLETE` is not terminal, so a
finished job holds its capacity slot forever — now has a number:

| | delivered | collected | profit |
|---|---:|---:|---:|
| one long-lived shop (as shipped) | **3** | $875.00 | $761.22 |
| a virgin system per contract | 43 | $10,375.00 | $9,023.62 |
| **cost of the defect** | **−40 contracts** | **−$9,500.00** | **−$8,262.40** |

**The capacity defect destroys 92% of the contribution profit available
in these 100 contracts.** It is the single most expensive defect
measured in this entire engagement, and the fix is capacity accounting
the owner must authorise.

## Brain on versus Brain off

| | Brain off | Brain on |
|---|---:|---:|
| delivered | 17 | **17** |
| collected | $4,200.00 | **$4,200.00** |
| cost | $546.94 | **$546.94** |
| profit | $3,653.06 | **$3,653.06** |

**Difference: $0.00, to the cent.** This repeats the previous round's
finding with money attached, and the cause is unchanged:
`run_business_cycle` ranks `[decision]` — a list of exactly one — so
`_client_factor`, a relative multiplier, has nothing to be relative to.

## Hard acceptance criteria

| # | criterion | result |
|---:|---|---|
| 1 | five distinct contract preflight jobs pass | **pass** — 5/5, 14 stages each |
| 2 | every contract has the mandatory fields | **pass** — 0 of 100 missing |
| 3 | prices vary meaningfully | **pass** — $75–$3,500, 47× |
| 4 | all five skills represented | **pass** — 20 each |
| 5 | contract validation path exercised | **pass** — 13 stopped at `contract` |
| 6 | invoice system exercised | **pass** — 43 invoiced |
| 7 | payment-state system exercised | **pass** — ESTIMATED→INVOICED→PAID |
| 8 | confirmations reconciled | **pass** — invoiced − collected = 0 |
| 9 | invoices not counted as cash | **pass** — four separate buckets |
| 10 | unpaid not counted as collected | **pass** — 57 undelivered, $0, no record |
| 11 | costs deducted | **pass** — $1,351.38 |
| 12 | profit per job | **pass** |
| 13 | profit per skill | **pass** |
| 14 | profit per contract size | **pass** |
| 15 | crashes across the financial lifecycle | **pass** — 4 stages |
| 16 | complete application restart | **pass** — every figure identical |
| 17 | capacity defects measured | **pass** — −$8,262.40 |
| 18 | Brain compared | **pass** — $0.00 difference |
| 19 | 24-hour simulation | **pass** |
| 20 | all financial totals reconcile | **pass** |

**All twenty met. The financial simulation is declared successful**,
under the stated caveat that every dollar is synthetic.

## The two questions

**How much could DeskPilot realistically generate and retain with a
continuous supply of paying customers?**

On measured economics: **$209.85 contribution profit per billable
contract at an 87% margin.** At one contract an hour that is ~$1,679 a
day and ~$36,900 a month. The margin is real and unusually high because
delivery costs almost nothing — $0 in model calls, ~$31 of modelled
operating cost per contract at fixture rates.

But today it would retain **$10,375 of $129,375 offered — 8%** — and
with the capacity defect in play, **$875, under 1%**.

**What breaks, what recovers, and what stops that revenue being real?**

*Recovers well:* every crash tested. No money moved, created or
destroyed; the books survive restart exactly; incidents record; the
audit chain never broke in any run.

*Breaks, in order of money:*

1. **Capacity never released — −$8,262.40 per 100 contracts (92%).**
   `COMPLETE` is not terminal. Needs owner authorisation; I did not
   touch it.
2. **All three T2 routes blocked — $117,500 of quoted work (91%)
   unservable.** Not one defect but three: no `.csv` reader exists; the
   `.xlsx` reader false-positives on consolidated sheets; and enabling
   the owner signing key, which is the third route, stops delivery
   altogether. Still the largest revenue unlock available, and now with
   three specific fixes attached rather than a configuration note.
3. **Jobs stranded mid-verification** (previous report) — each crash
   permanently shrinks the shop.
4. **The Brain contributes $0.** Not broken; never asked a question.

*Stops it being real:* no payment rail (`money_can_move: False`), **no
contracting details — which this round proves blocks invoicing
entirely**, `max_committed_jobs = 0` as shipped, and no customer flow.
None of those four is an engineering defect — they are owner decisions.

The T2 blockers **are** engineering defects, and they are what caps the
business at small and standard work. Documented here for approval
rather than fixed, since changing them is outside this round's
authority:

1. **`independent.py` provenance check reads only the first source
   sheet.** A correctly consolidated output is flagged as inventing
   values. Fix: compare against the union of the sheets the checklist
   named. Small, and it unlocks T2-tier spreadsheet work.
2. **No independently authored `.csv` reader.** Fix: adopt one, or
   accept that `.csv` deliverables cannot reach T2 without a human.
   This is a dependency decision.
3. **An owner signing key stops delivery.** `harness.py` mints an
   unsigned approval for a C2 external effect. Fix: mint a signed one
   when a channel is configured. Until then the two features are
   mutually exclusive, and no test covers the combination.

---

*A follow-up pass tested the "unlockable" claim this report first made
about the T2 ceiling and found it wrong in a way that mattered; the
section above records what the tests showed. Evidence:
`docs/evidence/t2-tier-revenue-blockers.json`.*

*Regression and mutation stand from `a7763a5`: 4,722 passed, 44
skipped, 10,085 subtests; 155/155 mutants killed. No production code
changed in this round or the last, so those results carry forward.
MT5 coexistence remains uncertified — all measurements are Linux.*
