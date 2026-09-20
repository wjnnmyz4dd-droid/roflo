"""Durable storage, with the one-authority law enforced by the database.

Two things here are enforcement rather than convention:

**Append-only tables.** ``audit_log``, ``verification_evidence``, ``ledger_entries``,
``governor_decisions`` and ``action_requests`` carry SQLite triggers that ABORT on
UPDATE and DELETE. The immutability holds even against code that bypasses this
module entirely, including a direct ``sqlite3`` connection.

**Table ownership.** Each authority opens a :class:`AuthorityConnection` naming the
tables it owns. A write to any other table raises :class:`AuthorityError`. This is
how "one responsibility, one authoritative owner" becomes testable instead of
aspirational.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from .errors import AuthorityError, ImmutableRecord

#: How long to wait for another writer before giving up. A 24/7 process has a
#: scheduler and a webhook path that can collide; failing instantly on a lock that
#: clears in milliseconds would turn a non-event into an incident.
BUSY_TIMEOUT_S = 10.0

#: ``(table, column, column-spec)`` added to databases created before the column
#: existed. Append to this list; never edit or remove an entry.
MIGRATIONS = (
    ("action_requests", "phase", "TEXT NOT NULL DEFAULT 'SETTLED'"),
    ("action_requests", "request_id", "TEXT NOT NULL DEFAULT ''"),
    ("requirements", "acceptance", "TEXT NOT NULL DEFAULT ''"),
    ("requirements", "check_name", "TEXT NOT NULL DEFAULT ''"),
    ("requirements", "params", "TEXT NOT NULL DEFAULT '{}'"),
    ("requirements", "criticality", "TEXT NOT NULL DEFAULT 'MANDATORY'"),
    ("requirements", "status", "TEXT NOT NULL DEFAULT 'DRAFT'"),
    ("requirements", "supersedes", "TEXT NOT NULL DEFAULT ''"),
    ("requirements", "committed_at", "TEXT"),
    ("verification_evidence", "requirement_id", "TEXT NOT NULL DEFAULT ''"),
    ("verification_evidence", "artifact_digest", "TEXT NOT NULL DEFAULT ''"),
    ("verification_evidence", "artifact_id", "TEXT NOT NULL DEFAULT ''"),
)

#: Tables whose rows may never change once written. ``artifacts`` and
#: ``client_feedback`` join the list: an artifact record that could be edited
#: would break the binding between what was checked and what was delivered, and
#: rewriting a client's words is never a correction.
APPEND_ONLY = (
    "audit_log", "verification_evidence", "ledger_entries",
    "governor_decisions", "action_requests", "price_estimates", "policy_versions",
    "qualification_verdicts", "payment_events", "artifacts",
    # A proposal and the owner's answer to it are history. Editing either would
    # let a denial become an approval with nothing to show for the change.
    "capability_proposals",
    # A verifier's trial record and the certification decision from it are
    # history. A verifier that failed must stay failed on the record even after
    # it is fixed and re-certified, or a revocation could be edited away.
    "verifier_certifications",
)

#: Which authority owns which tables. The single source of this mapping.
TABLE_OWNER = {
    "audit_log": "audit",
    "verification_evidence": "audit",
    "policy_versions": "policy",
    "policy_current": "policy",
    "pricing_reference": "policy",
    "ledger_entries": "ledger",
    "payments": "ledger",
    "payment_events": "ledger",
    "jobs": "orchestrator",
    "requirements": "orchestrator",
    "artifacts": "orchestrator",
    "client_feedback": "feedback",
    "capability_assessments": "capability",
    "capability_proposals": "capability",
    "verifier_certifications": "capability",
    "price_estimates": "governor",
    "calibration_state": "governor",
    "governor_decisions": "governor",
    "budget_grants": "governor",
    "action_requests": "gate",
    "memory_facts": "memory",
    "work_sources": "discovery",
    "opportunities": "discovery",
    "qualification_verdicts": "qualification",
    "owner_approvals": "owner",
}

_WRITE = re.compile(
    r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+"
    r"[\"'`\[]?(?P<table>\w+)", re.IGNORECASE,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, event TEXT NOT NULL, authority TEXT NOT NULL,
  initiator TEXT NOT NULL, job_id TEXT, why TEXT NOT NULL,
  input_ref TEXT, decision TEXT, permission TEXT, financial_authorization TEXT,
  external_effect TEXT, result TEXT, verification_ref TEXT,
  payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_evidence (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, subject_ref TEXT NOT NULL,
  tier TEXT NOT NULL, method TEXT NOT NULL,
  executor_identity TEXT NOT NULL, verifier_identity TEXT NOT NULL,
  verdict TEXT NOT NULL, raw_output TEXT NOT NULL, consequence TEXT NOT NULL,
  requirement_id TEXT NOT NULL DEFAULT '', artifact_digest TEXT NOT NULL DEFAULT '',
  artifact_id TEXT NOT NULL DEFAULT '',
  verifier_ref TEXT NOT NULL DEFAULT '', verifier_state TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS policy_versions (
  version INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  owner_identity TEXT NOT NULL, doc TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_current (
  id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pricing_reference (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, jurisdiction TEXT NOT NULL,
  tier INTEGER NOT NULL, unit_cents INTEGER NOT NULL, unit TEXT NOT NULL,
  source TEXT NOT NULL, effective_date TEXT NOT NULL, retrieved_date TEXT NOT NULL,
  confidence REAL NOT NULL, is_fixture INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger_entries (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, job_id TEXT NOT NULL,
  category TEXT NOT NULL, amount_cents INTEGER NOT NULL, direction TEXT NOT NULL,
  source TEXT NOT NULL, corrects TEXT, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, state TEXT NOT NULL,
  amount_cents INTEGER NOT NULL, collected_cents INTEGER NOT NULL DEFAULT 0,
  rail TEXT NOT NULL, verification_method TEXT NOT NULL DEFAULT '',
  verified_at TEXT, updated_at TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'usd', external_ref TEXT NOT NULL DEFAULT '',
  refunded_cents INTEGER NOT NULL DEFAULT 0,
  payout_state TEXT NOT NULL DEFAULT 'NOT_APPLICABLE', payout_verified_at TEXT
);
CREATE TABLE IF NOT EXISTS payment_events (
  event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, rail TEXT NOT NULL,
  event_type TEXT NOT NULL, job_id TEXT NOT NULL DEFAULT '',
  payment_id TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT '', livemode INTEGER NOT NULL DEFAULT 0,
  applied INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  state TEXT NOT NULL, blocked_on TEXT, title TEXT NOT NULL,
  client_id TEXT NOT NULL, quoted_cents INTEGER NOT NULL DEFAULT 0,
  consequence TEXT NOT NULL, state_entered_at TEXT NOT NULL,
  signals TEXT NOT NULL DEFAULT '{}', fail_reason TEXT NOT NULL DEFAULT '',
  resume_state TEXT, grant_id TEXT, job_class TEXT NOT NULL DEFAULT 'general'
);
CREATE TABLE IF NOT EXISTS requirements (
  -- The key is (job_id, id), not id. A requirement id like "R-001" is a name
  -- within one job, and two clients using the same checklist template is the
  -- normal case for a repeatable service. With a global primary key and
  -- INSERT OR REPLACE, the second job silently took over the first job's row.
  id TEXT NOT NULL, job_id TEXT NOT NULL, text TEXT NOT NULL,
  source TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '',
  acceptance TEXT NOT NULL DEFAULT '', check_name TEXT NOT NULL DEFAULT '',
  params TEXT NOT NULL DEFAULT '{}', criticality TEXT NOT NULL DEFAULT 'MANDATORY',
  status TEXT NOT NULL DEFAULT 'DRAFT', supersedes TEXT NOT NULL DEFAULT '',
  committed_at TEXT,
  PRIMARY KEY (job_id, id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, job_id TEXT NOT NULL, role TEXT NOT NULL,
  path TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL,
  media_type TEXT NOT NULL, produced_by TEXT NOT NULL DEFAULT '',
  capability_version TEXT NOT NULL DEFAULT '',
  source_digest TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS client_feedback (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, job_id TEXT NOT NULL,
  artifact_digest TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
  classification TEXT NOT NULL, confidence TEXT NOT NULL DEFAULT '',
  matched_requirement TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'OPEN', routed_to TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS capability_proposals (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
  decision TEXT NOT NULL DEFAULT '', covers TEXT NOT NULL DEFAULT '',
  why TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL DEFAULT '',
  decided_by TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
  job_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS verifier_certifications (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL,
  verifier_ref TEXT NOT NULL, capability_version TEXT NOT NULL,
  state TEXT NOT NULL, certified_checks TEXT NOT NULL DEFAULT '',
  trials_total INTEGER NOT NULL DEFAULT 0, trials_correct INTEGER NOT NULL DEFAULT 0,
  false_accepts INTEGER NOT NULL DEFAULT 0, false_rejects INTEGER NOT NULL DEFAULT 0,
  why TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS capability_assessments (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, verdict TEXT NOT NULL,
  conformance TEXT NOT NULL, assessor TEXT NOT NULL, ts TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS price_estimates (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, ts TEXT NOT NULL,
  lines TEXT NOT NULL, known_cents INTEGER NOT NULL, estimated_cents INTEGER NOT NULL,
  calibration REAL NOT NULL, effective_cost_cents INTEGER NOT NULL,
  pricing_context TEXT NOT NULL, preliminary INTEGER NOT NULL DEFAULT 0,
  job_class TEXT NOT NULL DEFAULT 'general',
  allowance_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS calibration_state (
  job_class TEXT PRIMARY KEY, k REAL NOT NULL, n INTEGER NOT NULL,
  updated_at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS governor_decisions (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, ts TEXT NOT NULL,
  verdict TEXT NOT NULL, estimate_id TEXT, revenue_cents INTEGER NOT NULL,
  effective_cost_cents INTEGER NOT NULL, margin REAL NOT NULL,
  floor REAL NOT NULL, policy_version INTEGER NOT NULL,
  calibration REAL NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_grants (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, ts TEXT NOT NULL,
  authorized_cents INTEGER NOT NULL, spent_cents INTEGER NOT NULL DEFAULT 0,
  decision_id TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
  spend_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS action_requests (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, job_id TEXT,
  action_class TEXT NOT NULL, destination TEXT NOT NULL, privacy TEXT NOT NULL,
  grant_id TEXT, allowed INTEGER NOT NULL, reason TEXT NOT NULL,
  cost_cents INTEGER NOT NULL DEFAULT 0, simulated INTEGER NOT NULL DEFAULT 0,
  phase TEXT NOT NULL DEFAULT 'SETTLED', request_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS owner_approvals (
  nonce TEXT PRIMARY KEY, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  owner_identity TEXT NOT NULL, subject TEXT NOT NULL, action_class TEXT NOT NULL,
  job_id TEXT NOT NULL DEFAULT '', max_cents INTEGER NOT NULL DEFAULT 0,
  used_at TEXT, key_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS work_sources (
  name TEXT PRIMARY KEY, kind TEXT NOT NULL, readiness TEXT NOT NULL,
  compliance TEXT NOT NULL, determination TEXT NOT NULL DEFAULT '',
  determined_by TEXT NOT NULL DEFAULT '', determined_at TEXT,
  is_fixture INTEGER NOT NULL DEFAULT 0, healthy INTEGER NOT NULL DEFAULT 1,
  last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, source TEXT NOT NULL,
  external_ref TEXT NOT NULL, title TEXT NOT NULL, quoted_cents INTEGER NOT NULL,
  deadline TEXT NOT NULL DEFAULT '', client_ref TEXT NOT NULL DEFAULT '',
  needs TEXT NOT NULL DEFAULT '[]', signals TEXT NOT NULL DEFAULT '{}',
  raw_ref TEXT NOT NULL DEFAULT '', job_id TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qualification_verdicts (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, opportunity_id TEXT NOT NULL,
  job_id TEXT, verdict TEXT NOT NULL, reason TEXT NOT NULL,
  governor_verdict TEXT NOT NULL DEFAULT '', conformance TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL DEFAULT 0.0, rank_position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory_facts (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL,
  payload TEXT NOT NULL, evidence_ref TEXT NOT NULL, tier TEXT NOT NULL
);
"""


