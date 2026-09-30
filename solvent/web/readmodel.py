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

import json
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

    def latest_diagnostics(self) -> dict:
        """The diagnostics authority's most recent recorded report.

        The control centre renders this. It does not re-derive what any of it
        means, because a second copy of that judgement is a second answer to
        the same question -- and the two answers drifted apart the last time
        this page carried its own.

        Returns ``{"present", "stale", "age_seconds", "ran_at", "worst",
        "findings"}``. An absent or stale report is reported as such rather
        than dressed up as current: diagnostics matter most when the runtime
        that writes them has stopped.
        """
        import time

        from ..diagnostics import REPORT_STALE_SECONDS, UNKNOWN

        row = self.one("SELECT * FROM diagnostic_reports "
                       "ORDER BY ran_at DESC LIMIT 1")
        if not row:
            return {"present": False, "stale": True, "age_seconds": None,
                    "ran_at": "", "worst": UNKNOWN, "findings": []}
        age = max(0.0, time.time() - float(row["ran_at"]))
        stale = age > REPORT_STALE_SECONDS
        return {"present": True, "stale": stale, "age_seconds": age,
                "ran_at": row["ts"],
                # A stale report is not evidence about now, so its summary is
                # not allowed to read as a current verdict.
                "worst": UNKNOWN if stale else row["worst"],
                "findings": json.loads(row["findings"] or "[]")}

    def capability_gaps(self, *, min_occurrences: int = 3) -> list[dict]:
        """Needs that opportunities keep asking for and no proven capability
        covers. Mirrors :meth:`solvent.discovery.Discovery.capability_gaps`
        over the read-only projection.

        Deliberately *not* a second judgement: it counts rows and subtracts the
        proven set, which is arithmetic, and it reports advertised budget as
        advertised. The decision about whether a gap is worth closing stays
        with the owner and the Skills Lab, exactly as it does in the authority.
        """
        proven = {c.get("name") for c in self.capabilities() if c.get("proven")}
        counts: dict[str, dict] = {}
        for row in self.opportunities():
            try:
                needs = json.loads(row.get("needs") or "[]")
            except ValueError:
                continue
            for need in needs:
                if need in proven:
                    continue
                entry = counts.setdefault(
                    need, {"need": need, "occurrences": 0,
                           "advertised_cents": 0, "sources": set()})
                entry["occurrences"] += 1
                entry["advertised_cents"] += int(row.get("quoted_cents") or 0)
                entry["sources"].add(row.get("source", ""))
        out = [e for e in counts.values() if e["occurrences"] >= min_occurrences]
        for entry in out:
            entry["sources"] = sorted(s for s in entry["sources"] if s)
        return sorted(out, key=lambda e: (-e["occurrences"], e["need"]))

    def applications(self) -> list[dict]:
        return self.rows("SELECT * FROM applications ORDER BY ts DESC")

    def submission_attempts(self, application_id: str = "") -> list[dict]:
        if application_id:
            return self.rows(
                "SELECT * FROM submission_attempts WHERE application_id = ? "
                "ORDER BY ts", (application_id,))
        return self.rows("SELECT * FROM submission_attempts ORDER BY ts DESC")

    def applications_awaiting_approval(self) -> list[dict]:
        """Prepared, verified, and waiting on the owner to authorise sending.

        Verified is part of the filter on purpose: an unverified application
        must never appear as something to approve, because approving one would
        be the owner standing in for the verification.
        """
        return [a for a in self.applications()
                if a.get("state") == "PREPARED" and int(a.get("verified") or 0)
                and a.get("submission_mode") in
                ("AUTOMATED_SUBMISSION_SUPPORTED",
                 "AUTOMATED_SUBMISSION_SUPPORTED_WITH_AUTH")]

    def applications_needing_manual_submission(self) -> list[dict]:
        """Verified packages a person has to send (§39).

        Listed separately from the approval queue so nothing can present "a
        person must do this" as "waiting for your authorisation to automate
        it" -- they need different actions from the owner.
        """
        return [a for a in self.applications()
                if int(a.get("verified") or 0)
                and a.get("submission_mode") not in
                ("AUTOMATED_SUBMISSION_SUPPORTED",
                 "AUTOMATED_SUBMISSION_SUPPORTED_WITH_AUTH")]

    def submissions_needing_reconciliation(self) -> list[dict]:
        """Attempts whose outcome nobody can establish (§24).

        The one thing that must not happen to these is another send, so they
        are surfaced as their own category rather than mixed in with failures
        that can safely be retried.
        """
        return [a for a in self.submission_attempts()
                if a.get("outcome") == "UNKNOWN_REMOTE_STATE"]

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

    def deliverables(self, *, job_id: str = "", client_id: str = "") -> list[dict]:
        """Every file Solvent has produced, newest first, with its job.

        Joined here rather than assembled in the page, because "which client
        does this file belong to" is a question with one right answer and the
        answer lives in the jobs table. A page that worked it out for itself
        would be a second place that could get it wrong.
        """
        sql = ("SELECT a.*, j.client_id AS client_id, j.state AS job_state, "
               "j.title AS job_title FROM artifacts a "
               "JOIN jobs j ON j.id = a.job_id")
        clauses, params = [], []
        if job_id:
            clauses.append("a.job_id = ?")
            params.append(job_id)
        if client_id:
            clauses.append("j.client_id = ?")
            params.append(client_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.rows(sql + " ORDER BY a.ts DESC, a.rowid DESC", tuple(params))

    def deliverable_verification(self, digest: str) -> dict:
        """``{passed, failed, total}`` for **this exact file**.

        Scoped by digest, the same way the delivery gate scopes it. Evidence
        for a previous version of a file is not evidence for this one, and a
        page that showed the job's verification next to a corrected artifact
        would be reporting a pass that does not apply to what it is showing.
        """
        rows = self.rows(
            "SELECT verdict, COUNT(*) AS n FROM verification_evidence "
            "WHERE artifact_digest = ? GROUP BY verdict", (digest,))
        counts = {r["verdict"]: r["n"] for r in rows}
        passed, failed = counts.get("PASS", 0), counts.get("FAIL", 0)
        return {"passed": passed, "failed": failed, "total": passed + failed}

    # ------------------------------------------------------------ resilience
    def incidents(self, *, status: str = "", component: str = "",
                  fingerprint: str = "", open_only: bool = False) -> list[dict]:
        sql, params, clauses = "SELECT * FROM incidents", [], []
        for column, value in (("status", status), ("component", component),
                              ("fingerprint", fingerprint)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if open_only:
            clauses.append("status NOT IN ('VERIFIED', 'FIXED')")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.rows(sql + " ORDER BY ts DESC", tuple(params))

    def incident(self, incident_id: str) -> dict | None:
        return self.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))

    def incident_timeline(self, incident_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM incident_events WHERE incident_id = ? ORDER BY ts, id",
            (incident_id,))

    def related_incidents(self, fingerprint: str, exclude: str = "") -> list[dict]:
        return [r for r in self.rows(
            "SELECT * FROM incidents WHERE fingerprint = ? ORDER BY ts",
            (fingerprint,)) if r["id"] != exclude]

    def incident_lesson(self, fingerprint: str) -> dict | None:
        return self.one(
            "SELECT * FROM incident_lessons WHERE fingerprint = ? "
            "ORDER BY ts DESC LIMIT 1", (fingerprint,))

    def flight_frames(self, trace_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM flight_frames WHERE trace_id = ? ORDER BY ts, id",
            (trace_id,))

    def breakers(self) -> list[dict]:
        return self.rows("SELECT * FROM circuit_breakers ORDER BY component")

    def incident_components(self) -> list[dict]:
        return self.rows(
            "SELECT component, COUNT(*) AS n, "
            "SUM(CASE WHEN status NOT IN ('VERIFIED','FIXED') THEN 1 ELSE 0 END) "
            "AS open FROM incidents GROUP BY component ORDER BY n DESC")

    # --------------------------------------------------------- notifications
    def notifications(self, *, state: str = "", severity: str = "") -> list[dict]:
        sql, params, clauses = "SELECT * FROM notifications", [], []
        for column, value in (("state", state), ("severity", severity)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.rows(sql + " ORDER BY ts DESC", tuple(params))

    def unresolved_notifications(self) -> list[dict]:
        return self.rows(
            "SELECT * FROM notifications WHERE state NOT IN "
            "('ACKNOWLEDGED', 'EXPIRED') ORDER BY ts DESC")

    def notification_delivery_failures(self) -> list[dict]:
        """Sends that failed, counted durably rather than read off the state.

        An acknowledged notification is answered; the channel that could not
        carry it is still broken, and a page showing zero failures because
        somebody clicked acknowledge would be hiding a broken alerting path.
        """
        return self.rows(
            "SELECT * FROM notifications WHERE delivery_failures > 0 "
            "ORDER BY ts DESC")

    def notification_settings(self) -> dict:
        """What is configured, as the website is allowed to see it.

        The phone number is a mask. The full value lives in the runtime's
        environment and never reaches this process, which reads a file.
        """
        return {
            "owner_phone_mask": self.policy_value("notifications",
                                                  "owner_phone_mask", default=""),
            "sms_provider": self.policy_value("notifications", "sms_provider",
                                              default=""),
            "voice_provider": self.policy_value("notifications",
                                                "voice_provider", default=""),
            "escalate_after_seconds": self.policy_value(
                "notifications", "escalate_after_seconds", default=900),
        }

    def lessons(self, *, kind: str = "") -> list[dict]:
        """What Solvent has concluded, newest first, with what it concluded it from."""
        sql = "SELECT * FROM memory_facts"
        params: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            params = (kind,)
        return self.rows(sql + " ORDER BY ts DESC, rowid DESC", params)

    def lesson_kinds(self) -> list[dict]:
        return self.rows(
            "SELECT kind, COUNT(*) AS n, MIN(tier) AS weakest FROM memory_facts "
            "GROUP BY kind ORDER BY n DESC")

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
        """Re-walk the hash chain with the Audit Log's own algorithm.

        Recomputed here rather than trusting a stored flag, because a chain that
        reports itself intact is the one thing an attacker would edit.

        An earlier version of this hedged: when the recomputed digest did not
        match it returned *intact* with a note saying digest recomputation had
        not been attempted, because the field order was not certain. That is the
        wrong direction for a security check to be unsure in — a tampered log
        would have reported clean on the Security page. The body is now built
        field-for-field the way :meth:`solvent.audit.AuditLog.record` builds it,
        and a mismatch says so.
        """
        import hashlib
        import json

        previous = "0" * 64
        rows = self.rows("SELECT * FROM audit_log ORDER BY seq")
        for row in rows:
            if row.get("prev_hash") != previous:
                return False, f"chain breaks at seq {row.get('seq')}: prev_hash"
            try:
                payload = json.loads(row.get("payload") or "{}")
            except ValueError:
                return False, f"seq {row.get('seq')}: payload is not JSON"
            body = {
                "ts": row.get("ts"), "event": row.get("event"),
                "authority": row.get("authority"),
                "initiator": row.get("initiator"), "job_id": row.get("job_id"),
                "why": row.get("why"), "input_ref": row.get("input_ref"),
                "decision": row.get("decision"),
                "permission": row.get("permission"),
                "financial_authorization": row.get("financial_authorization"),
                "external_effect": row.get("external_effect"),
                "result": row.get("result"),
                "verification_ref": row.get("verification_ref"),
                "payload": payload,
            }
            expected = hashlib.sha256(
                (previous + json.dumps(body, sort_keys=True,
                                       default=str)).encode()).hexdigest()
            if expected != row.get("hash"):
                return False, f"seq {row.get('seq')}: content hash mismatch"
            previous = row.get("hash")
        return True, f"{len(rows)} entries, chain verified"

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
