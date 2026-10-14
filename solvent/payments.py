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


# ------------------------------------------------------------------ delivery
#
# Verification above needs the raw bytes that were signed. Nothing in Solvent
# could supply them: the egress contract denies ``socket.bind`` for the whole
# interpreter, so this process cannot listen on a port, and no HTTP endpoint
# exists or can be added by listening. ``verify_event`` was therefore complete,
# tested, and unreachable — a payment could never be verified end to end.
#
# The delivery mechanism is a spool directory instead. Something outside this
# process that *is* allowed to accept an HTTP request — a few lines of receiver
# on the host, or ``stripe listen`` piped to a file — writes each delivery into
# the spool, and Solvent reads it. That keeps the boundary where the deployment
# contract already puts it: the thing that touches the network is not the thing
# that holds the books.
#
# The file format exists to protect one property: **the body must arrive as the
# exact bytes Stripe signed.** Re-serialising JSON changes bytes and either
# breaks verification or, far worse, verifies something other than what arrived.
# So the headers go on one line as JSON and everything after the first newline
# is the body, byte for byte, untouched.

#: Files the spool reader will consider. Anything else is left alone, so a
#: half-written file under a temporary name is never read.
DELIVERY_SUFFIX = ".webhook"


def write_delivery(path, headers: dict, raw_body: bytes) -> None:
    """Write one delivery atomically. Used by receivers and by the tests.

    Written to a temporary name and renamed, because a reader must never see a
    half-written delivery: a truncated body fails verification and would be
    recorded as a rejected payment rather than as an incomplete file.
    """
    import json as _json
    import os
    from pathlib import Path as _Path

    target = _Path(path)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(_json.dumps(headers).encode("utf-8") + b"\n" + bytes(raw_body))
    os.replace(partial, target)


def read_delivery(path) -> tuple[dict, bytes]:
    """``(headers, raw_body)`` from one spooled delivery.

    The body is returned exactly as it was stored. Nothing here parses, decodes
    or normalises it — that is the whole reason this function exists rather than
    a JSON envelope with the body as a field.
    """
    from pathlib import Path as _Path

    blob = _Path(path).read_bytes()
    line, separator, body = blob.partition(b"\n")
    if not separator:
        raise FailClosed(
            f"{path}: not a delivery — expected a JSON header line, a newline, "
            "then the raw body")
    try:
        headers = json.loads(line)
    except json.JSONDecodeError as exc:
        raise FailClosed(f"{path}: header line is not JSON: {exc}") from exc
    if not isinstance(headers, dict):
        raise FailClosed(f"{path}: header line is not a JSON object")
    return headers, body


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


# ------------------------------------------------------------------- crypto
#
# Receiving payment in cryptocurrency, with one property above all others:
# **Solvent never holds a key and never sends anything.** It records an
# address the owner supplies, it recognises a confirmation somebody else
# observed on a chain, and that is the whole of its involvement with the
# money. There is no wallet here, no signing, no broadcast, and no code
# path that could acquire any of them -- an address is a string to this
# module, and a string cannot spend.
#
# The confirmation route is the same shape as Stripe's, for the same
# reason: this process cannot listen on a port, because the egress
# contract denies ``socket.bind`` for the whole interpreter. Something
# outside that is allowed to watch a chain writes what it saw into the
# spool, and Solvent reads it. What Solvent will not do is believe it
# without the owner having configured what counts as proof.

#: Networks the owner may nominate, and what each one settles in. This is
#: a list of names, not a claim that any of them is connected: nothing
#: here reaches a chain, and ``CryptoRail`` refuses every event until the
#: owner has configured a verification signal.
#:
#: Deliberately short. Each entry is a network whose address format this
#: module can actually check, because an address it cannot check is an
#: address it must not accept -- a mistyped destination loses the money
#: with no recourse, and there is no "contact support" on a chain.
CRYPTO_NETWORKS: dict[str, tuple[str, ...]] = {
    # Native coin plus the two dollar-denominated tokens most commonly
    # used for invoicing. Which of them the owner can actually receive
    # depends on their own wallet, which is theirs to know.
    "ethereum": ("ETH", "USDC", "USDT"),
    "base": ("ETH", "USDC"),
    "polygon": ("POL", "USDC", "USDT"),
    "bitcoin": ("BTC",),
}

#: Confirmations before a chain payment is treated as settled, by network.
#: Zero is never permitted: an unconfirmed transaction can be replaced,
#: and treating one as revenue is how a reorg becomes a bad debt.
DEFAULT_CONFIRMATIONS: dict[str, int] = {
    "ethereum": 12, "base": 30, "polygon": 60, "bitcoin": 3,
}

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values) -> int:
    generator = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1ffffff) << 5) ^ value
        for i in range(5):
            checksum ^= generator[i] if ((top >> i) & 1) else 0
    return checksum


