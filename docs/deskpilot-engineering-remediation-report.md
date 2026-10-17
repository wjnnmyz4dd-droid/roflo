# DeskPilot — P0/P1 engineering remediation

**Implementation round.** Two of the six problem areas are repaired and
tested; four are stopped with their exact blockers, as the brief
directs. No live financial transaction, no marketplace bidding, no real
contract, no real customer delivery, no VPS deployment. The Action
Gate, Governor, Brain, worker/checker pairs, incident supervisor,
contracting engine, ledger and capacity controller are all reused, none
replaced.

Date 2026-10-17. Baseline `3c7d6d0`, verified clean before editing.
Evidence: `docs/evidence/deskpilot-repair-and-retest-results.json`.

---

## Headline

| | before | after |
|---|---:|---:|
| contracts delivered, **one long-lived shop** | 3 | **27** |
| collected (simulated) | $875.00 | **$27,700.00** |
| contribution profit | $761.22 | **$24,118.56** |
| contracts delivered, fresh system each | 43 | **53** |
| collected | $10,375.00 | **$33,875.00** |
| profit | $9,023.62 | **$29,489.12** |
| **real revenue** | $0.00 | **$0.00** |

**A 31× profit improvement in the arrangement that matters** — one
long-lived shop is how a business actually runs — and for the first
time in this engagement, **large ($1,200) and premium ($3,500)
contracts completed at all**: 5 of 25 in each band.

## Repair 1 — completed jobs no longer hold capacity

`JobState.is_terminal` covers only `REJECTED` and `FAILED`, and
`_committed_count` counted every non-terminal job. A finished,
delivered, paid job therefore held its slot for ever, and the shop
seized after `max_concurrent_jobs` lifetime successes.

The fix does **not** change `is_terminal` — other code depends on it,
and the brief forbids changing unrelated rules. Instead occupancy is
now stated in its own right:

```python
OCCUPIES_CAPACITY = frozenset({
    JobState.AWAITING_OWNER_APPROVAL, JobState.ACCEPTED,
    JobState.EXECUTING, JobState.BLOCKED, JobState.VERIFYING,
    JobState.READY_FOR_DELIVERY, JobState.REVISION_REQUESTED,
})
```

`is_terminal` asks *can this job still change*; occupancy asks *is work
still owed*. They are different questions and conflating them cost the
business most of its throughput.

**One judgment call, stated rather than buried.** `AWAITING_PAYMENT`
does not occupy a processing slot. I first wrote the test asserting the
opposite and changed it on reflection, not on results: the work has
shipped, so labour is not owed, and holding a slot would re-seize the
shop after 20 unpaid invoices — the same defect in a new place. The
unpaid-exposure limit already governs outstanding money and does it
better, by amount rather than by job count. **Releasing a slot is not
forgiveness of a debt**: the payment row, the invoice and the audit
trail are untouched.

**Acceptance test, run at the shipped ceiling of 3:** 10 contracts
offered, **10 delivered**. Before the repair the same shop managed 3
in its lifetime.

## Repair 2 — the spreadsheet reviewer no longer calls consolidation fabrication

`_read_independently` read `book[book.sheetnames[0]]`. A brief that
says *consolidate these two sheets* has two sources, and judging the
output against one of them reported three legitimate values —
`CR-5486`, `CR-6484`, `CR-8064` — as *"value(s) the source never had"*.

Now `_authorised_sheets()` reads the scope the checklist declares
through `consolidate_sheets`, and `_supplied_values()` unions those
sheets, each read with its own header because consolidated sources need
not agree on column order.

**Three guards, each its own test, so the repair cannot degrade into
"accept anything":**

| guard | behaviour |
|---|---|
| a value in **no** authorised sheet | still fails |
| a value from a sheet the checklist **never named** | still fails |
| a brief with no consolidation | unchanged first-sheet behaviour |

## Stopped for an owner decision

The brief says to stop a repair that needs another decision, document
it, and carry on. Four did.

### Per-skill commitment pools (Phase 3) — blocked on a schema change

I implemented this, tested it against the data, **found it inert, and
backed it out rather than ship it.**

