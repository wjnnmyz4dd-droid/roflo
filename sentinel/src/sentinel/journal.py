"""Immutable, hash-chained event journal (SQLite).

* Append-only: UPDATE and DELETE are rejected by triggers.
* Every row stores the hash of the previous row; ``verify()`` recomputes the chain.
* ``synchronous=FULL`` + WAL so an acknowledged append survives a process kill.
* Unique keys (permit ids, intent ids) are enforced by side tables written in the
  SAME transaction as the event, which is how exactly-once guarantees are made.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

from sentinel.contracts import canonical_json

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    type TEXT NOT NULL,
    component TEXT NOT NULL,
    correlation_id TEXT,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type);
CREATE INDEX IF NOT EXISTS ix_events_corr ON events(correlation_id);
CREATE TABLE IF NOT EXISTS unique_keys (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS keys_no_update BEFORE UPDATE ON unique_keys
BEGIN SELECT RAISE(ABORT, 'unique keys are append-only'); END;
CREATE TRIGGER IF NOT EXISTS keys_no_delete BEFORE DELETE ON unique_keys
BEGIN SELECT RAISE(ABORT, 'unique keys are append-only'); END;
"""


class JournalError(RuntimeError):
    pass


class DuplicateKey(JournalError):
    pass


@dataclass(frozen=True)
class Event:
    seq: int
    ts: int
    type: str
    component: str
    correlation_id: str | None
    payload: dict
    prev_hash: str
    hash: str


def _row_hash(prev_hash: str, ts: int, type_: str, component: str, corr: str | None, payload: str) -> str:
    h = hashlib.sha256()
    for part in (prev_hash, str(ts), type_, component, corr or "", payload):
        h.update(part.encode())
        h.update(b"\x1f")
    return h.hexdigest()


class Journal:
    def __init__(self, path: str, clock=None):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _now(self) -> int:
        return int(self._clock.now()) if self._clock else int(time.time())

    def append(
        self,
        type_: str,
        component: str,
        payload: Any,
        correlation_id: str | None = None,
        unique: list[tuple[str, str]] | None = None,
    ) -> Event:
        body = canonical_json(payload)
        with self._lock:
            cur = self._db.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                row = cur.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                prev = row[0] if row else GENESIS
                ts = self._now()
                h = _row_hash(prev, ts, type_, component, correlation_id, body)
                cur.execute(
                    "INSERT INTO events(ts,type,component,correlation_id,payload,prev_hash,hash) VALUES (?,?,?,?,?,?,?)",
                    (ts, type_, component, correlation_id, body, prev, h),
                )
                seq = cur.lastrowid
                for ns, key in unique or []:
                    try:
                        cur.execute("INSERT INTO unique_keys(namespace,key,event_seq) VALUES (?,?,?)", (ns, key, seq))
                    except sqlite3.IntegrityError as e:
                        raise DuplicateKey(f"{ns}:{key} already recorded") from e
                cur.execute("COMMIT")
            except BaseException:
                try:
                    cur.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        return Event(seq, ts, type_, component, correlation_id, json.loads(body), prev, h)

    def has_key(self, namespace: str, key: str) -> bool:
        with self._lock:
            return (
                self._db.execute("SELECT 1 FROM unique_keys WHERE namespace=? AND key=?", (namespace, key)).fetchone()
                is not None
            )

    def events(self, type_: str | None = None, correlation_id: str | None = None, since_seq: int = 0) -> Iterator[Event]:
        q = "SELECT seq,ts,type,component,correlation_id,payload,prev_hash,hash FROM events WHERE seq>?"
        args: list = [since_seq]
        if type_:
            q += " AND type=?"
            args.append(type_)
        if correlation_id:
            q += " AND correlation_id=?"
            args.append(correlation_id)
        q += " ORDER BY seq"
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        for r in rows:
            yield Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7])

    def events_of_types(self, types: tuple) -> Iterator[Event]:
        q = ("SELECT seq,ts,type,component,correlation_id,payload,prev_hash,hash FROM events WHERE type IN (%s) ORDER BY seq"
             % ",".join("?" * len(types)))
        with self._lock:
            rows = self._db.execute(q, list(types)).fetchall()
        for r in rows:
            yield Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7])

    def last(self, type_: str, correlation_id: str | None = None) -> Event | None:
        q = "SELECT seq,ts,type,component,correlation_id,payload,prev_hash,hash FROM events WHERE type=?"
        args: list = [type_]
        if correlation_id:
            q += " AND correlation_id=?"
            args.append(correlation_id)
        q += " ORDER BY seq DESC LIMIT 1"
        with self._lock:
            r = self._db.execute(q, args).fetchone()
        return Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7]) if r else None

    def get(self, seq: int) -> Event | None:
        with self._lock:
            r = self._db.execute(
                "SELECT seq,ts,type,component,correlation_id,payload,prev_hash,hash FROM events WHERE seq=?", (seq,)
            ).fetchone()
        return Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7]) if r else None

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        n = 0
        with self._lock:
            rows = self._db.execute(
                "SELECT seq,ts,type,component,correlation_id,payload,prev_hash,hash FROM events ORDER BY seq"
            ).fetchall()
        for seq, ts, t, comp, corr, payload, prev_hash, h in rows:
            if prev_hash != prev:
                return False, f"chain break at seq {seq}"
            if _row_hash(prev_hash, ts, t, comp, corr, payload) != h:
                return False, f"hash mismatch at seq {seq}"
            prev = h
            n += 1
        return True, f"{n} events verified"
