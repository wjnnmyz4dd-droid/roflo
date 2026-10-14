"""How much work DeskPilot may owe at once.

Finding a hundred jobs is not being able to do a hundred jobs, and the
difference between those two sentences is where a service business
fails. Every accepted contract is a promise with a deadline attached;
accepting a second one does not make the first one later, it makes both
of them late. So there is one number that matters more than throughput:
how much is already owed.

This is a reservation ledger, not a second gate on everything. It
decides exactly one thing -- whether there is room for one more
obligation -- and it decides nothing else. It cannot approve a bid,
permit an external effect, price work or certify a skill. Refusing to
take on more work is a different act from authorising the work already
taken, and the Action Gate, the Governor and the owner's approval all
still stand where they stood.

**Capacity is time, not jobs.** A ten-second CSV tidy and a four-hour
report are not interchangeable units, and a limit counted in jobs would
let four hours of work through a gate meant for ten seconds of it. Every
reservation is denominated in seconds of expected work, and the expected
work comes from measurement -- see ``Finder.completion_estimate``.

**Three kinds of obligation, all of them real.** Executing a job is the
obvious one. Verifying it is not optional and not free. A revision the
client is entitled to ask for is work DeskPilot already promised. A
capacity model that counts only execution is a model that runs out of
room the first time somebody asks for a change.

**What the budget is a share of.** The Windows VPS this runs on also
runs MetaTrader 5, which is the owner's trading platform and is not
DeskPilot's to crowd out. The budget here is a configured share of a
machine, never an inference about the whole of it, and nothing in this
module reads, signals, or so much as names an MT5 process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .audit import new_id, now
from .errors import FailClosed

#: What a reservation is for. Each is work owed to somebody.
EXECUTE = "EXECUTE"
VERIFY = "VERIFY"
REVISE = "REVISE"
#: A bid that has been sent and not yet answered. Not yet an obligation,
#: and not nothing either: if it wins, it becomes one without asking.
BID = "BID"

KINDS = (EXECUTE, VERIFY, REVISE, BID)

HELD = "HELD"
CONSUMED = "CONSUMED"
RELEASED = "RELEASED"

#: Still counting against the budget.
OUTSTANDING = (HELD,)

#: How a pressure signal reads. UNMEASURED is deliberately not OK: the
#: Windows VPS reports neither CPU nor memory through anything already
#: installed, and reporting silence as health is how a machine gets
#: loaded until the thing sharing it suffers.
OK = "OK"
TIGHT = "TIGHT"
FULL = "FULL"
UNMEASURED = "UNMEASURED"
#: Measured, but with no threshold to be measured against. Not OK:
#: nothing checked it.
INFO = "INFO"
#: Above this fraction of a limit, a signal is tight rather than fine.
TIGHT_AT = 0.8

#: Which direction is bad. Free disk is the one signal here where a
#: high number is health, and reading it as a ceiling says FULL on a
#: healthy machine and OK on a dying one -- which is what the first
#: version of this did.
CEILING = "CEILING"
FLOOR = "FLOOR"


class Refusal(Enum):
    """Why capacity said no. Each is a different problem to fix."""

    NONE = "NONE"
    NO_ROOM = "NO_ROOM"
    #: Something was sent and its outcome is unknown. Committing more
    #: while the books cannot be read is how one unknown becomes three.
    UNRECONCILED = "UNRECONCILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    DUPLICATE = "DUPLICATE"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class Budget:
    """What the owner has allowed DeskPilot to owe at one time.

    Defaults are the smallest thing that still works, and zero is a
    legitimate setting meaning "take nothing on". Every field is a
    number the owner sets; none is inferred from the machine, because
    inferring a budget from available hardware is how a shared VPS
    becomes an unshared one.
    """

    #: Total seconds of expected work that may be owed at once.
    max_owed_seconds: float = 0.0
    #: Jobs that may be in flight at once, whatever their size.
    max_concurrent_jobs: int = 0
    #: Bids outstanding at once. A bid is a contingent obligation.
    max_open_bids: int = 0
    #: How much of the machine DeskPilot may use. A share, never all.
    cpu_share: float = 0.0
    #: Refuse new work below this much free disk.
    min_free_disk_bytes: int = 0
    #: Workers of each kind. Zero means "the concurrent job limit
    #: governs", never "unlimited" -- that limit already fails closed.
    max_execution_workers: int = 0
    max_verification_workers: int = 0
    #: Refuse new work above this much memory in use. Only consulted
    #: where the host reports memory at all; a Windows VPS does not,
    #: and an unmeasured signal is reported as unmeasured, not as fine.
    max_memory_used_percent: float = 0.0
    #: Extra room held alongside each accepted job for the verification
    #: and revisions it will need. A job is not finished when execution
    #: finishes; it is finished when the customer stops asking. Zero is
    #: allowed and means the owner accepts that a revision arriving will
    #: compete with new work for room.
    revision_headroom_fraction: float = 0.0

    def workers(self, kind: str) -> int:
        """Workers allowed for a kind of work.

        Unset falls back to the concurrent job limit rather than to no
        limit. "Zero means unlimited" is the shape of several bugs this
        system has already had; it is not repeated here.
        """
        chosen = (self.max_execution_workers if kind == EXECUTE
                  else self.max_verification_workers if kind == VERIFY
                  else 0)
        return chosen if chosen > 0 else self.max_concurrent_jobs

    def configured(self) -> bool:
        return (self.max_owed_seconds > 0 and self.max_concurrent_jobs > 0)

    def problem(self) -> str:
        if self.max_owed_seconds <= 0:
            return ("no capacity budget is set, so there is no number to "
                    "refuse against; DeskPilot takes nothing on until the "
                    "owner says how much it may owe")
        if self.max_concurrent_jobs <= 0:
            return "no concurrent job limit is set"
        if not 0 < self.cpu_share <= 1:
            return ("the CPU share must be a fraction above zero and at "
                    "most one; this machine also runs MetaTrader 5 and the "
                    "whole of it is not DeskPilot's to take")
        return ""


@dataclass(frozen=True, slots=True)
class Reservation:
    id: str
    job_id: str
    kind: str
    units: float
    state: str
    why: str


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether there is room, and the arithmetic behind the answer."""

    allowed: bool
    refusal: Refusal
    why: str
    owed_seconds: float = 0.0
    requested_seconds: float = 0.0
    budget_seconds: float = 0.0
    in_flight: int = 0
    reservation_id: str = ""

    def summary(self) -> str:
        return (f"{'room' if self.allowed else self.refusal.value}: "
                f"{self.owed_seconds:.0f}s owed + {self.requested_seconds:.0f}s "
                f"requested against {self.budget_seconds:.0f}s")


