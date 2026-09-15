"""Payment rail: only a cryptographically verified external signal is money."""

import hashlib
import hmac
import json
import time
import unittest

from solvent.errors import FailClosed
from solvent.payments import (
    DEFAULT_TOLERANCE_SECONDS, EventKind, JOB_METADATA_KEY, StripeRail,
)
from solvent.types import PaymentState, PayoutState, money
from tests_solvent.fixtures import Rig

SECRET = "whsec_test_secret"


def body(event_id="evt_1", event_type="payment_intent.succeeded", amount=45_000,
         currency="usd", job="J1", livemode=True):
    return json.dumps({
        "id": event_id, "type": event_type, "livemode": livemode,
        "data": {"object": {"id": "pi_123", "amount_received": amount,
                            "amount_refunded": amount, "amount": amount,
                            "currency": currency, "status": "succeeded",
                            "metadata": {JOB_METADATA_KEY: job}}},
    }).encode()


def headers(raw, *, secret=SECRET, timestamp=None, scheme="v1", extra=""):
    timestamp = timestamp or int(time.time())
    signature = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw,
                         hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},{scheme}={signature}{extra}"}


class SignatureVerification(unittest.TestCase):
    def setUp(self):
        self.rail = StripeRail()

    def test_a_genuine_event_verifies(self):
        event = self.rail.verify_event(body(), headers(body()), SECRET)
        self.assertEqual(event.kind, EventKind.CUSTOMER_PAID)
        self.assertEqual(event.job_id, "J1")
        self.assertEqual(event.amount_cents, 45_000)

    def test_a_tampered_body_is_refused(self):
        raw = body()
        with self.assertRaises(FailClosed):
            self.rail.verify_event(raw.replace(b"45000", b"99999"),
                                   headers(raw), SECRET)

    def test_a_signature_from_another_secret_is_refused(self):
        raw = body()
        with self.assertRaises(FailClosed):
            self.rail.verify_event(raw, headers(raw, secret="whsec_other"), SECRET)

    def test_an_empty_secret_is_refused_rather_than_vacuously_passing(self):
        """A published advisory describes exactly this bypass."""
        raw = body()
        with self.assertRaises(FailClosed) as ctx:
            self.rail.verify_event(raw, headers(raw), "")
        self.assertIn("vacuously succeed", str(ctx.exception))

    def test_only_the_v1_scheme_is_honoured(self):
        """Stripe sends a deliberately fake v0 on test events."""
        raw = body()
        with self.assertRaises(FailClosed) as ctx:
            self.rail.verify_event(raw, headers(raw, scheme="v0"), SECRET)
        self.assertIn("no v1 signature", str(ctx.exception))

    def test_a_stale_timestamp_is_treated_as_a_replay(self):
        raw = body()
        stale = int(time.time()) - (DEFAULT_TOLERANCE_SECONDS + 60)
        with self.assertRaises(FailClosed) as ctx:
            self.rail.verify_event(raw, headers(raw, timestamp=stale), SECRET)
        self.assertIn("replay", str(ctx.exception))

    def test_a_zero_tolerance_rail_cannot_be_constructed(self):
        """Zero tolerance disables the recency check entirely."""
        with self.assertRaises(FailClosed):
            StripeRail(tolerance_seconds=0)

    def test_a_reserialised_object_is_refused(self):
        """Re-encoding changes bytes; the raw body is what was signed."""
        raw = body()
        with self.assertRaises(FailClosed) as ctx:
            self.rail.verify_event(json.loads(raw), headers(raw), SECRET)
        self.assertIn("raw request body", str(ctx.exception))

    def test_a_missing_header_is_refused(self):
        with self.assertRaises(FailClosed):
            self.rail.verify_event(body(), {}, SECRET)

    def test_multiple_v1_signatures_are_accepted_during_secret_rotation(self):
        raw = body()
        rotated = headers(raw)
        rotated["Stripe-Signature"] += ",v1=" + "0" * 64
        self.assertTrue(self.rail.verify_event(raw, rotated, SECRET))

    def test_an_event_without_an_id_is_refused(self):
        raw = json.dumps({"type": "payment_intent.succeeded",
                          "data": {"object": {}}}).encode()
        with self.assertRaises(FailClosed) as ctx:
            self.rail.verify_event(raw, headers(raw), SECRET)
        self.assertIn("idempotency", str(ctx.exception))


