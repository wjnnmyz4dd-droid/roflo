# SENTINEL — Next-Phase Report (Integration, Edge Research, Re-qualification)

Date: 2026-10-03. Branch `claude/ai-trading-agent-audit-e3teyy`. Phase-1 baseline: commit `97c5b01`
(`QUALIFICATION_REPORT.md`).

**No live broker connection exists. No real order has ever been sent. No real-money account was
touched. Nothing in this phase authorizes demo trading, micro-live, or live.**

Evidence labels used throughout:

| Label | Meaning |
|---|---|
| **EXECUTED** | Ran here, on real processes/data; output is in `reports/` or reproducible by a named test/tool |
| **SOURCE-VERIFIED** | Established by reading primary sources (code, official pages), not by running |
| **SIMULATED** | Ran here against a simulator or fake (SimBroker, `tests/fake_mt5.py`, FakeClock replay) |
| **NOT EXECUTED** | Not run; reason stated |
| **BLOCKED** | Could not be run without something only the owner can provide, or a source refused access |

---

## Executive summary

1. **Safety architecture: frozen and re-qualified.** Every change to a safety component in this
   phase is a documented, reproduced defect fix or the explicitly requested split-brain
   strengthening (§2–§3). No new authority was introduced.
   - Tests: **241** passing. All 144 phase-1 test IDs are still present and passing; two were
     *extended*, none weakened (§1).
   - Mutation round 4: 68/70 killed in the full run. Both survivors were analysed: one is the known phase-1 layered guard; the other was a real test gap, now closed. One kill was for the wrong reason and led to defect D5. After the fixes, 74 mutants are applied in total and only the layered guard survives (§29).
2. **MT5:** a demo-only adapter is written and contract-tested against a fake terminal (**SIMULATED**).
   - Real MT5 is **BLOCKED**: the `MetaTrader5` package is Windows-only, there is no terminal in
     this Linux container, and there are no demo credentials. **Execution stays DISABLED.**
   - Broker data is **BLOCKED** for the same reason. All market data is still Yahoo proxy data,
     labelled `PROXY / RESEARCH-ONLY`.
3. **Edge: none found.**
   - Two new pre-registered lineages failed at the validation gate:
     - L2: time-series momentum;
     - L3: FX local-hours seasonality.
   - Both holdouts therefore stay **closed**.
   - The phase-1 breakout (L1) stays frozen and unpromoted.
   - **There is no surviving strategy.**
4. **News:** a point-in-time FOMC calendar (2000–2027) was built from federalreserve.gov.
   - Measured news value on the frozen L1 is **negligible and inconclusive**: the gate removes
     ≤0.7 % of trades.
   - NFP/CPI history is **BLOCKED** (BLS 403; ALFRED unreachable).
5. **FTMO: OWNER INPUT REQUIRED — FTMO PRODUCT/PHASE.**
   - The five profiles remain UNREVIEWED, simulation-only, and expire 2026-11-01.
   - New adversarial tests pass: swaps, commissions, exact boundaries, restart in a restricted
     window, expiry mid-session, operator-resume bypass.
6. **Split-brain:** the fenced send ledger closes the "paused leader wakes and sends" hole for every
   pause *before* the fenced record. This is **EXECUTED** with real processes and SIGSTOP.
   - One residual remains and is pinned by a test: a pause *after* the record sends late.
   - The new leader detects it, but cannot prevent it, because MT5 has no broker-side fence.
7. **Stress:**
   - **MEMORY PRESSURE** and **CPU STARVATION** were executed. Every memory-exhaustion point failed
     closed (HALT); restart invariants held. The fence held under 4× CPU oversubscription.
8. **Continuous Shadow:**
   - **EXECUTED** live on proxy data for {{LIVE_MIN}} minutes. It is a Saturday, so only BTC trades.
   - A 120-day SIMULATED shadow replay produced 870 candidates, 0 order events and verified journals.
   - **Broker-demo paper: BLOCKED** (no MT5, no demo account), and the Shadow prerequisites are not
     met anyway (§34).

**Bottom line:** nothing qualifies for broker-demo, let alone micro-live. The minimum missing set is
in §33.

---

## 1. Existing architecture verification

| Check | Result | Label |
|---|---|---|
| Phase-1 suite preserved | All **144** phase-1 test IDs collected at `97c5b01` are present at HEAD and pass (`comm` of collected IDs: 0 missing) | EXECUTED |
| Phase-1 tests modified | 2, both strictly extended (see the note below) | EXECUTED (git diff) |
| Authority chain unchanged | FTMO Compliance → News → Finder → Validator → Arbiter → Risk → Permit → Execution → Broker → Reconciler → Journal → Learning → Research → Promotion. `test_authority_matrix_has_no_duplicate_execute_or_propose` passes: one executor, one proposer | EXECUTED |
| Single broker-order path | `ExecutionService.submit(permit)`. `test_no_component_but_execution_calls_send_order` passes, now including `mt5.py`'s callers | EXECUTED |
| No new authority | New modules (`dataquality`, `calendar_hist`, `lineages`, `shadow_runner`, `broker/mt5`) are a data gate, research code, a shadow driver and a broker *port implementation*. None can issue permits, sign decisions or call `send_order` | SOURCE-VERIFIED + EXECUTED (import/inventory tests) |

The two extended phase-1 tests:

1. `test_bypass_surfaces_inventory` now allows `mt5.py`, a broker port implementation like
   `sim.py`, and `shadow_runner.py`, which only sets quotes on a shadow-mode SimBroker. It gains a
   new assertion that `shadow_runner.py` never builds paper mode.
2. `test_crash_matrix.OPEN_POINTS` gains the new crash point `exec_after_fenced_record_before_send`.

## 2. Changes made

Commits since phase 1: `5ac0993` (pre-registration, before any computation), `d236076`, `aac4576`,
`062f0a4`, `c5b51e6`, `3c8be9f`, `496dbae` and the final report commit.