def _bech32_ok(address: str) -> bool:
    """BIP-173/350 checksum. Implemented rather than approximated.

    A length-and-prefix check would accept a single mistyped character,
    which is exactly the failure this exists to catch: the money goes to
    an address nobody holds and is gone.
    """
    if address != address.lower() and address != address.upper():
        return False
    address = address.lower()
    if address.count("1") == 0:
        return False
    position = address.rfind("1")
    head, tail = address[:position], address[position + 1:]
    if not head or len(tail) < 6:
        return False
    if any(c not in _BECH32_CHARSET for c in tail):
        return False
    expanded = ([ord(c) >> 5 for c in head] + [0]
                + [ord(c) & 31 for c in head]
                + [_BECH32_CHARSET.index(c) for c in tail])
    # 1 is bech32 (v0), 0x2bc830a3 is bech32m (v1+). Either is a valid
    # checksum; which one is correct depends on the witness version.
    return _bech32_polymod(expanded) in (1, 0x2bc830a3)


def address_problem(network: str, address: str) -> str:
    """``""`` if this address is usable on this network, else why not.

    Fails closed on anything it cannot fully verify. A mixed-case EVM
    address carries an EIP-55 checksum that needs Keccak-256, which is
    not Python's sha3_256 and is not in the standard library; rather
    than accept such an address unchecked, this refuses it and asks for
    the unambiguous lower-case form. Refusing a valid address costs the
    owner one retype. Accepting an invalid one costs them the payment.
    """
    network = (network or "").strip().lower()
    address = (address or "").strip()
    if not address:
        return "no address is recorded"
    if network not in CRYPTO_NETWORKS:
        return (f"{network!r} is not a network this records; known: "
                + ", ".join(sorted(CRYPTO_NETWORKS)))
    if network == "bitcoin":
        if address.lower().startswith(("bc1", "tb1")):
            return "" if _bech32_ok(address) else (
                "the bech32 checksum does not match, so this address has a "
                "typo in it somewhere")
        if address[0] in "13" and 26 <= len(address) <= 35:
            return ("this is a legacy base58 address and its checksum is not "
                    "verified here; supply a bech32 address beginning bc1")
        return "this does not look like a Bitcoin address"
    # EVM networks.
    if not address.startswith("0x") or len(address) != 42:
        return "an EVM address is 0x followed by 40 hex characters"
    body = address[2:]
    if any(c not in "0123456789abcdefABCDEF" for c in body):
        return "an EVM address is 0x followed by 40 hex characters"
    if body != body.lower() and body != body.upper():
        return ("this is a mixed-case EIP-55 address and verifying its "
                "checksum needs Keccak-256, which is not available here; "
                "supply the all-lower-case form so nothing is accepted "
                "unchecked")
    return ""


@dataclass(frozen=True, slots=True)
class CryptoReceipt:
    """Where the owner would be paid, and under what conditions.

    ``address`` is empty until the owner fills it in. Empty is the
    shipped state and a working one: everything here refuses while it is
    empty, which is correct, because the alternative to refusing is
    inventing a destination for somebody's money.
    """

    network: str = ""
    asset: str = ""
    address: str = ""
    minimum_confirmations: int = 0

    @property
    def configured(self) -> bool:
        return not self.problem()

    def problem(self) -> str:
        if not self.network and not self.address:
            return "no crypto receipt address is configured"
        bad = address_problem(self.network, self.address)
        if bad:
            return bad
        assets = CRYPTO_NETWORKS.get(self.network.strip().lower(), ())
        if self.asset.strip().upper() not in assets:
            return (f"{self.asset!r} is not recorded as settling on "
                    f"{self.network}; known: " + ", ".join(assets))
        if self.minimum_confirmations < 1:
            return ("a payment with no confirmation requirement is not a "
                    "settled payment; an unconfirmed transaction can be "
                    "replaced")
        return ""


