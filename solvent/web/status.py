"""One status vocabulary for the whole control centre, and the rules behind it.

Every page used to invent its own words. ``DONE``/``ACTION``, ``ON``/``OFF``,
``ENGAGED``, ``SIMULATED``, ``HEALTHY``/``DEGRADED``/``FAILED`` — each sensible
where it stood, and collectively a surface where the owner had to learn what
green meant on this page as opposed to that one. Worse, three of those
vocabularies had no way to say *I cannot tell*, so a page that could not
establish a fact reported the reassuring half of a boolean.

Five states, and the distinctions between them are the point:

``VERIFIED HEALTHY``
    Checked, just now, against evidence. Not "configured", not "started
    successfully once". If the most recent evidence is older than the thing
    being described changes, this is not the answer.

``OWNER ACTION REQUIRED``
    Working as designed, and waiting for a decision only the owner can make.
    Nothing is wrong.

``UNKNOWN``
    The control centre could not establish the fact. A projection that returned
    nothing, a file it may not read, a report too old to describe now. This is
    the state the old vocabularies were missing, and its absence is why a
    halted business read as NORMAL for the life of this surface.

``UNSAFE``
    Established, and the answer is bad. Real execution permitted without the
    controls that are supposed to accompany it; an audit chain that does not
    verify.

``BLOCKED``
    Cannot proceed, and not because of anything the owner has left undone — a
    platform that forbids it, a dependency that is absent, a certification that
    failed.

Two rules are enforced by :func:`healthy` and tested rather than remembered:

1. **Nothing reads VERIFIED HEALTHY without evidence.** The constructor takes
   the evidence and refuses to build the healthy state without it.
2. **Stale evidence is not evidence.** Freshness is a parameter, and what
   happens when it lapses is ``UNKNOWN``, never the previous answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

VERIFIED_HEALTHY = "VERIFIED HEALTHY"
OWNER_ACTION_REQUIRED = "OWNER ACTION REQUIRED"
UNKNOWN = "UNKNOWN"
UNSAFE = "UNSAFE"
BLOCKED = "BLOCKED"

#: Every state, in the order a reader should scan them: worst first, because a
#: page with one UNSAFE row and nineteen healthy ones is an unsafe page.
ORDER = (UNSAFE, BLOCKED, UNKNOWN, OWNER_ACTION_REQUIRED, VERIFIED_HEALTHY)

#: Which pill tone each state renders as. UNKNOWN is deliberately not neutral:
#: a fact nobody could establish is a warning, and rendering it grey made it
#: read as "nothing to see".
TONE = {
    VERIFIED_HEALTHY: "ok",
    OWNER_ACTION_REQUIRED: "info",
    UNKNOWN: "warn",
    UNSAFE: "bad",
    BLOCKED: "bad",
}


@dataclass(frozen=True)
class Status:
    """One fact, its state, and what the state rests on."""

    state: str
    #: One sentence for the owner. Says what is true, not what to feel.
    detail: str
    #: What this rests on. Required for VERIFIED_HEALTHY, and the reason that
    #: state cannot be reached by accident.
    evidence: str = ""
    #: What the owner should do, when there is something to do.
    action: str = ""
    #: Age of the evidence in seconds, where the fact has an age at all.
    age_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.state not in TONE:
            raise ValueError(
                f"{self.state!r} is not one of the five states; a sixth status "
                f"word is how this surface came to have five vocabularies")
        if self.state == VERIFIED_HEALTHY and not self.evidence.strip():
            raise ValueError(
                "VERIFIED HEALTHY needs evidence. A healthy state with nothing "
                "behind it is the claim this module exists to prevent")

    @property
    def tone(self) -> str:
        return TONE[self.state]

    @property
    def worst_first(self) -> int:
        return ORDER.index(self.state)


def healthy(detail: str, evidence: str, *, age_seconds: float | None = None
            ) -> Status:
    return Status(VERIFIED_HEALTHY, detail, evidence=evidence,
                  age_seconds=age_seconds)


def owner_action(detail: str, action: str, *, evidence: str = "") -> Status:
    return Status(OWNER_ACTION_REQUIRED, detail, evidence=evidence,
                  action=action)


def unknown(detail: str, *, action: str = "", age_seconds: float | None = None
            ) -> Status:
    return Status(UNKNOWN, detail, action=action, age_seconds=age_seconds)


def unsafe(detail: str, *, action: str = "", evidence: str = "") -> Status:
    return Status(UNSAFE, detail, evidence=evidence, action=action)


def blocked(detail: str, *, action: str = "", evidence: str = "") -> Status:
    return Status(BLOCKED, detail, evidence=evidence, action=action)


def fresh(detail: str, evidence: str, *, age_seconds: float | None,
          stale_after: float, missing: str = "") -> Status:
    """``VERIFIED HEALTHY`` while the evidence is fresh, ``UNKNOWN`` after.

    The one function that decides whether an age is still evidence, so the
    rule lives in one place rather than in each page's idea of recent. Passing
    ``None`` means there is no evidence at all, which is ``UNKNOWN`` and not a
    failure: nothing was established, and that is a different sentence from
    "something is wrong".
    """
    if age_seconds is None:
        return unknown(missing or f"{detail} — nothing has reported this")
    if age_seconds > stale_after:
        return unknown(
            f"{detail} — last evidence is {_ago(age_seconds)} old, which is "
            f"too old to describe now",
            age_seconds=age_seconds)
    return healthy(detail, evidence, age_seconds=age_seconds)


def _ago(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


def worst(statuses) -> str:
    """The state a summary should carry. Worst wins; no statuses is UNKNOWN."""
    found = [s.state for s in statuses]
    if not found:
        return UNKNOWN
    for state in ORDER:
        if state in found:
            return state
    return UNKNOWN


@dataclass
class Board:
    """A named set of statuses, summarised by its worst member."""

    rows: list[tuple[str, Status]] = field(default_factory=list)

    def add(self, name: str, status: Status) -> None:
        self.rows.append((name, status))

    @property
    def summary(self) -> str:
        return worst([status for _name, status in self.rows])

    def needing_attention(self) -> list[tuple[str, Status]]:
        return [(name, status) for name, status in self.rows
                if status.state != VERIFIED_HEALTHY]

    def sorted_worst_first(self) -> list[tuple[str, Status]]:
        return sorted(self.rows, key=lambda row: row[1].worst_first)