def _immutability_triggers() -> str:
    out = []
    for table in APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            out.append(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} "
                f"BEFORE {op} ON {table} BEGIN "
                f"SELECT RAISE(ABORT, '{table} is append-only: {op} forbidden'); END;"
            )
    return "\n".join(out)


class AuthorityConnection:
    """A connection that may only write the tables its authority owns."""

    def __init__(self, conn: sqlite3.Connection, authority: str,
                 lock: threading.RLock) -> None:
        self._conn = conn
        self._authority = authority
        self._lock = lock
        self._owned = {t for t, owner in TABLE_OWNER.items() if owner == authority}

    @property
    def authority(self) -> str:
        return self._authority

    def _check(self, sql: str) -> None:
        match = _WRITE.match(sql)
        if not match:
            return
        table = match.group("table").lower()
        if table not in self._owned:
            owner = TABLE_OWNER.get(table, "<unknown table>")
            raise AuthorityError(
                f"authority {self._authority!r} may not write {table!r}; "
                f"that table is owned by {owner!r}"
            )

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        self._check(sql)
        with self._lock:
            try:
                return self._conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                if "append-only" in str(exc):
                    raise ImmutableRecord(str(exc)) from exc
                raise

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Reads are unrestricted: every authority may read, none may write another's."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()


