"""Ninety postings the Finder has to get right.

The expectations here are written from the ten operating rules, not from
reading the Finder. That distinction is the whole value of the file: a matrix
whose expected values were lifted out of the implementation proves only that
the code agrees with itself, and it will keep agreeing after the code breaks.

Each scenario fixes one thing and varies another, so a failure names a cause:

* **contract** -- declared fixed, declared hourly, inferred from prose,
  genuinely ambiguous. An ambiguous posting is never guessed (RULE 5).
* **value** -- below the owner's minimum, C_LOW through C_CRITICAL. Value
  decides the verification tier, and a tier nothing can produce is a decline
  before the money moves (RULE 8).
* **feasibility** -- whether a certified capability can actually do it.
* **history** -- hourly revenue is the hours actually worked, so with no
  observed execution it is UNKNOWN, never a guess (RULES 2, 5).
* **minimum** -- configured, permitted and agreed are three conditions, and
  a minimum needs all three (RULES 3, 5).
* **economics** -- fees and upfront costs that make real money disappear
  (RULE 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from solvent.finder import ContractType, Verdict

CAPABILITIES = ("csv-cleanup", "spreadsheet-automation", "document-processing",
                "data-entry", "report-builder")

#: Values chosen to sit unambiguously inside each consequence band.
BELOW_MIN = 1_500        # $15
C_LOW = 15_000           # $150
C_MED = 40_000           # $400
C_HIGH = 150_000         # $1,500
C_CRITICAL = 600_000     # $6,000


@dataclass
class Scenario:
    name: str
    #: Posting fields, merged over the default posting.
    posting: dict
    expect_verdict: Verdict
    expect_contract: ContractType
    #: Substring that must appear in the Finder's stated reason.
    expect_because: str = ""
    #: True when revenue must be reported as not knowable.
    expect_revenue_unknown: bool = False
    expect_minimum_applied: bool | None = None
    expect_conversion_note: bool | None = None
    #: Capability history to seed, as {capability: [wall_seconds, ...]}.
    history: dict = field(default_factory=dict)
    #: Capabilities to leave uncertified, making the work infeasible.
    withhold: tuple = ()
    #: Source permissions to grant beyond the defaults.
    grant: tuple = ()
    notes: str = ""


def _posting(ref, **over):
    base = {
        "ref": ref, "title": "Clean up a spreadsheet",
        "quoted_cents": C_MED, "needs": ["csv-cleanup"],
        "project_state": "CT", "client_ref": "client:acme",
        "body": "fixed price for the whole project", "deliverables": [],
        "payment_structure": "FIXED",
    }
    base.update(over)
    return base


FAST = [18.0, 22.0, 20.0]          # a realistic automated run: ~20 seconds


def scenarios() -> list[Scenario]:
    out: list[Scenario] = []

    # -- 1-10: a fixed price is the stated price, whatever the work takes ---
    for i, cents in enumerate(
            [C_LOW, C_MED, C_HIGH, 60_000, 90_000, 120_000, 200_000,
             300_000, 45_000, 75_000], start=1):
        out.append(Scenario(
            f"{i:02d}-fixed-{cents}",
            _posting(f"fx{i}", quoted_cents=cents),
            Verdict.PURSUE, ContractType.FIXED_PRICE,
            expect_because="expected",
            notes="a fixed price earns in full however fast the work is"))

    # -- 11-15: the same posting for each capability ------------------------
    for i, cap in enumerate(CAPABILITIES, start=11):
        out.append(Scenario(
            f"{i}-capability-{cap}",
            _posting(f"cap{i}", needs=[cap]),
            Verdict.PURSUE, ContractType.FIXED_PRICE,
            notes="one Finder serves all five skills (RULE 1)"))

    # -- 16-20: the capability is not certified -----------------------------
    for i, cap in enumerate(CAPABILITIES, start=16):
        out.append(Scenario(
            f"{i}-not-certified-{cap}",
            _posting(f"nc{i}", needs=[cap]),
            Verdict.DECLINE, ContractType.FIXED_PRICE,
            expect_because="nothing here can do it",
            withhold=(cap,),
            notes="feasibility is checked before profit"))

    # -- 21-26: hourly with no observed history is UNKNOWN, never guessed ---
    for i, cents in enumerate([C_LOW, C_MED, C_HIGH, 50_000, 80_000, 110_000],
                              start=21):
        out.append(Scenario(
            f"{i}-hourly-no-history",
            _posting(f"hn{i}", quoted_cents=cents, payment_structure="HOURLY",
                     body="paid per hour"),
            Verdict.HOLD, ContractType.HOURLY,
            expect_because="not known",
            expect_revenue_unknown=True,
            notes="RULE 5: hours nobody worked are not an estimate"))

    # -- 27-32: hourly with history bills the hours actually worked ---------
    for i, cap in enumerate(CAPABILITIES, start=27):
        out.append(Scenario(
            f"{i}-hourly-measured-{cap}",
            _posting(f"hm{i}", needs=[cap], payment_structure="HOURLY",
                     body="hourly engagement"),
            Verdict.DECLINE, ContractType.HOURLY,
            expect_because="below the",
            history={cap: FAST},
            expect_minimum_applied=False,
            notes="20 seconds at $40/hour is 22 cents: honest, and far "
                  "below any sane minimum fee. This is the arithmetic that "
                  "makes fixed price the right default."))
    out.append(Scenario(
        "32-hourly-measured-long-run",
        _posting("hm32", payment_structure="HOURLY", body="hourly engagement"),
        Verdict.DECLINE, ContractType.HOURLY,
        expect_because="below the",
        history={"csv-cleanup": [600.0, 700.0, 650.0]},
        notes="even ten minutes of real work bills $7"))

    # -- 33-38: the one-hour minimum needs all three conditions -------------
    out.append(Scenario(
        "33-minimum-not-permitted-by-source",
        _posting("mn33", payment_structure="HOURLY", body="hourly engagement"),
        Verdict.DECLINE, ContractType.HOURLY,
        history={"csv-cleanup": FAST}, expect_minimum_applied=False,
        notes="configured by the owner, but the source has not granted it"))
    out.append(Scenario(
        "34-minimum-permitted-and-structured",
        _posting("mn34", payment_structure="HOURLY", body="hourly engagement",
                 structured_terms=True),
        Verdict.PURSUE, ContractType.HOURLY,
        history={"csv-cleanup": FAST}, grant=("HOURLY_MINIMUM",),
        expect_minimum_applied=True,
        notes="all three conditions met: $40 for the hour"))
    out.append(Scenario(
        "35-minimum-permitted-but-terms-unconfirmed",
        _posting("mn35", payment_structure="HOURLY", body="hourly engagement",
                 structured_terms=False),
        Verdict.DECLINE, ContractType.HOURLY,
        history={"csv-cleanup": FAST}, grant=("HOURLY_MINIMUM",),
        expect_minimum_applied=False,
        notes="the platform allows it; this client did not agree to it"))
    out.append(Scenario(
        "36-posting-excludes-a-minimum",
        _posting("mn36", payment_structure="HOURLY", structured_terms=True,
                 body="hourly engagement, no minimum charge"),
        Verdict.DECLINE, ContractType.HOURLY,
        history={"csv-cleanup": FAST}, grant=("HOURLY_MINIMUM",),
        expect_minimum_applied=False,
        notes="the posting says no, which outranks the owner's preference"))
    out.append(Scenario(
        "37-minimum-on-owner-entered-work",
        _posting("mn37", payment_structure="HOURLY", body="hourly engagement",
                 owner_entered=True),
        Verdict.PURSUE, ContractType.HOURLY,
        history={"csv-cleanup": FAST}, grant=("HOURLY_MINIMUM",),
        expect_minimum_applied=True,
        notes="the owner entering the job is the strongest confirmation"))
    out.append(Scenario(
        "38-long-job-does-not-need-the-minimum",
        _posting("mn38", payment_structure="HOURLY", structured_terms=True,
                 body="hourly engagement"),
        Verdict.PURSUE, ContractType.HOURLY,
        history={"csv-cleanup": [7200.0]}, grant=("HOURLY_MINIMUM",),
        expect_minimum_applied=False,
        notes="two real hours bills two hours; the minimum does not inflate"))

    # -- 39-46: contract type read off the posting, or admitted unknown -----
    for i, (body, structure, expect) in enumerate([
            ("flat fee for the project", "", ContractType.FIXED_PRICE),
            ("one-off piece of work", "", ContractType.FIXED_PRICE),
            ("paid at an hourly rate", "", ContractType.HOURLY),
            ("time and materials", "", ContractType.HOURLY),
            ("$40/hr against a fixed project budget", "", ContractType.UNKNOWN),
            ("we will discuss terms", "", ContractType.UNKNOWN),
            ("per project", "HOURLY", ContractType.HOURLY),
            ("hourly", "FIXED", ContractType.FIXED_PRICE)], start=39):
        verdict = {ContractType.FIXED_PRICE: Verdict.PURSUE,
                   ContractType.HOURLY: Verdict.HOLD,
                   ContractType.UNKNOWN: Verdict.HOLD}[expect]
        out.append(Scenario(
            f"{i}-contract-{expect.value.lower()}-{i}",
            _posting(f"ct{i}", body=body, payment_structure=structure),
            verdict, expect,
            expect_revenue_unknown=expect is not ContractType.FIXED_PRICE,
            notes="structured platform fields outrank prose"))

    # -- 47-52: the owner's minimum project fee -----------------------------
    for i, cents in enumerate([BELOW_MIN, 2_000, 4_999, 5_000, 8_000, 12_000],
                              start=47):
        below = cents < 4_000
        out.append(Scenario(
            f"{i}-minimum-fee-{cents}",
            _posting(f"mf{i}", quoted_cents=cents),
            Verdict.DECLINE if below else Verdict.PURSUE,
            ContractType.FIXED_PRICE,
            expect_because="below the" if below else "expected",
            notes="$50 minimum project fee, set by the owner"))

    # -- 53-58: fees and upfront costs that eat the money -------------------
    for i, (fee, upfront, expect) in enumerate([
            (0, 0, Verdict.PURSUE),
            (10_000, 0, Verdict.PURSUE),
            # $10 on $400 is a 2.5% margin. "Thin, but profit" was a
            # PURSUE until the owner's minimum margin stopped being a
            # number nothing read: clearing cost by a hair is not a
            # business, and a $400 job demanding $390 up front is the
            # shape of an advance-fee scam besides.
            (0, 39_000, Verdict.DECLINE),
            (0, 60_000, Verdict.DECLINE),
            (35_000, 0, Verdict.PURSUE),
            (0, 40_000, Verdict.DECLINE)], start=53):
        out.append(Scenario(
            f"{i}-economics-fee{fee}-upfront{upfront}",
            _posting(f"ec{i}", platform_fee_cents=fee,
                     upfront_cost_cents=upfront),
            expect, ContractType.FIXED_PRICE,
            expect_because=("margin" if (fee, upfront) == (0, 39_000)
                            else "cost" if expect is Verdict.DECLINE
                            else "expected"),
            notes="RULE 7: profitability on real costs, not on revenue"))

    # -- 59-64: verification reachability before the money ------------------
    for i, cents in enumerate([C_HIGH, 200_000, 400_000, 499_999], start=59):
        out.append(Scenario(
            f"{i}-c-high-needs-a-second-opinion-{cents}",
            _posting(f"vr{i}", quoted_cents=cents),
            Verdict.PURSUE, ContractType.FIXED_PRICE,
            notes="C_HIGH is reachable here: a differential reader exists"))
    out.append(Scenario(
        "63-c-critical-external-fact",
        _posting("vr63", quoted_cents=C_CRITICAL),
        Verdict.HOLD, ContractType.FIXED_PRICE,
        expect_because="external authority",
        notes="T3 depends on the subject matter; it is not claimed"))
    out.append(Scenario(
        "64-c-critical-very-large",
        _posting("vr64", quoted_cents=1_000_000),
        Verdict.HOLD, ContractType.FIXED_PRICE,
        expect_because="external authority",
        notes="a bigger number does not make an external authority appear"))

    # -- 65-70: risk indicators hold rather than decline --------------------
    for i, risks in enumerate([
            ["pays_outside_platform"],
            ["unverified_client"],
            ["pays_outside_platform", "urgent_unpaid_trial"],
            ["scope_undefined"],
            ["requests_credentials"],
            ["bulk_account_creation"]], start=65):
        out.append(Scenario(
            f"{i}-risk-{risks[0]}",
            _posting(f"rk{i}", risk_indicators=risks),
            Verdict.HOLD, ContractType.FIXED_PRICE,
            expect_because="risk indicator",
            notes="a person decides; the Finder does not clear its own risks"))

    # -- 71-76: a posting that does not say what it needs -------------------
    for i in range(71, 77):
        out.append(Scenario(
            f"{i}-no-stated-capability",
            _posting(f"ns{i}", needs=[], quoted_cents=C_LOW + i * 100),
            Verdict.DECLINE, ContractType.FIXED_PRICE,
            expect_because="does not say what capability",
            notes="feasibility cannot be established from silence"))

    # -- 77-82: platform fee changes what is actually earned ----------------
    for i, (quoted, fee) in enumerate([
            (C_MED, 4_000), (C_MED, 20_000), (C_HIGH, 15_000),
            (C_HIGH, 50_000), (C_LOW, 1_500), (C_LOW, 10_500)], start=77):
        out.append(Scenario(
            f"{i}-net-of-platform-fee-{quoted}-{fee}",
            _posting(f"pf{i}", quoted_cents=quoted, platform_fee_cents=fee),
            Verdict.PURSUE, ContractType.FIXED_PRICE,
            notes="ranking uses net budget, not the headline number"))

    # -- 83-86: hourly work a human would take hours over -------------------
    for i, cap in enumerate(CAPABILITIES[:4], start=83):
        out.append(Scenario(
            f"{i}-conversion-advice-{cap}",
            _posting(f"cv{i}", needs=[cap], payment_structure="HOURLY",
                     structured_terms=True, body="hourly engagement"),
            Verdict.PURSUE, ContractType.HOURLY,
            history={cap: FAST}, grant=("HOURLY_MINIMUM",),
            expect_conversion_note=True,
            notes="RULE 4 as advice: say the fixed price is worth more, "
                  "rather than billing hours nobody worked"))

    # -- 87-90: confidence discounts the score, it does not invent -----------
    for i, confidence in enumerate([1.0, 0.75, 0.5, 0.25], start=87):
        out.append(Scenario(
            f"{i}-confidence-{confidence}",
            _posting(f"cf{i}", confidence=confidence),
            Verdict.PURSUE, ContractType.FIXED_PRICE,
            notes="a less certain posting ranks lower, not differently"))

    return out


# ---------------------------------------------------------------- the runner

def build_opportunity(scenario: Scenario, *, source: str):
    """One posting as discovery would have produced it.

    Built directly rather than polled, because ``structured_terms`` and
    ``owner_entered`` belong to the *source* in the fixture plumbing, and the
    matrix needs to vary them per posting.
    """
    from solvent.discovery import Opportunity
    from solvent.content import quarantine
    from solvent.types import PrivacyClass

    p = scenario.posting
    return Opportunity(
        id=f"opp_{scenario.name}", source=source,
        external_ref=p.get("ref", ""), title=p["title"],
        quoted_cents=int(p["quoted_cents"]),
        needs=tuple(p.get("needs", ())), client_ref=p.get("client_ref", ""),
        posting=quarantine(p.get("body", ""), source=source,
                           privacy=PrivacyClass.PUBLIC),
        structured_terms=bool(p.get("structured_terms", False)),
        owner_entered=bool(p.get("owner_entered", False)),
        payment_structure=p.get("payment_structure", ""),
        platform_fee_cents=int(p.get("platform_fee_cents", 0)),
        upfront_cost_cents=int(p.get("upfront_cost_cents", 0)),
        risk_indicators=tuple(p.get("risk_indicators", ())),
        confidence=float(p.get("confidence", 1.0)))


@dataclass
class Outcome:
    scenario: Scenario
    passed: bool
    detail: str
    assessment: object = None


def check(scenario: Scenario, assessment) -> Outcome:
    """Compare one assessment against what the operating rules require."""
    problems = []
    econ = assessment.economics

    if assessment.verdict is not scenario.expect_verdict:
        problems.append(
            f"verdict {assessment.verdict.value}, expected "
            f"{scenario.expect_verdict.value} ({assessment.why})")
    if econ.contract_type is not scenario.expect_contract:
        problems.append(
            f"contract {econ.contract_type.value}, expected "
            f"{scenario.expect_contract.value}")
    if scenario.expect_because and scenario.expect_because not in assessment.why:
        problems.append(
            f"reason {assessment.why!r} does not mention "
            f"{scenario.expect_because!r}")
    if scenario.expect_revenue_unknown and econ.revenue_cents is not None:
        problems.append(
            f"revenue reported as {econ.revenue_cents} when it is not knowable")
    if not scenario.expect_revenue_unknown and econ.revenue_cents is None:
        problems.append("revenue reported unknown when the posting states it")
    if (scenario.expect_minimum_applied is not None
            and econ.minimum_applied is not scenario.expect_minimum_applied):
        problems.append(
            f"minimum_applied={econ.minimum_applied}, expected "
            f"{scenario.expect_minimum_applied}")
    if scenario.expect_conversion_note is not None:
        has = bool(assessment.conversion_note)
        if has is not scenario.expect_conversion_note:
            problems.append(
                f"conversion_note present={has}, expected "
                f"{scenario.expect_conversion_note}")

    # Two invariants every scenario must satisfy, whatever it is testing.
    if econ.human_hours is not None and econ.billable_hours is not None:
        if econ.billable_hours > econ.human_hours and not econ.minimum_applied:
            problems.append(
                "billable hours exceed the human-equivalent estimate without "
                "an agreed minimum, which is a fabricated hour")
    if assessment.verdict is not Verdict.PURSUE and assessment.score != 0.0:
        problems.append(
            f"scored {assessment.score} without being worth pursuing")

    return Outcome(scenario, not problems, "; ".join(problems), assessment)
