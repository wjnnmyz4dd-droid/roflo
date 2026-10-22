#!/usr/bin/env python3
"""Does an installed DeskPilot's business state survive a restart?

Three separate interpreters against one installed application, with
a real close between each. The first writes a prospect, a signed
contract and a cleared 20% deposit; the second reads them back; the
third reads them again and checks that the second did not duplicate
anything by looking.

A single process asserting its own dictionaries would pass against
the defect this is here to catch -- the Chief of Staff held its
negotiations and engagements in memory until recently, and every
restart emptied them.

Nothing external happens. Simulation mode stays on, the payment
event is synthetic and carries its own external reference, and the
client is invented.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

TODAY = "2026-10-22"
SEEN = "2026-10-01"

#: Written by the first process, read by the two after it.
WRITE = '''
import os, secrets, sys
os.environ.setdefault("SOLVENT_OWNER_KEY", %(key)r)
from solvent.harness import Solvent
from solvent.crm import Basis, Contact, Evidence, Standing
from solvent.engagement import Acceptance, Deliverable, Engagement, Proposal
s = Solvent(%(db)r)
s.crm.discover(prospect_id="restart-1", company="Lone Star Framing",
               industry="construction", state="TX")
s.crm.add_evidence("restart-1", Evidence(
    "advertised for a data-entry clerk twice this year",
    Basis.PUBLISHED, "the company's own careers page", %(seen)r))
s.crm.add_contact("restart-1", Contact(
    "Dana Reyes", "dana@restart-1.example", "office manager",
    "named on the careers page", %(seen)r))
s.crm.advance("restart-1", Standing.QUALIFIED, "windows restart check")
proposal = Proposal(
    proposal_id="restart-pr", prospect_id="restart-1",
    service="csv-cleanup", price_cents=100000,
    deliverables=(Deliverable("cleaned csv", "csv-cleanup",
                              "the checker recomputes it"),),
    turnaround_days=3, revisions_included=1)
deal = Engagement(engagement_id="restart-e", proposal=proposal)
s.chief_of_staff.engagements["restart-1"] = deal
deal.send()
deal.accept(Acceptance("Dana Reyes", %(today)r, "email", "sha256:synthetic"))
deal.request_deposit()
deal.record_payment({"verified": True, "cents": 20000,
                     "external_ref": "pi_restart_deposit"})
print("WROTE", deal.stage.value, deal.collected_cents)
s.store.close()
'''

READ = '''
import os, sys
os.environ.setdefault("SOLVENT_OWNER_KEY", %(key)r)
from solvent.harness import Solvent
s = Solvent(%(db)r)
chief = s.chief_of_staff
book = chief.engagements
deal = book.get("restart-1")
step = chief.next_step("restart-1", today=%(today)r)
print("DURABLE", chief.durable)
print("PROSPECTS", len(chief.crm.all()))
print("ENGAGEMENTS", len(book))
print("STAGE", None if deal is None else deal.stage.value)
print("COLLECTED", None if deal is None else deal.collected_cents)
print("PAYMENTS", None if deal is None else len(deal.payments))
print("AUTHORISED", None if deal is None else deal.work_authorised)
print("ACCEPTED_BY", None if deal is None or deal.acceptance is None
      else deal.acceptance.accepted_by)
print("STEP", step.step.value)
s.store.close()
'''

REPLAY = '''
import os, sys
os.environ.setdefault("SOLVENT_OWNER_KEY", %(key)r)
from solvent.harness import Solvent
s = Solvent(%(db)r)
deal = s.chief_of_staff.engagements["restart-1"]
# The same rail event delivered twice, which is the ordinary case.
deal.record_payment({"verified": True, "cents": 20000,
                     "external_ref": "pi_restart_deposit"})
print("AFTER_REPLAY", deal.collected_cents, len(deal.payments))
s.store.close()
'''


def run(python: str, code: str) -> subprocess.CompletedProcess:
    return subprocess.run([python, "-I", "-c", code], capture_output=True,
                          text=True, check=False, timeout=600)


def said(out: str) -> dict:
    facts = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            facts[parts[0]] = " ".join(parts[1:])
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    python = os.path.join(args.root, "venv", "Scripts", "python.exe")
    db = os.path.join(args.root, "data", "deskpilot.db")
    if not os.path.exists(python):
        print(f"FAIL  no installed interpreter at {python}")
        return 1
    fill = {"db": db, "key": "0" * 64, "today": TODAY, "seen": SEEN}

    rows: list[dict] = []

    def record(name: str, ok: bool, detail: str) -> None:
        print(f"{'PASS' if ok else 'FAIL':4}  {name}: {detail}", flush=True)
        rows.append({"name": name, "ok": ok, "detail": detail})

    first = run(python, WRITE % fill)
    record("a contract and a cleared deposit are written",
           first.returncode == 0 and "WROTE DEPOSIT_RECEIVED 20000"
           in first.stdout,
           first.stdout.strip() or first.stderr.strip()[-300:])
    if first.returncode != 0:
        return _finish(rows, args.out)

    second = run(python, READ % fill)
    facts = said(second.stdout)
    record("the books are durable", facts.get("DURABLE") == "True",
           facts.get("DURABLE", second.stderr.strip()[-200:]))
    record("the prospect survived the restart",
           facts.get("PROSPECTS") == "1", facts.get("PROSPECTS", "?"))
    record("the engagement survived the restart",
           facts.get("ENGAGEMENTS") == "1", facts.get("ENGAGEMENTS", "?"))
    record("it is still at the stage it reached",
           facts.get("STAGE") == "DEPOSIT_RECEIVED", facts.get("STAGE", "?"))
    record("the verified deposit survived",
           facts.get("COLLECTED") == "20000", facts.get("COLLECTED", "?"))
    record("the acceptance survived with who signed it",
           facts.get("ACCEPTED_BY") == "Dana Reyes",
           facts.get("ACCEPTED_BY", "?"))
    record("work is authorised, recomputed not restored",
           facts.get("AUTHORISED") == "True", facts.get("AUTHORISED", "?"))
    record("the next step is the one it stopped at, not an earlier one",
           facts.get("STEP") == "ADMIT_WORK", facts.get("STEP", "?"))

    third = run(python, REPLAY % fill)
    replayed = said(third.stdout).get("AFTER_REPLAY", "")
    record("a replayed payment event is not counted twice",
           replayed == "20000 1", replayed or third.stderr.strip()[-200:])

    fourth = run(python, READ % fill)
    after = said(fourth.stdout)
    record("reading it twice created no second engagement",
           after.get("ENGAGEMENTS") == "1", after.get("ENGAGEMENTS", "?"))
    record("and no second payment",
           after.get("PAYMENTS") == "1", after.get("PAYMENTS", "?"))
    return _finish(rows, args.out)


def _finish(rows: list[dict], out: str) -> int:
    failed = [r for r in rows if not r["ok"]]
    print()
    print(f"{len(rows) - len(failed)}/{len(rows)} check(s) passed")
    if out:
        pathlib.Path(out).write_text(
            json.dumps({"rows": rows, "failed": [r["name"] for r in failed]},
                       indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