| Area | Change | Kind |
|---|---|---|
| `risk.py` | Swaps included in daily P&L and the loss-streak test | Defect fix D1 |
| `reconcile.py` | MT5 history lag → retryable `INTENT_UNRESOLVED_RETRY` (no guessing); reconcile-scope vs. manual-scope halts | Defect fix D2 |
| `lease.py`, `execution.py`, `reconcile.py`, `system.py` | Fenced send ledger (`Lease.record_send`), in-flight-send detection by the new leader | Requested split-brain strengthening (S1) |
| `broker/mt5.py` | Demo-only MT5 adapter (new broker port implementation) | New integration (W2) |
| `broker/mt5.py`, `reconcile.py` | Intent lookup window bound to the intent's age, not a fixed 7 days | Defect fix D3 (found in this phase's source review) |
| `shadow_runner.py` | Only bar-aligned, fully closed bars drive decisions and staleness | Defect fix D4 (found by the live shadow run) |
| `lease.py` | Bounded retry of store setup when instances create it simultaneously | Defect fix D5 (found via a wrong-reason mutation kill) |
| `dataquality.py` | Provenance + data-quality gate (FAIL / PROXY_ONLY / BROKER_GRADE) | New gate on research inputs (W1) |
| `research/calendar_hist.py` | Point-in-time FOMC calendar → `CalendarSnapshot` for `NewsEngine` | New data source (W3) |
| `research/lineages.py`, `research/edge.py` (`skip=` hook) | L2/L3 lineages; gate-attribution hook | Research (W6) |
| `shadow_runner.py` | Continuous shadow loop + separate counterfactual journal | W5 |
| Tests | `test_mt5_adapter` (18), `test_split_brain` (10), `test_stress` (8), `test_calendar_hist` (40), `test_ftmo_adversarial` (7), `test_shadow_runner` (5), `test_dataquality` (4); plus regression tests `test_risk_daily_loss_includes_swaps` (D1), `test_old_unknown_intent_is_found_in_history_after_long_outage` (D3), `test_in_progress_unaligned_bar_does_not_create_new_decisions` (D4), `test_mp_simultaneous_creation_of_coordination_store` (D5) and `test_unknown_intent_with_history_unavailable_keeps_reconcile_unclean` (mutation gap) | — |
| Tools | `run_phase2`, `data_quality`, `fence_actor`, `build_fomc_calendar`, `news_attribution`, `shadow_continuous`; `crash_scenario` gains memory pressure; `mutate` gains round 4 | — |

## 3. Why each change was necessary

Safety-component changes follow the freeze protocol.

**D1. Risk ignored swaps.**
- *Defect:* `RiskEngine` computed today's P&L and the loss streak from `profit − commission` only,
  while `ComplianceEngine` (correctly) includes swaps.
- *Reproduction:* a day with −600 swap per deal and zero profit; compliance sees the loss, risk
  does not.
- *Why the design fails:* two authorities disagree about the same broker truth; the internal daily
  limit under-counts carry costs on multi-day holds (L2 holds a month).
- *Minimum change:* add `+ swap` in two expressions.
- *Regression test:* `test_risk_daily_loss_includes_swaps` (a swap-only day crossing the internal 1.5 % limit must HALT).
- *Adversarial:* mutation "risk: swaps excluded from daily P&L" (§29).

**D2. Reconciler could not express "not knowable yet".**
- *Defect:* MT5 deal history can lag positions. The reconciler had only CONFIRMED / NOT_EXECUTED,
  so a lagging history either resolved an executed order as NOT_EXECUTED or raised a false
  foreign-position alarm (manual halt).
- *Reproduction:* `test_history_lag_is_not_guessed_as_not_executed` (`history_lag=50`).
- *Minimum change:* `BrokerUncertain` from `find_by_client_id` → blocking finding with
  `retry=True`. A halt is reconcile-scoped only if *all* blocking findings are retryable.
- *Regression/adversarial:* that test, plus mutations "reconcile: unresolvable intent not blocking"
  and "reconcile: every blocking finding auto-retryable" (§29).

**D3. MT5 intent lookup window was a fixed 7 days.**
- *Defect:* `MT5Broker.find_by_client_id` searched only the last 7 days of deal history.
- *Reproduction (EXECUTED, SIMULATED terminal):*
  1. Order `UNKNOWN` (`none_result_after_fill`).
  2. The broker closes the position.
  3. Sentinel is down for 8 days.
  4. Reconcile journals `RESOLVED_NOT_EXECUTED` although the broker holds the intent's IN and OUT
     deals.
- *Why the design fails:* "absent from a window" was treated as "never executed".
- *Minimum change:* the reconciler passes the intent's persisted timestamp. The adapter searches
  from there (minus 1 day). `SimBroker` ignores the argument.
- *Regression:* `test_old_unknown_intent_is_found_in_history_after_long_outage` (CONFIRMED; `order_send_calls == 2`: the original send plus the broker-side close, nothing re-sent).
- *Adversarial:* mutation round 4 addendum (§29).

**D4. Shadow runner re-decided every poll (non-safety; found live).**
- *Defect:* Yahoo appends an in-progress bar stamped at the latest minute (e.g. `04:54`). The
  runner keyed "one decision per bar" on the last bar's timestamp, so every 5-minute poll was a new
  decision.
- *Reproduction:* `reports/shadow_live_pre_d4.log` shows 4 DECIDED in 4 polls within one hour.
- *Minimum change:* keep only bar-aligned, fully closed bars.
- *Regression:* `test_in_progress_unaligned_bar_does_not_create_new_decisions`.
- *Impact:* none on trading. Shadow only; the Finder already ignored unclosed bars. Decisions were
  duplicated, not wrong.

**D5. Lease store creation race (availability).**
- *Defect, reproduction and fix:* see §29.
- *Why the design failed:* SQLite's busy timeout does not cover the WAL switch.
- *Minimum change:* retry setup on "locked" until `busy_timeout`, then raise (still fails closed).

**S1. Split-brain strengthening (explicitly requested).**
- *Defect:* a leader frozen (GC, VM pause, SIGSTOP) between its last lease check and the broker
  call wakes after a takeover and sends. SimBroker's fence would refuse it; **MT5 has no
  broker-side fence**.
