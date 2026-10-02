# Sentinel — Qualification Report

**Date:** 2026-10-02/03.
**Branch:** `claude/ai-trading-agent-audit-e3teyy` (repo `wjnnmyz4dd-droid/roflo`, directory `sentinel/`).

**Status: STOPPED BEFORE LIVE.** No live broker adapter exists and no real order was ever sent.

**Verdict labels used in this report:**

| Label | Meaning |
|---|---|
| PASS / FAIL | The test was **executed**; evidence is a test name or artifact. |
| NOT EXECUTED | Not run; the reason is given. |
| INSPECTION | Established by reading source, not by running anything. |

---

## Executive summary (read this first)

1. **Safety architecture: built, attacked and largely qualified in simulation.**
   - The current suite has 144 tests, all passing. 19 of them are real process-kill crash tests.
   - Mutation testing removed 49 safeguards one at a time. 47 removals were caught outright. The 2
     remaining ones survive only because a second independent guard catches the same fault;
     removing both layers together is caught.
   - The adversarial process found and fixed real defects in this build. They are listed in §Q/§S.
2. **Edge: NOT ESTABLISHED for anything that could be deployed.**
   - The pre-registered criteria were met on the daily-bar holdout (2019→2026, 390 trades,
     +0.164 R/trade, t=2.33). But the same rule lost money over 2000–2018 (−0.035 R, 760 trades).
   - The holdout gain is concentrated in BTCUSD.
   - The edge disappears at about 2× modelled costs on the full sample.
   - On the hourly bars the pipeline actually runs, the Finder alone is negative in both periods.
   - All data are Yahoo proxies (futures/spot), not broker CFD prices.
3. **Not qualified for micro-live, and not close:**
   - no MT5 adapter;
   - no broker-side fencing;
   - FTMO profiles not human-reviewed;
   - only one calendar source and no historical calendar;
   - the Validator adds no measured information on daily data;
   - shadow operation is a single live cycle.

---

## A. Repository / source identities

| Source | Identity verified | Use |
|---|---|---|
| This build | `sentinel/` in `wjnnmyz4dd-droid/roflo`. Commits `4c5d6ab` (pre-registration committed before any P&L), `b777f94`, and later | Primary |
| flukelaster/ai-trading-agent @ `e85c186` | Audited earlier (`audits/`) | Inspiration for defect classes only. **No code copied** |
| FTMO rules | ftmo.com pages retrieved 2026-10-02. Text stored in `provenance/ftmo/*.txt`; sha256 recorded in every policy file and verified by test | FTMO profiles |
| Economic calendar | Forex Factory weekly JSON (`nfs.faireconomy.media/ff_calendar_thisweek.json`). Snapshot `provenance/calendar/ff_thisweek_20261002.json` (sha256 `23f01d0f…`) | News gate |
| Market data | Yahoo chart API: GC=F, CL=F, JPY=X, BTC-USD, at 1d and 1h. Files plus sha256 in `data/*.meta.json` | Research and replay |
| calinfaja/K-LEAN | Cloned. HEAD `c8a06e9c` (2026-02-16), Apache-2.0. "Second opinions from multiple LLMs" | §T |
| ifixai-ai/iFixAi | Cloned. HEAD `4cebf807` (2026-10-01), Apache-2.0. "Independent Auditing of AI Agents" | §U |

## B. System architecture

The full specification is in `ARCHITECTURE.md`. The authority chain as built:

```
Lease ▸ Reconcile ▸ Clock-drift ▸ Compliance(account) ▸ Risk(account) ▸ exits
      ▸ Finder ▸ News ▸ Validator ▸ Arbiter ▸ Risk(size) ▸ Compliance(trade) ▸ News re-check
      ▸ PermitIssuer ▸ Execution ▸ broker ▸ Reconciler ▸ Journal ▸ Learning ▸ Proposals ▸ Promotion
```

The ordering differs from the brief in two places, for safety reasons:

- Account-level compliance and risk run **first**, as cheap global vetoes.
- Trade-level compliance runs **after** risk sizing, because FTMO worst-case checks need the volume.
- News runs twice: before validation (cheap) and immediately before permit issue (time may have
  passed).

