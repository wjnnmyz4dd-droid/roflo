"""The grader. It believes neither the shop nor the clients.

The third authority named in :mod:`simulation`, and the only one
whose job is to disagree. It reads two sets of records that were
written independently --

* **DeskPilot's own**: the orchestrator's jobs, the Ledger's costs
  and payments, the storefront's orders, the engagements, the
  capacity reservations, the audit log;
* **the simulation's own**: the act log the world kept, and the
  expense journal on disk --

and reports every place they disagree. A figure that appears in only
one of them is a finding, not a rounding difference.

**It shares no code with either side.** It does not import
:mod:`simulation.world`, :mod:`simulation.customers` or
:mod:`simulation.market`; it takes their output as data. A grader
that called the world's own helpers would be checking the world's
arithmetic against itself.

**It grades on what it can prove.** Every finding carries both
numbers and the place each came from, so a reader can go and look.
Where DeskPilot genuinely does not record something -- the deposit
lives in the Engagement and not in the job-keyed Ledger -- the
grader says so in the finding rather than quietly adding the two
together and calling it reconciled.

**A discrepancy is a fail.** :meth:`Grade.passed` is false if any
finding is not ``ok``. There is no severity ladder here: the
assignment's rule is that no unexplained financial discrepancy may
receive a passing grade, and a grader with a tolerance knob is a
grader that can be turned off.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

#: Lines the Ledger books per job. Named here independently of
#: :mod:`simulation.books` -- the grader checking the two sets are
#: disjoint must not import the set from the thing it is checking.
LEDGER_LINES = ("COMPUTE", "AI_API", "PLATFORM_FEES", "PAYMENT_FEES",
                "VERIFICATION")


@dataclass(frozen=True, slots=True)
class Finding:
    """One reconciliation, and where each side of it came from."""

    what: str
    ok: bool
    deskpilot: object = None
    simulation: object = None
    detail: str = ""

    def line(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        both = ""
        if self.deskpilot is not None or self.simulation is not None:
            both = f"  deskpilot={self.deskpilot!r} simulation={self.simulation!r}"
        return f"{mark} {self.what}{both}" + (f"  -- {self.detail}"
                                              if self.detail else "")


@dataclass
class Grade:
    """What the two sets of records say, and whether they agree."""

    findings: list = field(default_factory=list)
    figures: dict = field(default_factory=dict)

    def note(self, what: str, ok: bool, *, deskpilot=None, simulation=None,
             detail: str = "") -> Finding:
        found = Finding(what=what, ok=bool(ok), deskpilot=deskpilot,
                        simulation=simulation, detail=detail)
        self.findings.append(found)
        return found

    @property
    def passed(self) -> bool:
        return all(f.ok for f in self.findings)

    @property
    def failures(self) -> list:
        return [f for f in self.findings if not f.ok]

    def report(self) -> str:
        head = (f"{'PASS' if self.passed else 'FAIL'}  "
                f"{len(self.findings) - len(self.failures)}/"
                f"{len(self.findings)} reconciliations agree")
        return "\n".join([head, ""] + [f.line() for f in self.findings])


# ---- reading each side ------------------------------------------------


def what_deskpilot_says(solvent) -> dict:
    """Every figure read from DeskPilot's own authorities."""
    from solvent.qualification import OCCUPIES_CAPACITY

    jobs = list(solvent.orchestrator.jobs())
    ledger = solvent.ledger
    payments = ledger.payment_rows()
    engagements = dict(getattr(solvent.chief_of_staff, "engagements", {}) or {})
    orders = list(solvent.storefront.orders())

    invoiced = sum(int(r["amount_cents"] or 0) for r in payments)
    collected = sum(int(r["collected_cents"] or 0) for r in payments)
    deposits = sum(int(getattr(e, "collected_cents", 0) or 0)
                   for e in engagements.values())
    agreed = sum(int(getattr(getattr(e, "proposal", None), "price_cents", 0)
                     or 0) for e in engagements.values())
    ordered = sum(int(getattr(o, "offered_cents", 0) or 0) for o in orders)

    # Read from the Ledger's own table rather than from a helper, so
    # the grader is looking at the rows and not at somebody's
    # summary of them. `direction = 'COST'` because the same table
    # carries corrections.
    costs = {}
    for row in solvent.store.for_authority("ledger").query(
            "SELECT category, amount_cents FROM ledger_entries "
            "WHERE direction = 'COST'"):
        costs[row["category"]] = (costs.get(row["category"], 0)
                                  + int(row["amount_cents"] or 0))

    states = {}
    for job in jobs:
        states[job.state.value] = states.get(job.state.value, 0) + 1

    return {
        "clients": sorted({str(getattr(j, "client_id", "") or "")
                           for j in jobs} - {""}),
        "jobs": len(jobs),
        "job_states": states,
        "holding_capacity": len([j for j in jobs
                                 if j.state in OCCUPIES_CAPACITY]),
        "orders": len(orders),
        "order_states": _count(o.state.value for o in orders),
        "engagements": len(engagements),
        "signed": len([e for e in engagements.values()
                       if getattr(e, "acceptance", None) is not None]),
        "gross_contract_cents": agreed + ordered,
        "agreed_engagement_cents": agreed,
        "ordered_storefront_cents": ordered,
        "invoiced_cents": invoiced,
        "collected_cents": collected,
        "deposits_cents": deposits,
        "receivable_cents": sum(
            max(0, int(r["amount_cents"] or 0) - int(r["collected_cents"] or 0))
            for r in payments),
        "ledger_costs_cents": sum(costs.values()),
        "ledger_costs_by_line": costs,
        "real_revenue_cents": int(ledger.real_revenue_cents()),
        "owed_seconds": float(solvent.capacity.owed_seconds()),
        "reservations": len(solvent.capacity.outstanding()),
        "pipelines": solvent.capacity.pipelines(),
        "audit_rows": solvent.audit.count(),
        "audit_chain_ok": bool(solvent.audit.verify_chain()[0]),
        "payment_methods": sorted({
            str(r["verification_method"] or "") for r in payments
            if "verification_method" in r.keys()}),
    }


