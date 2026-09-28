"""Resilience and incident intelligence: what failed, why, and has it before?

**What this is not.** It is not a second Policy Store, Ledger, Audit Log,
Financial Governor, Action Gate or capability registry, and it holds no
authority over any of them. It cannot promote a capability, change a rule,
authorise spending, permit an external effect or clear HALT. Diagnosis is not
permission: this module's entire output is *a description of what happened*,
and every decision that follows from it belongs to the authority that already
owned that decision.

**What it owns**, because nothing did: the durable memory of failure. Solvent
could fail safely — the authorities are fail-closed and a crash left the
database consistent — but it could not remember that it had failed, could not
tell a new failure from the same one for the fourth time, and had nothing to
show an owner asking "why did that happen, and will it happen again?".

Four things live here, and they are deliberately separate from Business Memory:

**Incidents.** One record per failure, carrying where it started, what Solvent
was doing, what it affected, whether any external effect became uncertain, how
it recovered, the root cause when established, the fix, and how the fix was
verified. Business Memory holds lessons about *clients and work*, drawn from
verified evidence and refusing anything less. An incident is knowledge about
*Solvent itself*, often recorded at the moment of greatest uncertainty, and
mixing the two would put unverified crash guesses in the same table the pricing
model learns from.

**The flight recorder.** A bounded, sanitised ring of what the process was doing
before it failed. Bounded because an unbounded diagnostic log is a disk-pressure
incident waiting to happen, and sanitised because a crash dump is the classic
way a secret escapes the one place it was supposed to stay.

**Failure fingerprints.** A stable identity for a *kind* of failure, computed
from characteristics that survive between runs — never from a timestamp, an id
or a path, which differ every time and would make every failure look new.

**Circuit breakers.** Containment for a dependency that keeps failing. A breaker
opens to stop Solvent hammering something broken; it never opens a door.
Fallback still needs the clearance the original call needed, which is why this
module has no way to choose a provider.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .store import Store

AUTHORITY = "resilience"

#: Where an incident is in its life. Ordered loosely from detection to proof.
DETECTED = "DETECTED"
CONTAINED = "CONTAINED"
RECOVERING = "RECOVERING"
RECOVERED = "RECOVERED"
ROOT_CAUSE_PENDING = "ROOT_CAUSE_PENDING"
FIX_PENDING = "FIX_PENDING"
FIXED = "FIXED"
VERIFIED = "VERIFIED"
RECURRED = "RECURRED"
OWNER_ACTION_REQUIRED = "OWNER_ACTION_REQUIRED"

STATUSES = (DETECTED, CONTAINED, RECOVERING, RECOVERED, ROOT_CAUSE_PENDING,
            FIX_PENDING, FIXED, VERIFIED, RECURRED, OWNER_ACTION_REQUIRED)

#: Statuses that mean nobody needs to look right now.
_SETTLED = (VERIFIED, FIXED)

#: How bad. Severity is about consequence, not about how loud the exception was.
S_INFO = "INFO"
S_DEGRADED = "DEGRADED"
S_SERIOUS = "SERIOUS"
S_CRITICAL = "CRITICAL"
SEVERITIES = (S_INFO, S_DEGRADED, S_SERIOUS, S_CRITICAL)

#: Circuit breaker states.
CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

#: How many consecutive failures open a breaker, and how long it stays open
#: before a canary is allowed. Defaults; Policy may override per component.
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60

#: Frames kept per trace, and traces kept in total. The flight recorder is a
#: diagnostic aid, not an archive: an unbounded one becomes the disk-pressure
#: incident it was meant to help diagnose.
FRAMES_PER_TRACE = 40
MAX_TRACES = 200


# --------------------------------------------------------------- redaction

#: Patterns whose *values* never reach an incident, a frame or a notification.
#: Keyed by what they look like rather than by field name, because the field a
#: secret arrives in is whatever the caller happened to call it.
_SECRET_PATTERNS = (
    # Scheme-prefixed credentials FIRST. The label rule below matches
    # "Authorization: Bearer" and treats "Bearer" as the value, which redacted
    # the word Bearer and left the token sitting in plain sight — a header
    # token leaking into every incident record that touched an HTTP error.
    # Order is the fix: take the token before anything else claims the label.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"),
     r"\1 [REDACTED]"),
    # Anything that names itself a secret, followed by its value.
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|"
                r"owner[_-]?key|private[_-]?key|webhook[_-]?secret|"
                r"authorization|bearer)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
    # Stripe-shaped credentials, live or test.
    (re.compile(r"\b[sr]k_(live|test)_[A-Za-z0-9]{4,}"), "[REDACTED]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{4,}"), "[REDACTED]"),
    # Long hex, which is what a raw key or signature looks like.
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "[REDACTED]"),
    # A phone number is owner PII, not a diagnostic.
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[REDACTED]"),
    # PEM blocks.
    (re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.S), "[REDACTED]"),
)

#: Environment variables whose values are redacted wherever they appear, even
#: when they appear with no label around them. This is what catches a secret
#: interpolated into a message by code that never called it a secret.
_SECRET_ENV = ("SOLVENT_OWNER_KEY", "SOLVENT_STRIPE_SECRET",
               "SOLVENT_WEB_PASSWORD_HASH", "SOLVENT_OWNER_PHONE",
               "SOLVENT_SMS_CREDENTIAL", "SOLVENT_VOICE_CREDENTIAL")

#: The shortest env value worth substring-matching. Below this the value is as
#: likely to be a common word, and redacting every occurrence of "1" would
#: destroy the diagnostic without protecting anything.
_MIN_ENV_VALUE = 8


def redact(value, *, environ=None) -> str:
    """Sanitise text for storage. Applied at every write, never at read.

    Redacting on the way in rather than on the way out is the whole point: a
    secret that reached the table is already stored, and the table is read by
    the website, the audit log, backups and anything an investigator runs. There
    is no second chance to catch it.
    """
    import os

    text = value if isinstance(value, str) else json.dumps(value, default=str,
                                                           sort_keys=True)
    env = os.environ if environ is None else environ
    for name in _SECRET_ENV:
        secret = env.get(name, "")
        if secret and len(secret) >= _MIN_ENV_VALUE:
            text = text.replace(secret, "[REDACTED]")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ------------------------------------------------------------ fingerprints

#: Things that differ on every run and must not reach a fingerprint, or the
#: same failure gets a new identity each time and correlation never fires.
_VOLATILE = (
    (re.compile(r"\b(job|art|ver|prop|mem|resp|int|inc|trace)_[0-9a-f]+\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<hex>"),
    (re.compile(r"/[^\s'\"]+"), "<path>"),
    # Numbers *including a unit suffix*. ``\b\d+\b`` alone leaves "30s" and
    # "45s" distinct, so "timed out after 30s" and "timed out after 45s"
    # fingerprinted as two different failures — which is precisely the thing
    # this normalisation exists to stop, and it read as two new failure classes
    # every time one dependency was slow.
    (re.compile(r"\d+(?:\.\d+)?\s?[a-zA-Z]{0,2}\b"), "<n>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),
    (re.compile(r"\s+"), " "),
)


def normalise(signature: str) -> str:
    """Strip everything that differs run to run, keep what identifies the fault."""
    text = signature or ""
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return text.strip().lower()[:300]


def fingerprint(*, component: str, operation: str, error_class: str,
                signature: str = "") -> str:
    """A stable identity for a *kind* of failure.

    Deliberately not the exception message: two different faults raise
    ``ValueError("invalid")`` and the same fault raises it with a different row
    number every time. The fingerprint is built from where it happened, what was
    being attempted, the exception type, and the message with its volatile parts
    removed — so the same bug fingerprints the same way next week, and a
    genuinely different bug in the same function does not.

    Secrets never reach it: the signature is redacted before it is normalised,
    so a fingerprint cannot become an oracle for a value nobody should see.
    """
    body = "|".join([component, operation, error_class,
                     normalise(redact(signature))])
    return hashlib.sha256(body.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Correlation:
    """What the record already knows about a failure like this one."""

    fingerprint: str
    seen_before: bool
    prior_ids: tuple = ()
    prior_root_cause: str = ""
    prior_fix: str = ""
    prior_prevention: str = ""
    fix_was_verified: bool = False
    regression: bool = False

    @property
    def summary(self) -> str:
        if not self.seen_before:
            return "NEW FAILURE CLASS"
        if self.regression:
            return ("SEEN BEFORE — and a verified fix was supposed to prevent "
                    "it, so this is a regression")
        return f"SEEN BEFORE — {len(self.prior_ids)} prior incident(s)"


# ------------------------------------------------------------- the authority

class Resilience:
    """Durable memory of failure, and the containment that follows from it.

    Holds no authority. Every method here either records what happened or
    reports what is recorded; nothing decides what Solvent may do about it.
    """

    def __init__(self, store: Store, audit: AuditLog, policy=None) -> None:
        self._db = store.for_authority(AUTHORITY)
        self._audit = audit
        #: Consulted for thresholds and for who counts as an owner. Never
        #: written to: an incident cannot change a rule, which is the difference
        #: between remembering a failure and being governed by one.
        self._policy = policy

    # ------------------------------------------------------------ incidents

    def record_incident(self, *, component: str, operation: str,
                        error_class: str, signature: str = "",
                        severity: str = S_DEGRADED, job_id: str = "",
                        client_id: str = "", trigger: str = "",
                        symptoms: str = "", checkpoint: str = "",
                        external_uncertainty: str = "", trace_id: str = "",
                        detected_by: str = AUTHORITY) -> str:
        """Record one failure and say whether it has been seen before.

        Everything free-text is redacted on the way in. A crash record is the
        classic place a secret escapes: it is written at the moment of least
        care, by code that is already handling something unexpected, and it is
        then read by the website, copied into backups and shown to whoever is
        investigating.

        An incident that matches a prior fingerprint does not overwrite it. Both
        are kept and the new one is linked, because "this is the fourth time"
        is the fact that matters and a counter that replaced the history would
        lose the three cases that establish it.
        """
        if severity not in SEVERITIES:
            raise FailClosed(f"{severity!r} is not a severity; one of {SEVERITIES}")
        print_ = fingerprint(component=component, operation=operation,
                             error_class=error_class, signature=signature)
        prior = self.correlate(print_)

        incident_id = new_id("inc")
        timestamp = now()
        status = RECURRED if prior.seen_before else DETECTED
        self._db.execute(
            "INSERT INTO incidents(id,ts,first_seen,last_seen,component,operation,"
            "error_class,fingerprint,severity,status,job_id,client_id,trigger,"
            "symptoms,checkpoint,external_uncertainty,trace_id,detected_by,"
            "recurrence_count,related_to,root_cause,fix,prevention,"
            "verification_ref,recovery_action,recovery_result,owner_action) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'','','','','','','')",
            (incident_id, timestamp, timestamp, timestamp, component, operation,
             error_class, print_, severity, status, job_id, client_id,
             redact(trigger), redact(symptoms), checkpoint,
             redact(external_uncertainty), trace_id, detected_by,
             len(prior.prior_ids) + 1, ",".join(prior.prior_ids)))
        self._db.commit()
        self._event(incident_id, status, prior.summary, detected_by)
        self._audit.record(
            event="resilience.incident", authority=AUTHORITY,
            initiator=detected_by, why=redact(trigger)[:200] or operation,
            job_id=job_id or None, decision=print_,
            result=f"{severity} {status}: {prior.summary}",
            external_effect=redact(external_uncertainty)[:180] or "none recorded")
        return incident_id

    def correlate(self, print_: str) -> Correlation:
        """What the record already knows about this kind of failure."""
        rows = self._db.query(
            "SELECT * FROM incidents WHERE fingerprint = ? ORDER BY ts",
            (print_,))
        if not rows:
            return Correlation(fingerprint=print_, seen_before=False)
        lesson = self._db.query_one(
            "SELECT * FROM incident_lessons WHERE fingerprint = ? "
            "ORDER BY ts DESC LIMIT 1", (print_,))
        verified = any(r["status"] == VERIFIED for r in rows)
        return Correlation(
            fingerprint=print_, seen_before=True,
            prior_ids=tuple(r["id"] for r in rows),
            prior_root_cause=(lesson["root_cause"] if lesson else ""),
            prior_fix=(lesson["fix"] if lesson else ""),
            prior_prevention=(lesson["prevention"] if lesson else ""),
            fix_was_verified=verified,
            # A failure that recurs after a *verified* fix is a regression, and
            # that is a different sentence from "this happens sometimes".
            regression=verified)

    def advance(self, incident_id: str, status: str, *, why: str,
                actor: str = AUTHORITY) -> None:
        """Move an incident along. Every move is an event, not an overwrite."""
        if status not in STATUSES:
            raise FailClosed(f"{status!r} is not an incident status")
        self.incident(incident_id)
        self._db.execute(
            "UPDATE incidents SET status = ?, last_seen = ? WHERE id = ?",
            (status, now(), incident_id))
        self._db.commit()
        self._event(incident_id, status, redact(why), actor)

    def record_recovery(self, incident_id: str, *, action: str, result: str,
                        actor: str = AUTHORITY) -> None:
        self.incident(incident_id)
        self._db.execute(
            "UPDATE incidents SET recovery_action = ?, recovery_result = ?, "
            "status = ?, last_seen = ? WHERE id = ?",
            (redact(action), redact(result), RECOVERING, now(), incident_id))
        self._db.commit()
        self._event(incident_id, RECOVERING, f"{action} -> {result}", actor)

    def record_root_cause(self, incident_id: str, *, root_cause: str,
                          actor: str = AUTHORITY) -> None:
        self.incident(incident_id)
        self._db.execute(
            "UPDATE incidents SET root_cause = ?, status = ?, last_seen = ? "
            "WHERE id = ?",
            (redact(root_cause), FIX_PENDING, now(), incident_id))
        self._db.commit()
        self._event(incident_id, FIX_PENDING, redact(root_cause), actor)

    def record_fix(self, incident_id: str, *, fix: str, prevention: str = "",
                   actor: str = AUTHORITY) -> None:
        """A fix is claimed here and *not* believed. FIXED is not VERIFIED."""
        row = self.incident(incident_id)
        if not row["root_cause"]:
            raise FailClosed(
                "a fix with no root cause behind it is a guess that happened to "
                "stop the symptom; record what caused it first")
        self._db.execute(
            "UPDATE incidents SET fix = ?, prevention = ?, status = ?, "
            "last_seen = ? WHERE id = ?",
            (redact(fix), redact(prevention), FIXED, now(), incident_id))
        self._db.commit()
        self._event(incident_id, FIXED, redact(fix), actor)

    def verify_fix(self, incident_id: str, *, verification_ref: str,
                   actor: str = AUTHORITY) -> None:
        """Mark a fix proven, and only then keep the lesson.

        ``verification_ref`` names the evidence — a regression test, a
        reproduction that no longer fails. Restarting successfully is not it: a
        process that came back up has demonstrated that it can start, which is
        the one thing nobody doubted.

        The lesson is written here rather than at ``record_fix`` on purpose.
        Unverified fixes are exactly the ones that should not be offered to the
        next person who hits this failure.
        """
        row = self.incident(incident_id)
        if row["status"] != FIXED:
            raise FailClosed(
                f"{incident_id} is {row['status']}; a fix is verified after it "
                "is recorded, not instead of recording it")
        if not verification_ref.strip():
            raise FailClosed(
                "verifying a fix requires naming the evidence; 'it stopped "
                "happening' is not evidence that it was fixed")
        self._db.execute(
            "UPDATE incidents SET status = ?, verification_ref = ?, last_seen = ? "
            "WHERE id = ?", (VERIFIED, verification_ref, now(), incident_id))
        self._db.execute(
            "INSERT INTO incident_lessons(id,ts,fingerprint,root_cause,fix,"
            "prevention,verification_ref,incident_id) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("les"), now(), row["fingerprint"], row["root_cause"],
             row["fix"], row["prevention"], verification_ref, incident_id))
        self._db.commit()
        self._event(incident_id, VERIFIED, f"evidence: {verification_ref}", actor)

    def needs_owner(self, incident_id: str, *, why: str,
                    actor: str = AUTHORITY) -> None:
        self.advance(incident_id, OWNER_ACTION_REQUIRED, why=why, actor=actor)
        self._db.execute("UPDATE incidents SET owner_action = ? WHERE id = ?",
                         (redact(why), incident_id))
        self._db.commit()

    # -------------------------------------------------------------- reading

    def incident(self, incident_id: str) -> dict:
        row = self._db.query_one("SELECT * FROM incidents WHERE id = ?",
                                 (incident_id,))
        if row is None:
            raise FailClosed(f"no incident {incident_id!r}")
        return dict(row)

    def incidents(self, *, status: str = "", component: str = "",
                  fingerprint_: str = "", job_id: str = "",
                  open_only: bool = False) -> list[dict]:
        sql, params = "SELECT * FROM incidents", []
        clauses = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if component:
            clauses.append("component = ?")
            params.append(component)
        if fingerprint_:
            clauses.append("fingerprint = ?")
            params.append(fingerprint_)
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)
        if open_only:
            clauses.append(f"status NOT IN ({','.join('?' * len(_SETTLED))})")
            params.extend(_SETTLED)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [dict(r) for r in self._db.query(sql + " ORDER BY ts DESC",
                                                tuple(params))]

    def timeline(self, incident_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM incident_events WHERE incident_id = ? ORDER BY ts, id",
            (incident_id,))]

    def lesson_for(self, print_: str) -> dict | None:
        row = self._db.query_one(
            "SELECT * FROM incident_lessons WHERE fingerprint = ? "
            "ORDER BY ts DESC LIMIT 1", (print_,))
        return dict(row) if row else None

    def _event(self, incident_id: str, stage: str, detail: str,
               actor: str) -> None:
        self._db.execute(
            "INSERT INTO incident_events(id,ts,incident_id,stage,detail,actor) "
            "VALUES(?,?,?,?,?,?)",
            (new_id("iev"), now(), incident_id, stage, redact(detail)[:600],
             actor))
        self._db.commit()

    # ------------------------------------------------------ flight recorder

    def frame(self, *, trace_id: str, component: str, operation: str,
              state: str = "", decision: str = "", checkpoint: str = "",
              detail=None) -> None:
        """Record one step of what Solvent was doing. Bounded and sanitised.

        Written before the thing it describes, not after, because the frames
        that matter most are the ones describing the operation that never
        returned.
        """
        self._db.execute(
            "INSERT INTO flight_frames(id,ts,trace_id,component,operation,state,"
            "decision,checkpoint,detail) VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id("frm"), now(), trace_id, component, operation,
             state, redact(decision)[:400], checkpoint,
             redact(detail if detail is not None else "")[:1000]))
        self._db.commit()
        self._trim(trace_id)

    def _trim(self, trace_id: str) -> None:
        """Keep the recorder bounded. An unbounded diagnostic log becomes the
        disk-pressure incident it exists to help diagnose."""
        self._db.execute(
            "DELETE FROM flight_frames WHERE trace_id = ? AND id NOT IN ("
            "SELECT id FROM flight_frames WHERE trace_id = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?)",
            (trace_id, trace_id, FRAMES_PER_TRACE))
        self._db.execute(
            "DELETE FROM flight_frames WHERE trace_id NOT IN ("
            "SELECT trace_id FROM (SELECT trace_id, MAX(ts) AS m "
            "FROM flight_frames GROUP BY trace_id ORDER BY m DESC LIMIT ?))",
            (MAX_TRACES,))
        self._db.commit()

    def frames(self, trace_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM flight_frames WHERE trace_id = ? ORDER BY ts, id",
            (trace_id,))]

    def traces(self) -> list[str]:
        return [r["trace_id"] for r in self._db.query(
            "SELECT trace_id, MAX(ts) AS m FROM flight_frames "
            "GROUP BY trace_id ORDER BY m DESC")]

    def last_checkpoint(self, trace_id: str) -> str:
        """The last durable state this trace reached, for a restart to resume from."""
        row = self._db.query_one(
            "SELECT checkpoint FROM flight_frames WHERE trace_id = ? "
            "AND checkpoint != '' ORDER BY ts DESC, id DESC LIMIT 1", (trace_id,))
        return row["checkpoint"] if row else ""

    # ------------------------------------------------------ circuit breakers

    def _breaker(self, component: str) -> dict:
        row = self._db.query_one(
            "SELECT * FROM circuit_breakers WHERE component = ?", (component,))
        if row is None:
            self._db.execute(
                "INSERT INTO circuit_breakers(component,state,failure_count,"
                "opened_at,last_failure,next_probe,canary_result,why) "
                "VALUES(?,?,0,'','',0,'','')", (component, CLOSED))
            self._db.commit()
            row = self._db.query_one(
                "SELECT * FROM circuit_breakers WHERE component = ?", (component,))
        return dict(row)

    def _threshold(self, component: str) -> int:
        if self._policy is None:
            return DEFAULT_FAILURE_THRESHOLD
        return int(self._policy.get("resilience", "failure_threshold",
                                    default=DEFAULT_FAILURE_THRESHOLD))

    def _cooldown(self, component: str) -> int:
        if self._policy is None:
            return DEFAULT_COOLDOWN_SECONDS
        return int(self._policy.get("resilience", "cooldown_seconds",
                                    default=DEFAULT_COOLDOWN_SECONDS))

    def failure(self, component: str, *, why: str = "",
                clock=None) -> str:
        """Record a failed call. Returns the breaker state afterwards."""
        at = (clock or time.time)()
        breaker = self._breaker(component)
        count = breaker["failure_count"] + 1
        state = breaker["state"]
        opened_at = breaker["opened_at"]
        next_probe = breaker["next_probe"]
        if state != OPEN and count >= self._threshold(component):
            state, opened_at = OPEN, now()
            next_probe = at + self._cooldown(component)
        elif state == HALF_OPEN:
            # The canary failed. Back to OPEN, and the cooldown starts again.
            state, opened_at = OPEN, now()
            next_probe = at + self._cooldown(component)
        self._db.execute(
            "UPDATE circuit_breakers SET state=?, failure_count=?, opened_at=?, "
            "last_failure=?, next_probe=?, why=? WHERE component=?",
            (state, count, opened_at, now(), next_probe, redact(why)[:300],
             component))
        self._db.commit()
        if state == OPEN and breaker["state"] != OPEN:
            self._audit.record(
                event="resilience.breaker_opened", authority=AUTHORITY,
                initiator=AUTHORITY, why=redact(why)[:200],
                decision=component,
                result=f"{count} consecutive failure(s); calls to {component} "
                       "are contained",
                external_effect="none; containment grants no new authority")
        return state

    def success(self, component: str, *, clock=None) -> str:
        """Record a successful call, closing the breaker if one was open."""
        breaker = self._breaker(component)
        if breaker["state"] != CLOSED:
            self._audit.record(
                event="resilience.breaker_closed", authority=AUTHORITY,
                initiator=AUTHORITY, why="a call succeeded",
                decision=component, result=f"{component} is available again")
        self._db.execute(
            "UPDATE circuit_breakers SET state=?, failure_count=0, opened_at='', "
            "next_probe=0, canary_result='', why='' WHERE component=?",
            (CLOSED, component))
        self._db.commit()
        return CLOSED

    def may_call(self, component: str, *, clock=None) -> tuple[bool, str]:
        """May Solvent call this now? Containment only — never a redirection.

        A breaker says *stop hammering this*. It does not say *use something
        else instead*: choosing a different provider, model or destination is a
        permission question, and this module has no way to answer one. That is
        why nothing here returns a fallback.
        """
        at = (clock or time.time)()
        breaker = self._breaker(component)
        if breaker["state"] == CLOSED:
            return True, "closed"
        if breaker["state"] == HALF_OPEN:
            return True, "half-open: one canary call is allowed"
        if at >= (breaker["next_probe"] or 0):
            self._db.execute(
                "UPDATE circuit_breakers SET state=? WHERE component=?",
                (HALF_OPEN, component))
            self._db.commit()
            return True, "cooldown elapsed; one canary call is allowed"
        wait = int((breaker["next_probe"] or 0) - at)
        return False, (f"{component} is contained after "
                       f"{breaker['failure_count']} failure(s); next probe in "
                       f"{wait}s")

    def record_canary(self, component: str, *, passed: bool, detail: str = "",
                      clock=None) -> str:
        """The outcome of the one probe a half-open breaker allows.

        Recovery is bounded on purpose: one safe operation before the full
        workload comes back. Restoring everything the instant a dependency
        answers once is how a flapping service turns into a retry storm.
        """
        self._db.execute(
            "UPDATE circuit_breakers SET canary_result=? WHERE component=?",
            ("PASSED" if passed else "FAILED", component))
        self._db.commit()
        if passed:
            return self.success(component, clock=clock)
        return self.failure(component, why=f"canary failed: {detail}",
                            clock=clock)

    def breaker(self, component: str) -> dict:
        return self._breaker(component)

    def breakers(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM circuit_breakers ORDER BY component")]

    def open_breakers(self) -> list[dict]:
        return [b for b in self.breakers() if b["state"] != CLOSED]
