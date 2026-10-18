# DeskPilot — recovering an invoice a crash left unwritten (finding B)

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real money moved; real
revenue recorded across every run in this document: **0 cents**.

---

## 1. Reproduction

From the eleven-point crash matrix: killing the process inside
`ledger.open_payment` leaves a job delivered, in `AWAITING_PAYMENT`, with
no payment row. Nothing recovered it. Work had gone out of the door with
nothing on paper saying it was owed for.

## 2. Root cause

Opening the payment row was the **first** durable write about the money.
If it did not happen, nothing recorded that anything was owed, and a
restart had nothing to reconcile against. Reconstructing the figure after
the fact would have meant inventing it.

## 3. The repair: write the obligation first

A new append-only table, owned by the Ledger:

```sql
CREATE TABLE billing_obligations (
  job_id TEXT PRIMARY KEY, ts TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  rail TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'usd',
  external_ref TEXT NOT NULL DEFAULT '', basis TEXT NOT NULL DEFAULT '',
  client_ref TEXT NOT NULL DEFAULT ''
);
```

`job_id` is the primary key, so it **is** the durable idempotency key: a
second obligation for the same job is refused by the database, not by a
check that could race. The table is in `APPEND_ONLY`, so the figure a
recovered invoice copies cannot have moved since it was written.

The cycle now writes the obligation immediately **before** `open_payment`.

`runtime.reconcile` gained `_reconcile_invoices`, which writes the missing
invoice only when three separate durable records, from three different
authorities, agree:

| Record | Authority |
|---|---|
| a billing obligation for the job | Ledger |
| the job is in a delivered state | orchestrator |
| a `delivery.completed` event for the job | audit log (append-only) |

The state alone is **not** enough: a job can be moved by a repair, a
replay or a migration, while the delivery event is written once, by the
code that performed the delivery. Requiring both is what stops this
becoming a way to invoice work nobody delivered.

The recovered row is `INVOICED` — billed, not paid. Nothing in this path
can mark a payment collected; `real_revenue_cents` is untouched.

## 4. A second defect, found by the integrated acceptance run

The acceptance run with injected crashes reported one
**delivered-but-unbilled** job. Investigated rather than waved through:

* the crash at the verification point was **resumed to delivery** by
  recovery;
* a resumed job delivers through `_execute_job`, which has no invoice
  step, so no obligation was ever recorded;
* `_reconcile_invoices` correctly found a delivery with nothing owed on
  file, recorded an incident and **blocked the job on the owner**.

That already met the acceptance criterion — an explicit, auditable
exception rather than a silent loss — but it did not need a person for a
fixed-price job, where the amount is simply the quote the job was
accepted on. So:

* the cycle now records a `billing.terms_agreed` audit event **before the
  work starts**, naming the basis;
* a delivery completed by recovery with a `FIXED_PRICE` basis on file is
  priced from the job's own quoted amount and invoiced;
* an `HOURLY` basis — whose figure depends on hours this process cannot
  reconstruct — is still escalated, because inventing it is exactly the
  fabrication this work exists to prevent;
* no basis on file → escalated.

## 5. Tests

`tests_solvent/test_invoice_recovery.py` — **17 tests**, covering the
three crash windows the assignment names (before the obligation, between
the obligation and the payment, after the payment), repeated restarts,
duplicate recovery attempts and an interrupted recovery, plus the four
cases above.

`tests_solvent/test_invoice_recovery_through_the_cycle.py` — **6 tests**,
proving the real business cycle writes both records, that the obligation
matches the invoice it produced, that it names who owes it, and that a
complete cycle leaves reconciliation nothing to do.

## 6. Mutation

| Mutant | Verdict |
|---|---|
| `invoice-recovery-trusts-the-job-state-alone` | KILLED |
| `invoice-recovery-marks-the-recovered-invoice-paid` | KILLED |
| `invoice-recovery-reinvoices-what-is-already-billed` | KILLED |
| `the-obligation-is-never-written-down` | KILLED |
| `recovery-prices-an-hourly-job-from-its-quote` | KILLED |
| `the-agreed-basis-is-never-written-down` | KILLED |

`invoice-recovery-trusts-the-job-state-alone` **survived its first run**:
the "never invoice this" test used a job that was not in a billable state
at all, so a repair trusting the state alone passed it. A test was added
for a job that reaches `AWAITING_PAYMENT` *without* a delivery event, and
the mutant is now killed.

## 7. Never, and where it is enforced

| Rule | Enforcement |
|---|---|
| Never fabricate a delivery acknowledgment | the `delivery.completed` event is required and is append-only |
| Never fabricate a payment confirmation | the recovered row is `INVOICED`; only `PAID`/`PARTIALLY_PAID` mean money, and they demand an external verification method |
| Never issue duplicate invoices | `job_id` primary key on the obligation; a left join finds only obligations with no payment row |
| Preserve amounts and terms | the invoice copies an append-only obligation |
| Escalate ambiguous billing | no obligation, or an hourly basis, blocks on the owner with an incident |
