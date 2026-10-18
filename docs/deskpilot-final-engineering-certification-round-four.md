# DeskPilot — final engineering certification

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.13

**No real external effect was produced in any part of this work.** Egress
stayed `simulation_only`. No real customer action was taken, no real
marketplace was contacted, no money moved, nothing was deployed to the
VPS, and MetaTrader 5 was not touched. Every owner approval in every run
was issued by a test approver holding a throwaway key, as an explicit
synthetic test action.

---

## 1. The four repairs

| # | Repair | Status |
|---|---|---|
| 1 | Per-skill capacity pools | **VERIFIED FIXED** |
| 2 | Independent review of `.csv` deliverables | **VERIFIED FIXED** |
| 3 | Owner signing and delivery authorization | **VERIFIED FIXED** |
| 4 | Startup reconciliation and automatic resumption | **VERIFIED FIXED** |

Each has its own document:

* `docs/deskpilot-owner-signing-final-repair.md`
* `docs/deskpilot-automatic-recovery-certification.md`
* `docs/deskpilot-four-repair-integration-test.md`
* `docs/deskpilot-eleven-point-crash-matrix.md`
* `docs/deskpilot-final-100-opportunity-acceptance.md`
* `docs/evidence/deskpilot-final-certification.json`

## 2. What integrating them found

Three defects were found only by running the repairs together, and each
is now covered by a test and a mutant:

1. **Recovery recovered nothing.** `reconcile` blocks everything it
   finds mid-flight, so running it before `resume_interrupted` — the
   documented order — left resumption with nothing to resume. Every
   unit test of each half passed throughout. Fixed by
   `recover_after_restart`, which resumes first and reconciles second.
2. **A resumed T2 job could never finish.** The independent-review step
   lived only in the business cycle, so a resumed job above T1 was
   refused at the delivery gate for want of a second opinion nobody had
   asked for, and returned to `VERIFYING`. Fixed by `_review_and_deliver`,
   shared by both paths.
3. **Automatic resumption had no caller.** `Runtime.start` reconciled
   and nothing else, so the whole of repair 4 would have been a function
   production never ran. Fixed: `Runtime.start` now calls recovery, and
   where the owner has not named a work root it says so plainly instead
   of silently doing less.

The third is the third instance this engagement has found of the same
shape — correct machinery that nothing calls — after `supervised()` and
`Capacity.reserve`. It is now pinned by a mutant
(`recovery-the-runtime-never-resumes-anything`).

Two further defects were found by the crash matrix:

4. **A broken reviewer crashed the cycle.** Owner tooling that returned
   something that was not an approval raised `AttributeError` out of the
   business cycle. It is now a refusal, as a `FailClosed` already was.
5. **A job interrupted before its source was recorded crashed on
   resumption**, with `AttributeError: 'NoneType' object has no
   attribute 'digest'` three functions downstream. Recovery caught it,
   recorded an incident and escalated — nothing was lost — but a crash
   is not a refusal. `_execute_job` now says what is missing.

## 3. The gates, as measured

| Gate | Result |
|---|---|
| Full regression suite | **4,793 passed, 44 skipped, 10,085 subtests, 0 failed** |
| Mutation testing | **173 / 173 killed** (0 survived, 0 not applied) |
| Eleven-point crash matrix | **11 / 11 seams reached**, 0 stranded |
| Four-repair integration | **17 tests passing** |
| 100-opportunity acceptance | 52 delivered, 0 unauthorized, 0 stranded |

The mutant count grew from 155 to 173: eighteen new ones covering the
repaired owner-signing and recovery paths. The historical 155 were
**not** assumed to cover the new code; every new control has a mutant
named after it, and the table in each repair's document says which.

Three survived on first run across this engagement. None of them meant
unsafe behaviour; all three meant a test whose name claimed more than it
checked, and in each case the test was strengthened and the mutant
re-run to a kill. No acceptance criterion was changed after seeing a
result.

## 4. The four counts the assignment asks for

| Count | Value | How it was measured |
|---|---|---|
| **Unauthorized deliveries** | **0** | 52 `delivery.completed` events against 52 `owner.approval_consumed` events in the 100-opportunity run; the set difference is empty |
| **Duplicate invoices** | **0** | invoiced job ids vs. their distinct set, in the acceptance run and at every crash seam |
| **Fabricated payments** | **0** | `real_revenue_cents` is 0 in every run; nothing in this work can confirm a payment |
| **Stranded jobs** | **0** | no job in `EXECUTING`, `VERIFYING` or `READY_FOR_DELIVERY` after the acceptance run or after recovery at any of the eleven seams |

## 5. Remaining findings, not claimed as fixed

1. **A crash at worker execution leaves a job in `ACCEPTED`, holding its
   pipeline slot.** Neither resumption nor reconciliation touches it:
   there is no partial work to repeat and nothing in doubt, but nothing
   will start it either. Closing this means giving `ACCEPTED` a
   start-or-escalate rule, which belongs to admission rather than to
   recovery.
2. **A crash during invoice creation leaves a delivered job with no
   invoice.** Recovery does not create the missing one — it will not
   invent a financial record — so the job sits in `AWAITING_PAYMENT`
   with nothing owed on paper. Visible, not silent, and not money lost
   to a double charge; still work the owner has to finish.
3. **`.md` deliverables above T1 cannot be reviewed automatically.** No
   independently authored Markdown reader exists in this build, so three
   of the hundred jobs blocked on the owner for a human review. Correct
   refusal, and a real limit on what document-processing can deliver
   unattended at C_HIGH.
4. **`check_preserve_columns` compares against the source's distinct
   rows**, so two rows differing only in whitespace are distinct until
   the worker trims them, and a brief combining trim with dedupe and
   protected columns can fail a check the work actually satisfied. Found
   while writing the integration fixture; not on the repair path and not
   fixed here.

## 6. What this certification does not say

* **Nothing here is a Windows or MetaTrader 5 claim.** Every number was
  measured on Linux x86_64, CPython 3.11.
* **Nothing here is a revenue result.** Real revenue recorded across
  every run: 0 cents. The 52 deliveries are synthetic contracts inside a
  sandbox.
* **Nothing here is evidence that a person approved anything.** Every
  owner approval was issued by a test approver holding a throwaway key.
* **Nothing was deployed.** The VPS was not touched, MT5 was not
  touched, and no live customer acquisition, payment or marketplace
  action was enabled.
