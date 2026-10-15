#!/usr/bin/env python3
"""Print the four messages the owner would actually receive.

Not a description of them and not a mock-up: a real Solvent, a real
business cycle through ``run_business_cycle``, and the four messages
read back out of the notifier afterwards, exactly as they were
composed. The point is that the owner can read the words before
switching anything on, rather than discovering at 06:50 that the
summary says something they did not expect.

The payment in this demonstration is simulated, and the message says
so in those words. That is the whole reason this tool prints the body
rather than the subject line: a payment notification that did not
distinguish simulated money from real money would be the single most
expensive sentence in the system.

Nothing here contacts a marketplace, sends an email, or moves money.
The notifier writes to the local store and the two summaries are
composed against this throwaway database.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solvent import briefing                              # noqa: E402
from solvent.harness import OWNER, Solvent                # noqa: E402

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

BUDGET = {"max_owed_seconds": 100_000.0, "max_concurrent_jobs": 4,
          "max_execution_workers": 5, "max_verification_workers": 5,
          "max_open_bids": 4, "cpu_share": 0.5, "min_free_disk_bytes": 0,
          "skills": list(SKILLS), "pipeline_target": 4, "replenish_at": 2,
          "halted": False}


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def show(today: str = "2026-10-15") -> int:
    with tempfile.TemporaryDirectory() as name:
        box = pathlib.Path(name)
        solvent = Solvent(str(box / "briefings.db"))
        try:
            solvent.policy.amend({"capacity": BUDGET}, OWNER,
                                 "briefing demonstration")

            briefing.job_obtained(
                solvent, job_id="demo-1", capability="csv-cleanup",
                amount_cents=4_000, source="owner_entered")
            briefing.payment_received(
                solvent, job_id="demo-1", amount_cents=4_000,
                simulated=True, method="simulated")
            for slot in briefing.SUMMARY_TIMES:
                briefing.send_summary(solvent, slot=slot, today=today)

            by_event = {}
            for row in solvent.notifier.notifications():
                by_event.setdefault(row["event"], []).append(row)

            for event in briefing.EVENTS:
                rows = by_event.get(event, [])
                _rule(f"{event}   ({len(rows)} message(s))")
                if not rows:
                    print("  NOT SENT -- this is a defect, not a demo")
                    continue
                for row in rows:
                    print(f"  subject: {row['summary']}")
                    body = (row.get("body") or "").rstrip()
                    for line in body.splitlines():
                        print(f"  | {line}")
            missing = [e for e in briefing.EVENTS if not by_event.get(e)]
            _rule("result")
            if missing:
                print("  MISSING: " + ", ".join(missing))
                return 1
            print(f"  all {len(briefing.EVENTS)} messages sent, "
                  f"slots {', '.join(briefing.SUMMARY_TIMES)}")
            return 0
        finally:
            try:
                solvent.store.close()
            except Exception:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", default="2026-10-15")
    return show(parser.parse_args(argv).today)


if __name__ == "__main__":
    raise SystemExit(main())
