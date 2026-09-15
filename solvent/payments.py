"""Payment rails — adapters that turn an external signal into a verified fact.

A rail is **not an authority**. It answers one narrow question — *did this
external event really come from the payment provider, and what does it say?* —
and hands the answer to the Ledger, which remains the only authority on what
money moved. There is no Stripe Governor, no Stripe Ledger and no Stripe Policy:
Stripe is a source of externally verifiable facts, nothing more.

What is **not** payment verification, and is refused here:

* a client saying they paid,
* a browser redirected to a success page,
* an agent reporting that payment arrived,
* an unsigned or badly-signed webhook,
* a correctly-signed event for a different job, amount or currency.

The Stripe implementation follows the published scheme: the ``Stripe-Signature``
header carries ``t=<timestamp>`` and one or more ``v1=<signature>`` values; the
signed payload is ``"{timestamp}.{raw_body}"``; the MAC is HMAC-SHA256 keyed with
the endpoint's ``whsec_`` secret; comparison is constant-time; and a timestamp
outside the tolerance is rejected as a replay.

Three details are defences rather than formalities:

* **Only ``v1`` schemes are considered.** Stripe also sends a fake ``v0`` for test
  events; honouring any scheme but ``v1`` is a downgrade attack.
* **An empty secret is refused outright.** A published advisory describes exactly
  this bypass — an unset secret making verification vacuously succeed.
* **The raw body is verified, never a re-serialised object.** Re-encoding JSON
  changes bytes and either breaks verification or, worse, verifies something
  other than what arrived.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Protocol

from .errors import FailClosed
from .types import Cents

#: Stripe's own libraries default to five minutes. Zero disables the check.
DEFAULT_TOLERANCE_SECONDS = 300

#: Metadata key Solvent sets when it creates a payment request, and reads back to
#: associate an event with a job. Without it an event cannot be attributed.
JOB_METADATA_KEY = "solvent_job_id"


class EventKind:
    """What an event means to Solvent, independent of the rail's vocabulary."""

    CUSTOMER_PAID = "CUSTOMER_PAID"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"
    PAYOUT_PAID = "PAYOUT_PAID"
    PAYOUT_FAILED = "PAYOUT_FAILED"
    IGNORED = "IGNORED"


@dataclass(frozen=True, slots=True)
class VerifiedPaymentEvent:
    """A fact established from a cryptographically verified external signal."""

    event_id: str
    rail: str
    kind: str
    raw_type: str
    job_id: str
    amount_cents: Cents
    currency: str
    livemode: bool
    external_ref: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def is_test_money(self) -> bool:
        """Test-mode money is not revenue, however genuine the signature."""
        return not self.livemode


class PaymentRail(Protocol):
    """The smallest interface a payment provider must satisfy."""

    name: str

    def verify_event(self, raw_body: bytes, headers: dict,
                     secret: str) -> VerifiedPaymentEvent:
        """Verify an inbound event, or raise :class:`FailClosed`."""


