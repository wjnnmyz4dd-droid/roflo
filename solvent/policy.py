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
    # Which identities are the owner. Recorded at bootstrap and changeable only
    # by an identity already on the list, so a component cannot mint one by
    # calling itself "owner:something".
    "governance": {"owner_identities": []},
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
    # Bootstrap is a *preference*, not a prohibition. It nudges ranking toward
    # work that needs no money up front, which matters most before there is an
    # operating reserve. It can never reject a job: the Financial Governor is the
    # only authority that decides whether work is worth doing.
    "bootstrap": {"prefer_low_upfront": True, "upfront_penalty": 0.15},
    "preauthorizations": [],
}


#: The constrained profile for getting from a working simulation to one small
#: real paid job. Every value is configuration, not a hardcoded business rule:
#: the owner sets the numbers, and the profile only decides *which knobs matter*.
#:
#: The restrictions are deliberate. First revenue is not the moment to discover
#: that Solvent will take four jobs at once from three sources.
FIRST_REVENUE_PROFILE: dict[str, Any] = {
    "first_revenue_mode": True,
    "qualification": {
        "max_concurrent_jobs": 1,          # one job at a time, start to finish
    },
    "financial": {
        "max_committed_unverified_cents": 0,   # owner sets the real exposure cap
        "max_job_spend_cents": 0,              # owner sets the real spend cap
    },
    "growth": {
        "autonomous_capability_install": False,
        "autonomous_contract_changes": False,
        "autonomous_source_registration": False,
    },
    "verification": {"mandatory_before_delivery": True},
}


class PolicyStore:
    """Owner-governed authority. Versioned, append-only, never self-modified."""

    def __init__(self, store: Store, audit: AuditLog,
                 owner_identity: str = "owner:root") -> None:
        self._db = store.for_authority("policy")
        self._audit = audit
        if self._db.query_one("SELECT version FROM policy_current WHERE id = 1") is None:
            doc = copy.deepcopy(DEFAULT_POLICY)
            doc["governance"]["owner_identities"] = [owner_identity]
            self._write(doc, "system:bootstrap",
                        f"initial policy; owner is {owner_identity}")

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

    def is_owner(self, identity: str) -> bool:
        """Is this identity a registered owner?

        Membership of a policy-held list, not merely a string that looks like an
        owner. A component cannot promote itself by choosing its own name.

        **Known P0 limitation:** this checks *registration*, not *authentication*.
        Anything running in-process with the registered name is accepted. Binding
        an identity to a key belongs to the authenticated Owner Channel, which is
        P1; until then external execution stays fail-closed. Recorded as OD-5 in
        OWNER_DECISIONS.md.
        """
        if not identity or not identity.startswith("owner:"):
            return False
        registered = self.get("governance", "owner_identities", default=[])
        return identity in registered

    def amend(self, patch: dict, owner_identity: str, reason: str) -> int:
        """Apply an owner amendment. **Owner path only.**

        No Solvent authority may call this; that is asserted by an architectural
        test rather than left to discipline.
        """
        if not self.is_owner(owner_identity):
            raise FailClosed(
                "policy may only be amended by a registered owner identity "
                f"(got {owner_identity!r}); registered owners: "
                f"{self.get('governance', 'owner_identities', default=[])}"
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

    @property
    def first_revenue_mode(self) -> bool:
        return bool(self.get("first_revenue_mode", default=False))

    def enter_first_revenue_mode(self, *, owner_identity: str,
                                 max_job_spend_cents: int,
                                 max_committed_unverified_cents: int,
                                 margin_floor: float, source: str,
                                 capabilities: list[str], reason: str) -> int:
        """Apply the constrained first-revenue profile.

        The caller must supply the money limits: these are business decisions the
        owner owns, so the profile refuses to invent them. Passing zero or a
        negative number is treated as "not decided" rather than "no limit", which
        is the safe reading of an unset financial ceiling.
        """
        if max_job_spend_cents <= 0 or max_committed_unverified_cents <= 0:
            raise FailClosed(
                "first-revenue mode needs real spend limits from the owner; "
                "an unset ceiling is an undecided ceiling, not an unlimited one")
        if not 0.0 < margin_floor < 1.0:
            raise FailClosed(f"margin floor {margin_floor!r} is not a fraction")
        if not source:
            raise FailClosed("first-revenue mode runs against exactly one source")

        patch = copy.deepcopy(FIRST_REVENUE_PROFILE)
        patch["financial"]["max_job_spend_cents"] = max_job_spend_cents
        patch["financial"]["max_committed_unverified_cents"] = \
            max_committed_unverified_cents
        patch["financial"]["margin_floor"] = margin_floor
        patch["discovery"] = {"approved_sources": [source]}
        patch["qualification"] = {**FIRST_REVENUE_PROFILE["qualification"],
                                  "approved_capabilities": list(capabilities)}
        version = self.amend(patch, owner_identity, reason)
        self._audit.record(
            event="policy.first_revenue_mode", authority="policy",
            initiator=owner_identity, why=reason, decision="FIRST_REVENUE_MODE",
            result=f"one source ({source}), one job at a time",
            financial_authorization=f"job cap {max_job_spend_cents} cents")
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
