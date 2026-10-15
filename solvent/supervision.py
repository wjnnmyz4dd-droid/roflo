"""Noticing when something has quietly stopped.

Everything else in this system refuses badly-formed work. Nothing was
watching for work that was accepted correctly and then simply stopped
moving -- a worker that took a job and died, a checker that never
answered, a delivery that failed silently, a pipeline that drained to
nothing while the Finder was paused. Those do not raise. They sit.

So this looks for stillness. It is a detector and an escalator, and
the owner's constraint on it is the important part: *it must not
become another autonomous authority capable of overriding
safeguards.* It therefore has no power to accept, price, deliver,
approve, pay, or raise a limit. What it can do is notice, retry a
bounded number of times where a retry is safe, and tell somebody.

**Every finding names the authority that owns the fix.** A supervisor
that fixed things itself would be a second decision-maker over work
the first one already owns, and the two would disagree the first time
they were both right about different things.

**Retries are bounded and counted here, not attempted here.** The
finding says how many attempts have been made and whether another is
allowed; whoever owns the action performs it. A supervisor that
retried deliveries would be a delivery authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: How serious, and therefore whether it interrupts the owner. These
#: are this module's own vocabulary for ranking findings; the
#: notifier has its own and they are not the same list, which cost a
#: round of silently-dropped escalations to discover.
INFO = "INFO"
WARN = "WARN"
CRITICAL = "CRITICAL"

#: What a CRITICAL finding is sent as. Taken from the notifier's
#: vocabulary rather than this one: a finding needs the owner to do
#: something, which is what that severity means there.
OWNER_ACTION_REQUIRED = "ACTION_REQUIRED"

#: What can be noticed. One name per failure shape, because a generic
#: "something is wrong" cannot be deduplicated or acted on.
WORKER_STALLED = "worker_stalled"
CHECKER_STALLED = "checker_stalled"
DELIVERY_FAILED = "delivery_failed"
PAYMENT_UNRECONCILED = "payment_unreconciled"
QUEUE_STARVED = "queue_starved"
OVERCOMMITTED = "overcommitted"
RESOURCE_PRESSURE = "resource_pressure"
NOTIFICATION_FAILED = "notification_failed"
UNRECONCILED_EXTERNAL = "unreconciled_external"
CHANNEL_UNAVAILABLE = "channel_unavailable"

CONDITIONS = (WORKER_STALLED, CHECKER_STALLED, DELIVERY_FAILED,
              PAYMENT_UNRECONCILED, QUEUE_STARVED, OVERCOMMITTED,
              RESOURCE_PRESSURE, NOTIFICATION_FAILED,
              UNRECONCILED_EXTERNAL, CHANNEL_UNAVAILABLE)

#: How long a job may sit in a stage before sitting is the problem.
#: Generous on purpose: a false stall that pages the owner at 3am
#: teaches them to ignore the alert, which is worse than not having
#: one.
DEFAULT_STALL_HOURS = 6.0

#: Attempts before a thing stops being retried and starts being
#: escalated. Bounded because an unbounded retry against a broken
#: external service is a denial of service with extra steps.
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing that has stopped, and who owns putting it right."""

    condition: str
    severity: str
    subject: str
    detail: str
    owner_authority: str
    attempts: int = 0

    @property
    def may_retry(self) -> bool:
        return self.attempts < MAX_ATTEMPTS

    @property
    def dedupe_key(self) -> str:
        """Stable across repeats of the same incident.

        Without this the owner gets the same stalled job every tick
        until they fix it, which is how an alert becomes noise and
        then becomes ignored.
        """
        return f"supervision:{self.condition}:{self.subject}"