All four vetoes and the arbiter sign their decisions. The permit issuer verifies:

- each signature;
- staleness (30 s);
- binding to the same candidate, instrument, direction and stop;
- that volume is within both the risk approval and the volume compliance evaluated.

Execution accepts only verified permits. It is unique per permit, per intent, per decision set and
per candidate.

## C. Authority matrix

`authority.AUTHORITY_MATRIX` holds the matrix; it is reproduced in ARCHITECTURE.md §2. These tests
enforce it:

- `test_authority_matrix_has_no_duplicate_execute_or_propose`: exactly one proposer (Finder) and one
  executor (Execution).
- `test_non_execution_components_cannot_reach_broker_or_permits`: import-graph check over 9 modules.
- `test_no_component_but_execution_calls_send_order`.
- `test_shadow_mode_has_no_execution_path`.

There is no ambiguous authority. Risk and compliance may *demand* flattening, but only Execution
closes, and a close needs a compliance ALLOW (because FTMO's news window forbids closes too).

## D. Finder results

| Test | Purpose | Fault | Expected | Actual | Verdict | Evidence / reproduction |
|---|---|---|---|---|---|---|
| test_finder_never_uses_unfinished_bar | look-ahead guard | unfinished bar with +500 spike | identical candidate | identical | PASS | `pytest tests/test_adversarial_components.py -k finder` |
| Future-perturbation probe (100 signals, 4 instruments) | leakage | bars after t multiplied ×3 | 0 decisions change | 0 changed | PASS | `tools/run_research.py extras` → `reports/edge_extras.json["leakage"]` |
| Edge study | profitability | — | — | see §W | — | `reports/edge_dev.json`, `reports/edge_holdout.json` |

## E. Validator results

| Test | Fault | Expected | Actual | Verdict |
|---|---|---|---|---|
| test_validator_rejects_corrupted_inputs ×8 | duplicate / out-of-order, negative, zero, impossible OHLC, bad tick, future bar, missing signal bar, foreign instrument | INVALID or INDETERMINATE; arbiter never approves | as expected | PASS |
| test_stale_signal_and_closed_market_indeterminate | stale signal, closed market | INDETERMINATE | INDETERMINATE | PASS |
| test_spike_and_choppy_regime_falsified | one-bar spike, driftless series | INVALID | INVALID | PASS |
| test_costs_destroy_edge_is_detected | 25× costs | INVALID | INVALID | PASS |
| test_insufficient_history_is_indeterminate | 140 bars | INDETERMINATE | INDETERMINATE | PASS |
| test_validator_analogs_do_not_overlap | overlapping analog samples | n ≤ len/H | holds | PASS |

**Defects found in the Validator during qualification (all fixed and tested):**

1. The volatility check ranked the signal bar itself. Every breakout looked "extreme", which caused
   76/143 rejections in the smoke replay.
2. Weekend gap-opens were treated as bad ticks.
3. Overlapping analogs inflated n and the t-statistic, so a 140-bar history passed as "VALID".

## F. Independence analysis (measured)

Source: `reports/edge_extras.json["independence"]`, daily bars, all Finder signals.

| Metric | Value |
|---|---|
| Shared declared features (Jaccard) | 0.50 (close/high/low/donchian shared) |
| Shared data | yes (same bars) |
| Validator VALID rate | 2.7 % pooled (0 % on XAUUSD and USOIL daily) |
| Point-biserial(VALID, realised R) | **0.018 (no information)** |
| Error φ | −0.95: mechanical, because it rejects almost everything, including winners |
| Independence score | 0.50 |

**Interpretation:** the Validator does not behave as an independent informative opinion on daily
data. Its verdicts are dominated by data-quality INDETERMINATE (the Yahoo daily proxy had 524 of
6,631 XAUUSD bars rejected as OHLC-inconsistent) and by cost and regime falsification.

On hourly data the ablations look better (holdout: Finder alone −0.026 R vs. +0.185 R with the
Validator, n=68, t=1.60) but are **not statistically significant**, and the development year showed
+0.03 R (t=0.26).

The pipeline is configured with independence unmeasured (`None`). The Arbiter therefore demands the
stricter 0.10 R minimum.

## G. Arbiter results

`test_arbiter_rules_each_fire` exercises every rule R1–R9:

- expired candidate;
- missing validation;
- mismatched candidate;
- INVALID → REJECT;
- incomplete checks;
- contradictory checks;
- data-hash mismatch;
- edge below threshold;
- stricter threshold when independence is low;
- missing evidence.

`test_arbiter_has_no_market_or_broker_inputs` checks that the signature is `(candidate, report, now)`
only. All PASS. Mutation testing: 4/4 arbiter mutations killed.

## H. Risk results (all PASS)

| Area | Tests |
|---|---|
| Sizing | Sizes to 0.25 % budget using broker tick value |
| Blocks | stale/invalid quote, crossed market, stop on wrong side or too close, spread > 10 % of stop, stale snapshot, NaN stop |
| Halts | impossible account (≤0, NaN), internal daily loss (broker-derived, includes unrealised), drawdown, loss streak |
| Exposure | Stop-less position makes open risk unbounded → BLOCK (control trade allowed without it); USD correlation bucket; margin |
| Restart | `test_daily_loss_is_derived_from_broker_after_restart`: a brand-new engine with no memory HALTs on broker-derived loss |
| Loss-streak halt | Operator-scoped and resets at RESUME (fix found by the holdout replay, see §Q) |

## I. Execution results (all PASS)

| Test | Fault | Result |
|---|---|---|
| test_timeout_after_fill_is_unknown_halts_and_never_resends | broker filled, response lost | UNKNOWN → halt → reconcile CONFIRMED; exactly 1 send, 1 position |
| test_timeout_before_fill_resolves_not_executed | request lost | NOT_EXECUTED, 0 positions |
| test_disconnect_mid_send… | disconnect | halted while unreachable; resumes only after clean reconcile |
| test_definite_reject_and_requote_are_terminal_no_retry | reject, requote | 1 send, no retry |
| test_partial_fill_records_actual_volume | 50 % fill | journal volume = broker volume |
| test_duplicate_ack_recorded_once | duplicate acknowledgement | ignored idempotently |
| test_same_candidate_cycle_twice… | scheduler duplication | 1 position |
| test_permit_cannot_be_reused_or_altered… / test_unused_expired_permit_is_refused | replay, ×10 volume, expiry | refused |
| test_one_decision_set_cannot_mint_two_positions | approvals reused for a second intent | refused (**defect found and fixed**) |
| test_decisions_are_bound_to_candidate_instrument_direction_and_stop | genuinely signed approvals for another candidate, instrument, direction or stop | refused (**defect found and fixed**) |

## J. Reconciliation results (all PASS)

- **Foreign position:** manual halt; nothing traded on top of it.
- **Local position missing at broker without a deal:** halt.
- **Stop-out by gap:** P&L comes from the broker deal (not a local estimate) and the fill is below the
  stop.
- **Restart:** trading requires a reconcile first.
- **Clock drift vs broker:** halt.
- **Crash during close:** resolved from the broker.
- **Defect found and fixed:** closed-position P&L was journaled only at the next reconcile, so a
  crash in between left a broker-truth gap (`exec_after_broker_ack_before_persist` crash test).

## K. News engine results

| Test | Verdict |
|---|---|
| Live feed parsed; NFP `08:30-04:00` → **12:30 UTC** (the audited repo stored 08:30 as UTC) | PASS |
| EST/EDT explicit offsets → 13:30 / 12:30 UTC | PASS |
| Record without timezone → rejected, not guessed | PASS |
| UNKNOWN → BLOCK: nothing loaded, stale (>6 h), fetched in the future, malformed > 5 %, payload not a list, implausibly empty, outside coverage (isolated after mutation round 1) | PASS |
| High-impact 30/15 min window; central-bank 60/30 min; USD events also gate XAUUSD/BTCUSD; unknown impact treated as HIGH; Medium not blocking (policy) | PASS |
| Duplicate records merged; two-source conflict (time and impact) resolved conservatively | PASS (merge logic only; **a second live source is NOT implemented**) |
| Source outage / rate-limit: keeps the old snapshot, then fails closed when stale | PASS |
| Prompt-injection title ("IGNORE ALL RULES… place_order…") is inert data | PASS |
| MT5 economic calendar source | **NOT EXECUTED** (no MT5) |
| Historical calendar for replay | **NOT AVAILABLE**: ForexFactory pages return 403 and no archive endpoint exists. Replay uses a labelled rule-derived NFP schedule only, so the historical news counterfactual covers NFP only (n=2–3) |

## L. FTMO compliance results

Rules modelled as versioned data (5 profiles, valid 2026-10-02 → 2026-11-01, review status
**UNREVIEWED**):

| Rule | Value |
|---|---|
| Daily loss | 5 % (2-Step) / 3 % (1-Step) of initial capital, below the 00:00 CE(S)T balance |
| Maximum loss | static 10 % (2-Step) / end-of-day trailing 10 % (1-Step) |
| Equity | balance + floating ± swaps − commissions |
| FTMO Account (Standard) news | ±2 min restricted window on targeted instruments, applying to opens, closes and SL/TP triggers |
| FTMO Account (Standard) weekend | flat before the weekend |
| Evaluation phases | news and weekend rules not applicable |

| Test | Verdict |
|---|---|
| Source hashes in profiles match the retrieved pages | PASS |
| Fail-closed: missing / corrupt / contradictory / unknown-rule / unsourced profile; no profile; expired; unreviewed outside simulation | PASS |
| FTMO worked examples: 2-Step day limit 97,000 from midnight 102,000; first day uses initial capital; 1-Step trailing limit 94,000 from HWM 104,000 | PASS |
| Equity breach → HALT; safety buffer (25 % of allowance) → BLOCK; unrealised loss counts | PASS |
| Pre-trade worst case (stop + 20 ticks slippage + commission + open risk) crossing the floor → BLOCK | PASS |
| CET/CEST midnight across the 2026-10-25 DST switch | PASS |
| FTMO Account restricted window blocks open and close on targeted instruments only; Challenge unaffected; flatten before event; unknown calendar → BLOCK; weekend | PASS |
| Compliance bypass via Finder, Validator, Arbiter, forged decisions, swapped authority, replayed / altered / expired permits, halted control state, shadow mode | PASS. The only broker-order path is `ExecutionService.submit(permit)`; `test_bypass_surfaces_inventory` |
| REST API / webhook / manual-order endpoint | **NOT APPLICABLE**: none exist by design. Any future surface must call `ExecutionService.submit` with a permit |
| Not modelled | Best Day rule (1-Step, informational), minimum trading days, profit target, Forbidden Trading Practices (clause 7.3), instrument trading-hours tables (weekend cut-off approximated as Friday 18:00/20:00 UTC) |

## M. Journal / provenance results

- **Append-only:** UPDATE/DELETE triggers. **Tamper-evident:** the hash chain detects edits even if
  the triggers are dropped.
- **Exactly-once:** unique keys are written atomically with the event.
- **Startup:** integrity verification at startup → manual halt.
- **Records kept:** every candidate, validation, arbiter verdict, news / risk / compliance decision,
  permit, intent, broker response and position close. That includes **every rejected candidate**:
  23,011 events in the dev replay, all chain-verified.

Tests PASS: `test_journal_*`, `test_tampered_journal_halts_at_startup`,
`test_corrupted_journal_file_prevents_startup`.

## N. Learning / memory results (all PASS)

| Property | Behaviour |
|---|---|
| FACT creation | Only from hash-verified broker-truth journal events. Refused for: duplicate fact, non-truth event type, future-dated event, tampered journal, direct FACT injection |
| Laundering | An INFERENCE cannot be cited as evidence for an OBSERVATION |
| References | Hallucinated references are rejected |
| Duplicated evidence | Cannot inflate a lesson |
| Lesson lifecycle | ACTIVE → WEAKENED/RETIRED with decay; support/contradiction history kept |
| Output | Proposals only. The module contains no writers for parameters, risk, policy, files or orders (static test) |
| Crash during learning | Idempotent; no duplicate facts |

## O. Evidence / promotion pipeline results (all PASS)

- Ordered stages.
- Criteria hash registered before the holdout; criteria immutable.
- Holdout opened once (no reuse).
- A failed stage ends the line.
- Promotion requires an operator HMAC signature from a key the system does not hold. Its output is
  "paper-eligible", never live.
- The real edge study used this pipeline: `reports/promotion.db` records PROPOSAL →
  CRITERIA_REGISTERED → HOLDOUT_OPENED once, chain verified.

## P. Crash-recovery results

Each case is a separate process killed with `os._exit(137)`, then restarted in a fresh process. Every
case checks: no duplicate order, no unauthorised fill, no unresolved intent, no orphan position, P&L
equal to broker deals, journal verified, trading only after reconcile.

| Kill point | Verdict |
|---|---|
| cycle_start, after_candidate, during_validation, after_arbiter, before_risk, after_risk, before_permit, exec_after_intent_persisted, exec_before_broker_send, exec_after_broker_ack_before_persist, after_submit_before_outcome | PASS ×11 |
| exit_before_close, exec_close_before_send, exec_close_after_send_before_persist | PASS ×3 |
| Timeout-after-fill + crash; partial fill + crash (reconciled volume = broker volume) | PASS ×2 |
| During learning; during promotion evaluation; manual halt survives crash + restart | PASS ×3 |
| Disk full, memory pressure, CPU starvation as OS-level faults | disk-full simulated (PASS); memory and CPU **NOT EXECUTED** (a long pause is covered by the lease-expiry test) |

Reproduce with `pytest tests/test_crash_matrix.py` (scenario driver: `tools/crash_scenario.py`).

## Q. Chaos / fault-injection results

| Fault | Verdict |
|---|---|
| Disk full at intent persist; DB unavailable; corrupted journal file; tampered journal | PASS: no order, halt |
| Broker credential failure | PASS |
| Stale market feed; zero / crossed / extreme-spread quotes; flash move through stop | PASS |
| Calendar outage / rate limit | PASS |
| **Worst possible moment:** open position + pending uncertain order + broker disconnect + local journal corruption + stale feed + restart | PASS: halted, nothing opened, nothing closed blindly, the uncertain order was never re-sent; integrity is a manual halt. The precondition (one real UNKNOWN order plus 2 broker positions) is asserted |
| **Split brain:** lease, fencing token, paused leader returns, lease stolen while still valid locally (clock disagreement), stale lock released, standby when not leader | PASS |
| **Residual risk, test-pinned:** without broker-side fencing (real MT5), a stale leader that skips its own lease check gets a second position | Documented |
| LLM outage / malformed output / hallucination / prompt injection to tools | **NOT APPLICABLE by design**: no LLM in the decision path (static test bans LLM SDKs, subprocess, eval, pickle) |
| Network partition between two real hosts; dependency failure | **NOT EXECUTED** |

**Defects found by adversarial and chaos testing (fixed):**

1. Gate decisions were unsigned, so any caller could forge an ALLOW.
2. Decision sets were not bound to one intent (approvals could be reused).
3. Decisions were not bound to candidate, instrument, direction or stop.
4. Replaying history halted because profile validity was judged against market time instead of the
   wall clock.
5. An unexpected exception crashed the cycle instead of failing closed.
6. A NaN stop crashed the risk engine at hashing instead of producing a BLOCK.
7. Closed-position P&L was journaled late after a successful close.
8. The loss-streak halt was mis-scoped, so it re-fired daily for months once triggered (found by the
   holdout replay).

## R. Security results

| Area | Result |
|---|---|
| External text | Calendar titles are capped at 200 chars and never interpreted. Injection test PASS |
| LLM | No LLM, shell, eval or pickle in `src/` (excluding research) — PASS |
| Secrets | Permit and decision keys are process-local `os.urandom`, never logged or persisted; the logger masks secret-like keys |
| Authentication | No network API surface exists, so there is no authentication to bypass |
| **Limitation (INSPECTION)** | Key separation is in-process, so it is logical, not cryptographic. Malicious code in the same interpreter could read the keys. Process isolation (separate risk/compliance/execution processes with their own keys) is needed before anything live |
| Dependencies | Safety core is stdlib-only. Research needs no third-party packages at runtime. Tests use pytest |

## S. Mutation-test results

Runner: `tools/mutate.py`. Every mutation runs the full suite on a fresh copy.

| Round | Killed | Survivors | What happened |
|---|---|---|---|
| 1 | 40 / 46 | coverage check, missing-position, expired permit, redundant manual-halt branch, lease theft, overlapping analogs | All 6 were real test gaps → tests added; redundant branch removed |
| 2 | 45 / 48 | stop-less risk and candidate binding were killed for the *wrong reason* (margin, signature) | Tests rewritten to isolate the guard |
| **3 (final)** | **47 / 49** | permit/intent uniqueness; issuer volume check | Both are layered guards. Removing both layers together is **KILLED** (`reports/mutation_combined.json`) |

Reports: `reports/mutation_round{1,2,3}.txt|json`.

## T. K-LEAN second opinion

**SOURCE-DERIVED K-LEAN REVIEW. K-LEAN did not run.** It needs a NanoGPT/OpenRouter API key and a
LiteLLM proxy; none is available. I applied its published code-reviewer / security-auditor checklist
(`src/klean/data/agents/*.md`) myself.

| Finding (K-LEAN checklist frame) | Status |
|---|---|
| Resilience: retries on non-idempotent operations | Addressed. No resend; reconcile by client id |
| Authorisation, least privilege: decisions forgeable / reusable | Was a real defect → fixed (signatures, binding, single use) |
| Error handling: unexpected exceptions | Was a real defect → fixed (fail-closed catch-all) |
| Secrets management: in-process keys | Open. Needs process isolation |
| Test coverage of critical paths | Mutation-verified (§S) |

## U. iFixAI second opinion

**SOURCE-DERIVED iFixAI REVIEW. iFixAi did not run against this system.**

- iFixAi audits an LLM agent endpoint with a judge model. Sentinel deliberately has no LLM agent.
- Its only key-free mode (`--provider mock`) is documented as "a plumbing check, not a diagnosis".
- It was not run, because running it would have proved nothing about Sentinel.

I mapped its inspection taxonomy onto the source:

| iFixAi category | Assessment |
|---|---|
| Usurpation / Insubordination | Authority is enforced in code (permits, signatures, import graph); no prompt-level authority exists |
| Fabrication / Concealment | FACT only from hash-verified broker truth; append-only journal records rejections too |
| Oversight atrophy | Promotion needs an operator signature; live is unreachable |
| Systemic risk | Single executor and lease. Residual: no broker-side fence (§Q) |
| Miscalibration | The Finder emits no fake probability (`confidence=None`). The Validator's statistical test is uncalibrated (t-stat threshold 1.0, chosen a priori) |

## V. Disagreement register

| Primary finding | K-LEAN (source-derived) | iFixAI (source-derived) | Reproducible evidence | Unresolved | Disposition |
|---|---|---|---|---|---|
| Daily-bar holdout met pre-registered criteria | n/a (code review tool) | n/a | `reports/edge_holdout.json` | Development period contradicts it; BTC concentration | Recorded as **weak / unconfirmed**, not as edge |
| In-process key separation adequate for simulation | Flags as a secrets-management risk | Flags as an identity-attestation gap | INSPECTION | Process isolation design | Agree it blocks live; acceptable for simulation |

There were no material disagreements on defects. Both reviews are labelled source-derived and carry
no weight beyond the evidence.

## W. Edge / performance evidence

Trade model:
- signal at the close of bar *i*;
- entry at the open of bar *i*+1, plus half spread and slippage;
- stop at 2×ATR, filled at the gap open if price opens beyond it;
- time exit after 10 bars;
- round-trip commission deducted.

Costs come from `system.DEFAULT_COSTS` (e.g. XAU 0.30 spread / 0.10 commission / 0.10 slippage).

**Daily bars (the pre-registered primary set):**

| Period | Variant | n | Exp. R | t | PF | Win % | Max DD (R) | Bootstrap p5 |
|---|---|---|---|---|---|---|---|---|
| Development (≤2018) | Finder | 760 | −0.035 | −0.77 | 0.93 | 45.7 | 57.7 | −0.128 |
| **Holdout (2019→2026)** | **Finder** | **390** | **+0.164** | **2.33** | **1.40** | 46.7 | 10.1 | **+0.082** |
| Holdout | +Validator(+Arbiter) | 36 | +0.217 | 0.96 | 1.53 | 47.2 | 7.9 | n/a |

Holdout per instrument (n, R): XAU (94, +0.076), OIL (92, +0.095), JPY (67, −0.033),
**BTC (137, +0.367)**.

Pre-registered checks: all 8 TRUE (`criteria_checks`). Two implementation notes must be disclosed:

1. The "2× costs" criterion was computed as the mean of per-instrument 2× expectancies, not a pooled
   2× run.
2. The criteria were judged on the daily set. The pre-registration did not say explicitly which
   timeframe the pass/fail applies to.

**Hourly bars (what the live pipeline runs):**

| Period | Finder alone | +Validator / Arbiter |
|---|---|---|
| Development | −0.039 R (n 1,311, t −1.18) | +0.032 R (n 79, t 0.26) |
| Holdout | −0.026 R (n 1,169, t −0.74) | +0.185 R (n 68, t 1.60) |

**Sensitivity:**

- Walk-forward by year (fixed parameters, full daily sample): positive in 18 of 27 years (66.7 %).
  Clustered losses: 2001, 2005, 2008, 2010, 2015–16, 2025.
- Cost stress (full daily sample, expectancy R at 0/1/2/3/5× costs):

  | Instrument | 0× | 1× | 2× | 3× | 5× |
  |---|---|---|---|---|---|
  | XAU | .056 | .029 | .001 | −.027 | −.079 |
  | OIL | .024 | .005 | −.014 | −.032 | −.067 |
  | JPY | .012 | −.004 | −.018 | −.033 | −.061 |
  | BTC | .612 | .130 | −.040 | −.150 | −.304 |

  **The apparent edge disappears at about 2× modelled costs.**
- Parameter sensitivity (27 combinations, reported only, never used for selection): positive in
  22/27 (XAU), 19/27 (OIL), 16/27 (JPY), 16/27 (BTC).
- Null model (random entries): ≈0 R for XAU/OIL/JPY, −0.19 R for BTC. The harness is not
  manufacturing edge.

**Paper replays of the FULL production pipeline** (sim broker, hourly, $100k simulated account):

| Period | Cycles | Trades | Net |
|---|---|---|---|
| Development year | 5,881 | 54 | +$449 |
| Holdout year | 8,733 | 30 | +$1,081 |

The holdout replay was halted in 5,018 cycles by the loss-streak scoping defect (fixed afterwards,
**not re-run** on the holdout).

**Verdict: EDGE NOT ESTABLISHED.**
- The daily hypothesis passed its pre-registered holdout. But it is contradicted by its own
  development period, concentrated in one instrument, cost-fragile, and measured on proxy prices.
- The hourly configuration that the pipeline actually trades shows no demonstrated edge.

## X. Data-leakage results

| Check | Result |
|---|---|
| Future-perturbation | 0/100 decisions changed (PASS) |
| Positive control | A one-bar look-ahead variant inflates expectancy to +0.33…+1.34 R. The harness detects leakage when it exists (PASS) |
| Finder unfinished-bar guard; validator future-bar rejection; analog windows strictly before signal; train/holdout separation enforced by the promotion journal; no hyperparameter search | PASS |
| Revised macro data / future news / normalisation leakage | Not applicable: no macro or news features are used by Finder or Validator |
| Holdout reuse | Prevented by the journal |
| Process caveat | The development and holdout splits were run after a smoke test touched the last 20 hourly days (0 trades, no P&L); disclosed in the pre-registration |

## Y. No-trade counterfactual results

Source: `reports/pipeline_{dev,holdout}.json`. Counterfactual R uses the same trade model.

| Stopped at | Dev n / R / t | Holdout n / R / t | Reading |
|---|---|---|---|
| Arbiter REJECT (Validator INVALID) | 1,601 / −0.023 / −0.83 | 914 / −0.017 / −0.47 | Rejected trades were slightly negative; not significant |
| Arbiter INDETERMINATE | 329 / −0.117 / −2.05 | 175 / +0.109 / +1.07 | **Inconsistent across periods** |
| Risk BLOCK | 136 / +0.097 / 1.05 | 109 / +0.136 / 1.34 | Risk blocked mildly positive trades: a safety cost, not significant |
| News BLOCK (synthetic NFP only) | 2 / −1.02 | 3 / −0.05 | Sample too small |
| Executed | 54 / +0.042 / 0.28 | 30 / +0.108 / 0.63 | Not significant |

Compliance blocked nothing in replay: Challenge profile, never near limits. Its value in replay is
**not measured**.

## Z. Component attribution

| Component | Earns its place on edge? | Earns its place on safety? |
|---|---|---|
| Validator | Unproven; contradictory evidence | Yes: rejects corrupted / stale / future data deterministically |
| Arbiter | Adds no filtering beyond the Validator in these runs (identical results) | Yes: deterministic, signed, bound decisions; enforces complete evidence |
| Risk, Compliance, News, Reconciler, Lease, Journal | Not measurable as edge | **Yes**: every one is mutation-verified (§S) |

Flagged for review, **not removed**: the Arbiter's edge-threshold rule (R8) duplicates the
Validator's cost-survival test in practice.

## AA. Unresolved blockers

1. No MT5 / broker adapter. Order filling modes, server time, history lag and symbol specs are
   unverified.
2. No broker-side fencing. A second process holding stale leadership is prevented only by local
   lease checks.
3. FTMO profiles are UNREVIEWED and expire 2026-11-01. The account phase and product must be chosen
   by a human.
4. Single calendar source. No historical calendar. FTMO's own "Restricted event" flags are not
   ingested (titles are matched instead).
5. In-process key separation.
6. No demonstrated edge on the deployable configuration.
7. Proxy data only.
8. Shadow operation is one live cycle (Friday night: 3 markets closed, BTC had no setup).
9. Validator independence and value are unproven.

## AB. Minimum missing set (to reach a credible shadow)

1. MT5 read-only adapter: quotes, bars, account, deals, positions, server time.
2. Shadow scheduler running continuously for weeks, with the calendar refreshed hourly.
3. Broker-CFD historical data, with the edge study re-run on it.
4. A second scheduled-event source, or FTMO's restricted-event list.
5. A human review of the FTMO profile for the actual account.

## AC. What must be true before SHADOW (continuous, live data)

- Read-only broker adapter passing a contract-test suite against a demo account.
- Clock sync with the broker within 2 s, alerting on drift.
- Calendar refresh loop with staleness alerts.
- Journal backup and integrity check on a schedule.
- All tests passing; the mutation kill rate held at ≥95 % with no unexplained survivor.
- The shadow journal reviewed weekly, comparing shadow decisions with what the replay predicts.

## AD. What must be true before PAPER (broker demo account)

- Everything in AC, plus 4+ weeks of clean shadow with no unexplained halt.
- Execution adapter with idempotency by client id (comment / magic) and a broker-history lookup
  tested against demo timeouts.
- An execution gateway as a separate process holding the only broker credentials and the permit
  key (closes the in-process key gap).
- Fencing at the gateway.
- FTMO profile human-APPROVED for the target product.
- Re-qualification of the full suite against the demo broker.

## AE. What would have to be true before MICRO/LIVE could even be considered

1. **Edge evidence:**
   - the pre-registered hypothesis passes a **fresh, never-touched forward period**, on broker data,
     in the deployable timeframe;
   - expectancy survives ≥2× realistic costs;
   - no single-instrument concentration explains it;
   - paper P&L agrees with the replay's prediction within its confidence interval.
2. **Safety evidence:**
   - months of paper with zero duplicate orders, zero unexplained positions and zero
     compliance-limit approaches without a halt;
   - every crash and chaos test repeated against the demo broker;
   - split-brain tested across two real hosts.
3. **Governance:** a written authorization from you naming account, product profile, size limits and
   kill-switch owner. The promotion pipeline's operator signature records it.

**None of these are met today. This is where the work stops. Live deployment requires your explicit
authorization and is not recommended on current evidence.**
