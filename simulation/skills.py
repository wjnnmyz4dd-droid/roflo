"""Which skills the shop is selling this quarter, and when that changes.

The assignment asks for six configurations -- two, three, four and
five active skills, five contracted to two, and two expanded to five.
None of that needed new machinery. A skill is sellable when the owner
has cleared it with ``promote_capability``; it stops being sellable
when the owner records a LIMITED decision over the top, which is the
existing way of saying *build and test it, do not sell it*. Both are
owner acts through ``CapabilityRegistry``, and the storefront already
declines an order for a skill that ``may_deploy`` refuses, naming the
reason.

So this module holds a list of names and performs owner acts. It
decides nothing: it does not price, admit, refuse or schedule, and
the only thing it reads back is what the registry says afterwards.

**What ``active`` means is checked, not asserted.** :func:`applied_to`
returns what ``may_deploy`` answers for every offer in the catalogue
after the change, so a configuration that failed to apply shows up as
a configuration that failed to apply rather than as a quiet
difference in the results table.

**The per-skill queue policy, as production actually implements it.**
The owner's intent is one active job and three queued per skill.
``Capacity`` enforces a per-pipeline limit on *committed* jobs --
``capacity.max_concurrent_jobs``, counted by
``jobs_in_flight(capability)`` -- so four per skill is exactly that
intent, and :data:`PER_SKILL_COMMITTED` sets it. What production does
**not** have is a per-skill limit on jobs *executing*:
``Budget.workers`` is documented as "workers allowed for a kind of
work, across the whole shop". A run therefore holds at most four
committed per skill, and the active-job bound is global rather than
per skill. Stated here because the difference belongs in the report
rather than in a footnote nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from solvent.harness import OWNER, promote_capability
from solvent.storefront import OFFERS

#: Every skill the build sells, in the catalogue's own order.
EVERY_SKILL = tuple(offer.capability for offer in OFFERS)

#: The two the owner starts with, named in the assignment: report
#: building and document processing.
FIRST_TWO = ("report-builder", "document-processing")

#: Committed jobs one skill's pipeline may hold: one being worked and
#: three waiting. See the module docstring for what production
#: enforces and what it does not.
PER_SKILL_COMMITTED = 4

#: Seconds of work the owner is willing to owe per active skill. Four
#: hours, which is the figure the shop policy already used, scaled by
#: how many skills are switched on rather than fixed at five.
OWED_SECONDS_PER_SKILL = 14_400.0


def _ordered(names) -> tuple:
    """The named skills, in catalogue order, refusing an unknown one."""
    wanted = list(names)
    unknown = [n for n in wanted if n not in EVERY_SKILL]
    if unknown:
        raise ValueError(
            f"{', '.join(unknown)} is not a skill this build sells; it sells "
            f"{', '.join(EVERY_SKILL)}")
    if not wanted:
        raise ValueError("a shop selling nothing is not a configuration")
    return tuple(n for n in EVERY_SKILL if n in set(wanted))


def _grow(count: int) -> tuple:
    """``count`` skills, starting from the owner's first two."""
    rest = [n for n in EVERY_SKILL if n not in FIRST_TWO]
    return _ordered(list(FIRST_TWO) + rest[:max(0, count - len(FIRST_TWO))])


@dataclass(frozen=True, slots=True)
class Configuration:
    """One run's skill set, and any change to it partway through."""

    name: str
    description: str
    #: Skills cleared for sale on day zero.
    start: tuple
    #: ``(day, skills)``: what the shop sells from that day on. Empty
    #: for a configuration that does not change.
    changes_to: tuple = ()

    def skills_on(self, day: int) -> tuple:
        """What should be sellable on this day."""
        current = self.start
        for when, skills in self.changes_to:
            if day >= when:
                current = skills
        return current

    @property
    def changes(self) -> tuple:
        return tuple(when for when, _ in self.changes_to)


#: Configuration A through F, as the assignment names them. The
#: change in E and F lands at day 45, halfway through a ninety-day
#: run, so each half is long enough to read.
MIDPOINT = 45

CONFIGURATIONS = {
    "A": Configuration(
        "A", "two skills: report building and document processing",
        _ordered(FIRST_TWO)),
    "B": Configuration("B", "three skills", _grow(3)),
    "C": Configuration("C", "four skills", _grow(4)),
    "D": Configuration("D", "five skills", _ordered(EVERY_SKILL)),
    "E": Configuration(
        "E", f"five skills, contracted to two on day {MIDPOINT}",
        _ordered(EVERY_SKILL),
        changes_to=((MIDPOINT, _ordered(FIRST_TWO)),)),
    "F": Configuration(
        "F", f"two skills, expanded to five on day {MIDPOINT}",
        _ordered(FIRST_TWO),
        changes_to=((MIDPOINT, _ordered(EVERY_SKILL)),)),
}


def policy_for(skills) -> dict:
    """The capacity policy that matches this many active skills.

    Scaled rather than fixed: a two-skill shop that kept a five-skill
    allowance would be measured against a ceiling it could never
    reach, and the run would say capacity was never the constraint
    when it had simply been switched off.
    """
    skills = _ordered(skills)
    return {"capacity": {
        "skills": list(skills),
        "max_concurrent_jobs": PER_SKILL_COMMITTED,
        "max_owed_seconds": OWED_SECONDS_PER_SKILL * len(skills),
    }}


def sell(solvent, skills, *, owner_identity: str = OWNER,
         why: str = "") -> dict:
    """Make exactly these skills sellable. An owner act, times five.

    Promotes what should be on and is not; withdraws what should be
    off and is not. Returns what the registry says afterwards, read
    back rather than assumed.
    """
    wanted = set(_ordered(skills))
    reason = why or "the owner chose which skills this shop sells"
    for name in EVERY_SKILL:
        sellable, _ = solvent.capability.may_deploy(name)
        if name in wanted and not sellable:
            promote_capability(solvent, name, owner_identity=owner_identity,
                               why=reason)
        elif name not in wanted and sellable:
            # The existing way to stop selling something: the owner
            # says it is for building and testing. The APPROVED row
            # stays -- the table is append-only, and "when did this
            # stop being sellable" has to remain answerable.
            solvent.capability.decide(
                name=name, decision=solvent.capability.LIMITED,
                owner_identity=owner_identity, why=reason,
                scope="development and testing")
    solvent.policy.amend(policy_for(sorted(wanted)), owner_identity, reason)
    return applied_to(solvent)


def applied_to(solvent) -> dict:
    """What each skill's deployability actually is, from the registry."""
    return {name: bool(solvent.capability.may_deploy(name)[0])
            for name in EVERY_SKILL}


def active_in(solvent) -> tuple:
    """The skills a client could actually buy right now."""
    return tuple(name for name, sellable in applied_to(solvent).items()
                 if sellable)
