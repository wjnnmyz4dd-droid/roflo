"""Watching money arrive, and never touching it.

The owner's position is precise and this module exists to hold it:
DeskPilot should *receive* payment and keep working, and should not
manage or move funds. So everything here reads. There is no transfer,
no withdrawal, no conversion, no refund, no retry, no wallet, and no
method that could be talked into one. What it adds is the question
nobody was answering: what state is each job's money actually in, and
which clients owe for work already delivered.

**The seven states the owner named**, and where each comes from:

    NOT_INVOICED       no invoice has been issued for delivered work
    INVOICED           an invoice exists and nothing has arrived
    PAYMENT_PENDING    the rail reports money in flight, or the client
                       says they have paid -- an expectation, not money
    PAYMENT_CONFIRMED  a verified provider event said it landed
    PAYMENT_FAILED     the provider said this attempt did not happen
    PAYMENT_DISPUTED   the client has objected
    REFUNDED           it went back

These are a *projection*. The Ledger owns payment state and this does
not write to it; two authorities over the same fact is how a system
ends up with two answers about whether it has been paid.

**Confirmation comes from the provider or it does not come.** The one
rule worth stating twice. A client's email saying "sent it this
morning", a message in a marketplace thread, a PDF that looks like a
remittance advice -- all of these are things somebody can write, and
an AI reading them cannot tell a true one from a false one. Only a
signed webhook from the rail, verified by
:class:`solvent.payments.StripeRail` or its crypto equivalent, moves a
job to PAYMENT_CONFIRMED. :meth:`confirmation_problem` is the function
that says no to everything else, and it says no by default.

**Slow payment must not stop production.** The owner was explicit.
Once the deliverable is accepted and the work obligation is closed,
the worker's execution capacity is released whether or not the money
has arrived -- an unpaid invoice is a debt to chase, not a reason to
leave a worker holding a finished job. Unpaid invoices are tracked
here instead, separately, so they are visible without being in the
way.

**But a client who has not paid does not get more work on credit.**
:meth:`client_standing` reports who is overdue and by how much;
:meth:`may_take_more_work_from` turns that into a recommendation
against a threshold the owner sets. It is a recommendation: this
module blocks nothing by itself, because refusing a customer is a
commercial decision and those belong to Policy and the owner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import PaymentState

#: The owner's seven, as they asked for them. Deliberately their
#: vocabulary rather than the Ledger's: the Ledger has more states
#: than this because it also tracks cost, and a summary that showed
#: the owner ESTIMATED and INCURRED would be showing them accounting
#: rather than answering "have we been paid".
NOT_INVOICED = "NOT_INVOICED"
INVOICED = "INVOICED"
PAYMENT_PENDING = "PAYMENT_PENDING"
PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
PAYMENT_FAILED = "PAYMENT_FAILED"
PAYMENT_DISPUTED = "PAYMENT_DISPUTED"
REFUNDED = "REFUNDED"

STATES = (NOT_INVOICED, INVOICED, PAYMENT_PENDING, PAYMENT_CONFIRMED,
          PAYMENT_FAILED, PAYMENT_DISPUTED, REFUNDED)

#: How a Ledger state reads in the owner's vocabulary. Anything not
#: listed is money that has not been invoiced yet, which is the
#: honest reading of a cost estimate.
_FROM_LEDGER = {
    PaymentState.INVOICED: INVOICED,
    PaymentState.PAYMENT_PENDING: PAYMENT_PENDING,
    PaymentState.OVERDUE: INVOICED,
    PaymentState.PAID: PAYMENT_CONFIRMED,
    PaymentState.PARTIALLY_PAID: PAYMENT_CONFIRMED,
    PaymentState.PAYMENT_FAILED: PAYMENT_FAILED,
    PaymentState.DISPUTED: PAYMENT_DISPUTED,
    PaymentState.REFUNDED: REFUNDED,
}

#: States in which money is owed and has not arrived. OVERDUE folds
#: into INVOICED above because the difference is how long it has been
#: waiting, which :meth:`outstanding` works out from the dates rather
#: than trusting a flag somebody has to remember to set.
UNPAID = (INVOICED, PAYMENT_PENDING, PAYMENT_FAILED)

#: Evidence good enough to confirm a payment. A verified rail event
#: and nothing else. This tuple is the whole allow-list on purpose:
#: adding to it is a decision somebody has to make deliberately.
AUTHORITATIVE = ("rail_event",)


@dataclass(frozen=True, slots=True)
class Standing:
    """What one client owes, and for how long."""

    client_ref: str
    unpaid_invoices: int = 0
    unpaid_cents: int = 0
    oldest_unpaid_days: float = 0.0
    disputes: int = 0

    @property
    def clean(self) -> bool:
        return not self.unpaid_invoices and not self.disputes


class PaymentMonitor:
    """A read-only view of where every job's money has got to.

    Holds no authority. Writes nothing. Given a Ledger it can answer
    what state a job is in, what is outstanding, and which clients
    are behind; given anything else it says it cannot tell, which is
    the honest answer and not an error to be smoothed over.
    """

    def __init__(self, ledger, policy=None, clock=None) -> None:
        self._ledger = ledger
        self._policy = policy
        self._clock = clock

    # ------------------------------------------------------- one job

    def state(self, job_id: str) -> str:
        """Which of the owner's seven states this job's money is in."""
        try:
            row = self._ledger.payment_for(job_id)
        except Exception:
            # A books-unreadable answer is not NOT_INVOICED. Saying
            # "no invoice" when the table will not open would read as
            # "nothing is owed", which is the comfortable wrong
            # answer in exactly the way this system keeps finding.
            raise
        if not row:
            return NOT_INVOICED
        raw = row.get("state")
        try:
            ledger_state = (raw if isinstance(raw, PaymentState)
                            else PaymentState(raw))
        except ValueError:
            return NOT_INVOICED
        return _FROM_LEDGER.get(ledger_state, NOT_INVOICED)

    def is_simulated(self, job_id: str) -> bool:
        try:
            return bool(self._ledger.is_simulated(job_id))
        except Exception:
            return False

    def confirmation_problem(self, *, evidence_kind: str = "",
                             evidence_ref: str = "") -> str:
        """Why this is not good enough to call a payment confirmed.

        Default-deny, and the default is the point. The argument for
        a permissive version is always the same -- the client clearly
        did pay, the email is obviously genuine, the marketplace
        thread says so -- and every one of those is a sentence
        somebody can write. The provider's own signed event is the
        only thing in this system that an outsider cannot forge, so
        it is the only thing that counts.
        """
        if evidence_kind not in AUTHORITATIVE:
            return (f"{evidence_kind or 'no'} evidence is not authoritative; "
                    "a payment is confirmed by a verified event from the "
                    "payment rail and by nothing else -- not an email, not "
                    "a client message, not a document that looks like a "
                    "receipt")
        if not evidence_ref:
            return ("the evidence has no reference, so nobody could check "
                    "it later; an unverifiable confirmation is not one")
        return ""

    # -------------------------------------------- the whole order book

    def census(self) -> dict:
        """How many jobs sit in each of the seven states."""
        counts = {state: 0 for state in STATES}
        for job_id in self._job_ids():
            counts[self.state(job_id)] = counts.get(self.state(job_id), 0) + 1
        return counts

    def outstanding(self, *, now=None) -> list:
        """Invoices issued and not collected, oldest first.

        Tracked here and not in the capacity gate, deliberately. An
        unpaid invoice is a debt to chase; it is not a reason to
        leave a worker holding a job that is finished and accepted,
        and the owner said so plainly.
        """
        rows = []
        for job_id in self._job_ids():
            state = self.state(job_id)
            if state not in UNPAID:
                continue
            row = self._ledger.payment_for(job_id) or {}
            rows.append({
                "job_id": job_id,
                "state": state,
                "amount_cents": int(row.get("amount_cents") or 0),
                "client_ref": str(row.get("client_ref") or ""),
                "age_days": self._age_days(row, now=now),
                "simulated": self.is_simulated(job_id),
            })
        rows.sort(key=lambda r: -r["age_days"])
        return rows

    def unpaid_cents(self, *, include_simulated: bool = False) -> int:
        """What is owed. Simulated money is left out unless asked for.

        A total that quietly included simulated invoices would make
        the owner's receivables look healthier than they are, in the
        same way counting simulated payments as revenue would make
        the takings look healthier. Same mistake, other column.
        """
        return sum(r["amount_cents"] for r in self.outstanding()
                   if include_simulated or not r["simulated"])

    # ------------------------------------------------ who is behind

    def client_standing(self, *, now=None) -> dict:
        """Every client with something outstanding, and how far behind."""
        standings: dict[str, dict] = {}
        for row in self.outstanding(now=now):
            if row["simulated"]:
                continue
            ref = row["client_ref"] or "unattributed"
            entry = standings.setdefault(
                ref, {"invoices": 0, "cents": 0, "oldest": 0.0})
            entry["invoices"] += 1
            entry["cents"] += row["amount_cents"]
            entry["oldest"] = max(entry["oldest"], row["age_days"])
        out = {}
        for ref, entry in standings.items():
            out[ref] = Standing(
                client_ref=ref, unpaid_invoices=entry["invoices"],
                unpaid_cents=entry["cents"],
                oldest_unpaid_days=round(entry["oldest"], 2))
        return out

    def may_take_more_work_from(self, client_ref: str, *, now=None) -> tuple:
        """Should DeskPilot take another job from this client?

        A recommendation and an explanation, never an enforcement.
        Refusing a customer is a commercial decision; Policy and the
        owner make those. What this can do is make sure the decision
        is not made in ignorance of an unpaid invoice from last month.
        """
        days = self._overdue_after_days()
        if days <= 0:
            return True, ("the owner has not set an overdue threshold, so "
                          "no client is behind by this system's reckoning")
        standing = self.client_standing(now=now).get(client_ref)
        if standing is None or standing.clean:
            return True, "nothing outstanding from this client"
        if standing.oldest_unpaid_days < days:
            return True, (f"{standing.unpaid_invoices} invoice(s) "
                          f"outstanding, oldest {standing.oldest_unpaid_days:.0f} "
                          f"day(s), which is inside the {days:.0f}-day "
                          "threshold")
        return False, (f"{standing.unpaid_invoices} invoice(s) totalling "
                       f"${standing.unpaid_cents / 100:,.2f} are unpaid, the "
                       f"oldest {standing.oldest_unpaid_days:.0f} days, past "
                       f"the owner's {days:.0f}-day threshold; taking more "
                       "work would be extending more credit")

    # ----------------------------------------------------------- money

    def profit(self) -> dict:
        """Collected against projected, told apart.

        Two numbers, never one. "Estimated and actual profit" was in
        the owner's list of summary contents, and the reason it is
        two entries rather than one is that adding them is how a
        business comes to believe it has money it has invoiced.
        """
        # Every read is reported, including the ones that fail. An
        # earlier version caught each exception and carried on with
        # zero, so a ledger that would not open produced a tidy
        # "collected $0.00" -- which is what a quiet month looks
        # like, and a broken database is not a quiet month. Unreadable
        # reading as healthy is the failure this system keeps finding,
        # and it found it here too.
        problems = []
        collected = None
        try:
            collected = int(self._ledger.real_revenue_cents())
        except Exception as exc:
            problems.append(f"collected revenue: {type(exc).__name__}")
        costs = None
        try:
            costs = int(self._ledger.total_costs_cents())
        except Exception as exc:
            problems.append(f"recorded costs: {type(exc).__name__}")
        invoiced_unpaid = None
        try:
            invoiced_unpaid = self.unpaid_cents()
        except Exception as exc:
            problems.append(f"outstanding invoices: {type(exc).__name__}")

        def total(*parts):
            return (None if any(p is None for p in parts)
                    else sum(parts))

        return {
            "collected_revenue_cents": collected,
            "recorded_costs_cents": costs,
            "actual_profit_cents": total(collected, -costs
                                         if costs is not None else None),
            "invoiced_but_unpaid_cents": invoiced_unpaid,
            "projected_if_everything_is_paid_cents": total(
                collected, invoiced_unpaid,
                -costs if costs is not None else None),
            "problems": problems,
            "basis": ("collected is money a verified rail event confirmed; "
                      "projected assumes every outstanding invoice is paid "
                      "in full, which nothing has established"),
        }

    # --------------------------------------------------------- internals

    def _job_ids(self) -> list:
        try:
            return list(self._ledger.invoiced_job_ids())
        except Exception:
            return []

    def _overdue_after_days(self) -> float:
        if self._policy is None:
            return 0.0
        try:
            return float(self._policy.get(
                "commercial", "overdue_after_days", default=0) or 0)
        except Exception:
            return 0.0

    def _age_days(self, row: dict, *, now=None) -> float:
        """How long this invoice has been sitting, in days.

        ``updated_at`` is when the row last moved, which for an
        invoice nobody has paid is when it was issued -- the same
        moment precisely in the case this measures, because a row
        that has moved since is not outstanding and never reaches
        here. There is no invoiced_at column and adding one would
        store a date the table already has.

        Timestamps are ISO-8601 strings, not epoch seconds. An
        earlier version subtracted them as floats and raised; a
        malformed one now reads as age zero, which errs towards
        *not* calling a client overdue on the strength of a date
        nobody can parse.
        """
        from datetime import datetime, timezone

        stamp = row.get("updated_at") or ""
        issued = self._moment(stamp)
        if issued is None:
            return 0.0
        if now is None:
            moment = datetime.now(timezone.utc)
        elif isinstance(now, (int, float)):
            moment = datetime.fromtimestamp(float(now), timezone.utc)
        elif isinstance(now, str):
            moment = self._moment(now) or datetime.now(timezone.utc)
        else:
            moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return max(0.0, (moment - issued).total_seconds() / 86_400.0)

    @staticmethod
    def _moment(stamp):
        from datetime import datetime, timezone

        if not stamp:
            return None
        try:
            parsed = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(
            tzinfo=timezone.utc)
