# Sentinel — Architecture and Authority Specification

Sentinel is a fail-closed trading-system **safety core** with an adversarial qualification suite.
It runs against a durable simulated broker. **There is no live broker adapter and no path to live
capital.**

## 1. Decision flow

```
 wall clock ─┐                                   broker (canonical truth)
             ▼                                        ▲        │
   ┌───────────────────┐   STARTUP ⇒ HALT until RECONCILED    │ account / deals / positions / quotes
   │ Lease (fencing)   │──────────────────────────────────────┘
   └────────┬──────────┘
            ▼
   Reconciler (B) ─► Clock-drift check ─► Compliance: account (B/HALT) ─► Risk: account (B/HALT)
            ▼
   Required exits (compliance flatten / horizon) ─► Execution.close (needs compliance ALLOW for the close)
            ▼  per instrument
   Finder (P) ─► News gate (B) ─► Validator (V) ─► Arbiter (A) ─► Risk (S/B) ─► Compliance (B) ─► News re-check (B)
            ▼
   PermitIssuer: verifies 4 signed decisions bound to this candidate/intent ─► Permit (single use, 30 s TTL)
            ▼
   Execution (X): INTENT_PERSISTED ─► ORDER_SENT ─► broker ─► ACKED | REJECTED | UNKNOWN ─► (Reconciler) RESOLVED
            ▼
   Immutable journal (hash chain) ─► Learning (L, PC: memory + proposals) ─► Promotion pipeline (PR, needs operator)
```

Shadow mode builds the orchestrator **without** an execution service. The path to the broker does
not exist; it is not merely disabled.

## 2. Authority matrix

| Component | Propose trade | Validate | Arbitrate | Block / halt | Size | Execute | Close | Learn | Propose change | Promote change |
|---|---|---|---|---|---|---|---|---|---|---|
| Finder | **yes** | – | – | – | – | – | – | – | – | – |
| Validator | – | **yes** | – | – | – | – | – | – | – | – |
| Arbiter | – | – | **yes** | (gate only) | – | – | – | – | – | – |
| News gate | – | – | – | **yes** | – | – | – | – | – | – |
| Compliance (FTMO) | – | – | – | **yes** | – | – | demand flatten | – | – | – |
| Risk | – | – | – | **yes** | **yes** (reduce only) | – | demand flatten | – | – | – |
| Execution | – | – | – | refuses invalid permits | – | **yes** (permitted intents only) | **yes** (authorised reasons) | – | – | – |
| Reconciler | – | – | – | **yes** (unknown state) | – | – | – | – | – | – |
| Learning | – | – | – | – | – | – | – | **yes** | **yes** (proposals) | – |
| Promotion pipeline | – | – | – | – | – | – | – | – | – | **yes**, only with operator signature; never to live |
| Orchestrator | routes only | – | – | – | – | – | – | – | – | – |

The matrix is data in `authority.py`, and tests enforce it against the code:

- import-graph checks;
- a static check that `send_order` / `close_position` are called only from `execution.py`;
- signature checks on every decision;
- permit verification.

## 3. Component contracts

| Component | Inputs | Outputs | Failure state | Recovery | Test file |
|---|---|---|---|---|---|
| Finder | closed bars | `TradeCandidate` with provenance hash | None (no candidate) | stateless | test_adversarial_components |
| Validator | candidate, raw bars, cost model | `ValidationReport` (VALID / INVALID / INDETERMINATE) | INDETERMINATE | stateless | test_adversarial_components |
| Arbiter | candidate, report | `ArbiterDecision` (signed), rule trace | INDETERMINATE / NO_TRADE | stateless | test_adversarial_components |
| News | calendar snapshot (UTC-normalised), instrument | `GateDecision` (signed) | BLOCK on UNKNOWN | refresh keeps last snapshot; staleness decides | test_compliance_news, test_chaos |
| Compliance | versioned profile JSON, broker snapshot + deals, calendar | `GateDecision` (signed) | HALT on profile problems, BLOCK on missing truth | derived from broker each call | test_compliance_news |
| Risk | broker snapshot + deals + spec + quote | `GateDecision` with approved volume (signed) | BLOCK / HALT | derived from broker each call; halts journaled | test_adversarial_components, test_execution_reconcile |
| Execution | `Permit` | journal events, broker order | UNKNOWN ⇒ halt pending reconcile | no resend; reconciler resolves | test_execution_reconcile, test_crash_matrix |
| Reconciler | broker truth + journal | RESOLVED / POSITION_CLOSED / RECONCILED | not clean ⇒ halt | rerun when broker reachable | test_execution_reconcile |
| Lease | coordination DB | fencing token; fenced send ledger (`record_send`) immediately before every broker send | LeaseLost ⇒ STANDBY / send refused | wait for expiry, re-acquire; new leader halts on another owner's in-flight ledger row | test_execution_reconcile, test_split_brain |
| Journal | events | hash-chained rows | integrity failure ⇒ manual halt | operator | test_authority_and_journal, test_chaos |
| Learning | journal (read), own store | FACT / OBSERVATION / INFERENCE / HYPOTHESIS / LESSON / proposals | MemoryRejected | idempotent post-mortem | test_adversarial_components, test_crash_matrix |
| Promotion | proposals, criteria, stage artifacts | STAGE_RESULT, PROMOTED (operator) | PromotionRefused | — | test_adversarial_components |

## 4. Key invariants

1. No broker order without a persisted intent and a verified, unexpired, single-use permit.
2. One permit requires ALLOW from compliance and news, ALLOW/REDUCE from risk, and APPROVE from the
   arbiter. All four are signed and bound to the same candidate.
3. An UNKNOWN order outcome halts trading until the reconciler resolves it from broker history.
   Nothing is resent.
4. After every process start, trading stays forbidden until a clean reconciliation.
5. Halts, loss limits and FTMO limits come from broker truth or from the journal. A restart cannot
   reset them.
6. Learning cannot write configuration, parameters, risk, compliance or code. It can only write
   memory records and proposals.
7. Promotion needs ordered stage results under criteria registered before the holdout is opened,
   plus an operator signature. It never targets live.

## 5. Known structural limits (also listed in the qualification report)

- In-process key separation is logical, not cryptographic. Code running inside the same process
  could read the keys. Process isolation would be needed to harden this.
- MetaTrader 5 has no fencing tokens. Phase 2 added a fenced send ledger (compare-and-insert in the
  coordination DB right before `order_send`), which refuses a leader that lost the lease before
  that point. A leader paused *after* the record can still send late. The new leader detects it
  (in-flight ledger row, then FOREIGN → manual halt) but cannot prevent it; a single execution
  gateway is still required.
- Calendars: live Forex Factory weekly JSON, and a historical point-in-time FOMC calendar from
  federalreserve.gov (`research/calendar_hist.py`, research/replay only). There is no historical
  NFP/CPI source (blocked).
- The MT5 adapter (`broker/mt5.py`, demo-only guard) is qualified only against a fake terminal.
  Filling modes, server time, symbol specs and the history lag of a real broker are unverified.
