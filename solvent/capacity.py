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
from .types import JOBS_PER_AGENT_CEILING, JOBS_PER_AGENT_DEFAULT

#: What a reservation is for. Each is work owed to somebody.
EXECUTE = "EXECUTE"
VERIFY = "VERIFY"
REVISE = "REVISE"
#: A bid that has been sent and not yet answered. Not yet an obligation,
#: and not nothing either: if it wins, it becomes one without asking.
BID = "BID"

KINDS = (EXECUTE, VERIFY, REVISE, BID)

HELD = "HELD"
#: A worker has picked this up and is doing it. Still owed, and now
#: also occupying one of the five workers. Committed and executing are
#: different things: a pipeline holds four jobs and a worker runs one
#: of them, so counting them together would make a full pipeline look
#: like four busy workers.
RUNNING = "RUNNING"
CONSUMED = "CONSUMED"
RELEASED = "RELEASED"

#: Still counting against the budget.
OUTSTANDING = (HELD, RUNNING)


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

#: Readings that describe the machine rather than the order book.
#: Worker concurrency is reduced for these and for nothing else: a
#: shop that is busy should not run fewer workers, and a host that is
#: running out of disk should. Module level rather than a class
#: attribute, because an invariant test enumerates the gate's public
#: surface and a constant is not a decision it makes.
RESOURCE_SIGNALS = ("disk_free_bytes", "memory_used_percent")


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
    #: How many jobs may be executing at once across the whole shop.
    #: One worker per skill is the owner's model, so this is a count of
    #: workers and is NOT bounded by the per-pipeline job ceiling: five
    #: workers each holding four jobs is twenty committed and five
    #: running. Zero means "the per-pipeline limit governs", never
    #: "unlimited".
    max_execution_workers: int = 0
    max_verification_workers: int = 0
    #: Jobs a skill's pipeline aims to hold, and the depth at which it
    #: asks for more. Both are the owner's numbers.
    pipeline_target: int = 0
    replenish_at: int = 0
    #: Committed jobs across *all* pipelines together. Five pipelines of
    #: four is twenty, and without this the per-pipeline limit is the
    #: only one there is: five skills could each fill to four and the
    #: shop would hold twenty obligations having never checked whether
    #: twenty is a number it can deliver. Zero means "the per-pipeline
    #: limits govern", never "unlimited".
    max_committed_jobs: int = 0
    #: The furthest ahead DeskPilot will promise. A deadline beyond this
    #: is not refused for being far away -- it is that work nearer than
    #: this has to fit in front of it, and this is the window the fit is
    #: checked over. Zero means deadline capacity is not checked, which
    #: is a setting rather than a default to rely on.
    deadline_horizon_hours: float = 0.0
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
        """Workers allowed for a kind of work, across the whole shop.

        Unset falls back to the per-pipeline job limit rather than to
        no limit. "Zero means unlimited" is the shape of several bugs
        this system has already had; it is not repeated here.
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
        if self.max_concurrent_jobs > JOBS_PER_AGENT_CEILING:
            return (f"a limit of {self.max_concurrent_jobs} jobs at once is "
                    f"above the {JOBS_PER_AGENT_CEILING} this work agent "
                    f"may hold; the default is {JOBS_PER_AGENT_DEFAULT} and "
                    "four is the most that can be configured")
        if self.pipeline_target > self.max_concurrent_jobs:
            return (f"a pipeline target of {self.pipeline_target} is above "
                    f"the {self.max_concurrent_jobs} jobs a skill may hold; "
                    "a target nothing can reach is not a target")
        if (self.max_committed_jobs > 0
                and self.max_committed_jobs < self.max_concurrent_jobs):
            return (f"a global ceiling of {self.max_committed_jobs} job(s) "
                    f"is below the {self.max_concurrent_jobs} a single "
                    "pipeline may hold, so one skill could never fill "
                    "even though the limits say it may")
        if (self.max_committed_jobs > 0
                and self.max_committed_jobs < self.max_execution_workers):
            return (f"a global ceiling of {self.max_committed_jobs} job(s) "
                    f"with {self.max_execution_workers} worker(s) leaves a "
                    "worker that can never be given anything to do")
        if self.replenish_at >= self.pipeline_target > 0:
            return (f"replenishing at {self.replenish_at} with a target of "
                    f"{self.pipeline_target} would top up a pipeline that "
                    "is already full, which is a loop rather than a rule")
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
    #: Which skill's pipeline this belongs to. Empty for work that
    #: names no skill, which still counts against the seconds owed.
    capability: str = ""


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
            max_concurrent_jobs=int(
                get("max_concurrent_jobs", JOBS_PER_AGENT_DEFAULT)
                or JOBS_PER_AGENT_DEFAULT),
            max_open_bids=int(get("max_open_bids", 0) or 0),
            cpu_share=float(get("cpu_share", 0) or 0),
            min_free_disk_bytes=int(get("min_free_disk_bytes", 0) or 0),
            revision_headroom_fraction=max(
                0.0, float(get("revision_headroom_fraction", 0) or 0)),
            max_execution_workers=int(get("max_execution_workers", 0) or 0),
            max_verification_workers=int(
                get("max_verification_workers", 0) or 0),
            pipeline_target=int(get("pipeline_target", 0) or 0),
            replenish_at=int(get("replenish_at", 0) or 0),
            max_committed_jobs=int(get("max_committed_jobs", 0) or 0),
            deadline_horizon_hours=float(
                get("deadline_horizon_hours", 0) or 0),
            max_memory_used_percent=float(
                get("max_memory_used_percent", 0) or 0))

    # ------------------------------------------------------- what is owed

    def outstanding(self, kind: str = "") -> list[Reservation]:
        placeholders = ",".join("?" for _ in OUTSTANDING)
        sql = (f"SELECT * FROM capacity_reservations WHERE state IN "
               f"({placeholders})" + (" AND kind = ?" if kind else ""))
        params = tuple(OUTSTANDING) + ((kind,) if kind else ())
        return [Reservation(r["id"], r["job_id"], r["kind"],
                            float(r["units"]), r["state"], r["why"],
                            (r["capability"] if "capability" in r.keys()
                             else "") or "")
                for r in self._db.query(sql, params)]

    def owed_seconds(self) -> float:
        """Seconds of work already promised. Bids included.

        A bid counts. It is contingent, but it is contingent on somebody
        else's decision, not DeskPilot's -- the moment a client accepts,
        the obligation exists whether there was room for it or not.
        """
        return sum(r.units for r in self.outstanding())

    def jobs_in_flight(self, capability: str = "") -> int:
        """Distinct jobs owed. Per skill when one is named.

        The owner's model is five skill pipelines, not one queue, so
        "four jobs" is four jobs in *a* pipeline. Counting them
        globally would let one busy skill starve the other four, and
        would make a five-worker shop look full at four jobs.
        """
        return len({r.job_id for r in self.outstanding()
                    if r.job_id and r.kind != BID
                    and (not capability or r.capability == capability)})

    def executing(self, capability: str = "") -> int:
        """Jobs a worker has actually started, globally or per skill."""
        rows = self._db.query(
            "SELECT job_id, capability FROM capacity_reservations "
            "WHERE state = ? AND kind = ?", (RUNNING, EXECUTE))
        return len({r["job_id"] for r in rows
                    if r["job_id"]
                    and (not capability
                         or (r["capability"] or "") == capability)})

    def pipelines(self) -> dict:
        """Each skill's queue depth against its target. Decides nothing.

        A skill is due replenishment when its pipeline falls to the
        owner's threshold. This reports it; the Finder acts on it, and
        the capacity gate still decides whether anything may be taken.
        """
        budget = self.budget()
        out = {}
        for capability in sorted(self._skills()):
            depth = self.jobs_in_flight(capability)
            out[capability] = {
                "committed": depth,
                "executing": self.executing(capability),
                "target": budget.pipeline_target,
                "replenish_at": budget.replenish_at,
                "needs_replenishment": depth <= budget.replenish_at,
                "room": max(0, budget.max_concurrent_jobs - depth),
            }
        return out

    def _skills(self) -> tuple:
        """The skills with pipelines, from policy -- never inferred.

        Read from the owner's own list rather than from whatever
        happens to be in the reservation table, so a skill with an
        empty pipeline still appears and can be replenished.
        """
        named = self._policy.get("capacity", "skills", default=()) or ()
        held = {r.capability for r in self.outstanding() if r.capability}
        return tuple(sorted(set(named) | held))

    def open_bids(self) -> int:
        return len(self.outstanding(BID))

    def committed_ceiling(self) -> int:
        """The global cap on committed jobs, however it is reached.

        An owner who set only the per-pipeline numbers still has a
        global ceiling -- it is just implied rather than stated, and
        the implied one is what the five pipelines come to. Returning
        it rather than zero means the exposure check below always has
        a number to check against.
        """
        budget = self.budget()
        if budget.max_committed_jobs > 0:
            return budget.max_committed_jobs
        skills = len(self._skills())
        if skills and budget.max_concurrent_jobs > 0:
            return skills * budget.max_concurrent_jobs
        return budget.max_concurrent_jobs

    def exposure(self) -> dict:
        """Everything owed or possibly owed, by what kind of thing it is.

        A discovered opportunity is not a job commitment. An outstanding
        bid is a potential obligation. An awarded contract is a real
        one. Counting them in one number is how a shop talks itself
        into believing it has room: four bids out and sixteen jobs
        committed is twenty obligations if the week goes well, and
        "sixteen committed" is the comfortable half of that sentence.

        ``worst_case_committed`` is the number that matters for
        admission, because every outstanding bid could be accepted
        tomorrow and none of them will ask first.
        """
        committed = self.jobs_in_flight()
        bids = self.open_bids()
        ceiling = self.committed_ceiling()
        return {
            "committed_jobs": committed,
            "outstanding_bids": bids,
            "worst_case_committed": committed + bids,
            "committed_ceiling": ceiling,
            "worst_case_headroom": max(0, ceiling - committed - bids),
            "executing": self.executing(),
            "owed_seconds": round(self.owed_seconds(), 1),
        }

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
                 job_id: str = "", capability: str = "") -> Decision:
        """Is there room for this much more work? Decides nothing else.

        ``capability`` names the skill pipeline this belongs to. The
        job limit is per pipeline: five skills each holding four jobs
        is the owner's model, and counting them in one pile would make
        a five-worker shop read as full at four.
        """
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
            # A bid limit on its own bounds how many offers are out,
            # not whether they can be honoured. Sixteen jobs committed
            # against a ceiling of twenty leaves room for four more
            # bids by that count and none at all by this one, because
            # four acceptances would make twenty-one obligations. The
            # offer is refused rather than the commitment broken: the
            # client who accepts is not the one who should discover
            # the shop was full.
            ceiling = self.committed_ceiling()
            worst_case = self.jobs_in_flight() + self.open_bids() + 1
            if ceiling > 0 and worst_case > ceiling:
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{self.jobs_in_flight()} job(s) are committed and "
                    f"{self.open_bids()} bid(s) are out; one more offer "
                    f"would be {worst_case} obligations if every one were "
                    f"accepted, against the {ceiling} this shop can hold",
                    owed, seconds, budget.max_owed_seconds)
        else:
            in_flight = self.jobs_in_flight(capability)
            fresh = job_id and job_id not in {
                r.job_id for r in self.outstanding() if r.kind != BID}
            ceiling = self.committed_ceiling()
            fresh_global = job_id and job_id not in {
                r.job_id for r in self.outstanding() if r.kind != BID}
            if (fresh_global and ceiling > 0
                    and self.jobs_in_flight() >= ceiling):
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{self.jobs_in_flight()} job(s) are committed across "
                    f"every pipeline, which is the {ceiling} this shop "
                    "holds; a free slot in one pipeline is not free "
                    "capacity in the shop",
                    owed, seconds, budget.max_owed_seconds,
                    self.jobs_in_flight())
            if fresh and in_flight >= budget.max_concurrent_jobs:
                where = (f"{capability}'s pipeline" if capability
                         else "the unattributed pipeline")
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{in_flight} job(s) are already in {where}, which is "
                    f"the {budget.max_concurrent_jobs} a skill may hold",
                    owed, seconds, budget.max_owed_seconds, in_flight)

        # Verification workers are still bounded here, because nothing
        # "starts" a check the way a worker picks up a job: the
        # reservation is the occupancy. Execution moved to start().
        if kind == VERIFY:
            allowed = budget.workers(VERIFY)
            busy = len(self.outstanding(VERIFY))
            if busy >= allowed:
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{busy} verification worker(s) are busy, which is the "
                    f"{allowed} the owner allows",
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
                why: str = "", capability: str = "") -> Decision:
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

        # Checking the budget and taking the room must be one step. Two
        # bids arriving together both read the same headroom, both found
        # it sufficient and both took it; the gate said no to neither
        # and the ledger finished 60s past the budget.
        with self._db.transaction():
            existing = self._db.query_one(
                "SELECT * FROM capacity_reservations WHERE "
                "idempotency_key = ?", (idempotency_key,))
            if existing is not None:
                return Decision(
                    False, Refusal.DUPLICATE,
                    f"this commitment is already reserved as "
                    f"{existing['id']}; a repeated event is not a second "
                    "piece of work",
                    reservation_id=existing["id"])

            decision = self.room_for(seconds, kind=kind, job_id=job_id,
                                     capability=capability)
            # The ledger records what room_for judged, not what the
            # caller asked for. Judging the inflated figure and then
            # holding the bare one would give the headroom away the
            # moment it was taken.
            seconds = self.committed_seconds(seconds, kind)
            reservation_id = new_id("cap")
            if decision.allowed:
                self._db.execute(
                    "INSERT INTO capacity_reservations(id,ts,job_id,kind,"
                    "units,state,why,idempotency_key,capability) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (reservation_id, now(), job_id, kind, float(seconds),
                     HELD, why[:300], idempotency_key, capability))

        # Audit after the transaction: the audit log is a different
        # authority on the same connection, and committing its write
        # would end this one early.
        if not decision.allowed:
            self._audit.record(
                event="capacity.refused", authority="capacity",
                initiator="capacity", why=decision.why,
                decision=decision.refusal.value, job_id=job_id or None)
            return decision

        self._audit.record(
            event="capacity.reserved", authority="capacity",
            initiator="capacity", why=why or f"{kind} {seconds:.0f}s",
            decision=kind, job_id=job_id or None, input_ref=reservation_id,
            result=f"{seconds:.0f}s")
        return Decision(True, Refusal.NONE, decision.why, decision.owed_seconds,
                        seconds, decision.budget_seconds,
                        decision.in_flight, reservation_id)

    def admit(self, job_id: str, *, execute_seconds: float,
              verify_seconds: float = 0.0, revise_seconds: float = 0.0,
              idempotency_key: str = "", why: str = "",
              capability: str = "") -> Decision:
        """Take on a whole job, or none of it.

        Reserving only the execution is what caused the worst failure
        found in load simulation. A job was admitted with room for its
        execution, ran, and then could not get room to be verified --
        and because its execution reservation is held until delivery,
        nothing freed up and the whole shop stopped. Four workers sat
        holding room for work they were not allowed to finish.

        So admission buys the entire obligation in one transaction:
        execution, the verification that is not optional, and the
        revision the client is entitled to ask for. If the three
        together do not fit, the job is not taken. Afterwards every
        later step already owns its room and cannot be refused, which
        is the only way "accepted" can mean "will be delivered".
        """
        if not idempotency_key:
            raise FailClosed(
                "admitting a job needs an idempotency key; without one a "
                "repeated marketplace event becomes a second obligation")
        execute_seconds = float(execute_seconds)
        verify_seconds = max(0.0, float(verify_seconds))
        revise_seconds = max(0.0, float(revise_seconds))

        parts = [(EXECUTE, execute_seconds)]
        if verify_seconds:
            parts.append((VERIFY, verify_seconds))
        if revise_seconds:
            parts.append((REVISE, revise_seconds))

        with self._db.transaction():
            existing = self._db.query_one(
                "SELECT * FROM capacity_reservations WHERE "
                "idempotency_key = ?", (f"{idempotency_key}:EXECUTE",))
            if existing is not None:
                return Decision(
                    False, Refusal.DUPLICATE,
                    f"this job is already admitted as {existing['id']}; a "
                    "repeated event is not a second piece of work",
                    reservation_id=existing["id"])

            # Judged as one obligation. Asking three times in a row
            # would let the first two through and refuse the third,
            # which is the deadlock this method exists to prevent.
            total = sum(self.committed_seconds(s, kind)
                        for kind, s in parts)
            decision = self.room_for(total, kind=EXECUTE, job_id=job_id,
                                     capability=capability)
            if not decision.allowed:
                refusal = decision
            else:
                refusal = None
                ids = {}
                for kind, seconds in parts:
                    held = self.committed_seconds(seconds, kind)
                    reservation_id = new_id("cap")
                    ids[kind] = reservation_id
                    self._db.execute(
                        "INSERT INTO capacity_reservations(id,ts,job_id,"
                        "kind,units,state,why,idempotency_key,capability) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (reservation_id, now(), job_id, kind, float(held),
                         HELD, why[:300], f"{idempotency_key}:{kind}",
                         capability))

        if refusal is not None:
            self._audit.record(
                event="capacity.refused", authority="capacity",
                initiator="capacity", why=refusal.why,
                decision=refusal.refusal.value, job_id=job_id or None)
            return refusal

        self._audit.record(
            event="capacity.admitted", authority="capacity",
            initiator="capacity", why=why or f"job {job_id}",
            decision=EXECUTE, job_id=job_id or None,
            input_ref=ids[EXECUTE], result=f"{total:.0f}s")
        return Decision(True, Refusal.NONE,
                        f"admitted {total:.0f}s of obligation for {job_id}: "
                        + ", ".join(f"{k.lower()} {s:.0f}s" for k, s in parts),
                        decision.owed_seconds, total, decision.budget_seconds,
                        decision.in_flight, ids[EXECUTE])

    def reservation_for(self, job_id: str, kind: str) -> str:
        """The room already held for this step, if the job was admitted.

        Any outstanding state, not only HELD. Once a worker starts a
        job its reservation is RUNNING, and a lookup that only knew
        about HELD could not find the thing it was about to finish --
        so consuming a job that had actually been started raised
        "unknown reservation".
        """
        placeholders = ",".join("?" for _ in OUTSTANDING)
        row = self._db.query_one(
            "SELECT id FROM capacity_reservations WHERE job_id = ? AND "
            f"kind = ? AND state IN ({placeholders}) ORDER BY ts LIMIT 1",
            (job_id, kind) + tuple(OUTSTANDING))
        return row["id"] if row else ""

    def _finish(self, reservation_id: str, state: str, why: str) -> None:
        row = self._db.query_one(
            "SELECT * FROM capacity_reservations WHERE id = ?",
            (reservation_id,))
        if row is None:
            raise FailClosed(f"unknown reservation {reservation_id!r}")
        if row["state"] not in OUTSTANDING:
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

    def start(self, reservation_id: str, *, why: str = "worker started"
              ) -> Decision:
        """A worker picks up queued work. The other half of the model.

        Admission fills a pipeline; this occupies a worker. They are
        separate because the owner's shop is five skills each holding
        four jobs and running one: if admission occupied a worker, a
        full pipeline would read as four busy workers and the fifth
        skill would never get one.

        Refuses rather than queues when no worker is free, so the
        caller learns it is waiting instead of believing it started.
        """
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT * FROM capacity_reservations WHERE id = ?",
                (reservation_id,))
            if row is None:
                raise FailClosed(f"unknown reservation {reservation_id!r}")
            if row["kind"] != EXECUTE:
                raise FailClosed(
                    f"{row['kind']} work is not what a worker picks up; "
                    "only execution occupies one")
            if row["state"] == RUNNING:
                return Decision(True, Refusal.NONE, "already running",
                                reservation_id=reservation_id)
            if row["state"] != HELD:
                raise FailClosed(
                    f"reservation {reservation_id!r} is {row['state']} and "
                    "cannot be started")

            budget = self.budget()
            capability = (row["capability"] if "capability" in row.keys()
                          else "") or ""
            allowed, throttle = self.effective_workers(EXECUTE)
            busy = self.executing()
            if busy >= allowed:
                return Decision(
                    False, Refusal.NO_ROOM,
                    (f"{busy} worker(s) are already executing, which is the "
                     f"{allowed} currently allowed"
                     + (f" ({throttle})" if throttle
                        else " by the owner's limit")),
                    reservation_id=reservation_id)
            if capability and self.executing(capability):
                return Decision(
                    False, Refusal.NO_ROOM,
                    f"{capability}'s worker is already on a job, and a "
                    "worker takes one at a time",
                    reservation_id=reservation_id)
            self._db.execute(
                "UPDATE capacity_reservations SET state = ?, why = ? "
                "WHERE id = ?", (RUNNING, why[:300], reservation_id))

        self._audit.record(
            event="capacity.started", authority="capacity",
            initiator="capacity", why=why, decision=RUNNING,
            job_id=row["job_id"] or None, input_ref=reservation_id)
        return Decision(True, Refusal.NONE, why,
                        reservation_id=reservation_id)

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
        """Only where the host says. Silence is not a reading.

        A host that reports no memory at all is the ordinary case on
        Windows and is not a failure. A host that raises when asked is
        a different thing, and it refuses: a ceiling the owner set and
        nobody can check is not a ceiling that has been respected.
        """
        if budget.max_memory_used_percent <= 0:
            return ""
        if self._diagnostics is None:
            return self._unreadable("memory", "no diagnostics are wired")
        try:
            memory = self._diagnostics.host().get("memory") or {}
        except Exception as exc:
            return self._unreadable("memory", str(exc)[:120])
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

    @staticmethod
    def _unreadable(what: str, why: str) -> str:
        """A limit that cannot be checked has not been satisfied.

        The books-unreadable path already refuses; these two did not,
        so a host that threw on being asked read as "no problem" and
        opened the gate. Two ways of failing to check, two different
        answers, and the quieter one was the dangerous one.
        """
        return (f"the owner set a {what} limit and this host will not "
                f"say what the {what} is ({why}); a limit nobody can "
                "check is not a limit that has been respected")

    def _disk_problem(self, budget: Budget) -> str:
        if budget.min_free_disk_bytes <= 0:
            return ""
        if self._diagnostics is None:
            return self._unreadable("free disk", "no diagnostics are wired")
        try:
            free = int(self._diagnostics.host().get("disk_free_bytes", 0))
        except Exception as exc:
            return self._unreadable("free disk", str(exc)[:120])
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

    def effective_workers(self, kind: str) -> tuple:
        """How many workers may actually start, and why that number.

        The owner's five is an upper bound, not an instruction to run
        five resource-hungry processes on a box that also runs
        MetaTrader 5. This reduces the bound when the machine says so
        and leaves it alone when the machine says nothing it was asked
        about.

        Three rules, in order, each deterministic:

        1. A **measured** resource signal reading FULL drops the limit
           to one. Not to zero: zero cannot be told apart from halted,
           and halting is the owner's decision with its own command.
           One worker means work in progress still finishes.
        2. A measured resource signal reading TIGHT halves it, floored
           at one.
        3. A resource limit the owner set that this host **will not
           report** also drops it to one. An unreadable metric is not
           a healthy one, and the alternative -- carrying on at five
           because nobody could check -- is the failure this whole
           file exists to avoid.

        Nothing here stops a worker that is already running. It bounds
        what may *start*, so reducing concurrency preserves queued
        commitments rather than abandoning them; whether those
        commitments remain deliverable at the lower rate is a deadline
        question, and :meth:`deadline_problem` is where it is asked.
        """
        allowed = self.budget().workers(kind)
        if allowed <= 1:
            return allowed, ""
        worst, culprit = OK, ""
        # ``reading``, not ``signal``: an invariant test forbids the bare
        # identifier anywhere in this file, because that is what
        # ``import signal`` and ``signal.SIGTERM`` look like and nothing
        # here may ever send one to a process. MetaTrader 5 is on this
        # machine. A backpressure reading is not an OS signal, and the
        # guard is worth more than the nicer variable name.
        for reading in self.pressure()["signals"]:
            if reading["name"] not in RESOURCE_SIGNALS:
                continue
            state = reading["state"]
            if state == UNMEASURED and reading["limit"]:
                return 1, (f"{reading['name']} is a limit the owner set and "
                           "this host will not report it; one worker until "
                           "it can be read")
            if state == FULL:
                worst, culprit = FULL, reading["name"]
                break
            if state == TIGHT and worst != FULL:
                worst, culprit = TIGHT, reading["name"]
        if worst == FULL:
            return 1, f"{culprit} is at its limit; one worker until it clears"
        if worst == TIGHT:
            reduced = max(1, allowed // 2)
            if reduced < allowed:
                return reduced, (f"{culprit} is tight; {reduced} worker(s) "
                                 f"instead of {allowed}")
        return allowed, ""

    def deadline_problem(self, seconds: float, deadline_hours: float) -> str:
        """Whether this job's deadline survives the queue in front of it.

        Deadline capacity is not the same limit as job capacity and
        was not being checked as one. A shop with room for a job can
        still be unable to deliver it on time, because the work
        already owed has to come out of the same hours -- and the
        number of workers those hours are divided between is the
        *effective* count, not the configured one, so a throttled host
        makes deadlines harder rather than only slower.

        Returns the reason it does not fit, or empty when it does.
        """
        budget = self.budget()
        if not deadline_hours or budget.deadline_horizon_hours <= 0:
            return ""
        if deadline_hours > budget.deadline_horizon_hours:
            return ""
        workers, _ = self.effective_workers(EXECUTE)
        workers = max(1, workers)
        # Everything already owed, plus this, shared between the
        # workers that may actually run. The safety factor is the
        # owner's and is applied here rather than assumed elsewhere.
        ahead = self.owed_seconds() + float(seconds)
        # Read from policy, not from Budget. The Finder already reads
        # it from there and a second copy on Budget would be a second
        # answer to the same question, which is how two limits that
        # are supposed to be one drift apart.
        factor = max(1.0, float(self._policy.get(
            "capacity", "deadline_safety_factor", default=1.0) or 1.0))
        needed_hours = (ahead * factor) / (workers * 3600.0)
        if needed_hours > deadline_hours:
            return (f"{ahead:.0f}s of work is owed across {workers} "
                    f"worker(s), which is {needed_hours:.1f}h at the "
                    f"owner's {factor:g}x safety factor, and this job is "
                    f"due in {deadline_hours:.1f}h")
        return ""

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
            "exposure": self.exposure(),
            "effective_execution_workers": self.effective_workers(EXECUTE)[0],
            "throttled_because": self.effective_workers(EXECUTE)[1],
            "by_kind": {k: round(v, 1) for k, v in sorted(by_kind.items())},
            "revision_headroom_fraction": budget.revision_headroom_fraction,
            "unreconciled": self._unreconciled(),
            "has_obligations": self.has_obligations(),
        }
