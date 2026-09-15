"""Ledger — authoritative financial actuals.

Owns one decision: *what money actually moved?*

The Governor forecasts; the Ledger records. Keeping them apart is what stops a
forecast contaminating the books and stops the component that wants to say yes
from also keeping score.

Two rules do most of the work here:

* **Expected revenue is not collected revenue.** A payment reaches ``PAID`` only
  with an external verification method and timestamp. An invoice is not income.
* **Entries are immutable.** A correction is a new, linked entry — never an edit
  — so the history of what Solvent believed remains readable after it was wrong.

Learning may read this and may never write it. Calibration and autonomy evidence
are drawn from here precisely because Learning cannot reach it.
"""

from __future__ import annotations

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .store import Store
from .payments import EventKind, VerifiedPaymentEvent
from .types import Cents, CostCategory, PaymentState, PayoutState

#: Cost sources Solvent trusts. A heuristic token count is not among them:
#: roflo's ``len(text)/4`` estimator must never reach the books.
TRUSTED_COST_SOURCES = frozenset({"PROVIDER_BILLING", "RECEIPT", "VERIFIED_PAYMENT",
                                  "OWNER_RECORDED", "SIMULATED_FIXTURE"})

#: Prefix marking a verification that did not come from a real payment rail.
#: Simulated money must never be reportable as real revenue, so the marker lives
#: in the stored value rather than in a comment or a caller's good intentions.
SIMULATED_PREFIX = "SIMULATED:"


