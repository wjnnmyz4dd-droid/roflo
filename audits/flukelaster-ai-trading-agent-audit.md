# Forensic Audit — `flukelaster/ai-trading-agent`

**Mode:** read-only, source-first, adversarial, inspiration-only.
**Audited commit:** `e85c1861a0132c870df27b807afa6e9f26205c57` (branch `main`, 2026-05-03).
**Audit date:** 2026-10-01.
**Repository source modified:** No. The audited clone was left with `git status --ignored` clean. Test byproducts (`.coverage`, `__pycache__`) were deleted. Every probe script lived outside the repository.

Status vocabulary used throughout: IMPLEMENTED · IMPLEMENTED BUT UNVERIFIED · PARTIALLY IMPLEMENTED · STUBBED · DOCUMENTATION-ONLY · BROKEN · UNUSED/DEAD · MISSING · INDETERMINATE.
Verdict tags on defects: **CONFIRMED-RUNTIME** means I reproduced it by running the repository's own code. **CONFIRMED-SOURCE** means the code path is unambiguous. **PLAUSIBLE** means the source points strongly that way but I could not run the path end to end.

---

## 1. Executive factual summary

This is a **single-operator, MT5-connected, multi-symbol bar-close trading bot**, written in about 4 weeks (2026-04-07 → 2026-05-03) with heavy AI-assisted development. It has a large FastAPI/Next.js control plane and a Claude-agent layer.

What it actually is:

- **Primary trade generator:** a deterministic per-symbol `BotEngine`. On every M15 bar close it runs **one** rule-based strategy for that symbol (default `ema_crossover`). It then applies filters and places a market order through a custom HTTP bridge to MetaTrader 5 on a Windows VPS.
- **Second trade generator:** a Claude multi-agent pipeline (Orchestrator holds `place_order`). In multi-agent mode it also runs on every candle, **in parallel with** the strategy engine, even in the default "strategy" trading mode.
- **Third trade generator:** a TradingView webhook that calls the engine's order-placement routine directly.
- **Instruments:** four hard-wired profiles (GOLD, OILCash, BTCUSD, USDJPY, plus XM "micro" aliases). Any broker symbol can be added at runtime through the DB.
- **Trading style:** intraday-signal / **open-ended holding** trend and breakout trading on M15. There is no forced session, overnight or weekend exit. Positions can be held for days.
- **Multi-asset selection: none.** Each symbol is evaluated independently and serially. The first symbol to pass its own filters is executed first. There is no scoring, ranking or capital allocation across opportunities.

The most consequential findings:

1. **The daily-loss protection is non-functional.** The Redis key that every daily-loss check reads (`circuit:daily_pnl:{symbol}`) is written only by `CircuitBreaker.record_trade_result()`, and nothing in production code calls it. Four separate "daily loss" controls therefore always see 0. Only the balance-based 15 % drawdown-from-peak halt works. (CONFIRMED-SOURCE + RUNTIME)
2. **Shadow → Paper → Micro → Live gates only the AI agent's MCP broker tool.** The strategy engine, which is the main trader, ignores `ROLLOUT_MODE`. Its default `paper_trade=False` means it sends real orders when started. (CONFIRMED-SOURCE)
3. **The "non-bypassable guardrails" are only partly effective.**
   - The spread check compares spread to itself, so it can never fire.
   - The consecutive-loss halt only ever records wins.
   - The daily-loss input is the dead key from finding 1.
   - The strategy engine and the webhook do not pass through the guardrails at all. (CONFIRMED-RUNTIME)
4. **AI tool boundaries are prompt-level, not capability-level.** Agents run with `permission_mode="bypassPermissions"` and only an `allowed_tools` auto-approve list. Read from the SDK source, that does not remove other tools. The "analysis-only" single agent can therefore call `place_order`, and agents probably have Claude Code built-ins (Bash/Write) available. (CONFIRMED-SOURCE for MCP tools; PLAUSIBLE for built-ins)
5. **The economic-calendar timing is wrong.** The repo already uses the Forex Factory JSON feed. It stores ET-offset times as if they were UTC, so with the US on EDT every event is mis-timed by 4 hours: the "near event" window fires before the real release and is over by the time it happens. Its hardcoded fallback can never fire. It is a lot-halving filter, not a block. (CONFIRMED-RUNTIME)
6. **Backtest, Monte Carlo and ML evaluation contain material validity defects:**
   - Backtest P&L hardcodes `×100`.
   - Costs are off by default and not symbol-scaled.
   - Monte Carlo permutes trade order, so final-balance statistics and "probability of ruin" are degenerate.
   - ML early-stops on its own test set, and model promotion compares unlike metrics. (CONFIRMED-RUNTIME / SOURCE)
7. **Several strategies cannot fire live as wired.** `dca` and `momentum_rank` (cross-asset mode) set signals on bars the engine never reads. (CONFIRMED-RUNTIME)
8. **There is no profitability evidence.** The repo has no backtest artifacts, no walk-forward or OOS reports, no paper logs and no live statements. The only performance evidence is one README screenshot of 7 trades (3W/4L, +$1.68).

The existing test suite is healthy as a suite: **504 passed, 0 failed, 0 skipped, 1,257 warnings, 31 % line coverage**. The highest-risk runtime paths (scheduler 9 %, BotManager 13 %, correlation 0 %, confirmation gate 0 %, ML trainer 0 %, MCP server 0 %) are essentially untested.

---

## 2. Repository identity

| Item | Value |
|---|---|
| Owner / repo | `flukelaster/ai-trading-agent` (public on GitHub) |
| Branch audited | `main` (13 other remote feature/fix branches exist, not audited) |
| Commit | `e85c1861a0132c870df27b807afa6e9f26205c57` — "feat: production hardening — symbol auto-trade, money correctness, OWASP top 10 (#72)" |
| History | 292 commits, 2026-04-07 → 2026-05-03; 1 human author (3 name/email variants); 305 `Co-Authored-By: Claude…` trailers |
| Activity | Burst development: 230 commits in 2026-04-11 → 04-17; 2 commits after 04-20; nothing after 2026-05-03 (≈5 months idle as of audit date) |
| Releases / tags | None. `frontend/package.json` = 0.1.0; UI footer shows "v2.0.0" |
| License | **No LICENSE file.** README: "License: Private — internal use only" (badge "Private"). See §Licence below |
| Size | backend/app 24.6k Python LOC (143 files); mcp_server 4.0k (30); tests 6.3k (35); alembic 22 migrations; mt5_bridge 563 LOC; frontend ≈11.8k TS/TSX |
| Environment inspected | Linux container, CPython 3.12.3 venv with the repo's pinned `requirements*.txt`. No MetaTrader 5 (Windows-only package), no PostgreSQL/Redis server (tests use SQLite + fakeredis), no Claude OAuth token, no FRED key, no Telegram, no broker account |

### Licence / usage

- Only the README and badge address licensing: "Private — internal use only". No licence text exists in the repository and no source files carry licence headers.
- A public repository with no licence grant is, by default, "all rights reserved" by the copyright holder.
- The repository states nothing that permits personal use, modification, deployment, commercial use, redistribution or derivative works. Its only statement points the other way ("internal use only").
- **Status: unclear / no permissions granted.** Reading it for inspiration is what the audit did. Copying code is not supported by any stated grant. This is not a legal opinion.

---

## 3. Complete repository map

| Path | What it is | Runtime-active? | Decision authority |
|---|---|---|---|
| `backend/app/main.py` | FastAPI lifespan: schema check, mints 24h internal JWT, builds MT5 connector, Redis, BotManager, sentiment, collector, macro, calendar, optimizer, Telegram, health monitor, MCP tool init, scheduler, runner manager | Yes | Wiring only |
| `backend/app/config.py` | `SYMBOL_PROFILES` (4 + micro aliases), `SESSION_PROFILES`, pydantic `Settings` (env/.env) | Yes | Config authority #1 (env) |
| `backend/app/constants.py` | All risk/sizing/trailing constants | Yes | Static |
| `app/services/symbol_config_service.py` + `api/routes/symbols.py` | DB-backed symbol profiles; Redis pub/sub hot-reload; bootstrap seeds 90d history + ML retrain on symbol add | Yes | Config authority #2 (DB overrides static) |
| `app/bot/manager.py` | `BotManager`: one engine per enabled symbol; portfolio leverage check; position cache; hot reload | Yes | Portfolio gate (leverage) |
| `app/bot/engine.py` (1,884 LOC) | `BotEngine`: candle processing, filters, sizing, order, paper sim, trailing/breakeven/partial TP/time exit, close detection, reconciliation, orphan adoption | Yes | **Primary trade authority** |
| `app/bot/scheduler.py` | APScheduler: 1s tick, per-TF candle cron, 15-min sentiment, 30s sync, 5-min recovery/reconcile/vault, hourly calendar, weekly optimize + ML retrain, daily resets/backup/summary/cleanup | Yes | Orchestrates both trade generators |
| `app/bot/health_monitor.py` | Bridge heartbeat; pauses all engines after 3 failures; **resumes all PAUSED engines** on recovery | Yes | Can override risk pauses (see §23) |
| `app/strategy/*` | 10 registered strategies + ensemble, indicators, regime (ATR/ADX, multi-TF, HMM), quant signals | Partly (see §8) | Signal authority |
| `app/strategy/mtf_filter.py`, `kalman.py` | H4/D1 consensus filter; Kalman filter | **UNUSED/DEAD** (no importers) | None |
| `app/risk/manager.py` | Lot sizing (fixed-risk, Kelly), SL/TP, adaptive confidence, `can_open_trade` | Yes | Sizing + per-symbol permission |
| `app/risk/circuit_breaker.py` | Redis daily P&L / global P&L / peak-balance drawdown | Yes (daily part ineffective) | Halt authority |
| `app/risk/correlation.py` | Static + rolling correlation conflict check | Yes | Block authority |
| `app/risk/garch.py` | GARCH(1,1) vol forecast (arch lib) | Yes (sizing input) | Advisory to sizing |
| `app/risk/var.py`, `portfolio_optimizer.py` | VaR; max-Sharpe / risk-parity optimizer | API/analytics only | None |
| `app/risk/portfolio_risk.py` | Portfolio risk calc | **UNUSED/DEAD** | None |
| `app/mt5/*` | HTTP connector (retry), `OrderExecutor` (retry), `MarketDataService` (stale tick, spread spike, OHLCV validation), broker alias resolver | Yes | Execution path |
| `mt5_bridge/main.py`, `watchdog.py` | Windows FastAPI around `MetaTrader5` package; watchdog restarts ≤3/h | Yes (prod) | Final broker boundary |
| `app/binance/connector.py` | Binance Spot REST "drop-in" connector | **UNUSED/DEAD** (`_binance_connector` never assigned; only a connectivity test) | None |
| `app/data/collector.py` | MT5 history → DB in 30-day chunks; `load_from_db` | Yes | Data |
| `app/data/macro.py` | FRED series (7) → DB | Yes if `FRED_API_KEY` | None (analytics/ML optional) |
| `app/data/macro_events.py` | Forex Factory weekly JSON (USD/High only) + hardcoded fallback | Yes | Lot-halving filter |
| `app/news/*` | RSS (Google News, FXStreet, Investing.com) per symbol, 2h max age, 5 headlines | Yes | Input to sentiment |
| `app/ai/client.py`, `news_sentiment.py`, `context_builder.py`, `prompts.py` | Claude (Haiku via Agent SDK) sentiment scoring cached in Redis | Yes if OAuth token | Veto (counter-direction filter) |
| `app/ai/confirmation_gate.py` | N-of-5 confirmation (quant/ML/regime/RR/AI) | Yes (fail-open) | Block authority |
| `app/ai/hallucination_check.py` | Post-hoc regex check of agent text vs indicators | Yes | None (log only) |
| `app/ai/strategy_optimizer.py` | Weekly LLM param suggestion + in-sample backtest compare | Yes (auto-apply flag-gated) | Can change strategy params |
| `app/ai/{baseline_manager,bias_guard,expert_framework,param_gate,pattern_validator,quant_analyzer,trade_accountability}.py` | Various AI-governance helpers | **UNUSED/DEAD** (no importers) | None |
| `app/ml/*` | LightGBM trainer, features (48 cols), predictor, drift, calibration, integrity (HMAC), sentiment features | Partly | Signal authority only when `ml_signal` strategy selected |
| `app/backtest/*` | Engine, grid optimizer, walk-forward, Monte Carlo, permutation/cointegration tests, stress test, overfitting composite | API/UI + weekly optimizer | None live |
| `app/memory/*` | Redis/DB agent memory + daily consolidation | Yes (scheduler) | None |
| `app/runner/*` | Process/Docker "runner" job system, Redis queue, heartbeat; `agent_entrypoint` | Initialized; agent entrypoint calls `run_agent` | Separate path to the same AI agent |
| `app/notifications/telegram.py` | Thai-language alerts | Yes if configured | None |
| `app/auth.py`, `auth_webauthn.py`, `middleware/*`, `vault*.py`, `audit.py` | JWT password auth (active), WebAuthn (routes mounted, middleware disabled), rate limiter, AES-GCM vault, audit log | Yes | Access control |
| `app/api/routes/*` (25 files, 127 route decorators; 129 app routes incl. WS) | REST control plane: start/stop/emergency, settings, rollout, strategy, symbols, ML, backtest, quant, secrets, runners, webhooks | Yes | Human (and webhook) authority |
| `backend/mcp_server/*` | FastMCP stdio server (41 tools), guardrails, 5 agents + prompt registry, strategy-switch guard, SDK client | Yes when Claude token present | AI trade authority (Orchestrator / any agent) |
| `backend/tests/*` | 35 files, 504 tests | CI | — |
| `alembic/` | 22 migrations | Deploy | — |
| `frontend/` | Next.js 16 dashboard (17 route dirs) | UI | Human controls |
| `.github/workflows/ci.yml` | ruff, ruff format, mypy (soft), pytest `--cov-fail-under=30`; frontend eslint/tsc/build | CI | — |
| `Dockerfile.trading-agent`, `backend/Dockerfile`, `docker-compose.yml`, `railway.toml`, `nixpacks.toml` | Railway deployment, Postgres/Redis compose, installs `@anthropic-ai/claude-code@latest` globally | Deploy | — |
| `scripts/*` | pg_dump backup, run_agent, Windows firewall + watchdog task | Ops | — |
| `docs/`, `agent-character/` | Screenshots, logo, DB scaling notes, chibi art | — | — |

