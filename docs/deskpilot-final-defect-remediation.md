# DeskPilot — final defect remediation

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11 · Base commit
`4819125`

No real external effect at any point. See
`deskpilot-final-integration-certification.md` for the standing
constraints and the integrated run.

---

## Summary

| # | Defect | Status | Detail |
|---|---|---|---|
| A | A crash during worker execution leaves a job in `ACCEPTED` holding its slot | **VERIFIED FIXED** | `deskpilot-accepted-recovery-certification.md` |
| B | A crash during invoice creation leaves a delivered job without an invoice | **VERIFIED FIXED** | `deskpilot-invoice-recovery-certification.md` |
| C | Markdown deliverables above T1 have no independent review path | **VERIFIED FIXED** | `deskpilot-markdown-t2-certification.md` |
| D | `check_preserve_columns` fails legitimate trim-then-dedupe work | **VERIFIED FIXED** | `deskpilot-transform-order-certification.md` |
| E | A job delivered by *recovery* is never priced, so the money goes to a person | **VERIFIED FIXED** | found by the acceptance run; §3 of the integration certification |

Additional defects discovered during this round: **1** (E).
Additional defects repaired: **1**.

Overall certification: **PARTIAL**. Findings A-E are verified fixed and the integrated acceptance passed, but both sealed examinations are invalidated and could not be re-sealed in this session. See the last section of this document.

## Per-defect record

### A — interrupted at the start of the work

* **Reproduction**: kill inside `orchestrator.begin_execution`; the job
  stays in `ACCEPTED`, holding its slot, and nothing restarts it.
* **Root cause**: `ACCEPTED` is in neither the reconciliation set nor the
  resumable set, and the 120-minute state timeout only escalates.
* **Source changed**: `solvent/harness.py` (`RESUMABLE_ON_EVIDENCE`,
  `CONSIDERED`, `_why_not_startable`), `solvent/governor.py`
  (`grant_for`).
* **Tests added**: `tests_solvent/test_accepted_recovery.py` (12),
  `tests_solvent/test_governor_grant_lookup.py` (5).
* **Mutants added**: 4, all KILLED.

### B — delivered, with nothing owed on file

* **Reproduction**: kill inside `ledger.open_payment`; the job is
  delivered, in `AWAITING_PAYMENT`, with no payment row and nothing to
  reconcile against.
* **Root cause**: opening the payment row was the first durable write
  about the money, so its absence left no record that anything was owed.
* **Source changed**: `solvent/store.py` (the append-only
  `billing_obligations` table, keyed by `job_id`), `solvent/ledger.py`
  (`record_billing_obligation`, `obligation_for`,
  `unbilled_obligations`, `payment_rows`), `solvent/harness.py` (write
  the obligation before the invoice), `solvent/runtime.py`
  (`_reconcile_invoices`).
* **Tests added**: `tests_solvent/test_invoice_recovery.py` (17),
  `tests_solvent/test_invoice_recovery_through_the_cycle.py` (6).
* **Mutants added**: 6, all KILLED — one after a test gap was closed.

### C — no second opinion on Markdown

* **Reproduction**: three document-processing jobs in the previous
  acceptance run refused with "no independently authored reader for .md".
* **Root cause**: the worker renders Markdown and the capability's own
  checker reads it back with `line.startswith("## ")` — one project's
  idea of Markdown consulted twice.
* **Source changed**: `solvent/independent.py` (reader registry,
  `_markdown_blocks`, `_inline_text`, `_recompute_markdown`,
  `_recompute_markdown_against_document`, docx tables),
  `pyproject.toml` (`markdown-it-py==3.0.0` in the `[verification]`
  extra, licensed MIT and security-reviewed).
* **Tests added**: `tests_solvent/test_markdown_t2.py` (28).
* **Mutants added**: 4, all KILLED.

### D — correct work failed by the protected-column check

* **Reproduction**: trim-then-dedupe against a padded source; `FAIL`,
  "1 value(s) lost or altered (e.g. ['  Acme Ltd '])".
* **Root cause**: `expected` was built from the raw cells of the source's
  distinct rows and compared byte-for-byte, while the worker's fixed
  operation order trims (rank 2) before it dedupes (rank 5).
* **Source changed**: `solvent/csvverify.py` (`_NORMALISERS`,
  `_REWRITERS`, `_authorised_for`, `_apply`, and the four-branch
  comparison).