- *Reproduction (EXECUTED):* `test_mp_leader_frozen_before_fenced_record_is_refused_on_wake`
  (fails without the ledger).
- *Minimum change:* a compare-and-insert in the existing coordination DB immediately before the
  broker call:
  - `BEGIN IMMEDIATE`;
  - verify owner + token + expiry > now + guard;
  - insert `(intent_id, owner, token, ts)`.

  The new leader's reconciler treats another owner's recent ledger row with no broker deal as
  `IN_FLIGHT_SEND_BY_OTHER_INSTANCE` (blocking, retry). No new authority: the same Lease already
  gated execution.
- *Regression:* `test_execution_refuses_send_when_lease_lost_between_check_and_send`,
  `test_reconciler_sees_other_instance_in_flight_send_then_late_fill_is_manual`; crash point
  `exec_after_fenced_record_before_send`.
- *Adversarial:* multi-process suite (§8) and mutations "lease: record_send fence removed",
  "execution: fenced send ledger bypassed", "reconcile: other instance's in-flight send ignored".

**Non-safety additions** (data gate, adapter, calendar, shadow runner, research) were required by
workstreams 1, 2, 3, 5 and 6 respectively.

## 4. MT5 data pipeline

| Item | Status | Label |
|---|---|---|
| `MT5Broker.history_bars` (`copy_rates_range`) / `history_ticks` (`copy_ticks_range`), server-time → UTC conversion, provenance record | Implemented | SIMULATED (fake terminal) |
| Acquiring real broker bars/ticks | `pip download MetaTrader5` fails (no Linux distribution). No terminal, no account | **BLOCKED** |
| Data-quality gate | `dataquality.check` → FAIL / PROXY_ONLY / BROKER_GRADE. BROKER_GRADE requires `source_kind=BROKER`, positive spreads and a symbol spec; proxy data can never pass (`test_dataquality`, 4 tests) | EXECUTED |
| Never mixing proxy and broker | `DatasetProvenance.source_kind` is mandatory. PROXY is force-labelled `PROXY / RESEARCH-ONLY`; every research report carries the provenance string | EXECUTED |

Data-quality verdicts on the datasets used so far (`reports/data_quality.json`, **EXECUTED**):

| Dataset | Verdict | Main reason |
|---|---|---|
| XAUUSD 1d (GC=F) | **FAIL** | 7.1 % missing bars; 524 OHLC-inconsistent bars |
| XAUUSD 1h | **FAIL** | 2.0 % missing; 3,057 OHLC-inconsistent bars |
| USOIL 1d (CL=F) | PROXY_ONLY | — |
| USOIL 1h | **FAIL** | 2.8 % missing |
| USDJPY 1d (JPY=X) | **FAIL** | 2.4 % missing |
| USDJPY 1h | PROXY_ONLY | — |
| BTCUSD 1d / 1h | PROXY_ONLY | — |

**Consequence:** the phase-1 evidence on XAU daily, XAU/OIL hourly and JPY daily was computed on
datasets that now **fail** the gate. Phase-1 edge numbers on those series are weaker evidence than
the phase-1 report implied. The CME daily maintenance break (21–22 UTC) is excused for GC=F/CL=F;
holidays are not.

## 5. Broker symbol/spec report

**BLOCKED.** No broker server is reachable, so no real symbol list, contract sizes, tick values,
filling modes, trading sessions, swap rates or commissions could be read.

What exists (SIMULATED):
- `MT5Broker.spec()` maps `symbol_info`:
  - `trade_tick_size`, `trade_tick_value`;
  - `volume_min`, `volume_max`, `volume_step`, `trade_contract_size`;
  - `currency_base`, `currency_profit`;
  - `filling_mode`, `trade_mode`.
- `test_spec_comes_from_broker_metadata` shows a changed tick value flows into sizing.
- `DEFAULT_COSTS` / `DEFAULT_SPECS` remain **assumptions**, not broker facts.

## 6. MT5 adapter

`src/sentinel/broker/mt5.py`. **SIMULATED only.** Not verified against a real terminal.

| Property | Implementation | Test (all SIMULATED, all PASS) |
|---|---|---|
| Demo-only guard | Execution enabled only if all hold: `account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO`; an explicit `DemoAuthorization("DEMO", logins, servers, magic)`; login and server in the allow-lists. Re-checked **before every send/close** | `test_execution_disabled_unless_authorized_demo` ×5 (REAL, CONTEST, no auth, wrong kind, wrong login: 0 `order_send` calls); `test_relogin_to_real_account_after_connect_disables_execution` |
| Read-only on any account | Quotes, account, history work; trading raises `EXECUTION DISABLED` | `test_shadow_with_real_account_reads_but_cannot_trade` |
| Idempotency | `magic` + comment `sn:` + sha256(intent_id)[:24] (≤31 chars) | `test_pipeline_unknown_order_reconciled_through_mt5_history_without_resend` |
| Retcodes | DONE → FILLED; DONE_PARTIAL → PARTIAL; TIMEOUT / CONNECTION / `None` → UNCERTAIN (halt + reconcile, never resend); others → REJECTED | `test_retcode_classification` ×5; `test_partial_fill_and_filling_mode` |
| Server time | Offset from tick time in whole hours; a non-whole-hour offset or an offset change (DST) → `BrokerUncertain` → halt + re-reconcile | `test_server_time_offset_is_removed_and_irregular_offsets_are_uncertain` |
| History lag | A position without its IN deal → `BrokerUncertain` (retry), never NOT_EXECUTED | `test_history_lag_is_not_guessed_as_not_executed` |
| Broker rewrites comments | Unattributable position → FOREIGN → manual halt | `test_broker_rewritten_comment_fails_closed` |
| Filling mode | From `symbol_info.filling_mode` (FOK → IOC → RETURN) | as above |

The fake terminal's constant values are arbitrary by design: the adapter uses MT5 constants only by
name. Real-terminal behaviours not covered:
- reconnect semantics;
- `order_check`;
- requote loops;
- symbol suffixes;
- hedging vs. netting accounts (the adapter assumes hedging).

