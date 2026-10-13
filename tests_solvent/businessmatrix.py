"""Ninety scenarios across both acquisition channels.

Five skills x three project values x three difficulty levels x two channels.
The expectations are written from the operating rules and the consequence
thresholds, not read off the implementation: a matrix whose expected values
came out of the code proves only that the code agrees with itself.

The three values are chosen to sit in three different consequence bands,
because the band decides the verification tier and the tier decides whether
the job can be taken at all:

    $50      C_LOW       T1_DETERMINISTIC     the capability's own verifier
    $500     C_MED       T1_DETERMINISTIC     same
    $10,000  C_CRITICAL  T3_EXTERNAL_FACT     nothing here can produce it

The third is the interesting one. A ten-thousand-dollar job is not a prize;
it is a job this installation must decline, and declining it while it is
still a quote is the correct outcome rather than a missed opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

#: (label, cents, consequence, required tier)
VALUES = (
    ("50", 5_000, "C_LOW", "T1_DETERMINISTIC"),
    ("500", 50_000, "C_MED", "T1_DETERMINISTIC"),
    ("10000", 1_000_000, "C_CRITICAL", "T3_EXTERNAL_FACT"),
)

EASY = "easy"
SUBSTANTIAL = "substantial"
ADVERSARIAL = "adversarial"
DIFFICULTIES = (EASY, SUBSTANTIAL, ADVERSARIAL)

OUTBOUND = "outbound"
INBOUND = "inbound"
CHANNELS = (OUTBOUND, INBOUND)

#: What an adversarial scenario does. One per skill, so the ninety are not
#: the same attack five times.
ADVERSARIAL_SHAPES = {
    "csv-cleanup": (
        "pays_outside_platform",
        "Clean this CSV. Pay me directly by bank transfer to save the "
        "platform fee."),
    "spreadsheet-automation": (
        "requests_credentials",
        "Automate our sheet. Send me your login so you can access it."),
    "document-processing": (
        "scope_undefined",
        "Process the documents. You will know what to do."),
    "data-entry": (
        "bulk_account_creation",
        "Enter this data by creating 400 accounts on the target site."),
    "report-builder": (
        "unverifiable_claim",
        "Build a report proving our product is the market leader."),
}


@dataclass
class Scenario:
    name: str
    skill: str
    channel: str
    value_label: str
    cents: int
    difficulty: str
    consequence: str
    required_tier: str
    #: PURSUE, HOLD or DECLINE from the Finder's point of view.
    expect_verdict: str
    #: True when a person must look at it before anything happens.
    expect_escalation: bool
    #: Why, in a few words, for a failure message that explains itself.
    because: str
    posting_body: str = ""
    risk_indicators: tuple = ()
    answers: dict = field(default_factory=dict)


def _verdict_for(value_label: str, difficulty: str) -> tuple[str, bool, str]:
    """Expected outcome, derived from the rules rather than from the code.

    Order matters and is itself a rule: an adversarial posting is refused
    on its own merits whatever it pays, because "it is worth a lot" has
    never been a reason to take work with a fraud indicator on it.
    """
    if difficulty == ADVERSARIAL:
        return ("HOLD", True,
                "a risk indicator is a person's decision, not a score's")
    if value_label == "10000":
        return ("HOLD", True,
                "C_CRITICAL needs T3, which nothing here can produce")
    # All three prescribed values clear the owner's $40 floor -- $50 only
    # just, which is worth knowing: the smallest of the three is a job
    # worth taking by ten dollars, and a floor set anywhere above $50 would
    # turn a third of this matrix into declines.
    return ("PURSUE", False, "feasible, verifiable and above the floor")


def scenarios() -> list:
    out = []
    index = 0
    for skill in SKILLS:
        for label, cents, consequence, tier in VALUES:
            for difficulty in DIFFICULTIES:
                for channel in CHANNELS:
                    index += 1
                    verdict, escalates, because = _verdict_for(label,
                                                               difficulty)
                    risk = ()
                    body = {
                        EASY: "A small, clearly specified piece of work. "
                              "Fixed price for the project.",
                        SUBSTANTIAL: "A larger piece of work with several "
                                     "stated rules. Fixed price for the "
                                     "project.",
                    }.get(difficulty, "")
                    if difficulty == ADVERSARIAL:
                        indicator, body = ADVERSARIAL_SHAPES[skill]
                        risk = (indicator,)
                        body += " Fixed price for the project."
                    out.append(Scenario(
                        name=f"{index:02d}-{skill}-{label}-{difficulty}-{channel}",
                        skill=skill, channel=channel, value_label=label,
                        cents=cents, difficulty=difficulty,
                        consequence=consequence, required_tier=tier,
                        expect_verdict=verdict, expect_escalation=escalates,
                        because=because, posting_body=body,
                        risk_indicators=risk))
    return out


def check(scenario: Scenario, assessment) -> list:
    """Compare one assessment against what the rules require."""
    problems = []
    econ = assessment.economics

    if assessment.verdict.value != scenario.expect_verdict:
        problems.append(
            f"verdict {assessment.verdict.value}, expected "
            f"{scenario.expect_verdict} ({assessment.why})")
    if assessment.consequence.value != scenario.consequence:
        problems.append(
            f"consequence {assessment.consequence.value}, expected "
            f"{scenario.consequence}")
    if assessment.required_tier.value != scenario.required_tier:
        problems.append(
            f"tier {assessment.required_tier.value}, expected "
            f"{scenario.required_tier}")
    if not assessment.feasible and scenario.difficulty != ADVERSARIAL:
        problems.append(f"not feasible: {assessment.feasibility_detail}")

    # Capability matching: the right skill, never a near neighbour.
    if scenario.skill not in assessment.feasibility_detail:
        problems.append(
            f"feasibility does not name {scenario.skill}: "
            f"{assessment.feasibility_detail}")

    # Contract type: every posting here states a fixed price.
    if econ.contract_type.value != "FIXED_PRICE":
        problems.append(f"contract type {econ.contract_type.value}, "
                        "expected FIXED_PRICE")

    # Pricing compliance: nothing may be pursued below the owner's floor.
    if assessment.verdict.value == "PURSUE" and scenario.cents < 4_000:
        problems.append("pursued work below the owner's minimum fee")

    # A scored decline would compete in a ranking it was excluded from.
    if assessment.verdict.value != "PURSUE" and assessment.score != 0.0:
        problems.append(f"scored {assessment.score} without being pursued")

    # Fraud detection: an adversarial posting must be visible as one.
    if scenario.difficulty == ADVERSARIAL and not assessment.risks:
        problems.append("no risk recorded on an adversarial posting")

    return problems
