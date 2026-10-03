"""Simulated broker with durable state and fault injection.

The simulated broker lives in its OWN SQLite file so that it survives kills of
the trading process, the way a real broker would. It deliberately does NOT
deduplicate by client id: exactly-once is the trading system's job, so the sim
must not hide duplicates.

Faults are queued in the ``faults`` table and consumed one per matching call, so
a test can arm a fault in one process and have it fire in another.

The one non-realistic feature is the fencing check (rejects orders carrying a
lower fencing token than one already seen). Real MT5 cannot do this; see the
qualification report. It is switchable so tests can show what happens without it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

from sentinel.broker.base import BrokerError, BrokerUncertain
from sentinel.contracts import AccountSnapshot, Direction, InstrumentSpec, Position, Quote

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes (instrument TEXT PRIMARY KEY, bid REAL, ask REAL, ts INTEGER);
CREATE TABLE IF NOT EXISTS positions (
    ticket TEXT PRIMARY KEY, instrument TEXT, direction TEXT, volume REAL, open_price REAL,
    sl REAL, tp REAL, open_time INTEGER, client_id TEXT, commission REAL);
CREATE TABLE IF NOT EXISTS deals (
    deal_id TEXT PRIMARY KEY, ticket TEXT, client_id TEXT, instrument TEXT, kind TEXT,
    direction TEXT, volume REAL, price REAL, profit REAL, commission REAL, ts INTEGER);
CREATE TABLE IF NOT EXISTS faults (id INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT, kind TEXT, arg TEXT);
"""

DEFAULT_SPECS = {
    "XAUUSD": InstrumentSpec("XAUUSD", "metal", ("XAU", "USD"), 0.01, 1.0, 0.01, 50.0, 0.01, 100.0),
    "USOIL": InstrumentSpec("USOIL", "energy", ("OIL", "USD"), 0.01, 10.0, 0.01, 50.0, 0.01, 1000.0),
    "BTCUSD": InstrumentSpec("BTCUSD", "crypto", ("BTC", "USD"), 0.01, 0.01, 0.01, 10.0, 0.01, 1.0),
    # USDJPY: tick value in USD depends on the rate; sim keeps it fixed at ~150.
    "USDJPY": InstrumentSpec("USDJPY", "forex", ("USD", "JPY"), 0.001, 0.6667, 0.01, 50.0, 0.01, 100000.0),
}