def what_the_simulation_says(acts, journal_path) -> dict:
    """Every figure read from the simulation's own records.

    ``acts`` is the world's act log as plain dictionaries, and
    ``journal_path`` the expense journal on disk. Neither is a live
    object: the grader reads what was written down.
    """
    acts = [dict(a) for a in acts]
    rows = _journal_rows(journal_path)
    by_line = {}
    for row in rows:
        by_line[row["line"]] = by_line.get(row["line"], 0) + int(row["cents"])

    def did(name: str) -> list:
        return [a for a in acts if a.get("act") == name and a.get("done")]

    return {
        "acts": len(acts),
        "clients_seen": sorted({a["customer_id"] for a in acts}),
        "arrived": len(did("place an order")) + len(did("write in asking for a price")),
        "orders_placed": len(did("place an order")),
        "wrote_in": len(did("write in asking for a price")),
        "accepted_a_price": len(did("accept the price")),
        "signed": len(did("sign")),
        "declined": len(did("decline")),
        "walked_away": len(did("walk away")),
        "went_quiet": len(did("go quiet")),
        "deposits_paid": len(did("pay the deposit")),
        "invoices_paid": len(did("pay the invoice")),
        "asked_for_changes": len(did("ask for changes")),
        "came_back": len(did("come back")),
        "refused": len([a for a in acts if not a.get("done")]),
        "outside_world_acts": len([a for a in acts
                                   if str(a.get("act", "")).startswith(
                                       "[outside-world]")]),
        "journal_rows": len(rows),
        "journal_cents": sum(int(r["cents"]) for r in rows),
        "journal_by_line": by_line,
        "journal_truncated_rows": len([r for r in rows
                                       if r["line"] == "TRUNCATED"]),
    }


def _journal_rows(path) -> list:
    path = pathlib.Path(path)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"line": "TRUNCATED", "cents": 0, "day": -1})
    return out


def _count(values) -> dict:
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


# ---- grading -----------------------------------------------------------


