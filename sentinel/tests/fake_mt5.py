"""SIMULATED MetaTrader5 module for adapter contract tests.

Models documented MT5 behaviours that matter for safety:
* timestamps are broker-server local time encoded as epoch seconds (offset_hours ahead of UTC)
* account_info().trade_mode distinguishes DEMO / CONTEST / REAL
* order_send can return None (transport failure) or retcodes; a timeout may still have executed
* deal history can lag behind positions (history_lag calls)
* brokers can truncate or rewrite order comments
Numeric constant values here are arbitrary; the adapter must only use them by name.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from types import SimpleNamespace as NS

ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 100, 101, 102
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 200, 201
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 300, 301
TRADE_ACTION_DEAL = 400
ORDER_TIME_GTC = 500
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 600, 601, 602
SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC = 1, 2
SYMBOL_TRADE_MODE_FULL, SYMBOL_TRADE_MODE_CLOSEONLY = 700, 701
DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY = 800, 801, 802, 803
DEAL_TYPE_BUY, DEAL_TYPE_SELL = 900, 901
DEAL_REASON_CLIENT, DEAL_REASON_SL, DEAL_REASON_TP = 1000, 1001, 1002
COPY_TICKS_ALL = 1100
TRADE_RETCODE_REQUOTE, TRADE_RETCODE_REJECT, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10004, 10006, 10009, 10010
TRADE_RETCODE_TIMEOUT, TRADE_RETCODE_INVALID_VOLUME, TRADE_RETCODE_MARKET_CLOSED = 10012, 10014, 10018
TRADE_RETCODE_NO_MONEY, TRADE_RETCODE_PRICE_CHANGED, TRADE_RETCODE_CONNECTION = 10019, 10020, 10031


class FakeTerminal:
    def __init__(self, wall, offset_hours=3, trade_mode=ACCOUNT_TRADE_MODE_DEMO, login=5550001, server="Broker-Demo"):
        self.wall = wall
        self.offset = offset_hours * 3600
        self.account = NS(login=login, server=server, trade_mode=trade_mode, balance=100_000.0, currency="USD")
        self.symbols = {
            "XAUUSD": NS(name="XAUUSD", trade_tick_size=0.01, trade_tick_value=1.0, volume_min=0.01, volume_max=50.0,
                         volume_step=0.01, trade_contract_size=100.0, currency_base="XAU", currency_profit="USD",
                         filling_mode=SYMBOL_FILLING_IOC, trade_mode=SYMBOL_TRADE_MODE_FULL),
        }
        self.ticks = {"XAUUSD": (2000.0, 2000.3)}
        self.positions: dict[int, NS] = {}
        self.deals: list[NS] = []
        self.faults: list[str] = []
        self.history_lag = 0
        self.history_unavailable = False  # history_deals_get returns None (terminal error)
        self.comment_rewrite = None  # e.g. lambda c: c[:16]
        self.order_send_calls = 0
        self._ids = itertools.count(1000)
        self.err = (1, "Success")

    # ------------------------------------------------------------ helpers
    def server_now(self) -> int:
        return int(self.wall.time()) + self.offset

    def _naive(self, dt) -> int:
        return int(dt.replace(tzinfo=UTC).timestamp())

    # ------------------------------------------------------------ module API
    def initialize(self, **kw):
        return True

    def last_error(self):
        return self.err

    def account_info(self):
        a = self.account
        floating = sum(self._pnl(p) for p in self.positions.values())
        return NS(login=a.login, server=a.server, trade_mode=a.trade_mode, balance=a.balance, equity=a.balance + floating,
                  margin=0.0, margin_free=a.balance + floating, currency=a.currency)

    def symbol_info(self, sym):
        return self.symbols.get(sym)

    def symbol_info_tick(self, sym):
        if sym not in self.ticks:
            return None
        bid, ask = self.ticks[sym]
        return NS(bid=bid, ask=ask, time=self.server_now())

    def _pnl(self, p):
        bid, ask = self.ticks[p.symbol]
        s = self.symbols[p.symbol]
        px = bid if p.type == POSITION_TYPE_BUY else ask
        sign = 1 if p.type == POSITION_TYPE_BUY else -1
        return (px - p.price_open) * sign / s.trade_tick_size * s.trade_tick_value * p.volume

    def positions_get(self, ticket=None):
        out = []
        for p in self.positions.values():
            if ticket is not None and p.ticket != ticket:
                continue
            out.append(NS(**{**p.__dict__, "profit": self._pnl(p)}))
        return tuple(out)

    def history_deals_get(self, *args, ticket=None, **kw):
        if self.history_unavailable:
            self.err = (-1, "history request failed")
            return None
        if self.history_lag > 0:
            self.history_lag -= 1
            return ()  # history not yet synchronised (documented terminal behaviour)
        if ticket is not None:
            return tuple(d for d in self.deals if d.ticket == ticket)
        frm, to = args
        lo, hi = self._naive(frm), self._naive(to)
        return tuple(d for d in self.deals if lo <= d.time <= hi)

    def order_send(self, req):
        self.order_send_calls += 1
        fault = self.faults.pop(0) if self.faults else None
        if fault == "none_result_no_fill":
            self.err = (-10005, "IPC timeout")
            return None
        if fault == "requote":
            return NS(retcode=TRADE_RETCODE_REQUOTE, deal=0, order=0, volume=0.0, price=0.0, comment="Requote")
        if fault == "no_money":
            return NS(retcode=TRADE_RETCODE_NO_MONEY, deal=0, order=0, volume=0.0, price=0.0, comment="No money")
        assert req["action"] == TRADE_ACTION_DEAL
        sym = req["symbol"]
        bid, ask = self.ticks[sym]
        comment = self.comment_rewrite(req["comment"]) if self.comment_rewrite else req["comment"]
        vol = req["volume"] * (0.5 if fault == "partial" else 1.0)
        vol = round(vol, 2)
        now = self.server_now()
        if "position" in req:  # close
            p = self.positions.pop(req["position"])
            px = bid if p.type == POSITION_TYPE_BUY else ask
            profit = self._pnl(NS(**{**p.__dict__}))
            self.account.balance += profit
            did = next(self._ids)
            self.deals.append(NS(ticket=did, position_id=p.ticket, symbol=sym, entry=DEAL_ENTRY_OUT, reason=DEAL_REASON_CLIENT,
                                 type=DEAL_TYPE_SELL if p.type == POSITION_TYPE_BUY else DEAL_TYPE_BUY, volume=p.volume, price=px,
                                 profit=profit, commission=-2.5 * p.volume, fee=0.0, swap=0.0, magic=req["magic"], comment=comment, time=now))
            return NS(retcode=TRADE_RETCODE_DONE, deal=did, order=next(self._ids), volume=p.volume, price=px, comment="done")
        buy = req["type"] == ORDER_TYPE_BUY
        px = ask if buy else bid
        pid = next(self._ids)
        self.positions[pid] = NS(ticket=pid, symbol=sym, type=POSITION_TYPE_BUY if buy else POSITION_TYPE_SELL, volume=vol,
                                 price_open=px, sl=req["sl"], tp=req["tp"], time=now, magic=req["magic"], comment=comment, swap=0.0)
        did = next(self._ids)
        self.deals.append(NS(ticket=did, position_id=pid, symbol=sym, entry=DEAL_ENTRY_IN, reason=DEAL_REASON_CLIENT,
                             type=DEAL_TYPE_BUY if buy else DEAL_TYPE_SELL, volume=vol, price=px, profit=0.0,
                             commission=-2.5 * vol, fee=0.0, swap=0.0, magic=req["magic"], comment=comment, time=now))
        self.account.balance -= 2.5 * vol
        if fault == "none_result_after_fill":
            self.err = (-10005, "IPC timeout")
            return None
        if fault == "timeout_retcode_after_fill":
            return NS(retcode=TRADE_RETCODE_TIMEOUT, deal=0, order=0, volume=0.0, price=0.0, comment="Timeout")
        return NS(retcode=TRADE_RETCODE_DONE_PARTIAL if fault == "partial" else TRADE_RETCODE_DONE, deal=did, order=pid,
                  volume=vol, price=px, comment="done")

    def copy_rates_range(self, sym, tf, frm, to):
        return []


def module(terminal: FakeTerminal):
    """Expose the terminal plus constants as a module-like namespace (like ``import MetaTrader5 as mt5``)."""
    ns = NS(**{k: v for k, v in globals().items() if k.isupper()})
    for name in ("initialize", "last_error", "account_info", "symbol_info", "symbol_info_tick", "positions_get",
                 "history_deals_get", "order_send", "copy_rates_range"):
        setattr(ns, name, getattr(terminal, name))
    return ns


_ = datetime