A job does not record which capability it belongs to. The `jobs` table
has no such column, and `job_class` — the only candidate — is already
the Governor's pricing-calibration key, so reusing it would quietly
change pricing. Per-skill counting would therefore have returned zero
for every skill and the cap would never have bound: a control that
silently does nothing, which is the exact failure shape this engagement
keeps finding.

**Needs:** a new `jobs` column for the capability, set at intake from
the opportunity's declared need, plus a migration. It touches both the
capacity controller and the pricing key space, so it wants your
sign-off.

### CSV independent review (5B) — blocked on a dependency

Worker and checker both use stdlib `csv`, so it cannot be the
independent reader — a differential review using it would be Solvent
reading its own output twice, which the code says in as many words. No
third-party CSV reader is installed: `pandas`, `polars`, `pyarrow`,
`csvkit` and `clevercsv` are all absent.

This needs a **new third-party dependency**, against the repository's
zero-dependency principle. Fail-closed behaviour is preserved and CSV
T2 stays blocked. **Cost: 34 of 100 contracts still stop at
verification-eligibility for this reason alone.**

### Owner signing versus delivery (5C) — not attempted

`harness.py` mints an *unsigned* approval for the C2 delivery effect,
so configuring an Owner Channel makes the gate refuse every delivery
(measured: 0 of 100 with a key set). Fixing it changes Action Gate
authorisation flow and deserves its own focused change and test pass
rather than being folded into this one.

### Startup reconciliation (Phase 2) and Brain ranking (Phase 4) — not done

Unchanged and unclaimed. Jobs killed mid-verification still strand in
`VERIFYING`; `run_business_cycle` still ranks a list of one.

## Attributing the improvement

The two arrangements isolate the two repairs, which is why both were
run:

| | delivered | what it isolates |
|---|---|---|
| fresh system per contract | 43 → **53** (+10) | the 5A fix alone — no capacity pressure exists in this arm |
| one long-lived shop | 3 → **27** (+24) | both repairs together |

So the capacity repair accounts for roughly **+14 contracts** and 5A
for **+10**. That is an implied figure from two arms rather than a
separately ablated run, and I am labelling it as such.

## What still stops the money

| stop | contracts | cause | owner or engineering |
|---|---:|---|---|
| `verification-eligibility` | 34 | no independent `.csv` reader | **owner** — dependency decision |
| `contract` | 13 | client ambiguity — correct behaviour | neither |
| capacity | 0 | **repaired** | — |
| spreadsheet T2 false positive | 0 | **repaired** | — |

Unchanged beyond this round: no payment rail, `max_committed_jobs = 0`
as shipped, no customer flow, and **real revenue of $0.00** in every
run — `Ledger.real_revenue_cents()` reads 0 in both arms, which is the
system certifying that no simulated money was counted as earnings.

## Scope I did not reach

The brief asks for fifteen phases and ten separate reports. This round
delivers two repairs, their tests, the 100-contract retest and this
report. **Not done, and not claimed:** the independent audit of the
original 43 jobs, the per-stage timing certification, the five-worker
concurrency study, the full crash matrix re-run, and the 24-hour model
after repairs. Those remain open, and the measurements from `b08d52e`
and `91987f5` stand as the most recent evidence for them.

## Regression

| | before (`a7763a5`) | after |
|---|---:|---:|
| passed | 4,722 | **4,728** |
| failed | 0 | **0** |
| skipped | 44 | 44 |
| subtests | 10,085 | 10,085 |

The delta is exactly the 6 new tests in
`tests_solvent/test_remediation_p0p1.py`.

**One failure on the first run, caused by me and worth recording.**
`QualificationOwnsNoEconomics` scans `qualification.py` for economic
terms, to stop a second financial authority growing there. My new
docstring used the word "calibration" while explaining why `job_class`
could not be reused, and tripped it. The test is right; I reworded the
comment rather than touch the test.

The 44 skips are the pre-existing egress-guard subprocess suite and are
**not counted as passed**.

MT5 coexistence remains **uncertified** — everything here is Linux.