## 7. Execution/reconciliation qualification

| Scenario | Result | Label |
|---|---|---|
| Phase-1 exactly-once / UNKNOWN / partial / reconcile suite (unchanged) | PASS | SIMULATED |
| Crash matrix: 13 entry points + 3 exit points + 4 scenario tests, real `os._exit` kills, restart invariants (no duplicate, no unauthorized fill, no unresolved intent, no orphan, P&L = broker) | 20/20 PASS, including the new `exec_after_fenced_record_before_send` | EXECUTED (real processes, SimBroker) |
| UNKNOWN through MT5 → reconciled via (magic, comment), `order_send_calls == 1` | PASS | SIMULATED |
| UNKNOWN + history lag → HALT(reconcile), resolved once history arrives | PASS | SIMULATED |
| UNKNOWN + 8-day outage + broker closed the position (D3) | Before the fix: wrongly `RESOLVED_NOT_EXECUTED`. After: `RESOLVED_CONFIRMED`, no resend | SIMULATED |
| Against a real MT5 demo | — | **BLOCKED** |

## 8. Split-brain

Real OS processes on one host, real wall clock, SimBroker with **fencing disabled** (like MT5).
`tests/test_split_brain.py`, `tools/fence_actor.py`. Invariants checked after every run:
- one owner per fencing token;
- every broker order has a ledger row (nothing reached the broker unfenced);
- ledger tokens are monotone in time.

| Scenario | Outcome | Label |
|---|---|---|
| Two processes racing for 5 s | Invariants hold; every send fenced | EXECUTED |
| Leader `kill -9`; standby takes over after TTL | B's first send is after the kill; B's tokens are all greater than A's | EXECUTED |
| Leader frozen (SIGSTOP) **before** the fenced record, B takes over, A resumed | A refused: `fenced send refused (owner=B …)`; A's order never reaches the broker | EXECUTED |
| Leader frozen **after** the fenced record, B takes over, A resumed | **Residual:** A's order lands late (no broker-side fence). B sees A's in-flight ledger row at once (`sends_by_others`); its reconciler halts (`IN_FLIGHT_SEND_BY_OTHER_INSTANCE`, retry), then classifies the late fill as FOREIGN → **manual** halt | EXECUTED (late send) + SIMULATED (reconciler path, in-process) |
| Coordination store unavailable (partition: exclusive lock held, 0.3 s busy timeout) | No send; the actor fails at startup or every cycle errors | EXECUTED |
| Clock disagreement ±5 s between processes | Takeovers can happen early; stale attempts are refused by the token check; invariants hold | EXECUTED |
| Simultaneous creation of the coordination store by 12 processes ×10 rounds (D5) | All start (failed before the fix) | EXECUTED |
| Stability | Early runs exposed three harness issues, all fixed in the *test/actor*: re-pausing after the first pause, a fail-at-startup partition outcome, and reading the ledger before its schema existed. Then 6 consecutive clean runs of the 9-test suite. After the D5 fix: 5 more consecutive clean runs of split-brain + stress (18 tests) | EXECUTED |
| Two hosts / network-filesystem coordination | — | **NOT EXECUTED** (single container) |

**Residual risk, stated plainly:** with MT5 the window between the fenced record and `order_send`
returning cannot be closed from the client side. It is detected (new-leader halt, then manual
review), not prevented.

The phase-1 recommendation stands: before any demo trading, put a single execution gateway process
in front of the terminal that holds the only credentials, and serialize sends there.

## 9. Historical news source

| Source | Status | Label |
|---|---|---|
| federalreserve.gov `fomchistorical{2000..2020}.htm` + `fomccalendars.htm` (2021–2027) | Fetched (HTTP 200), parsed by `research/calendar_hist.py` into `data/calendar/fomc.json`; each record carries `source_url` + `source_sha256` | EXECUTED |
| Count | 8 scheduled decisions per year (2003: two listed dates merged into one widened window; 2020: 7 scheduled + 2 unscheduled), plus 25 conference calls (2001–2011). Notation votes excluded | EXECUTED (parser checked against the pages; 2017/2018 cross-month meetings were initially missed and fixed) |
| BLS (NFP/CPI release history) | HTTP 403 via the proxy; not routed around | **BLOCKED** |
| FRED/ALFRED release dates | Timeouts | **BLOCKED** |
| Dukascopy / other commercial calendars | Connection reset, or licence unknown | **BLOCKED** / not used |
| Forex Factory live JSON | Live week only (as in phase 1) | EXECUTED (live) |

Canonical schema fields:
- `event_id`, `title`, `currency`, `impact`, `is_central_bank`;
- `kind` (`scheduled` / `unscheduled` / `conference_call`);
- `decision_date`, `window_utc` [earliest, latest];
- `time_precision`, `known_from`, `source_url`, `source_sha256`.

## 10. News replay

Point-in-time rules (`snapshot_at(records, as_of)`):
- **Scheduled meetings** are known from 1 January of their year. The schedule is published earlier;
  this choice is conservative.
- **Unscheduled meetings and conference calls** are known only from the end of their ET day. A
  replay therefore **cannot** block a trade before them, exactly as a live system could not.
- **Release time is bracketed, not guessed:**
  - 2013+ at 14:00 ET;
  - 2011–2012 between 12:30 and 14:15 ET;
  - earlier at ~14:15 ET.

  The snapshot emits enough points that the 60/30-min central-bank blackout covers the whole
  bracket.

Tests (`test_calendar_hist.py`, 40 incl. parametrized, **EXECUTED**):
- known decisions at correct UTC (EST/EDT);
- cross-month meetings;
- no record visible before `known_from` (sampled leakage probe);
- unscheduled 2020-03-15 invisible on the day before;
- `NewsEngine` blocks at 18:30 UTC on 2015-12-16 and allows at 15:00;
- the pre-2013 bracket is blocked continuously.

Residuals:
- A mid-year reschedule appears at its final date.
- **Coverage is FOMC only.** A replay snapshot passes `NewsEngine.health` while missing NFP/CPI, so
  replay results understate news exposure.

