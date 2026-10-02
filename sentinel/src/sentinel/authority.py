"""Authority model and execution permits.

The authority matrix is data, checked by tests against the code (import graph
and capability handles). Execution only accepts a ``Permit``. A permit can only
be produced by ``PermitIssuer``, which requires three ALLOW-type gate decisions
(compliance, news, risk) plus an arbiter approval for the same intent. The
issuer signs the permit with an HMAC key that only it and the execution service
hold; permits are single-use (enforced in the journal) and expire quickly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from sentinel.contracts import (
    ArbiterDecision,
    ArbiterVerdict,
    GateDecision,
    GateVerdict,
    OrderIntent,
    canonical_json,
    content_hash,
)

# Capabilities: P=propose trade, V=validate, A=arbitrate, B=block, S=size, X=execute,
# C=close, L=learn, PC=propose change, PR=promote change.
AUTHORITY_MATRIX: dict[str, set[str]] = {
    "finder": {"P"},
    "validator": {"V"},
    "arbiter": {"A"},
    "news": {"B"},
    "compliance": {"B", "C"},  # may demand flattening (close), never open
    "risk": {"B", "S", "C"},  # may reduce size / halt / demand flatten, never open
    "execution": {"X", "C"},  # executes only permitted intents / authorised closes
    "reconciler": {"B"},  # halts on unknown state; never trades
    "learning": {"L", "PC"},
    "promotion": {"PR"},  # requires a human approval record; see promotion.py
    "orchestrator": set(),  # routes messages only
}

# Modules that must never import broker access or the permit issuer.
NO_BROKER_ACCESS = ["finder", "validator", "arbiter", "news", "compliance", "risk", "learning", "promotion", "independence"]


class PermitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Permit:
    permit_id: str
    intent: OrderIntent
    intent_hash: str
    decisions_hash: str
    issued_at: int
    expires_at: int
    signature: str


def _sig(key: bytes, body: str) -> str:
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def _permit_body(permit_id: str, intent_hash: str, decisions_hash: str, issued_at: int, expires_at: int) -> str:
    return canonical_json([permit_id, intent_hash, decisions_hash, issued_at, expires_at])


class PermitKey:
    """Process-local secret shared by issuer and execution. Never logged or persisted."""

    def __init__(self, key: bytes | None = None):
        self._key = key or os.urandom(32)

    def sign(self, body: str) -> str:
        return _sig(self._key, body)

    def check(self, body: str, sig: str) -> bool:
        return hmac.compare_digest(_sig(self._key, body), sig)


class DecisionSigner:
    """Held by exactly one authority. The issuer verifies each decision's origin."""

    def __init__(self, authority: str, key: PermitKey | None = None):
        self.authority = authority
        self.key = key or PermitKey()

    def sign(self, decision):
        import dataclasses

        body = self.authority + "|" + content_hash(dataclasses.replace(decision, signature=""))
        return dataclasses.replace(decision, signature=self.key.sign(body))


def decision_signature_ok(key: PermitKey, authority: str, decision) -> bool:
    import dataclasses

    if not decision.signature:
        return False
    body = authority + "|" + content_hash(dataclasses.replace(decision, signature=""))
    return key.check(body, decision.signature)


class PermitIssuer:
    TTL_SECONDS = 30

    def __init__(self, key: PermitKey, verify_keys: dict[str, PermitKey]):
        self._key = key
        missing = {"arbiter", "compliance", "news", "risk"} - set(verify_keys)
        if missing:
            raise PermitError(f"issuer lacks verification keys for {sorted(missing)}")
        self._verify = verify_keys

    def issue(
        self,
        intent: OrderIntent,
        arbiter: ArbiterDecision,
        compliance: GateDecision,
        news: GateDecision,
        risk: GateDecision,
        now: int,
    ) -> Permit:
        for name, d in (("arbiter", arbiter), ("compliance", compliance), ("news", news), ("risk", risk)):
            if not decision_signature_ok(self._verify[name], name, d):
                raise PermitError(f"{name} decision signature invalid or missing (forged/altered decision)")
        for d in (arbiter, compliance, news, risk):
            if now - d.created_at > self.TTL_SECONDS or d.created_at > now + 5:
                raise PermitError("gate decision is stale")
        if arbiter.candidate_id != intent.candidate_id or arbiter.verdict is not ArbiterVerdict.APPROVE_FOR_RISK_REVIEW:
            raise PermitError("arbiter approval missing for this candidate")
        for d, name in ((compliance, "compliance"), (news, "news"), (risk, "risk")):
            if d.authority != name:
                raise PermitError(f"decision from wrong authority: {d.authority} != {name}")
            if d.verdict not in (GateVerdict.ALLOW, GateVerdict.REDUCE):
                raise PermitError(f"{name} did not allow: {d.verdict}")
        for name, d in (("compliance", compliance), ("news", news), ("risk", risk)):
            if d.subject != intent.candidate_id:
                raise PermitError(f"{name} decision is about {d.subject!r}, not this candidate")
        if risk.detail.get("instrument") != intent.instrument or news.detail.get("instrument") != intent.instrument \
                or compliance.detail.get("instrument") != intent.instrument:
            raise PermitError("a gate decision was made for a different instrument")
        if risk.detail.get("direction") != intent.direction.value or risk.detail.get("stop") != intent.stop_loss \
                or compliance.detail.get("stop") != intent.stop_loss:
            raise PermitError("intent direction/stop differ from what risk and compliance evaluated")
        if compliance.detail.get("volume", -1) + 1e-12 < intent.volume:
            raise PermitError("intent volume exceeds the volume compliance evaluated")
        if risk.approved_volume is None or intent.volume > risk.approved_volume + 1e-12:
            raise PermitError("intent volume exceeds risk-approved volume")
        if compliance.verdict is GateVerdict.REDUCE or news.verdict is GateVerdict.REDUCE:
            raise PermitError("only risk may reduce")
        intent_hash = content_hash(intent)
        decisions_hash = content_hash([arbiter, compliance, news, risk])
        permit_id = "permit-" + hashlib.sha256(f"{intent.intent_id}|{intent_hash}".encode()).hexdigest()[:32]
        expires = min(now + self.TTL_SECONDS, intent.expires_at)
        body = _permit_body(permit_id, intent_hash, decisions_hash, now, expires)
        return Permit(permit_id, intent, intent_hash, decisions_hash, now, expires, self._key.sign(body))


def verify_permit(key: PermitKey, permit: Permit, now: int) -> None:
    if content_hash(permit.intent) != permit.intent_hash:
        raise PermitError("intent altered after permit issue")
    body = _permit_body(permit.permit_id, permit.intent_hash, permit.decisions_hash, permit.issued_at, permit.expires_at)
    if not key.check(body, permit.signature):
        raise PermitError("bad permit signature")
    if now > permit.expires_at:
        raise PermitError("permit expired")
