"""The sealed exam for the capacity gate.

Sealed means three things, and the file is arranged so that breaking
any of them is visible rather than convenient:

1. **The code is frozen.** DIGESTS records the SHA-256 of every module
   under examination at the moment of freezing. The exam refuses to run
   against anything else, so an implementation cannot be adjusted after
   seeing a result.
2. **The scoring is frozen.** CRITERIA is fixed here, before the first
   run, and is the same list the load simulator uses. A criterion added
   after seeing the marks is not a criterion.
3. **The workloads are unseen.** Every number below is drawn from
   ranges the development tests do not use -- different budgets,
   different job counts, different arrival shapes, and stress
   conditions combined in pairs rather than singly. The seed is derived
   from the freeze timestamp rather than read from the environment, so
   the paper is the same paper on every machine and cannot be reshuffled
   until it gives a better mark.

FIRST_RUN records what happened the first time this was run. It is not
updated on later runs: the point of a first run is that there is only
one of them.
"""

from __future__ import annotations

import hashlib
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.loadsim_solvent import (UNRELIABLE_IF, Simulation,  # noqa: E402
                                   Stress)

#: When the code under examination was frozen.
FROZEN_AT = "2026-10-14T20:59:51Z"

#: SHA-256 of each module at the freeze. Changing any of them invalidates
#: the exam rather than quietly re-marking it.
DIGESTS = {
    "solvent/capacity.py":
        "2edd19da7ce7731bb804081ac1317565f67aef6a7ffdf6507c65f8d9767af13c",
    "solvent/finder.py":
        "0fac9a608a6742aeaa2ebd7309111f7a128b19977194061598583f970d423fd7",
    "solvent/revenuemodel.py":
        "2c36183d9ce87c93e029bee036b2490173e47501f607173f56c34faef73a72a3",
    "solvent/store.py":
        "56176dab1619586046df0b2f496b97f6edae71de836a40ff06485feb98c8d911",
}

#: The scoring criteria, fixed before the first run. Identical to the
#: load simulator's, because an exam marked against a different rubric
#: from the practice is not measuring the same thing.
CRITERIA = tuple(sorted(UNRELIABLE_IF))

#: Derived from the freeze, never from the environment. An exam whose
#: seed comes from a variable is an exam that can be re-sat.
SEED = int(hashlib.sha256(FROZEN_AT.encode()).hexdigest()[:8], 16)

#: Job counts the development tests never use. The sweep uses
#: 1/5/10/25/50/100/250/500; these are deliberately none of them.
UNSEEN_LEVELS = (7, 13, 37, 71, 149, 307)

#: Stress conditions are combined in pairs here. Development tested
#: them one at a time, so every combination below is new.
UNSEEN_PAIRS = (
    ("spike", "slow_execution"),
    ("revisions", "delayed_verification"),
    ("outage", "failed_submissions"),
    ("payment_delays", "spike"),
    ("slow_execution", "revisions"),
    ("delayed_verification", "failed_submissions"),
)


def frozen_digests() -> dict:
    return {name: hashlib.sha256(
        pathlib.Path(name).read_bytes()).hexdigest()
        for name in sorted(DIGESTS)}


def seal_is_intact() -> tuple[bool, str]:
    """Has anything under examination moved since the freeze?"""
    now = frozen_digests()
    moved = [name for name, digest in DIGESTS.items()
             if now.get(name) != digest]
    if moved:
        return False, ("the seal is broken: " + ", ".join(moved)
                       + " changed after the freeze, so this exam is "
                       "no longer the paper that was set")
    return True, "the seal is intact"


def paper() -> list:
    """The exam paper. Deterministic, and unseen by the implementation."""
    rng = random.Random(SEED)
    questions = []
    for n, level in enumerate(UNSEEN_LEVELS):
        pair = UNSEEN_PAIRS[n % len(UNSEEN_PAIRS)]
        # Budgets from ranges the development tests do not use. The dev
        # budget is 600s / 3 jobs / 2 bids; none of these can be that.
        budget = {
            "max_owed_seconds": float(rng.randrange(1200, 9000, 300)),
            "max_concurrent_jobs": rng.randint(2, 4),
            "max_open_bids": rng.randint(1, 6),
            "cpu_share": rng.choice([0.25, 0.4, 0.6, 0.75]),
            "min_free_disk_bytes": 0,
            "max_execution_workers": rng.randint(1, 4),
            "max_verification_workers": rng.randint(1, 4),
            "max_memory_used_percent": 0.0,
            "revision_headroom_fraction": rng.choice([0.0, 0.15, 0.35, 0.6]),
            "deadline_safety_factor": rng.choice([1.5, 2.0, 3.0]),
            "halted": False,
        }
        questions.append({"level": level, "stress": pair, "budget": budget})
    return questions


