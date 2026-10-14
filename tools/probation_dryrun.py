#!/usr/bin/env python3
"""Run the real bidder over really-observed postings, and count.

Everything here is real except the one thing that cannot be: the
postings are the ones Freelancer's public endpoint actually returned
during the observation window, the adapter is the production adapter,
and the Finder and its probation logic are production code. Nothing
is submitted, nothing is accepted, no money moves.

The assumption, stated once and visibly: the mapping from the
platform's job labels to DeskPilot's five skills is the auditor's
screen, not the owner's decision. Change the screen and the counts
change. It is a *candidate* mapping so the dry run has something to
chew on; the owner's real mapping is theirs to make, and until they
make one every posting arrives asking for nothing.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solvent.discovery import Compliance, Readiness          # noqa: E402
from solvent.finder import ExecutionHistory, Finder          # noqa: E402
from solvent.harness import (OWNER, Solvent, owner_promotions,  # noqa: E402
                             promote_capability)
from solvent.marketplaces import FreelancerActiveProjects    # noqa: E402
from tools.observe_freelancer_feed import CANDIDATE_LABELS   # noqa: E402

#: The owner's figures as supplied, plus the platform's published fee.
COMMERCIAL = {
    "hourly_rate_cents": 4_000,
    "minimum_project_fee_cents": 4_000,
    "minimum_margin_fraction": 0.30,
    "platform_fee_fraction": 0.10,
    "platform_fee_minimum_cents": 500,     # "10% or $5, whichever is greater"
    "payment_fee_fraction": 0.029,
    "payment_fee_flat_cents": 30,
    "compute_cents_per_cpu_hour": 50,
}

#: What the owner would grant on probation: small jobs, real room.
GRANT = {"max_jobs": 2, "max_value_cents": 15_000,
         "min_deadline_hours": 48}

#: Measured by tools/benchmark_skills.py on this machine. Benchmark
#: timings, which is exactly what probation is for.
BENCHMARK_SECONDS = {
    "csv-cleanup": 0.0009, "spreadsheet-automation": 0.00243,
    "document-processing": 0.00187, "data-entry": 0.00156,
    "report-builder": 0.00056,
}


def label_mapping() -> dict:
    """One platform label -> one skill, first match wins."""
    out: dict = {}
    for skill, labels in CANDIDATE_LABELS.items():
        for label in labels:
            out.setdefault(label, skill)
    return out


#: A rate the owner has NOT recorded and this auditor has not
#: researched. Present only so the dry run can show what the last
#: configuration gap is hiding behind it: with no cited rate the
#: Finder refuses every fixed-price job, and that refusal masks
#: whatever would have happened next. Labelled ASSUMED everywhere it
#: is reported, and it is not a market observation.
ASSUMED_MARKET_RATE_CENTS = 4_000
ASSUMED_CITATION = ("ASSUMED FOR THIS DRY RUN ONLY - no market rate has "
                    "been researched or recorded by the owner")


def configured(box: pathlib.Path, *, probation: bool,
               assume_market_rates: bool = False) -> Solvent:
    solvent = Solvent(str(box / "dryrun.db"))
    for name in owner_promotions():
        promote_capability(solvent, name, owner_identity=OWNER,
                           why="dry run: cleared for client work")
    terms = dict(COMMERCIAL)
    if assume_market_rates:
        terms["market_hourly_cents"] = {
            skill: ASSUMED_MARKET_RATE_CENTS for skill in BENCHMARK_SECONDS}
        terms["market_rate_citations"] = {
            skill: ASSUMED_CITATION for skill in BENCHMARK_SECONDS}
    solvent.policy.amend({"commercial": terms}, OWNER, "dry run")
    if probation:
        for skill in BENCHMARK_SECONDS:
            solvent.policy.grant_probation(
                owner_identity=OWNER, capability=skill, **GRANT)
    return solvent


def benchmarks() -> ExecutionHistory:
    return ExecutionHistory({skill: [seconds] * 7
                             for skill, seconds in BENCHMARK_SECONDS.items()})


def reason_for(bid) -> str:
    """One bucket per reason, so the tally is readable."""
    if bid.recommended:
        return "RECOMMENDED to the owner"
    text = (bid.why or "") + " " + " ".join(bid.blockers)
    for needle, bucket in (
            ("does not say which capability", "no skill mapped to its labels"),
            ("has not put", "capability not on probation"),
            ("states no price", "posting states no price"),
            ("no exchange rate", "budget not in the owner's currency"),
            ("is below", "budget below DeskPilot's floor"),
            ("ceiling", "above the probation value ceiling"),
            ("nobody knows", "needs longer than the probation window"),
            ("cannot be deployed", "capability not deployable"),
    ):
        if needle in text:
            return bucket
    return "other: " + text[:70]


def run(feed_dir: str, *, probation: bool = True,
        assume_market_rates: bool = False) -> dict:
    from tools.observe_freelancer_feed import load

    samples = load(feed_dir)
    projects = {}
    for sample in samples:
        for project in sample["projects"]:
            projects[project.get("id")] = project

    source = FreelancerActiveProjects(capability_for=label_mapping())
    payload = {"status": "success",
               "result": {"projects": list(projects.values())}}
    opportunities = source.parse(payload)

    tally = collections.Counter()
    recommended = []
    with tempfile.TemporaryDirectory() as name:
        box = pathlib.Path(name)
        solvent = configured(box, probation=probation,
                             assume_market_rates=assume_market_rates)
        try:
            finder = Finder(solvent.discovery, solvent.policy,
                            solvent.capability, solvent.orchestrator,
                            solvent.store)
            for opportunity in opportunities:
                bid = finder.bid(opportunity, history=ExecutionHistory(),
                                 benchmarks=benchmarks())
                tally[reason_for(bid)] += 1
                if bid.recommended:
                    recommended.append({
                        "ref": opportunity.external_ref,
                        "title": opportunity.title[:70],
                        "skill": opportunity.needs[0] if opportunity.needs
                                 else "",
                        "amount_cents": bid.amount_cents,
                        "floor_cents": bid.floor_cents,
                        "margin": round(bid.margin, 3) if bid.margin else None,
                    })
                if bid.submittable:
                    raise AssertionError(
                        "a dry run produced a submittable bid, which "
                        "probation must never do")
        finally:
            try:
                solvent.store.close()
            except Exception:
                pass

    return {
        "observed_postings": len(projects),
        "parsed_opportunities": len(opportunities),
        "probation_granted": probation,
        "market_rates_assumed": assume_market_rates,
        "outcomes": dict(tally.most_common()),
        "recommended": recommended,
        "submittable": 0,
        "note": ("Postings are real and were returned by the platform's "
                 "public endpoint. The label-to-skill mapping is the "
                 "auditor's screen, not the owner's decision."),
    }


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/feed"
    print(json.dumps({
        "without_probation": run(directory, probation=False),
        "with_probation": run(directory, probation=True),
        "with_probation_and_assumed_rates": run(
            directory, probation=True, assume_market_rates=True),
    }, indent=2, default=str))
