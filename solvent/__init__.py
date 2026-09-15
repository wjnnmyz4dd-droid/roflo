"""Solvent — an AI-operated service business.

**Solvent exists to find, qualify, complete, deliver and profit from legitimate
client work.** That is the mission. Everything else in this package exists to
protect it.

The business loop, which is the product:

    DISCOVER -> QUALIFY -> CAPABILITY -> CONFORMANCE -> PRICE -> PROFITABILITY
    -> PERMISSION -> ACCEPT -> EXECUTE -> VERIFY -> DELIVER -> GET PAID
    -> ACTUAL PROFIT -> LEARN -> FIND THE NEXT GOOD JOB

Solvent is **not** primarily a generic adaptive AI, a self-improvement
experiment, an AI board of directors, a consultant, or a project-management
platform. Those may one day be capabilities it sells; none of them is the
mission. If a change makes Solvent cleverer without making it better at finding,
doing and getting paid for work, it is off-mission.

The six governing authorities are infrastructure, not the product. Each owns
exactly one consequential decision so that no two components can decide the same
thing:

    Policy Store       may Solvent do this class of thing at all?
    Financial Governor is this specific spend financially acceptable?
    Action Gate        may this specific external action leave Solvent?
    Job Orchestrator   what state is this job in, and may it advance?
    Ledger             what money actually moved?
    Audit Log          what happened, and on whose authority?

Acquisition adds two more from the same map: **Discovery** finds candidate work
and can never accept it, and **Qualification** decides what is worth taking while
owning no economics of its own.

The package deliberately has **no third-party dependencies**. Every dependency is
a potential path around the Action Gate, so the smallest trustworthy core is one
that imports only the standard library.

See ``SOLVENT.md`` for the canonical mission statement.
"""

from .types import (  # noqa: F401
    ActionClass, BlockedOn, CapabilityVerdict, Conformance, ConsequenceTier,
    CostCategory, GovernorVerdict, JobState, OperatingMode, PaymentState,
    PermissionLevel, PricingTier, PrivacyClass, RequirementSource, VerificationTier,
)
from .errors import (  # noqa: F401
    AuthorityError, EgressDenied, FailClosed, GovernorRefused, ImmutableRecord,
    PermissionDenied, SolventError,
)

__all__ = ["types", "errors"]
__version__ = "0.1.0"
