"""Owner Channel — the authenticated path for consequential approval.

Owns one decision: *did the owner really authorise this specific action?*

Solvent's earlier posture accepted any string beginning ``owner:`` as the owner,
then any string on a policy-held register. Registration is not authentication:
anything running in-process under the registered name was accepted. This module
closes that, and is deliberately the smallest thing that can be trusted rather
than an identity platform.

An approval is a **signed, scoped, single-use, expiring grant**:

* **Signed** with an HMAC over every field that matters, using a key Solvent
  never stores in its own database. Change the job, the action class or the
  ceiling and the signature stops matching.
* **Scoped** to one job, one action class and one amount. Never a session
  elevation, so an approval for a $200 delivery cannot authorise a $9,000 payment.
* **Single-use**, enforced by a recorded nonce. A captured approval cannot be
  replayed.
* **Expiring**, so an approval left lying around stops working.

**No key, no approvals.** If ``SOLVENT_OWNER_KEY`` is absent the channel cannot
verify anything and every consequential approval fails closed. That is the
correct behaviour: an unauthenticated system should not be able to spend money.

**What this is not.** It is a shared-secret scheme. Anything that can read the
key can mint approvals, so the key belongs outside the worker namespace with the
other credentials (see the egress deployment contract). It is honest
authentication of an approval, not proof of a human being.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .audit import AuditLog, now
from .errors import FailClosed
from .store import Store
from .types import ActionClass, Cents, fmt

#: Environment variable holding the owner's signing key.
KEY_ENV = "SOLVENT_OWNER_KEY"

#: The shortest key this channel will accept, in bytes.
#:
#: A signing key is the whole of the authentication. A short one is not a weaker
#: version of this scheme, it is a different scheme: 32 bytes of randomness
#: cannot be guessed, a passphrase someone typed can be, and both satisfy "the
#: variable is set". Refusing here rather than at first use means the failure is
#: a provisioning error that nobody has to notice, instead of a signature scheme
#: that appears to work.
MIN_KEY_BYTES = 32

#: Keys that are obviously a placeholder rather than a secret. Not a security
#: control — anything that gets past MIN_KEY_BYTES is long enough to matter —
#: but a copied-and-pasted example is a realistic provisioning mistake and it
#: should fail loudly at the point it was made.
_PLACEHOLDERS = frozenset({
    "<owner_key>", "owner_key", "changeme", "change-me", "secret", "password",
    "solvent_owner_key", "your-key-here", "todo", "xxx", "test",
})


def key_problem(key: bytes | None) -> str:
    """Why this key must not be used, or ``""``. Never quotes the key itself."""
    if not key:
        return f"no owner signing key: {KEY_ENV} is unset or empty"
    if len(key) < MIN_KEY_BYTES:
        return (f"the owner signing key is {len(key)} bytes; {MIN_KEY_BYTES} is "
                f"the minimum. Generate one with "
                f"`python3 -c \"import secrets; print(secrets.token_hex(32))\"` "
                f"and set {KEY_ENV} from your secret store")
    text = key.decode("utf-8", "replace").strip().strip("\"'").lower()
    if text in _PLACEHOLDERS:
        return (f"{KEY_ENV} is set to a placeholder rather than a secret; "
                "an example value is not a key")
    if len(set(key)) < 4:
        return (f"{KEY_ENV} has almost no variety in it and is not a random "
                "key; generate one rather than typing one")
    for width in range(1, 9):
        if len(key) > width and key == key[:width] * (len(key) // width) + \
                key[:len(key) % width]:
            return (f"{KEY_ENV} is a short value repeated to reach the length "
                    "limit; its strength is the part that repeats")
    return ""


@dataclass(frozen=True, slots=True)
class SignedApproval:
    """One authenticated grant. Every field is covered by the signature."""

    nonce: str
    owner_identity: str
    subject: str
    action_class: ActionClass
    job_id: str
    max_cents: Cents
    expires_at: str
    signature: str
    key_id: str = ""

    def payload(self) -> str:
        """Exactly what is signed. Field order is part of the contract."""
        return "|".join([
            self.nonce, self.owner_identity, self.subject, self.action_class.value,
            self.job_id, str(self.max_cents), self.expires_at,
        ])


class OwnerChannel:
    """Issues and verifies authenticated owner approvals."""

    def __init__(self, store: Store, audit: AuditLog, policy,
                 key: bytes | None = None) -> None:
        self._db = store.for_authority("owner")
        self._audit = audit
        self._policy = policy
        raw = key if key is not None else _key_from_environment()
        #: Why the configured key is unusable, or "". Kept rather than raised at
        #: construction: a Solvent that will not start because a credential is
        #: missing cannot tell the owner what it needs. It starts, refuses every
        #: approval, and says which variable to set.
        self.key_refusal = key_problem(raw)
        self._key = None if self.key_refusal else raw

    @property
    def available(self) -> bool:
        """Whether this channel can authenticate anything at all."""
        return bool(self._key)

    def key_id(self) -> str:
        """A non-secret fingerprint, so audit records say which key was used.

        Derived through HMAC under a fixed public label rather than by hashing
        the key directly. A bare truncated hash of a secret is a target: anyone
        holding an audit row could test candidate keys against it offline. This
        fingerprint cannot be tested against anything without the key.
        """
        if not self._key:
            return ""
        return hmac.new(self._key, b"solvent/key-id/v1",
                        hashlib.sha256).hexdigest()[:12]

    # ---------------------------------------------------------------- issuing

    def issue(self, *, owner_identity: str, subject: str, action_class: ActionClass,
              job_id: str = "", max_cents: Cents = 0,
              ttl_minutes: int = 20) -> SignedApproval:
        """Mint an approval. Only a registered owner may ask, and only with a key.

        In deployment this is called by whatever the owner actually touches — an
        app, a signed CLI on their own machine. Solvent asks; it never answers on
        the owner's behalf.
        """
        if not self.available:
            raise FailClosed(
                f"{self.key_refusal}. Set {KEY_ENV} before issuing approvals: "
                "an unauthenticated system must not be able to approve spending")
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"{owner_identity!r} is not a registered owner identity")

        nonce = secrets.token_hex(16)
        expires = (datetime.now(timezone.utc)
                   + timedelta(minutes=ttl_minutes)).isoformat()
        approval = SignedApproval(
            nonce=nonce, owner_identity=owner_identity, subject=subject,
            action_class=action_class, job_id=job_id, max_cents=max_cents,
            expires_at=expires, signature="", key_id=self.key_id())
        signed = SignedApproval(
            **{**_fields(approval), "signature": self._sign(approval.payload())})

        self._db.execute(
            "INSERT INTO owner_approvals(nonce,issued_at,expires_at,owner_identity,"
            "subject,action_class,job_id,max_cents,key_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (nonce, now(), expires, owner_identity, subject, action_class.value,
             job_id, max_cents, self.key_id()))
        self._db.commit()
        self._audit.record(
            event="owner.approval_issued", authority="owner",
            initiator=owner_identity, why=subject, job_id=job_id or None,
            decision=action_class.value, permission="OWNER_APPROVAL",
            financial_authorization=f"ceiling {fmt(max_cents)}",
            result=f"nonce {nonce[:8]}… expires {expires}", key_id=self.key_id())
        return signed

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    # -------------------------------------------------------------- verifying

    def verify(self, approval: SignedApproval, *, action_class: ActionClass,
               job_id: str, amount_cents: Cents) -> str:
        """Return "" when the approval genuinely authorises this action.

        Checks run strictest-first, and every failure is a refusal with a reason
        rather than a silent downgrade.
        """
        if not self.available:
            return (f"no owner key configured ({KEY_ENV}); consequential approval "
                    "cannot be authenticated")
        if not self._policy.is_owner(approval.owner_identity):
            return f"{approval.owner_identity!r} is not a registered owner identity"

        expected = self._sign(approval.payload())
        # Constant-time: a signature check that leaks timing is not a check.
        if not hmac.compare_digest(expected, approval.signature or ""):
            return "approval signature does not match; it was forged or altered"

        row = self._db.query_one(
            "SELECT * FROM owner_approvals WHERE nonce = ?", (approval.nonce,))
        if row is None:
            return "approval nonce is unknown; it was not issued by this channel"
        if row["used_at"]:
            return f"approval was already used at {row['used_at']} (single-use)"

        if datetime.fromisoformat(approval.expires_at) < datetime.now(timezone.utc):
            return "approval has expired"
        if approval.action_class is not action_class:
            return (f"approval is scoped to {approval.action_class.value}, "
                    f"not {action_class.value}")
        if approval.job_id and approval.job_id != job_id:
            return f"approval is scoped to job {approval.job_id!r}, not {job_id!r}"
        if amount_cents > approval.max_cents:
            return (f"approval ceiling {fmt(approval.max_cents)} exceeded by "
                    f"{fmt(amount_cents)}")
        return ""

    def consume(self, approval: SignedApproval) -> None:
        """Burn the nonce. Called only once an action is actually permitted."""
        self._db.execute(
            "UPDATE owner_approvals SET used_at = ? WHERE nonce = ? AND used_at IS NULL",
            (now(), approval.nonce))
        self._db.commit()
        self._audit.record(
            event="owner.approval_consumed", authority="owner",
            initiator=approval.owner_identity, why=approval.subject,
            job_id=approval.job_id or None, decision=approval.action_class.value,
            result=f"nonce {approval.nonce[:8]}… consumed")

    def outstanding(self) -> list[dict]:
        """Approvals issued and not yet used — what the owner is still owed."""
        return [dict(r) for r in self._db.query(
            "SELECT * FROM owner_approvals WHERE used_at IS NULL ORDER BY issued_at")]


def _key_from_environment() -> bytes | None:
    raw = os.environ.get(KEY_ENV, "")
    return raw.encode() if raw else None


def provisioning_instructions() -> str:
    """What the owner has to do, with nothing secret in it.

    Returned as text so the owner's action packet and the readiness report say
    the same thing, and so neither of them has to contain a key to explain one.
    """
    return (
        f"1. Generate a key on your own machine:\n"
        f"     python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
        f"2. Store it in your host's secret manager. Do not paste it into a "
        f"file in this repository, a commit message, a chat window or a "
        f"support ticket.\n"
        f"3. Expose it to the Solvent process as {KEY_ENV}, outside the worker "
        f"namespace.\n"
        f"4. Rotate it by setting a new value; approvals already issued under "
        f"the old key stop verifying, which is the intended effect.\n"
        f"Solvent never generates, stores or displays this key. With no key it "
        f"runs and refuses every consequential approval.")


def _fields(approval: SignedApproval) -> dict:
    return {name: getattr(approval, name) for name in
            ("nonce", "owner_identity", "subject", "action_class", "job_id",
             "max_cents", "expires_at", "signature", "key_id")}
