"""The vocabulary the installer reports in, and the redaction that guards it.

Two things live here because they are the same concern seen twice: saying
exactly what is true, and never saying more than is true.

**Status is not a boolean.** The defect this installer exists partly to fix is
an AI backend that read as working because a configuration file named it. So
the states are kept separate and ordered: a thing can be *supported* by the
code, *configured* by the owner, *installed* on the disk, *reachable* over the
network, and only then *ready*. Collapsing those into "ok" is how the previous
experience lied, so :class:`Level` refuses to provide a single truthy test and
:func:`ready` is the only thing that answers the question the owner asked.

**Redaction is structural.** :class:`Secret` wraps a value so that the ordinary
ways a value reaches a log -- ``str``, ``repr``, f-strings, ``json`` -- all
produce the mask instead. A rule that says "do not log the owner key" is a rule
someone forgets; a type whose ``__repr__`` cannot leak is one they cannot.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, replace
from typing import Any

#: What :class:`Secret` shows instead of itself, everywhere.
MASK = "[redacted]"


class Secret:
    """A value that must never reach a log, a screen, or a command line.

    The point is the absence of an accessor that looks innocent. ``reveal()``
    is deliberately ugly to read at a call site, so the handful of places
    genuinely entitled to the plaintext are visible in review.
    """

    __slots__ = ("_value", "_label")

    def __init__(self, value: str, label: str = "secret") -> None:
        self._value = value
        self._label = label

    def reveal(self) -> str:
        """The plaintext. Call sites are audited; do not add a nicer alias."""
        return self._value

    @property
    def label(self) -> str:
        return self._label

    @property
    def present(self) -> bool:
        return bool(self._value)

    def __str__(self) -> str:
        return MASK

    def __repr__(self) -> str:
        return f"Secret({self._label}={MASK})"

    def __format__(self, spec: str) -> str:
        return MASK

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)


class Level(enum.Enum):
    """How far along one component is. Ordered, and deliberately not boolean.

    ``SUPPORTED`` through ``READY`` are the five words §9 of the installer
    contract insists are not synonyms. ``ABSENT`` and ``FAILED`` are the two
    ways a component can be not-even-supported and broken-despite-trying.
    """

    ABSENT = "ABSENT"
    SUPPORTED = "SUPPORTED"
    CONFIGURED = "CONFIGURED"
    INSTALLED = "INSTALLED"
    REACHABLE = "REACHABLE"
    READY = "READY"
    FAILED = "FAILED"

    @property
    def rank(self) -> int:
        return _LEVEL_ORDER.index(self)

    def at_least(self, other: "Level") -> bool:
        """Ordering comparison that refuses to answer for ``FAILED``.

        A failed component is not "below ready", it is outside the ladder:
        treating it as a low rank is how a failure becomes a warning.
        """
        if Level.FAILED in (self, other):
            return self is other
        return self.rank >= other.rank


_LEVEL_ORDER = (Level.ABSENT, Level.SUPPORTED, Level.CONFIGURED,
                Level.INSTALLED, Level.REACHABLE, Level.READY, Level.FAILED)


def ready(level: Level) -> bool:
    """The only function that may answer "is this usable?"."""
    return level is Level.READY


class Outcome(enum.Enum):
    """The result of one check on one line of the owner's screen."""

    PASS = "PASS"
    #: True, and nothing need be done.
    WILL_INSTALL = "WILL INSTALL"
    #: Not present, and the installer intends to supply it.
    ACTION = "ACTION"
    #: Needs the owner to decide or supply something.
    WARN = "WARN"
    #: Worth saying, does not stop installation.
    BLOCKED = "BLOCKED"
    #: Stops installation until a human changes something outside DeskPilot.
    FAIL = "FAIL"
    #: Tried and did not work.

    @property
    def stops_installation(self) -> bool:
        return self in (Outcome.BLOCKED, Outcome.FAIL)


@dataclass(frozen=True, slots=True)
class Row:
    """One line the owner reads. ``detail`` is for humans, not for parsing."""

    name: str
    outcome: Outcome
    detail: str
    #: Set when this row is about a component with a maturity level.
    level: Level | None = None
    #: Machine-readable extras for the Advanced Details view (§22).
    facts: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name,
                               "outcome": self.outcome.value,
                               "detail": self.detail}
        if self.level is not None:
            out["level"] = self.level.value
        if self.facts:
            out["facts"] = _scrub(self.facts)
        return out


@dataclass(frozen=True, slots=True)
class Failure:
    """The five things §21 requires instead of a stack trace.

    ``changed`` and ``not_changed`` are both required rather than one being
    inferred from the other, because the question the owner is actually asking
    after a failed installer is "what state is my machine in now", and an empty
    ``changed`` list is a meaningful, reassuring answer that must be said out
    loud rather than left to inference.
    """

    what_failed: str
    why: str
    changed: tuple[str, ...]
    not_changed: tuple[str, ...]
    safe_next_action: str

    def to_json(self) -> dict[str, Any]:
        return {"what_failed": self.what_failed, "why": self.why,
                "changed": list(self.changed),
                "not_changed": list(self.not_changed),
                "safe_next_action": self.safe_next_action}

    def render(self) -> str:
        lines = [self.what_failed.upper(), "",
                 f"Why: {self.why}", "",
                 "What was changed:"]
        lines += [f"  - {c}" for c in self.changed] or ["  nothing"]
        lines += ["", "What was NOT changed:"]
        lines += [f"  - {c}" for c in self.not_changed] or ["  nothing stated"]
        lines += ["", f"Safe next action: {self.safe_next_action}"]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Report:
    """A phase's result: the rows, and whether the owner may continue."""

    phase: str
    rows: tuple[Row, ...]
    failure: Failure | None = None

    @property
    def may_continue(self) -> bool:
        """False if any row stops installation. Fail closed by construction."""
        if self.failure is not None:
            return False
        return not any(r.outcome.stops_installation for r in self.rows)

    @property
    def blockers(self) -> tuple[Row, ...]:
        return tuple(r for r in self.rows if r.outcome.stops_installation)

    def row(self, name: str) -> Row | None:
        for r in self.rows:
            if r.name == name:
                return r
        return None

    def with_rows(self, *rows: Row) -> "Report":
        return replace(self, rows=self.rows + rows)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"phase": self.phase,
                               "may_continue": self.may_continue,
                               "rows": [r.to_json() for r in self.rows]}
        if self.failure is not None:
            out["failure"] = self.failure.to_json()
        return out

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True)


def _scrub(value: Any) -> Any:
    """Replace every :class:`Secret` anywhere inside a structure with the mask.

    Applied on the way out to JSON rather than on the way in, so that code
    holding a report still has the real object and only the serialised form is
    masked. Recurses through dicts, lists and tuples because a secret nested
    two levels down is still a secret.
    """
    if isinstance(value, Secret):
        return MASK
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value
