# DeskPilot — five-skill smoke test

Date: 2026-10-21 · Linux x86_64, CPython 3.11.17 · Branch
`claude/solvent-architecture-audit-8ot9et`

All five certified skills, exercised end to end with faults injected.
Synthetic work throughout: no marketplace was contacted, no client
messaged, no proposal submitted and no payment processed.

**Executed on Linux. UNVERIFIED ON WINDOWS.**

---

## 1. The run

```
offered                 100   (20 per skill, five skills)
crashes injected          5   worker execution, verification,
                              delivery acknowledgment, invoice
                              creation, recovery
delivered                52
wall                    8.9 s
```

| Skill | Offered | Delivered |
|---|---|---|
| csv-cleanup | 20 | 2 |
| data-entry | 20 | 10 |
| document-processing | 20 | 11 |
| report-builder | 20 | 10 |
| spreadsheet-automation | 20 | 19 |

Final census: 52 COMPLETE, 40 REJECTED, 7 BLOCKED, 1 AWAITING_PAYMENT.

**52 of 100 delivering is the system working, not failing.** The 40
rejections are qualification refusing work it should refuse, and the 7
blocks are approval and verification controls holding. A run that
delivered 100 of 100 would mean the gates had stopped gating. The low
csv-cleanup figure is the same effect concentrated: that skill's
synthetic briefs include the ones designed to be refused.

## 2. The invariants the brief asks for

| Required | Result |
|---|---|
| Four commitments per skill | `ceiling_respected: true` |
| One active worker per skill | peak in-flight within the per-skill pool throughout |
| No cross-skill starvation | `cross_skill_starvation: []`, and `skills_delivering_nothing: []` |
| No duplicate delivery | 53 delivery events, 53 consumed owner approvals, 0 unauthorized |
| No duplicate invoices | `duplicate_invoices: 0` |
| No fabricated payments | `fabricated_payments: 0`, `real_revenue_cents: 0` |
| No silently stranded jobs | `stranded_jobs: []` |
| No delivered-but-unbilled | `delivered_but_unbilled: []` |
| Audit chain | `audit_chain_ok: true` |
| Unexpected exceptions | `{}` |

## 3. Per-skill coverage

Each skill is exercised through the full cycle — admit, execute,
verify independently, authorize, deliver, invoice, reconcile — with
small, medium, large and complex briefs, and with invalid input,
missing files, corrupted files and unexpected formats among the 20
offered. The refusals are counted as outcomes rather than discarded:
a brief that *should* be rejected and is rejected is a pass.

## 4. Failure injection

Five crashes at the five named points, each followed by a restart and
an inspection of durable state. After every one:

* recovery resumed the job or escalated it by name — never discarded it
* no job was left holding a pipeline slot
* the financial record was consistent: no duplicate invoice, no
  invented payment, no delivery without a consumed approval
* capacity accounting matched the census
* the audit chain verified

The crash during **invoice creation** is the one that previously left a
delivered job unbilled, and the crash during **recovery** is the one
that previously double-counted an attempt. Both are clean.

## 5. What this does not establish

* **Nothing about Windows.** Same Python, different OS, different
  filesystem, different locking. The five-skill smoke test on Windows
  is in `.github/workflows/windows-preinstall.yml` and has not run.
* **Nothing about real work.** Every brief is synthetic and every
  approval came from a test approver with a throwaway key.
* **Nothing about revenue.** `real_revenue_cents: 0`.
* **Nothing about the service doing this unattended.** The cycle was
  driven by the harness. The installed service does not schedule it —
  see `deskpilot-preinstall-system-audit.md` §3.

## 6. Status

**FIVE-SKILL TEST: PASS** on Linux. **UNVERIFIED ON WINDOWS.**
