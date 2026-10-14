"""One Job Finder, over the discovery that already exists.

Discovery polls sources, respects their rate limits and terms, deduplicates
and records opportunities. It does all of that for every capability already,
so a "job finder" that polled its own sources would be a second discovery
agent with its own compliance surface -- the thing the operating rules forbid.

What was actually missing sits one layer up: of the opportunities discovery
has already found, **which are worth putting through the pipeline at all**.
That is this module, and the boundary is worth stating precisely, because
getting it wrong would create the overlapping authority the rules forbid.

``Qualification.rank`` already orders work best-first, scoring the Governor's
real economics against client history and completion risk. It is the
authority on *what to do next*, and nothing here overrides it. This module
sits before it and answers a cheaper question: is this posting worth the cost
of qualifying -- can anything here do it, could its verification tier be
reached, does it clear the owner's floor, and is the money real? What comes
out is a triage order for qualification's inbox, not a work queue.

It reads. It does not poll, qualify, price, commit, execute or decide.

Three things make this honest rather than a scoring function:

**It separates what a human would charge from what this will actually bill.**
A fixed-price posting is worth its stated price whether the work takes five
hours or five minutes. An hourly posting is worth the rate times the hours
*actually worked*, and for automated work that is usually minutes. These are
not the same number and the Finder never lets one stand in for the other.

**It will not invent the numbers it does not have.** No observed execution
history means hourly revenue is unknown, and the assessment says ``UNKNOWN``
rather than guessing. An unknown does not quietly become a zero, and it does
not quietly become an average.

**It checks reachability before recommending, not after.** An opportunity
whose consequence tier demands a second opinion nothing here can produce is
declined while it is still a posting.
"""

from __future__ import annotations

import dataclasses
import statistics
from fractions import Fraction
from dataclasses import dataclass, field
from enum import Enum

from . import content
from . import quoting
from . import sourceaccess
from .discovery import Opportunity
from .quoting import BillingBasis
from .types import Cents, ConsequenceTier, VerificationTier, fmt

#: Postings whose wording puts them in one camp or the other. Deliberately
#: short: a posting that does not say is UNKNOWN, not assumed.
_FIXED_WORDS = ("fixed price", "fixed-price", "fixed fee", "flat rate",
                "flat fee", "per project", "project budget", "one-off",
                "milestone")
_HOURLY_WORDS = ("per hour", "hourly", "/hr", "an hour", "per-hour",
                 "time and materials", "timesheet")


class ContractType(Enum):
    """How the client intends to pay. Not how valuable the work is."""

    FIXED_PRICE = "FIXED_PRICE"
    HOURLY = "HOURLY"
    #: The posting did not say. Ranked, but never silently treated as either.
    UNKNOWN = "UNKNOWN"


class Verdict(Enum):
    PURSUE = "PURSUE"
    #: Worth doing, but something must be settled first -- usually by a person.
    HOLD = "HOLD"
    DECLINE = "DECLINE"


class Confidence(Enum):
    """How much of the assessment rests on evidence rather than absence."""

    MEASURED = "MEASURED"      # observed execution history for this capability
    PREDICTED = "PREDICTED"    # modelled from configured rates
    #: A median over too few runs to call measured. Separate from both
    #: because a client told "about four minutes" deserves to know
    #: whether that rests on one run or fifty, and rounding one sample
    #: up to MEASURED is the kind of small lie that becomes a missed
    #: deadline.
    PROVISIONAL = "PROVISIONAL"
    UNKNOWN = "UNKNOWN"        # not determinable; says so instead of guessing


@dataclass(frozen=True, slots=True)
class Economics:
    """What the work would earn and cost. Any field may be ``None``."""

    contract_type: ContractType
    revenue_cents: Cents | None
    cost_cents: Cents | None
    profit_cents: Cents | None
    margin: float | None
    confidence: Confidence
    basis: str
    #: Hours a *human* would spend. Context for the client's price. Never
    #: billed, never added to an hourly invoice.
    human_hours: float | None = None
    #: Hours this would actually work, and so the most an hourly contract
    #: could honestly bill before any agreed minimum.
    worked_hours: float | None = None
    #: What an hourly contract would bill after an agreed, permitted minimum.
    billable_hours: float | None = None
    minimum_applied: bool = False


