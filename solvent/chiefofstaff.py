"""Coordination. It sequences the business; it decides nothing.

The external project this responsibility is modelled on --
``goyaljai/chief-of-staff``, MIT, verified at ``cba186a`` -- could not
be imported: 18 of its files use ``subprocess``, 14 ``psycopg2``, 13
``fastapi``, 7 ``telegram``, 6 ``openai``, and DeskPilot's egress hook
denies ``subprocess.Popen`` and ``socket.connect`` process-wide while
``solvent/`` imports only the standard library under a test that
enforces it. What is adapted is its *idea*: a layer that holds the
sequence and escalates only when genuinely stuck.

**This class owns no authority, and the test suite pins that.** It
holds no money, admits no job, authorises no delivery and sets no
price. Every decision belongs to something that already existed:

======================  ====================================
the decision            who makes it
======================  ====================================
is this work worth it   Qualification, then the Governor
what may we spend       FinancialGovernor
may this leave          ActionGate
how low may we go       the owner's NegotiationPolicy, as
                        arithmetic in solvent.negotiation
may we write to them    the suppression list
has money arrived       a verified payment rail
is the deliverable ok   the independent checker
======================  ====================================

What is genuinely this layer's is **ordering and escalation**: what
to do next, and when to stop and ask. :meth:`next_step` is the whole
of it, and it returns a recommendation rather than performing
anything, so that a coordinator bug cannot become a business action.
"""

from __future__ import annotations

import json
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from enum import Enum

from .crm import CRM, Prospect, Standing
from .engagement import Engagement, Stage, engagement_from_dict
from .errors import FailClosed
from .negotiation import Negotiation, Outcome, negotiation_from_dict
from .outreach import Campaign, Suppressions, may_write

#: The memory kinds the two books live under. Not tables of their own:
#: ``memory_facts`` already exists and the store refuses a write from
#: any authority but memory's, which is what caught the CRM inventing
#: a table nobody owned.
_KIND_NEGOTIATION = "cos_negotiation"
_KIND_ENGAGEMENT = "cos_engagement"



class Step(Enum):
    """What to do next with one relationship."""

    RESEARCH = "RESEARCH"
    QUALIFY = "QUALIFY"
    PRICE = "PRICE"
    PREPARE_OUTREACH = "PREPARE_OUTREACH"
    FOLLOW_UP = "FOLLOW_UP"
    PROPOSE = "PROPOSE"
    NEGOTIATE = "NEGOTIATE"
    REQUEST_DEPOSIT = "REQUEST_DEPOSIT"
    AWAIT_DEPOSIT = "AWAIT_DEPOSIT"
    ADMIT_WORK = "ADMIT_WORK"
    AWAIT_DELIVERY = "AWAIT_DELIVERY"
    INVOICE = "INVOICE"
    CHASE_BALANCE = "CHASE_BALANCE"
    IDENTIFY_RECURRING = "IDENTIFY_RECURRING"
    #: Nothing to do, and that is a real answer.
    NOTHING = "NOTHING"
    #: Only a person can move this on.
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class Recommendation:
    """What to do, why, and who has to do it.

    ``owner_must_decide`` is separate from ``step`` on purpose: an
    escalation is not a kind of work, it is work stopping. Folding it
    into the step would let a caller treat "ask the owner" as just
    another thing to get on with.
    """

    prospect_id: str
    step: Step
    why: str
    owner_must_decide: bool = False

    def as_dict(self) -> dict:
        return {"prospect_id": self.prospect_id, "step": self.step.value,
                "why": self.why, "owner_must_decide": self.owner_must_decide}


