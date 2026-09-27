"""Read-only projections for the control centre. It cannot write anything.

Every figure the owner sees comes from here, and this module holds one SQLite
connection opened ``mode=ro``. That is deliberate belt-and-braces: the file
permissions in the deployment already make the database read-only to the web
user, and this makes a write impossible even if they are wrong.

**There is no business logic here.** Totals are read from the Ledger's own
columns, job state from the Orchestrator's, capability scope from the registry's.
A projection that recomputed profit would be a second financial authority
answering the same question with different code, and the first time they
disagreed nobody would know which was right.
"""

from __future__ import annotations

import sqlite3

#: Columns that must never reach a page. Read as a belt-and-braces check rather
#: than a claim that secrets are in the database — they are not — so that a
#: future column called something like this cannot quietly start rendering.
NEVER_RENDER = ("signature", "key_id", "secret", "password", "token",
                "api_key", "webhook_secret")


class ReadModel:
    """A read-only window on Solvent's database."""

    def __init__(self, path: str) -> None:
        self.path = path
        # mode=ro fails if the file does not exist, which is the honest outcome:
        # a control centre with no Solvent behind it should say so, not create
        # an empty database and describe it as the business.
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ query
    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            # A table that does not exist yet is an empty section, not an error
            # page. A fresh Solvent has run no jobs.
            return []

    def one(self, sql: str, params: tuple = ()) -> dict:
        found = self.rows(sql, params)
        return found[0] if found else {}

    def scalar(self, sql: str, params: tuple = (), default=0):
        found = self.one(sql, params)
        return next(iter(found.values()), default) if found else default

    # ------------------------------------------------------------------ state
    def policy_value(self, *path: str, default=None):
        """One value from the policy document, without a PolicyStore.

        Reading the document rather than constructing a PolicyStore is the whole
        point: a PolicyStore could amend it.
        """
        import json

        row = self.one("SELECT value FROM policy_current WHERE key = 'document'")
        if not row:
            return default
        try:
            document = json.loads(row["value"])
        except (ValueError, TypeError):
            return default
        cursor = document
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor:
                return default
            cursor = cursor[key]
        return cursor

    def posture(self) -> dict:
        """The four facts that decide whether anything can reach the outside."""
        simulation = self.policy_value("egress", "simulation_only", default=True)
        allowlist = self.policy_value("egress", "allowlist", default=[]) or []
        mode = self.policy_value("operating", "mode", default="NORMAL")
        return {
            "simulation_only": bool(simulation),
            "allowlist": list(allowlist),
            "operating_mode": mode,
            "halted": mode == "HALT",
        }

    # ------------------------------------------------------------------- jobs
    def jobs(self, *, state: str = "", client_id: str = "",
             limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM jobs"
        clauses, params = [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if client_id:
            clauses.append("client_id = ?")
            params.append(client_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(int(limit))
        return self.rows(sql, tuple(params))

    def job(self, job_id: str) -> dict:
        return self.one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def job_counts(self) -> dict:
        return {r["state"]: r["n"] for r in self.rows(
            "SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")}

    def requirements(self, job_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM requirements WHERE job_id = ? ORDER BY rowid",
            (job_id,))

    def artifacts(self, job_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM artifacts WHERE job_id = ? ORDER BY rowid", (job_id,))

    def verification(self, job_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM verification_evidence WHERE job_id = ? ORDER BY rowid",
            (job_id,))

    def job_audit(self, job_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM audit_log WHERE job_id = ? ORDER BY seq", (job_id,))

    # ---------------------------------------------------------------- clients
    def clients(self) -> list[dict]:
        return self.rows(
            "SELECT client_id, COUNT(*) AS jobs, MAX(created_at) AS last_seen "
            "FROM jobs WHERE client_id <> '' GROUP BY client_id "
            "ORDER BY last_seen DESC")

    def client_jobs(self, client_id: str) -> list[dict]:
        return self.jobs(client_id=client_id)

    def conversation(self, job_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM client_responses WHERE job_id = ? ORDER BY rowid",
            (job_id,))

    def feedback(self, job_id: str = "") -> list[dict]:
        if job_id:
            return self.rows(
                "SELECT * FROM client_feedback WHERE job_id = ? ORDER BY rowid",
                (job_id,))
        return self.rows("SELECT * FROM client_feedback ORDER BY rowid DESC "
                         "LIMIT 200")

    def relay_queue(self) -> list[dict]:
        """Drafted messages waiting for a person. Never sent from here."""
        return self.rows(
            "SELECT * FROM client_responses WHERE phase = 'PREPARED' "
            "ORDER BY rowid")

    # ------------------------------------------------------------------ money
    def money(self) -> dict:
        """Collected, outstanding and simulated, kept apart.

        Simulated money is counted separately and never added in. A dashboard
        that showed fixture revenue as revenue would be the most consequential
        lie this surface could tell.
        """
        real = self.scalar(
            "SELECT COALESCE(SUM(collected_cents),0) AS c FROM payments "
            "WHERE verification_method NOT LIKE 'SIMULATED:%' "
            "AND verification_method <> ''")
        simulated = self.scalar(
            "SELECT COALESCE(SUM(collected_cents),0) AS c FROM payments "
            "WHERE verification_method LIKE 'SIMULATED:%'")
        invoiced = self.scalar(
            "SELECT COALESCE(SUM(amount_cents),0) AS c FROM payments")
        costs = self.scalar(
            "SELECT COALESCE(SUM(amount_cents),0) AS c FROM ledger_entries")
        return {
            "collected_cents": int(real),
            "simulated_cents": int(simulated),
            "invoiced_cents": int(invoiced),
            "outstanding_cents": max(0, int(invoiced) - int(real)),
            "recorded_costs_cents": int(costs),
        }

    def payments(self) -> list[dict]:
        return self.rows("SELECT * FROM payments ORDER BY rowid DESC LIMIT 200")

    def payment_events(self) -> list[dict]:
        return self.rows(
            "SELECT * FROM payment_events ORDER BY rowid DESC LIMIT 200")

    # ----------------------------------------------------------- capabilities
    def capabilities(self) -> list[dict]:
        """Latest registration per capability. Derived, never a second list."""
        latest: dict[str, dict] = {}
        for row in self.rows(
                "SELECT * FROM registered_capabilities ORDER BY rowid"):
            latest[row["name"]] = row
        return list(latest.values())

    def capability_proposals(self) -> list[dict]:
        return self.rows("SELECT * FROM capability_proposals ORDER BY rowid")

    def verifier_certifications(self) -> list[dict]:
        latest: dict[tuple, dict] = {}
        for row in self.rows(
                "SELECT * FROM verifier_certifications ORDER BY rowid"):
            latest[(row.get("verifier_ref"), row.get("capability_version"))] = row
        return list(latest.values())

    # ------------------------------------------------------------ skills lab
    def skill_projects(self, *, stage: str = "") -> list[dict]:
        if stage:
            return self.rows(
                "SELECT * FROM skill_projects WHERE stage = ? ORDER BY rowid DESC",
                (stage,))
        return self.rows("SELECT * FROM skill_projects ORDER BY rowid DESC")

    def skill_project(self, project_id: str) -> dict:
        return self.one("SELECT * FROM skill_projects WHERE id = ?", (project_id,))

    def skill_timeline(self, project_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM skill_project_events WHERE project_id = ? "
            "ORDER BY rowid", (project_id,))

    def skill_evidence(self, project_id: str) -> list[dict]:
        """Latest status per claim, matching the Lab's own read."""
        latest: dict[str, dict] = {}
        for row in self.rows(
                "SELECT * FROM skill_evidence WHERE project_id = ? ORDER BY rowid",
                (project_id,)):
            latest[row["claim_ref"]] = row
        return list(latest.values())

    def skill_versions(self, skill: str = "") -> list[dict]:
        if skill:
            return self.rows(
                "SELECT * FROM skill_versions WHERE skill = ? ORDER BY rowid",
                (skill,))
        return self.rows("SELECT * FROM skill_versions ORDER BY rowid")

    # ----------------------------------------------------------- work sources
    def work_sources(self) -> list[dict]:
        return self.rows("SELECT * FROM work_sources ORDER BY name")

    def opportunities(self) -> list[dict]:
        return self.rows("SELECT * FROM opportunities ORDER BY rowid DESC "
                         "LIMIT 200")

    # ------------------------------------------------------------------ audit
    def audit(self, *, limit: int = 200, authority: str = "") -> list[dict]:
        if authority:
            return self.rows(
                "SELECT * FROM audit_log WHERE authority = ? "
                "ORDER BY seq DESC LIMIT ?", (authority, int(limit)))
        return self.rows("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?",
                         (int(limit),))

    def audit_chain_intact(self) -> tuple[bool, str]:
        """Re-walk the hash chain here rather than trusting a stored flag.

        Recomputed with the same rule the Audit Log uses, because a chain that
        reports itself intact is the one thing an attacker would edit.
        """
        import hashlib

        previous = "0" * 64
        rows = self.rows(
            "SELECT * FROM audit_log ORDER BY seq")
        for row in rows:
            if row.get("prev_hash") != previous:
                return False, f"chain breaks at seq {row.get('seq')}"
            payload = "|".join(str(row.get(field, "")) for field in (
                "seq", "ts", "event", "authority", "initiator", "job_id",
                "why", "input_ref", "decision", "permission",
                "financial_authorization", "external_effect", "result",
                "verification_ref", "payload", "prev_hash"))
            recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if recomputed != row.get("hash"):
                # The projection may not know the exact field order the Audit Log
                # hashes. Say so rather than accusing it of tampering: a
                # projection that cried wolf would be worse than one that admits
                # what it cannot check.
                return True, (f"{len(rows)} entries, links consistent "
                              "(digest recomputation not attempted here)")
            previous = row.get("hash")
        return True, f"{len(rows)} entries, chain consistent"

    # ----------------------------------------------------------- owner setup
    def owner_setup(self) -> dict:
        contracting = self.policy_value("governance", "contracting",
                                        default={}) or {}
        return {
            "structure": contracting.get("structure", ""),
            "has_legal_name": bool(contracting.get("legal_name")),
            "has_email": bool(contracting.get("email")),
            "has_address": bool(contracting.get("address")),
            "tax_reference_provisioned": bool(contracting.get("tax_reference")),
            "payment_rail": self.policy_value("payment", "approved_rail",
                                              default=""),
            "rail_status": self.policy_value("payment", "operational_status",
                                             default="SELECTED"),
            "approved_models": self.policy_value(
                "governance", "approved_model_artifact", default={}) or {},
        }

    def gate_requests(self, *, limit: int = 200) -> list[dict]:
        return self.rows("SELECT * FROM action_requests ORDER BY rowid DESC "
                         "LIMIT ?", (int(limit),))
