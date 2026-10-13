"""210 scenarios across the whole acquisition-to-payment lifecycle.

The earlier ninety covered assessment: would the Finder take this work?
These cover what happens afterwards, which is where a business actually
fails -- a job taken correctly and then claimed without permission,
executed without a reviewer, delivered without a gate, or invoiced without
an identity.

Seven lifecycle stages x five skills x three values x two channels = 210.
Each scenario names the stage it is probing and what must be true there.

Expectations are derived from the rules, not from the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

#: Three bands, three different required tiers. $10,000 is the one that
#: must be refused: C_CRITICAL needs T3 and nothing here produces it.
VALUES = (("50", 5_000, "C_LOW"),
          ("500", 50_000, "C_MED"),
          ("10000", 1_000_000, "C_CRITICAL"))

CHANNELS = ("outbound", "inbound")

#: The lifecycle, and the question each stage has to answer correctly.
STAGES = (
    ("qualify", "is this work worth taking at all?"),
    ("claim", "may this contract be accepted, and can it still be done?"),
    ("execute", "does the capability run and produce an artifact?"),
    ("verify", "is the deliverable checked before it leaves?"),
    ("submit", "may the finished work be sent through this channel?"),
    ("invoice", "can a valid invoice be issued?"),
    ("reconcile", "is the money that arrived actually recorded as real?"),
)


@dataclass
class Scenario:
    name: str
    stage: str
    question: str
    skill: str
    value_label: str
    cents: int
    consequence: str
    channel: str
    #: What must be true at this stage. One of:
    #: PROCEED, REFUSE_TIER, REFUSE_PERMISSION, REFUSE_IDENTITY,
    #: REFUSE_SIMULATED.
    expect: str
    because: str
    notes: tuple = field(default_factory=tuple)


def _expected(stage: str, value_label: str) -> tuple:
    """Derived from the rules. The value band decides most of it."""
    critical = value_label == "10000"

    if stage == "qualify":
        if critical:
            return ("REFUSE_TIER",
                    "C_CRITICAL requires T3, which nothing here produces")
        return ("PROCEED", "feasible, verifiable and above the floor")

    if stage == "claim":
        # Permission first, whatever the value. claim() asks the cheapest,
        # most certain question before the expensive ones -- the same
        # ordering submit() documents -- so a source that may not accept at
        # all is refused before anyone works out its verification tier.
        # My first version of this matrix expected the tier refusal for
        # C_CRITICAL and was wrong about the order.
        return ("REFUSE_PERMISSION",
                "ACCEPT_CONTRACT is granted by nobody by default, and no "
                "marketplace's terms permit it")

    if stage == "execute":
        return ("PROCEED", "the capability is certified and deterministic")

    if stage == "verify":
        if critical:
            return ("REFUSE_TIER",
                    "C_CRITICAL needs T3_EXTERNAL_FACT, which nothing here "
                    "produces")
        return ("PROCEED",
                "T1 is produced by the capability's own certified verifier")

    if stage == "submit":
        return ("REFUSE_PERMISSION",
                "no source permits automated submission; three forbid it "
                "and the rest are undetermined")

    if stage == "invoice":
        return ("REFUSE_IDENTITY",
                "no contracting identity is recorded, so no invoice is valid")

    return ("REFUSE_SIMULATED",
            "every payment so far is simulated, and simulated money is "
            "never real revenue")


def scenarios() -> list:
    out = []
    index = 0
    for stage, question in STAGES:
        for skill in SKILLS:
            for label, cents, consequence in VALUES:
                for channel in CHANNELS:
                    index += 1
                    expect, because = _expected(stage, label)
                    out.append(Scenario(
                        name=f"{index:03d}-{stage}-{skill}-{label}-{channel}",
                        stage=stage, question=question, skill=skill,
                        value_label=label, cents=cents,
                        consequence=consequence, channel=channel,
                        expect=expect, because=because))
    return out
