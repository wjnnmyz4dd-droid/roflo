"""The control centre's status board. It translates; it does not judge.

Solvent already has an authority that decides whether each part of itself is
healthy: :mod:`solvent.diagnostics`, which runs twenty checks and records a
report. The control centre renders that report and deliberately does not
re-derive what any of it means, because a second copy of that judgement is a
second answer to the same question — and the two answers drifted apart the last
time this surface carried its own.

So this module does three things, and judging is not one of them:

1. **Translates** the diagnostics authority's seven states into the five the
   owner sees. The mapping is a table, in one place, tested.
2. **Adds the facts that authority cannot report about itself.** The
   diagnostics checks run *inside* the runtime. A runtime that is wedged, out
   of memory or simply not running records nothing, and nothing looks exactly
   like quiet-because-everything-is-fine. Liveness therefore comes from the
   heartbeat file's age, read from outside the process that writes it.
3. **Refuses to let stale evidence read as current.** A recorded report older
   than :data:`solvent.diagnostics.REPORT_STALE_SECONDS` describes a moment
   that has passed, so it becomes ``UNKNOWN`` rather than keeping its verdict.

Nothing here writes, decides, or asks the runtime for anything.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import diagnostics as diag
from . import status as st

#: The diagnostics authority's states, mapped onto what the owner reads.
#:
#: ``DEGRADED`` and ``WARNING`` both become OWNER ACTION REQUIRED rather than a
#: shade of green: something is working less well than it should, and the owner
#: is the one who decides whether that matters today. ``FAILED`` becomes BLOCKED
#: rather than UNSAFE because a check that failed has stopped something, which
#: is different from something dangerous being permitted.
FROM_DIAGNOSTICS = {
    diag.HEALTHY: st.VERIFIED_HEALTHY,
    diag.DEGRADED: st.OWNER_ACTION_REQUIRED,
    diag.WARNING: st.OWNER_ACTION_REQUIRED,
    diag.UNKNOWN: st.UNKNOWN,
    diag.OWNER_ACTION_REQUIRED: st.OWNER_ACTION_REQUIRED,
    diag.FAILED: st.BLOCKED,
    diag.UNSAFE: st.UNSAFE,
}

#: A heartbeat older than this is not evidence that the runtime is running.
#:
#: The runtime touches the file every 60s and the host watchdog fails it at
#: 180s. Matching the watchdog means the page and the watchdog agree about
#: whether the runtime is up, which is one answer rather than two.
HEARTBEAT_STALE_SECONDS = 180.0


def translate(finding: dict) -> st.Status:
    """One recorded diagnostics finding, as a status the owner reads.

    An unrecognised state becomes ``UNKNOWN``, not healthy. A new word arriving
    from the authority is a word this table has not been taught, and guessing
    that it means "fine" is how a surface comes to report things it cannot
    interpret.
    """
    state = FROM_DIAGNOSTICS.get(str(finding.get("state", "")), st.UNKNOWN)
    detail = str(finding.get("detail", "")) or "no detail recorded"
    action = str(finding.get("owner_action", ""))
    if state == st.VERIFIED_HEALTHY:
        return st.healthy(detail, evidence="recorded by the diagnostics "
                                           "authority in the runtime")
    return st.Status(state, detail, action=action)


def runtime_liveness(heartbeat_path: str, *, now: float | None = None
                     ) -> st.Status:
    """Is the runtime running *now*, from the one signal it cannot fake.

    Writing the heartbeat requires getting far enough through a tick to write
    it, so its age is evidence about the runtime rather than a record of
    something the runtime said about itself once. A recorded "runtime started"
    event is not this: a process that started and then died wrote one.

    Four outcomes, and three of them are not green:

    * fresh — VERIFIED HEALTHY, with the age as evidence;
    * stale — UNKNOWN, because the runtime may be wedged, may be stopped, and
      this cannot tell which;
    * absent — UNKNOWN, because a runtime that has never ticked and a path
      that is misconfigured look identical from here;
    * unreadable — UNKNOWN, naming the permission problem, because the control
      centre runs as a restricted identity and a refused read is a fact about
      this page rather than about the runtime.
    """
    if not heartbeat_path:
        return st.unknown(
            "no heartbeat path is recorded, so the runtime's liveness cannot "
            "be checked from here",
            action="Run a repair: the installer records this path.")
    now = time.time() if now is None else now
    try:
        age = now - Path(heartbeat_path).stat().st_mtime
    except FileNotFoundError:
        return st.unknown(
            "the runtime has not written its heartbeat, so either it has not "
            "run since this path was configured or the path is wrong",
            action="Check that the DeskPilot service is running.")
    except OSError as exc:
        return st.unknown(
            f"the heartbeat could not be read from here: {exc.strerror or exc}",
            action="This is a permissions problem on the control centre, not "
                   "necessarily a problem with the runtime.")
    if age > HEARTBEAT_STALE_SECONDS:
        return st.unknown(
            f"the runtime last reported {st._ago(age)} ago, which is longer "
            f"than its {HEARTBEAT_STALE_SECONDS:.0f}s interval — it may be "
            f"stopped or wedged, and this cannot tell which",
            action="Check the DeskPilot service.", age_seconds=age)
    return st.healthy(
        f"the runtime reported {st._ago(age)} ago",
        evidence=f"heartbeat at {heartbeat_path}, {age:.0f}s old",
        age_seconds=age)


def operating_mode(posture: dict) -> st.Status:
    """HALT is not a fault. It is the owner's switch, in the position they left
    it, and reporting it as a failure would train them to ignore it."""
    mode = str(posture.get("operating_mode", "")) or "UNKNOWN"
    if mode == "HALT":
        return st.Status(st.BLOCKED,
                         "HALT is engaged: no new work is taken and no "
                         "external effect is permitted",
                         evidence="operating_mode in the policy document",
                         action="Clear HALT from Approvals when you are ready.")
    if mode == "NORMAL":
        return st.healthy("accepting work",
                          evidence="operating_mode in the policy document")
    return st.unknown(f"the operating mode reads {mode!r}, which this page "
                      f"does not recognise")


def execution_mode(posture: dict) -> st.Status:
    """Simulation versus real, and the one combination that is UNSAFE.

    Simulation on is VERIFIED HEALTHY, not an action item: it is the safe state
    and the state this deployment is meant to be in until the owner decides
    otherwise. Real execution is reported as what it is — and as UNSAFE when it
    is permitted with an empty allowlist, because that is real effects with
    nothing scoping where they may go.
    """
    simulated = bool(posture.get("simulation_only", True))
    # Each entry is a record with a host and the action classes it permits,
    # not a bare hostname. Joining the list directly raised a TypeError the
    # moment a real allowlist appeared, which is a page that cannot render
    # precisely when it has something worth saying.
    allowlist = [_host(entry) for entry in posture.get("allowlist", []) or []]
    if simulated:
        return st.healthy(
            "simulation only: nothing reaches a real client, a real payment "
            "rail or a real network destination",
            evidence="egress.simulation_only in the policy document")
    if not allowlist:
        return st.unsafe(
            "real execution is permitted and no destination allowlist is "
            "recorded, so nothing scopes where effects may go",
            evidence="egress.simulation_only is false and egress.allowlist is "
                     "empty",
            action="Record an allowlist, or return to simulation.")
    return st.Status(
        st.OWNER_ACTION_REQUIRED,
        f"real execution is permitted, scoped to {len(allowlist)} "
        f"destination(s): " + ", ".join(sorted(allowlist)[:4]),
        evidence="egress.simulation_only is false",
        action="Nothing is wrong. This is the state you chose; it is listed "
               "here because it is the one that can spend money.")


def _host(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("host", "") or entry.get("destination", "")
                   or "an unnamed destination")
    return str(entry)


def audit_chain(intact: bool, detail: str) -> st.Status:
    """A chain that does not verify is the most serious thing this page says."""
    if intact:
        return st.healthy("every audit record hashes to its predecessor",
                          evidence=detail or "chain recomputed from the log")
    return st.unsafe(
        f"the audit chain does not verify: {detail}",
        evidence="chain recomputed from the log",
        action="Stop and investigate. The audit log is the record everything "
               "else is checked against.")


def diagnostics_freshness(latest: dict) -> st.Status:
    """Whether the diagnostics report is recent enough to mean anything."""
    if not latest.get("present"):
        return st.unknown(
            "no diagnostics have been recorded, so nothing here has been "
            "checked by the runtime",
            action="The runtime records these on a timer; if this persists, "
                   "the runtime is not running.")
    return st.fresh(
        "diagnostics are current",
        evidence=f"report recorded at {latest.get('ran_at', 'an unknown time')}",
        age_seconds=latest.get("age_seconds"),
        stale_after=diag.REPORT_STALE_SECONDS)


def build(read, *, heartbeat_path: str = "", now: float | None = None
          ) -> st.Board:
    """The whole board, worst first.

    Liveness and the two posture facts come first because they govern how much
    the rest of the board is worth: a stale report from a runtime that stopped
    an hour ago is a description of an hour ago.
    """
    board = st.Board()
    board.add("Runtime", runtime_liveness(heartbeat_path, now=now))

    posture = read.posture()
    board.add("Operating mode", operating_mode(posture))
    board.add("Execution mode", execution_mode(posture))

    intact, detail = read.audit_chain_intact()
    board.add("Audit chain", audit_chain(intact, detail))

    latest = read.latest_diagnostics()
    board.add("Diagnostics", diagnostics_freshness(latest))
    for finding in latest.get("findings", []) or []:
        if not latest.get("stale"):
            board.add(_label(finding.get("name", "check")), translate(finding))
    return board


def _label(name: str) -> str:
    """``audit_chain`` -> ``Audit chain``. The authority names checks for a
    log; this is the same name for somebody reading a page."""
    return str(name).replace("_", " ").strip().capitalize() or "Check"
