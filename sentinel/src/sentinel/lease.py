"""Single-execution-authority lease with fencing tokens.

A lease row in a shared SQLite coordination DB names the current leader, its
expiry and a monotonically increasing fencing token. Acquisition and renewal are
compare-and-set inside ``BEGIN IMMEDIATE``. Every broker call carries the token;
a stale leader is rejected by the fence even if it still believes it is leader.

Clock disagreement: expiry is judged with the *coordination DB's* writer clock
plus a guard band, and a leader refuses to act if it is within ``guard`` seconds
of its own expiry. A paused process that wakes after expiry gets a stale token.
"""

from __future__ import annotations

import os
import sqlite3
import time


class LeaseLost(RuntimeError):
    pass


class Lease:
    def __init__(self, path: str, owner: str, ttl: float = 10.0, guard: float = 2.0, clock=None, busy_timeout: float = 30.0):
        self.owner = owner
        self.ttl = ttl
        self.guard = guard
        self._clock = clock
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout)
        # Two instances creating the store at the same instant: switching to WAL is not covered by the busy
        # handler and fails immediately with "database is locked". Retry setup within busy_timeout, then fail.
        deadline = time.monotonic() + busy_timeout
        while True:
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=FULL")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK (id=1), owner TEXT, expires REAL, token INTEGER)"
                )
                self._db.execute("INSERT OR IGNORE INTO lease VALUES (1, NULL, 0, 0)")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS sends (intent_id TEXT PRIMARY KEY, owner TEXT, token INTEGER, ts REAL)"
                )
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        self.token: int | None = None
        self.expires: float = 0.0

    def _now(self) -> float:
        return self._clock.now() if self._clock else time.time()

    def acquire(self) -> bool:
        now = self._now()
        cur = self._db.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            owner, expires, token = cur.execute("SELECT owner, expires, token FROM lease WHERE id=1").fetchone()
            if owner == self.owner and expires > now and self.token == token:
                new_exp = now + self.ttl
                cur.execute("UPDATE lease SET expires=? WHERE id=1", (new_exp,))
                cur.execute("COMMIT")
                self.expires = new_exp
                return True
            if owner is not None and expires > now:
                cur.execute("ROLLBACK")
                self.token = None
                return False
            new_token = token + 1
            new_exp = now + self.ttl
            cur.execute("UPDATE lease SET owner=?, expires=?, token=? WHERE id=1", (self.owner, new_exp, new_token))
            cur.execute("COMMIT")
            self.token, self.expires = new_token, new_exp
            return True
        except BaseException:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def assert_leader(self) -> int:
        """Return a usable fencing token or raise. Never act near expiry."""
        if self.token is None:
            raise LeaseLost("not leader")
        if self._now() > self.expires - self.guard:
            if not self.acquire():
                raise LeaseLost("lease expired and could not be renewed")
        owner, token = self._db.execute("SELECT owner, token FROM lease WHERE id=1").fetchone()
        if owner != self.owner or token != self.token:
            self.token = None
            raise LeaseLost("lease taken by another instance")
        return self.token

    def record_send(self, intent_id: str) -> int:
        """Fenced send ledger: atomically verify we STILL hold the current token and record the send.

        MT5 cannot reject a stale writer, so this compare-and-insert in the shared coordination store is
        the fence. A paused leader that wakes after losing the lease fails here and never reaches MT5.
        A leader paused AFTER this commit can still send late; the new leader's reconciliation
        sees this ledger row (another owner's unresolved send) and halts for operator review.
        """
        now = self._now()
        cur = self._db.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            owner, expires, token = cur.execute("SELECT owner, expires, token FROM lease WHERE id=1").fetchone()
            if owner != self.owner or token != self.token or expires <= now + self.guard:
                cur.execute("ROLLBACK")
                mine, self.token = self.token, None
                raise LeaseLost(f"fenced send refused (owner={owner}, token={token}, mine={mine})")
            cur.execute("INSERT INTO sends VALUES (?,?,?,?)", (intent_id, self.owner, token, now))
            cur.execute("COMMIT")
            return token
        except LeaseLost:
            raise
        except BaseException:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def sends_by_others(self, since: float = 0.0) -> list[tuple]:
        return self._db.execute("SELECT intent_id, owner, token, ts FROM sends WHERE owner != ? AND ts >= ?",
                                (self.owner, since)).fetchall()

    def release(self) -> None:
        if self.token is None:
            return
        self._db.execute("UPDATE lease SET expires=0 WHERE id=1 AND owner=? AND token=?", (self.owner, self.token))
        self.token = None