class _Book(MutableMapping):
    """The deals this layer is tracking, written down as they change.

    A :class:`~collections.abc.MutableMapping` and not a ``dict``
    subclass. ``dict.update`` and ``dict.setdefault`` do not go through
    a subclass's ``__setitem__`` in CPython, so a subclass would have
    persisted ``book[pid] = deal`` and silently dropped
    ``book.update({pid: deal})`` -- a storage layer with a hole in it
    shaped exactly like the second-most-obvious way to use it. Every
    mutating method on a MutableMapping is defined in terms of
    ``__setitem__``, so there is one way in.

    Putting a deal here also hands it an ``on_change`` hook, so that
    later transitions on the object -- an acceptance, a payment, a
    concession -- are written as they happen rather than at whatever
    point somebody remembers to save. The thing being tracked is
    mutable; a store that only recorded the moment of handover would
    hold the first version of every deal for ever.
    """

    def __init__(self, save: Callable[[str, object], None]) -> None:
        self._items: dict[str, object] = {}
        self._save = save

    def __getitem__(self, key):
        return self._items[key]

    def __setitem__(self, key, value):
        self._attach(key, value)
        self._items[key] = value
        self._save(key, value)

    def __delitem__(self, key):
        # Deliberately not persisted as a deletion: the store is
        # append-only and forgetting a deal is not a thing a
        # coordination layer gets to do to the record.
        self._items[key].on_change = None
        del self._items[key]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def restore(self, key, value) -> None:
        """Put back what storage already holds, without rewriting it."""
        self._attach(key, value)
        self._items[key] = value

    def _attach(self, key, value) -> None:
        value.on_change = lambda deal, _k=key: self._save(_k, deal)