class LedgerAppliesEvents(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rail = StripeRail()
        self.payment = self.rig.ledger.open_payment(
            job_id="J1", amount_cents=money("450"), rail="stripe", currency="usd")

    def apply(self, **kwargs):
        raw = body(**kwargs)
        return self.rig.ledger.apply_payment_event(
            self.rail.verify_event(raw, headers(raw), SECRET))

    def test_a_verified_live_payment_becomes_revenue(self):
        self.assertIn("applied", self.apply())
        self.assertEqual(self.rig.ledger.collected_for("J1"), 45_000)
        self.assertEqual(self.rig.ledger.real_revenue_cents(), 45_000)

    def test_test_mode_money_is_recorded_but_is_never_revenue(self):
        """A real signature over test money is still not revenue."""
        outcome = self.apply(livemode=False)
        self.assertIn("TEST MODE", outcome)
        self.assertEqual(self.rig.ledger.real_revenue_cents(), 0)

    def test_a_duplicate_event_is_ignored(self):
        self.apply()
        self.assertIn("duplicate", self.apply())

    def test_an_event_for_another_job_cannot_pay_this_one(self):
        self.assertIn("refused", self.apply(event_id="e2", job="J_OTHER"))
        self.assertEqual(self.rig.ledger.collected_for("J1"), 0)

    def test_an_event_with_no_job_attribution_is_refused(self):
        self.assertIn("no job attribution", self.apply(event_id="e3", job=""))

    def test_a_currency_mismatch_is_refused(self):
        self.assertIn("currency mismatch",
                      self.apply(event_id="e4", currency="eur"))
        self.assertEqual(self.rig.ledger.collected_for("J1"), 0)

    def test_an_overpayment_is_refused_as_an_anomaly(self):
        self.assertIn("exceeds the invoiced", self.apply(event_id="e5", amount=99_999))

    def test_an_underpayment_is_partial_not_paid(self):
        self.apply(event_id="e6", amount=20_000)
        payment = self.rig.ledger.payment_for("J1")
        self.assertEqual(payment["state"], PaymentState.PARTIALLY_PAID.value)

    def test_a_refund_reduces_collected_revenue(self):
        self.apply()
        self.apply(event_id="e7", event_type="charge.refunded")
        self.assertEqual(self.rig.ledger.collected_for("J1"), 0)
        self.assertEqual(self.rig.ledger.real_revenue_cents(), 0)

    def test_a_dispute_withdraws_collected_revenue(self):
        self.apply()
        self.apply(event_id="e8", event_type="charge.dispute.created")
        self.assertEqual(self.rig.ledger.collected_for("J1"), 0)

    def test_an_unhandled_event_type_changes_nothing(self):
        self.assertIn("not a payment event",
                      self.apply(event_id="e9", event_type="customer.created"))

    def test_every_event_is_recorded_whether_applied_or_refused(self):
        self.apply(event_id="a1")
        self.apply(event_id="a2", currency="eur")
        recorded = self.rig.ledger.payment_events("J1")
        self.assertEqual(len(recorded), 2)
        self.assertEqual({r["applied"] for r in recorded}, {0, 1})

    def test_every_event_reaches_the_audit_log(self):
        self.apply()
        events = [e["event"] for e in self.rig.audit.events(job_id="J1")]
        self.assertIn("payment.event", events)


class PayoutIsNotPayment(unittest.TestCase):
    """A customer paying and the money reaching a bank are different events."""

    def setUp(self):
        self.rig = Rig()
        self.rail = StripeRail()
        self.rig.ledger.open_payment(job_id="J1", amount_cents=money("450"),
                                     rail="stripe", currency="usd")

    def apply(self, **kwargs):
        raw = body(**kwargs)
        return self.rig.ledger.apply_payment_event(
            self.rail.verify_event(raw, headers(raw), SECRET))

    def test_a_payout_does_not_create_revenue(self):
        outcome = self.apply(event_id="p1", event_type="payout.paid")
        self.assertIn("collected revenue unchanged", outcome)
        self.assertEqual(self.rig.ledger.collected_for("J1"), 0)
        self.assertIs(self.rig.ledger.payout_state("J1"), PayoutState.PAID)

    def test_payment_and_payout_are_tracked_separately(self):
        self.apply(event_id="c1")
        self.assertEqual(self.rig.ledger.collected_for("J1"), 45_000)
        self.assertIs(self.rig.ledger.payout_state("J1"), PayoutState.NOT_APPLICABLE)
        self.apply(event_id="p2", event_type="payout.paid")
        self.assertIs(self.rig.ledger.payout_state("J1"), PayoutState.PAID)

    def test_a_failed_payout_is_recorded_without_touching_revenue(self):
        self.apply(event_id="c2")
        self.apply(event_id="p3", event_type="payout.failed")
        self.assertIs(self.rig.ledger.payout_state("J1"), PayoutState.FAILED)
        self.assertEqual(self.rig.ledger.collected_for("J1"), 45_000)


class RailIsNotAnAuthority(unittest.TestCase):
    def test_the_rail_cannot_move_money_or_write_the_ledger(self):
        """Stripe is a source of facts, not a business authority."""
        rail = StripeRail()
        for forbidden in ("charge", "create_payment", "refund", "capture",
                          "set_payment_state", "record_cost", "apply"):
            with self.subTest(method=forbidden):
                self.assertFalse(hasattr(rail, forbidden))

    def test_the_rail_module_never_touches_the_store(self):
        import pathlib
        source = pathlib.Path("solvent/payments.py").read_text()
        for forbidden in ("Store", "for_authority", "INSERT INTO", "UPDATE "):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