class Store:
    """Owns the database file and hands out per-authority connections."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                     timeout=BUSY_TIMEOUT_S)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            # Settings that only matter once the process runs for weeks rather
            # than for one command. WAL survives a killed writer without leaving
            # the database locked against the next start; synchronous=FULL means
            # a committed transaction is on disk before the commit returns, which
            # is the property every recovery claim here depends on. Neither
            # applies to an in-memory database, and WAL cannot be set on one.
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
        self._conn.executescript(SCHEMA)
        self._conn.executescript(_immutability_triggers())
        self._migrate()
        self._conn.commit()

    def _rebuild_requirements_key(self) -> None:
        """Re-key ``requirements`` on (job_id, id) if it still has the old PK.

        SQLite cannot alter a primary key, so this is the standard rebuild:
        create, copy, drop, rename — inside the transaction the caller commits.
        Every row is copied unchanged, and the table is not append-only, so
        nothing immutable is rewritten. A database already on the new shape is
        left alone.
        """
        info = list(self._conn.execute("PRAGMA table_info(requirements)"))
        if not info:
            return
        key_columns = {r["name"] for r in info if r["pk"]}
        if key_columns == {"job_id", "id"}:
            return
        columns = [r["name"] for r in info]
        needed = ("id", "job_id", "text", "source", "confirmed_by", "acceptance",
                  "check_name", "params", "criticality", "status", "supersedes",
                  "committed_at")
        if not set(needed) <= set(columns):
            return          # an older shape still missing columns; ADD COLUMN first
        names = ", ".join(needed)
        self._conn.executescript(f"""
            CREATE TABLE requirements_rekeyed (
              id TEXT NOT NULL, job_id TEXT NOT NULL, text TEXT NOT NULL,
              source TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '',
              acceptance TEXT NOT NULL DEFAULT '', check_name TEXT NOT NULL DEFAULT '',
              params TEXT NOT NULL DEFAULT '{{}}',
              criticality TEXT NOT NULL DEFAULT 'MANDATORY',
              status TEXT NOT NULL DEFAULT 'DRAFT',
              supersedes TEXT NOT NULL DEFAULT '', committed_at TEXT,
              PRIMARY KEY (job_id, id)
            );
            INSERT INTO requirements_rekeyed({names})
              SELECT {names} FROM requirements;
            DROP TABLE requirements;
            ALTER TABLE requirements_rekeyed RENAME TO requirements;
        """)

    def _migrate(self) -> None:
        """Add columns a newer Solvent needs to a database an older one created.

        ``CREATE TABLE IF NOT EXISTS`` silently leaves an existing table alone, so
        without this an upgraded Solvent would read columns that are not there.
        Additive only: no column is dropped, renamed or retyped, and no row is
        rewritten, so an append-only table stays append-only.
        """
        for table, column, spec in MIGRATIONS:
            existing = {r["name"] for r in
                        self._conn.execute(f"PRAGMA table_info({table})")}
            if existing and column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
        self._rebuild_requirements_key()

    def for_authority(self, authority: str) -> AuthorityConnection:
        if authority not in set(TABLE_OWNER.values()):
            raise AuthorityError(f"unknown authority {authority!r}")
        return AuthorityConnection(self._conn, authority, self._lock)

    def raw_readonly(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Read path for projections and tests. Never writes."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def tables(self) -> Iterable[str]:
        return sorted(TABLE_OWNER)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
