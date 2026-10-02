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
    def __init__(self, path: str, owner: str, ttl: float = 10.0, guard: float = 2.0, clock=None):
        self.owner = owner
        self.ttl = ttl
        self.guard = guard
        self._clock = clock
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK (id=1), owner TEXT, expires REAL, token INTEGER)"
        )
        self._db.execute("INSERT OR IGNORE INTO lease VALUES (1, NULL, 0, 0)")
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

    def release(self) -> None:
        if self.token is None:
            return
        self._db.execute("UPDATE lease SET expires=0 WHERE id=1 AND owner=? AND token=?", (self.owner, self.token))
        self.token = None
