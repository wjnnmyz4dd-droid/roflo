#!/usr/bin/env python3
"""Measure what is actually on the one accessible job feed.

Evidence gathering, not acquisition. This reads the public,
unauthenticated, documented endpoint that Freelancer's own robots.txt
does not disallow, and counts what comes back. It submits nothing,
creates nothing and spends nothing.

It is deliberately *not* wired into DeskPilot. DeskPilot may not poll
this platform until the owner has read its API terms and recorded a
determination, and that is still true. This tool exists so the owner
has numbers to make that decision with, rather than deciding whether
to pursue a platform on the strength of its marketing page.

Two things it refuses to do, because the whole point is evidence:

It does not call a posting "a job DeskPilot can do". It counts the
platform's own job labels and reports the ones that *might* map to
the five skills as **candidates**, under a screen the owner can
read and disagree with. A taxonomy label is not a statement about
what a client wants.

It does not convert currencies. A budget in INR is reported as a
budget in INR. Deciding how many dollars that is needs an exchange
rate, and a feed summary built on an invented rate would be a
measurement of nothing.

Usage::

    python3 tools/observe_freelancer_feed.py sample out/dir
    python3 tools/observe_freelancer_feed.py report out/dir
"""

from __future__ import annotations

import collections
import json
import pathlib
import statistics
import sys
import time

#: Platform job labels that *might* correspond to one of the five
#: certified skills. A screen for counting, never a mapping: whether a
#: posting labelled "Excel" wants spreadsheet-automation is a judgement
#: about that posting, and the owner makes it.
CANDIDATE_LABELS = {
    "csv-cleanup": ("data cleansing", "data processing", "data entry",
                    "excel", "data mining"),
    "spreadsheet-automation": ("excel", "visual basic", "data processing",
                               "data analysis"),
    "document-processing": ("word", "pdf", "copy typing", "editing",
                            "proofreading", "transcription"),
    "data-entry": ("data entry", "copy typing", "web search",
                   "data processing"),
    "report-builder": ("report writing", "research writing",
                       "technical writing", "data analysis"),
}

#: The owner's currency. Everything else is counted separately rather
#: than converted.
OWNER_CURRENCY = "USD"


def load(directory: str) -> list:
    """Every sample on disk, oldest first."""
    out = []
    for path in sorted(pathlib.Path(directory).glob("s*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("status") != "success":
            continue
        result = payload.get("result") or {}
        out.append({
            "file": path.name,
            "mtime": path.stat().st_mtime,
            "total_count": result.get("total_count"),
            "projects": [p for p in (result.get("projects") or [])
                         if isinstance(p, dict)],
        })
    return out


def labels_of(project: dict) -> list:
    jobs = project.get("jobs")
    if not isinstance(jobs, list):
        return []
    return [str(j.get("name", "")).strip().lower()
            for j in jobs if isinstance(j, dict) and j.get("name")]


def candidates(project: dict) -> list:
    """Which of the five a posting is a candidate for, under the screen."""
    seen = set(labels_of(project))
    return sorted(skill for skill, wanted in CANDIDATE_LABELS.items()
                  if seen & set(wanted))


def report(directory: str) -> dict:
    samples = load(directory)
    if not samples:
        return {"error": f"no samples in {directory}"}

    unique: dict = {}
    for sample in samples:
        for project in sample["projects"]:
            unique[project.get("id")] = project

    currencies = collections.Counter()
    types = collections.Counter()
    labels = collections.Counter()
    per_skill = collections.Counter()
    usd_minimums = []
    usd_candidate_minimums = []
    bids = []

    for project in unique.values():
        currency = project.get("currency") or {}
        code = str(currency.get("code", "") or "").upper() or "UNSTATED"
        currencies[code] += 1
        types[str(project.get("type", "") or "unstated").lower()] += 1
        for label in labels_of(project):
            labels[label] += 1
        matched = candidates(project)
        for skill in matched:
            per_skill[skill] += 1
        budget = project.get("budget") or {}
        minimum = budget.get("minimum")
        if code == OWNER_CURRENCY and isinstance(minimum, (int, float)):
            usd_minimums.append(float(minimum))
            if matched:
                usd_candidate_minimums.append(float(minimum))
        stats = project.get("bid_stats") or {}
        if isinstance(stats.get("bid_count"), int):
            bids.append(stats["bid_count"])

    first, last = samples[0]["mtime"], samples[-1]["mtime"]

    def spread(values):
        if not values:
            return None
        ordered = sorted(values)
        return {
            "n": len(ordered),
            "min": ordered[0],
            "median": statistics.median(ordered),
            "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
            "max": ordered[-1],
        }

    # How fast new work appears, which is the number that decides
    # whether a platform is worth watching at all. Counted as ids not
    # seen in any earlier sample, so it is arrivals rather than churn.
    seen: set = set()
    arrivals = []
    for n, sample in enumerate(samples):
        ids = {p.get("id") for p in sample["projects"]}
        fresh = ids - seen
        candidates_fresh = sum(
            1 for p in sample["projects"]
            if p.get("id") in fresh and candidates(p))
        if n:
            gap = sample["mtime"] - samples[n - 1]["mtime"]
            arrivals.append({"new": len(fresh),
                             "new_candidates": candidates_fresh,
                             "after_seconds": round(gap, 1)})
        seen |= ids

    elapsed = max(1.0, last - first)
    new_after_first = sum(a["new"] for a in arrivals)
    new_candidates_after_first = sum(a["new_candidates"] for a in arrivals)

    any_candidate = sum(1 for p in unique.values() if candidates(p))
    return {
        "arrivals_per_sample": arrivals,
        "new_projects_per_hour": round(new_after_first / elapsed * 3600, 1),
        "new_candidates_per_hour": round(
            new_candidates_after_first / elapsed * 3600, 1),
        "observed": True,
        "samples": len(samples),
        "observation_seconds": round(last - first, 1),
        "distinct_projects_seen": len(unique),
        "platform_total_count": [s["total_count"] for s in samples],
        "currencies": dict(currencies.most_common()),
        "project_types": dict(types),
        "top_labels": dict(labels.most_common(25)),
        "candidates_for_any_of_the_five": any_candidate,
        "candidates_by_skill": dict(per_skill.most_common()),
        "usd_budget_minimum": spread(usd_minimums),
        "usd_candidate_budget_minimum": spread(usd_candidate_minimums),
        "existing_bids_per_project": spread(bids),
        "caveats": [
            "A candidate is a posting whose platform labels overlap a "
            "screen in this file. It is not a statement that DeskPilot "
            "can do the job, and the screen is the auditor's, not the "
            "platform's.",
            "Budgets are the floor of each posting's range, in the "
            "posting's own currency. Nothing is converted.",
            "Counts are of what the feed returned in these samples, not "
            "of everything on the platform.",
        ],
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    command, directory = sys.argv[1], sys.argv[2]
    if command == "report":
        print(json.dumps(report(directory), indent=2, default=str))
        return 0
    if command == "sample":
        print("this tool does not fetch; sample with curl and write the "
              "response to <dir>/sNN.json, so that every network call is "
              "visible in the transcript that made it")
        return 2
    print(f"unknown command {command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