class StripeRail:
    """Stripe webhook verification. Reads events; never moves money.

    This adapter deliberately cannot create a charge. Creating a payment request
    is an external effect and belongs to the Action Gate; verifying an inbound
    event is a read, and that is all this does.
    """

    name = "stripe"

    #: Stripe event types mapped to what they mean for Solvent's Ledger.
    EVENT_KINDS = {
        "payment_intent.succeeded": EventKind.CUSTOMER_PAID,
        "checkout.session.completed": EventKind.CUSTOMER_PAID,
        "invoice.payment_succeeded": EventKind.CUSTOMER_PAID,
        "charge.refunded": EventKind.REFUNDED,
        "charge.dispute.created": EventKind.DISPUTED,
        "payout.paid": EventKind.PAYOUT_PAID,
        "payout.failed": EventKind.PAYOUT_FAILED,
    }

    def __init__(self, tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS) -> None:
        if tolerance_seconds <= 0:
            # Stripe's own guidance: a tolerance of zero disables the recency
            # check entirely, which removes the replay protection.
            raise FailClosed("webhook tolerance must be positive; 0 disables "
                             "replay protection")
        self.tolerance = tolerance_seconds

    # ------------------------------------------------------------ signature

    @staticmethod
    def parse_signature_header(header: str) -> tuple[int, list[str]]:
        """Split ``t=...,v1=...,v0=...`` into a timestamp and the v1 signatures.

        Schemes other than ``v1`` are discarded. Stripe sends a deliberately fake
        ``v0`` on test events, so accepting any scheme offered would be a
        downgrade attack.
        """
        timestamp = None
        signatures: list[str] = []
        for element in (header or "").split(","):
            prefix, _, value = element.strip().partition("=")
            if prefix == "t":
                try:
                    timestamp = int(value)
                except ValueError:
                    raise FailClosed(f"malformed webhook timestamp {value!r}")
            elif prefix == "v1":
                signatures.append(value)
        if timestamp is None:
            raise FailClosed("webhook signature header carries no timestamp")
        if not signatures:
            raise FailClosed("webhook signature header carries no v1 signature")
        return timestamp, signatures

    def _expected(self, timestamp: int, raw_body: bytes, secret: str) -> str:
        signed_payload = str(timestamp).encode() + b"." + raw_body
        return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    def verify_event(self, raw_body: bytes, headers: dict,
                     secret: str) -> VerifiedPaymentEvent:
        """Verify and interpret one webhook delivery."""
        if not secret:
            # An unset secret making verification vacuously succeed is a known
            # published bypass. Refusing is the only safe reading.
            raise FailClosed(
                "no webhook signing secret configured; an empty secret would make "
                "signature verification vacuously succeed")
        if not isinstance(raw_body, (bytes, bytearray)):
            raise FailClosed(
                "webhook verification needs the raw request body; a re-serialised "
                "object is not the bytes that were signed")

        header = headers.get("Stripe-Signature") or headers.get("stripe-signature")
        if not header:
            raise FailClosed("webhook has no Stripe-Signature header")

        timestamp, signatures = self.parse_signature_header(header)
        expected = self._expected(timestamp, bytes(raw_body), secret)
        if not any(hmac.compare_digest(expected, candidate)
                   for candidate in signatures):
            raise FailClosed(
                "webhook signature does not match; the event was not sent by "
                f"{self.name} or was modified in transit")

        drift = abs(time.time() - timestamp)
        if drift > self.tolerance:
            raise FailClosed(
                f"webhook timestamp is {int(drift)}s old, outside the "
                f"{self.tolerance}s tolerance; treated as a replay")

        return self._interpret(bytes(raw_body))

    # ---------------------------------------------------------- interpreting

    def _interpret(self, raw_body: bytes) -> VerifiedPaymentEvent:
        try:
            event = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise FailClosed(f"webhook body is not JSON: {exc}") from exc

        event_id = event.get("id", "")
        if not event_id:
            raise FailClosed("webhook event has no id; idempotency is impossible")

        raw_type = event.get("type", "")
        kind = self.EVENT_KINDS.get(raw_type, EventKind.IGNORED)
        obj = (event.get("data") or {}).get("object") or {}
        metadata = obj.get("metadata") or {}

        amount = obj.get("amount_received")
        if amount is None:
            amount = obj.get("amount_paid")
        if amount is None:
            amount = obj.get("amount_refunded") if kind == EventKind.REFUNDED else None
        if amount is None:
            amount = obj.get("amount") or obj.get("amount_total") or 0

        return VerifiedPaymentEvent(
            event_id=event_id, rail=self.name, kind=kind, raw_type=raw_type,
            job_id=str(metadata.get(JOB_METADATA_KEY, "")),
            amount_cents=int(amount), currency=str(obj.get("currency", "")).lower(),
            livemode=bool(event.get("livemode", False)),
            external_ref=str(obj.get("id", "")),
            detail={"status": obj.get("status", "")})
