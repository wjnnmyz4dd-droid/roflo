"""A child process that drives the chain and is killed partway through.

Run as a subprocess by :mod:`tests_solvent.test_crash_recovery`. It
is a separate file rather than a string written at test time so that
what gets killed is readable, reviewable code.

``SIGKILL``, not an exception. A raised error unwinds the stack and
runs ``finally`` blocks, which is the one thing a real crash does not
do: the process stops between two writes with no chance to tidy up.
Anything the database does not already hold at that instant is gone.

The kill is injected **inside** the production call stack, by
replacing one production function with a wrapper that calls the real
one and then dies. So the interruption lands where a real one would
-- between the Ledger's two writes, between a reservation and the
work it reserves for -- rather than at a convenient boundary.

Usage::

    python -m tests_solvent.crash_child <db> <spool> <stop_at> <stage>

``stage`` is "arrive" (build the contract) or "finish" (carry on from
whatever the database already holds). Exit code 0 means it ran to the
end without reaching its kill point, which is a failure of the test
rather than of the system, and the parent says so.
"""

from __future__ import annotations

import os
import pathlib
import signal
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Written just before the kill, so the parent can tell "died where we
#: asked" from "died somewhere else". One line, flushed and fsynced.
MARKER = "reached"

#: The most abrupt stop each platform has. POSIX has ``SIGKILL``, which
#: the kernel does not let the process handle. Windows has no such
#: signal, and ``os.kill`` there calls ``TerminateProcess``, which is
#: the same thing by a different name: no handler, no unwinding, no
#: ``finally``. Named once so the parent can assert on the exit code
#: rather than guessing it.
LETHAL = getattr(signal, "SIGKILL", signal.SIGTERM)

#: What the parent's ``subprocess.run`` reports for that stop. POSIX
#: reports a signalled child as the negated signal number; Windows
#: reports the exit code ``TerminateProcess`` was given, which is the
#: signal number itself.
KILLED_RETURNCODE = -LETHAL if hasattr(signal, "SIGKILL") else int(LETHAL)