class SimBroker:
    def __init__(
        self,
        path: str,
        initial_balance: float = 100_000.0,
        specs: dict | None = None,
        commission_per_lot: float = 5.0,
        enforce_fencing: bool = True,
        clock=None,
        slippage: dict | None = None,
    ):
        self.slippage = slippage or {}
        self.path = path
        self.specs = specs or DEFAULT_SPECS
        self.commission_per_lot = commission_per_lot
        self.enforce_fencing = enforce_fencing
        self._clock = clock
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)
        if self._get("balance") is None:
            self._set("balance", initial_balance)
            self._set("max_fence", 0)
            self._set("connected", True)
        self.calls: list[str] = []

    # ---- control surface used by tests / simulator -------------------------------------------
    def _now(self) -> int:
        return int(self._clock.now()) if self._clock else int(time.time())

    def _get(self, k):
        r = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None

    def _set(self, k, v) -> None:
        self._db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def set_quote(self, instrument: str, bid: float, ask: float, ts: int | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO quotes VALUES(?,?,?,?) ON CONFLICT(instrument) DO UPDATE SET bid=excluded.bid, ask=excluded.ask, ts=excluded.ts",
                (instrument, bid, ask, ts if ts is not None else self._now()),
            )
            self._process_stops(instrument)

    def arm_fault(self, op: str, kind: str, arg: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT INTO faults(op,kind,arg) VALUES(?,?,?)", (op, kind, arg))

    def set_connected(self, flag: bool) -> None:
        with self._lock:
            self._set("connected", flag)

    def _take_fault(self, op: str):
        r = self._db.execute("SELECT id,kind,arg FROM faults WHERE op=? ORDER BY id LIMIT 1", (op,)).fetchone()
        if not r:
            return None, None
        self._db.execute("DELETE FROM faults WHERE id=?", (r[0],))
        return r[1], r[2]

    def _check_conn(self) -> None:
        if not self._get("connected"):
            raise BrokerUncertain("broker disconnected")

    # ---- broker API -------------------------------------------------------------------------
    def server_time(self) -> int:
        self._check_conn()
        return self._now()

    def spec(self, instrument: str) -> InstrumentSpec:
        if instrument not in self.specs:
            raise BrokerError(f"unknown symbol {instrument}")
        return self.specs[instrument]

    def quote(self, instrument: str) -> Quote:
        self._check_conn()
        r = self._db.execute("SELECT bid,ask,ts FROM quotes WHERE instrument=?", (instrument,)).fetchone()
        if not r:
            raise BrokerError(f"no quote for {instrument}")
        return Quote(instrument, r[0], r[1], r[2])

    def _pnl(self, instrument: str, direction: str, volume: float, open_price: float, price: float) -> float:
        s = self.spec(instrument)
        sign = 1 if direction == "LONG" else -1
        return (price - open_price) * sign / s.tick_size * s.tick_value * volume

    def account(self) -> AccountSnapshot:
        with self._lock:
            self._check_conn()
            self.calls.append("account")
            balance = float(self._get("balance"))
            positions = []
            unreal = 0.0
            margin = 0.0
            for t, ins, d, vol, op, sl, tp, ot, cid, com in self._db.execute(
                "SELECT ticket,instrument,direction,volume,open_price,sl,tp,open_time,client_id,commission FROM positions ORDER BY ticket"
            ):
                q = self._db.execute("SELECT bid,ask FROM quotes WHERE instrument=?", (ins,)).fetchone()
                px = (q[0] if d == "LONG" else q[1]) if q else op
                pnl = self._pnl(ins, d, vol, op, px)
                unreal += pnl
                margin += abs(vol * self.spec(ins).contract_size * op) / 30.0  # 1:30 leverage model
                positions.append(Position(t, ins, Direction(d), vol, op, sl, tp, ot, cid, round(pnl, 2), 0.0, com))
            equity = balance + unreal
            now = self._now()
            return AccountSnapshot(
                now, now, round(balance, 2), round(equity, 2), round(margin, 2), round(equity - margin, 2), "USD",
                tuple(positions), (),
            ).with_hash()

    def send_order(self, client_id, instrument, direction, volume, stop_loss, take_profit, max_slippage_points, fencing_token):
        with self._lock:
            self.calls.append(f"send:{client_id}")
            self._check_conn()
            if self.enforce_fencing:
                mx = int(self._get("max_fence") or 0)
                if fencing_token < mx:
                    raise BrokerError(f"STALE_FENCE {fencing_token} < {mx}")
                self._set("max_fence", fencing_token)
            kind, arg = self._take_fault("send")
            if kind == "timeout_before_fill":
                raise BrokerUncertain("timeout (order never reached broker)")
            if kind == "disconnect":
                self._set("connected", False)
                raise BrokerUncertain("connection dropped")
            if kind == "reject":
                raise BrokerError(arg or "REJECTED: market closed")
            if kind == "requote":
                raise BrokerError("REQUOTE")
            spec = self.spec(instrument)
            if volume < spec.volume_min or volume > spec.volume_max:
                raise BrokerError("INVALID_VOLUME")
            q = self._db.execute("SELECT bid,ask FROM quotes WHERE instrument=?", (instrument,)).fetchone()
            if not q:
                raise BrokerError("NO_PRICES")
            price = q[1] if direction is Direction.LONG else q[0]
            price += direction.sign * self.slippage.get(instrument, 0.0)
            filled = volume
            if kind == "partial":
                filled = max(spec.volume_min, round(volume * float(arg or 0.5) / spec.volume_step) * spec.volume_step)
                filled = round(filled, 8)
            ticket = "T" + uuid.uuid4().hex[:12]
            com = self.commission_per_lot * filled
            now = self._now()
            self._db.execute(
                "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ticket, instrument, direction.value, filled, price, stop_loss, take_profit, now, client_id, com),
            )
            self._db.execute(
                "INSERT INTO deals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("D" + uuid.uuid4().hex[:12], ticket, client_id, instrument, "IN", direction.value, filled, price, 0.0, com, now),
            )
            self._set("balance", float(self._get("balance")) - com)
            ack = {"status": "FILLED" if filled == volume else "PARTIAL", "ticket": ticket, "filled_volume": filled,
                   "requested_volume": volume, "price": price, "client_id": client_id}
            if kind == "timeout_after_fill":
                raise BrokerUncertain("timeout after broker executed the order")
            if kind == "duplicate_ack":
                ack["_duplicate"] = True
            return ack

    def close_position(self, ticket, client_id, fencing_token):
        with self._lock:
            self.calls.append(f"close:{ticket}")
            self._check_conn()
            if self.enforce_fencing:
                mx = int(self._get("max_fence") or 0)
                if fencing_token < mx:
                    raise BrokerError(f"STALE_FENCE {fencing_token} < {mx}")
                self._set("max_fence", fencing_token)
            kind, _ = self._take_fault("close")
            if kind == "reject":
                raise BrokerError("CLOSE_REJECTED")
            r = self._db.execute("SELECT instrument,direction FROM positions WHERE ticket=?", (ticket,)).fetchone()
            if not r:
                raise BrokerError("POSITION_NOT_FOUND")
            q = self._db.execute("SELECT bid,ask FROM quotes WHERE instrument=?", (r[0],)).fetchone()
            price = q[0] if r[1] == "LONG" else q[1]
            price -= (1 if r[1] == "LONG" else -1) * self.slippage.get(r[0], 0.0)
            result = self._close(ticket, price, client_id, "CLOSE")
            if kind == "timeout_after_fill":
                raise BrokerUncertain("timeout after close executed")
            return result

    def _close(self, ticket: str, price: float, client_id: str | None, reason: str) -> dict:
        ins, d, vol, op, cid = self._db.execute(
            "SELECT instrument,direction,volume,open_price,client_id FROM positions WHERE ticket=?", (ticket,)
        ).fetchone()
        profit = round(self._pnl(ins, d, vol, op, price), 2)
        com = self.commission_per_lot * vol
        now = self._now()
        self._db.execute("DELETE FROM positions WHERE ticket=?", (ticket,))
        self._db.execute(
            "INSERT INTO deals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("D" + uuid.uuid4().hex[:12], ticket, client_id or cid, ins, "OUT:" + reason, d, vol, price, profit, com, now),
        )
        self._set("balance", float(self._get("balance")) + profit - com)
        return {"status": "CLOSED", "ticket": ticket, "price": price, "profit": profit}

    def _process_stops(self, instrument: str) -> None:
        q = self._db.execute("SELECT bid,ask FROM quotes WHERE instrument=?", (instrument,)).fetchone()
        if not q:
            return
        bid, ask = q
        rows = self._db.execute(
            "SELECT ticket,direction,sl,tp FROM positions WHERE instrument=?", (instrument,)
        ).fetchall()
        for t, d, sl, tp in rows:
            px = bid if d == "LONG" else ask
            if sl is not None and ((d == "LONG" and px <= sl) or (d == "SHORT" and px >= sl)):
                slip = self.slippage.get(instrument, 0.0) * (1 if d == "LONG" else -1)
                self._close(t, px - slip, None, "SL")  # stop fills at market (gaps slip through) plus slippage
            elif tp is not None and ((d == "LONG" and px >= tp) or (d == "SHORT" and px <= tp)):
                self._close(t, tp, None, "TP")

    def find_by_client_id(self, client_id: str, since_ts: int | None = None) -> list[dict]:  # full history kept
        with self._lock:
            self._check_conn()
            self.calls.append(f"find:{client_id}")
            rows = self._db.execute(
                "SELECT deal_id,ticket,kind,volume,price,ts FROM deals WHERE client_id=? ORDER BY ts", (client_id,)
            ).fetchall()
            return [dict(zip(("deal_id", "ticket", "kind", "volume", "price", "ts"), r)) for r in rows]

    def deals_since(self, ts: int) -> list[dict]:
        with self._lock:
            self._check_conn()
            rows = self._db.execute(
                "SELECT deal_id,ticket,client_id,instrument,kind,direction,volume,price,profit,commission,ts FROM deals WHERE ts>=? ORDER BY ts",
                (ts,),
            ).fetchall()
            keys = ("deal_id", "ticket", "client_id", "instrument", "kind", "direction", "volume", "price", "profit", "commission", "ts")
            return [dict(zip(keys, r)) for r in rows]

    def open_position_count(self, client_id: str | None = None) -> int:
        if client_id:
            return self._db.execute("SELECT COUNT(*) FROM positions WHERE client_id=?", (client_id,)).fetchone()[0]
        return self._db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

    def inject_foreign_position(self, instrument: str, direction: Direction, volume: float, price: float) -> str:
        """A position the trading system did not open (manual trade, other EA)."""
        ticket = "M" + uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ticket, instrument, direction.value, volume, price, None, None, self._now(), None, 0.0),
        )
        return ticket
