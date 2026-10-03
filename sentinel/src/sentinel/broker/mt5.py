"""MetaTrader 5 broker adapter (Broker port) — DEMO-ONLY, fail-closed.

Status: written against the documented ``MetaTrader5`` Python API and qualified ONLY against
``tests/fake_mt5.py`` (SIMULATED). It has NOT been run against a real terminal: the package is
Windows-only and no terminal or credentials exist in the build environment.

The ``mt5`` module is injected (the real ``MetaTrader5`` package on Windows, or a fake). The adapter
never hard-codes numeric MT5 constants; it reads them from the injected module.

Safety properties:
* EXECUTION DISABLED unless ALL hold, checked at connect AND before every order-capable call:
  account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO, login in the allow-list, server in the
  allow-list, and the operator config declares ``account_kind == "DEMO"``. Otherwise the adapter
  is read-only.
* MT5 timestamps are broker-server local time encoded as epoch seconds. The adapter measures the
  server offset (whole hours) against UTC and converts every timestamp to true UTC. A non-integral
  or unstable offset raises BrokerUncertain (time is unknown -> callers halt).
* Identity: magic number + comment ``sn:<24 hex of intent id>``. Brokers may truncate or rewrite
  comments; when an intent cannot be matched unambiguously the adapter reports UNCERTAIN rather
  than guessing.
* Retcodes: DONE / DONE_PARTIAL are fills; TIMEOUT, CONNECTION, or no result are UNCERTAIN (never
  resent); everything else is a definite rejection.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sentinel.broker.base import BrokerError, BrokerUncertain
from sentinel.contracts import AccountSnapshot, Direction, InstrumentSpec, Position, Quote

COMMENT_PREFIX = "sn:"


@dataclass(frozen=True)
class DemoAuthorization:
    account_kind: str  # must be "DEMO"
    allowed_logins: tuple
    allowed_servers: tuple
    magic: int


def intent_comment(intent_id: str) -> str:
    return COMMENT_PREFIX + hashlib.sha256(intent_id.encode()).hexdigest()[:24]  # 27 chars < MT5's 31


class MT5Broker:
    def __init__(self, mt5, auth: DemoAuthorization | None, asset_classes: dict, symbol_map: dict | None = None,
                 wall_clock=time):
        self.mt5 = mt5
        self.auth = auth
        self.asset_classes = asset_classes
        self.symbol_map = symbol_map or {}  # canonical -> broker symbol (e.g. XAUUSD -> XAUUSD.r)
        self._wall = wall_clock
        self._offset: int | None = None
        self.execution_enabled = False
        self.disabled_reason = "not connected"
        self.calls: list[str] = []

    # ------------------------------------------------------------------ connection / guard
    def connect(self, **kw) -> None:
        if not self.mt5.initialize(**kw):
            raise BrokerUncertain(f"MT5 initialize failed: {self.mt5.last_error()}")
        self._evaluate_guard()

    def _evaluate_guard(self) -> None:
        info = self.mt5.account_info()
        a = self.auth
        if info is None:
            self.execution_enabled, self.disabled_reason = False, "no account info"
        elif a is None or a.account_kind != "DEMO":
            self.execution_enabled, self.disabled_reason = False, "no DEMO authorization configured"
        elif info.trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO:
            self.execution_enabled, self.disabled_reason = False, f"account trade_mode {info.trade_mode} is not DEMO"
        elif info.login not in a.allowed_logins:
            self.execution_enabled, self.disabled_reason = False, f"login {info.login} not in allow-list"
        elif info.server not in a.allowed_servers:
            self.execution_enabled, self.disabled_reason = False, f"server {info.server} not in allow-list"
        else:
            self.execution_enabled, self.disabled_reason = True, "authorized demo account"

    def _require_execution(self) -> None:
        self._evaluate_guard()  # re-check every time: the terminal may have been re-logged into another account
        if not self.execution_enabled:
            raise BrokerError(f"EXECUTION DISABLED: {self.disabled_reason}")

    # ------------------------------------------------------------------ time
    def _server_offset(self) -> int:
        probe = next(iter(self.symbol_map.values()), None) or next(iter(self.asset_classes), None)
        tick = self.mt5.symbol_info_tick(self.symbol_map.get(probe, probe)) if probe else None
        if tick is None:
            raise BrokerUncertain("cannot read server time")
        raw = int(tick.time) - int(self._wall.time())
        off = round(raw / 3600) * 3600
        # A last tick can be old (quiet market); only accept the offset if it is near a whole hour
        if abs(raw - off) > 120:
            if self._offset is None:
                raise BrokerUncertain(f"server time offset {raw}s is not a whole number of hours (stale tick or clock drift)")
            return self._offset
        if self._offset is not None and off != self._offset:
            prev = self._offset
            self._offset = off
            raise BrokerUncertain(f"server UTC offset changed {prev} -> {off} (DST switch or clock problem); re-reconcile")
        self._offset = off
        return off

    def to_utc(self, server_ts: int) -> int:
        return int(server_ts) - self._server_offset()

    def server_time(self) -> int:
        tick = self.mt5.symbol_info_tick(self._sym(next(iter(self.asset_classes))))
        if tick is None:
            raise BrokerUncertain("broker unreachable")
        return self.to_utc(tick.time)

    # ------------------------------------------------------------------ metadata / quotes
    def _sym(self, canonical: str) -> str:
        return self.symbol_map.get(canonical, canonical)

    def spec(self, instrument: str) -> InstrumentSpec:
        s = self.mt5.symbol_info(self._sym(instrument))
        if s is None:
            raise BrokerError(f"unknown symbol {instrument}")
        if s.trade_tick_size <= 0 or s.trade_tick_value <= 0 or s.volume_step <= 0:
            raise BrokerError(f"invalid broker spec for {instrument}")
        currencies = tuple(dict.fromkeys(c for c in (s.currency_base, s.currency_profit) if c))
        return InstrumentSpec(instrument, self.asset_classes.get(instrument, "unknown"), currencies, float(s.trade_tick_size),
                              float(s.trade_tick_value), float(s.volume_min), float(s.volume_max), float(s.volume_step),
                              float(s.trade_contract_size))

    def quote(self, instrument: str) -> Quote:
        t = self.mt5.symbol_info_tick(self._sym(instrument))
        if t is None:
            raise BrokerUncertain(f"no tick for {instrument}")
        return Quote(instrument, float(t.bid), float(t.ask), self.to_utc(t.time))

    # ------------------------------------------------------------------ account truth
    def _canonical(self, broker_sym: str) -> str:
        for k, v in self.symbol_map.items():
            if v == broker_sym:
                return k
        return broker_sym

    def account(self) -> AccountSnapshot:
        info = self.mt5.account_info()
        pos = self.mt5.positions_get()
        if info is None or pos is None:
            raise BrokerUncertain(f"account/positions unavailable: {self.mt5.last_error()}")
        out = []
        for p in pos:
            d = Direction.LONG if p.type == self.mt5.POSITION_TYPE_BUY else Direction.SHORT
            cid = p.comment if str(p.comment).startswith(COMMENT_PREFIX) and p.magic == (self.auth.magic if self.auth else None) else None
            out.append(Position(str(p.ticket), self._canonical(p.symbol), d, float(p.volume), float(p.price_open),
                                float(p.sl) or None, float(p.tp) or None, self.to_utc(p.time), cid, float(p.profit), float(p.swap), 0.0))
        now = int(self._wall.time())
        return AccountSnapshot(now, now, float(info.balance), float(info.equity), float(info.margin), float(info.margin_free),
                               info.currency, tuple(out), ()).with_hash()

    def deals_since(self, ts: int) -> list[dict]:
        off = self._server_offset()
        frm = datetime.fromtimestamp(ts + off, UTC).replace(tzinfo=None)
        to = datetime.fromtimestamp(int(self._wall.time()) + off + 86400, UTC).replace(tzinfo=None)
        deals = self.mt5.history_deals_get(frm, to)
        if deals is None:
            raise BrokerUncertain(f"history unavailable: {self.mt5.last_error()}")
        out = []
        for d in deals:
            if d.entry == self.mt5.DEAL_ENTRY_IN:
                kind = "IN"
            elif d.entry in (self.mt5.DEAL_ENTRY_OUT, self.mt5.DEAL_ENTRY_OUT_BY):
                kind = "OUT:" + ("SL" if d.reason == self.mt5.DEAL_REASON_SL else "TP" if d.reason == self.mt5.DEAL_REASON_TP else "CLOSE")
            else:
                continue  # balance operations etc. are not trades
            out.append({"deal_id": str(d.ticket), "ticket": str(d.position_id), "client_id": d.comment if d.magic == (self.auth.magic if self.auth else None) else None,
                        "instrument": self._canonical(d.symbol), "kind": kind, "direction": "LONG" if d.type == self.mt5.DEAL_TYPE_BUY else "SHORT",
                        "volume": float(d.volume), "price": float(d.price), "profit": float(d.profit),
                        "commission": -float(d.commission) - float(getattr(d, "fee", 0.0)), "swap": float(d.swap),
                        "ts": self.to_utc(d.time)})
        return out

    def find_by_client_id(self, client_id: str, since_ts: int | None = None) -> list[dict]:
        """Look an intent up in deal history by (magic, comment). Ambiguity -> UNCERTAIN.

        ``since_ts`` = when the intent was persisted: history is searched from one day before it, so an
        intent is never declared NOT_EXECUTED merely because its deals fell outside a fixed window."""
        comment = intent_comment(client_id)
        self.calls.append(f"find:{client_id}")
        start = since_ts - 86400 if since_ts is not None else int(self._wall.time()) - 7 * 86400
        hits = [d for d in self.deals_since(start) if d["client_id"] == comment]
        for d in hits:
            d["client_id"] = client_id
        live = [p for p in (self.mt5.positions_get() or []) if p.comment == comment]
        if live and not [d for d in hits if d["kind"] == "IN"]:
            raise BrokerUncertain("position exists but its opening deal is not yet in history (history lag)")
        return hits

    # ------------------------------------------------------------------ orders
    def _filling(self, s) -> int:
        m = self.mt5
        if s.filling_mode & m.SYMBOL_FILLING_FOK:
            return m.ORDER_FILLING_FOK
        if s.filling_mode & m.SYMBOL_FILLING_IOC:
            return m.ORDER_FILLING_IOC
        return m.ORDER_FILLING_RETURN

    def _classify(self, res) -> str:
        m = self.mt5
        if res is None:
            return "UNCERTAIN"
        if res.retcode == m.TRADE_RETCODE_DONE:
            return "FILLED"
        if res.retcode == m.TRADE_RETCODE_DONE_PARTIAL:
            return "PARTIAL"
        if res.retcode in (m.TRADE_RETCODE_TIMEOUT, m.TRADE_RETCODE_CONNECTION):
            return "UNCERTAIN"
        return "REJECTED"

    def send_order(self, client_id, instrument, direction, volume, stop_loss, take_profit, max_slippage_points, fencing_token):
        self._require_execution()
        m = self.mt5
        sym = self._sym(instrument)
        s = m.symbol_info(sym)
        t = m.symbol_info_tick(sym)
        if s is None or t is None:
            raise BrokerError(f"symbol/tick unavailable for {instrument}")
        if s.trade_mode != m.SYMBOL_TRADE_MODE_FULL:
            raise BrokerError(f"symbol {instrument} not fully tradable (trade_mode={s.trade_mode})")
        req = {
            "action": m.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(volume),
            "type": m.ORDER_TYPE_BUY if direction is Direction.LONG else m.ORDER_TYPE_SELL,
            "price": t.ask if direction is Direction.LONG else t.bid, "sl": float(stop_loss), "tp": float(take_profit or 0.0),
            "deviation": int(max_slippage_points), "magic": self.auth.magic, "comment": intent_comment(client_id),
            "type_time": m.ORDER_TIME_GTC, "type_filling": self._filling(s),
        }
        self.calls.append(f"send:{client_id}")
        try:
            res = m.order_send(req)
        except Exception as e:  # noqa: BLE001 - transport failure: outcome unknown
            raise BrokerUncertain(f"order_send raised {e!r}") from e
        kind = self._classify(res)
        if kind == "UNCERTAIN":
            raise BrokerUncertain(f"order outcome unknown: {getattr(res, 'retcode', None)} {m.last_error()}")
        if kind == "REJECTED":
            raise BrokerError(f"REJECTED retcode={res.retcode} {getattr(res, 'comment', '')}")
        deals = m.history_deals_get(ticket=res.deal) if res.deal else None
        if not deals:
            raise BrokerUncertain("filled but deal not visible yet; reconcile")
        return {"status": kind, "ticket": str(deals[0].position_id), "filled_volume": float(res.volume),
                "requested_volume": float(volume), "price": float(res.price), "client_id": client_id}

    def close_position(self, ticket, client_id, fencing_token):
        self._require_execution()
        m = self.mt5
        ps = m.positions_get(ticket=int(ticket))
        if not ps:
            raise BrokerError("POSITION_NOT_FOUND")
        p = ps[0]
        t = m.symbol_info_tick(p.symbol)
        s = m.symbol_info(p.symbol)
        req = {"action": m.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "position": p.ticket,
               "type": m.ORDER_TYPE_SELL if p.type == m.POSITION_TYPE_BUY else m.ORDER_TYPE_BUY,
               "price": t.bid if p.type == m.POSITION_TYPE_BUY else t.ask, "deviation": 20, "magic": self.auth.magic,
               "comment": intent_comment(client_id), "type_time": m.ORDER_TIME_GTC, "type_filling": self._filling(s)}
        self.calls.append(f"close:{ticket}")
        try:
            res = m.order_send(req)
        except Exception as e:  # noqa: BLE001
            raise BrokerUncertain(f"close raised {e!r}") from e
        kind = self._classify(res)
        if kind == "UNCERTAIN":
            raise BrokerUncertain("close outcome unknown")
        if kind == "REJECTED":
            raise BrokerError(f"CLOSE_REJECTED retcode={res.retcode}")
        return {"status": "CLOSED", "ticket": str(ticket), "price": float(res.price)}

    # ------------------------------------------------------------------ data (research)
    def history_bars(self, instrument: str, timeframe_const, frm_utc: int, to_utc: int):
        off = self._server_offset()
        rates = self.mt5.copy_rates_range(self._sym(instrument), timeframe_const,
                                          datetime.fromtimestamp(frm_utc + off, UTC).replace(tzinfo=None),
                                          datetime.fromtimestamp(to_utc + off, UTC).replace(tzinfo=None))
        if rates is None:
            raise BrokerUncertain(f"copy_rates_range failed: {self.mt5.last_error()}")
        return [{"ts": int(r["time"]) - off, "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                 "close": float(r["close"]), "tick_volume": int(r["tick_volume"]), "spread_points": int(r["spread"])} for r in rates]

    def history_ticks(self, instrument: str, frm_utc: int, to_utc: int):
        off = self._server_offset()
        ticks = self.mt5.copy_ticks_range(self._sym(instrument), datetime.fromtimestamp(frm_utc + off, UTC).replace(tzinfo=None),
                                          datetime.fromtimestamp(to_utc + off, UTC).replace(tzinfo=None), self.mt5.COPY_TICKS_ALL)
        if ticks is None:
            raise BrokerUncertain(f"copy_ticks_range failed: {self.mt5.last_error()}")
        return [{"ts_ms": int(t["time_msc"]) - off * 1000, "bid": float(t["bid"]), "ask": float(t["ask"])} for t in ticks]


_ = timedelta  # kept for adapters that window history by days