def die(where: str, box: pathlib.Path) -> None:
    """Record that we got here, then stop the process dead."""
    note = box / "crash-marker.txt"
    with open(note, "w", encoding="utf-8") as handle:
        handle.write(f"{MARKER} {where}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.kill(os.getpid(), LETHAL)


def kill_inside(module, name: str, where: str, box: pathlib.Path,
                *, before: bool = False) -> None:
    """Replace a production function so it dies when it is reached."""
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        if before:
            die(where, box)
        result = real(*args, **kwargs)
        die(where, box)
        return result

    setattr(module, name, wrapper)


def main(argv: list) -> int:
    db, spool, stop_at, stage = argv[1], argv[2], argv[3], argv[4]
    # The spool's parent, which is the directory the parent test owns.
    # The database lives one level down, inside the shop.
    box = pathlib.Path(spool).parent

    from solvent import contracting, dealmaking, settlement
    from solvent.crm import Basis, Contact, Evidence, Standing
    from solvent.discovery import OpportunityStatus
    from solvent.engagement import Acceptance
    from solvent.harness import OWNER, Solvent
    from solvent.ledger import Ledger
    from solvent.runtime import Runtime, reconcile
    from solvent import harness as harness_module
    from solvent import orchestrator as orchestrator_module

    from simulation import rail
    from simulation.shop import open_a_shop

    if not pathlib.Path(db).exists():
        # The child builds the shop, because the parent must not: a
        # parent that constructs a ``Solvent`` installs the egress
        # guard, and the guard denies ``subprocess.Popen`` for the
        # rest of the interpreter. It is irreversible by design and
        # is not worked around.
        built = open_a_shop(pathlib.Path(db).parent,
                            policy={"runtime": {"payment_spool": spool}})
        built.store.close()

    solvent = Solvent(db)
    runtime = Runtime(solvent, clock=lambda: _clock[0])
    runtime.start()
    runtime.default_tasks()

    pid = "client:northstar"
    offer_id = "csv-cleanup"

    # ---- the injections, each inside a production call -------------
    if stop_at == "capacity-reservation":
        from solvent import capacity as capacity_module
        kill_inside(capacity_module.Capacity, "reserve",
                    stop_at, box)
    elif stop_at == "execution":
        kill_inside(harness_module, "_record_measurement", stop_at, box)
    elif stop_at == "quality-verification":
        kill_inside(orchestrator_module.JobOrchestrator, "mark_verified",
                    stop_at, box)
    elif stop_at == "invoice-creation":
        # Between the two writes the Ledger documents: the obligation
        # is recorded, the payment row is not yet open.
        kill_inside(Ledger, "open_payment", stop_at, box, before=True)
    elif stop_at == "settlement-reconciliation":
        kill_inside(orchestrator_module.JobOrchestrator, "complete",
                    stop_at, box, before=True)

    if stage == "arrive":
        crm = solvent.crm
        crm.discover(prospect_id=pid, company="Northstar Client 001",
                     industry="construction", state="TX")
        crm.add_evidence(pid, Evidence(
            claim="they asked for a price for csv-cleanup",
            basis=Basis.CLIENT_STATED, source="SIMULATION",
            seen_on="2026-01-05"))
        crm.add_contact(pid, Contact(
            name="Ops", email="ops@northstar-client.invalid",
            role="operations", source="SIMULATION", seen_on="2026-01-05"))
        for standing, why in ((Standing.QUALIFIED, "fits a skill we sell"),
                              (Standing.CONTACTED, "we answered them"),
                              (Standing.REPLIED, "they asked the price")):
            crm.advance(pid, standing, why)

        solvent.discovery.insert("inbox", owner_identity=OWNER, postings=[{
            "ref": f"enq-{pid}", "title": "csv-cleanup for Northstar",
            "quoted_cents": 60_000, "needs": ["csv-cleanup"],
            "client_ref": pid, "project_state": "TX", "client_state": "TX",
            "body": "SIMULATION"}])
        row = next(r for r in solvent.discovery.opportunities()
                   if r.get("client_ref") == pid)
        solvent.discovery.set_status(str(row["id"]), OpportunityStatus.ACCEPTED)

        opened = dealmaking.open_ready(solvent, limit=1, initiator="crash")
        if not opened or not opened[0].opened:
            print("NO_DEAL", opened and opened[0].why)
            return 2
        deal = solvent.chief_of_staff.negotiations[pid]
        deal.respond(deal.current_cents, why="the client accepted")

        drawn = contracting.propose(solvent, prospect_id=pid,
                                    initiator="crash")
        if not drawn.done:
            print("NO_PROPOSAL", drawn.why)
            return 2
        engagement = solvent.chief_of_staff.engagements[pid]
        if stop_at == "contract-acceptance":
            kill_inside(type(engagement), "accept", stop_at, box)
        engagement.accept(Acceptance("Ops", "2026-01-05", "email",
                                     f"sha256:{pid}"))
        request = engagement.request_deposit()
        engagement.record_payment({
            "verified": True, "cents": request["amount_cents"],
            "external_ref": f"sim_dep_{pid}"})
        engagement.supply(**_materials(solvent, box))
        print("CONTRACTED", engagement.proposal.price_cents)

    engagement = solvent.chief_of_staff.engagements.get(pid)
    if engagement is None:
        print("NO_ENGAGEMENT")
        return 3

    if stop_at == "job-admission":
        kill_inside(type(solvent.storefront), "place", stop_at, box)
    admitted = contracting.admit(solvent, prospect_id=pid, initiator="crash")
    print("ADMIT", admitted.refusal.value, admitted.why[:80])

    cycle = runtime._one_business_job()
    print("CYCLE", None if cycle is None else cycle.stopped_at,
          None if cycle is None else cycle.job_id)

    paid_for = [j for j in solvent.orchestrator.jobs()
                if j.state.value == "AWAITING_PAYMENT"]
    for job in paid_for:
        owed = int((solvent.ledger.payment_for(job.id) or {})
                   .get("amount_cents") or 0)
        if owed <= 0:
            continue
        rail.pay(spool, job_id=job.id, amount_cents=owed,
                 event_id=f"evt_{job.id}")
        if stop_at == "payment-receipt":
            die(stop_at, box)

    runtime.run_due()
    reconcile(solvent, initiator="crash")
    settlement.settle_paid_jobs(solvent, initiator="crash")
    print("DONE", [j.state.value for j in solvent.orchestrator.jobs()])
    return 0


_clock = [0.0]


def _materials(solvent, box: pathlib.Path) -> dict:
    """The client's file and form answers, from the real generators."""
    import random

    from simulation.customers import Population, a_customer

    population = Population(seed="crash", length_days=1)
    customer = a_customer(random.Random("crash"), index=1, arrives_on=0,
                          door="relationship")
    customer.wants = "csv-cleanup"
    customer.customer_id = "northstar"
    return population.materials_for(customer, workroot=box / "materials")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
