"""One Job Finder, over the discovery that already exists.

Discovery polls sources, respects their rate limits and terms, deduplicates
and records opportunities. It does all of that for every capability already,
so a "job finder" that polled its own sources would be a second discovery
agent with its own compliance surface -- the thing the operating rules forbid.

What was actually missing sits one layer up: of the opportunities discovery
has already found, **which one should be done next, and is it worth doing at
all**. That is this module. It reads; it does not poll, qualify, price,
commit, execute or decide. Qualification still qualifies. The Governor still
holds the money. The owner still approves what needs approving.

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
from dataclasses import dataclass, field
from enum import Enum

from . import quoting
from .discovery import Opportunity
from .quoting import BillingBasis
from .types import Cents, ConsequenceTier, VerificationTier

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
                                             "hourly_minimum")
        if not permitted:
            return False, f"the source does not permit it: {why}"

        text = f"{opportunity.title} {opportunity.description}".lower()
        if "no minimum" in text or "without minimum" in text:
            return False, "the posting excludes a minimum charge"
        if not opportunity.structured_terms and not opportunity.owner_entered:
            return (False, "the posting's terms are unconfirmed, and a "
                           "minimum may only be billed where it was agreed")
        return True, f"{configured:g} hour minimum, configured and permitted"

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

        conversion = ""
        if (econ.contract_type is ContractType.HOURLY
                and econ.human_hours is not None
                and econ.worked_hours is not None
                and econ.human_hours > econ.worked_hours * 2):
            conversion = (
                f"a human would spend about {econ.human_hours:.1f} hour(s) on "
                f"this; billed hourly it would honestly earn "
                f"{econ.billable_hours or 0:.2f} hour(s). Where the platform "
                "permits it, proposing a fixed price for the same scope "
                "reflects the value delivered without billing hours nobody "
                "worked")

        verdict, why = self._verdict(
            opportunity, econ, feasible, feasibility_detail, reachable,
            tier_detail, unknowns)
        score = self._score(econ, verdict, opportunity)

        return Assessment(
            opportunity_id=opportunity.id, source=opportunity.source,
            title=opportunity.title, verdict=verdict, economics=econ,
            score=score, why=why, consequence=consequence,
            required_tier=required, tier_reachable=reachable,
            tier_detail=tier_detail, feasible=feasible,
            feasibility_detail=feasibility_detail, risks=tuple(risks),
            unknowns=tuple(unknowns), conversion_note=conversion)

    def _verdict(self, opportunity, econ, feasible, feasibility_detail,
                 reachable, tier_detail, unknowns):
        if not feasible:
            return Verdict.DECLINE, f"nothing here can do it ({feasibility_detail})"
        if reachable is False:
            return Verdict.DECLINE, (
                f"its value demands verification this installation cannot "
                f"produce ({tier_detail})")
        if econ.revenue_cents is None:
            return Verdict.HOLD, (
                f"what it would earn is not known: {econ.basis}")

        minimum = self.minimum_fee_cents
        if minimum > 0 and econ.revenue_cents < minimum:
            return Verdict.DECLINE, (
                f"{econ.revenue_cents / 100:,.2f} is below the "
                f"{minimum / 100:,.2f} minimum the owner set")
        if econ.profit_cents is not None and econ.profit_cents <= 0:
            return Verdict.DECLINE, (
                f"it would cost {(econ.cost_cents or 0) / 100:,.2f} to earn "
                f"{econ.revenue_cents / 100:,.2f}")
        if reachable is None:
            return Verdict.HOLD, (
                f"worth doing, but {tier_detail.lower()}")
        if opportunity.risk_indicators:
            return Verdict.HOLD, (
                "worth doing, but the posting carries "
                f"{len(opportunity.risk_indicators)} risk indicator(s): "
                + ", ".join(opportunity.risk_indicators[:3]))
        return Verdict.PURSUE, (
            f"{(econ.profit_cents or 0) / 100:,.2f} expected on "
            f"{econ.revenue_cents / 100:,.2f}; {econ.basis}")

    def _score(self, econ, verdict, opportunity) -> float:
        """Expected profit, discounted by how much of it is actually known.

        Deliberately not a weighted basket of tunable factors: a score nobody
        can explain is a score nobody can challenge. Anything not worth
        pursuing scores zero rather than competing on a technicality.
        """
        if verdict is not Verdict.PURSUE or not econ.profit_cents:
            return 0.0
        score = float(econ.profit_cents)
        if econ.confidence is Confidence.PREDICTED:
            score *= 0.5
        score *= float(min(max(opportunity.confidence, 0.0), 1.0))
        return round(score, 2)

    def rank(self, opportunities, **kwargs) -> list[Assessment]:
        """Best first. Ties keep discovery's order, so ranking is repeatable."""
        assessed = [self.assess(o, **kwargs) for o in opportunities]
        return sorted(assessed, key=lambda a: -a.score)

    def next_job(self, opportunities, **kwargs) -> Assessment | None:
        """The one to do next, or None when nothing qualifies."""
        for assessment in self.rank(opportunities, **kwargs):
            if assessment.pursue:
                return assessment
        return None