@dataclass
class Supervisor:
    """Looks for stillness. Fixes nothing.

    Wired with the authorities it reads and given none of their
    powers. Every check is defensive: an authority that cannot be
    read produces a finding saying so rather than an empty list,
    because a supervisor that reports "all clear" when it could not
    look is worse than no supervisor.
    """

    capacity: object = None
    orchestrator: object = None
    payments: object = None
    applications: object = None
    channels: object = None
    stall_hours: float = DEFAULT_STALL_HOURS
    _attempts: dict = field(default_factory=dict)
    _failures: list = field(default_factory=list)

    def check(self) -> list:
        findings = []
        for probe in (self._capacity_findings, self._queue_findings,
                      self._payment_findings, self._external_findings,
                      self._channel_findings):
            try:
                findings.extend(probe())
            except Exception as exc:
                findings.append(Finding(
                    condition=RESOURCE_PRESSURE, severity=WARN,
                    subject=probe.__name__.strip("_"),
                    detail=(f"this check could not run ({type(exc).__name__}); "
                            "a check that did not run is not a check that "
                            "passed"),
                    owner_authority="operator"))
        return sorted(findings, key=lambda f: CONDITIONS.index(f.condition))

    # ------------------------------------------------------------ probes

    def _capacity_findings(self) -> list:
        if self.capacity is None:
            return []
        out = []
        pressure = self.capacity.pressure()
        if pressure.get("state") == "FULL":
            out.append(Finding(
                condition=RESOURCE_PRESSURE, severity=WARN,
                subject="host",
                detail="; ".join(pressure.get("relief") or ["at a limit"]),
                owner_authority="capacity"))
        unmeasured = [name for name in pressure.get("unmeasured") or []
                      if name in ("disk_free_bytes", "memory_used_percent")]
        if unmeasured:
            out.append(Finding(
                condition=RESOURCE_PRESSURE, severity=WARN,
                subject="telemetry",
                detail=(f"{', '.join(unmeasured)} cannot be read on this "
                        "host, so pressure there is unknown rather than "
                        "absent"),
                owner_authority="operator"))
        exposure = self.capacity.exposure()
        ceiling = exposure.get("committed_ceiling") or 0
        if ceiling and exposure.get("worst_case_committed", 0) > ceiling:
            out.append(Finding(
                condition=OVERCOMMITTED, severity=CRITICAL,
                subject="shop",
                detail=(f"{exposure['committed_jobs']} committed plus "
                        f"{exposure['outstanding_bids']} outstanding offer(s) "
                        f"is past the {ceiling} this shop can hold"),
                owner_authority="capacity"))
        return out

    def _queue_findings(self) -> list:
        if self.capacity is None:
            return []
        out = []
        try:
            pipelines = self.capacity.pipelines()
        except Exception:
            return out
        starved = [name for name, row in pipelines.items()
                   if not row["committed"]]
        if starved and len(starved) == len(pipelines) and pipelines:
            # Every queue empty at once is different from one queue
            # empty: one is normal replenishment, all of them is the
            # Finder having stopped.
            out.append(Finding(
                condition=QUEUE_STARVED, severity=WARN,
                subject="all pipelines",
                detail=(f"all {len(pipelines)} skill queue(s) are empty; "
                        "replenishment asks per skill, so every one being "
                        "empty points at acquisition rather than at the "
                        "queues"),
                owner_authority="finder"))
        workers, throttle = self.capacity.effective_workers("EXECUTE")
        busy = self.capacity.executing()
        committed = self.capacity.jobs_in_flight()
        if committed and not busy and workers > 0 and not throttle:
            out.append(Finding(
                condition=WORKER_STALLED, severity=WARN,
                subject="workers",
                detail=(f"{committed} job(s) are committed, {workers} "
                        "worker(s) are free and nothing is running"),
                owner_authority="orchestrator"))
        return out

    def _payment_findings(self) -> list:
        if self.payments is None:
            return []
        out = []
        profit = self.payments.profit()
        if profit.get("problems"):
            out.append(Finding(
                condition=PAYMENT_UNRECONCILED, severity=CRITICAL,
                subject="ledger",
                detail="; ".join(profit["problems"]),
                owner_authority="ledger"))
        return out

    def _external_findings(self) -> list:
        if self.applications is None:
            return []
        unresolved = self.applications.unresolved()
        if not unresolved:
            return []
        return [Finding(
            condition=UNRECONCILED_EXTERNAL, severity=CRITICAL,
            subject="submissions",
            detail=(f"{len(unresolved)} external action(s) were started and "
                    "nobody established what happened; these block new "
                    "commitments until somebody does"),
            owner_authority="applications")]

    def _channel_findings(self) -> list:
        if self.channels is None:
            return []
        out = []
        for channel, health in self.channels.all().items():
            if health.paused:
                out.append(Finding(
                    condition=CHANNEL_UNAVAILABLE, severity=WARN,
                    subject=channel, detail=health.why,
                    owner_authority="discovery"))
        return out

    # ------------------------------------------------------- escalation

    def escalate(self, findings: list, notifier=None) -> list:
        """Tell the owner, once per incident.

        Deduplicated on the finding's own key, so a stalled job that
        is still stalled next tick does not produce a second message.
        The owner asked not to be flooded, and an alert they learn to
        ignore is worse than no alert.

        Only CRITICAL interrupts. Everything else is for the summary,
        which is where a WARN belongs: it wants reading today, not
        answering now.
        """
        if notifier is None:
            return []
        sent, failed = [], []
        for finding in findings:
            if finding.severity != CRITICAL:
                continue
            try:
                notifier.notify(
                    event="business.exception",
                    severity=OWNER_ACTION_REQUIRED,
                    summary=(f"{finding.condition}: {finding.detail} "
                             f"({finding.owner_authority} owns this)")[:400],
                    dedupe_key=finding.dedupe_key)
                sent.append(finding.dedupe_key)
            except Exception as exc:
                # Recorded, never swallowed. The first version caught
                # this and continued, so a wrong severity string made
                # every escalation vanish and escalate() still
                # returned as though it had worked -- a notification
                # failure that reports success is the one failure
                # this whole module exists to catch in other people's
                # code.
                failed.append(f"{finding.dedupe_key}: {exc}")
        if failed:
            self._failures.extend(failed)
            del self._failures[:-20]
        return sent

    def escalation_failures(self) -> list:
        """Escalations that could not be delivered. Never empty quietly."""
        return list(self._failures)

    def record_attempt(self, finding: Finding) -> int:
        """Count a retry without performing one.

        Whoever owns the action retries it. This exists so that the
        count lives somewhere the next tick can see, which is what
        makes "bounded" mean anything across restarts of a loop.
        """
        key = finding.dedupe_key
        self._attempts[key] = self._attempts.get(key, 0) + 1
        return self._attempts[key]

    def attempts(self, finding: Finding) -> int:
        return self._attempts.get(finding.dedupe_key, 0)
