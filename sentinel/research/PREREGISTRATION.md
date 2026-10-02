# Pre-registration — Edge qualification of `donchian_breakout_v1`

Written and committed **before** any profit/loss result of this strategy was computed.
The only prior runs were functional smoke tests of the pipeline. One covered the last 20 days of
hourly data. It produced zero trades and no P&L, and its gate counts were not used for any choice
below.

## Hypothesis

H1: A close beyond the prior 20-bar high/low has positive net expectancy after costs, under these
rules:
- entry at the next bar open;
- stop at 2.0×ATR(14) from the signal close;
- exit at the stop or after 10 bars.

Instruments: XAUUSD, USOIL, USDJPY, BTCUSD (Yahoo proxies GC=F, CL=F, JPY=X, BTC-USD).

All parameters are the textbook defaults fixed in `FinderParams` / `ValidatorParams`. **No parameter
search will be performed.** Parameter *sensitivity* (§4) is reported, but is never used to pick
values.

## Data

| Set | Bars | Development | Holdout (touched once, after dev results are recorded) |
|---|---|---|---|
| D (daily) | 1d, from 2000 (BTC 2014) | up to 2018-12-31 | 2019-01-01 → end of data |
| H (hourly) | 1h, last ~730 days | first 365 days | remaining days |

Costs come from `system.DEFAULT_COSTS` (spread, round-trip commission, per-side slippage in price
units). Cost stress multiplies all three by 1×, 2×, 3× and 5×.

## Pass criteria (all must hold on the HOLDOUT)

1. Pooled holdout trades ≥ 100.
2. Pooled expectancy ≥ +0.05 R per trade at 1× costs.
3. t-statistic of mean R ≥ 2.0.
4. Profit factor ≥ 1.15.
5. Expectancy > 0 at 2× costs.
6. At least 3 of the 4 instruments have positive holdout expectancy.
7. Walk-forward by calendar year over the full daily sample: expectancy positive in ≥ 60 % of years
   (each year evaluated with fixed, never-refit parameters).
8. Block-bootstrap (20-trade blocks, 5,000 resamples) 5th percentile of mean R > 0.

If any criterion fails, the edge is reported as **NOT DEMONSTRATED**. Nothing will be re-tuned
against the holdout.

## Pipeline-level questions (reported, not pass/fail)

- Ablation: Finder alone vs +Validator vs +Arbiter vs full pipeline (news, risk, compliance).
- No-trade counterfactual R of candidates stopped at each gate.
- Finder/Validator independence: shared features, shared data, and the correlation of the
  Validator's verdict with realised outcomes.
