"""The payment provider, simulated. Everything it talks to is production.

The only thing DeskPilot is not allowed to have in a simulated run is a
real payment processor. So this stands in for one, and for nothing
else: it writes a webhook delivery into the spool, signed with the
same scheme the real provider uses, and stops.

What happens next is entirely DeskPilot's. The ``payments`` scheduled
task reads the spool, ``payments.StripeRail`` checks the signature
with a constant-time compare and a replay window, and
``Ledger.apply_payment_event`` decides what the event means for the
books -- refusing an overpayment, a currency mismatch, a job it has
no invoice for, and a second delivery of an event it has already
seen. None of that is reimplemented here, and none of it is bypassed.

**The signature is produced by the production signer.** This calls
``StripeRail._expected``, the private method verification itself
uses, rather than writing HMAC code of its own. A simulated payer
with its own signing implementation would be testing whether two
pieces of code agree, and would keep passing if both drifted the same
way.

**The money is test money, and the Ledger is told so.** Every event
here carries ``livemode: false``, so ``VerifiedPaymentEvent.is_test_money``
is true and the Ledger records the payment with the ``SIMULATED:``
prefix that keeps it out of ``real_revenue_cents``. There is no
argument to this module that can set it true: a run that could
produce something the books counted as real revenue would be a run
that could lie to its owner about being paid.
"""

from __future__ import annotations

import json
import pathlib
import time

from solvent import payments as rail_module

#: The signing secret a simulated run uses. Not a credential: it signs
#: fictional events into a directory this run owns, and the real
#: secret is read from the environment and never stored.
TEST_SECRET = "whsec_simulation_only_not_a_real_endpoint_secret"

#: The environment variable the production tick reads the real secret
#: from. Named here so a run can set it and nothing has to guess.
SECRET_ENV = "SOLVENT_STRIPE_WEBHOOK_SECRET"


def pay(spool, *, job_id: str, amount_cents: int, event_id: str,
        currency: str = "usd", secret: str = TEST_SECRET,
        kind: str = "payment_intent.succeeded") -> pathlib.Path:
    """Write one signed delivery into the spool. Returns the path.

    ``event_id`` is the rail's idempotency key, so a caller that
    passes the same one twice is a provider re-delivering -- which
    the Ledger is supposed to ignore, and which a run should
    exercise rather than avoid.

    Signed with the real clock, not the simulated one. The rail
    rejects a timestamp outside its tolerance as a replay, and that
    check is about the wall clock of the machine doing the
    verifying. A simulated date here would make every payment look
    like a nine-month-old replay.
    """
    spool = pathlib.Path(spool)
    spool.mkdir(parents=True, exist_ok=True)
    body = json.dumps({
        "id": event_id,
        "type": kind,
        "livemode": False,
        "data": {"object": {
            "id": f"pi_{event_id}",
            # The field a real provider uses for this event type.
            # ``_interpret`` reads `amount_received` first for every
            # kind, so one field would work -- and a fixture that
            # does not look like the thing it stands in for stops
            # being evidence about the thing it stands in for.
            **({"amount_refunded": int(amount_cents)}
               if kind == "charge.refunded"
               else {"amount": int(amount_cents)}
               if kind.startswith("charge.dispute")
               else {"amount_received": int(amount_cents)}),
            "currency": currency,
            "status": "succeeded",
            "metadata": {rail_module.JOB_METADATA_KEY: job_id},
        }},
    }, sort_keys=True).encode("utf-8")

    stamp = int(time.time())
    signature = rail_module.StripeRail()._expected(stamp, body, secret)
    headers = {"Stripe-Signature": f"t={stamp},v1={signature}"}

    path = spool / f"{event_id}{rail_module.DELIVERY_SUFFIX}"
    rail_module.write_delivery(path, headers, body)
    return path


def refund(spool, *, job_id: str, amount_cents: int, event_id: str,
           **kw) -> pathlib.Path:
    """The provider says money went back to the client."""
    return pay(spool, job_id=job_id, amount_cents=amount_cents,
               event_id=event_id, kind="charge.refunded", **kw)


def dispute(spool, *, job_id: str, amount_cents: int, event_id: str,
            **kw) -> pathlib.Path:
    """The client has charged back. The money is withdrawn pending outcome."""
    return pay(spool, job_id=job_id, amount_cents=amount_cents,
               event_id=event_id, kind="charge.dispute.created", **kw)


def tamper(spool, *, job_id: str, amount_cents: int, event_id: str) -> pathlib.Path:
    """A delivery signed with the wrong secret. Must be refused.

    Here so a run can show the verification doing its job rather than
    asserting that it would. A simulation that only ever sends valid
    payments proves the happy path and nothing about the control.
    """
    return pay(spool, job_id=job_id, amount_cents=amount_cents,
               event_id=event_id, secret="whsec_not_the_configured_secret")