## 11. FTMO profile status

**OWNER INPUT REQUIRED — FTMO PRODUCT/PHASE.**
- Five profiles exist:
  - 2-Step Challenge;
  - 2-Step Verification;
  - 2-Step FTMO Account;
  - 1-Step Challenge;
  - 1-Step FTMO Account.

  All Standard, all `UNREVIEWED`, valid 2026-10-02 → 2026-11-01. Sources are hashed (phase 1,
  SOURCE-VERIFIED).
- Which product and phase applies to the owner's account cannot be inferred and was not guessed.
- Outside simulation every profile HALTs until a human marks it APPROVED. After 2026-11-01 every
  profile HALTs until the rules are re-verified.

## 12. FTMO adversarial results

All **EXECUTED** unit/integration tests on simulated accounts; all PASS.

| Attack / edge | Expected | Test |
|---|---|---|
| Swap booked exactly at CE(S)T midnight | Counted as today's flow → stricter floor | `test_swap_charged_at_midnight_counts_conservatively` |
| Accumulated swaps + commissions reach −5 % | HALT with zero trading P&L | `test_accumulated_swaps_and_commissions_can_cause_halt` |
| Commission makes the worst case cross the floor | BLOCK (ALLOW without commission) | `test_commission_is_part_of_worst_case` |
| Floating equity exactly at the limit / +0.01 / at the buffer edge / +0.01; 1-Step 3 % | HALT / BLOCK / BLOCK / ALLOW; HALT | `test_floating_loss_exact_boundaries` |
| Profile expires mid-session; host clock rolled back before `valid_from` | HALT (and an expired profile never traps a close) | `test_profile_expiry_mid_session_and_clock_rollback_halt` |
| Restart inside an FTMO Account restricted window | Still blocked after restart; no position | `test_restart_during_restricted_window_still_blocks` |
| Operator RESUME while equity is below the floor (manual bypass) | Cycle still HALTs with "FTMO limit breached"; no new IN deal | `test_operator_resume_cannot_override_a_compliance_breach` |
| Forged / swapped / replayed decisions, permits (phase 1) | Refused | `test_authority_and_journal` (unchanged, PASS) |
| DST midnight (phase 1) | Correct | `test_cest_cet_midnight_dst` |

Not modelled (unchanged):
- Best Day;
- minimum trading days;
- profit target;
- Forbidden Trading Practices;
- per-instrument session tables;
- FTMO's own restricted-event list (titles are matched instead).

## 13. Continuous Shadow

`shadow_runner.py` + `tools/shadow_continuous.py`. Shadow mode only: `Components.execution is None`
is asserted at every step, and the bypass test forbids paper mode in this module.

Per step:
1. Refresh feeds; on failure keep the old data and journal `FEED_ERROR` / `CALENDAR_ERROR`.
2. Staleness gate (`FEED_STALE` → no decision).
3. One decision per closed bar (unique key, survives restart).
4. Resolve counterfactuals into a separate journal.

| Run | Result | Label |
|---|---|---|
| Unit tests | 5/5 PASS: one decision per bar; feed outage → `FEED_STALE`; calendar outage → news BLOCK; restart does not re-decide; counterfactual written once, only after the full horizon, and the production journal is unchanged | EXECUTED |
| **Live**, real clock, Yahoo hourly proxy + live Forex Factory calendar, {{LIVE_MIN}} min, poll 300 s | {{LIVE_RESULT}} | EXECUTED on PROXY data (Saturday: only BTCUSD open) |
| **Replay**, 120 days to 2026-10-02, hourly FakeClock, point-in-time FOMC calendar | 2,823 decisions, 57 `FEED_STALE` (gaps), 870 candidates: 801 Arbiter REJECT, 65 INDETERMINATE, 1 news BLOCK, **3 WOULD_EXECUTE**. 0 order events; all three journals verify | SIMULATED |

The replay window lies inside the phase-1 hourly holdout year. It replays the frozen pipeline for
operational evidence only; nothing was selected from it.

## 14. Broker-demo status

**BLOCKED.** No MT5 terminal can run here, no demo account or credentials exist, and execution
therefore stays DISABLED by construction.

Even with credentials, the Shadow prerequisites (§34) are not met, so broker-demo paper would not
start. No demo order was attempted.

## 15. Edge hypotheses

| ID | Hypothesis | Source of the idea | Status |
|---|---|---|---|
| L1 | 20-bar Donchian breakout, 2×ATR stop, 10-bar exit (phase 1) | Classic trend-following | **Frozen; not promoted; not tuned** |
| L2 `tsmom_12m_v1` | Sign of the 12-month return predicts the next month (time-series momentum) | Moskowitz, Ooi & Pedersen (2012) | Failed validation gate |
| L3 `fx_local_hours_v1` | USDJPY: USD weakens in Tokyo hours and strengthens in New York hours | Breedon & Ranaldo (2013) | Failed validation gate |

The papers are untrusted text. They only shaped hypotheses written into the pre-registration; they
have no execution authority.

## 16. Preregistered experiments

`research/PREREGISTRATION_PHASE2.md`, committed in `5ac0993` **before any computation**.

- **Splits:**
  - L2: development ≤2010, validation 2011–2018, holdout 2019+;
  - L3: thirds of the hourly sample.
- **Validation gates:**
  - L2: expectancy > 0 and n ≥ 60 (non-BTC pooled);
  - L3: expectancy > 0 and n ≥ 100.
- **Holdout criteria (L2):** n ≥ 60, ≥ 0.10 R, t ≥ 2, PF ≥ 1.2, > 0 at 2× costs, ≥ 2/3 instruments
  positive, bootstrap p5 > 0.
- **Holdout mechanics:** opened once, only via `PromotionPipeline`, only if the gate passed.
- **Disclosure:** the holdout periods overlap periods seen in phase 1 (L1 daily holdout 2019+;
  hourly holdout year).

## 17. Failed strategies

All **EXECUTED** on PROXY data (`reports/phase2_devval.json`).