class Capacity:
    """The reservation ledger. Reads the budget; owns the holds."""

    def __init__(self, store, policy, audit, *, applications=None,
                 diagnostics=None):
        self._db = store.for_authority("capacity")
        self._store = store
        self._policy = policy
        self._audit = audit
        #: Consulted for unreconciled outcomes. Optional so capacity can
        #: be constructed before the submission authority exists.
        self._applications = applications
        self._diagnostics = diagnostics

    # ------------------------------------------------------------ budget

    def budget(self) -> Budget:
        get = lambda k, d: self._policy.get("capacity", k, default=d)  # noqa: E731
        return Budget(
            max_owed_seconds=float(get("max_owed_seconds", 0) or 0),
            max_concurrent_jobs=int(get("max_concurrent_jobs", 0) or 0),
            max_open_bids=int(get("max_open_bids", 0) or 0),
            cpu_share=float(get("cpu_share", 0) or 0),
            min_free_disk_bytes=int(get("min_free_disk_bytes", 0) or 0),
            revision_headroom_fraction=max(
                0.0, float(get("revision_headroom_fraction", 0) or 0)),
            max_execution_workers=int(get("max_execution_workers", 0) or 0),
            max_verification_workers=int(
                get("max_verification_workers", 0) or 0),
            max_memory_used_percent=float(
                get("max_memory_used_percent", 0) or 0))

    # ------------------------------------------------------- what is owed

    def outstanding(self, kind: str = "") -> list[Reservation]:
        sql = ("SELECT * FROM capacity_reservations WHERE state = ?"
               + (" AND kind = ?" if kind else ""))
        params = (HELD,) + ((kind,) if kind else ())
        return [Reservation(r["id"], r["job_id"], r["kind"], float(r["units"]),
                            r["state"], r["why"])
                for r in self._db.query(sql, params)]

    def owed_seconds(self) -> float:
        """Seconds of work already promised. Bids included.

        A bid counts. It is contingent, but it is contingent on somebody
        else's decision, not DeskPilot's -- the moment a client accepts,
        the obligation exists whether there was room for it or not.
        """
        return sum(r.units for r in self.outstanding())

    def jobs_in_flight(self) -> int:
        return len({r.job_id for r in self.outstanding()
                    if r.job_id and r.kind != BID})

    def open_bids(self) -> int:
        return len(self.outstanding(BID))

    def has_obligations(self) -> bool:
        """Is anything owed? Discovery waits while this is true."""
        return any(r.kind != BID for r in self.outstanding())

    # -------------------------------------------------------- the decision

    def committed_seconds(self, seconds: float, kind: str,
                          budget: "Budget | None" = None) -> float:
        """What accepting this really costs, not what it optimistically costs.

        Taking a job on is not buying its execution; it is buying its
        execution plus the verification and the revisions that follow
        from it. Reserving only the execution is how a queue that looks
        half full turns out to be full.
        """
        budget = budget or self.budget()
        if kind != EXECUTE:
            return float(seconds)
        return float(seconds) * (1.0 + budget.revision_headroom_fraction)

    def room_for(self, seconds: float, *, kind: str = EXECUTE,
                 job_id: str = "") -> Decision:
        """Is there room for this much more work? Decides nothing else."""
        if kind not in KINDS:
            raise FailClosed(f"{kind!r} is not a kind of obligation")
        seconds = float(seconds)
        if seconds < 0:
            raise FailClosed("a reservation cannot be for negative work")

        budget = self.budget()
        problem = budget.problem()
        if problem:
            return Decision(False, Refusal.NOT_CONFIGURED, problem)
        seconds = self.committed_seconds(seconds, kind, budget)

        if bool(self._policy.get("capacity", "halted", default=False)):
            return Decision(False, Refusal.HALTED,
                            "the owner has halted new commitments")

        unresolved = self._unreconciled()
        if unresolved:
            return Decision(
                False, Refusal.UNRECONCILED,
                f"{unresolved} attempt(s) have an outcome nobody has "
                "established; committing further while the books cannot be "
                "read turns one unknown into several")

        owed = self.owed_seconds()
        if owed + seconds > budget.max_owed_seconds:
            return Decision(
                False, Refusal.NO_ROOM,
                f"{owed:.0f}s is already owed and this needs {seconds:.0f}s "
                f"more, which is past the {budget.max_owed_seconds:.0f}s the "
                "owner allows",
                owed, seconds, budget.max_owed_seconds, self.jobs_in_flight())

        if kind == BID:
            if self.open_bids() >= budget.max_open_bids:
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{self.open_bids()} bid(s) are already outstanding, "
                    f"which is the limit; every one of them could be "
                    "accepted tomorrow",
                    owed, seconds, budget.max_owed_seconds)
        else:
            in_flight = self.jobs_in_flight()
            fresh = job_id and job_id not in {
                r.job_id for r in self.outstanding() if r.kind != BID}
            if fresh and in_flight >= budget.max_concurrent_jobs:
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{in_flight} job(s) are already in flight, which is the "
                    "limit the owner set",
                    owed, seconds, budget.max_owed_seconds, in_flight)

        if kind in (EXECUTE, VERIFY):
            allowed_workers = budget.workers(kind)
            busy = len(self.outstanding(kind))
            if busy >= allowed_workers:
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{busy} {kind.lower()} worker(s) are busy, which is "
                    f"the {allowed_workers} the owner allows",
                    owed, seconds, budget.max_owed_seconds,
                    self.jobs_in_flight())

        memory = self._memory_problem(budget)
        if memory:
            return Decision(False, Refusal.NO_ROOM, memory,
                            owed, seconds, budget.max_owed_seconds)

        disk = self._disk_problem(budget)
        if disk:
            return Decision(False, Refusal.NO_ROOM, disk,
                            owed, seconds, budget.max_owed_seconds)

        return Decision(True, Refusal.NONE,
                        f"{owed:.0f}s owed, {seconds:.0f}s requested, "
                        f"{budget.max_owed_seconds:.0f}s allowed",
                        owed, seconds, budget.max_owed_seconds,
                        self.jobs_in_flight())

    def reserve(self, seconds: float, *, kind: str = EXECUTE,
                job_id: str = "", idempotency_key: str = "",
                why: str = "") -> Decision:
        """Take the room, or explain why there is none.

        ``idempotency_key`` is required and is the whole defence against
        a marketplace delivering the same acceptance twice. The database
        refuses the second insert; this turns that refusal into the
        existing reservation rather than a new one, because the right
        answer to "accept job X" arriving twice is one obligation.
        """
        if not idempotency_key:
            raise FailClosed(
                "a reservation needs an idempotency key; without one a "
                "repeated marketplace event becomes a second obligation")

        existing = self._db.query_one(
            "SELECT * FROM capacity_reservations WHERE idempotency_key = ?",
            (idempotency_key,))
        if existing is not None:
            return Decision(
                False, Refusal.DUPLICATE,
                f"this commitment is already reserved as {existing['id']}; a "
                "repeated event is not a second piece of work",
                reservation_id=existing["id"])

        decision = self.room_for(seconds, kind=kind, job_id=job_id)
        # The ledger records what room_for judged, not what the caller
        # asked for. Judging the inflated figure and then holding the
        # bare one would give the headroom away the moment it was taken.
        seconds = self.committed_seconds(seconds, kind)
        if not decision.allowed:
            self._audit.record(
                event="capacity.refused", authority="capacity",
                initiator="capacity", why=decision.why,
                decision=decision.refusal.value, job_id=job_id or None)
            return decision

        reservation_id = new_id("cap")
        self._db.execute(
            "INSERT INTO capacity_reservations(id,ts,job_id,kind,units,state,"
            "why,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",
            (reservation_id, now(), job_id, kind, float(seconds), HELD,
             why[:300], idempotency_key))
        self._db.commit()
        self._audit.record(
            event="capacity.reserved", authority="capacity",
            initiator="capacity", why=why or f"{kind} {seconds:.0f}s",
            decision=kind, job_id=job_id or None, input_ref=reservation_id,
            result=f"{seconds:.0f}s")
        return Decision(True, Refusal.NONE, decision.why, decision.owed_seconds,
                        seconds, decision.budget_seconds,
                        decision.in_flight, reservation_id)

    def _finish(self, reservation_id: str, state: str, why: str) -> None:
        row = self._db.query_one(
            "SELECT * FROM capacity_reservations WHERE id = ?",
            (reservation_id,))
        if row is None:
            raise FailClosed(f"unknown reservation {reservation_id!r}")
        if row["state"] != HELD:
            # Already finished. Releasing twice would hand back room that
            # was handed back once already, which is how a budget leaks.
            return
        self._db.execute(
            "UPDATE capacity_reservations SET state = ?, released_at = ?, "
            "why = ? WHERE id = ?",
            (state, now(), why[:300], reservation_id))
        self._db.commit()
        self._audit.record(
            event=f"capacity.{state.lower()}", authority="capacity",
            initiator="capacity", why=why, decision=state,
            job_id=row["job_id"] or None, input_ref=reservation_id)

    def consume(self, reservation_id: str, why: str = "work done") -> None:
        """The work happened. The room is given back."""
        self._finish(reservation_id, CONSUMED, why)

    def release(self, reservation_id: str, why: str = "no longer owed") -> None:
        """The obligation went away -- a bid lost, a job cancelled."""
        self._finish(reservation_id, RELEASED, why)

    # ------------------------------------------------------------- limits

    def _unreconciled(self) -> int:
        if self._applications is None:
            return 0
        try:
            return len(self._applications.unresolved())
        except Exception:
            # A books-unreadable failure is itself a reason to stop, not
            # a reason to carry on as though the books were clean.
            return 1

    def _memory_problem(self, budget: Budget) -> str:
        """Only where the host says. Silence is not a reading."""
        if budget.max_memory_used_percent <= 0 or self._diagnostics is None:
            return ""
        try:
            memory = self._diagnostics.host().get("memory") or {}
        except Exception:
            return ""
        used = memory.get("used_percent")
        if used is None:
            return ""
        if float(used) >= budget.max_memory_used_percent:
            return (f"{float(used):.0f}% of memory is in use, at or above "
                    f"the {budget.max_memory_used_percent:.0f}% ceiling the "
                    "owner set for a machine that also runs MetaTrader 5")
        return ""

    def _host(self) -> dict:
        if self._diagnostics is None:
            return {}
        try:
            return self._diagnostics.host() or {}
        except Exception:
            return {}

    def _disk_problem(self, budget: Budget) -> str:
        if budget.min_free_disk_bytes <= 0 or self._diagnostics is None:
            return ""
        try:
            free = int(self._diagnostics.host().get("disk_free_bytes", 0))
        except Exception:
            return ""
        if free and free < budget.min_free_disk_bytes:
            return (f"{free / 1e9:.1f}GB of disk is free, below the "
                    f"{budget.min_free_disk_bytes / 1e9:.1f}GB the owner "
                    "requires before taking on more work")
        return ""

    # ---------------------------------------------------------- pressure

    @staticmethod
    def _signal(name: str, value, limit, *, note: str = "",
                measured: bool = True, mode: str = CEILING) -> dict:
        """One reading, with its state derived rather than asserted."""
        if not measured or value is None:
            return {"name": name, "state": UNMEASURED, "measured": False,
                    "value": None, "limit": limit, "utilisation": None,
                    "mode": mode, "note": note}
        if not limit:
            return {"name": name, "state": INFO, "measured": True,
                    "value": value, "limit": limit, "utilisation": None,
                    "mode": mode, "note": note}
        if mode == FLOOR:
            # Pressure rises as the value falls towards the floor.
            used = round(float(limit) / float(value), 3) if value else 1.0
        else:
            used = round(float(value) / float(limit), 3)
        state = FULL if used >= 1.0 else TIGHT if used >= TIGHT_AT else OK
        return {"name": name, "state": state, "measured": True,
                "value": value, "limit": limit, "utilisation": used,
                "mode": mode, "note": note}

    def pressure(self) -> dict:
        """Every backpressure signal, each labelled measured or not.

        The honest half of this is short. On the Windows VPS this system
        is built for, nothing already installed reports CPU or memory:
        ``_memory()`` reads /proc/meminfo and the load average comes from
        ``os.getloadavg()``, neither of which exists there. So CPU is not
        throttled by sampling it -- it is honoured by running no more
        workers than the owner allowed. The share in the budget is an
        allocation the owner declares, not a measurement DeskPilot takes,
        and this method says so rather than printing a comfortable zero.
        """
        budget = self.budget()
        host = self._host()
        memory = host.get("memory") or {}
        signals = [
            self._signal("owed_seconds", round(self.owed_seconds(), 1),
                         budget.max_owed_seconds,
                         note="work promised and not yet delivered"),
            self._signal("jobs_in_flight", self.jobs_in_flight(),
                         budget.max_concurrent_jobs),
            self._signal("open_bids", self.open_bids(), budget.max_open_bids,
                         note="contingent obligations"),
            self._signal("execution_workers", len(self.outstanding(EXECUTE)),
                         budget.workers(EXECUTE)),
            self._signal("verification_workers", len(self.outstanding(VERIFY)),
                         budget.workers(VERIFY)),
            self._signal(
                "revisions_open", len(self.outstanding(REVISE)), 0,
                note="unbounded by design: a customer may always ask again"),
            self._signal(
                "disk_free_bytes", host.get("disk_free_bytes"),
                budget.min_free_disk_bytes,
                measured="disk_free_bytes" in host, mode=FLOOR,
                note="free disk must stay above the floor, so the signal "
                     "is healthy when it is high"),
            self._signal(
                "memory_used_percent", memory.get("used_percent"),
                budget.max_memory_used_percent,
                measured=bool(memory),
                note="not reported by a Windows host through anything "
                     "already installed"),
            self._signal(
                "cpu", None, None, measured=False,
                note="never sampled; the owner's share is honoured by the "
                     "worker limits above, not by measuring the machine "
                     "MetaTrader 5 is also using"),
            self._signal("database_bytes", host.get("database_bytes"), 0,
                         measured="database_bytes" in host,
                         note="growth signal only; no ceiling is set"),
            self._signal("unreconciled", self._unreconciled(), 1,
                         note="any at all stops new commitments"),
        ]
        states = [s["state"] for s in signals]
        overall = (FULL if FULL in states
                   else TIGHT if TIGHT in states else OK)
        return {
            "state": overall,
            "accepting": self.room_for(0.0).allowed,
            "signals": signals,
            "unmeasured": [s["name"] for s in signals if not s["measured"]],
            "relief": self._relief(signals),
        }

    @staticmethod
    def _relief(signals: list) -> list:
        """What to shed, in the order it should be shed.

        Discovery first, because a job nobody has been promised costs
        nothing to not find. Accepted work is last: shedding that is
        breaking a promise, which is the thing all of this exists to
        avoid.
        """
        pressed = {s["name"] for s in signals
                   if s["state"] in (TIGHT, FULL)}
        if not pressed:
            return []
        relief = ["stop speculative discovery"]
        if pressed & {"open_bids", "owed_seconds"}:
            relief.append("send no further bids")
        if pressed & {"owed_seconds", "jobs_in_flight", "execution_workers"}:
            relief.append("accept no further jobs")
        relief.append("finish and verify what is already owed")
        return relief

    # -------------------------------------------------------------- report

    def report(self) -> dict:
        budget = self.budget()
        held = self.outstanding()
        by_kind: dict[str, float] = {}
        for reservation in held:
            by_kind[reservation.kind] = (by_kind.get(reservation.kind, 0.0)
                                         + reservation.units)
        owed = self.owed_seconds()
        return {
            "configured": budget.configured(),
            "problem": budget.problem(),
            "owed_seconds": round(owed, 1),
            "budget_seconds": budget.max_owed_seconds,
            "headroom_seconds": round(
                max(0.0, budget.max_owed_seconds - owed), 1),
            "utilisation": (round(owed / budget.max_owed_seconds, 3)
                            if budget.max_owed_seconds else None),
            "jobs_in_flight": self.jobs_in_flight(),
            "max_concurrent_jobs": budget.max_concurrent_jobs,
            "open_bids": self.open_bids(),
            "max_open_bids": budget.max_open_bids,
            "by_kind": {k: round(v, 1) for k, v in sorted(by_kind.items())},
            "revision_headroom_fraction": budget.revision_headroom_fraction,
            "unreconciled": self._unreconciled(),
            "has_obligations": self.has_obligations(),
        }
