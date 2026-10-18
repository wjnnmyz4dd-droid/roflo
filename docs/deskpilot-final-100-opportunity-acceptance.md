# DeskPilot — integrated 100-opportunity acceptance

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect was
produced at any point in this run.

---

## 1. How it was run

One shop, one process, 100 synthetic contracts: **20 per skill across
five skills**, in four value bands. Owner signing **configured** with an
isolated per-run key (`secrets.token_hex(32)`), and **one explicit
synthetic owner approval issued per delivery** — never reused, never
batched. Per-skill pipelines capped at **4 committed**. Egress
`simulation_only`. Payments simulated, never confirmed.

Nothing was bypassed: not signing, not T2, not the capacity controls,
not the Action Gate.

The driver is a scratchpad script
(`acceptance100.py`, alongside the synthetic contract generator it
imports) and is **not committed**: it is a one-off load driver bound to
a temporary directory, not product code, and committing it would imply a
reproducibility it does not have. What is committed is its output, in
`docs/evidence/deskpilot-final-certification.json`, and the behaviour it
exercises, which is covered by
`tests_solvent/test_four_repair_integration.py` and the per-repair
suites.

## 2. Result

| Measure | Value |
|---|---|
| Opportunities offered | 100 (20 per skill) |
| **Delivered** | **52** |
| Deliveries that consumed a signed owner approval | **52 of 52** |
| **Unauthorized deliveries** | **0** |
| Skills delivering nothing | **0** |
| Jobs stranded mid-flight at the end | **0** |
| Duplicate invoices | **0** |
| Fabricated payments | **0** |
| Real revenue recorded | **0 cents** |
| Audit chain | verifies |
| Exceptions escaping the cycle | 0 |
| Wall clock | 6.5 s |

Delivered per skill: csv-cleanup 2, data-entry 10, document-processing
10, report-builder 10, spreadsheet-automation 20.

Final job census: 52 `COMPLETE`, 41 `REJECTED`, 7 `BLOCKED`, **0** in
`EXECUTING`, `VERIFYING` or `READY_FOR_DELIVERY`.

Peak in-flight per pipeline against a ceiling of 4: csv-cleanup 4,
document-processing 3, others 0 at the sampling points. The ceiling was
respected throughout; it was also reached, so the control was exercised
rather than merely present.

## 3. Where the other 48 went, and why that is the correct answer

A job safely refused is not a job that failed, and it is not a job
delivered. Reported separately:

| Outcome | Count | Reason given by the system |
|---|---|---|
| Rejected at qualification: risk/exposure ceiling | 27 | "exposure or risk beyond policy limits" — the three highest value bands in data-entry, document-processing and report-builder sit above the configured risk ceiling. |
| Rejected at qualification: no capacity | 14 | "not selected: capacity is already committed elsewhere" — csv-cleanup's pipeline was full. This is the per-skill pool doing its job. |
| Blocked on the client | 4 | An ambiguous date (`04/06/2026`) in a csv source. The client is the only one who knows whether that is April or June, and they are asked plainly rather than guessed at. |
| Blocked on the owner | 3 | A document-processing job above T1 whose deliverable is `.md`, for which no independently authored reader exists in this build. Escalated with an incident; **not** delivered on the strength of the worker's own word. |

Every one of the 52 deliveries can name the signed approval that let it
out: the audit log records 52 `delivery.completed` events and 52
`owner.approval_consumed` events, one per delivered job, and the set
difference is empty. That is how the zero in the table above is
measured, rather than assumed from the absence of a complaint.

Those last three are the honest outcome of repair 2's boundary: csv now
has a genuinely independent reader, `.md` does not. Before the automatic
recovery work they would have sat in `VERIFYING` for ever. They are
counted as **safely refused**, not as recovered and not as delivered.

## 4. What this run does not show

* **It is not a revenue result.** Nothing was sold, nothing was invoiced
  to a real client, and `real_revenue_cents` is 0 by construction. The
  52 deliveries are synthetic contracts delivered inside a sandbox.
* **It is not a Windows or MetaTrader 5 result.** Linux only.
* **It is not evidence of owner consent.** The approvals were issued by
  a test approver holding a throwaway key.
* **It is not a throughput claim.** 6.6 s for 100 contracts reflects
  small synthetic sources, not client-sized work.
