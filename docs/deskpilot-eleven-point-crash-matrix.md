# DeskPilot — the eleven-point crash matrix

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect was
produced at any point in this work.

---

## 1. Method, and what counts as a test

Eleven failure points, one child process each. The child runs a real
contract against a real on-disk database and **dies inside the named
seam** — `os._exit(137)` from within the call, which is as abrupt as
SIGKILL and lands exactly where it is meant to. A timer-based kill
cannot prove which seam it hit; this can, because the child writes a
`REACHED` marker immediately before dying and that file survives the
kill.

Two rules, stated before the results:

* **A crash scenario does not count as tested if the job never reached
  the injected failure point.** The first version of this matrix reached
  10 of 11: `approval_signing` was never reached, because no owner key
  was configured and the cycle therefore never asked the owner to sign
  anything. That was reported as *not tested*, not as a pass. Every seam
  in the table below is now run with signing configured and an approver
  supplied, which is what makes that seam reachable.
* **A job safely refused is not a job successfully recovered.** The two
  are counted in separate columns and never added together.

The parent process then reopens the database, runs the composed recovery
path (`recover_after_restart`: resume what is safely recoverable, then
reconcile the rest), and records the job census, the audit chain, the
invoices, the payments, the pipeline slots and the open incidents.

| Seam | Injected into |
|---|---|
| admission | `orchestrator.intake` |
| worker execution | `orchestrator.begin_execution` |
| output persistence | `orchestrator.register_artifact` |
| checker execution | `orchestrator.submit_for_verification` |
| verification-result persistence | `audit.record_verification` |
| owner approval signing | `owner.issue` |
| delivery preparation | `orchestrator.mark_verified` |
| delivery acknowledgment | `orchestrator.record_delivery` |
| invoice creation | `ledger.open_payment` |
| payment reconciliation | `ledger.set_payment_state` |
| queue replenishment | `qualification.select` |

## 2. Result

**11 of 11 seams reached and tested. 0 stranded.**

| Measure | Value |
|---|---|
| Seams reached and tested | 11 / 11 |
| Seams not reached | none |
| Databases reopened after the kill | 11 / 11 |
| Audit chains verifying after recovery | 11 / 11 |
| **Resumed** (work picked up and finished) | **3** |
| **Safely refused** (escalated, not repeated) | **7** |
| Nothing to recover (the crash preceded any job) | 1 |
| **Stranded** | **0** |
| Duplicate invoices | **0** |
| Fabricated payments | **0** |
| Real revenue recorded | **0 cents** |
| Mean restart time | 0.0133 s |
| Mean recovery time | 0.0144 s |

## 3. Seam by seam

| Seam | State at restart | After recovery | Outcome |
|---|---|---|---|
| admission | *no job yet* | *no job* | nothing to recover |
| worker execution | `ACCEPTED` | `ACCEPTED` | safely refused |
| output persistence | `EXECUTING` | `BLOCKED` (owner) | safely refused |
| checker execution | `EXECUTING` | `AWAITING_PAYMENT` | **resumed** |
| verification-result persistence | `VERIFYING` | `AWAITING_PAYMENT` | **resumed** |
| owner approval signing | `READY_FOR_DELIVERY` | `BLOCKED` (owner) | safely refused |
| delivery preparation | `VERIFYING` | `AWAITING_PAYMENT` | **resumed** |
| delivery acknowledgment | `READY_FOR_DELIVERY` | `BLOCKED` (owner) | safely refused |
| invoice creation | `AWAITING_PAYMENT` | `AWAITING_PAYMENT` | safely refused |
| payment reconciliation | `AWAITING_PAYMENT` | `AWAITING_PAYMENT` | safely refused |
| queue replenishment | `QUALIFYING` | `QUALIFYING` | safely refused |

Reading it:

* The three **resumed** cases are all mid-recompute states. Nothing had
  been sent, the work was re-run, the checker ran again, the delivery
  gate was asked again with a fresh signed approval, and the job reached
  `AWAITING_PAYMENT`. Each took about 14 ms.
* The two `READY_FOR_DELIVERY` cases — a crash while signing, and a
  crash between the gate allowing the send and the acknowledgment being
  written — are the ambiguous ones. They are **never** repeated: the
  first attempt may have arrived. Both were blocked on the owner with an
  incident, which is the correct answer and is not a recovery.
* `output persistence` is a crash inside the call that records the
  *source* artifact. On resumption the job cannot say what it was given,
  so it is refused with that reason and blocked on the owner. See §4.
* The `AWAITING_PAYMENT` and `QUALIFYING` cases are not interrupted
  work: the job is settled or not yet started, so recovery leaves them
  alone. Payment reconciliation shows one invoice, which is the invoice
  that already existed — not a second one.

## 4. Two defects this matrix found

**A resumption could crash instead of refusing.** A crash inside the
call that records the source artifact leaves a job with no `SOURCE` on
record. Resumption then raised `AttributeError: 'NoneType' object has no
attribute 'digest'` three functions downstream. Recovery handled it
correctly — the exception was caught, an incident was recorded, and
reconciliation escalated the job — so nothing was lost and nothing
unverified shipped. But a crash is not a refusal. `_execute_job` now
checks and says what is missing. Test:
`test_a_job_whose_source_was_never_recorded_is_refused`; mutant
`recovery-a-job-with-no-recorded-source-crashes-instead`, KILLED.

**The signing seam was unreachable in the first matrix.** With no owner
key configured the cycle never asks the owner to sign, so the seam was
reported as *not tested* rather than as a pass. Every run in this table
has a key and an approver, and all eleven are reached.

## 5. One gap left open, stated rather than closed

A crash at **worker execution** leaves the job in `ACCEPTED`, holding
its pipeline slot. `ACCEPTED` is not in the interrupted set, so neither
resumption nor reconciliation touches it: there is no partial work to
repeat and nothing in doubt, but the slot stays taken and nothing will
start the job. It is visible (the job is in `ACCEPTED` with no
execution grant) and it is not silent in the `VERIFYING` sense, but it
will not move on its own. Closing it means giving `ACCEPTED` a
start-or-escalate rule, which is a change to admission rather than to
recovery, and it is listed as a remaining finding rather than claimed as
fixed.
