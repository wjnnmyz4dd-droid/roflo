"""Self-diagnostics: does Solvent know when something is wrong?

**Diagnosis is not authority.** Every function here reads and classifies. None
of them repairs, permits, promotes, spends or halts. A finding names a
*recommended response*, and whether that response may be carried out is decided
by the authority that already owns it — Policy for rules, the Governor for
money, the Action Gate for external effects, the owner for anything else. A
diagnostic layer that could act on its own findings would be a second governor
whose only qualification is noticing things.

Two rules shape the classification, and both exist because the opposite is the
comfortable default:

**UNKNOWN is not HEALTHY.** A check that could not run has not passed. The
tempting shortcut — treat an exception in a health check as "probably fine" —
turns the diagnostic layer into a machine for producing green lights.

**UNRECORDED is not SAFE.** A control nobody has confirmed is a control nobody
has. The network posture is the case that matters most: a firewall script
existing in the repository is not a firewall running on the host, and the only
honest reading of "nobody recorded whether it was applied" is that it was not.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import resilience as res
from .branding import PRODUCT

#: How a check came out. Ordered worst-last so a summary can take the maximum.
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
WARNING = "WARNING"
UNKNOWN = "UNKNOWN"
OWNER_ACTION_REQUIRED = "OWNER_ACTION_REQUIRED"
FAILED = "FAILED"
UNSAFE = "UNSAFE"

#: Severity order. UNKNOWN sits above HEALTHY deliberately: it is not good news.
_RANK = {HEALTHY: 0, DEGRADED: 1, WARNING: 2, UNKNOWN: 3,
         OWNER_ACTION_REQUIRED: 4, FAILED: 5, UNSAFE: 6}

STATES = tuple(sorted(_RANK, key=_RANK.get))

#: What should happen about a finding. Naming it is all this module does.
OBSERVE = "OBSERVE_ONLY"
SAFE_RECOVERY = "SAFE_AUTO_RECOVERY"
CONTAIN = "CONTAIN"
RETRY_LATER = "RETRY_LATER"
CIRCUIT_BREAK = "CIRCUIT_BREAK"
BLOCK_WORKFLOW = "BLOCK_WORKFLOW"
OWNER_ESCALATION = "OWNER_ESCALATION"
HALT_REQUIRED = "HALT_REQUIRED"

RESPONSES = (OBSERVE, SAFE_RECOVERY, CONTAIN, RETRY_LATER, CIRCUIT_BREAK,
             BLOCK_WORKFLOW, OWNER_ESCALATION, HALT_REQUIRED)

#: A backup older than this is stale enough to say so.
BACKUP_STALE_HOURS = 26
#: Disk and memory headroom below which Solvent is in trouble.
DISK_WARN_PERCENT = 85
DISK_FAIL_PERCENT = 95


@dataclass(frozen=True, slots=True)
class Finding:
    """One check, in plain English first."""

    name: str
    state: str
    detail: str
    response: str = OBSERVE
    category: str = "system"
    #: What the owner would have to do. Empty when it is not theirs to do.
    owner_action: str = ""

    @property
    def healthy(self) -> bool:
        return self.state == HEALTHY

    @property
    def needs_owner(self) -> bool:
        return bool(self.owner_action) or self.response == OWNER_ESCALATION

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "detail": self.detail,
                "response": self.response, "category": self.category,
                "owner_action": self.owner_action}


@dataclass
class Report:
    findings: list = field(default_factory=list)

    @property
    def worst(self) -> str:
        if not self.findings:
            return UNKNOWN
        return max((f.state for f in self.findings), key=lambda s: _RANK[s])

    @property
    def failing(self) -> list:
        return [f for f in self.findings if f.state != HEALTHY]

    @property
    def owner_actions(self) -> list:
        return [f for f in self.findings if f.needs_owner]

    def by_category(self) -> dict:
        out: dict = {}
        for finding in self.findings:
            out.setdefault(finding.category, []).append(finding)
        return out

    def to_dict(self) -> dict:
        return {"worst": self.worst,
                "findings": [f.to_dict() for f in self.findings]}


def _checked(name, category, response_on_failure=OBSERVE):
    """Wrap a check so a raising check reads UNKNOWN rather than disappearing.

    A health check that throws is the one case where silence is most dangerous
    and most likely: the surrounding code is already in trouble. Swallowing the
    exception and reporting nothing leaves a gap in the report that reads
    exactly like a passing check.
    """
    def wrap(fn):
        def run(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - deliberate: see docstring
                return Finding(
                    name=name, state=UNKNOWN, category=category,
                    detail=f"the check could not run: {res.redact(str(exc))[:200]}",
                    response=response_on_failure)
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run
    return wrap


class Diagnostics:
    """Reads every authority. Commands none of them."""

    def __init__(self, solvent, *, started_at: float | None = None) -> None:
        self._s = solvent
        self._started_at = started_at if started_at is not None else time.time()

    # --------------------------------------------------------------- the run

    def run(self) -> Report:
        """Every check, in one pass. Order is display order, not priority."""
        return Report(findings=[
            self.database(), self.audit_chain(), self.ledger_integrity(),
            self.capability_registry(), self.verifiers(), self.jobs(),
            self.customer_service(), self.incidents(), self.breakers(),
            self.network_posture(), self.egress(), self.model(),
            self.payment_ingress(), self.work_sources(), self.owner_key(),
            self.notifications(), self.disk(), self.backups(),
        ])

    # ---------------------------------------------------------------- checks

    @_checked("Database", "storage", BLOCK_WORKFLOW)
    def database(self) -> Finding:
        rows = self._s.store.raw_readonly("PRAGMA integrity_check")
        verdict = rows[0][0] if rows else "unknown"
        if verdict != "ok":
            return Finding("Database", FAILED, f"integrity check says {verdict}",
                           BLOCK_WORKFLOW, "storage",
                           owner_action="restore from the most recent backup")
        return Finding("Database", HEALTHY, "integrity check passes", OBSERVE,
                       "storage")

    @_checked("Audit chain", "evidence", HALT_REQUIRED)
    def audit_chain(self) -> Finding:
        ok, detail = self._s.audit.verify_chain()
        if not ok:
            return Finding(
                "Audit chain", FAILED,
                f"the hash chain does not verify: {detail}", HALT_REQUIRED,
                "evidence",
                owner_action="stop work and investigate; the record of what "
                             "happened can no longer be trusted")
        return Finding("Audit chain", HEALTHY, detail, OBSERVE, "evidence")

    @_checked("Ledger", "money", OWNER_ESCALATION)
    def ledger_integrity(self) -> Finding:
        real = self._s.ledger.real_revenue_cents()
        simulated = self._s.policy.get("egress", "simulation_only", default=True)
        if simulated and real:
            return Finding(
                "Ledger", FAILED,
                f"simulation is on and yet {real} cent(s) of real revenue are "
                "recorded; one of those two is wrong", OWNER_ESCALATION, "money",
                owner_action="reconcile the ledger before trusting any total")
        return Finding("Ledger", HEALTHY,
                       f"real revenue {real} cent(s); simulation "
                       f"{'on' if simulated else 'off'}", OBSERVE, "money")

    @_checked("Capabilities", "work", BLOCK_WORKFLOW)
    def capability_registry(self) -> Finding:
        proven = [c for c in self._s.capability.capabilities() if c.proven]
        if not proven:
            return Finding(
                "Capabilities", OWNER_ACTION_REQUIRED,
                "nothing is registered as proven, so no work can be delivered",
                BLOCK_WORKFLOW, "work",
                owner_action="run `solvent setup capability`")
        deliverable = [c.name for c in proven
                       if self._s.capability.may_deploy(c.name)[0]]
        if not deliverable:
            return Finding(
                "Capabilities", OWNER_ACTION_REQUIRED,
                f"{len(proven)} proven capability(ies), none cleared to deliver",
                BLOCK_WORKFLOW, "work",
                owner_action="record the owner decision that clears one")
        return Finding("Capabilities", HEALTHY,
                       f"{len(deliverable)} deliverable: {', '.join(sorted(deliverable))}",
                       OBSERVE, "work")

    @_checked("Verifiers", "evidence", BLOCK_WORKFLOW)
    def verifiers(self) -> Finding:
        rows = self._s.store.raw_readonly(
            "SELECT verifier_ref, capability_version, state FROM "
            "verifier_certifications ORDER BY ts")
        latest: dict = {}
        for row in rows:
            latest[(row["verifier_ref"], row["capability_version"])] = row["state"]
        revoked = [k for k, v in latest.items() if v == "REVOKED"]
        certified = [k for k, v in latest.items() if v == "CERTIFIED"]
        if not latest:
            return Finding("Verifiers", UNKNOWN,
                           "no verifier has been certified in this database",
                           BLOCK_WORKFLOW, "evidence")
        if revoked:
            return Finding(
                "Verifiers", WARNING,
                f"{len(revoked)} revoked, {len(certified)} certified; work "
                "verified by a revoked verifier cannot be delivered",
                BLOCK_WORKFLOW, "evidence")
        return Finding("Verifiers", HEALTHY, f"{len(certified)} certified",
                       OBSERVE, "evidence")

    @_checked("Jobs", "work", OWNER_ESCALATION)
    def jobs(self) -> Finding:
        blocked = [j for j in self._s.orchestrator.jobs()
                   if j.blocked_on is not None]
        if blocked:
            return Finding(
                "Jobs", WARNING,
                f"{len(blocked)} job(s) blocked and waiting on something",
                OWNER_ESCALATION, "work",
                owner_action="check the blocked jobs on the Jobs page")
        return Finding("Jobs", HEALTHY,
                       f"{len(self._s.orchestrator.jobs())} job(s), none blocked",
                       OBSERVE, "work")

    @_checked("Customer service", "clients", OWNER_ESCALATION)
    def customer_service(self) -> Finding:
        escalations = self._s.relations.escalations()
        waiting = [e for e in escalations if e["phase"] == "PREPARED"]
        if waiting:
            return Finding(
                "Customer service", OWNER_ACTION_REQUIRED,
                f"{len(waiting)} client message(s) waiting on the owner",
                OWNER_ESCALATION, "clients",
                owner_action="read them on the Customer service page")
        return Finding("Customer service", HEALTHY,
                       "nothing is waiting on a person", OBSERVE, "clients")

    @_checked("Incidents", "resilience", OWNER_ESCALATION)
    def incidents(self) -> Finding:
        open_ = self._s.resilience.incidents(open_only=True)
        owner = [i for i in open_ if i["status"] == res.OWNER_ACTION_REQUIRED]
        if owner:
            return Finding("Incidents", OWNER_ACTION_REQUIRED,
                           f"{len(owner)} incident(s) need the owner",
                           OWNER_ESCALATION, "resilience",
                           owner_action="read them on the Incidents page")
        if open_:
            return Finding("Incidents", DEGRADED,
                           f"{len(open_)} open incident(s)", OBSERVE,
                           "resilience")
        return Finding("Incidents", HEALTHY, "no open incidents", OBSERVE,
                       "resilience")

    @_checked("Circuit breakers", "resilience", CONTAIN)
    def breakers(self) -> Finding:
        open_ = self._s.resilience.open_breakers()
        if open_:
            names = ", ".join(sorted(b["component"] for b in open_))
            return Finding("Circuit breakers", DEGRADED,
                           f"contained: {names}", CONTAIN, "resilience")
        return Finding("Circuit breakers", HEALTHY, "all closed", OBSERVE,
                       "resilience")

    @_checked("Network firewall", "security", OWNER_ESCALATION)
    def network_posture(self) -> Finding:
        posture = self._s.policy.network_posture()
        default_deny = posture.get("default_deny")
        if default_deny is None:
            return Finding(
                "Network firewall", UNSAFE,
                "nobody has recorded whether a default-deny firewall is "
                "applied on this host. A script in the repository is not a "
                "firewall on the host, so the only honest reading is that "
                "there is not one", OWNER_ESCALATION, "security",
                owner_action="run deploy/firewall.sh --apply, then record it "
                             "with `solvent setup firewall --default-deny`")
        if not default_deny:
            return Finding("Network firewall", UNSAFE,
                           "recorded as not default-deny", OWNER_ESCALATION,
                           "security",
                           owner_action="apply a default-deny policy")
        return Finding("Network firewall", HEALTHY,
                       "recorded as default-deny", OBSERVE, "security")

    @_checked("Egress", "security", OBSERVE)
    def egress(self) -> Finding:
        simulation = self._s.policy.get("egress", "simulation_only", default=True)
        allowlist = self._s.policy.get("egress", "allowlist", default=[]) or []
        if simulation:
            return Finding("Egress", HEALTHY,
                           f"simulation only; nothing leaves this machine "
                           f"({len(allowlist)} destination(s) would be allowed)",
                           OBSERVE, "security")
        if not allowlist:
            return Finding("Egress", UNSAFE,
                           "simulation is off and the allowlist is empty",
                           BLOCK_WORKFLOW, "security")
        return Finding("Egress", WARNING,
                       f"live; {len(allowlist)} destination(s) allowed",
                       OBSERVE, "security")

    @_checked("AI model", "work", OWNER_ESCALATION)
    def model(self) -> Finding:
        approved = self._s.policy.approved_models()
        if not approved:
            return Finding("AI model", OWNER_ACTION_REQUIRED,
                           "no model artifact is cleared", BLOCK_WORKFLOW,
                           "work",
                           owner_action="run `solvent setup model`")
        return Finding("AI model", HEALTHY,
                       f"{len(approved)} cleared artifact(s)", OBSERVE, "work")

    @_checked("Payment ingress", "money", OBSERVE)
    def payment_ingress(self) -> Finding:
        rail = self._s.policy.payment_rail()
        status = rail.get("operational_status", "")
        if status == "LIVE_VERIFIED":
            return Finding("Payment ingress", HEALTHY,
                           "the rail is live and verified", OBSERVE, "money")
        return Finding(
            "Payment ingress", OWNER_ACTION_REQUIRED,
            f"the rail is at {status or 'nothing chosen'}; money cannot be "
            "verified yet. Not required for a first supervised trial",
            OBSERVE, "money",
            owner_action="advance the rail with `solvent setup stripe`")

    @_checked("Work sources", "work", OBSERVE)
    def work_sources(self) -> Finding:
        sources = self._s.discovery.sources()
        if not sources:
            return Finding("Work sources", OWNER_ACTION_REQUIRED,
                           "none registered; work is hand-fed", OBSERVE, "work",
                           owner_action="register one when you want discovery")
        return Finding("Work sources", HEALTHY, f"{len(sources)} registered",
                       OBSERVE, "work")

    @_checked("Owner key", "security", BLOCK_WORKFLOW)
    def owner_key(self) -> Finding:
        if self._s.owner.available:
            return Finding("Owner key", HEALTHY,
                           f"configured (id {self._s.owner.key_id()})", OBSERVE,
                           "security")
        return Finding(
            "Owner key", OWNER_ACTION_REQUIRED,
            "no signing key is configured, so no consequential action can be "
            "approved", BLOCK_WORKFLOW, "security",
            owner_action="generate one and set SOLVENT_OWNER_KEY")

    @_checked("Notifications", "alerting", OBSERVE)
    def notifications(self) -> Finding:
        from . import notify

        state = notify.configuration(self._s.policy)
        failures = self._s.notifier.delivery_failures()
        if not state["owner_phone"]:
            return Finding("Notifications", OWNER_ACTION_REQUIRED,
                           f"no owner phone recorded; {PRODUCT} cannot reach "
                           "you away from the website", OBSERVE, "alerting",
                           owner_action="run `solvent setup notifications`")
        if not state["sms_provider"]:
            return Finding("Notifications", DEGRADED,
                           "a phone is recorded but no SMS provider is "
                           "configured, so nothing can be sent", OBSERVE,
                           "alerting",
                           owner_action="configure an SMS provider")
        if failures:
            return Finding(
                "Notifications", DEGRADED,
                f"{len(failures)} notification(s) a channel could not carry; "
                "the configuration is in place but something is not working",
                OWNER_ESCALATION, "alerting",
                owner_action="check the Notifications page and the provider")
        return Finding("Notifications", HEALTHY,
                       f"SMS via {state['sms_provider']}"
                       + (f", voice via {state['voice_provider']}"
                          if state["voice_provider"] else ", no voice provider"),
                       OBSERVE, "alerting")

    @_checked("Disk", "system", OWNER_ESCALATION)
    def disk(self) -> Finding:
        usage = shutil.disk_usage(self._root())
        percent = round(100 * usage.used / usage.total, 1)
        free_gb = round(usage.free / (1024 ** 3), 2)
        if percent >= DISK_FAIL_PERCENT:
            return Finding("Disk", FAILED,
                           f"{percent}% used, {free_gb} GB free",
                           BLOCK_WORKFLOW, "system",
                           owner_action="free space before accepting work")
        if percent >= DISK_WARN_PERCENT:
            return Finding("Disk", WARNING, f"{percent}% used, {free_gb} GB free",
                           OWNER_ESCALATION, "system")
        return Finding("Disk", HEALTHY, f"{percent}% used, {free_gb} GB free",
                       OBSERVE, "system")

    @_checked("Backups", "storage", OWNER_ESCALATION)
    def backups(self) -> Finding:
        directory = Path(self._s.policy.get("backup", "directory", default="")
                         or "/var/lib/solvent/backups")
        if not directory.is_dir():
            return Finding(
                "Backups", UNKNOWN,
                f"no backup directory at {directory}; nothing can be said about "
                "whether a restore would work", OWNER_ESCALATION, "storage",
                owner_action="configure and test a backup")
        newest = max((p.stat().st_mtime for p in directory.glob("*")), default=0)
        if not newest:
            return Finding("Backups", UNKNOWN, f"{directory} is empty",
                           OWNER_ESCALATION, "storage",
                           owner_action="take and test a backup")
        age_hours = round((time.time() - newest) / 3600, 1)
        if age_hours > BACKUP_STALE_HOURS:
            return Finding("Backups", WARNING,
                           f"the newest backup is {age_hours}h old",
                           OWNER_ESCALATION, "storage")
        return Finding("Backups", HEALTHY, f"newest is {age_hours}h old",
                       OBSERVE, "storage")

    # -------------------------------------------------------- host metrics

    def _root(self) -> str:
        path = getattr(self._s.store, "path", ":memory:")
        if path == ":memory:":
            return os.getcwd()
        return str(Path(path).parent)

    def host(self) -> dict:
        """Operational numbers, with no opinion attached.

        Metrics are not a diagnosis and not an authority. Everything here is
        read and reported; the checks above are where a number becomes a state.
        """
        usage = shutil.disk_usage(self._root())
        path = getattr(self._s.store, "path", ":memory:")
        database_bytes = 0
        wal_bytes = 0
        if path != ":memory:":
            for suffix, name in (("", "db"), ("-wal", "wal"), ("-shm", "shm")):
                candidate = Path(str(path) + suffix)
                if candidate.exists():
                    size = candidate.stat().st_size
                    database_bytes += size
                    if name == "wal":
                        wal_bytes = size
        return {
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "started_at": self._started_at,
            "disk_total_bytes": usage.total,
            "disk_used_bytes": usage.used,
            "disk_free_bytes": usage.free,
            "disk_used_percent": round(100 * usage.used / usage.total, 1),
            "memory": _memory(),
            "load_average": _load_average(),
            "database_path": path,
            "database_bytes": database_bytes,
            "wal_bytes": wal_bytes,
            "audit_events": len(self._s.audit.events()),
        }


def _memory() -> dict:
    """Memory, where the host will say. ``{}`` where it will not.

    Reported as absent rather than guessed. A made-up memory figure is worse
    than no memory figure, because somebody would act on it.
    """
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            fields[key] = int(rest.strip().split()[0]) * 1024
        total = fields.get("MemTotal", 0)
        available = fields.get("MemAvailable", 0)
        if not total:
            return {}
        return {"total_bytes": total, "available_bytes": available,
                "used_percent": round(100 * (total - available) / total, 1)}
    except (OSError, ValueError, IndexError):
        return {}


def _load_average() -> list:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return []