---

## 4. Architecture (reconstructed from source)

```
                ┌──────── Windows VPS ─────────┐
                │ MetaTrader5 terminal (XM)     │
                │ mt5_bridge FastAPI :8001      │  X-Bridge-Key (HMAC compare)
                └──────────────▲────────────────┘
                               │ HTTP (httpx, 8s, 2 retries)
┌──────────────────────────────┴──────────────────────────────────────────┐
│ Railway backend (FastAPI)                                               │
│                                                                         │
│ APScheduler                                                             │
│  ├ every 1s  : tick → Redis pub/sub → WS dashboard                      │
│  ├ M15 cron  : _candle_job(symbols)  ── for each symbol SERIALLY ──┐    │
│  │              (market-hours filter by asset class)               │    │
│  │   trading_mode == "strategy":                                   ▼    │
│  │     BotEngine.process_candle()  ───────────────► TRADE PATH A        │
│  │     then fire-and-forget _run_ai_agent() ──────► TRADE PATH B        │
│  │   trading_mode == "ai_autonomous":                                   │
│  │     regime only, then _run_ai_agent() ─────────► TRADE PATH B        │
│  ├ :02/:17/:32/:47 : RSS → Claude Haiku sentiment → Redis               │
│  ├ every 30s : sync_positions (close detection, trailing/BE/partial)    │
│  ├ every 5m  : reconcile, pending-trade recovery, vault health          │
│  ├ hourly    : economic calendar refresh (first engine only)            │
│  ├ Mon 04:00 : ML retrain; Mon 06:00: LLM optimizer (+auto-apply flag)  │
│  └ daily     : circuit-breaker resets per asset class, backup, summary  │
│                                                                         │
│ POST /api/webhooks/tradingview ───────────────────► TRADE PATH C        │
│ REST (JWT) : start/stop/emergency-stop, settings, rollout, strategy     │
└─────────────────────────────────────────────────────────────────────────┘
```

**Trade path A (strategy engine): the main path.**

```
MT5 OHLCV(200 bars, engine TF) ─► strategy.calculate() ─► signal = df.iloc[-2]
  ─► H1 EMA21 trend filter (block if opposite)
  ─► account balance ─► CircuitBreaker (daily [dead], global [dead], peak-DD [works])
  ─► regime detect (multi-TF + HMM) ─► macro-event proximity (lot ×0.5 only)
  ─► AI sentiment (Redis, decays) ─► RiskManager.can_open_trade
       (max concurrent per symbol, daily loss [dead], sentiment veto)
  ─► BotManager.check_portfolio_limit (notional leverage ≥3× blocks)
  ─► correlation conflict (blocks opposite-direction exposure on correlated pairs)
  ─► ConfirmationGate (≥ majority of available sources; fail-open)
  ─► GARCH vol ─► ATR/ADX regime ─► SL/TP (ATR×mult×regime factor)
  ─► lot = Kelly (≥20 trades, WR≥35%) or fixed-risk ─► warm-up ramp ─► loss-streak cut
       ─► event ×0.5 ─► (or fixed_lot override)
  ─► paper_trade ? local simulation : OrderExecutor.place_order (3 tries) ─► bridge /order
  ─► Trade row in DB (Redis fallback) ─► Telegram
```

**Trade path B (Claude agents).**

```
single mode : one Sonnet agent, system prompt says "analysis only, MUST NOT call place_order"
              — but no tool filter is passed (all 41 MCP tools callable).
multi mode  : Reflector(Haiku) ─► Technical/Fundamental/Risk (Haiku, parallel)
              ─► Orchestrator(Sonnet) with place_order/modify/close/log tools
place_order ─► TradingGuardrails.validate_order ─► rollout mode (env) ─► bridge /order
```

**Trade path C (TradingView webhook).** Key + timestamp + nonce, then `engine._size_and_place_order()` directly. It skips the circuit breakers, `can_open_trade`, portfolio/correlation checks, the confirmation gate, the MTF filter, the engine state (works while STOPPED/PAUSED), the guardrails and the rollout mode.

### Where a trade can be created / approved / rejected / resized / modified / closed

| Action | Points in code | Final authority |
|---|---|---|
| Created | A: `engine.process_candle`; B: MCP `place_order` (Orchestrator, or any agent given the SDK semantics); C: webhook; partial-TP re-open in `_execute_partial_tp`; human via MT5 terminal (adopted as orphan) | Each path independently; no single arbiter |
| Approved / rejected | A: circuit breakers, `can_open_trade`, portfolio leverage, correlation, confirmation gate, H1 filter, market-hours filter; B: guardrails + rollout mode + LLM judgement; bridge `order_send` retcode | Path-specific; broker is the only common gate |
| Resized | `RiskManager` (Kelly/fixed), regime multiplier, vol factor, warm-up, streak, event ×0.5, `fixed_lot`; bridge volume clamp to `volume_min/max/step`; micro mode caps at 0.01 (path B only) | Path A: RiskManager chain; Path B: LLM-chosen lot, capped by guardrail 1.0 / micro 0.01 |
| Delayed | Market-hours filter, PAUSED state, cooldowns (path B min 120 s, 5/h) | Scheduler / guardrails |
| Modified | Engine trailing / breakeven every 30 s (real mode only); MCP `modify_position` (SL-widen ≤2× check) | Both can modify the same ticket |
| Closed | Broker SL/TP; engine time-exit (disabled by default); engine partial-TP close-and-reopen (disabled by default); MCP `close_position`; `/api/bot/emergency-stop` (close all); bridge `/positions DELETE` | Multiple |

---

## 5. Trading universe

| Profile key | Broker symbol(s) | Asset class | Default strategy | Engine TF | ML TF (profile) | contract_size (profile) | pip_value | Default / max lot | Session rule |
|---|---|---|---|---|---|---|---|---|---|
| GOLD | `GOLD`, alias `GOLDmicro` | metal | ema_crossover | M15 | M15 | 100 | 1.0 | 0.1 / 1.0 | Mon–Fri, closed 22–23 UTC |
| OILCash | `OILCash`, `OILCashmicro` | energy | breakout | M15 | M15 | 100 | 10.0 | 0.1 / 5.0 (micro alias capped 1.0) | Mon–Fri, closed 22–23 UTC |
| BTCUSD | `BTCUSD`, `BTCUSDmicro` | crypto | breakout | M15 | **H1** | 1 | 1.0 | 0.01 / 0.5 | 24/7 |
| USDJPY | `USDJPY`, `USDJPYmicro` | forex | ema_crossover | M15 | M15 | 100000 | 100.0 | 0.1 / 5.0 (micro alias 1.0) | Mon–Fri, closed 22–23 UTC |

