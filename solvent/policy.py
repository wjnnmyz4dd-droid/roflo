"""Policy Store — the trust root.

Owns one decision: *may Solvent do this class of thing at all?*

Everything that grants authority lives here and nowhere else: permission level
per action class, the margin floor, spend ceilings, calibration bounds, the
egress allowlist, approved pricing sources, and ``operating_mode`` — the kill
switch, which is governance state rather than an agent.

**Only the owner writes.** :meth:`PolicyStore.amend` demands an owner identity
and a reason, appends a new immutable version, and records it. No authority may
call it; ``tests_solvent/test_architecture.py`` asserts that statically, so the
rule holds as the codebase grows rather than only today.

Learning has no path here at all. That is what stops an optimising component
from quietly lowering the standards it is measured against.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .audit import AuditLog, now
from .errors import FailClosed
from .store import Store
from .types import ActionClass, ConsequenceTier, OperatingMode, PermissionLevel

#: Shipped defaults. Deliberately strict: a new business has no track record,
#: so it must find clearly profitable work rather than marginal work.
DEFAULT_POLICY: dict[str, Any] = {
    "operating_mode": OperatingMode.NORMAL.value,
    "permissions": {
        ActionClass.C0_INTERNAL_READ.value: PermissionLevel.AUTONOMOUS.value,
        ActionClass.C1_EXTERNAL_READ.value: PermissionLevel.AUTONOMOUS.value,
        ActionClass.C2_EXTERNAL_COMMUNICATION.value: PermissionLevel.PREAUTHORIZED.value,
        ActionClass.C3_FINANCIAL_COMMITMENT.value: PermissionLevel.OWNER_APPROVAL_REQUIRED.value,
        ActionClass.C4_CREDENTIAL_CONFIG_CHANGE.value: PermissionLevel.OWNER_APPROVAL_REQUIRED.value,
        # Solvent may never change its own authority. Not a threshold — a prohibition.
        ActionClass.C5_AUTHORITY_CHANGE.value: PermissionLevel.PROHIBITED.value,
    },
    "financial": {
        "margin_floor": 0.30,
        "owner_approval_above_cents": 50_000,
        "max_job_spend_cents": 25_000,
        # Bounds the damage of calibration lag: value committed but not yet
        # verified can never exceed this, so an undetected drift is capped.
        "max_committed_unverified_cents": 150_000,
        "anomaly_multiple": 2.0,
    },
    "calibration": {
        "percentile": 0.80,
        "n_min": 5,
        "k_bootstrap": 1.50,
        "max_loosen_per_period": 0.10,
        "window": 50,
        "degradation_threshold": 2.0,
    },
    "pricing": {
        "materiality_fraction": 0.05,
        "max_age_days": {"DEFAULT": 365, "ON_SITE_LABOR": 180, "REMOTE_LABOR": 180,
                         "MATERIALS": 90, "MARKET_RATE": 180, "SALES_TAX": 365,
                         "PREVAILING_WAGE": 365},
        "approved_sources": ["BLS", "OWNER", "LEDGER_ACTUALS", "FIXTURE"],
        "allow_fallback_preliminary": True,
        "owner_rate_overrides": {},
    },
    "verification": {
        "consequence_thresholds_cents": {"C_MED": 25_000, "C_HIGH": 100_000,
                                         "C_CRITICAL": 500_000},
        "budget_fraction": {"C_LOW": 0.05, "C_MED": 0.10, "C_HIGH": 0.15,
                            "C_CRITICAL": 0.25},
    },
    "egress": {"allowlist": [], "simulation_only": True},
    "preauthorizations": [],
}


class PolicyStore:
    """Owner-governed authority. Versioned, append-only, never self-modified."""

    def __init__(self, store: Store, audit: AuditLog) -> None:
        self._db = store.for_authority("policy")
        self._audit = audit
        if self._db.query_one("SELECT version FROM policy_current WHERE id = 1") is None:
            self._write(DEFAULT_POLICY, "system:bootstrap", "initial default policy")

    # -------------------------------------------------------------- writing

    def _write(self, doc: dict, owner_identity: str, reason: str) -> int:
        cur = self._db.execute(
            "INSERT INTO policy_versions(ts,owner_identity,doc,reason) VALUES(?,?,?,?)",
            (now(), owner_identity, json.dumps(doc, sort_keys=True), reason),
        )
        version = int(cur.lastrowid)
        self._db.execute(
            "INSERT INTO policy_current(id,version) VALUES(1,?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version", (version,))
        self._db.commit()
        return version

    def amend(self, patch: dict, owner_identity: str, reason: str) -> int:
        """Apply an owner amendment. **Owner path only.**

        No Solvent authority may call this; that is asserted by an architectural
        test rather than left to discipline.
        """
        if not owner_identity or not owner_identity.startswith("owner:"):
            raise FailClosed(
                "policy may only be amended by an authenticated owner identity "
                f"(got {owner_identity!r})"
            )
        if not reason:
            raise FailClosed("policy amendments require a recorded reason")
        doc = _deep_merge(copy.deepcopy(self.doc()), patch)
        version = self._write(doc, owner_identity, reason)
        self._audit.record(
            event="policy.amended", authority="policy", initiator=owner_identity,
            why=reason, decision=f"version {version}", patch=patch,
        )
        return version

    # -------------------------------------------------------------- reading

    def doc(self) -> dict:
        row = self._db.query_one(
            "SELECT d.doc AS doc FROM policy_current c JOIN policy_versions d "
            "ON d.version = c.version WHERE c.id = 1")
        if row is None:
            raise FailClosed("policy store is unreadable: no authority, no action")
        return json.loads(row["doc"])

    @property
    def version(self) -> int:
        row = self._db.query_one("SELECT version FROM policy_current WHERE id = 1")
        if row is None:
            raise FailClosed("policy store is unreadable: no authority, no action")
        return int(row["version"])

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.doc()
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # ------------------------------------------------------- decision helpers

    @property
    def operating_mode(self) -> OperatingMode:
        return OperatingMode(self.get("operating_mode", default=OperatingMode.HALT.value))

    def set_operating_mode(self, mode: OperatingMode, owner_identity: str,
                           reason: str) -> int:
        """The kill switch. Governance state, enforced at two chokepoints."""
        version = self.amend({"operating_mode": mode.value}, owner_identity, reason)
        if mode is OperatingMode.HALT:
            self._audit.record(
                event="policy.halt", authority="policy", initiator=owner_identity,
                why="HALT abandons in-flight obligations; see abandoned_jobs",
                decision="HALT", result="external effects and spending stopped",
            )
        return version

    def permission_for(self, action_class: ActionClass) -> PermissionLevel:
        """Unknown class fails closed to owner approval, never to autonomy."""
        raw = self.get("permissions", action_class.value)
        if raw is None:
            return PermissionLevel.OWNER_APPROVAL_REQUIRED
        return PermissionLevel(raw)

    def consequence_for(self, value_cents: int) -> ConsequenceTier:
        thresholds = self.get("verification", "consequence_thresholds_cents", default={})
        if value_cents >= thresholds.get("C_CRITICAL", 500_000):
            return ConsequenceTier.C_CRITICAL
        if value_cents >= thresholds.get("C_HIGH", 100_000):
            return ConsequenceTier.C_HIGH
        if value_cents >= thresholds.get("C_MED", 25_000):
            return ConsequenceTier.C_MED
        return ConsequenceTier.C_LOW

    def verification_budget(self, value_cents: int) -> int:
        """What Solvent may spend proving the work is right, given its value."""
        tier = self.consequence_for(value_cents)
        fraction = self.get("verification", "budget_fraction", tier.value, default=0.05)
        return int(value_cents * fraction)

    def preauthorization_for(self, action_class: ActionClass,
                             amount_cents: int) -> dict | None:
        """A bounded standing grant, or None.

        This is the pressure valve that keeps urgency from eroding
        authentication: the owner pre-authorises *bounded classes* in advance,
        rather than approving something unauthenticated under time pressure.
        """
        for grant in self.get("preauthorizations", default=[]):
            if grant.get("action_class") != action_class.value:
                continue
            if amount_cents > int(grant.get("max_cents", 0)):
                continue
            return grant
        return None

    def egress_entry(self, destination: str) -> dict | None:
        """Find the allowlist entry for a destination, or None.

        An entry may cap the privacy class it is allowed to receive, so that
        "this host is approved" never silently means "approved for anything".
        Entries may be plain hostnames or dicts; an empty allowlist permits
        nothing.
        """
        for entry in self.get("egress", "allowlist", default=[]):
            if isinstance(entry, str):
                entry = {"host": entry}
            host = entry.get("host", "")
            if host and (destination == host or destination.endswith("." + host)):
                return entry
        return None

    def egress_allowed(self, destination: str) -> bool:
        """Allowlist membership. Empty allowlist means nothing may leave."""
        return self.egress_entry(destination) is not None


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
