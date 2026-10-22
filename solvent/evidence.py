"""Where a number came from, carried with the number.

Lifted out of :mod:`solvent.upworkbid`, which is where it was first
written and the wrong home for it: competitive market rates are not an
Upwork concern, and a module about one marketplace should not be the
place every other module goes for the idea of evidence.

The rule the whole file exists to enforce: **a value and its
provenance travel together or not at all.** A number with no source
and a source with no number are both ways of saying something the
evidence does not support, and :class:`Figure` refuses to hold either.

UNAVAILABLE is deliberately distinct from zero *and* from silence. An
owner reading a quote has to be able to tell "we looked and could not
get it" from "we did not look" from "it is nothing".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import FailClosed
from .types import fmt


class Provenance(Enum):
    """Where a number came from. Required, never inferred, never blank."""

    #: Read from Upwork, through an official route, for this opportunity.
    VERIFIED_PLATFORM_DATA = "VERIFIED PLATFORM DATA"
    #: Measured from DeskPilot's own record of what actually happened.
    HISTORICAL_OBSERVATION = "HISTORICAL OBSERVATION"
    #: Computed. A guess with arithmetic behind it is still a guess.
    MODEL_ESTIMATE = "MODEL ESTIMATE"
    #: Not obtainable. Distinct from zero, and distinct from "we did not look".
    UNAVAILABLE = "UNAVAILABLE"


class Confidence(Enum):
    """How much weight the figure will bear."""

    INSUFFICIENT_DATA = "INSUFFICIENT DATA"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class Figure:
    """A number and where it came from, which travel together or not at all.

    ``value`` is ``None`` exactly when the provenance is ``UNAVAILABLE``;
    the constructor refuses any other combination, because a number
    without a source and a source without a number are both ways of
    saying something the evidence does not support.
    """

    value: float | int | None
    provenance: Provenance
    detail: str = ""

    def __post_init__(self) -> None:
        unavailable = self.provenance is Provenance.UNAVAILABLE
        if unavailable and self.value is not None:
            raise FailClosed(
                "a figure marked UNAVAILABLE carries a value, which is a "
                f"contradiction: {self.value!r} ({self.detail})")
        if not unavailable and self.value is None:
            raise FailClosed(
                f"a figure with no value is marked {self.provenance.value}; "
                "an absent number is UNAVAILABLE, not an estimate")
        if unavailable and not self.detail:
            raise FailClosed(
                "an UNAVAILABLE figure must say why it is unavailable, or "
                "the owner cannot tell 'we could not get it' from 'we did "
                "not look'")

    @property
    def known(self) -> bool:
        return self.provenance is not Provenance.UNAVAILABLE

    @property
    def verified(self) -> bool:
        return self.provenance is Provenance.VERIFIED_PLATFORM_DATA

    def render(self, money: bool = False) -> str:
        """For the owner to read. The provenance is never dropped."""
        if not self.known:
            return f"UNAVAILABLE ({self.detail})"
        shown = fmt(int(self.value)) if money else f"{self.value:g}"
        return f"{shown} [{self.provenance.value}]" + (
            f" — {self.detail}" if self.detail else "")


def unavailable(why: str) -> Figure:
    return Figure(None, Provenance.UNAVAILABLE, why)


def estimate(value, why: str) -> Figure:
    return Figure(value, Provenance.MODEL_ESTIMATE, why)


def observed(value, why: str) -> Figure:
    return Figure(value, Provenance.HISTORICAL_OBSERVATION, why)


def platform(value, why: str) -> Figure:
    return Figure(value, Provenance.VERIFIED_PLATFORM_DATA, why)