@dataclass(frozen=True, slots=True)
class Assessment:
    """The Finder's read on one opportunity. A recommendation, not a decision."""

    opportunity_id: str
    source: str
    title: str
    verdict: Verdict
    economics: Economics
    score: float
    why: str
    consequence: ConsequenceTier
    required_tier: VerificationTier
    tier_reachable: bool | None = None
    tier_detail: str = ""
    feasible: bool = True
    feasibility_detail: str = ""
    risks: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    #: Set when an hourly posting would earn materially more as fixed price.
    conversion_note: str = ""

    @property
    def pursue(self) -> bool:
        return self.verdict is Verdict.PURSUE


def classify_contract(opportunity: Opportunity) -> ContractType:
    """Read the contract type off the posting, or admit it is not stated.

    Structured platform fields are believed first; they are the platform's
    own schema rather than someone's prose. Only then the text, and only when
    it says one thing and not the other -- a posting mentioning both an
    hourly rate and a project budget is ``UNKNOWN``, because guessing which
    one governs is how an unagreed charge gets raised later.
    """
    declared = (opportunity.payment_structure or "").strip().upper()
    if declared in ("FIXED", "FIXED_PRICE", "FIXED-PRICE", "PROJECT"):
        return ContractType.FIXED_PRICE
    if declared in ("HOURLY", "PER_HOUR", "TIME_AND_MATERIALS"):
        return ContractType.HOURLY

    text = f"{opportunity.title} {opportunity.description}".lower()
    fixed = any(word in text for word in _FIXED_WORDS)
    hourly = any(word in text for word in _HOURLY_WORDS)
    if fixed and not hourly:
        return ContractType.FIXED_PRICE
    if hourly and not fixed:
        return ContractType.HOURLY
    return ContractType.UNKNOWN


@dataclass
class ExecutionHistory:
    """Observed execution times, by capability. The only basis for hourly.

    Empty is a legitimate and common state, and it reads as "unknown", never
    as "fast" or as zero.
    """

    samples: dict[str, list[float]] = field(default_factory=dict)

    @classmethod
    def from_store(cls, store) -> "ExecutionHistory":
        samples: dict[str, list[float]] = {}
        try:
            rows = store.raw_readonly(
                "SELECT capability, wall_seconds FROM execution_measurements "
                "WHERE wall_seconds > 0")
        except Exception:
            # No table yet in an older database. Unknown, not an error.
            return cls()
        for row in rows:
            samples.setdefault(row["capability"], []).append(
                float(row["wall_seconds"]))
        return cls(samples)

    def expected_seconds(self, capability: str) -> float | None:
        seen = self.samples.get(capability) or []
        return statistics.median(seen) if seen else None

    def count(self, capability: str) -> int:
        return len(self.samples.get(capability) or [])


@dataclass(frozen=True, slots=True)
class CompletionEstimate:
    """How long this would take, and how much that estimate is worth.

    Built only from execution DeskPilot has actually performed. There is
    deliberately no fallback: a capability with no measured runs returns
    ``None`` seconds and says so, because a bid whose delivery time was
    guessed is a promise to a client backed by nothing.
    """

    capability: str
    seconds: float | None
    samples: int
    confidence: Confidence
    basis: str

    @property
    def hours(self) -> float | None:
        return None if self.seconds is None else self.seconds / 3600.0

    @property
    def known(self) -> bool:
        return self.seconds is not None


@dataclass(frozen=True, slots=True)
class Bid:
    """A proposed bid. Composed here, never sent from here.

    Sending belongs to ``Applications.submit``, which asks the seven
    gates -- platform permission, owner approval, the Action Gate and
    the rest. A Bid is an opinion about a number until that happens, and
    ``submittable`` says whether it is even worth taking there.
    """

    opportunity_id: str
    capability: str
    amount_cents: Cents | None
    estimate: CompletionEstimate
    floor_cents: Cents
    cost_cents: Cents | None
    margin: float | None
    submittable: bool
    why: str
    basis: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def summary(self) -> str:
        if not self.submittable:
            return f"no bid: {self.why}"
        hours = self.estimate.hours or 0.0
        return (f"bid {fmt(self.amount_cents or 0)} on "
                f"{self.opportunity_id}, delivery in about "
                f"{hours:.2f}h, margin {(self.margin or 0):.0%}")