class ChiefOfStaff:
    """Holds the sequence. Asks the authorities. Escalates when stuck."""

    def __init__(self, *, crm: CRM, suppressions: Suppressions,
                 campaign: Campaign | None = None, audit=None,
                 memory=None) -> None:
        self.crm = crm
        self.suppressions = suppressions
        self.campaign = campaign or Campaign()
        self._audit = audit
        #: :class:`solvent.memory.BusinessMemory`, or ``None`` for a
        #: coordinator that forgets. :attr:`durable` says which, the
        #: same way :class:`~solvent.crm.CRM` does, so that nobody has
        #: to infer it from whether a restart happened to work.
        self._memory = memory
        self.negotiations: MutableMapping[str, Negotiation] = _Book(
            lambda key, deal: self._persist(_KIND_NEGOTIATION, key, deal))
        self.engagements: MutableMapping[str, Engagement] = _Book(
            lambda key, deal: self._persist(_KIND_ENGAGEMENT, key, deal))
        if self._memory is not None:
            self._rehydrate()

    @property
    def durable(self) -> bool:
        return self._memory is not None

    # ---- remembering between restarts ----------------------------------

    def _persist(self, kind: str, prospect_id: str, deal) -> None:
        if self._memory is None:
            return
        from .types import VerificationTier

        # T1: a deterministic record of what this layer was told, not a
        # claim about the client's business or about money having
        # arrived. The payments inside an engagement carry their own
        # external references, which is what makes them checkable.
        self._memory.learn(
            kind=kind, subject=prospect_id,
            payload={"prospect_id": prospect_id, **deal.as_dict()},
            evidence_ref=f"{kind}:{prospect_id}",
            tier=VerificationTier.T1_DETERMINISTIC)

    def _rehydrate(self) -> None:
        """Rebuild both books from their latest recorded state.

        The same two traps as :meth:`solvent.crm.CRM._rehydrate`, for
        the same reason: ``recall`` returns newest first, and ``ts``
        can tie when several writes land in one instant. The journal
        length decides instead -- a negotiation's steps and an
        engagement's history each grow by at least one on every
        change, so the longest is the latest.

        A row that cannot be rebuilt is skipped rather than guessed
        at. A half-restored contract is worse than a missing one: the
        missing one stops the pipeline and gets noticed.
        """
        for kind, rebuild, journal, book in (
                (_KIND_NEGOTIATION, negotiation_from_dict, "steps",
                 self.negotiations),
                (_KIND_ENGAGEMENT, engagement_from_dict, "history",
                 self.engagements)):
            for prospect_id, payload in self._latest(kind, journal).items():
                try:
                    book.restore(prospect_id, rebuild(payload))
                except (ValueError, KeyError, TypeError, FailClosed):
                    continue

    def _latest(self, kind: str, journal: str) -> dict:
        try:
            facts = self._memory.recall(kind=kind)
        except Exception:
            return {}
        latest: dict[str, dict] = {}
        for fact in facts:
            payload = fact.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if not isinstance(payload, dict) or not payload.get("prospect_id"):
                continue
            known = latest.get(payload["prospect_id"])
            if known is None or len(payload.get(journal, [])) > len(
                    known.get(journal, [])):
                latest[payload["prospect_id"]] = payload
        return latest

    # ---- the one interesting method -----------------------------------

    def next_step(self, prospect_id: str, *, today: str = "") -> Recommendation:
        """What to do next, as advice. Performs nothing.

        The order of these checks is the coordination logic, and it is
        deliberately boring: the furthest-along state wins, terminal
        states stop everything, and anything a limit refused becomes an
        escalation rather than a retry.
        """
        prospect = self.crm.get(prospect_id)

        if prospect.standing.terminal:
            return self._say(prospect_id, Step.NOTHING,
                             f"{prospect.company} is "
                             f"{prospect.standing.value}")

        engagement = self.engagements.get(prospect_id)
        if engagement is not None:
            return self._engagement_step(prospect_id, engagement)

        deal = self.negotiations.get(prospect_id)
        if deal is not None:
            return self._negotiation_step(prospect_id, deal)

        if not prospect.evidence:
            return self._say(prospect_id, Step.RESEARCH,
                             "nothing is known about them yet")
        if not prospect.verified_need:
            return self._say(
                prospect_id, Step.RESEARCH,
                f"{len(prospect.hypotheses)} hypothesis(es) and nothing "
                "published; a guess is not a reason to write to somebody")
        if prospect.standing is Standing.RESEARCHED:
            return self._say(prospect_id, Step.QUALIFY,
                             "there is a published need to qualify against")
        if not prospect.contacts:
            return self._say(prospect_id, Step.RESEARCH,
                             "no contact with a recorded source")

        refusal = self._outreach_refusal(prospect, today)
        if refusal:
            return self._say(prospect_id, Step.NOTHING, refusal)

        if prospect.standing is Standing.QUALIFIED:
            return self._say(prospect_id, Step.PREPARE_OUTREACH,
                             "qualified, contactable, not yet written to")
        if prospect.standing is Standing.CONTACTED:
            return self._say(prospect_id, Step.FOLLOW_UP,
                             "written to, no reply yet")
        if prospect.standing is Standing.REPLIED:
            return self._say(prospect_id, Step.PROPOSE, "they replied")
        if prospect.standing in (Standing.PAID, Standing.REPEAT):
            return self._say(prospect_id, Step.IDENTIFY_RECURRING,
                             "paid; worth asking what else they need")
        return self._say(prospect_id, Step.NOTHING,
                         f"nothing queued at {prospect.standing.value}")

    def _outreach_refusal(self, prospect: Prospect, today: str) -> str:
        """Ask the suppression rules rather than reimplementing them."""
        for contact in prospect.contacts:
            if not may_write(contact.email, suppressions=self.suppressions,
                             campaign=self.campaign, today=today,
                             prospect=prospect):
                return ""
        if not prospect.contacts:
            return "no contact with a recorded source"
        return may_write(prospect.contacts[0].email,
                         suppressions=self.suppressions,
                         campaign=self.campaign, today=today,
                         prospect=prospect)

    def _negotiation_step(self, prospect_id: str,
                          deal: Negotiation) -> Recommendation:
        if deal.outcome is Outcome.ESCALATED:
            return self._say(prospect_id, Step.ESCALATE, deal.escalation,
                             owner_must_decide=True)
        if deal.outcome is Outcome.REFUSED:
            return self._say(prospect_id, Step.NOTHING,
                             "their last offer was below the floor and was "
                             "refused")
        if deal.outcome is Outcome.AGREED_PENDING_AUTHORISATION:
            return self._say(prospect_id, Step.PROPOSE,
                             f"terms agreed at {deal.current_cents}; a "
                             "contract can be drawn")
        return self._say(prospect_id, Step.NEGOTIATE, "a counteroffer is open")

    def _engagement_step(self, prospect_id: str,
                         deal: Engagement) -> Recommendation:
        stage = deal.stage
        if stage is Stage.PAID:
            return self._say(prospect_id, Step.IDENTIFY_RECURRING,
                             "settled in full")
        if stage in (Stage.DECLINED, Stage.ABANDONED):
            return self._say(prospect_id, Step.NOTHING, stage.value.lower())
        if deal.acceptance is None:
            return self._say(prospect_id, Step.PROPOSE,
                             "the proposal is out and unanswered")
        if not deal.deposit_satisfied:
            if stage is Stage.DEPOSIT_REQUESTED:
                return self._say(
                    prospect_id, Step.AWAIT_DEPOSIT,
                    f"waiting on {deal.deposit_due_cents - deal.collected_cents}"
                    " cents of deposit; work is not authorised")
            return self._say(prospect_id, Step.REQUEST_DEPOSIT,
                             "accepted, and no deposit has been asked for")
        if stage is Stage.DEPOSIT_RECEIVED:
            return self._say(prospect_id, Step.ADMIT_WORK,
                             "deposit cleared; the Governor can be asked")
        if stage is Stage.WORK_AUTHORISED:
            return self._say(prospect_id, Step.AWAIT_DELIVERY,
                             "with the five-skill engine")
        if stage is Stage.DELIVERED:
            return self._say(prospect_id, Step.INVOICE, "delivered, unbilled")
        if stage is Stage.INVOICED:
            return self._say(prospect_id, Step.CHASE_BALANCE,
                             f"{deal.outstanding_cents} cents outstanding")
        return self._say(prospect_id, Step.NOTHING, stage.value)

    # ---- the book --------------------------------------------------------

    def pipeline(self, *, today: str = "") -> list[Recommendation]:
        return [self.next_step(p.prospect_id, today=today)
                for p in self.crm.all()]

    def escalations(self, *, today: str = "") -> list[Recommendation]:
        """Only what a person has to answer. Short on a good day."""
        return [r for r in self.pipeline(today=today) if r.owner_must_decide]

    def performance(self) -> dict:
        """Phase 14's figures, with simulated money kept apart from real.

        ``collected_cents`` counts only verified payments recorded
        against engagements. It is labelled as simulated whenever
        simulation is on, because a dashboard that shows synthetic
        revenue under a heading that says Revenue is the single most
        misleading thing this system could render.
        """
        counts = self.crm.counts()
        stages = {s.value: 0 for s in Stage}
        collected = outstanding = contracted = 0
        for deal in self.engagements.values():
            stages[deal.stage.value] += 1
            collected += deal.collected_cents
            outstanding += deal.outstanding_cents
            contracted += deal.proposal.price_cents
        agreed = sum(1 for n in self.negotiations.values()
                     if n.outcome is Outcome.AGREED_PENDING_AUTHORISATION)
        return {
            "prospects": counts["prospects"],
            "with_a_verified_need": counts["with_a_verified_need"],
            "contactable": counts["contactable"],
            "by_industry": counts["by_industry"],
            "by_standing": counts["by_standing"],
            "messages_prepared": len(self.campaign.sent),
            "negotiations": len(self.negotiations),
            "negotiations_agreed": agreed,
            "negotiations_escalated": sum(
                1 for n in self.negotiations.values()
                if n.outcome is Outcome.ESCALATED),
            "engagements": len(self.engagements),
            "by_stage": stages,
            "contracted_cents": contracted,
            "collected_cents": collected,
            "outstanding_cents": outstanding,
            "suppressed_addresses": sum(1 for s in self.suppressions.all()
                                        if s.active),
        }

    def _say(self, prospect_id: str, step: Step, why: str,
             owner_must_decide: bool = False) -> Recommendation:
        return Recommendation(prospect_id, step, why, owner_must_decide)