| Lineage | Development | Validation | Gate |
|---|---|---|---|
| L2 TSMOM (non-BTC pooled) | n 346, +0.066 R, t 1.11, PF 1.17 | n 287, **−0.015 R**, t −0.21, PF 0.97 | **FAIL** |
| L3 FX local hours | n 340, **−0.060 R**, t −1.45 | n 341, **−0.040 R**, t −1.02 | **FAIL** |
| L1 breakout (phase 1, frozen) | daily −0.035 R (n 760); hourly −0.039 R (n 1,311) | holdout daily +0.164 R (BTC-concentrated, cost-fragile); hourly −0.026 R | Not established; not promoted |

L2 per instrument in validation:

| Instrument | Expectancy |
|---|---|
| XAU | −0.109 |
| OIL | +0.020 |
| JPY | +0.044 |
| BTC | +0.055 (n 44; excluded from the gate by design) |

Holdout attempts for L2 and L3 were **REFUSED** by the pipeline (`reports/phase2_holdout_refusals.txt`).
No rescue was attempted, and no further hypotheses were added after seeing these results.

## 18. Surviving strategies

**None.**

## 19. Final holdout

| Lineage | Holdout status |
|---|---|
| L1 | Opened once in phase 1 (`reports/promotion.db`). Not reopened |
| L2 | **Not opened** (gate failed) |
| L3 | **Not opened** (gate failed) |

No holdout result was used to change anything.

## 20. Cost stress

Expectancy R at 1× / 1.5× / 2× / 3× modelled costs, **EXECUTED**:

| Lineage / period | 1× | 1.5× | 2× | 3× |
|---|---|---|---|---|
| L2 development | +0.066 | +0.014 | −0.038 | −0.143 |
| L2 validation | −0.015 | −0.069 | −0.123 | −0.231 |
| L3 development | −0.060 | −0.080 | −0.099 | −0.139 |
| L3 validation | −0.040 | −0.065 | −0.091 | −0.141 |

At 0× costs L3 is −0.021 in development and +0.010 in validation: there is no gross edge to
protect. L1 (phase 1): disappears at about 2× on the full sample.

Costs are `DEFAULT_COSTS` assumptions; real broker spreads and commissions are **BLOCKED**.

## 21. Validator attribution

There is no surviving Finder candidate, so the Validator cannot be qualified as edge-adding. Evidence
(post-hoc, multiple comparisons, not significant unless stated):

- **Phase 1:** point-biserial(VALID, R) = 0.018 on daily (no information). Hourly ablation:
  development +0.032 R (n 79) vs Finder −0.039; holdout +0.185 R (n 68, t 1.60) vs −0.026.
- **Phase 2, frozen L1 replay, FOMC-gated variant:** unchanged (§23).
- **Phase 2, 120-day shadow replay counterfactuals (SIMULATED):**

  | Stopped at | n | Mean R | t |
  |---|---|---|---|
  | Validator INVALID → Arbiter REJECT | 798 | −0.051 | −1.22 |
  | Validator INDETERMINATE | 64 | −0.343 | **−3.13** |
  | WOULD_EXECUTE | 3 | −0.255 | — |

**Reading:**
- Rejected candidates lost money on average, so the Validator avoided losses in this window.
- The INDETERMINATE effect is large here but had the *opposite sign* in the phase-1 holdout
  (+0.109). It is not stable.
- **Earns its place on safety, unproven on edge.**

## 22. Arbiter attribution

- The Arbiter added **no filtering beyond the Validator** in any run: identical trade sets in phase 1
  and in this phase's attribution runs.
- Its value is integrity, not edge:
  - deterministic rule IDs;
  - signed, candidate-bound decisions;
  - refusal of incomplete evidence (mutations killed, §29).
- Rule R8 (edge threshold) still duplicates the Validator's cost test in practice. It is flagged,
  not removed (architecture frozen).

## 23. News attribution

Frozen L1, point-in-time FOMC gate (60 min before .. 30 min after), `tools/news_attribution.py` →
`reports/news_attribution.json`, PROXY data, **EXECUTED**:

| Cadence / period / variant | Baseline n, R | With FOMC gate n, R | Removed by gate (n, mean R) | Held through FOMC, not gated (n, R) |
|---|---|---|---|---|
| Daily / dev / Finder | 760, −0.035 | 758, −0.034 | 2, −0.55 | 144, −0.034 |
| Daily / holdout (info) / Finder | 390, +0.164 | 390, +0.164 | 0 | 81, −0.003 |
| Hourly / dev / Finder | 1,311, −0.039 | 1,306, −0.036 | 5, −0.63 | 21, −0.25 |
| Hourly / holdout (info) / Finder | 1,169, −0.026 | 1,168, −0.025 | 1, −1.01 | 26, +0.03 |
| Any period / +Validator+Arbiter | — | identical | 0 | ≤ 4 |

**Reading:**
- The decision-time FOMC gate changes ≤ 0.7 % of trades. Removed trades were losers, but n ≤ 5.
  **News value: negligible and inconclusive.**
- Exposure the gate *cannot* see (positions held through a release) is far larger: 144 daily trades
  in development.
- The two daily removals come from the post-event tail of conference-call days.
- Phase-1 synthetic-NFP figures (n = 2–3) remain uninformative.
- Real NFP/CPI attribution: **BLOCKED** (no legitimate historical source reachable).

## 24. Risk attribution

- **Phase 1 counterfactual:** Risk BLOCK 136 (dev) / 109 (holdout) candidates at +0.097 / +0.136 R
  (t 1.05 / 1.34). Risk blocked mildly positive trades. That is a safety cost; it is not
  significant.
- **Phase-2 shadow replay:** Risk blocked nothing; candidates died earlier (Validator/Arbiter).
- **Safety value (EXECUTED):**
  - every risk mutation is killed, including the new swap one after its regression test (§29);
  - the operator-resume bypass is refused by compliance (§12).

## 25. Counterfactual NO_TRADE

- **New in this phase:** a separate append-only counterfactual journal (`counterfactual.db`).
  - Each journaled candidate is resolved **once**, only after its full horizon is observable, with
    the same trade model and costs.
  - Nothing reads it back into production; the test asserts the production journal is unchanged.
