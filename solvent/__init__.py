"""Solvent — an owner-governed business operating system.

Solvent is built to one law: **one responsibility, one authoritative owner**. Six
authorities exist at P0, and each owns exactly one consequential decision:

    Policy Store      may Solvent do this class of thing at all?
    Financial Governor is this specific spend financially acceptable?
    Action Gate       may this specific external action leave Solvent?
    Job Orchestrator  what state is this job in, and may it advance?
    Ledger            what money actually moved?
    Audit Log         what happened, and on whose authority?

Nothing here has a second opinion. A component that wants one of those answers
asks the authority that owns it; it never computes its own.

The package deliberately has **no third-party dependencies**. Every dependency is
a potential path around the Action Gate (a library that opens its own socket), so
the smallest trustworthy core is one that imports only the standard library.
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