class CryptoRail:
    """Recognises a chain confirmation somebody else observed.

    It does not watch a chain. It cannot: this process may not open a
    socket. An external watcher the owner runs sees the transaction,
    signs what it saw with a secret only the two of them share, and
    writes it into the spool; this checks that signature and turns the
    result into the same ``VerifiedPaymentEvent`` the Stripe rail
    produces, so everything downstream -- the ledger, reconciliation,
    the simulated-money exclusion -- treats both identically.

    Every refusal below is deliberate and none of them is recoverable
    by trying harder:

    * No configured secret: nothing can be verified, so nothing is.
    * A good signature over the wrong receipt address: money that went
      somewhere else is not revenue, however real the transaction.
    * Too few confirmations: a replaceable transaction is not a payment.
    """

    name = "crypto"

    def __init__(self, receipt: CryptoReceipt,
                 tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS) -> None:
        self.receipt = receipt
        self.tolerance_seconds = tolerance_seconds

    def verify_event(self, raw_body: bytes, headers: dict,
                     secret: str) -> VerifiedPaymentEvent:
        import hmac as _hmac
        import json as _json
        from hashlib import sha256

        problem = self.receipt.problem()
        if problem:
            raise FailClosed(
                f"no crypto payment can be verified: {problem}")
        if not secret:
            raise FailClosed(
                "no watcher secret is configured, so a confirmation cannot "
                "be told apart from anything else that wrote to the spool")
        supplied = str(headers.get("X-DeskPilot-Signature", "")).strip()
        if not supplied:
            raise FailClosed("the delivery carries no signature")
        expected = _hmac.new(secret.encode(), raw_body, sha256).hexdigest()
        if not _hmac.compare_digest(expected, supplied):
            raise FailClosed("the delivery's signature does not match")

        try:
            body = _json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise FailClosed(f"the delivery is not readable JSON: {exc}")

        to = str(body.get("to_address", "")).strip()
        if to.lower() != self.receipt.address.strip().lower():
            raise FailClosed(
                "this confirmation is for an address that is not the "
                "recorded receipt address, so it is somebody else's money")
        confirmations = int(body.get("confirmations", 0) or 0)
        if confirmations < self.receipt.minimum_confirmations:
            raise FailClosed(
                f"{confirmations} confirmation(s) is below the "
                f"{self.receipt.minimum_confirmations} this network "
                "requires; an unconfirmed transaction can still be replaced")
        amount_cents = int(body.get("amount_cents", 0) or 0)
        if amount_cents <= 0:
            raise FailClosed(
                "a confirmation carrying no amount in cents establishes no "
                "revenue; the watcher converts at the rate it observed and "
                "says what that was, because this cannot price an asset")
        return VerifiedPaymentEvent(
            event_id=str(body.get("tx_hash", "")),
            rail=self.name,
            kind=EventKind.CUSTOMER_PAID,
            raw_type=str(body.get("type", "chain.confirmation")),
            job_id=str(body.get(JOB_METADATA_KEY, "")),
            amount_cents=amount_cents,
            currency=str(body.get("currency", "usd")).lower(),
            # A real chain, not a testnet. The watcher states which, and
            # testnet coins are not revenue however valid the signature.
            livemode=bool(body.get("livemode", False)),
            external_ref=str(body.get("tx_hash", "")),
            detail={"network": self.receipt.network,
                    "asset": self.receipt.asset,
                    "confirmations": confirmations,
                    "observed_rate": body.get("observed_rate", "")})


#: Auto-conversion states. Off is the shipped state and the safe one.
CONVERSION_OFF = "OFF"
CONVERSION_ADVISORY = "ADVISORY"       # record what a conversion would be
CONVERSION_AUTHORISED = "AUTHORISED"   # the owner has authorised a provider


@dataclass(frozen=True, slots=True)
class ConversionPolicy:
    """Whether crypto received should be turned into dollars, and by whom.

    Solvent cannot convert anything. Conversion means sending an asset
    to an exchange or a payment processor and receiving fiat, which is
    custody and a transfer -- two things this system holds no key for
    and has no path to perform. What this models is the *decision*, so
    that an owner who has arranged conversion elsewhere can record it
    and have the books reflect it.

    ``AUTHORISED`` therefore does not mean Solvent will convert. It
    means the owner has a provider, and that DeskPilot may record a
    conversion the provider performed. The money still moves outside.
    """

    state: str = CONVERSION_OFF
    provider: str = ""
    #: Convert only above this, so dust is not swept on every receipt.
    threshold_cents: Cents = 0

    def problem(self) -> str:
        if self.state == CONVERSION_OFF:
            return ""
        if self.state not in (CONVERSION_ADVISORY, CONVERSION_AUTHORISED):
            return f"{self.state!r} is not a conversion state"
        if self.state == CONVERSION_AUTHORISED and not self.provider.strip():
            return ("conversion cannot be authorised without naming the "
                    "provider that performs it, because Solvent does not")
        return ""

    @property
    def may_record_conversion(self) -> bool:
        return self.state == CONVERSION_AUTHORISED and not self.problem()
