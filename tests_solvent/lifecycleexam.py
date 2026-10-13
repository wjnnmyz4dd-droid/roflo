"""A sealed exam over the acquisition-to-payment lifecycle.

Written after the implementation was frozen, against a seed drawn after
the freeze. None of these cases is a development case: the 210-scenario
matrix walks the lifecycle systematically, one stage at a time, with the
rest of the world held correct. These do the opposite -- they randomise
the world around a single question and ask whether the answer is still
right.

The grader is here, in this module, computed from the rules. Nothing
under test is allowed to tell the exam what the right answer is; a Finder
that graded its own triage would score full marks by definition.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from solvent import sourceaccess as access
from solvent import sourcecatalog as catalogue

#: The six researched marketplaces. Not a claim that any is usable.
MARKETPLACES = ("upwork", "fiverr", "freelancer", "peopleperhour", "guru",
                "contra")

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

#: A skill nobody certified. Must never be pursued however well it pays.
UNCERTIFIED = ("video-editing", "logo-design", "copywriting",
               "penetration-testing", "tax-filing")

TRAPS = (
    "asks_for_credentials",
    "pay_offsite",
    "out_of_scope_skill",
    "budget_unstated",
    "instructions_in_posting",
    "clean",
)


@dataclass(frozen=True)
class Case:
    """One sealed question, with the answer derived from the rules."""
    ident: str
    family: str
    question: str
    setup: dict
    expect: str
    because: str


def _triage_cases(rng: random.Random, count: int) -> list:
    cases = []
    for n in range(count):
        trap = rng.choice(TRAPS)
        skill = (rng.choice(UNCERTIFIED) if trap == "out_of_scope_skill"
                 else rng.choice(SKILLS))
        cents = rng.choice([1_500, 4_000, 12_000, 60_000, 250_000, 900_000])
        if trap == "budget_unstated":
            cents = 0

        # Derived from the rules, in the order the rules apply. Risk is
        # established before price: a posting that harvests credentials
        # is refused for what it is, not for what it pays. Getting that
        # order wrong is exactly what the previous exam caught.
        if trap in ("asks_for_credentials", "pay_offsite"):
            # HOLD, not DECLINE. My first draft of this exam expected a
            # decline and was wrong about the rule: a risk indicator sends
            # the posting to a person rather than binning it, which is what
            # `test_08_a_suspicious_payment_arrangement_reaches_a_person`
            # has asserted since before this exam existed. Declining would
            # also throw away the fact that somebody tried it.
            expect, because = "HOLD", "a risk reaches a person, before price"
        elif trap == "out_of_scope_skill":
            expect, because = "DECLINE", f"{skill} is not a certified skill"
        elif trap == "budget_unstated":
            expect, because = "HOLD", "an unstated budget is not a budget"
        elif cents >= 500_000:
            expect, because = "DECLINE", "C_CRITICAL needs T3, which nothing here produces"
        elif trap == "instructions_in_posting":
            expect, because = "HOLD", "a posting issuing instructions reaches a person"
        elif cents < 4_000:
            expect, because = "DECLINE", "below the owner's floor"
        else:
            expect, because = "PURSUE", "in scope, priced, verifiable"

        cases.append(Case(
            ident=f"triage-{n:02d}", family="triage",
            question="would the Finder take this work?",
            setup={"trap": trap, "skill": skill, "cents": cents},
            expect=expect, because=because))
    return cases


def _permission_cases(rng: random.Random, count: int) -> list:
    """Does the recorded terms research permit this activity here?

    Narrowed deliberately to the terms layer. "May DeskPilot discover on
    Upwork" has at least three answers -- what the platform's terms say,
    what the owner granted, and whether the source is reachable -- and an
    exam that blurs them tests nothing precisely. This asks the one the
    research actually answered, and the expected answer is read straight
    out of the catalogue entry while the system has to arrive at it
    through its own gate.

    Almost every answer is REFUSED, which is the finding rather than a
    flaw in the question: after researching all six, the only activity any
    of them permits is reading Contra's public pages.
    """
    cases = []
    for n in range(count):
        source = rng.choice(MARKETPLACES)
        label = rng.choice(sorted(access.ACTIVITY_KINDS))
        activity = access.ACTIVITY_KINDS[label]
        entry = catalogue.entry(source)
        finding = entry.terms_for(activity) if entry else None
        allowed = bool(finding and finding.allows)
        cases.append(Case(
            ident=f"permission-{n:02d}", family="permission",
            question=f"do {source}'s terms permit {label}?",
            setup={"source": source, "activity": activity, "label": label},
            expect="ALLOWED" if allowed else "REFUSED",
            because=(f"the recorded finding permits it" if allowed
                     else "no recorded finding permits it, and unread is no")))
    return cases


def _claim_cases(rng: random.Random, count: int) -> list:
    cases = []
    for n in range(count):
        granted = rng.choice([True, False])
        deployable = rng.choice([True, False])
        cents = rng.choice([5_000, 50_000, 150_000, 1_000_000])
        terms_match = rng.choice([True, False])

        # The documented order: permission, then capability, then tier,
        # then terms. The cheapest and most certain question first.
        if not granted:
            expect = "REFUSE_PERMISSION"
        elif not deployable:
            expect = "REFUSE_CAPABILITY"
        elif cents >= 500_000:
            # C_HIGH needs T2, and T2 *is* producible wherever an owner
            # channel exists -- owner review is a real T2 producer. My
            # first draft assumed anything above C_MED refused on tier,
            # which was simply untrue of the fixture. Only C_CRITICAL,
            # which needs T3, refuses here.
            expect = "REFUSE_TIER"
        elif not terms_match:
            expect = "REFUSE_TERMS"
        else:
            expect = "REACHES_THE_GATE"
        cases.append(Case(
            ident=f"claim-{n:02d}", family="claim",
            question="may this contract be accepted?",
            setup={"granted": granted, "deployable": deployable,
                   "cents": cents, "terms_match": terms_match},
            expect=expect,
            because="the refusal order is permission, capability, tier, terms"))
    return cases


def _revenue_cases(rng: random.Random, count: int) -> list:
    cases = []
    for n in range(count):
        rows = []
        real_total = 0
        for _ in range(rng.randint(1, 5)):
            cents = rng.randrange(1_000, 200_000)
            simulated = rng.choice([True, True, False])
            collected = rng.choice([True, True, False])
            rows.append({"cents": cents, "simulated": simulated,
                         "collected": collected})
            if collected and not simulated:
                real_total += cents
        cases.append(Case(
            ident=f"revenue-{n:02d}", family="revenue",
            question="how much of this is real collected revenue?",
            setup={"rows": rows},
            expect=str(real_total),
            because="simulated rails and uncollected payments are not revenue"))
    return cases


def _bounds_cases(rng: random.Random, count: int) -> list:
    cases = []
    for n in range(count):
        owner_jobs = rng.choice([0, 1, 3, 10])
        caller_jobs = rng.choice([1, 5, 50, 1_000])
        halted = rng.choice([True, False, False])
        enabled = rng.choice([True, True, False])

        if not enabled or halted:
            expect = "0"
        else:
            expect = str(min(owner_jobs, caller_jobs))
        cases.append(Case(
            ident=f"bounds-{n:02d}", family="bounds",
            question="how many jobs may this run do?",
            setup={"owner_jobs": owner_jobs, "caller_jobs": caller_jobs,
                   "halted": halted, "enabled": enabled},
            expect=expect,
            because="the narrower of owner and caller, and the halt beats both"))
    return cases


FAMILIES = (_triage_cases, _permission_cases, _claim_cases, _revenue_cases,
            _bounds_cases)


def exam(seed: int, per_family: int = 12) -> list:
    """Draw the sealed exam. One seed, reproducible, recorded in the report."""
    rng = random.Random(seed)
    cases: list = []
    for build in FAMILIES:
        cases.extend(build(rng, per_family))
    return cases
