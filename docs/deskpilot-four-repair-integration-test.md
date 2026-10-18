# DeskPilot — the four engineering repairs, verified together

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect was
produced at any point in this work.

---

## 1. Why a separate test

Each repair has its own tests, and each passed. That is a weaker claim
than it looks, because all four touch the same path in order:

1. **Per-skill capacity pools** — qualification reserves a slot in the
   pipeline for the skill the job needs.
2. **Independent review of `.csv`** — a job above T1 needs a second
   opinion from a reader DeskPilot did not write.
3. **Owner signing on delivery** — the gate needs the owner's signed
   approval, or a bounded standing grant.
4. **Startup reconciliation and automatic resumption** — a restart has
   to pick the work back up without re-sending anything or losing the
   slot.

Integrating them found a defect that neither half's unit tests could
see, described in §3.

`tests_solvent/test_four_repair_integration.py` — **15 tests, all
passing.** One shop: a generated signing key, four slots per pipeline, a
C_HIGH csv job (so the review is genuinely required), and an owner
approver that issues one explicit approval per delivery.

## 2. What is asserted

**`AllFourHoldOnOneJob`** — one job through all four:

| Assertion | Repair |
|---|---|
| The job delivered | all four |
| The pipeline slot was taken and given back | 1 |
| `independent-review` is in the completed stages, by an "independently authored" reader | 2 |
| `owner.approval_consumed` is in the job's audit trail | 3 |
| No job in `EXECUTING`/`VERIFYING`; no duplicate invoice | 4 |

**`AnInterruptionIsRecoveredWithAllFourInPlace`** — the restart path with
signing on and the pool accounted for:

* the interrupted job is picked up and leaves `VERIFYING`;
* it obtains **its own** second opinion rather than inheriting the first
  attempt's;
* it is not invoiced twice;
* without an approver it is `HELD`, not delivered — recovery is not a way
  around the gate it would otherwise meet;
* the pipeline is not over its own ceiling afterwards;
* whether resumed or refused, nothing is left in `EXECUTING`,
  `VERIFYING` or `READY_FOR_DELIVERY`;
* the audit chain verifies after the whole sequence.

**`TheRepairsDoNotCancelEachOther`** — each repair's *refusal* still
works with the other three present: a full pipeline still refuses a fifth
job; an unsigned delivery is still refused; an unreviewable job still
blocks on the owner rather than stranding.

## 3. The defect integration found

Run in the order the documentation gave, the two halves of repair 4
cancelled out. `runtime.reconcile` blocks **everything** found mid-flight
on the owner — correct as a safety net — so running it first left
`resume_interrupted` with nothing to resume. Automatic recovery recovered
nothing, and every test of each half separately still passed.

Fixed by `harness.recover_after_restart`, which fixes the order (resume
what is safely recoverable, then reconcile the rest) and says why in its
docstring. The wrong order is now a registered mutant
(`recovery-reconciliation-runs-before-resumption`) and it is KILLED.

A second defect surfaced the same way: a resumed job above T1 re-verified
and was then refused at the delivery gate for want of an independent
review, because that step lived only in the business cycle. It went
straight back to `VERIFYING`. Fixed by `_review_and_deliver`, shared by
both paths; mutant `recovery-a-resumed-job-skips-the-review-it-owes`,
KILLED.

A third, smaller one: owner review tooling that returned something that
was not an approval raised `AttributeError` out of the cycle instead of
being refused. `_obtain_independent_review` now treats broken tooling the
same way it treats a refusal — there is no second opinion either way.

## 4. Two honest limits recorded by this test

* **Repair 2 gives `.csv` a genuinely separate parser; it does not invent
  checkable content.** A brief of "drop duplicates, trim whitespace,
  parses as csv" has nothing a second implementation can disagree about
  beyond the file parsing, and the reviewer refuses it — correctly. The
  integration brief therefore adds a `preserve_columns` requirement, and
  the comment in the test says why.
* **`check_preserve_columns` compares against the source's *distinct*
  rows**, and two rows differing only in whitespace are distinct until
  the worker trims them. The fixture uses exact duplicates to stay clear
  of that interaction, which is a separate question and is not answered
  here.