- **How instruments are defined:** hard-coded in `config.py`. They can be overridden or extended by DB rows (`SymbolConfig`, managed via `/api/symbols` with a broker-catalog dropdown from the bridge's `/symbols`). The engine set is the DB-enabled symbols, falling back to the `SYMBOLS` env var (default `"GOLD"`). New asset classes (`index`, `stock`) are supported structurally. **Not dynamically discovered for trading.** Discovery exists only as a UI helper.
- **Micro aliases inherit contract_size from the full profile** (100 for GOLDmicro). The README's own History screenshot shows GOLDmicro 0.1 lot SELL 4773.65 → 4787.81 = −$1.42. That implies a contract size near 1, not 100. Unless overridden in the DB, sizing for micro aliases is mis-scaled by about 100×. (PLAUSIBLE; screenshot-based)
- **Spread / commission / slippage assumptions:**
  - Live sizing adds a "slippage cushion" of `2.0 × pip_value` price units to the SL distance (see §14).
  - A commission haircut of 0.2 % is taken off the risk budget.
  - Backtests are cost-free by default.
  - No per-symbol spread model, commission schedule or swap model anywhere.
- **Minimum history:** engine 200 bars per call (ML needs about 114+ for `atr_percentile`; EMA-200 is under-warmed). ML retrain needs ≥500 bars in DB within 90 days. Walk-forward needs ≥200.
- **Special handling:** URL-encoding of broker symbols, alias → broker name at the MT5 boundary, asset-class market-hours approximations (forex weekend = Sat/Sun UTC; Sunday 22:00 reopen not modelled).
- **USDJPY P&L currency:** profit is in JPY per price unit, but sizing treats it as account currency. There is no FX conversion anywhere (see §14).

---

## 6. Trading style (from source, per strategy)

| Strategy | Signal TF | Exec TF | Entry trigger | Exit | Intended holding | Survives session / overnight / weekend | Classification |
|---|---|---|---|---|---|---|---|
| ema_crossover (EMA20/50) | M15 | M15 market order | Fresh cross on last closed bar | ATR SL 1.5× / TP 2.0×, breakeven at 0.5 ATR, trail after 1 ATR | Hours to days (no time stop by default) | Yes / Yes / Yes | Intraday-signal trend follower, de-facto swing-capable |
| rsi_filter | M15 | M15 | EMA cross + RSI + 5-bar divergence veto | Same | Same | Yes / Yes / Yes | Same |
| breakout (20-bar channel + 0.5 ATR + volume) | M15 | M15 | Close beyond channel | Same | Same | Yes / Yes / Yes | Intraday breakout, open-ended hold |
| mean_reversion (BB 20/2 + RSI 30/70, squeeze skip) | M15 | M15 | Close at band + RSI extreme | Same (no mean-target exit) | Same | Yes / Yes / Yes | Intraday mean-reversion entry with trend-style exits |
| grid (SMA20 ± k·5.0 price units) | M15 | M15 | Cross of grid level | Same | Same | Yes / Yes / Yes | Not a real grid (1 position logic, absolute 5.0 spacing) |
| dca (every 20 bars) | M15 | — | Never fires live (§8) | — | — | — | Dead as wired |
| momentum_rank / risk_parity / pair_spread | M15 (cross-data hardcoded M15) | M15 | Rank / vol / z-score | Same | Same | Yes / Yes / Yes | Cross-asset intraday; momentum_rank ranking mode dead |
| ml_signal (LightGBM 3-class) | Engine TF (M15; BTC model trained on H1 in API path but retrain uses engine TF) | M15 | p(class) ≥ dynamic threshold | Same | Label horizon 10 bars (≈2.5 h); holding open-ended | Yes / Yes / Yes | Short-horizon classifier with open-ended exits (horizon mismatch) |
| AI Orchestrator | M15 cadence | Market | LLM judgement | Broker SL/TP it sets; engine trailing also applies to its tickets if engine tracked ATR (it does not, so no trailing) | Unbounded | Yes / Yes / Yes | Discretionary-LLM |

- **Entry frequency:** at most 1 decision per symbol per M15 bar (path A).
- **Exits:** broker-side SL/TP plus the engine's 30-second trailing/breakeven loop.
- **Unused exit features:** `max_position_duration_hours` (default 0 = off), `enable_partial_tp` (off), `enable_scale_in` (off and not implemented in the engine).
- **No weekend or overnight flattening** exists anywhere (grep for friday/weekend/overnight/swap finds nothing relevant).
- **Overall classification: hybrid.** Signals come from intraday (M15) data, but holding is unbounded, so positions are swing/multi-day capable. It is not a scalper, and it does not behave as a session trader.

---

## 7. Multi-asset selection (USDJPY vs gold vs oil vs BTC)

The exact decision path, from `scheduler._candle_job` → `engine.process_candle` → `_check_trade_permission`:

1. One cron job per unique timeframe fires (all four default symbols share `bot_candle_M15`).
2. Symbols whose asset class is "closed" are dropped (`is_market_open`).
3. **Strategy mode:** symbols are processed **serially in dict order** (`for sym in active_symbols: await engine.process_candle()`). Each engine:
   - generates its own signal;
   - checks *its own* per-symbol `max_concurrent_trades` (3);
   - checks the portfolio notional-leverage gate (≥3× blocks; 30-second position cache);
   - checks the correlation conflict.
4. There is **no scoring, ranking, expected-return estimate, confidence ordering or cross-symbol capital budget.** Simultaneous opportunities are resolved by **first-come-first-served in dict order**, subject to the leverage cap.
5. Multiple simultaneous trades are allowed:
   - up to 3 per symbol (engine);
   - path B guardrails cap at 3 per symbol / 5 total (the engine path is not subject to the total cap).
6. Correlation rules **block hedges, not concentration.**
   - Negatively correlated pair (corr < −0.5) + same direction = block.
   - Positively correlated pair (corr > 0.5) + opposite direction = block.
   - Same-direction stacking on positively correlated assets is allowed.
   - The static table has only GOLD/USDJPY −0.7, GOLD/BTC 0.3, OIL/USDJPY 0.2.
   - Rolling 30-bar correlations override the static values when available.
7. Portfolio leverage is computed as `lot × price × contract_size / balance`. For USDJPY the result is in JPY notional, which inflates leverage by about 150× relative to USD. In practice, **one open USDJPY position likely saturates the 3× leverage gate and blocks all other symbols.** (PLAUSIBLE; arithmetic from source)
8. `settings.max_portfolio_leverage` is declared but never passed. The hard-coded default 3.0 is used.
9. **AI mode:** each symbol gets an independent agent run (`asyncio.gather`). There is no cross-symbol arbitration; the LLM sees only its own symbol's reports.
10. `momentum_rank` contains the only cross-asset ranking logic, and that branch never fires live (§8).

**Verdict: MISSING.** There is no opportunity-selection layer.

---

## 8. Strategy inventory and mechanics

Registered in `app/strategy/__init__.py`: `ema_crossover, rsi_filter, breakout, mean_reversion, dca, grid, risk_parity, momentum_rank, pair_spread`, plus `ml_signal` (if lightgbm) and `ensemble` (built from a config string).

All strategies share:

- the engine read of `signal = df.iloc[-2]` (last *closed* bar — no look-ahead);
- ATR-based SL/TP from `RiskManager` (not strategy-defined);
- the H1 EMA-21 trend filter;
- the risk chain;
- no strategy-specific exits;
- no ML or AI interaction except the sentiment veto and the confirmation gate.

| Strategy | Mechanics | Defaults | Tests | Notable defects / weaknesses | Status |
|---|---|---|---|---|---|
| ema_crossover | Fast/slow EMA cross | 20/50 | Unit (`test_ema_strategy.py`) | Whipsaw in ranges (self-declared) | IMPLEMENTED |
| rsi_filter | EMA cross + RSI bounds + 5-bar divergence veto | 20/50/14/70/30 | No dedicated test (37 % cov) | — | IMPLEMENTED BUT UNVERIFIED |
| breakout | Close > prior-20 high + 0.5 ATR (and tick volume > avg) | 20/14/0.5 | Unit | Tick volume is broker-specific | IMPLEMENTED |
| mean_reversion | Close ≤ lower BB & RSI<30 (and inverse); skip bandwidth < 0.005 or near 5-bar min | 20/2.0/14 | Unit | Python row loop; uses trend-style ATR exits rather than mean target | IMPLEMENTED |
| grid | SMA20 ref ± level×5.0 **absolute price units** | 5.0 / 5 levels | None | Spacing not symbol-scaled (5.0 = 500 pips USDJPY, 5 % of WTI, trivial on BTC); not a real multi-order grid | PARTIALLY IMPLEMENTED |
| dca | Signal on bar indices 20, 40, … 180 of the 200-bar window | 20 | None | **Engine reads index 198 → never 1 with 200 bars (CONFIRMED-RUNTIME)** | BROKEN (live) |
| momentum_rank | Rank active symbols by 20-bar return (M15 hardcoded); BUY #1 / SELL last; ADX ≥ 20; fallback absolute momentum | 20 | None | **Cross-asset signal written to `iloc[-1]` only; engine reads `iloc[-2]` → ranking branch never trades (CONFIRMED-RUNTIME)**; fallback branch works | PARTIALLY BROKEN |
| risk_parity | Cross-symbol ATR% weights → per-bar signals | — | None | Labelled risk parity but emits directional signals; no allocation | PARTIALLY IMPLEMENTED |
| pair_spread | z-score of spread vs pair symbol (M15, 250 bars) or single-symbol z-score fallback | — | None | Trades one leg only (no hedge leg) | PARTIALLY IMPLEMENTED |
| ml_signal | LightGBM 3-class over 48 features, ADX/ATR-percentile dampening, dynamic threshold | 0.5 | Unit (`test_ml_strategy.py`) | Falls back to **any** active model (other symbol) if none for this symbol; does not set `_last_ml_signal` so the confirmation gate never counts ML; inference window 200 bars vs EMA-200 feature | IMPLEMENTED, defects |
| ensemble | Weighted vote of sub-strategies; ±0.6 thresholds | config string | None (0 % cov) | Not used unless `ensemble_strategies` / params set | IMPLEMENTED BUT UNVERIFIED |
| mtf_filter (H4/D1 consensus) | — | `use_mtf_filter=True`, `mtf_timeframes="H4,D1"` | None | **Never imported**; engine uses an H1-only filter; `mtf_timeframes` unused | DEAD |
| kalman | Kalman trend filter | — | None | Never imported | DEAD |

How strategies combine: **one strategy per symbol (independent).** It is not an ensemble or vote unless `ensemble` is explicitly selected. The selected strategy can change in three ways:

- a human via the API;
- the weekly optimizer (params only, flag-gated);
- the Reflector agent's `apply_strategy` tool (flag + rollout ∉ {shadow, paper} + 1 h cooldown + 3/day).

The repo has a `STRATEGY_PROFILES` regime-suitability map, but it is LLM-advisory only.

---

## 9. Strategy evidence (profitability)

| Evidence type | Found in repo? | Detail |
|---|---|---|
| Backtest results (any strategy) | **No** | No result files, notebooks or reports. Backtester exists only as a UI/API tool |
| Walk-forward / OOS | **No results** | Code exists (§19) |
| Monte Carlo | **No results** | Code is defective (§19) |
| Paper results | **No** | — |
| Live results | **Anecdotal only** | `docs/screenshots/history.png`: 7 trades (USDJPYmicro, GOLDmicro, OILCash), 3W/4L, +$1.68 total, 14–17 Apr 2026, mix of "manual" and "[Agent:micro]" |
| Trade count, PF, Sharpe, Sortino, expectancy, MDD, costs | **None documented** | — |
| Cost realism | Not represented by default | `include_costs=False` default; when on: half-spread = 1.0 price unit for every symbol, commission 0.2 % of `close×lot×100` |

**Bias checks in the evaluation tooling:**

- **Look-ahead:** the signal on bar *i−1*, entry at bar *i*'s open, is correct. SL/TP are not evaluated on the entry bar itself, a mild optimistic bias.
- **Leakage:** in ML (§11). Also, a backtest of `ml_signal` over a window overlapping its training data is not prevented.
- **Survivorship:** not applicable (fixed symbols).
- **Overfitting / parameter mining:**
  - The weekly optimizer accepts LLM-suggested params if an **in-sample** 90-day backtest score is strictly higher, with no costs, on `settings.symbol` (legacy default GOLD) data regardless of which engine is being optimized.
  - `optimize()` is called without `strategy_name`, so it defaults to `ema_crossover`. Non-EMA engines raise and are skipped.
- **Tiny samples:** Kelly activates at 20 trades; the confidence-threshold win rate uses ≥10 trades.

**Verdict: no strategy, nor the combined system, has demonstrated profitability. Nothing can be called "proven."**

---

## 10. AI agent inventory

| Agent | Model | Invoked by | Allowed-tool list (auto-approve) | Prompt says | Real capability (from SDK source) | Validation of output |
|---|---|---|---|---|---|---|
| Single agent | `claude-sonnet-4-20250514` | Every candle when `AGENT_MODE=single` (default) | **None passed → all 41 MCP tools** | "MUST NOT call place_order, modify_position, or close_position" | Can call `place_order` / `modify` / `close` (prompt-only restriction). Prompt is user-editable via `/api/agent-prompts` | Hallucination regex check **after** run (log only) |
| Reflector | Haiku 4.5 | Phase 0 of multi | analyze_recent_trades, detect_regime, learnings/context, strategy profiles, overfitting score, **apply_strategy**, get_switch_status, memory tools* | Review & learn; may apply strategy | Can switch strategy (flag + rollout guard). *`get_memories/save_memory/validate_memory` are **not registered** on the MCP server → dead | Strategy-switch guard |
| Technical analyst | Haiku 4.5 | Multi | tick, ohlcv, indicators | Read-only analysis | All tools callable (bypassPermissions) | None |
| Fundamental analyst | Haiku 4.5 | Multi | sentiment, history, daily P&L, performance | Read-only | Same | None |
| Risk analyst | Haiku 4.5 | Multi | account, exposure, positions, validate_trade, correlation, lot/SL/TP calc, overfitting | APPROVED / CAUTION / REJECTED | Same; called with no proposed trade (signal=0), so it gives a generic verdict; `validate_trade` is fed by the LLM's own arguments | None — the Orchestrator honouring REJECTED is prompt-only |
| Orchestrator | Sonnet 4 | Multi | place_order, modify_position, close_position, log_decision, log_reasoning | "ONLY agent with execution authority … find good trades … max 3 trades per cycle" | Executes through guardrails + rollout mode | Guardrails (partly ineffective, §14) |
| Sentiment "agent" | Haiku 4.5 via `sdk_complete` | 15-min job | none (`max_turns=1`, bypassPermissions) | JSON label / score / confidence | Output parsed as JSON; confidence decays 10 %/h | Range not clamped; label used as veto |
| Strategy optimizer | Haiku 4.5 via `sdk_complete` | Weekly | none | Suggest params | Params clamped to `PARAM_RANGES`, then in-sample backtest compare | Weak (§9) |

Notes:

- **README claims "8 specialist agents".** Source has 5 tool-using agents in the pipeline plus the single agent plus 2 single-shot completions.
- **README/CLAUDE.md claim an "Anthropic SDK fallback".** `ai/client.py` uses the Agent SDK only. `anthropic_api_key` is "declared but unused". **No fallback exists.**
- **Failure behaviour:** SDK errors return `"Agent error: …"`, and the scheduler logs it. There is no retry and no trading pause. The `MAX_DAILY_AGENT_CALLS` / `validate_agent_call` / `ON_TOKEN_FAILURE="pause"` constants are **never enforced** (no callers).
- **What the LLM can directly do** (paths B / Reflector):

  | Action | Can the LLM do it directly? |
  |---|---|
  | Open trades | Yes |
  | Close trades | Yes |
  | Change size | Yes (≤1.0 lot; 0.01 in micro) |
  | Change SL | Yes (widening capped at 2×) |
  | Change TP | Yes |
  | Change risk limits | No (no tool) |
  | Modify strategies | Yes (`apply_strategy`, guarded) |
  | Modify code | Plausibly yes via built-in tools (see §21) |
  | Promote models | No |

- **The hard boundary between AI recommendation and deterministic authority** is the guardrail function and the rollout check inside `broker.place_order`. Everything else (analysis-only role, honouring REJECTED, max 3 trades) is prompt text.

---

## 11. ML system

| Aspect | Finding |
|---|---|
| Algorithm | LightGBM multiclass (3 classes), 100 rounds, early stopping 10, `num_leaves=15`, depth 4, class-balanced sample weights |
| Features | 48 columns: EMAs, RSI, ATR / ATR% / ATR percentile, rolling std, BB, MACD, stochastic, candle shape, ROC, cyclical hour / day-of-week, session flags, volume ratio, ADX / DI, sentiment (6 cols). Macro columns are merged but not in `FEATURE_COLUMNS` (unused) |
| Target | Symmetric triple-barrier: which of ±`tp_pips×pip_value` is touched first within `forward_bars` (10; BTC 5), else HOLD. `sl_pips` is computed then discarded (no-op expressions) |
| Per-symbol? | Model named `lightgbm_{symbol}_*`. **Inference falls back to any active model of any symbol** when the symbol's model is missing |
| Split | Chronological 80/20 (`train`) or 3 expanding folds (`train_walk_forward`); retrain job holds out the last 15 % as validation |
| Leakage / validity | (a) **Early stopping uses the test fold as `valid_sets`**, so reported test accuracy is optimistically biased. (b) **Best-fold model selected by test accuracy**, then "final" accuracy is measured on the last 20 %, which is fold 3's own early-stopping set. (c) No purge/embargo between train and test despite 10-bar forward labels. (d) Feature selection uses permutation importance on the first 60 % (good). (e) Sentiment features are joined by calendar day, so intraday look-ahead of up to 1 day exists if same-day sentiment is created after the bar. In practice the retrain path never passes `sentiment_df` (explicitly "reserved"), so the 6 sentiment features are NaN/absent |
| Train/serve skew | Training uses long DB history; inference uses a 200-bar window, so EMA-200 and ATR-percentile are not warmed the same way. Retrain uses `engine.timeframe`; the API training path uses `ml_timeframe` (BTC H1). Models can be trained on one TF and served on another |
| Retraining | Weekly (Mon 04:00 UTC) per symbol + on symbol bootstrap + manual API |
| Promotion | Automatic if `new_val_accuracy ≥ 1.05 × current_accuracy`. **But `current_accuracy` is read from the stored `classification_report` (walk-forward last-20 % accuracy), while `new_accuracy` is a different hold-out metric**, so the comparison is inconsistent. Accuracy (not P&L or calibration) is the only criterion |
| Rollback | Settings `ml_auto_rollback*` exist; **no code reads them** (DOCUMENTATION/CONFIG-ONLY) |
| Drift detection | `ml/drift.py` (feature-stats z-scores) exposed via API; 0 % test coverage; **does not gate trading or retraining** |
| Calibration | `ml/calibration.py` via API only; 0 % coverage; not used in decisions |
| Persistence / versioning | joblib bytes in `MLModelLog.model_binary` + HMAC digest verified before `joblib.load` (good mitigation for pickle RCE). Multiple "active" rows can coexist (auto vs manual names), and the loader uses `.limit(1)` without ordering, so selection is non-deterministic |
| Decision impact | Only when the engine's strategy is `ml_signal` (not a default for any asset class). Otherwise ML is analytics. The confirmation gate's ML source is never populated |

**Verdict:** ML is real code, but its evaluation is biased and its promotion logic is unsound. It affects trading only if explicitly selected.

---

## 12. Data requirements

| Data | Provider / API | Auth | Cost | Refresh | Storage | Failure behaviour |
|---|---|---|---|---|---|---|
| Live ticks | MT5 via bridge `/tick/{sym}` | Bridge key | Broker | 1 s poll | Redis pub/sub only | Tick rejected if > 30 s old or spread > 3× EMA-avg → order skipped |
| Live bars | MT5 `copy_rates_from_pos` (200 bars) | Bridge key | Broker | Each candle | Not stored | Empty df → no signal |
| Historical bars | MT5 `copy_rates_range` via `/ohlcv/{sym}/history`, collected in 30-day chunks to Postgres `ohlcv` | Bridge key | Broker | On symbol add (90 d), `/api/data/collect`, backtest "mt5" source | Postgres | Chunk failures logged and skipped; gaps only logged |
| Tick history | **None** | — | — | — | — | — |
| Account / positions / deals | MT5 via bridge | Bridge key | — | 30 s / 5 min | DB `trades` | Empty positions ⇒ sync skipped (timeout heuristic) |
| News | Google News RSS, FXStreet RSS, Investing.com RSS (feedparser) | None | Free (ToS unverified) | 15 min | DB `news_sentiment` + Redis | No news ⇒ no sentiment ⇒ no veto |
| Economic calendar | Forex Factory `ff_calendar_thisweek.json` (nfs.faireconomy.media) | None | Free | Hourly (Redis cache 1 h) | Redis + memory | Hardcoded FOMC/NFP/CPI fallback (cannot trigger, §13) |
| Macro | FRED (7 series) | `FRED_API_KEY` | Free | Daily 07:00 UTC | Postgres `macro_data` | Disabled without key |
| Sentiment / LLM | Claude Haiku/Sonnet via Claude Agent SDK + CLI (`CLAUDE_CODE_OAUTH_TOKEN`, Max subscription) | OAuth token | Subscription | 15 min + every candle | DB `ai_usage_logs` etc. | Returns None; strategy path continues without AI |
| Binance | Code only | — | — | — | — | Dead |

**Direct answers:**

- **Do we need to manually export CSV bars?** No. There is no CSV ingestion path at all. History is pulled from MT5 through the bridge (`HistoricalDataCollector.collect`), automatically on symbol creation (90 days) or via `/api/data/collect`.
- **Do we need tick CSV files?** No. Ticks are never stored or used historically. Backtests are bar-based only.
- **Can MT5 retrieve history automatically?** Yes, via `copy_rates_range` (bounded by what the broker terminal has downloaded; the repo does not force terminal history download).
- **What must be acquired separately?**
  - A Windows host with the MT5 terminal and a broker login.
  - A Claude OAuth token (for any AI features).
  - Optionally a FRED key and Telegram.
  - Postgres + Redis.
- **Is MT5 data alone sufficient to START?** Yes for the strategy engine; it trades with no AI, no news and no calendar (filters fail open).
- **Historical depth required:**
  - 200 bars live;
  - ≥500 bars in the last 90 days for ML retrain;
  - ≥200 for walk-forward;
  - `min_bars_required` per strategy for backtests.
- **Incomplete history:**
  - gaps are logged at DEBUG level only;
  - duplicates are dropped;
  - there is no forward-fill;
  - EMA / indicators silently span gaps;
  - ML retrain is skipped if < 500 bars.

---

## 13. News / macro / calendar system

**What exists (do not add another provider — this is already Forex Factory's feed):**

| Question | Answer (source + runtime) |
|---|---|
| Where calendar comes from | `MacroEventCalendar.fetch_from_api()` → `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (Forex Factory weekly JSON) |
| Feed contents (runtime-verified 2026-10-01) | Keys: `title, country, date, impact, forecast, previous`; countries AUD, CAD, CHF, CNY, EUR, GBP, JPY, NZD, USD; impact High / Medium / Low / Holiday; dates with ET offsets (e.g. `2026-10-02T08:30:00-04:00`); includes central-bank speakers and minutes |
| What the repo keeps | **Only `country == "USD"` and `impact == "High"`**; keeps title, date, time; **discards forecast / previous**; no actual values (feed has none); no JPY/EUR/etc. events, so BoJ decisions, Japanese CPI and Tankan are invisible to the USDJPY engine. Oil inventories (usually Medium) are invisible to OILCash |
| Timestamps | **BROKEN.** `event_dt.strftime("%H:%M")` keeps ET wall-clock time and drops the offset. `is_near_event` then treats it as UTC. Runtime: "Non-Farm Employment Change 2026-10-02 08:30" stored; true release 12:30 UTC. The block window (0–2 h before) fires 06:30–08:30 UTC and is over 4 hours before the release (CONFIRMED-RUNTIME) |
| Scheduled events available before release? | Yes (weekly feed), but mis-timed as above |
| Only next week | The feed is "this week" only; the 7-day lookahead cannot see beyond the current week |
| Fallback | Hardcoded FOMC 2026 dates (19:00 UTC), first-Friday NFP 13:30 UTC, CPI "12th of month" 13:30. `_hardcoded_events` requires `now ≤ date-midnight`, so same-day events are excluded; **`is_near_event` can never fire on fallback data** (0 hits across a 3-day simulation around NFP, CONFIRMED-RUNTIME). Also DST-unaware |
| Refresh topology | Scheduler refreshes only **the first engine's** calendar instance (`break` after the first). Other engines read the shared Redis cache only inside `refresh()`, which they never call. Their in-memory list stays empty → fallback → never fires. **Effectively only the first engine gets a (mis-timed) event filter** |
| Effect on trading | Engine: lot × 0.5 (never blocks). AI path: none (prompt says "flag macro events"). Webhook: same lot halving. Window: before the event only; none after the release |
| News | RSS headlines (≤5, ≤2 h old) → Claude sentiment label / score / confidence → used **after publication** as a direction veto (`can_open_trade`), a confirmation-gate vote, and agent context |
| Macro | FRED DXY proxy, Fed funds upper, CPI, 10Y-2Y, 10Y, S&P 500, LBMA gold AM (the last series was discontinued by FRED in 2022 — unverified here). Used for dashboards / context and optional ML columns (not in the feature list) |
| Central-bank events / speeches | Present in the feed, filtered out unless USD + High |
| NFP / CPI / FOMC | Represented when tagged USD/High in the feed (verified: PCE, GDP, AHE, NFP present this week) |

**Verdict:** news analysis exists. A news **compliance gate** (block before or after scheduled high-impact releases) is **MISSING in effect**. The data source already contains the needed fields; the gap is parsing (timezone), filtering (currency relevance) and enforcement (block vs halve, plus post-event window, plus all engines).

---

## 14. Risk engine

### 14.1 Every deterministic control

| Control | Where | Input basis | Status |
|---|---|---|---|
| Risk per trade 1 % | `RiskManager.calculate_lot_size` | **Balance** | IMPLEMENTED with scale defects (below) |
| Kelly ¼ (0.5 %–2 % risk) after ≥20 closed trades & WR ≥ 35 % | `engine._calculate_position_size` | DB trade P&L (all origins) | IMPLEMENTED; small-sample |
| Regime lot multiplier (0.5–1.0) | RiskManager | ATR/ADX regime | IMPLEMENTED with unit bug |
| Vol factor ×0.7 / ×1.2 | RiskManager | GARCH% or ATR fraction | IMPLEMENTED with unit bug |
| Warm-up ramp (25 %→100 % over 2 h after start) | engine | Wall clock | IMPLEMENTED |
| Loss-streak cut (×0.75 at 2, ×0.5 at 3+) | engine | Last 5 DB trades | IMPLEMENTED |
| Max lot per symbol | RiskManager / profile | Static | IMPLEMENTED |
| Event lot ×0.5 | engine | Calendar | BROKEN timing (§13) |
| Max concurrent per symbol (3) | `can_open_trade` | Bridge positions | IMPLEMENTED |
| **Daily loss 3 % per symbol** | `can_open_trade` + `CircuitBreaker.is_triggered` | Redis `circuit:daily_pnl:{sym}` | **BROKEN — never written** |
| **Portfolio daily loss 10 %** | `CircuitBreaker.is_global_triggered` | Same keys, for `settings.symbol_list` (env) only | **BROKEN** |
| Drawdown-from-peak 15 % halt | `CircuitBreaker.is_drawdown_halted` | **Balance** (realized only); peak key has no TTL; resettable via `/api/bot/reset-peak` | IMPLEMENTED (equity ignored) |
| 80 % daily-loss early warning | CircuitBreaker | Dead key | BROKEN |
| Cool-down auto-resume (60 min) | `can_resume` | Redis | IMPLEMENTED |
| Portfolio leverage ≥3× | `BotManager.check_portfolio_limit` | Notional (no FX conversion) | PARTIAL (USDJPY JPY notional) |
| Correlation conflict | `check_correlation_conflict` | Positions + rolling/static corr | IMPLEMENTED; blocks hedges, allows concentration; 0 % tests |
| Adaptive confidence threshold | `compute_effective_confidence` | Session, regime, recent WR, DD | IMPLEMENTED (only affects sentiment veto) |
| Stop requirement | Engine always sets ATR SL; bridge accepts any SL incl. 0; path B guardrails do **not** require an SL | — | PARTIAL |
| Margin check | None pre-trade (broker rejects) | — | MISSING |
| Equity / unrealized P&L | Not used by any engine gate | — | MISSING |
| Guardrails (path B only) | `mcp_server/guardrails.py` | | |
| — lot ≤ 1.0, > 0 | | | IMPLEMENTED |
| — ≤3 per symbol, ≤5 total | | Bridge positions (all) | IMPLEMENTED |
| — daily loss 3 % | | Dead key | BROKEN |
| — weekly loss 7 % | constant only | | DOCUMENTATION-ONLY |
| — 5 consecutive losses halt | | `record_trade(is_win=True)` always | **BROKEN (CONFIRMED-RUNTIME)** |
| — ≤5 trades/hour, ≥120 s spacing | | Redis counters (path B only) | IMPLEMENTED |
| — spread ≤3× avg | | `avg_spread = spread` | **BROKEN (CONFIRMED-RUNTIME)** |
| — max agent calls/day 200 | constant + method | No callers | DEAD |
| Kill switch | `/api/bot/emergency-stop` (close all, stop engines); `/api/bot/stop` | Human | IMPLEMENTED (not tested end-to-end) |

### 14.2 Sizing scale defects (CONFIRMED-RUNTIME)

`calculate_lot_size` adds `slippage_pips (2.0) × pip_value` **price units** to the SL distance. Balance 10,000, 1 % risk:

| Symbol | SL distance | Cushion added | Lot computed | Without cushion |
|---|---|---|---|---|
| GOLD | 15.0 | 2.0 | 0.06 | 0.07 |
| OILCash | 0.6 | **20.0** | 0.05 | 1.66 (cap 5.0) |
| BTCUSD | 900 | 2.0 | 0.11 | 0.11 |
| USDJPY | 0.15 | **200.0** | 0.01 (min) | 0.01 (JPY P&L not converted; correct ≈1.0 lot for $100 at 150) |

The latest commit's "money correctness" fix replaced `pip_value×100` with `contract_size` but left the slippage cushion on the pip_value scale. FX quote-currency conversion is missing. Errors err on the small side for OIL and USDJPY (2–3 orders of magnitude under-risked), but the sizing claim is not met.

### 14.3 ATR% unit mismatch (CONFIRMED-RUNTIME)

- The engine computes `atr_pct = atr / price` (a fraction, e.g. 0.006).
- The thresholds `HIGH_VOL_THRESHOLD = 0.5` and `LOW_VOL_THRESHOLD = 0.2` are percentages.
- Effect: `detect_regime` never returns `trending_high_vol` (GOLD 0.625 % → "trending_low_vol").
- When GARCH fails, lots are always ×1.2.
- Trailing steps are always tightened ×0.7.

### 14.4 Does live broker P&L reach the risk engine?

- **Balance** reaches it (drawdown halt, sizing).
- **Realized daily P&L does not.**
- **Equity / floating P&L is never used.**

**This is not real account protection against an intraday loss sequence or an open-position drawdown.**

### 14.5 Prop-firm (FTMO-type) compliance vs general risk

| FTMO-style element | Present? |
|---|---|
| Daily loss limit (equity-based, server-day) | No (balance-based design, and the realized version is dead) |
| Max total loss | Partial: 15 % drawdown from *peak balance* (not initial balance, not equity) |
| News restrictions | No effective gate (§13) |
| Overnight / weekend restrictions | None |
| Trade frequency limits | Path B only (5/h, 120 s) |
| Position-size limits | Per-symbol `max_lot`, guardrail 1.0 lot |
| Emergency liquidation | Manual endpoint only; no automatic flatten on limit breach (breakers pause new trades; open positions remain) |
| Account-state protection | Peak-balance key persisted; can be reset by API |

**No prop-firm compliance layer exists.** Nothing here duplicates a separate FTMO engine.

---

## 15. Execution (MT5 pathway)

**Flow:** `engine._size_and_place_order` → `OrderExecutor.place_order` (≤3 attempts; backoff 1 s, 2 s; no retry on "margin / insufficient / invalid volume / market closed / symbol not found") → `MT5BridgeConnector._request` (≤3 attempts on timeout / connect error) → bridge `POST /order` → `mt5.order_send(TRADE_ACTION_DEAL, IOC, deviation 20)`.

| Concern | Handling | Status |
|---|---|---|
| Rejections | Bridge returns `retcode != DONE` as an error string; executor retries non-permanent ones | PARTIAL |
| Partial fills | IOC; `TRADE_RETCODE_DONE_PARTIAL` (10010) treated as **failure** → executor retries → possible over-fill | BROKEN / PLAUSIBLE |
| Requotes / price changed | Retried with a fresh tick; deviation 20 points | PARTIAL |
| Filling mode | Hard-coded IOC (some brokers / symbols require FOK / RETURN) | RISK |
| **Duplicate orders** | **No idempotency key / client order ID / pre-retry position check.** A timeout after the broker accepted → up to 3 executor × 3 connector = **9 POSTs** for one signal (executor retry CONFIRMED-RUNTIME) | **BROKEN** |
| Fill price | Bridge returns the **requested tick price** (`price`), not `result.price`; returns requested `req.lot`, not the clamped volume → slippage logging and DB `open_price` / `lot` inaccurate | BROKEN |
| Stale prices | Engine tick rejected if > 30 s old; bridge converts `tick.time` with `datetime.fromtimestamp` (VPS local TZ, while MT5 times are server-time epoch) → age miscomputed (consistent with the CLAUDE.md "frequent stale tick" known issue) | PLAUSIBLE defect |
| Spread spikes | Engine: tick rejected if spread > 3× EMA-average (per-process memory); path B: dead | PARTIAL |
| Market closure | Asset-class hour approximations; broker rejection otherwise | PARTIAL |
| Symbol mismatch | Alias resolution (canonical ↔ broker), URL-encoding; bridge `symbol_select` | IMPLEMENTED |
| Disconnects | Bridge `ensure_connected()` re-init + login; health monitor pauses after 3×30 s; watchdog restarts the bridge (≤3/h) | IMPLEMENTED |
| Close detection | 30-second diff of known vs current tickets; deal lookup by `position_id` (1-day window; multi-exit positions keep the last deal only); if no deal found, profit = 0 | PARTIAL |
| Reconciliation | 5-minute DB vs MT5: orphans auto-adopted (any magic number, incl. manual trades); phantoms closed from 7-day history, else **profit = 0** | PARTIAL (silent P&L loss) |
| Magic-number scoping | Positions are **not filtered by magic**; manual trades count against limits and get trailed by the engine (only if ATR was tracked — orphans are not) | RISK |
| Restart | Engines come up **STOPPED**; human must call `/api/bot/start`; known tickets re-seeded on start; in-memory trailing ATR map lost, so **positions opened before a restart are never trailed / breakevened** | PARTIAL |
| Paper mode (engine) | In-memory simulated positions; lost on restart; random 1–3-tick slippage; no spread; no DB rows | PARTIAL |
| Idempotent? | **No** | — |

---

## 16. Portfolio management

- The portfolio layer is limited to three things: the notional-leverage gate, the correlation-conflict check, and per-symbol concurrency.
- `risk/portfolio_optimizer.py` (max-Sharpe / risk-parity with cvxpy) and `risk/var.py` are **API analytics only**. `risk/portfolio_risk.py` is dead.
- There is no capital allocation, no per-asset budget, no exposure netting, and no FX conversion.

See §7 for selection behaviour.

---

## 17. Autonomy

| Stage | Unattended? | Evidence |
|---|---|---|
| Startup | Yes (container), but engines **STOPPED** | `BotManager.__init__`; no auto-start in lifespan |
| MT5 connection | Yes (bridge on VPS, watchdog) | `mt5_bridge` |
| Data acquisition | Yes (live); history on symbol add / API | collector |
| Scanning / analysis | Yes, once started | scheduler |
| Strategy selection | Static per symbol; optional flag-gated LLM switch | — |
| Risk approval | Yes (with defects) | §14 |
| Order execution | Yes | §15 |
| Monitoring | Yes (health, reconcile, Telegram) | — |
| Position management | Yes for positions the engine opened in the current process | — |
| Exit | Broker SL/TP + trailing | — |
| Journal | Yes (DB trades, events, order audit, AI logs) | — |
| Learning | Weekly ML retrain (auto-promote), optional optimizer auto-apply (flag) | — |
| Restart / recovery | **Partial — human must re-start trading after every deploy or crash**; trailing state lost | — |

**Human required for:**

- `/api/bot/start` after every restart;
- rollout-mode promotions;
- enabling the auto-strategy-switch flag;
- symbol onboarding;
- resetting peak balance;
- resolving reconciliation "profit = 0" records;
- MT5 terminal / VPS operation;
- Claude OAuth token renewal.

---

## 18. Learning / self-improvement

| Mechanism | What actually changes | Governance |
|---|---|---|
| ML retrain (weekly, bootstrap) | Model weights; auto-promoted on an inconsistent accuracy comparison | No human approval; no P&L criterion; no rollback code |
| LLM optimizer (weekly) | Strategy params of `ema_crossover` only (others fail), if in-sample backtest score improves and Redis flag `enable_auto_strategy_switch=1` | Flag + StrategySwitchGuard (1 h cooldown, 3/day) |
| Reflector `apply_strategy` | Engine strategy choice (and params) | Flag + rollout ∉ {shadow, paper} + guard |
| Reflector learnings / session memory | Redis text memories (24 h / 7 d) and DB memory consolidation; injected into prompts | None needed (text only) |
| Adaptive confidence / Kelly / streak | Deterministic parameters derived from recent trades | Code-bounded |
| Rule creation / code modification | None by design; but agents run with `bypassPermissions` in a container with source and a writable home | **Not governed** (see §21) |
| Automatic deployment | Railway auto-deploy on push (CI) — human-pushed | — |

"Learning" here is real but shallow: model refit and parameter swaps. There is no out-of-sample-validated promotion and no performance-based demotion.

---

## 19. Testing infrastructure (backtest / WF / MC / replay)

| Tool | Data | Resolution | Costs | Look-ahead | Notes | Status |
|---|---|---|---|---|---|---|
| `BacktestEngine` | DB or live MT5 bars | Bars | Off by default; half-spread 1.0 price unit; commission 0.2 % × `close×lot×100` | Signal t−1 → open t (good) | **P&L uses hardcoded `×100` contract**; one position at a time; ignores trailing, BE, partial TP, MTF, gates, correlation, Kelly, regime-dependent live behaviour; same-bar SL-first (conservative); entry-bar SL/TP unchecked; Sharpe annualised with √252 on per-bar returns | IMPLEMENTED, low fidelity |
| Grid optimizer | — | — | Off | — | In-sample ranking | IMPLEMENTED |
| Walk-forward | Same | — | Off | Train/test split correct | Anchored / sliding; IS-vs-OOS Sharpe ratio; bootstrap CI; param CV | IMPLEMENTED BUT UNVERIFIED (24 % cov) |
| Monte Carlo | Trade list | — | — | — | **Permutation of trade order → final-balance stats identical every run; P(ruin) / P(profit) ∈ {0, 1} (CONFIRMED-RUNTIME)**. Only drawdown distribution varies | BROKEN for stated purpose |
| Permutation test / cointegration | Signals / prices | — | — | — | statsmodels-based | IMPLEMENTED |
| Overfitting composite (WF ratio + permutation + param stability + MC ruin) | — | — | — | — | Inherits the MC defect | PARTIAL (99 % unit-tested) |
| Stress test | Synthetic shocks | — | — | — | API only; 0 % cov | IMPLEMENTED BUT UNVERIFIED |
| Replay / tick sim | — | — | — | — | — | MISSING |
| Timezone / DST / session | Bar timestamps from bridge as naive local time | — | — | — | No TZ normalisation | MISSING |
| Missing bars | Logged only | — | — | — | — | PARTIAL |

---

## 20. Runtime results (actually reproduced)

**Test suite** (`pytest tests/ -q`, Python 3.12.3, repo-pinned deps, SQLite + fakeredis, no source changes):

| Collected | Passed | Failed | Skipped | Errors | Warnings | Coverage (app) |
|---|---|---|---|---|---|---|
| 504 | **504** | 0 | 0 | 0 | 1,257 | **31 %** (11,971 stmts) |

- The README claims "444 tests" and CLAUDE.md claims "403 / 444 / 454+"; actual is 504.
- `-W error::DeprecationWarning` → 1 collection error, meaning deprecation debt (`datetime.utcnow` etc.).

**Coverage of critical paths:**

| Area | Modules and coverage |
|---|---|
| Runtime path | scheduler 9 %, BotManager 13 %, engine 42 % |
| Risk | correlation 0 %, CircuitBreaker 89 %, RiskManager 81 % |
| AI | confirmation gate 0 %, hallucination check 0 %, strategy optimizer 0 % |
| ML | trainer 0 %, drift 0 %, calibration 0 % |
| Data | collector 0 %, macro 0 % |
| MT5 / connectivity | connector 23 %, market_data 23 %, health monitor 0 % |
| MCP | server 0 %, broker tool 37 %, guardrails 96 % |
| Backtest | engine 39 %, walk-forward 24 %, Monte Carlo 44 % |

**Tests that mostly prove mocks:**

- engine integration tests use a mock connector;
- guardrail tests validate the function in isolation, but not the inputs actually passed by `broker.py` (which is why the spread and loss-streak defects pass CI);
- agent tests mock the SDK.

**Untested critical production paths:** order retries / idempotency, partial fills, restart recovery, reconciliation, calendar parsing, daily P&L recording, webhook risk bypass, the multi-agent end-to-end with real tool restrictions.

**Defect probes run** (my scripts in the session scratchpad, importing repo code read-only). Results:

| # | Probe | Result |
|---|---|---|
| P1 | Monte Carlo on 8 trades | median = mean = p5 = p95 final balance = 10,070; P(ruin) = 0, P(profit) = 1 |
| P2 | DCA on 200 bars | Signals at 140/160/180; engine reads 198 → 0. momentum_rank `signal[-1]=1`, `signal[-2]=0` |
| P3 | Guardrails | Spread 50 vs avg 50 → allowed; 6 recorded trades → 0 consecutive losses |
| P4 | CircuitBreaker | `daily_pnl=0.0`, `is_triggered=False` (nothing writes it) |
| P5 | USDJPY backtest (profile-based RiskManager) | Median losing trade −1.59 vs ≈ −100 intended |
| P6 | Live FF feed | 5 USD-High events, times stored ET-as-UTC (08:30 vs true 12:30 UTC) |
| P7 | Fallback calendar | 0 `is_near_event` hits over 3 days around NFP |
| P8 | OrderExecutor | 3 POST attempts for one order on timeouts |
| P9 | `MLStrategy` | No `_last_ml_signal` attribute |
| P10 | Lot sizing | Table in §14.2 |

**Not run:**

- **Backtests on real data:** no broker data or MT5 available. Synthetic-data runs are not evidence of profitability, so none were reported as such.
- **Shadow / paper modes:** they require MT5 bridge + Postgres + Redis + Claude token.
- **Live trading:** prohibited by the audit rules.

---

## 21. Security

| Finding | Severity | Status |
|---|---|---|
| **Agents run with `permission_mode="bypassPermissions"` and only `allowed_tools`** (auto-approve list; SDK maps it to `--allowedTools`, not `--tools`). Built-in Claude Code tools (Bash, Write, Edit, WebFetch …) remain available to every agent, including the sentiment completion fed with **untrusted RSS headlines**. A prompt-injected headline could reach shell / file / network tools inside the backend container (which holds the bridge API key, JWT secret, vault key, DB URL in env) | High | PLAUSIBLE (SDK-source-derived; not executed — no token) |
| Single "analysis-only" agent has `place_order` / `close_position` available; restriction is prompt text, and prompts are editable via the API | High | CONFIRMED-SOURCE |
| **Auth fails open**: if `AUTH_PASSWORD_HASH` is empty, `require_auth` is a no-op for all 115 protected routes, including emergency-stop, settings, rollout mode, secrets, runners. The config comment says "fails closed"; the code does the opposite (startup only logs a warning) | High (misconfig) | CONFIRMED-SOURCE |
| Unauthenticated routes (with auth enabled): auth endpoints, `/health`, `/ws` (token query param checked in handler), `/ws/runners/...`, **`/api/webhooks/tradingview`** (shared key + timestamp + nonce) | Info | Verified by route introspection |
| Webhook bypasses all engine risk gates, engine state, guardrails and rollout mode; docstring claims "applies all risk checks" | High | CONFIRMED-SOURCE |
| MT5 bridge `/health` unauthenticated and returns account login + server; bridge binds `0.0.0.0:8001` over plain HTTP (TLS / firewall left to `setup_firewall.ps1`) | Medium | CONFIRMED-SOURCE |
| Pickle deserialization of models: HMAC digest checked before `joblib.load` (mitigated); `ModelTrainer.load_model(path)` and file-path loads in `MLStrategy.__init__` are unverified | Low | CONFIRMED-SOURCE |
| Dockerfile installs `@anthropic-ai/claude-code@latest` (unpinned) at build | Medium (supply chain) | CONFIRMED-SOURCE |
| Secrets: none committed (placeholders only); AES-GCM vault with HKDF; internal JWT minted per boot (24 h) and exported to `os.environ` (inherited by agent subprocesses) | Info / Low | CONFIRMED-SOURCE |
| CORS / trusted hosts / rate limiting / CSP headers implemented (rate limiter 0 % tested) | Info | — |
| MCP subprocess env set to only `REDIS_URL` / `MT5_BRIDGE_URL`; rollout mode read from env (`get_rollout_mode`), not the Redis-persisted value. A UI downgrade is reflected only if the env propagates to the spawned CLI / MCP process | Medium | INDETERMINATE |

---

## 22. Failure recovery

| Scenario | Behaviour |
|---|---|
| Backend restart / redeploy | Engines STOPPED until a human starts them; paper positions lost; trailing ATR map lost; known tickets re-seeded on start; pending trade saves recovered from Redis every 5 min |
| Bridge outage | 3 failures → all engines PAUSED; on recovery **all PAUSED engines resumed**, including ones paused by the drawdown halt (they re-halt at the next candle check, but positions are unmanaged meanwhile) |
| Broker rejects / timeouts | Retries without idempotency (§15) |
| DB failure on trade save | 3 retries → Redis list → 5-minute recovery job |
| Position closed while backend down | Detected by reconcile; P&L from 7-day history, else 0 |
| Partial TP flow | Close-then-reopen (not partial close); if reopen fails, exposure silently reduced; disabled by default |
| Claude unavailable | Strategy engine continues; AI path logs "Agent error"; no pause despite `ON_TOKEN_FAILURE="pause"` |
| Redis loss | Circuit-breaker / peak-balance / guardrail counters reset (peak key has no persistence other than AOF) |

---

## 23. Duplication / overlap matrix

| # | Component A | Component B | Shared responsibility | Actual authority | Conflict possible? | Both necessary? |
|---|---|---|---|---|---|---|
| 1 | Strategy engine (path A) | AI Orchestrator / single agent (path B) | **Opening / closing trades on the same symbol** | Both; they run in the same candle in strategy mode | Yes: double entries, opposite entries, AI closing engine tickets, limits counted separately | Genuine overlap |
| 2 | Engine `paper_trade` flag | `ROLLOUT_MODE` shadow / paper / micro / live | Live-vs-simulated switch | Each controls only its own path | Yes: rollout "shadow" while the engine trades live | Genuine duplicate config authority |
| 3 | `RiskManager.can_open_trade` + CircuitBreaker | `TradingGuardrails.validate_order` | Daily loss, concurrency, frequency | Path-specific; different limits (3/symbol vs 3/symbol + 5 total; frequency only in B) | Yes (inconsistent limits) | Genuine overlap; should be one authority |
| 4 | `can_open_trade` daily-loss | `CircuitBreaker.is_triggered` | Same rule, same dead key, two places | Neither works | — | Duplicate |
| 5 | Engine trailing / breakeven | MCP `modify_position` | SL modification | Both can modify the same ticket | Yes (ratchet fights) | Overlap |
| 6 | Regime: `detect_multi_tf_regime` | HMM overlay; `detect_regime(atr, adx)` | Regime label for risk | `_size_and_place_order` overwrites with ATR/ADX (unit-broken) just before sizing | Yes (three labels per candle) | Duplicate |
| 7 | H1 EMA filter (engine) | `mtf_filter.py` H4/D1 (dead) + `use_mtf_filter` / `mtf_timeframes` | Higher-TF confirmation | H1 only | No (B is dead) | Remove-candidate |
| 8 | Static `SYMBOL_PROFILES` + env | DB `SymbolConfig` | Instrument config | DB overrides static at load / hot reload | Low | Layered (intentional) |
| 9 | Env `SYMBOLS` / `settings.symbol` (legacy GOLD) | DB-enabled engines | Active universe | Engines from DB; **global breaker and optimizer still use `settings.symbol(s)`** | Yes | Partial duplicate |
| 10 | CircuitBreaker auto-resume | HealthMonitor resume-all | PAUSED → RUNNING | Whichever fires | Yes | Overlap |
| 11 | Sentiment veto (`can_open_trade`) | Confirmation-gate AI vote; Fundamental analyst | News influence | Veto in path A; LLM in path B | Low | Layered |
| 12 | `MacroEventCalendar` per engine (N instances) | Calendar created in `main.py` for context builder | Event schedule | Only the first engine refreshed | Yes (inconsistent views) | Duplicate instances |
| 13 | Runner / agent_entrypoint job system | Scheduler `_run_ai_agent` | Running the AI agent | Both call `run_agent` | Possible double runs | Partially redundant |
| 14 | Engine paper simulator | MCP paper mode | Paper execution | Separate, neither persists positions | — | Duplicate |
| 15 | Bridge volume clamp | RiskManager max_lot / guardrail 1.0 / micro 0.01 | Lot bounds | Broker spec is the final bound | Low | Layered |

---

## 24. K-LEAN review (advisory)

**Native execution: NOT PERFORMED.**

- `calinfaja/K-LEAN` was cloned and inspected (Apache-2.0).
- K-LEAN requires a NanoGPT or OpenRouter API key plus a running LiteLLM proxy (`kln init` / `kln start`) and runs from inside Claude Code slash commands. No such key or credential exists in this environment.
- I am not claiming K-LEAN ran.

**Source-derived review**, applying K-LEAN's published `code-reviewer` and `security-auditor` frameworks (quality, OWASP security, performance, error handling, testing):

| Primary audit finding | K-LEAN-framework opinion (source-derived) | Evidence | Final status |
|---|---|---|---|
| Daily-loss key never written | "Error handling & resilience" + "test coverage": a dead write path behind passing unit tests is a classic mock-proves-itself gap | grep: no callers of `record_trade_result` | CONFIRMED |
| Order retry without idempotency | Resilience: retries on non-idempotent POST are a correctness defect, not a flake mitigation | `order_executor.py`, `connector.py` | CONFIRMED |
| Auth fail-open | OWASP A07 / A05 misconfiguration | `auth.py:require_auth` | CONFIRMED |
| bypassPermissions + untrusted RSS → LLM | OWASP LLM01 prompt injection with excessive agency (LLM06) | `sdk_client.py` | PLAUSIBLE |
| 1,884-line engine with many inline imports | Complexity / SRP: refactor candidate | `engine.py` | Advisory only |
| Python row loops in strategies / ensemble | Performance: O(n) Python per bar, acceptable at 200 bars | strategies | Advisory only |

No disagreement with the primary findings.

---

## 25. iFixAI review (advisory)

**Native execution: NOT PERFORMED.**

- `ifixai-ai/iFixAi` was cloned and inspected (Apache-2.0).
- iFixAi audits a running agent endpoint (system under test) with a judge model and requires provider API keys plus a fixture.
- The audited agent needs a Claude OAuth token, an MT5 bridge, Postgres and Redis; none are available. I am not claiming iFixAi ran.

**Source-derived review**, mapping iFixAi's inspection taxonomy onto the source:

| iFixAi category | Primary audit finding | Source-derived opinion | Final status |
|---|---|---|---|
| Usurpation / Insubordination | "Analysis-only" agent retains execution tools; the Orchestrator's honouring of Risk REJECTED is prompt-only | High exposure: authority boundary not enforced in code | CONFIRMED-SOURCE |
| Fabrication | `hallucination_check` runs after the agent may already have traded; it only logs | Detection exists, prevention absent | CONFIRMED-SOURCE |
| Oversight atrophy | Model auto-promotion, optional strategy auto-switch, no human approval, Telegram-only visibility | Moderate | CONFIRMED-SOURCE |
| Concealment / Opacity | Strong journaling (`log_decision` mandatory by prompt, AI usage logs, order audit) | Strength, but `log_decision` is not enforced in code | PARTIAL |
| Systemic risk | Three uncoordinated trade originators on one account | High | CONFIRMED-SOURCE |
| Miscalibration | Sentiment confidence used as a probability threshold with no calibration; ML calibration module unused | Moderate | CONFIRMED-SOURCE |
| Manipulation (prompt injection) | Untrusted news text → LLM with bypassPermissions | High | PLAUSIBLE |

No disagreement with the primary findings.

---

## 26. Component scorecard

No overall score is given.

| Component | Grade | Evidence summary | Strengths | Weaknesses / risks | Disposition |
|---|---|---|---|---|---|
| Control plane (FastAPI routes, auth, audit log) | C | 129 routes; route introspection; tests on bot / symbols / secrets / runners APIs | Router-level auth, audit log, rate limit, CSP, vault | Auth fail-open; webhook bypass | VERIFY FURTHER / POSSIBLE IMPROVEMENT |
| Symbol / config management | B | DB profiles, hot reload, broker catalog, validator; well tested (89 %) | Runtime extensibility | Micro aliases inherit contract size; legacy `settings.symbol` still used by breaker / optimizer | KEEP / POSSIBLE IMPROVEMENT |
| Strategy library | C | 10 strategies; 4 well-tested | Clean BaseStrategy contract, no look-ahead in core | dca / momentum_rank broken live; grid unscaled; dead mtf / kalman | POSSIBLE IMPROVEMENT; REMOVE CANDIDATE (mtf_filter, kalman) |
| Strategy evidence | F | None in repo | — | No profitability evidence | MISSING |
| Trade selection / portfolio | D | Serial FCFS; leverage gate; correlation | Correlation and leverage gates exist | No ranking / allocation; JPY notional; hedge-blocking logic | MISSING / POSSIBLE IMPROVEMENT |
| Risk engine (deterministic) | D | Runtime probes P3/P4/P10, §14 | Many layers; drawdown halt works; good structure | Daily loss dead; equity unused; scale / unit bugs | POSSIBLE IMPROVEMENT (critical) |
| MCP guardrails | D | 96 % unit coverage, but runtime P3 | Central chokepoint for path B | Spread / streak / daily dead; not applied to engine / webhook | POSSIBLE IMPROVEMENT |
| Execution / MT5 bridge | C | Source + P8 | Simple, alias-aware, volume clamp, watchdog | No idempotency, wrong fill price, partial-fill handling, IOC-only, TZ | POSSIBLE IMPROVEMENT |
| Position management (trailing / BE) | C | Source; engine 42 % cov | Multi-stage trailing | Lost on restart; unit bug; not for adopted / AI tickets | POSSIBLE IMPROVEMENT |
| Reconciliation / recovery | C | Source | Orphan / phantom handling, Redis fallback | Profit = 0 fallbacks; no magic filtering; manual restart | VERIFY FURTHER |
| Data acquisition (MT5 bars) | B | Source | Automatic history pull, validation, chunking | No TZ normalisation; gaps only logged | KEEP |
| News / sentiment | C | Source | Per-symbol feeds, decay, caching | Post-publication only; injection surface | KEEP / VERIFY FURTHER |
| Economic calendar | D | Runtime P6/P7 | Already uses FF feed with all needed fields | TZ bug, USD-only, halve-not-block, first-engine-only, dead fallback | POSSIBLE IMPROVEMENT (no new provider needed) |
| Macro (FRED) | C | Source | Simple, optional | Not in decisions; LBMA series possibly stale | NO ACTION |
| AI agents | D | Source + SDK source | Clear role prompts, journaling, guardrail chokepoint | Prompt-only authority; bypassPermissions; overlapping trade authority | POSSIBLE IMPROVEMENT (security-critical) |
| ML pipeline | D | Source | Triple-barrier labels, integrity HMAC, chronological split | Test-set early stopping, inconsistent promotion, cross-symbol fallback, train/serve skew, unused drift / calibration | POSSIBLE IMPROVEMENT |
| Learning / optimizer | D | Source | Flag-gated, guarded switches | In-sample param mining on wrong symbol; only works for EMA | POSSIBLE IMPROVEMENT / VERIFY FURTHER |
| Backtester | D | Source + P5 | Correct signal / entry timing | ×100 P&L, costs off, live / backtest divergence | POSSIBLE IMPROVEMENT |
| Walk-forward | C | Source | Anchored / sliding, CI, param stability | Inherits backtester defects | VERIFY FURTHER |
| Monte Carlo | F | P1 | — | Final-balance stats degenerate | POSSIBLE REPLACEMENT (method) |
| Overfitting composite | C | 99 % tests | Multi-signal idea | Uses broken MC component | POSSIBLE IMPROVEMENT |
| Rollout (shadow → live) | D | Source | Sequential transitions, audit logged | Governs AI path only; env-vs-Redis ambiguity; no criteria | POSSIBLE IMPROVEMENT |
| Monitoring / alerts | B | Source | Telegram, health monitor, DB pool monitor, Sentry | Resume-all overrides risk pauses | KEEP |
| Runner / job system | C | 94–100 % unit cov | Well-tested infra | Duplicates scheduler agent runs; stub executor fallback | VERIFY FURTHER |
| Binance connector | F (for claimed use) | Never wired | — | Dead | REMOVE CANDIDATE |
| Dead AI helpers (bias_guard, baseline_manager, param_gate, pattern_validator, expert_framework, trade_accountability, quant_analyzer), portfolio_risk | NE / dead | No importers | — | Maintenance noise | REMOVE CANDIDATE |
| Test suite | C | 504 / 504 pass, 31 % cov | Fast, deterministic, CI-gated | Critical paths 0–13 %; mock-only proofs | POSSIBLE IMPROVEMENT |
| CI / CD | B | `ci.yml` | ruff, format, pytest, frontend build | mypy soft; coverage floor 30 % | KEEP |
| Deployment / ops | C | Dockerfile, Railway, VPS scripts | Migration-on-boot with timeout, backups | Unpinned Claude CLI; manual start after deploy | VERIFY FURTHER |
| Frontend | NE | Not audited in depth (≈11.8k LOC; builds in CI) | — | — | NO ACTION |

---

## 27. Gap register

| # | Gap | Severity | Evidence |
|---|---|---|---|
| G1 | Realized daily P&L never recorded → all daily-loss controls inert | Critical | §14, P4 |
| G2 | No equity / floating-P&L protection | Critical | §14 |
| G3 | Rollout mode does not govern the main (strategy) trader; default engine is live | Critical | §4, §17 |
| G4 | Three uncoordinated trade originators (engine, AI, webhook) | High | §23 #1 |
| G5 | Non-idempotent order retries (up to 9 POSTs) | High | §15, P8 |
| G6 | AI capability boundaries prompt-only; bypassPermissions with untrusted input | High | §10, §21 |
| G7 | Webhook bypasses all risk gates | High | §21 |
| G8 | Economic calendar TZ bug, USD-only, halve-not-block, first-engine-only | High | §13, P6, P7 |
| G9 | Guardrail spread / streak / daily checks dead | High | P3 |
| G10 | Sizing scale errors (slippage cushion, JPY conversion, micro contract size) | High | P10, §5 |
| G11 | ATR% unit mismatch in regime / vol / trailing | Medium | §14.3 |
| G12 | Fill price / volume misreported by bridge; partial fills treated as failures | Medium | §15 |
| G13 | No opportunity ranking / capital allocation | Medium | §7 |
| G14 | No overnight / weekend / session exits | Medium (prop-firm relevant) | §6 |
| G15 | Backtest fidelity (×100, costs, missing live logic) | High for evaluation | §19, P5 |
| G16 | Monte Carlo degenerate | High for evaluation | P1 |
| G17 | ML evaluation bias and promotion logic | Medium | §11 |
| G18 | Strategy dead-wiring (dca, momentum_rank) and unscaled grid | Medium | P2 |
| G19 | Manual restart required; trailing state not persisted | Medium | §22 |
| G20 | Zero profitability evidence | Critical for any deployment decision | §9 |
| G21 | Auth fail-open by misconfiguration | High | §21 |
| G22 | Critical-path test coverage (scheduler, manager, correlation, gate, trainer, MCP server) | Medium | §20 |
| G23 | No licence grant | Usage blocker | §2 |

---

## 28. Possible improvements (research recommendations only — not performed)

Ordered by risk reduction:

1. **Single risk authority.** One deterministic pre-trade gate that every originator (engine, agent, webhook) must call. Its inputs should be broker equity, realized day P&L from deal history, open risk and server-day boundaries.
2. **Single execution authority** with idempotent order submission. For example, a unique client tag in the MT5 `comment` / `magic` plus a positions/deals check before any retry. Treat `DONE_PARTIAL` as a fill, and return `result.price` / `result.volume`.
3. **Make the rollout mode apply to all originators**, and remove the duplicate `paper_trade` switch.
4. **Enforce agent tool boundaries in code:** pass `tools=[...]` / `disallowed_tools` and drop `bypassPermissions`. Separate untrusted news summarisation from any tool-capable agent.
5. **Fix the calendar using the feed already in use:** convert to UTC, map currency to symbol, block (not halve) within a pre/post window, share one calendar instance, and keep `forecast` / `previous`.
6. **Correct sizing units:** slippage in price units per symbol, quote-to-account FX conversion, broker `trade_tick_value` from `/symbol-spec` instead of static `contract_size`.
7. **Backtester parity:** reuse live sizing / exits, symbol-specific costs, multi-position support. Monte Carlo by bootstrap resampling with replacement or block bootstrap (path-dependent ruin).
8. **ML:** separate validation fold for early stopping, purge/embargo, P&L-based promotion on a fixed hold-out, per-symbol-only loading, deterministic active-model selection.
9. **Persist trailing state** (DB / Redis) and auto-resume trading after restart only when explicitly configured.
10. **Remove dead modules** to shrink the attack and maintenance surface.

---

## 29. External alternatives

Research only. Licences and maintenance status should be re-verified before any decision; none of these were installed or tested here.

| Weak component | Candidate | Why it might be stronger | Caveats |
|---|---|---|---|
| Backtester fidelity / event-driven parity | NautilusTrader (LGPL-3.0) | Event-driven engine with the same strategy code for backtest and live, realistic order / fill modelling | Heavy dependency; no native MT5 adapter (would need a custom one); steep learning curve |
| Backtester (lighter) | backtesting.py (AGPL-3.0) / vectorbt (open-source edition, Apache-2.0 + Commons Clause) | Mature, tested bar backtesters with spread / commission support | AGPL / Commons Clause restrictions; still bar-based |
| ML time-series validation | scikit-learn `TimeSeriesSplit(gap=…)` (BSD) | Already a dependency; gap parameter provides an embargo | Purged k-fold (López de Prado) needs custom code |
| Monte Carlo | No library needed | Bootstrap-with-replacement / block bootstrap is a few lines of numpy | — |
| Economic calendar | **None recommended** | The existing Forex Factory feed already supplies time, currency, impact, forecast and previous | Fix parsing / enforcement first |
| News | **None recommended** | Existing RSS is adequate for an advisory signal | — |

**No replacement is justified where the repository's existing component would be adequate once its defects are fixed.**

---

## 30. Blockers to complete verification

1. No MT5 terminal / bridge (Windows-only `MetaTrader5` package) or broker account.
   - Execution, fills, reconciliation and bridge timezone behaviour were not exercised.
2. No Claude OAuth token.
   - Agent behaviour, actual tool availability under `bypassPermissions`, and the MCP subprocess env inheritance were not exercised.
   - K-LEAN and iFixAI were also not run natively (they need their own provider keys).
3. No historical market data.
   - No meaningful backtest / walk-forward / ML retrain reproduction was possible.
   - Synthetic-data runs were used only to demonstrate code defects.
4. No Postgres / Redis servers.
   - Full lifespan startup, shadow mode and paper mode were not run.
5. Frontend not audited beyond inventory.
6. 13 non-main branches not audited.
7. Production DB symbol overrides (e.g. real micro contract sizes) are unknown.

---

## 31. Claim vs reality

| Claim (README / CLAUDE.md) | Source evidence | Runtime evidence | Status |
|---|---|---|---|
| Trades GOLD · OILCash · BTCUSD · USDJPY via MT5 | `SYMBOL_PROFILES`, bridge | Screenshot shows such trades | IMPLEMENTED BUT UNVERIFIED |
| 8 specialist Claude agents | 5 pipeline agents + single + 2 single-shot completions | — | PARTIALLY IMPLEMENTED (count inflated) |
| "Only orchestrator has execution tools — specialists are read-only" | `allowed_tools` is an auto-approve list under bypassPermissions | SDK source | **Not enforced** (PLAUSIBLE false) |
| Single agent mode is analysis-only | Prompt-only; no tool filter | — | **False in code** |
| Hard guardrails, non-bypassable (lot, daily loss, frequency, cooldowns) | Guardrails apply to path B only; daily / spread / streak dead | P3, P4 | PARTIALLY IMPLEMENTED / BROKEN |
| 5 strategies + ensemble with regime-adaptive switching | 10 strategies + ensemble; switching is LLM / flag-gated | P2 | PARTIALLY IMPLEMENTED |
| Per-symbol LightGBM, 40+ features, drift detection, auto-retrain, calibration | 48 features, auto-retrain; drift / calibration analytics-only; cross-symbol fallback | — | PARTIALLY IMPLEMENTED |
| ML auto-rollback | Settings only | — | DOCUMENTATION/CONFIG-ONLY |
| RSS + macro feeds, Claude sentiment | Yes | FF feed fetched live | IMPLEMENTED |
| Economic calendar | FF feed, USD-High only, TZ bug | P6, P7 | BROKEN (timing) |
| VaR, Sharpe, Sortino, drawdown, Monte Carlo, walk-forward, cointegration | Present; MC defective | P1 | PARTIALLY IMPLEMENTED |
| Shadow → Paper → Micro-Live → Live | AI path only; engine separate | — | PARTIALLY IMPLEMENTED |
| Self-reflection writes lessons to memory | Reflector + session tools; memory tools unregistered | — | PARTIALLY IMPLEMENTED |
| Anthropic SDK fallback | `client.py` SDK-only; key unused | — | **DOCUMENTATION-ONLY** |
| Passkey auth "ready" | Routes mounted; middleware disabled; JWT active | Route introspection | PARTIALLY IMPLEMENTED |
| Binance routing for BTCUSD | Connector never wired | — | DEAD |
| MTF H4/D1 filter (`use_mtf_filter`, `mtf_timeframes`) | Engine uses H1 only; module dead | — | PARTIALLY IMPLEMENTED |
| 444 tests (27 files) | 504 tests (35 files) | 504 passed | Inaccurate (understated) |
| "Coverage critical paths ~89 %" | Scheduler 9 %, manager 13 %, correlation / gate 0 % | Coverage run | **Not reproduced** |
| Lot sizing "money correctness" fixed (last commit) | contract_size fixed; slippage cushion and FX conversion still wrong | P10 | PARTIALLY IMPLEMENTED |
| Webhook "applies all risk checks" | Skips breakers, permission, portfolio, correlation, gate, state, guardrails, rollout | — | **False** |
| Profitable / production trading | No evidence | 7-trade screenshot (+$1.68) | INDETERMINATE (no evidence) |

---

## 32. Final disposition by component

These are audit dispositions only; none were performed.

| Disposition | Components |
|---|---|
| **KEEP** | Symbol config / hot reload; MT5 bar acquisition and collector; monitoring / Telegram / health / Sentry; CI pipeline; vault; order-audit and decision journaling; FF calendar *source* |
| **VERIFY FURTHER** | Reconciliation / recovery; walk-forward; runner / job system; deployment (Claude CLI pin, env propagation to MCP); news sentiment value; production DB symbol overrides (contract sizes); frontend |
| **POSSIBLE IMPROVEMENT** | Risk engine (daily P&L, equity, units); MCP guardrails; execution / bridge (idempotency, fills); calendar parsing / enforcement; AI agent tool boundaries; rollout governance; ML evaluation / promotion; optimizer; backtester; trailing persistence; strategy wiring (dca, momentum_rank, grid scaling); overfitting composite; control-plane auth fail-open; webhook gating |
| **POSSIBLE REPLACEMENT** | Monte Carlo method (permutation → bootstrap) |
| **REMOVE CANDIDATE** | `strategy/mtf_filter.py`, `strategy/kalman.py`, `binance/connector.py`, `risk/portfolio_risk.py`, `ai/{bias_guard, baseline_manager, param_gate, pattern_validator, expert_framework, trade_accountability, quant_analyzer}.py`, unused settings (`max_portfolio_leverage` as wired, `mtf_timeframes`, `ml_auto_rollback*`, `use_session_profiles`, `enable_scale_in`) |
| **MISSING** | Cross-asset opportunity ranking / capital allocation; news compliance gate (block, multi-currency, post-event); equity-based protection; prop-firm compliance; weekend / overnight handling; tick / replay testing; profitability evidence; licence grant |
| **NO ACTION** | Macro FRED module; frontend (not graded) |

---

*End of audit.*