class Ledger:
    """Financial truth. Append-oriented, idempotent, verification-gated."""

    def __init__(self, store: Store, audit: AuditLog) -> None:
        self._db = store.for_authority("ledger")
        self._audit = audit

    # ----------------------------------------------------------------- costs

    def record_cost(self, *, job_id: str, category: CostCategory, amount_cents: Cents,
                    source: str, note: str = "", entry_id: str | None = None,
                    corrects: str | None = None) -> str:
        """Record an incurred cost. ``source`` must be a trusted origin."""
        if source not in TRUSTED_COST_SOURCES:
            raise FailClosed(
                f"cost source {source!r} is not trusted; "
                f"expected one of {sorted(TRUSTED_COST_SOURCES)}"
            )
        if amount_cents < 0:
            raise FailClosed("costs are positive; a negative cost must be a correction")
        entry_id = entry_id or new_id("led")
        if self._db.query_one("SELECT id FROM ledger_entries WHERE id = ?", (entry_id,)):
            return entry_id  # idempotent: the same entry twice is one entry
        self._db.execute(
            "INSERT INTO ledger_entries(id,ts,job_id,category,amount_cents,direction,"
            "source,corrects,note) VALUES(?,?,?,?,?,?,?,?,?)",
            (entry_id, now(), job_id, category.value, amount_cents, "COST", source,
             corrects, note))
        self._db.commit()
        self._audit.record(
            event="ledger.cost", authority="ledger", initiator="ledger",
            why=note or f"cost {category.value}", job_id=job_id, input_ref=entry_id,
            result=f"{amount_cents} cents", source=source)
        return entry_id

    def correct(self, *, original_id: str, job_id: str, category: CostCategory,
                corrected_amount_cents: Cents, source: str, reason: str) -> str:
        """Correct an earlier entry by appending a delta. Never an edit."""
        row = self._db.query_one(
            "SELECT amount_cents FROM ledger_entries WHERE id = ?", (original_id,))
        if row is None:
            raise FailClosed(f"cannot correct unknown entry {original_id!r}")
        delta = corrected_amount_cents - int(row["amount_cents"])
        entry_id = new_id("led")
        self._db.execute(
            "INSERT INTO ledger_entries(id,ts,job_id,category,amount_cents,direction,"
            "source,corrects,note) VALUES(?,?,?,?,?,?,?,?,?)",
            (entry_id, now(), job_id, category.value, abs(delta),
             "COST" if delta >= 0 else "CREDIT", source, original_id, reason))
        self._db.commit()
        self._audit.record(
            event="ledger.correction", authority="ledger", initiator="ledger",
            why=reason, job_id=job_id, input_ref=entry_id,
            result=f"delta {delta} cents against {original_id}")
        return entry_id

    def predicted_costs_for(self, job_id: str) -> Cents:
        """Costs excluding deliberate reserves, for calibration only.

        Compared against the estimate's predicted portion so that an unspent
        allowance never reads as an estimation error.
        """
        rows = self._db.query(
            "SELECT direction, amount_cents, category FROM ledger_entries "
            "WHERE job_id = ?", (job_id,))
        return sum(int(r["amount_cents"]) * (1 if r["direction"] == "COST" else -1)
                   for r in rows if not CostCategory(r["category"]).is_allowance)

    def costs_for(self, job_id: str) -> Cents:
        rows = self._db.query(
            "SELECT direction, amount_cents FROM ledger_entries WHERE job_id = ?",
            (job_id,))
        return sum(int(r["amount_cents"]) * (1 if r["direction"] == "COST" else -1)
                   for r in rows)

    # -------------------------------------------------------------- payments

    def open_payment(self, *, job_id: str, amount_cents: Cents, rail: str,
                     payment_id: str | None = None, currency: str = "usd",
                     external_ref: str = "") -> str:
        payment_id = payment_id or new_id("pay")
        self._db.execute(
            "INSERT INTO payments(id,job_id,state,amount_cents,collected_cents,rail,"
            "updated_at,currency,external_ref) VALUES(?,?,?,?,0,?,?,?,?)",
            (payment_id, job_id, PaymentState.ESTIMATED.value, amount_cents, rail,
             now(), currency.lower(), external_ref))
        self._db.commit()
        self._audit.record(
            event="payment.opened", authority="ledger", initiator="ledger",
            why="quote recorded", job_id=job_id, input_ref=payment_id,
            result=PaymentState.ESTIMATED.value)
        return payment_id

    def set_payment_state(self, payment_id: str, state: PaymentState, *,
                          collected_cents: Cents = 0, verification_method: str = "") -> None:
        """Advance a payment. Collection requires external verification.

        ``PAID`` and ``PARTIALLY_PAID`` are the only states that mean money
        arrived, so they are the only ones that demand proof. Without a
        verification method the call fails closed rather than inventing revenue.
        """
        row = self._db.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
        if row is None:
            raise FailClosed(f"unknown payment {payment_id!r}")
        if state.is_collected and not verification_method:
            raise FailClosed(
                f"{state.value} requires an external verification method; "
                "expected revenue is not collected revenue"
            )
        verified_at = now() if state.is_collected else row["verified_at"]
        self._db.execute(
            "UPDATE payments SET state=?, collected_cents=?, verification_method=?, "
            "verified_at=?, updated_at=? WHERE id=?",
            (state.value, collected_cents or int(row["collected_cents"]),
             verification_method or row["verification_method"], verified_at,
             now(), payment_id))
        self._db.commit()
        self._audit.record(
            event="payment.state", authority="ledger", initiator="ledger",
            why=f"payment moved to {state.value}", job_id=row["job_id"],
            input_ref=payment_id, result=state.value,
            financial_authorization=verification_method)

    def payment_for(self, job_id: str) -> dict | None:
        row = self._db.query_one(
            "SELECT * FROM payments WHERE job_id = ? ORDER BY updated_at DESC", (job_id,))
        return dict(row) if row else None

    def is_simulated(self, job_id: str) -> bool:
        row = self._db.query_one(
            "SELECT verification_method FROM payments WHERE job_id = ?", (job_id,))
        return bool(row) and str(row["verification_method"]).startswith(SIMULATED_PREFIX)

    def real_revenue_cents(self) -> Cents:
        """Collected revenue excluding anything verified by a simulated rail.

        The harness proves the loop with fixtures; this is what stops fixture
        money ever being counted, displayed, or reported as earnings.
        """
        total = 0
        for row in self._db.query("SELECT * FROM payments"):
            if not PaymentState(row["state"]).is_collected:
                continue
            method = str(row["verification_method"] or "")
            if not method or method.startswith(SIMULATED_PREFIX):
                continue
            total += int(row["collected_cents"])
        return total

    def collected_for(self, job_id: str) -> Cents:
        """Revenue that actually arrived and was externally verified."""
        row = self._db.query_one(
            "SELECT collected_cents, state, verification_method FROM payments "
            "WHERE job_id = ?", (job_id,))
        if row is None or not PaymentState(row["state"]).is_collected:
            return 0
        if not row["verification_method"]:
            return 0
        return int(row["collected_cents"])

    # --------------------------------------------------- external rail events

    def apply_payment_event(self, event: VerifiedPaymentEvent) -> str:
        """Apply a cryptographically verified rail event. The Ledger decides.

        The rail established that the event is genuine; this decides what it
        means for the books. Every event is recorded whether or not it is
        applied, so a refusal is as visible as an acceptance.

        Test-mode money is recorded and deliberately not counted: a genuine
        signature over a test payment is still not revenue.
        """
        seen = self._db.query_one(
            "SELECT outcome FROM payment_events WHERE event_id = ?", (event.event_id,))
        if seen is not None:
            # Rails re-deliver. Idempotency is by event id, not by content.
            return f"duplicate event ignored (first outcome: {seen['outcome']})"

        outcome = self._decide_event(event)
        self._db.execute(
            "INSERT INTO payment_events(event_id,ts,rail,event_type,job_id,"
            "payment_id,amount_cents,currency,livemode,applied,outcome) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (event.event_id, now(), event.rail, event.raw_type, event.job_id,
             self._payment_id_for(event.job_id) or "", event.amount_cents,
             event.currency, 1 if event.livemode else 0,
             1 if outcome.startswith("applied") else 0, outcome))
        self._db.commit()
        self._audit.record(
            event="payment.event", authority="ledger", initiator=f"rail:{event.rail}",
            why=event.raw_type, job_id=event.job_id or None,
            input_ref=event.event_id, decision=event.kind, result=outcome,
            external_effect=f"inbound {event.rail} event", livemode=event.livemode)
        return outcome

    def _payment_id_for(self, job_id: str) -> str | None:
        row = self._db.query_one(
            "SELECT id FROM payments WHERE job_id = ?", (job_id,))
        return row["id"] if row else None

    def _decide_event(self, event: VerifiedPaymentEvent) -> str:
        """What a verified event is allowed to change. Ordered strictest-first."""
        if event.kind == EventKind.IGNORED:
            return f"not a payment event ({event.raw_type})"
        if not event.job_id:
            return ("refused: event carries no job attribution; a payment that "
                    "cannot be tied to a job cannot be counted")
        row = self._db.query_one(
            "SELECT * FROM payments WHERE job_id = ?", (event.job_id,))
        if row is None:
            return f"refused: no payment record for job {event.job_id!r}"

        if event.kind in (EventKind.PAYOUT_PAID, EventKind.PAYOUT_FAILED):
            # A payout is money reaching the bank. It says nothing about whether
            # a customer paid, so it must never change collected revenue.
            state = (PayoutState.PAID if event.kind == EventKind.PAYOUT_PAID
                     else PayoutState.FAILED)
            self._db.execute(
                "UPDATE payments SET payout_state=?, payout_verified_at=?, "
                "updated_at=? WHERE id=?",
                (state.value, now() if state is PayoutState.PAID else None,
                 now(), row["id"]))
            return f"applied: payout {state.value} (collected revenue unchanged)"

        if event.currency and row["currency"] and event.currency != row["currency"]:
            return (f"refused: currency mismatch (event {event.currency}, "
                    f"invoice {row['currency']})")

        if event.kind == EventKind.CUSTOMER_PAID:
            invoiced = int(row["amount_cents"])
            if event.amount_cents > invoiced:
                return (f"refused: event amount {event.amount_cents} exceeds the "
                        f"invoiced {invoiced}; an overpayment is an anomaly")
            state = (PaymentState.PAID if event.amount_cents == invoiced
                     else PaymentState.PARTIALLY_PAID)
            method = f"{event.rail}:{event.raw_type}:{event.external_ref}"
            if event.is_test_money:
                # A real signature over test money is still not revenue.
                method = f"{SIMULATED_PREFIX}{method}"
            self._db.execute(
                "UPDATE payments SET state=?, collected_cents=?, "
                "verification_method=?, verified_at=?, updated_at=? WHERE id=?",
                (state.value, event.amount_cents, method, now(), now(), row["id"]))
            return (f"applied: {state.value}"
                    + (" (TEST MODE — not revenue)" if event.is_test_money else ""))

        if event.kind == EventKind.REFUNDED:
            self._db.execute(
                "UPDATE payments SET state=?, refunded_cents=?, collected_cents=?, "
                "updated_at=? WHERE id=?",
                (PaymentState.REFUNDED.value, event.amount_cents,
                 max(0, int(row["collected_cents"]) - event.amount_cents), now(),
                 row["id"]))
            return "applied: REFUNDED (collected revenue reduced)"

        if event.kind == EventKind.DISPUTED:
            self._db.execute(
                "UPDATE payments SET state=?, collected_cents=0, updated_at=? "
                "WHERE id=?", (PaymentState.DISPUTED.value, now(), row["id"]))
            return "applied: DISPUTED (collected revenue withdrawn pending outcome)"

        return f"refused: unhandled event kind {event.kind}"

    def payout_state(self, job_id: str) -> PayoutState:
        """Whether the money reached the bank — a different question to PAID."""
        row = self._db.query_one(
            "SELECT payout_state FROM payments WHERE job_id = ?", (job_id,))
        return PayoutState(row["payout_state"]) if row else PayoutState.NOT_APPLICABLE

    def payment_events(self, job_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM payment_events"
        params: tuple = ()
        if job_id:
            sql += " WHERE job_id = ?"
            params = (job_id,)
        return [dict(r) for r in self._db.query(sql + " ORDER BY ts", params)]

    # ---------------------------------------------------------------- profit

    def actual_profit(self, job_id: str) -> dict:
        """Actual profit for a job, from collected revenue and recorded cost."""
        revenue = self.collected_for(job_id)
        cost = self.costs_for(job_id)
        profit = revenue - cost
        return {
            "job_id": job_id, "collected_revenue_cents": revenue,
            "actual_cost_cents": cost, "actual_profit_cents": profit,
            "margin": (profit / revenue) if revenue > 0 else 0.0,
        }

    def verified_job_ids(self) -> list[str]:
        """Jobs whose revenue was externally verified — the calibration sample."""
        return [r["job_id"] for r in self._db.query(
            "SELECT DISTINCT job_id FROM payments WHERE state IN (?,?) "
            "AND verification_method != ''",
            (PaymentState.PAID.value, PaymentState.PARTIALLY_PAID.value))]