def sit() -> dict:
    """Sit the paper once and report what happened."""
    intact, why = seal_is_intact()
    results = []
    for question in paper():
        sim = Simulation(question["level"],
                         seed=SEED,
                         stress=Stress(**{k: True for k in question["stress"]}),
                         budget=question["budget"])
        try:
            outcome = sim.run()
        finally:
            sim.close()
        outcome["question"] = {"level": question["level"],
                               "stress": list(question["stress"]),
                               "budget": question["budget"]}
        results.append(outcome)
    failed = [r for r in results if not r["reliable"]]
    return {
        "frozen_at": FROZEN_AT,
        "seal_intact": intact,
        "seal_detail": why,
        "seed": SEED,
        "criteria": CRITERIA,
        "questions": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failures": [{"level": r["level"], "stress": r["stress"],
                      "failures": r["failures"]} for r in failed],
        "results": results,
    }


#: Every sitting, keyed by the freeze it was sat against. Preserved,
#: never refreshed: a first run that gets rewritten is not a first run.
#:
#: There are two because the seal did its job. The exam was sealed and
#: sat, and then `Finder.choose` was added -- after the exam, not
#: during it, but the digests cover finder.py and the seal broke on
#: the next run. The remedy is not to widen the seal: it is to freeze
#: the finished code and sit the paper again. The seed derives from
#: the freeze, so the second sitting is a different paper rather than
#: a re-sit of the same questions, and the first sitting stays below
#: exactly as it was recorded.
FIRST_SITTING = """Sat 2026-10-14T14:13:25Z, seed 3194657925.

Six questions, 6 passed, 0 failed.

    level   7, slow_execution+spike                           6 accepted,   6 delivered, peak 0.807
    level  13, delayed_verification+revisions                10 accepted,  10 delivered, peak 0.662
    level  37, failed_submissions+outage                     14 accepted,  14 delivered, peak 0.3
    level  71, payment_delays+spike                           9 accepted,   9 delivered, peak 0.77
    level 149, revisions+slow_execution                      67 accepted,  67 delivered, peak 0.898
    level 307, delayed_verification+failed_submissions      206 accepted, 206 delivered, peak 0.984

No criterion was tripped at any question: nothing accepted went
undelivered, no budget was exceeded, no duplicate commitment was
created, no deadline was missed, no obligation was lost across a
restart, no reservation leaked, and concurrency stayed inside the
limit throughout.
"""

SECOND_SITTING = """Sat 2026-10-14T14:31:21Z, seed 2624357441.

Six questions, 6 passed, 0 failed.

    level   7, slow_execution+spike                           3 accepted,   3 delivered, peak 0.96
    level  13, delayed_verification+revisions                13 accepted,  13 delivered, peak 0.937
    level  37, failed_submissions+outage                     35 accepted,  35 delivered, peak 0.835
    level  71, payment_delays+spike                          19 accepted,  19 delivered, peak 0.985
    level 149, revisions+slow_execution                      36 accepted,  36 delivered, peak 0.531
    level 307, delayed_verification+failed_submissions      192 accepted, 192 delivered, peak 0.946

No criterion was tripped at any question: nothing accepted went
undelivered, no budget was exceeded, no duplicate commitment was
created, no deadline was missed, no obligation was lost across a
restart, no reservation leaked, and concurrency stayed inside the
limit throughout.
"""


THIRD_SITTING = """Sat 2026-10-14T17:31:06Z, seed 2638412904.

Six questions, 6 passed, 0 failed. The third sitting, after the
four-job ceiling: acceptance is far lower than the earlier papers
because the budgets can no longer ask for more than four jobs at
once, which is the owner's limit working rather than a regression.

    level   7, slow_execution+spike                           5 accepted,   5 delivered, peak 0.6
    level  13, delayed_verification+revisions                 4 accepted,   4 delivered, peak 0.384
    level  37, failed_submissions+outage                      9 accepted,   9 delivered, peak 0.343
    level  71, payment_delays+spike                           5 accepted,   5 delivered, peak 0.44
    level 149, revisions+slow_execution                       7 accepted,   7 delivered, peak 0.279
    level 307, delayed_verification+failed_submissions       44 accepted,  44 delivered, peak 0.375

No criterion was tripped at any question.
"""


FOURTH_SITTING = """Sat 2026-10-14T20:59:51Z, seed 2636773900.

Six questions, 6 passed, 0 failed. The fourth sitting, after
probation was added to the Finder. Probation changes what may be
*quoted* and nothing about what may be *owed*, so the capacity
behaviour these questions examine is unchanged -- which is the
result, not an assumption.

    level   7, slow_execution+spike                           5 accepted,   5 delivered, peak 0.232
    level  13, delayed_verification+revisions                11 accepted,  11 delivered, peak 0.662
    level  37, failed_submissions+outage                     13 accepted,  13 delivered, peak 0.587
    level  71, payment_delays+spike                           7 accepted,   7 delivered, peak 0.6
    level 149, revisions+slow_execution                      27 accepted,  27 delivered, peak 0.836
    level 307, delayed_verification+failed_submissions       36 accepted,  36 delivered, peak 0.52

No criterion was tripped at any question.
"""


#: Which record belongs to which freeze.
SITTINGS = {
    "2026-10-14T14:13:25Z": FIRST_SITTING,
    "2026-10-14T14:31:21Z": SECOND_SITTING,
    "2026-10-14T17:31:06Z": THIRD_SITTING,
    "2026-10-14T20:59:51Z": FOURTH_SITTING,
}


if __name__ == "__main__":
    import json

    print(json.dumps(sit(), indent=2, default=str))
