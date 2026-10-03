# Pre-registration — Phase 2 research lineages

Committed **before** any statistic of these hypotheses was computed on any data.

**Global rules:**
- Parameters come from the cited literature. **No parameter search.**
- The Validation period is a gate: if a lineage fails Validation, its Holdout is **never opened**.
- The Holdout is opened once through `PromotionPipeline` (journal-enforced).
- A failed lineage is recorded as failed. Any change creates a new lineage with a new pre-registration.

**Data status:** all data available in this environment is **PROXY / RESEARCH-ONLY**: Yahoo GC=F,
CL=F, JPY=X, BTC-USD. MT5 broker data is BLOCKED (no MT5 terminal on Linux, no credentials). A pass on
proxy data is at most *preliminary*. It cannot qualify a strategy for broker-demo or live use without
re-running on broker data.

**Disclosure of prior exposure:**
- The 2019→2026 daily period and the second year of hourly data were already used as the holdout of
  lineage L1 (`donchian_breakout_v1`).
- Only L1's statistics were seen there. No statistic of L2 or L3 was ever computed on those periods.
- The exposure is still disclosed, because a researcher's general knowledge of those years (e.g.
  trending gold, 2020 crash) cannot be erased.

## Lineage L1 — `donchian_breakout_v1` (baseline, FAILED for deployment)

Status is carried from phase 1. It is not re-tested, not tuned and not promoted.

## Lineage L2 — `tsmom_12m_v1` (time-series momentum)

| Field | Value |
|---|---|
| Hypothesis | The sign of an instrument's trailing 12-month return predicts the sign of its next-month return |
| Why it might exist | Documented across 58 futures/forwards, including gold, crude oil and JPY. Proposed mechanisms: initial under-reaction and delayed over-reaction; hedging-pressure flows. Sources: Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*, JFE 104(2); Hurst, Ooi & Pedersen (2017), *A Century of Evidence on Trend-Following Investing*, JPM |
| Market | XAUUSD, USOIL, USDJPY (in the literature universe); BTCUSD (out of sample for the literature, reported separately) |
| Timeframe | Daily bars; monthly decision |
| Entry | At the close of the last trading day of month *m*, compute r12 = close / close 252 trading days earlier − 1. Direction = sign(r12); r12 = 0 means no trade. Enter at the **open of the first trading day of month m+1** |
| Exit | At the open of the first trading day of month m+2 (one-month hold). Every month is a separate round trip, even when the direction is unchanged (conservative on costs) |
| Invalidation | None intra-month (as in the literature). Risk unit for R: 3 × ATR(20) at signal time. FTMO compatibility of a stop-less hold is a separate question and is **not** tested here |
| Expected regime | Persistent macro trends |
| Expected failure regime | Sharp trend reversals (e.g. 2009 rebound, 2016); range-bound markets |
| Data required | Daily OHLC ≥ 13 months before the first signal |
| Costs (1×) | Spread + round-trip commission + 2 × slippage, from `system.DEFAULT_COSTS` |
| Swap | Financing of **4 % per year of notional, charged for either direction** (prorated by calendar days). Conservative CFD assumption; no broker swap table is available |

| Split | Period |
|---|---|
| Development | signals ≤ 2010-12-31 (BTC: none) |
| Validation | 2011-01-01 … 2018-12-31 |
| Holdout | 2019-01-01 … end of data |

**Validation gate (holdout opened only if ALL hold, on non-BTC instruments pooled):**
expectancy > 0 R at 1× costs, and n ≥ 60.

**Holdout pass criteria (ALL must hold; pooled non-BTC unless stated):**

1. n ≥ 60 trades.
2. Expectancy ≥ +0.10 R at 1× costs.
3. t ≥ 2.0.
4. Profit factor ≥ 1.20.
5. Expectancy > 0 at 2× costs (spread, commission, slippage and swap all doubled).
6. At least 2 of 3 non-BTC instruments with positive expectancy.
7. 20-trade block-bootstrap 5th percentile of mean R > 0.

## Lineage L3 — `fx_local_hours_v1` (intraday currency time-of-day effect)

| Field | Value |
|---|---|
| Hypothesis | Currencies tend to depreciate during their own local trading hours. For USDJPY: JPY weakens during Tokyo hours (USDJPY up) and USD weakens during New York hours (USDJPY down) |
| Why it might exist | Systematic order-flow patterns (domestic investors buying foreign currency in local hours). Source: Breedon & Ranaldo (2013), *Intraday Patterns in FX Returns and Order Flow*, Journal of Money, Credit and Banking 45(5) |
| Market | USDJPY only (the only currency pair in Sentinel's universe) |
| Timeframe | Hourly bars |
| Entry / exit, leg T | **Long** at the open of the 00:00 UTC bar; exit at the open of the 07:00 UTC bar. Mon–Fri UTC dates |
| Entry / exit, leg N | **Short** at the open of the 13:00 UTC bar; exit at the open of the 20:00 UTC bar |
| Missing bars | A leg is skipped if its entry or exit bar is missing |
| Invalidation | Time exit only |
| R unit | ATR(24) of hourly bars × √7 at entry |
| Expected regime | Normal liquidity |
| Expected failure regime | Event-driven days (BoJ, US data), carry unwinds |
| Costs (1×) | `DEFAULT_COSTS["USDJPY"]`: spread 0.012, round-trip commission 0.006, slippage 0.005 per side |
| Swap | None (intraday, flat before the 21–22 UTC rollover) |

| Split (hourly data, ~730 days) | Period |
|---|---|
| Development | first third |
| Validation | second third |
| Holdout | last third |

**Validation gate:** pooled expectancy (both legs) > 0 at 1× costs and n ≥ 100.

**Holdout pass criteria (ALL):**

1. n ≥ 150.
2. Expectancy > 0 at 1× **and** at 1.5× costs.
3. t ≥ 2.0.
4. Profit factor ≥ 1.10.
5. Both legs individually ≥ 0 expectancy.
6. Bootstrap p5 > 0.

## Reported for every lineage, never used for selection

- Cost stress at 1×, 1.5×, 2× and 3×, and the cost break-even multiple.
- Per-year and regime (volatility-tercile) segmentation.
- Win rate, average win and loss, R distribution, drawdown.
- Parameter neighbourhood (L2: 6/9/12-month lookbacks; L3: ±1 h windows), as **sensitivity only**.