* **Tests added**: `tests_solvent/test_transform_order.py` (21); the
  four-repair integration fixture restored to whitespace duplicates.
* **Mutants added**: 4, all KILLED — one after a test gap was closed.

### E — delivered by recovery, never priced *(found this round)*

* **Reproduction**: the integrated acceptance run with injected crashes
  reported one delivered-but-unbilled job; a crash during verification
  had been resumed all the way to delivery.
* **Root cause**: a resumed job delivers through `_execute_job`, which
  has no invoice step, so no billing obligation is recorded. The job was
  escalated correctly, but a fixed-price job does not need a person.
* **Source changed**: `solvent/harness.py` (record
  `billing.terms_agreed` before the work starts), `solvent/runtime.py`
  (`_agreed_basis`, price a recovery-delivered fixed-price job from its
  quote; still escalate hourly).
* **Tests added**: 4 in `test_invoice_recovery.py`, 1 in
  `test_invoice_recovery_through_the_cycle.py`.
* **Mutants added**: 2, both KILLED.
* **Note**: the first attempt recorded the terms beside the invoice,
  *after* the crash point it had to survive, and the acceptance run
  caught that too. Moving the record before execution closed it.

## Method note

Every defect in this document follows the same path: reproduce, write a
failing regression test, repair, re-run the focused tests, re-run the
integration, record the evidence. Three mutants survived their first run
during this round; in each case the finding was about a test that claimed
more than it checked, the test was strengthened, and the mutant was
re-run to a kill. No acceptance criterion was changed after seeing a
result.

## The two sealed examinations are invalidated — BLOCKED

Status: **BLOCKED**. This is the one part of the round that is not
finished, and the overall certification is **PARTIAL** because of it.

`tests_solvent/capacityexam.py` and `tests_solvent/readingexam.py` seal
the SHA-256 of every module they examine. Two of those modules were
changed under this assignment's explicit authorisation:

| Exam | Module | Why it changed |
|---|---|---|
| reading | `solvent/csvverify.py` | Finding D — stage-aware `check_preserve_columns` |
| capacity | `solvent/store.py` | Finding B — the `billing_obligations` table |

Both seals therefore broke and both exams refused to mark themselves.
That is the seal working exactly as designed. As `readingexam`'s own
docstring puts it: *"A broken seal is not a failure, it is an
invalidation: reseal, re-set and re-sit."* Five of the suite's five
failures are these two exams and nothing else:

```
FAILED tests_solvent/test_capacity_exam.py::TheSealHolds::test_nothing_under_examination_has_moved
FAILED tests_solvent/test_reading_exam.py::TheSeal::test_nothing_under_examination_has_moved
FAILED tests_solvent/test_reading_exam.py::TheSitting::test_every_question_is_answered_correctly
FAILED tests_solvent/test_reading_exam.py::TheSitting::test_the_paper_was_marked_at_all
FAILED tests_solvent/test_reading_exam.py::TheSitting::test_the_result_is_recorded_as_evidence
```

The three `TheSitting` failures are downstream of the same broken seal:
`sit()` returns `{"sealed": False}` without marking, so the sitting has
no `wrong` key and reports zero questions.

### Why it is not resealed

The documented remedy has four steps, and `capacityexam.SITTINGS` —
which preserves all seven prior sittings rather than refreshing them —
is the precedent for how it is done:

1. re-sit the existing paper against the changed source and show it
   still passes in full;
2. move `FROZEN_AT`, which moves `SEED`, which generates a genuinely
   different paper rather than a re-mark of the same questions;
3. update the digests;
4. sit the new paper and record its real result beside the old ones.

Steps 1, 3 and 4 need the frozen digests edited and the exams run.
Both actions were refused by this session's permission classifier as
`[Security Test Removal]` — a reasonable guard, since editing a seal's
digests is indistinguishable from tampering without the surrounding
intent.

### What was *not* done

* No digest was left altered. One edit made while attempting the
  procedure was reverted; `git diff` on both exam modules is empty.
* No sitting was fabricated. No prior sitting was overwritten.
* No exam assertion was weakened, skipped, deleted or marked `xfail`.
* The failures are left visible in the suite rather than suppressed.

### Residual risk

The two exams are not marking against this tree, so there is no
sealed-paper evidence for it and none is claimed. The surfaces they
examine remain covered by the rest of the suite and by the mutation
run, but that is weaker evidence than a sealed sitting and is not a
substitute for one. Resealing requires an operator to run the four
steps above with permission to edit the digests.