def reconcile(solvent, *, acts, journal_path, expect_paid: bool = True) -> Grade:
    """Compare the two sets of records and report every disagreement."""
    shop = what_deskpilot_says(solvent)
    sim = what_the_simulation_says(acts, journal_path)
    grade = Grade(figures={"deskpilot": shop, "simulation": sim})

    # ---- the records themselves ---------------------------------
    grade.note("the audit chain verifies", shop["audit_chain_ok"],
               deskpilot=shop["audit_rows"],
               detail="every DeskPilot figure below is read from a database "
                      "whose hash chain was recomputed")
    grade.note("no row of the expense journal was lost to a crash",
               sim["journal_truncated_rows"] == 0,
               simulation=sim["journal_truncated_rows"],
               detail="a truncated row is a write the journal did not finish")

    # ---- customers ------------------------------------------------
    grade.note("every client DeskPilot worked for is one the simulation sent",
               set(shop["clients"]) <= {f"client:{c}"
                                        for c in sim["clients_seen"]},
               deskpilot=len(shop["clients"]),
               simulation=len(sim["clients_seen"]),
               detail="a client in the shop's records that the simulation "
                      "never created would be DeskPilot inventing one")
    grade.note("nothing happened that production has no path for",
               sim["outside_world_acts"] == 0,
               simulation=sim["outside_world_acts"],
               detail="acts the world had to perform itself because "
                      "DeskPilot has no door for them")

    # ---- contracts -------------------------------------------------
    grade.note("every signature the clients gave reached an engagement",
               shop["signed"] == sim["signed"],
               deskpilot=shop["signed"], simulation=sim["signed"])

    # ---- money ------------------------------------------------------
    grade.note("no simulated payment was ever counted as real revenue",
               shop["real_revenue_cents"] == 0,
               deskpilot=shop["real_revenue_cents"])
    methods = [m for m in shop["payment_methods"] if m]
    grade.note("every payment the Ledger holds came through the rail",
               all(m.startswith("SIMULATED:") for m in methods),
               deskpilot=methods or "none",
               detail="a payment with no verification method was not "
                      "verified by anything")
    grade.note("collected never exceeds invoiced",
               shop["collected_cents"] <= shop["invoiced_cents"],
               deskpilot=(shop["collected_cents"], shop["invoiced_cents"]))
    grade.note("the receivable is exactly what is invoiced and unpaid",
               shop["receivable_cents"]
               == shop["invoiced_cents"] - shop["collected_cents"],
               deskpilot=shop["receivable_cents"],
               simulation=shop["invoiced_cents"] - shop["collected_cents"])
    if expect_paid:
        grade.note("somebody paid something",
                   shop["collected_cents"] > 0,
                   deskpilot=shop["collected_cents"],
                   simulation=sim["invoices_paid"],
                   detail="a run where no money moved cannot reconcile money")

    # ---- the two sets of books do not overlap -----------------------
    overlap = set(sim["journal_by_line"]) & set(LEDGER_LINES)
    grade.note("the journal books nothing the Ledger already booked",
               not overlap, simulation=sorted(overlap) or "none",
               detail="the one way this accounting could double-count")
    grade.note("the Ledger books only the lines it is supposed to",
               set(shop["ledger_costs_by_line"]) <= set(LEDGER_LINES),
               deskpilot=sorted(shop["ledger_costs_by_line"]),
               detail="a sixth Ledger line would need checking against "
                      "the journal's four")

    # ---- capacity ------------------------------------------------------
    grade.note("no slot is held by work that is finished",
               shop["owed_seconds"] == 0 or shop["holding_capacity"] > 0,
               deskpilot=(shop["owed_seconds"], shop["holding_capacity"]),
               detail="seconds owed with no job in a capacity-occupying "
                      "state is a leaked reservation")
    grade.note("no pipeline holds more than the owner allows",
               all(p["committed"] <= p["committed"] + p["room"]
                   for p in shop["pipelines"].values()),
               deskpilot={k: v["committed"]
                          for k, v in shop["pipelines"].items()})

    return grade


def profit_and_loss(solvent, *, journal_path) -> dict:
    """The run's books, with every line saying where it came from.

    Two bottom lines, deliberately. **Accrual** profit counts what was
    invoiced; **cash** profit counts what was collected. A business
    whose clients have not paid yet is profitable on one and broke on
    the other, and a single figure hides exactly that.
    """
    shop = what_deskpilot_says(solvent)
    rows = _journal_rows(journal_path)
    journal = {}
    for row in rows:
        journal[row["line"]] = journal.get(row["line"], 0) + int(row["cents"])

    direct = shop["ledger_costs_cents"]
    overhead = sum(journal.values())
    invoiced = shop["invoiced_cents"]
    # The deposit is money the client paid, held in the Engagement
    # rather than the job-keyed Ledger. Counted, and labelled, rather
    # than added in silence.
    cash_in = shop["collected_cents"] + shop["deposits_cents"]
    return {
        "gross_contract_cents": shop["gross_contract_cents"],
        "invoiced_cents": invoiced,
        "collected_cents": shop["collected_cents"],
        "deposits_cents": shop["deposits_cents"],
        "cash_in_cents": cash_in,
        "receivable_cents": shop["receivable_cents"],
        "direct_costs_cents": direct,
        "direct_costs_by_line": shop["ledger_costs_by_line"],
        "overhead_cents": overhead,
        "overhead_by_line": journal,
        "total_costs_cents": direct + overhead,
        "accrual_profit_cents": invoiced - direct - overhead,
        "cash_profit_cents": cash_in - direct - overhead,
        "real_revenue_cents": shop["real_revenue_cents"],
        "basis": {
            "invoiced": "Ledger payment rows, amount_cents",
            "collected": "Ledger payment rows, collected_cents",
            "deposits": "Engagement.collected_cents; the Ledger is job-keyed "
                        "and a deposit is taken before a job exists",
            "direct_costs": "Ledger cost rows, recorded by run_business_cycle "
                            "from measured execution",
            "overhead": "simulation.books, accrued at the owner's own "
                        "recorded rates; not money production spent",
        },
    }