- **Results:** §21 table (shadow replay) and phase-1 §Y (pipeline replays).
- **NO_TRADE conclusion:** across 862 NO_TRADE candidates in the shadow replay, abstaining cost
  nothing; the average missed candidate lost.

## 26. Learning/memory

- Unchanged and still passing:
  - FACT only from hash-verified broker truth;
  - inferences never become facts;
  - lessons are proposals only;
  - promotion needs ordered stages, a single holdout and an operator signature.
- Learning cannot modify live behaviour. Counterfactual results are research evidence only and are
  not wired to any automatic change (SOURCE-VERIFIED: no reader of `counterfactual.db` exists in
  `src/` outside `shadow_runner.counterfactual_summary`).
- No phase-2 lesson was promoted.

## 27. Crash/fault

| Suite | Result | Label |
|---|---|---|
| Crash matrix (20, real kills + restart invariants) incl. the new fenced-record point | 20/20 PASS | EXECUTED |
| Chaos suite (phase 1, unchanged) | PASS | SIMULATED |
| MT5 fault set: `None` result, timeout after fill, requote, no money, partial, history lag, comment rewrite, DST offset change, relogin to REAL | PASS | SIMULATED |
| Shadow loop faults: feed outage, calendar outage, restart | PASS | EXECUTED (unit) |
| Broker/terminal crash on a real MT5 demo | — | **BLOCKED** |

## 28. CPU/memory stress

**MEMORY PRESSURE (EXECUTED, `tests/test_stress.py`):**
- At each of 7 lifecycle points (candidate → risk → permit → intent persisted → before send → after
  the fenced record → after submit), the process sets `RLIMIT_AS` to current + 48 MB and fills it
  with ballast down to 512-byte granularity.
- **Every run failed closed:** `HALTED: exception MemoryError()`.
- A fresh process then restored all invariants: journal verifies; no duplicate, unauthorized,
  unresolved or orphan order; at most 1 position.
- First attempt disclosed: coarse 1 MB ballast left headroom and every cycle completed normally.
  The ballast was refined until allocation actually failed.

**CPU STARVATION (EXECUTED):**
- 16 busy-loop processes on 4 cores (4× oversubscription), two fence actors competing for 8 s:
  37 fenced sends, all invariants hold.
- SIGSTOP freezes of the leader (the extreme form of starvation) are covered in §8.
- **NOT EXECUTED:** cgroup CPU-quota throttling (not attempted; oversubscription plus SIGSTOP was used instead).

## 29. Mutation

`tools/mutate.py`, every mutation on a fresh copy, full suite (`-x`).

| Round | Applied | Killed | Survivors |
|---|---|---|---|
| 3 (phase 1) | 49 | 47 | 2 layered guards |
| **4 (phase 2, full run)** | **70** (49 old + 21 new) | **68** | (a) `authority: volume above risk approval accepted`: the known layered guard (the issuer check plus Risk sizing); killing both together is verified (`reports/mutation_combined.json`). (b) `reconcile: unresolvable intent not blocking`: a **real test gap**. With MT5 history unavailable and no position to flag, reconcile would report "clean" while an intent is still UNKNOWN. Closed by `test_unknown_intent_with_history_unavailable_keeps_reconcile_unclean` |
| 4 addendum | 6 | 5 | Only the layered guard |

Round-4 addendum detail:
- *Closed gap:* `reconcile: unresolvable intent not blocking` is now KILLED by its new test.
- *New mutants for this phase's fixes, all KILLED by their regression tests:*
  - `mt5: intent lookup ignores intent age` (D3);
  - `shadow: in-progress / unaligned bars kept` (D4);
  - `lease: concurrent store creation not retried` (D5).
- *Wrong-reason kill re-run:* `risk: swaps excluded from daily P&L` is now KILLED by
  `test_risk_daily_loss_includes_swaps`.

**Wrong-reason kill (disclosed).** In the full run, the risk-swap mutant was "killed" by
`test_mp_clock_disagreement[5.0]`, which has nothing to do with swaps:
1. There was no swap regression test yet; D1 had been fixed without one. The test was added and
   the mutant re-run (row above).
2. The unrelated failure was investigated. It reproduced as **D5**: two processes creating the
   coordination store simultaneously, so `PRAGMA journal_mode=WAL` fails immediately with
   "database is locked" because the busy handler does not apply. It fails closed (the instance
   dies at startup) but is an availability defect. It was fixed with a bounded retry inside
   `busy_timeout`.
3. The regression test releases 12 processes at the same instant over 10 rounds. It fails on the
   old code and passes on the new.

New round-4 mutants cover:
- the demo guard: REAL account, allow-list, re-check before send;
- retcode classification: timeout and `None`;
- DST offset change;
- history lag;
- the fenced send ledger: execution bypass, lease check;
- reconciler: in-flight detection, retry vs. manual scope, unresolvable-intent blocking;
- swaps in risk and compliance; commission in the worst case;
- the data-quality gate: proxy as broker, gaps;
- point-in-time news leakage;
- Shadow: duplicate decision, partial-horizon counterfactual, stale feed.

Kill rate on applied mutants: 73/74 after the fixes (98.6 %). The single survivor is explained.
Reports:
- `reports/mutation_round4.{txt,json}`;
- `reports/mutation_round4_addendum.{txt,json}`.

## 30. K-LEAN

**SOURCE-DERIVED review. K-LEAN did not run.**
- It needs a NanoGPT or OpenRouter API key (none in this environment: **BLOCKED**).
- HEAD `c8a06e9`, Apache-2.0.

I applied K-LEAN's own review checklist (`src/klean/data/prompts/review.md`: correctness, memory
safety, error handling, concurrency, architecture, security, standards) to the phase-2 diff myself.
This is my review using its frame, **not** K-LEAN output.

