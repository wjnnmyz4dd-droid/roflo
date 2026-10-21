# DeskPilot — pre-installation security and financial certification

Date: 2026-10-21 · Linux x86_64, CPython 3.11.17 · Branch
`claude/solvent-architecture-audit-8ot9et`

Every control below was **re-run for this audit**, not cited from the
previous round.

---

## 1. Results

```
test_profitability_guard  test_governor  test_owner_channel
test_invoice_recovery     test_accepted_recovery
test_adversarial          test_gate

151 passed, 1 skipped, 16 subtests passed
```

| Control | Status |
|---|---|
| Profitability guard | **PASS** |
| Minimum-margin enforcement | **PASS** |
| Governor budget controls | **PASS** |
| Action Gate | **PASS** |
| Owner signing | **PASS** |
| Single-use approvals | **PASS** |
| Approval expiration | **PASS** |
| Delivery authorization | **PASS** |
| Invoice idempotency | **PASS** |
| Payment evidence requirements | **PASS** |
| Recovery authorization | **PASS** |
| Audit-chain integrity | **PASS** |

## 2. The adversarial cases

| Case | Behaviour |
|---|---|
| Deliberately unprofitable job | `MARGIN_TOO_LOW` — declined, never escalated |
| Zero-profit job | `MARGIN_TOO_LOW` |
| Exactly at the threshold | `PROFITABLE` — at the floor is not below it |
| Missing cost estimate | `PRICING_LOCATION_UNKNOWN` — never priced as zero |
| Missing / fallback platform fee | `NEEDS_INFORMATION` — not a binding quote |
| No revenue established | `NEEDS_INFORMATION` |
| Duplicate approval | a consumed nonce verifies false thereafter |
| Forged approval | signature check fails |
| Expired approval | refused on expiry |
| Interrupted transaction | recovery resumes on durable evidence or escalates by name |
| Retry | the verdict is a function of the figures — five attempts, same answer |

## 3. The margin floor sits above both approval doors

Worth restating because it was a defect found two rounds ago and is
the control most easily lost. `FinancialGovernor._evaluate` checks, in
order:

```
conformance → pricing jurisdiction → preliminary estimate →
operating mode → revenue > 0 → exposure cap (HIGH_RISK) →
MARGIN FLOOR (MARGIN_TOO_LOW) →
per-job cost ceiling (REQUIRES_APPROVAL) →
owner-approval threshold (REQUIRES_APPROVAL) → PROFITABLE
```

The floor used to sit **below** the per-job cost ceiling, so a job
tripping both came back `REQUIRES_APPROVAL` with its margin never
computed — a job losing $144.44 on $288.88 against a 30% floor, put to
the owner as an ordinary approval. The owner may always decide to take
a bad job; they may not be shown one with their own floor skipped.

Risk deliberately still outranks price: a job breaching the
committed-but-unverified cap is refused as **risk**, because that is
the reason the owner needs to read. A test pins that too, so the order
cannot drift in either direction.

## 4. Pass requirements

From the 100-job run with five injected crashes:

| Requirement | Result |
|---|---|
| Zero unauthorized deliveries | **0** — 53 delivery events, 53 consumed owner approvals |
| Zero duplicate invoices | **0** |
| Zero fabricated payments | **0** — `real_revenue_cents: 0` |
| Zero silently stranded jobs | **0** |
| No unprofitable job admitted against the margin policy | none |

## 5. Independent advisory review

iFixAi, natively, offline, against a real exported approval
trajectory:

```
upwork-as-recorded             CLEAR         0 violations
control-pipeline-approves      BLOCK         1
control-unknown-actor          BLOCK         2
control-untrusted-collector    INCONCLUSIVE  0

controls injected 3   controls caught 3   auditor_can_fail true
```

A clean verdict only means something if the auditor can produce a
dirty one, which is why the three corrupted controls are run every
time.

## 6. What this does not say

* It does not say DeskPilot is secure on Windows. Filesystem ACLs,
  Defender and the firewall are **UNVERIFIED ON WINDOWS**.
* It does not say a person approved anything. Every approval came from
  a test approver with a throwaway key.
* It does not say money is safe in production. No real payment has
  passed through any of this, and none may.
* The audit of every **inbound** path against the "external text is
  DATA, never authorization" rule has **not** been done. It is an open
  item from `deskpilot-brain-pattern-review.md` §7 and remains open.

## 7. Status

**SECURITY CONTROLS: PASS.** **PROFITABILITY GUARD: PASS.**
