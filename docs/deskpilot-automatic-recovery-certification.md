# DeskPilot — automatic resumption of interrupted work: certification

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect was
produced at any point in this work.

---

## 1. The defect, as measured

A job killed mid-verification stayed in `VERIFYING` for ever. Nothing
resumed it, nothing timed it out, nothing escalated it, and it kept its
pipeline slot — so every crash permanently shrank the shop. The same was
true of `EXECUTING` and `READY_FOR_DELIVERY`.

## 2. The classification this repair rests on

Recovery is the part of the system most able to cause the harm it exists
to prevent, because it repeats work. Everything turns on telling these
five cases apart.

| Class | States | What recovery does |
|---|---|---|
| **Safely resumable** | `EXECUTING`, `VERIFYING` | Re-run the work. Both are pure recomputes: nothing has been sent, nothing outside the machine has changed, and repeating them is free of external consequence. |
| **Needs reverification** | `VERIFYING` on resumption | The checker runs again from scratch. Old evidence is never inherited; a job above T1 re-earns its independent review too. |
| **Ambiguous external effect** | `READY_FOR_DELIVERY`, and anything past it | Never repeated. The first attempt may have arrived. Blocked on the owner with an incident. |
| **Owner intervention** | attempts exhausted; review unobtainable; anything reconciliation finds that resumption did not finish | Blocked on the owner, with the state it was found in on the record. |
| **Terminal** | `REJECTED`, `FAILED`, `COMPLETE`, `AWAITING_PAYMENT` | Left alone. Terminal and settled states are not interrupted work. |

`READY_FOR_DELIVERY` is deliberately **excluded** from `RESUMABLE`, and a
test asserts it (`test_a_job_ready_for_delivery_is_not_resumed`). That
single exclusion is what prevents duplicate delivery.

## 3. What was built

`solvent/harness.py`:

* `RESUMABLE = (JobState.EXECUTING, JobState.VERIFYING)`
* `MAX_RESUME_ATTEMPTS = 3`
* `resume_interrupted(...)` — resumes each resumable job through the same
  `_execute_job` a first attempt uses, so the checker runs again and the
  delivery gate is asked again. Returns one record per job considered, so
  a restart is never silent.
* `_review_and_deliver(...)` — a job above T1 owes an independent review
  before the gate will release anything, and that step lives in the
  business cycle rather than in `_execute_job`. Without it a resumed T2
  job re-verified, was refused at the gate for want of a second opinion
  nobody had asked for, and went straight back to `VERIFYING`: a
  resumption that reliably achieved nothing. This asks for the review on
  the resumption path too, through the same function the cycle calls.
* `recover_after_restart(...)` — the composed entry point, in the one
  order that is coherent (see §4).

`solvent/runtime.py::reconcile` already gained `_reconcile_stranded_jobs`
(block anything mid-flight on the owner, with an incident) and
`_reconcile_capacity` (give back slots held by settled work).

`solvent/harness.py::_run_business_cycle` now also disposes of the one
remaining way a job could reach `VERIFYING` and stop: a tier above T1
whose independent review could not be obtained. The cycle used to return
and leave the job there. It now records an incident and blocks the job on
the owner.

## 4. Why the order matters — a defect found by integrating, not by unit tests

Each half passed its own tests. Composed in the documented order they
cancelled out:

> `reconcile` blocks **everything** found mid-flight on the owner. Run
> first, it leaves `resume_interrupted` with nothing to resume, and
> automatic recovery never recovers anything.

`recover_after_restart` fixes the order and states it:

1. **Resume** what is safely recoverable.
2. **Reconcile** everything else — the ambiguous external effects, the
   jobs a resumption could not finish, and any slot held by settled work.

After it returns, no job is in `EXECUTING`, `VERIFYING` or
`READY_FOR_DELIVERY`: each has either moved on or become a question
somebody can see. That is asserted directly
(`test_nothing_is_left_mid_flight_either_way`), and the wrong order is a
registered mutant (`recovery-reconciliation-runs-before-resumption`,
KILLED).

## 5. The four safeguards, and the mutant that proves each

| Safeguard | Mechanism | Mutant | Verdict |
|---|---|---|---|
| No external effect is repeated | `READY_FOR_DELIVERY` excluded from `RESUMABLE` | `recovery-resumes-work-that-has-an-external-effect-pending` | KILLED |
| Retries are bounded | `MAX_RESUME_ATTEMPTS = 3` | `recovery-attempts-are-unbounded` | KILLED |
| The bound survives a restart | attempts counted from the append-only audit log, not memory | `recovery-attempts-are-counted-in-memory` | KILLED |
| Exhausted work is escalated, not dropped | blocked on `OWNER` | `recovery-exhausted-work-is-discarded` | KILLED |
| Nothing stops silently in VERIFYING | block + incident on an unobtainable review | `recovery-an-unreviewable-job-is-left-in-verifying` | KILLED |
| A dead stop is recorded where failures are read | `record_incident` | `recovery-an-unreviewable-job-records-no-incident` | KILLED |
| Recovery runs before reconciliation | `recover_after_restart` | `recovery-reconciliation-runs-before-resumption` | KILLED |
| A resumed T2 job re-earns its review | `_review_and_deliver` | `recovery-a-resumed-job-skips-the-review-it-owes` | KILLED |

**8/8 killed, 0 survived, 0 not applied.**

## 6. Idempotency and duplication

* **Capacity**: reservations are keyed `commit:<job_id>`, so a replayed
  acceptance takes one slot, and `release_job` is idempotent — both the
  cycle and reconciliation may decide the same job has settled.
* **Invoices**: invoicing is keyed by job. Across the eleven-point crash
  matrix and the 100-opportunity acceptance run, duplicate invoices: **0**.
* **Payments**: nothing in this work can confirm a payment. Real revenue
  recorded across every run in this certification: **0 cents**.
* **Audit**: append-only with hash chaining; the chain verified after
  every crash, restart and recovery in the matrix.

## 7. Never, and where it is enforced

| Rule | Enforcement |
|---|---|
| Never approve unverified output | Resumption calls the same `_execute_job` and the same gate; a T2 job re-earns its independent review or is blocked. |
| Never duplicate a delivery or an invoice | `READY_FOR_DELIVERY` is not resumable; invoicing is keyed by job. |
| Never fabricate a payment confirmation | No code path in recovery writes a payment state. |
| Never repeat an ambiguous external effect | Blocked on the owner with the state it was found in. |
| Never silently discard a job to free capacity | Exhausted attempts escalate; `_reconcile_capacity` releases slots only for settled jobs. |
| Preserve complete audit history | Append-only tables with DELETE forbidden by trigger; attempts are counted *from* that history. |

## 8. Tests

* `tests_solvent/test_remediation_p0p1.py` — `StartupReconciliation`,
  `AutomaticResumption`, `AnUnavailableReviewDoesNotStrandTheJob`
  (36 tests in the file).
* `tests_solvent/test_four_repair_integration.py` —
  `AnInterruptionIsRecoveredWithAllFourInPlace` (6 tests), with owner
  signing on and per-skill pools configured.
* The eleven-point crash matrix: see
  `docs/deskpilot-eleven-point-crash-matrix.md`.
