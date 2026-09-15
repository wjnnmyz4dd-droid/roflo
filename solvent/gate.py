"""Action Gate — the single chokepoint for external effects.

Owns one decision: *may this specific external action leave Solvent?*

It decides nothing else. Whether the action is permitted in principle is the
Policy Store's answer; whether the money may be committed is the Financial
Governor's. The Gate asks both and adds the three questions only it can answer:
is the destination approved, is the payload's privacy class acceptable *for that
destination*, and is the operating mode currently permitting external effects.

The asymmetry matters: the Gate may **deny** an action the Governor approved, and
may **never allow** one the Governor denied.

Enforcement has two layers. In-process, :mod:`solvent.egress` denies network
syscalls outright and the Gate is the only caller permitted to open a window. At
deployment, the worker namespace has no route and holds no credentials. This
module is the decision layer; ``docs/solvent-egress-deployment-contract.md`` is
the containment layer, and neither is claimed to be sufficient alone.

**P0 default is simulation.** With an empty allowlist and ``simulation_only``
set, every external effect is recorded and refused transport. Nothing leaves
until the owner approves specific destinations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import egress
from .audit import AuditLog, new_id, now
from .errors import EgressDenied
from .governor import FinancialGovernor
from .policy import PolicyStore
from .store import Store
from .types import ActionClass, Cents, PermissionLevel, PrivacyClass, fmt

#: Ordering used to compare a payload's sensitivity against a destination's cap.
PRIVACY_ORDER = {
    PrivacyClass.PUBLIC: 0, PrivacyClass.INTERNAL: 1,
    PrivacyClass.CLIENT_CONFIDENTIAL: 2, PrivacyClass.RESTRICTED: 3,
}


@dataclass(frozen=True, slots=True)
class OwnerApproval:
    """A scoped, single-use grant carried from an authenticated owner channel.

    Deliberately not a session elevation: an approval names one job, one action
    class and one ceiling. A phone call is a notification, never one of these.
    """

    id: str
    owner_identity: str
    job_id: str
    action_class: ActionClass
    max_cents: Cents
    expires_at: str

    def problem(self, *, job_id: str, action_class: ActionClass,
                amount_cents: Cents) -> str:
        if not self.owner_identity.startswith("owner:"):
            return f"approval identity {self.owner_identity!r} is not an owner"
        if self.job_id != job_id:
            return f"approval is scoped to job {self.job_id!r}, not {job_id!r}"
        if self.action_class is not action_class:
            return (f"approval is scoped to {self.action_class.value}, "
                    f"not {action_class.value}")
        if amount_cents > self.max_cents:
            return (f"approval ceiling {fmt(self.max_cents)} exceeded by "
                    f"{fmt(amount_cents)}")
        if datetime.fromisoformat(self.expires_at) < datetime.now(timezone.utc):
            return "approval has expired"
        return ""


def approval(*, owner_identity: str, job_id: str, action_class: ActionClass,
             max_cents: Cents, ttl_minutes: int = 20) -> OwnerApproval:
    """Mint an approval. In production this comes from the authenticated channel."""
    return OwnerApproval(
        id=new_id("apv"), owner_identity=owner_identity, job_id=job_id,
        action_class=action_class, max_cents=max_cents,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat())


@dataclass(frozen=True, slots=True)
class ActionResult:
    allowed: bool
    reason: str
    request_id: str
    simulated: bool = False
    response: object = None


class ActionGate:
    """The only path to the outside."""

    def __init__(self, store: Store, audit: AuditLog, policy: PolicyStore,
                 governor: FinancialGovernor) -> None:
        self._db = store.for_authority("gate")
        self._audit = audit
        self._policy = policy
        self._governor = governor
        self._used_approvals: set[str] = set()
        egress.install()

    def request(
        self, *, action_class: ActionClass, destination: str, privacy: PrivacyClass,
        purpose: str, job_id: str | None = None, grant_id: str | None = None,
        amount_cents: Cents = 0, initiator: str = "execution",
        approval: OwnerApproval | None = None, perform=None,
    ) -> ActionResult:
        """Evaluate and, if permitted, carry out one external action."""
        request_id = new_id("act")
        allowed, reason, consume = self._evaluate(
            action_class=action_class, destination=destination, privacy=privacy,
            job_id=job_id, grant_id=grant_id, amount_cents=amount_cents,
            approval=approval)
        if allowed and consume is not None:
            # Consumed only on success: a request denied by a *later* check must
            # not burn the owner's approval, or a caller could exhaust approvals
            # by repeatedly failing a downstream condition.
            self._used_approvals.add(consume)

        simulated = False
        response = None
        if allowed:
            if self._policy.get("egress", "simulation_only", default=True):
                simulated = True
                reason = f"{reason}; recorded as SIMULATED (egress.simulation_only)"
            elif perform is not None:
                try:
                    with egress.window(f"gate:{request_id} {purpose}"):
                        response = perform()
                except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
                    self._record(request_id, action_class, destination, privacy,
                                 job_id, grant_id, False,
                                 f"transport failed: {exc}", amount_cents, False,
                                 initiator, purpose)
                    raise

        self._record(request_id, action_class, destination, privacy, job_id, grant_id,
                     allowed, reason, amount_cents, simulated, initiator, purpose)
        return ActionResult(allowed=allowed, reason=reason, request_id=request_id,
                            simulated=simulated, response=response)

    def require(self, **kwargs) -> ActionResult:
        """Act, or raise. For callers that must not proceed on refusal."""
        result = self.request(**kwargs)
        if not result.allowed:
            raise EgressDenied(result.reason)
        return result

    # ---------------------------------------------------------------- checks

    def _evaluate(self, *, action_class, destination, privacy, job_id, grant_id,
                  amount_cents, approval) -> tuple[bool, str, str | None]:
        """Ordered checks. Each answers a question owned by exactly one authority.

        Returns ``(allowed, reason, approval_id_to_consume)``. Validation never
        has side effects; the caller consumes the approval only if the action is
        actually allowed.
        """
        if not action_class.is_external:
            return True, "internal read; no external effect", None

        # Chokepoint two of two for the kill switch.
        mode = self._policy.operating_mode
        if mode.blocks_external:
            return False, f"operating_mode={mode.value} blocks external effects", None

        consume: str | None = None
        level = self._policy.permission_for(action_class)
        if level is PermissionLevel.PROHIBITED:
            return False, f"{action_class.value} is PROHIBITED by policy", None
        if level is PermissionLevel.OWNER_APPROVAL_REQUIRED:
            problem = self._approval_problem(approval, job_id, action_class, amount_cents)
            if problem:
                return False, f"{action_class.value} requires owner approval: {problem}", None
            consume = approval.id
        elif level is PermissionLevel.PREAUTHORIZED:
            if self._policy.preauthorization_for(action_class, amount_cents) is None:
                problem = self._approval_problem(approval, job_id, action_class,
                                                 amount_cents)
                if problem:
                    return False, (f"{action_class.value} exceeds any preauthorization "
                                   f"and {problem}"), None
                consume = approval.id

        entry = self._policy.egress_entry(destination)
        if entry is None:
            return False, (f"destination {destination!r} is not on the egress "
                           "allowlist"), None

        cap = entry.get("max_privacy")
        if cap is not None:
            cap_class = PrivacyClass(cap)
            if PRIVACY_ORDER[privacy] > PRIVACY_ORDER[cap_class]:
                # Privacy overrides cost and convenience, always.
                return False, (f"payload is {privacy.value} but {destination!r} "
                               f"accepts at most {cap_class.value}"), None

        allowed_classes = entry.get("classes")
        if allowed_classes and action_class.value not in allowed_classes:
            return False, (f"{destination!r} does not accept {action_class.value}"), None

        if action_class.spends and amount_cents > 0:
            if not grant_id:
                return False, "spending action has no Financial Governor budget grant", None
            ok, why = self._governor.authorize_spend(
                grant_id=grant_id, amount_cents=amount_cents,
                what=f"{action_class.value} -> {destination}")
            if not ok:
                return False, f"Financial Governor refused: {why}", None

        return True, f"authorized: {action_class.value} -> {destination}", consume

    def _approval_problem(self, approval, job_id, action_class, amount_cents) -> str:
        if approval is None:
            return "no authenticated owner approval supplied"
        if approval.id in self._used_approvals:
            return "approval already used (approvals are single-use)"
        return approval.problem(job_id=job_id or "", action_class=action_class,
                                amount_cents=amount_cents)

    # --------------------------------------------------------------- recording

    def _record(self, request_id, action_class, destination, privacy, job_id,
                grant_id, allowed, reason, amount_cents, simulated, initiator,
                purpose) -> None:
        self._db.execute(
            "INSERT INTO action_requests(id,ts,job_id,action_class,destination,"
            "privacy,grant_id,allowed,reason,cost_cents,simulated) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (request_id, now(), job_id, action_class.value, destination, privacy.value,
             grant_id, 1 if allowed else 0, reason, amount_cents,
             1 if simulated else 0))
        self._db.commit()
        self._audit.record(
            event="gate.allow" if allowed else "gate.deny", authority="gate",
            initiator=initiator, why=purpose, job_id=job_id, input_ref=request_id,
            decision="ALLOW" if allowed else "DENY",
            permission=self._policy.permission_for(action_class).value,
            financial_authorization=grant_id or "",
            external_effect=f"{action_class.value} -> {destination}",
            result=reason, simulated=simulated, privacy=privacy.value)

    def requests_for(self, job_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM action_requests WHERE job_id = ? ORDER BY ts", (job_id,))]