| Severity (K-LEAN scale) | Location | Finding | Disposition |
|---|---|---|---|
| HIGH | `broker/mt5.py` `find_by_client_id` | Fixed 7-day history window resolves an old executed intent as NOT_EXECUTED | **D3: reproduced, fixed, tested** |
| HIGH (residual) | `lease.record_send` → `order_send` | The window after the fenced record cannot be fenced at MT5 | Detected (new-leader halt); documented; gateway recommended (§8) |
| MEDIUM | `lease.py` store setup | Concurrent creation fails with "database is locked" (surfaced by mutation testing, not by the review) | **D5: reproduced, fixed, tested** |
| MEDIUM | `shadow_runner.step` | Bar dedupe keyed on in-progress proxy bars (surfaced by the live run) | **D4: reproduced, fixed, tested** |
| MEDIUM | `reconcile.py` in-flight window 600 s | A dead leader's recorded-but-unsent intent blocks the new leader (retry halt) for up to 10 min | Accepted availability cost; fail-closed |
| MEDIUM | `shadow_runner._build` | Rebuilding within the 30 s lease TTL of a previous build would give STANDBY (new Lease object, same owner) | Not reachable at hourly cadence; fail-closed; noted |
| LOW | `calendar_hist` | Known-from = 1 January is conservative but approximate; mid-year reschedules not reconstructable | Documented (§10) |
| — | Concurrency in `record_send` | `BEGIN IMMEDIATE` compare-and-insert; LeaseLost leaves no row | Verified by multi-process tests |

## 31. iFixAI

**SOURCE-DERIVED review. iFixAi did not run.**
- HEAD `4cebf80`, Apache-2.0. It audits an **LLM agent** endpoint with a judge model.
- Sentinel still has **no LLM in any decision authority**.
- Its key-free `--provider mock` mode is documented as a plumbing check, not a diagnosis. Running it
  would prove nothing about Sentinel, so it was not run. No judge-model key is available
  (**BLOCKED** for a real run).

Mapping of its categories onto the phase-2 changes:

| Category | Phase-2 assessment |
|---|---|
| Usurpation / insubordination | New modules hold no signing keys or permits; MT5 refuses non-demo accounts in code, not by instruction |
| Fabrication / concealment | Proxy data force-labelled; failed lineages, refused holdouts, harness fixes and the residual split-brain risk are reported here |
| Oversight atrophy | FTMO product/phase left to the owner; the demo guard requires an explicit owner allow-list |
| Systemic risk | Split-brain strengthened; MT5 fencing residual remains (§8) |
| External-text injection | Calendar titles, papers, READMEs: data only (phase-1 test `test_prompt_injection_in_title_is_inert_data` still passes) |

## 32. Remaining blockers

1. **No MT5 terminal or demo account** (Windows-only package; no credentials). Blocks broker data,
   the spec report, demo execution and demo re-qualification.
2. **OWNER INPUT REQUIRED — FTMO PRODUCT/PHASE**; profiles UNREVIEWED and expiring 2026-11-01.
3. **No edge**: L1 not established, L2/L3 failed validation.
4. **Proxy data only**, and several proxy series fail the data-quality gate.
5. **News history is FOMC-only**: NFP/CPI sources blocked (403 / unreachable).
6. **No broker-side fencing**: residual late-send window (§8); single execution gateway not built.
7. **In-process key separation** (phase 1, unchanged).
8. **Two-host split-brain** not executed.
9. **Continuous Shadow on broker data** not possible; the live shadow here is proxy-fed and short.

## 33. Minimum missing set

1. An owner-provided MT5 **demo** account (login, server, magic) and a Windows host or VM with the
   terminal. Then:
   - run the adapter contract tests against it;
   - pull the broker symbol specs;
   - pull ≥ 2 years of broker bars/ticks through the data-quality gate.
2. Owner selection of the FTMO product/phase, and a human review that marks that one profile
   APPROVED (re-verified after 2026-11-01).
3. A legitimate historical source for NFP/CPI and other high-impact releases, e.g. BLS release
   calendars fetched from an unblocked network, or a licensed calendar.
4. A new pre-registered hypothesis evaluated **on broker data** with an untouched forward period.
   Do not rescue L1/L2/L3.
5. A single execution-gateway process holding the only broker credentials and the permit key,
   serializing sends.

## 34. Conditions for continuous Shadow

On broker data. Nothing of this is met today except the software and the runner:

- MT5 read-only connection live; server offset stable; clock drift alerting ≤ 2 s.
- Broker bars pass the data-quality gate as BROKER_GRADE.
- Calendar refresh loop running; staleness → BLOCK verified in production logs; a second calendar
  source or FTMO's restricted list.
- `shadow_runner` running ≥ 4 weeks with:
  - no unexplained `FEED_STALE` / `LOOP_ERROR`;
  - journals verified daily;
  - counterfactual journal reviewed weekly against replay predictions.
- Full test suite green; mutation kill rate ≥ 95 % with no unexplained survivor.

## 35. Conditions for MT5 broker-demo

- Everything in §34, completed.
- Owner-signed `DemoAuthorization` (DEMO account, allow-listed login and server, magic).
- Execution gateway (§33.5) in place.
- Crash, chaos and split-brain suites re-run against the demo terminal, including:
  - real `order_send` timeouts;
  - history lag;
  - comment truncation;
  - DST change;
  - relogin-to-REAL refusal.
- FTMO profile for the selected product APPROVED and in its validity window.
- **A demo pass is not authorization for anything beyond demo.**

## 36. Conditions before micro-live could even be considered

1. A pre-registered strategy that passes a **fresh forward period on broker data** in the deployable
   timeframe:
   - ≥ 2× realistic costs;
   - not concentrated in one instrument;
   - paper P&L inside the replay's confidence interval.

   None exists today.
2. Months of broker-demo paper with zero duplicate orders, zero unexplained positions, zero
   un-halted compliance approaches, and every reconciliation clean.
3. Two-host split-brain testing.
4. The execution gateway with the only credentials.
5. Independent review of the FTMO profile.
6. **Explicit written owner authorization** naming account, product, size limits and kill-switch
   owner, recorded by the promotion pipeline.

**Micro-live is not recommended on current evidence, and none of this phase's work begins it.**