class Finder:
    """Ranks what discovery already found. Reads only."""

    def __init__(self, discovery, policy, capability, orchestrator=None,
                 store=None):
        self._discovery = discovery
        self._policy = policy
        self._capability = capability
        self._orchestrator = orchestrator
        self._store = store if store is not None else getattr(
            discovery, "store", None)

    # ---- the configured terms, read rather than assumed ----------------

    def _commercial(self, key, default=None):
        return self._policy.get("commercial", key, default=default)

    @property
    def hourly_rate_cents(self) -> Cents:
        return int(self._commercial("hourly_rate_cents", 0) or 0)

    @property
    def minimum_fee_cents(self) -> Cents:
        return int(self._commercial("minimum_project_fee_cents", 0) or 0)

    @property
    def minimum_margin_fraction(self) -> float:
        return float(self._commercial("minimum_margin_fraction", 0) or 0)

    def market_rate_cents(self, capability: str) -> tuple[Cents, str]:
        """The cited market rate for one capability, or zero and why not.

        Returns ``(cents, citation)``. A rate with no citation returns
        zero, which ``quote_fixed_price`` already refuses to price
        against -- so an uncited number cannot reach a quote even if
        somebody puts one in the table.
        """
        rates = dict(self._commercial("market_hourly_cents", {}) or {})
        citations = dict(self._commercial("market_rate_citations", {}) or {})
        cents = int(rates.get(capability, 0) or 0)
        citation = str(citations.get(capability, "")).strip()
        if cents <= 0:
            return 0, f"no market rate is recorded for {capability!r}"
        if not citation:
            return 0, (f"the market rate for {capability!r} has no recorded "
                       "citation, and an uncited rate is not evidence")
        return cents, citation

    # ---- bidding ---------------------------------------------------
    #
    # A method on the Finder, not a Bidder class and not a second
    # component. Bidding is the Finder answering one more question
    # about an opportunity it has already assessed -- "what would we
    # charge, and how fast could we deliver?" -- and the answer is built
    # entirely from authorities that already exist: measured execution
    # history for the time, `quoting` for the cost and the margin, the
    # owner's recorded terms for the floor. A separate bidding agent
    # would need its own copy of all three and would disagree with them
    # the first time one changed.

    def completion_estimate(self, capability: str, *,
                            history: ExecutionHistory | None = None
                            ) -> CompletionEstimate:
        """How long this capability actually takes, from measured runs.

        The median of observed wall times, never a guess. Confidence
        comes from how many runs there have been, because one sample is
        an anecdote and the difference matters to a client being told a
        delivery time.
        """
        history = history if history is not None else ExecutionHistory()
        seconds = history.expected_seconds(capability)
        samples = history.count(capability)
        if seconds is None:
            return CompletionEstimate(
                capability, None, 0, Confidence.UNKNOWN,
                f"no execution of {capability!r} has been measured, so how "
                "long it takes is not known; an unmeasured capability is "
                "not a fast one")
        confidence = (Confidence.MEASURED if samples >= 5
                      else Confidence.PROVISIONAL)
        return CompletionEstimate(
            capability, seconds, samples, confidence,
            f"median of {samples} measured run(s) of {capability!r}: "
            f"{seconds:.1f}s")

    def promised_seconds(self, estimate: "CompletionEstimate", *,
                         queued_seconds: float = 0.0) -> tuple[float, str]:
        """What may honestly be promised, from what was measured.

        A median is the time half of all runs *exceed*. Promising it is
        promising to be late half the time, which is the single most
        common way a service business loses a client it had already
        won. So the promise is the estimate multiplied by the owner's
        safety factor, plus whatever is already queued ahead of it --
        because work does not start when it is accepted, it starts when
        the machine gets to it.

        Returns ``(seconds, basis)``. Seconds is what to promise, not
        what it will take.
        """
        if not estimate.known:
            return 0.0, estimate.basis
        factor = float(self._policy.get("capacity", "deadline_safety_factor",
                                        default=2.0) or 2.0)
        factor = max(1.0, factor)
        work = (estimate.seconds or 0.0) * factor
        total = work + max(0.0, queued_seconds)
        return total, (
            f"{estimate.seconds:.0f}s measured x{factor:g} safety = "
            f"{work:.0f}s"
            + (f", behind {queued_seconds:.0f}s already queued"
               if queued_seconds else "")
            + f"; promise {total:.0f}s, not the {estimate.seconds:.0f}s "
              "median")

    def bid_floor_cents(self, cost_cents: Cents) -> tuple[Cents, str]:
        """The least DeskPilot may charge, and why.

        Two separate owner decisions, and the binding one is whichever
        is higher: the minimum project fee is a floor in cash, the
        minimum margin is a floor as a share of whatever is charged.
        Underneath both sits the cost, because no margin rule should be
        able to produce a price below what the work spends.
        """
        floor = self.minimum_fee_cents
        reason = f"the owner's {fmt(floor)} minimum project fee"
        margin = self.minimum_margin_fraction
        if margin > 0 and margin < 1:
            # price * (1 - margin) >= cost  =>  price >= cost / (1 - margin)
            #
            # In exact arithmetic, because the float version was out by a
            # cent: 70000 / (1 - 0.3) is 100000.00000000001 in binary
            # floating point, and rounding that up gives a floor of
            # $1000.01 for a cost of $700 at a 30% margin. One cent is
            # not money, but a floor that cannot be stated exactly is a
            # floor nobody can check, and "why is it a cent more than I
            # calculated" is a question with no good answer.
            share = Fraction(1) - Fraction(margin).limit_denominator(10_000)
            needed = (-(-cost_cents * share.denominator // share.numerator)
                      if cost_cents and share.numerator else 0)
            if needed > floor:
                floor, reason = needed, (
                    f"{fmt(cost_cents)} of cost at the owner's "
                    f"{margin:.0%} minimum margin")
        if cost_cents > floor:
            floor, reason = cost_cents, (
                f"{fmt(cost_cents)} of cost, which no price may sit below")
        return floor, reason

    def bid(self, opportunity: Opportunity, *,
            history: ExecutionHistory | None = None,
            units: int = 0,
            standard: "quoting.WorkStandard | None" = None) -> Bid:
        """Compose a bid for one opportunity. Never sends it.

        Refuses, rather than guessing, whenever the thing it would need
        is unknown: an unconfigured business, an unmeasured capability,
        a posting naming no budget, or a floor above what the client
        has offered.
        """
        capability = opportunity.needs[0] if opportunity.needs else ""
        estimate = self.completion_estimate(capability, history=history)
        blockers: list[str] = []

        configured, gaps = self.commercially_configured()
        if not configured:
            blockers.extend(gaps)
        if not capability:
            blockers.append("the posting does not say which capability it "
                            "needs, so nothing can be estimated")
        else:
            deployable, why = self._capability.may_deploy(capability)
            if not deployable:
                blockers.append(f"{capability} cannot be deployed: {why}")
        if not estimate.known:
            blockers.append(estimate.basis)

        if blockers:
            return Bid(opportunity.id, capability, None, estimate, 0, None,
                       None, False, blockers[0], blockers=tuple(blockers))

        # Cost first, from the estimate, at the owner's recorded rates.
        # cpu_seconds is taken as equal to wall_seconds, which overstates
        # cost on a job that waits on anything -- the safe direction,
        # because a bid built on an understated cost is the one that
        # loses money.
        rates = quoting.rates_from_policy(self._policy)
        measurement = quoting.ExecutionMeasurement(
            wall_seconds=estimate.seconds or 0.0,
            cpu_seconds=estimate.seconds or 0.0)
        budget = opportunity.net_budget_cents
        # Costed at the client's budget, which is slightly circular: the
        # platform and payment fees are a share of the price, so the
        # cost used to derive the floor is the cost of the *highest*
        # price under consideration. That overstates the floor a little
        # and never understates it, which is the direction to be wrong
        # in -- a floor computed at the floor would be lower, and
        # bidding at it would leave less margin than the owner asked
        # for once the real fees landed.
        cost = quoting.operating_cost(
            measurement, price_cents=max(0, budget), rates=rates)
        floor, floor_reason = self.bid_floor_cents(cost.total_cents)

        if opportunity.quoted_cents <= 0:
            return Bid(opportunity.id, capability, None, estimate, floor,
                       cost.total_cents, None, False,
                       "the posting states no price, so there is nothing to "
                       "bid against and a number here would be invented",
                       basis=cost.basis)
        if budget < floor:
            return Bid(opportunity.id, capability, None, estimate, floor,
                       cost.total_cents, None, False,
                       f"the client's {fmt(budget)} is below {fmt(floor)}, "
                       f"which is {floor_reason}", basis=cost.basis)

        # Competitive, within the owner's rules: the client's own budget
        # is the ceiling -- bidding above what they offered wins nothing
        # -- and the floor is the floor. Between them, DeskPilot bids
        # the client's number rather than undercutting it, because
        # speed is already the advantage and giving away margin on top
        # buys nothing the owner asked for.
        amount = budget
        gain = quoting.profitability(price_cents=amount, cost=cost)
        return Bid(
            opportunity.id, capability, amount, estimate, floor,
            cost.total_cents, gain.margin, True,
            f"{fmt(amount)} against a {fmt(floor)} floor; delivery in about "
            f"{(estimate.hours or 0):.2f}h on {estimate.basis}",
            basis=tuple(cost.basis) + (
                f"floor: {floor_reason}",
                f"completion: {estimate.basis} ({estimate.confidence.value})",
                gain.summary()))

    def commercially_configured(self) -> tuple[bool, list[str]]:
        """May the Finder recommend pursuing paid work at all?

        ``configuration_gaps`` has listed these since it was written, and
        listing them was all that happened: every gap left a default that
        reads as permission. No floor meant no floor applied. Compute at
        zero meant every margin shown was better than the real one. A
        minimum margin was read by the gap list and by nothing else --
        the system complained the number was missing and then never used
        it anywhere.

        So the gaps are a gate now. While any of them is open the Finder
        cannot say PURSUE, because it cannot tell work worth taking from
        work that merely looks like it. It still assesses, ranks and
        explains; it just will not recommend.
        """
        gaps = self.configuration_gaps()
        return (not gaps), gaps

    def minimum_hours_applicable(self, opportunity: Opportunity) -> tuple[bool, str]:
        """May a one-hour minimum be charged on this posting?

        Two separate permissions, and the Finder needs *both*. The owner
        preferring a minimum is not the client agreeing to one, and neither is
        the platform allowing it. Billing a minimum nobody agreed to is the
        unagreed minimum charge the rules forbid, so this defaults to no.
        """
        configured = float(self._commercial("minimum_hours", 0) or 0)
        if configured <= 0:
            return False, "the owner has not configured a minimum engagement"

        permitted, why = self._discovery.may(opportunity.source,
                                             sourceaccess.HOURLY_MINIMUM)
        if not permitted:
            return False, f"the source does not permit it: {why}"

        text = f"{opportunity.title} {opportunity.description}".lower()
        if "no minimum" in text or "without minimum" in text:
            return False, "the posting excludes a minimum charge"
        if not opportunity.structured_terms and not opportunity.owner_entered:
            return (False, "the posting's terms are unconfirmed, and a "
                           "minimum may only be billed where it was agreed")
        return True, f"{configured:g} hour minimum, configured and permitted"

    def configuration_gaps(self) -> list[str]:
        """Commercial terms the owner has not set, or has set in conflict.

        Separate from assessing any one posting, because these are answers
        only the owner can give and each one silently changes every
        assessment. A floor that no hourly job can clear rejects them all
        with a correct-sounding reason, and nothing in the per-job output
        would ever say why.
        """
        gaps: list[str] = []

        rate = self.hourly_rate_cents
        minimum_hours = float(self._commercial("minimum_hours", 0) or 0)
        floor = self.minimum_fee_cents
        if rate > 0 and minimum_hours > 0 and floor > rate * minimum_hours:
            gaps.append(
                f"the minimum project fee ({floor / 100:,.2f}) is above a "
                f"full minimum engagement at the hourly rate "
                f"({rate * minimum_hours / 100:,.2f}), so no hourly job of "
                f"{minimum_hours:g} hour(s) or less can ever be accepted")
        if floor <= 0:
            gaps.append(
                "no minimum project fee is set, so work worth less than the "
                "overhead of taking it will still be recommended")
        rates = dict(self._commercial("market_hourly_cents", {}) or {})
        if not rates:
            gaps.append(
                "no cited market rates are recorded, so a fixed price can "
                "only be taken from what the client already offered, never "
                "proposed from evidence")
        else:
            # The word in that sentence doing the work is "cited". This
            # used to check only that *some* rate existed, so a number
            # typed in with no source closed the gap and the sentence
            # became untrue -- which is worse than the gap, because an
            # uncited rate reads exactly like a researched one once it
            # is in the table.
            uncited = sorted(
                name for name in rates
                if not str((self._commercial("market_rate_citations", {})
                            or {}).get(name, "")).strip())
            if uncited:
                gaps.append(
                    f"{len(uncited)} market rate(s) have no recorded "
                    f"citation ({', '.join(uncited[:3])}), and a rate whose "
                    "source nobody wrote down cannot be told apart from one "
                    "somebody invented")
        if not float(self._commercial("compute_cents_per_cpu_hour", 0) or 0):
            gaps.append(
                "compute is costed at zero, so every margin shown is better "
                "than the real one")
        if float(self._commercial("minimum_margin_fraction", 0) or 0) <= 0:
            gaps.append(
                "no minimum margin is set, so a job clearing cost by one "
                "cent counts as profitable")
        return gaps

    # ---- the three questions -------------------------------------------

    def feasibility(self, opportunity: Opportunity) -> tuple[bool, str]:
        """Can anything here actually do this work, today?"""
        if not opportunity.needs:
            return False, ("the posting does not say what capability it "
                           "needs, so feasibility cannot be established")
        missing = []
        for need in opportunity.needs:
            allowed, why = self._capability.may_deploy(need)
            if not allowed:
                missing.append(f"{need}: {why}")
        if missing:
            return False, "; ".join(missing)
        return True, f"deployable: {', '.join(opportunity.needs)}"

    def verification_reachable(self, opportunity: Opportunity,
                               *, extension: str = "",
                               owner_channel=None) -> tuple[bool | None, str]:
        """Could the tier this job's value demands actually be produced?

        Asked here, before the work is recommended, rather than at delivery
        when the compute is already spent.
        """
        consequence = self._policy.consequence_for(opportunity.net_budget_cents)
        from .orchestrator import REQUIRED_TIER

        required = REQUIRED_TIER[consequence]
        if required.rank <= VerificationTier.T1_DETERMINISTIC.rank:
            return True, (f"{required.value} comes from the capability's own "
                          "certified verifier")
        if required.rank >= VerificationTier.T3_EXTERNAL_FACT.rank:
            return None, (f"{required.value} needs an external authority; "
                          "whether one exists depends on the subject matter")

        from . import independent

        report = independent.available(extension=extension,
                                       owner_channel=owner_channel)
        return report.any_usable, report.summary()

    def economics(self, opportunity: Opportunity, *,
                  history: ExecutionHistory | None = None,
                  units: int = 0,
                  standard: quoting.WorkStandard | None = None) -> Economics:
        """What this would earn, cost and leave -- or which of those is unknown."""
        contract = classify_contract(opportunity)
        rates = quoting.rates_from_policy(self._policy)
        history = history if history is not None else ExecutionHistory()
        capability = opportunity.needs[0] if opportunity.needs else ""

        human_hours = None
        if standard is not None and units > 0:
            human_hours = quoting.human_equivalent(units, standard).hours

        seconds = history.expected_seconds(capability)
        worked_hours = (seconds / 3600.0) if seconds is not None else None

        if contract is ContractType.FIXED_PRICE:
            if opportunity.quoted_cents <= 0:
                # No stated price is not a price of zero. Treating it as
                # zero declined the posting as "$0.00 is below the $40.00
                # minimum", which is a false statement about a posting that
                # named no figure -- and on a real board a large share of
                # listings name none. Unknown revenue is a HOLD: a person
                # asks what the budget is. Deliberately keyed on the quoted
                # figure, not the net: a stated price eaten by platform fees
                # really does earn nothing, and that is a decline.
                return Economics(
                    contract, None, None, None, None, Confidence.UNKNOWN,
                    "the posting states no price, and a posting that names "
                    "no figure is not a posting offering nothing",
                    human_hours=human_hours)
            revenue = opportunity.net_budget_cents
            basis = ("the client's stated price, which a fixed-price contract "
                     "earns in full however long the work actually takes")
            confidence = Confidence.MEASURED
        elif contract is ContractType.HOURLY:
            rate = self.hourly_rate_cents
            if rate <= 0:
                return Economics(
                    contract, None, None, None, None, Confidence.UNKNOWN,
                    "no hourly rate is configured, and inventing one would "
                    "misstate what this job is worth",
                    human_hours=human_hours)
            if worked_hours is None:
                return Economics(
                    contract, None, None, None, None, Confidence.UNKNOWN,
                    f"an hourly contract bills the hours actually worked, and "
                    f"no execution has been observed for {capability!r} yet",
                    human_hours=human_hours)
            billable = worked_hours
            applied, _ = self.minimum_hours_applicable(opportunity)
            if applied:
                configured = float(self._commercial("minimum_hours", 0) or 0)
                billable = max(billable, configured)
            revenue = int(round(rate * billable))
            basis = (f"{billable:.3f} billable hour(s) at "
                     f"{rate / 100:,.2f}/hour, from "
                     f"{history.count(capability)} observed run(s)")
            confidence = Confidence.MEASURED
        else:
            return Economics(
                contract, None, None, None, None, Confidence.UNKNOWN,
                "the posting does not state whether it is fixed-price or "
                "hourly, and the two pay very differently for the same work",
                human_hours=human_hours)

        lines = quoting.predicted_operating_lines(
            price_cents=revenue, rates=rates,
            compute_allowance_cents=int(
                self._commercial("compute_allowance_cents", 0) or 0))
        cost = sum(line.amount_cents for line in lines)
        cost += opportunity.upfront_cost_cents
        profit = revenue - cost
        margin = (profit / revenue) if revenue > 0 else None

        billable = worked_hours
        minimum_applied = False
        if contract is ContractType.HOURLY:
            applied, _ = self.minimum_hours_applicable(opportunity)
            if applied:
                configured = float(self._commercial("minimum_hours", 0) or 0)
                if worked_hours is not None and configured > worked_hours:
                    billable, minimum_applied = configured, True

        return Economics(
            contract, revenue, cost, profit, margin, confidence, basis,
            human_hours=human_hours, worked_hours=worked_hours,
            billable_hours=billable, minimum_applied=minimum_applied)

    # ---- putting it together --------------------------------------------

    def assess(self, opportunity: Opportunity, *,
               history: ExecutionHistory | None = None,
               extension: str = "", owner_channel=None,
               units: int = 0,
               standard: quoting.WorkStandard | None = None) -> Assessment:
        unknowns: list[str] = []
        risks = list(opportunity.risk_indicators)

        # A posting that tries to issue instructions is not thereby
        # dangerous -- nothing downstream obeys a posting, and the Gate,
        # the Governor and the owner all still stand between it and any
        # effect. But it is a fact about whoever wrote it, and a system
        # that pursues it like any other listing has thrown that fact
        # away. It reaches a person instead.
        if content.looks_like_instructions(opportunity.description):
            risks.append("posting_issues_instructions")

        consequence = self._policy.consequence_for(opportunity.net_budget_cents)
        from .orchestrator import REQUIRED_TIER

        required = REQUIRED_TIER[consequence]

        feasible, feasibility_detail = self.feasibility(opportunity)
        reachable, tier_detail = self.verification_reachable(
            opportunity, extension=extension, owner_channel=owner_channel)
        econ = self.economics(opportunity, history=history, units=units,
                              standard=standard)

        if econ.confidence is Confidence.UNKNOWN:
            unknowns.append(econ.basis)
        if reachable is None:
            unknowns.append(tier_detail)

        conversion = self._conversion_advice(opportunity, econ)

        verdict, why = self._verdict(
            opportunity, econ, feasible, feasibility_detail, reachable,
            tier_detail, unknowns, tuple(risks))
        score = self._score(econ, verdict, opportunity)

        return Assessment(
            opportunity_id=opportunity.id, source=opportunity.source,
            title=opportunity.title, verdict=verdict, economics=econ,
            score=score, why=why, consequence=consequence,
            required_tier=required, tier_reachable=reachable,
            tier_detail=tier_detail, feasible=feasible,
            feasibility_detail=feasibility_detail, risks=tuple(risks),
            unknowns=tuple(unknowns), conversion_note=conversion)

    def _conversion_advice(self, opportunity, econ) -> str:
        """Say when the same scope is worth more as a fixed price.

        The evidence is the client's own number. A posting that offers a
        budget far above what the hours will honestly bill is a client who
        has priced the *outcome*, not the time -- and billing them two
        minutes is both correct and a poor trade for everyone. The advice is
        to propose a fixed price for the same scope, never to pad the hours.

        Deliberately not based on a human-equivalent estimate: those need a
        cited work standard, almost none are recorded, and an uncited one is
        an opinion. The budget in front of us is a fact.
        """
        if econ.contract_type is not ContractType.HOURLY:
            return ""
        if econ.revenue_cents is None or econ.revenue_cents <= 0:
            return ""

        offered = opportunity.net_budget_cents
        if offered > econ.revenue_cents * 2:
            note = (
                f"billed honestly by the hour this earns "
                f"{econ.revenue_cents / 100:,.2f}, against the "
                f"{offered / 100:,.2f} the posting budgets for it. Where the "
                "platform permits it, proposing a fixed price for the same "
                "scope reflects what is delivered; the hours must not be "
                "padded to close the gap")
            if econ.human_hours is not None and econ.worked_hours is not None:
                note += (f" (a person would spend about "
                         f"{econ.human_hours:.1f} hour(s); this takes "
                         f"{econ.worked_hours * 60:.1f} minute(s))")
            return note
        return ""

    def _verdict(self, opportunity, econ, feasible, feasibility_detail,
                 reachable, tier_detail, unknowns, risks=()):
        if not feasible:
            return Verdict.DECLINE, f"nothing here can do it ({feasibility_detail})"
        if reachable is False:
            return Verdict.DECLINE, (
                f"its value demands verification this installation cannot "
                f"produce ({tier_detail})")
        if econ.revenue_cents is None:
            return Verdict.HOLD, (
                f"what it would earn is not known: {econ.basis}")

        # Risk before price, and the order is the point. A posting below
        # the owner's floor is declined either way, so the outcome looks
        # the same -- but declining it as "too cheap" throws away the fact
        # that somebody tried to harvest credentials, and a pattern of
        # cheap fraudulent postings would be invisible. Worse, the day the
        # owner lowers the floor, those postings become eligible with
        # their risk never examined. Found by the unseen exam.
        if risks:
            return Verdict.HOLD, (
                "worth a person's eye before anything else: the posting "
                f"carries {len(risks)} risk indicator(s): "
                + ", ".join(risks[:3]))

        minimum = self.minimum_fee_cents
        if minimum > 0 and econ.revenue_cents < minimum:
            return Verdict.DECLINE, (
                f"{econ.revenue_cents / 100:,.2f} is below the "
                f"{minimum / 100:,.2f} minimum the owner set")
        if econ.profit_cents is not None and econ.profit_cents <= 0:
            return Verdict.DECLINE, (
                f"it would cost {(econ.cost_cents or 0) / 100:,.2f} to earn "
                f"{econ.revenue_cents / 100:,.2f}")

        # The owner's margin, applied. Clearing cost by a cent is not a
        # business; the floor says what "worth doing" means in money and
        # this says what it means as a share.
        margin = self.minimum_margin_fraction
        if (margin > 0 and econ.profit_cents is not None
                and econ.revenue_cents > 0):
            required = econ.revenue_cents * margin
            if econ.profit_cents < required:
                return Verdict.DECLINE, (
                    f"{econ.profit_cents / 100:,.2f} on "
                    f"{econ.revenue_cents / 100:,.2f} is a "
                    f"{econ.profit_cents / econ.revenue_cents:.1%} margin, "
                    f"below the {margin:.0%} the owner requires")

        # Last, because it is a fact about the installation rather than
        # about this posting: every per-job question above has been
        # answered and recorded, and only then does an unconfigured
        # business stop the recommendation.
        configured, gaps = self.commercially_configured()
        if not configured:
            return Verdict.HOLD, (
                "this looks worth taking, but the commercial terms that "
                "decide what 'worth taking' means are not set, so the "
                f"answer cannot be trusted: {gaps[0]}"
                + (f" (and {len(gaps) - 1} more)" if len(gaps) > 1 else ""))
        if reachable is None:
            return Verdict.HOLD, (
                f"worth doing, but {tier_detail.lower()}")
        return Verdict.PURSUE, (
            f"{(econ.profit_cents or 0) / 100:,.2f} expected on "
            f"{econ.revenue_cents / 100:,.2f}; {econ.basis}")

    def _score(self, econ, verdict, opportunity) -> float:
        """How promising this looks, for triage only.

        Deliberately not a weighted basket of tunable factors: a score nobody
        can explain is a score nobody can challenge. Anything not worth
        pursuing scores zero rather than competing on a technicality.

        It is also deliberately cruder than ``Qualification._score``, which
        has the Governor's priced estimate, the client's history and a
        completion probability to work with. Making this one match it would
        be a second opinion on a question this layer is not asking.
        """
        if verdict is not Verdict.PURSUE or not econ.profit_cents:
            return 0.0
        score = float(econ.profit_cents)
        if econ.confidence is Confidence.PREDICTED:
            score *= 0.5
        score *= float(min(max(opportunity.confidence, 0.0), 1.0))
        return round(score, 2)

    def rank(self, opportunities, **kwargs) -> list[Assessment]:
        """Triage order for qualification's inbox. Not a work queue.

        Most promising first, so that a limited qualification budget is spent
        on the postings most likely to survive it. ``Qualification.rank``
        decides the actual order of work afterwards, on the Governor's
        figures; this ordering has no authority over that one and is not
        consulted again once a Decision exists.

        Ties keep discovery's order, so the triage is repeatable.
        """
        assessed = [self.assess(o, **kwargs) for o in opportunities]
        return sorted(assessed, key=lambda a: -a.score)

    def next_to_qualify(self, opportunities, **kwargs) -> Assessment | None:
        """The posting worth qualifying first, or None when none is.

        Deliberately not called ``next_job``: nothing here selects a job.
        Qualification, the Governor and the owner decide that, in that order.
        """
        for assessment in self.rank(opportunities, **kwargs):
            if assessment.pursue:
                return assessment
        return None
