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
import re
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
#: A content digest identifies weights; a tag is only a pointer at them.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

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

#: How many times Solvent may fix its own work before it stops and asks. Three
#: is a judgement, not a law: enough for a transient slip, few enough that a
#: capability which genuinely cannot satisfy a requirement surfaces instead of
#: grinding. Overridable at ``execution.max_correction_attempts``.
DEFAULT_MAX_CORRECTIONS = 3


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

    # ------------------------------------------------- recorded owner decisions
    #
    # OD-1, OD-2 and OD-3 are answers only the owner can give, and they live
    # here because Policy already owns *what is permitted*. None of them creates
    # a new authority and none of them activates anything: recording that Stripe
    # is the chosen rail is not the same as Stripe working, and recording that
    # the owner contracts as an individual is not the same as knowing their
    # legal name.

    #: Contracting fields no component may invent. Absent means absent.
    CONTRACTING_FIELDS = ("legal_name", "address", "tax_reference", "email")
    #: What an unprovisioned field reads as. Never a placeholder that could be
    #: mistaken for a value.
    PROVISIONING_REQUIRED = "OWNER_PROVISIONING_REQUIRED"

    INDIVIDUAL = "INDIVIDUAL"
    LLC = "LLC"
    CORPORATION = "CORPORATION"
    PARTNERSHIP = "PARTNERSHIP"
    CONTRACTING_STRUCTURES = (INDIVIDUAL, LLC, CORPORATION, PARTNERSHIP)

    def record_contracting_structure(self, *, owner_identity: str, structure: str,
                                     reason: str, **fields: str) -> int:
        """Record how the owner contracts. Structure now; identity when supplied.

        The decision and the data are separate facts and conflating them is how
        a system ends up inventing a company name to fill a field. ``structure``
        is the owner's answer to *what kind of party is this*; the identity
        fields are theirs to provide and nobody else's to guess, so an omitted
        one stays omitted rather than acquiring a plausible default.
        """
        if structure not in self.CONTRACTING_STRUCTURES:
            raise FailClosed(
                f"{structure!r} is not a contracting structure this records; "
                f"one of {', '.join(self.CONTRACTING_STRUCTURES)}")
        unknown = sorted(set(fields) - set(self.CONTRACTING_FIELDS))
        if unknown:
            raise FailClosed(f"unknown contracting field(s): {unknown}")
        supplied = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
        version = self.amend({"governance": {
            "contracting": {"structure": structure, **supplied},
            # Kept in step for the readiness check, which has always read this.
            "legal_entity": structure,
        }}, owner_identity, reason)
        self._audit.record(
            event="policy.contracting_recorded", authority="policy",
            initiator=owner_identity, why=reason, decision=structure,
            result=f"{len(supplied)} identity field(s) provisioned")
        return version

    def contracting_party(self) -> dict:
        """How Solvent may describe who it acts for. Never a guess.

        Returns the recorded structure, whatever identity fields the owner has
        actually supplied, and an explicit list of the ones they have not. A
        caller that needs a legal name and finds it in ``provisioning_required``
        has to stop — that is the point. There is no code path here that
        produces a name nobody provided.
        """
        record = self.get("governance", "contracting", default=None)
        if not isinstance(record, dict) or not record.get("structure"):
            return {"decided": False, "structure": "",
                    "provisioning_required": list(self.CONTRACTING_FIELDS),
                    "is_separate_legal_entity": False,
                    "may_claim_entity_status": False}
        structure = str(record["structure"])
        supplied = {f: str(record.get(f, "")).strip()
                    for f in self.CONTRACTING_FIELDS
                    if str(record.get(f, "")).strip()}
        separate = structure != self.INDIVIDUAL
        return {
            "decided": True,
            "structure": structure,
            **supplied,
            "provisioning_required": [f for f in self.CONTRACTING_FIELDS
                                      if f not in supplied],
            # An individual is not a company, and Solvent is not a person. The
            # only honest self-description is "software acting for the owner".
            "is_separate_legal_entity": separate,
            "may_claim_entity_status": separate,
        }

    #: Where a rail sits between "the owner picked it" and "money can move".
    RAIL_APPROVED_NOT_ACTIVATED = "APPROVED_BUT_NOT_ACTIVATED"
    RAIL_OPERATIONAL = "OPERATIONAL"

    def approve_payment_rail(self, *, owner_identity: str, rail: str,
                             verification_signal: str, reason: str) -> int:
        """Record the chosen rail. Choosing is not configuring, and neither is
        activating: this writes no credential, no webhook secret and no
        destination, and nothing here makes an external effect possible."""
        if not rail.strip() or not verification_signal.strip():
            raise FailClosed(
                "a rail without a verification signal cannot establish that "
                "money arrived, so PAID would be unreachable")
        version = self.amend({"payment": {
            "approved_rail": rail.strip(),
            "verification_signal": verification_signal.strip(),
            "operational_status": self.RAIL_APPROVED_NOT_ACTIVATED,
        }}, owner_identity, reason)
        self._audit.record(
            event="policy.payment_rail_approved", authority="policy",
            initiator=owner_identity, why=reason, decision=rail.strip(),
            result=f"signal={verification_signal.strip()}; "
                   f"{self.RAIL_APPROVED_NOT_ACTIVATED}")
        return version

    def payment_rail(self) -> dict:
        rail = self.get("payment", "approved_rail", default="")
        return {
            "rail": rail,
            "verification_signal": self.get("payment", "verification_signal",
                                            default=""),
            "operational_status": self.get(
                "payment", "operational_status",
                default=self.RAIL_APPROVED_NOT_ACTIVATED),
            "decided": bool(rail),
        }

    LOCAL = "LOCAL"
    CLOUD = "CLOUD"

    def approve_model_artifact(self, *, owner_identity: str, model: str, tag: str,
                               digest: str, license_id: str, license_source: str,
                               verified_on: str, reason: str,
                               restrictions: str = "none",
                               placement: str = "LOCAL", provider: str = "",
                               commercial_use: bool = True,
                               max_privacy: str = "INTERNAL",
                               cost_per_1k_tokens_cents: int = 0,
                               capabilities: tuple = (),
                               production: bool = True) -> int:
        """Record that one **exact** model artifact is cleared for paid client work.

        Clearance attaches to an artifact, never to a family. Within Qwen2.5, for
        instance, 0.5B/7B/14B/32B are Apache-2.0 while 3B is research-only and 72B
        carries a user-count threshold — so "the Qwen2.5 licence" is not a fact.
        The record is therefore keyed on the content digest, which a tag cannot
        launder, and every evidence field is required: a clearance that does not
        say what was checked, against what source, and when, is a claim rather
        than evidence.

        **Owner path only**, via :meth:`amend`, so no Solvent component — worker,
        Learning, Discovery or a client's text — can write it.
        """
        fields = {"model": model, "tag": tag, "digest": digest,
                  "license_id": license_id, "license_source": license_source,
                  "verified_on": verified_on}
        missing = sorted(k for k, v in fields.items() if not str(v).strip())
        if missing:
            raise FailClosed(
                f"model clearance is missing evidence: {', '.join(missing)}. "
                "An incomplete clearance is not a clearance")
        if not DIGEST_RE.match(digest):
            raise FailClosed(
                f"{digest!r} is not a content digest. Clearance attaches to the "
                "exact weights, not to a name a tag can be repointed at")

        record = {**fields, "restrictions": restrictions,
                  "provider": provider or ("ollama" if placement == self.LOCAL
                                           else ""),
                  "placement": placement,
                  "commercial_use": bool(commercial_use),
                  "max_privacy": max_privacy,
                  "cost_per_1k_tokens_cents": int(cost_per_1k_tokens_cents),
                  "capabilities": sorted(capabilities or ())}
        if placement not in (self.LOCAL, self.CLOUD):
            raise FailClosed(
                f"{placement!r} is neither {self.LOCAL} nor {self.CLOUD}; where "
                "a model runs decides what data may reach it")
        if placement == self.CLOUD and not record["provider"]:
            raise FailClosed(
                "a cloud model must name its provider: approval attaches to who "
                "runs the weights, not only to which weights they are")

        registry = dict(self.get("governance", "approved_models", default={}) or {})
        registry[tag] = record
        patch = {"governance": {"approved_models": registry}}
        if production:
            # The one artifact the deployment is configured to run. Readiness
            # compares this against the live configuration, so it stays a single
            # value even though approval is now a list.
            patch["governance"]["approved_model_artifact"] = record
        version = self.amend(patch, owner_identity, reason)
        self._audit.record(
            event="policy.model_artifact_approved", authority="policy",
            initiator=owner_identity, why=reason, decision="MODEL_CLEARED",
            result=f"{tag} @ {digest} under {license_id} "
                   f"({placement}, commercial={bool(commercial_use)})",
            input_ref=license_source)
        return version

    def approved_models(self) -> dict:
        """Every artifact the owner has cleared, keyed by tag."""
        registry = self.get("governance", "approved_models", default={})
        return dict(registry) if isinstance(registry, dict) else {}

    def model_approval(self, tag: str) -> dict | None:
        """One model's clearance, or ``None``. A family name is not a tag.

        Deliberately an exact-key lookup with no prefix or family matching.
        Within Qwen2.5 the 3B is research-only while the 7B and 14B are
        Apache-2.0, so "it's a Qwen model" answers nothing, and a lookup that
        was willing to guess would answer it wrongly.
        """
        return self.approved_models().get(tag)

    def select_model(self, *, need_capability: str = "", privacy: str = "",
                     max_cost_per_1k_cents: int | None = None,
                     prefer: str = "", exclude: tuple = ()) -> dict:
        """Which cleared model fits this need — or why none does.

        Returns ``{"tag": ..., "approval": {...}}`` on success and
        ``{"tag": "", "refusals": {tag: reason}}`` otherwise. It approves
        nothing: every candidate here was already cleared by the owner, and the
        cost it reports is the *declared* cost for the Financial Governor to
        rule on. Model routing is not a second economics authority, so this
        returns a number and does not spend it.

        Hybrid means local where it fits and approved cloud where it does not.
        It does not mean any local model, and it does not mean any cloud model:
        an unapproved artifact is not a fallback, it is an unapproved artifact.
        """
        from .types import PrivacyClass

        # Ordered by sensitivity: a model cleared for INTERNAL may not be shown
        # CLIENT_CONFIDENTIAL, and an unrecognised class sorts above everything
        # so it cannot slip under a ceiling by being unknown.
        order = {PrivacyClass.PUBLIC.value: 0, PrivacyClass.INTERNAL.value: 1,
                 PrivacyClass.CLIENT_CONFIDENTIAL.value: 2,
                 PrivacyClass.RESTRICTED.value: 3}
        refusals: dict[str, str] = {}
        candidates = []
        for tag, approval in sorted(self.approved_models().items()):
            if tag in exclude:
                refusals[tag] = "excluded by the caller (already tried or failed)"
                continue
            if not approval.get("commercial_use"):
                refusals[tag] = ("commercial use is not established for this "
                                 "artifact")
                continue
            if need_capability and need_capability not in (
                    approval.get("capabilities") or ()):
                refusals[tag] = (f"not demonstrated capable of "
                                 f"{need_capability!r}; a licence is not a skill")
                continue
            if privacy:
                ceiling = approval.get("max_privacy", PrivacyClass.PUBLIC.value)
                if order.get(privacy, 99) > order.get(ceiling, -1):
                    refusals[tag] = (f"may not receive {privacy} data "
                                     f"(ceiling {ceiling})")
                    continue
            cost = int(approval.get("cost_per_1k_tokens_cents", 0))
            if max_cost_per_1k_cents is not None and cost > max_cost_per_1k_cents:
                refusals[tag] = (f"declared cost {cost} exceeds the "
                                 f"{max_cost_per_1k_cents} the caller allowed")
                continue
            candidates.append((tag, approval, cost))

        if not candidates:
            return {"tag": "", "approval": None, "refusals": refusals,
                    "why": ("no cleared model satisfies this need; an unapproved "
                            "model is not a fallback")}

        def rank(item):
            tag, approval, cost = item
            placement = approval.get("placement")
            preferred = 0 if (prefer and placement == prefer) else 1
            return (preferred, cost, tag)

        tag, approval, cost = sorted(candidates, key=rank)[0]
        return {"tag": tag, "approval": approval, "refusals": refusals,
                "declared_cost_per_1k_cents": cost,
                "why": (f"{tag} is cleared for commercial use, "
                        f"{approval.get('placement')}, and fits the stated need; "
                        "the Financial Governor still rules on the spend")}

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
