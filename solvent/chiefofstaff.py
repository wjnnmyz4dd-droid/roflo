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

from dataclasses import dataclass, field
from enum import Enum

from .crm import CRM, Prospect, Standing
from .engagement import Engagement, Stage
from .errors import FailClosed
from .negotiation import Negotiation, Outcome
from .outreach import Campaign, Suppressions, may_write


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


class ChiefOfStaff:
    """Holds the sequence. Asks the authorities. Escalates when stuck."""

    def __init__(self, *, crm: CRM, suppressions: Suppressions,
                 campaign: Campaign | None = None, audit=None) -> None:
        self.crm = crm
        self.suppressions = suppressions
        self.campaign = campaign or Campaign()
        self._audit = audit
        self.negotiations: dict[str, Negotiation] = {}
        self.engagements: dict[str, Engagement] = {}

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
