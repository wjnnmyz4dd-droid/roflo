"""Whether an acquisition channel can currently be trusted to answer.

A marketplace does not usually fail by going down. It fails by
answering 401 because a token expired, or 429 because somebody looped,
or 200 with an empty list because the account was restricted -- and the
third one is the dangerous shape, because an empty list is also what a
quiet morning looks like.

So this records observations and derives a state, and the state it
derives when it cannot tell is UNCERTAIN rather than HEALTHY. The
owner's instruction was to pause affected channels when status becomes
uncertain, which only means anything if uncertainty is a state the
system can be in. Silence is not health here, in the same way an
unreadable disk is not a healthy disk.

**It pauses a channel; it does not decide what a channel may do.**
The source catalogue owns permission and the discovery authority owns
sources. This answers one question -- "is this channel answering
properly right now" -- and a paused channel is one nobody should call
until somebody looks, not one that has lost its permissions.

**Counting resets on success.** A channel that failed twice yesterday
and has answered correctly since is healthy, because the alternative
is a system that gets permanently more pessimistic and eventually
stops working for reasons nobody can find.
"""

from __future__ import annotations

from dataclasses import dataclass

HEALTHY = "HEALTHY"
#: Answering, but something is wrong enough to slow down for.
DEGRADED = "DEGRADED"
#: Nobody can say. Treated as a reason to stop, never as a reason to
#: carry on.
UNCERTAIN = "UNCERTAIN"
#: The platform has said no in a way that will not pass on its own.
BLOCKED = "BLOCKED"

STATES = (HEALTHY, DEGRADED, UNCERTAIN, BLOCKED)

#: What can be observed about a call to a channel. Each maps to a
#: state, and the mapping is the whole judgement this module makes.
OK = "ok"
AUTH_FAILED = "auth_failed"
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
RESTRICTED = "restricted"
POLICY_CHANGED = "policy_changed"
PAYMENT_HOLD = "payment_hold"
DISPUTE = "dispute"
COMPLAINT = "complaint"
UNREADABLE = "unreadable"

OBSERVATIONS = (OK, AUTH_FAILED, RATE_LIMITED, SERVER_ERROR, RESTRICTED,
                POLICY_CHANGED, PAYMENT_HOLD, DISPUTE, COMPLAINT,
                UNREADABLE)

#: An observation that settles the state on its own, whatever else
#: has happened. These are the platform speaking, not a transient.
_DECISIVE = {
    RESTRICTED: BLOCKED,
    POLICY_CHANGED: UNCERTAIN,
    AUTH_FAILED: UNCERTAIN,
    PAYMENT_HOLD: BLOCKED,
}

#: Observations that are bad but survivable, and how many in a row
#: before the channel stops being merely degraded.
_TOLERATED = {RATE_LIMITED: 3, SERVER_ERROR: 3, UNREADABLE: 1,
              DISPUTE: 1, COMPLAINT: 3}

#: States in which a channel should not be called.
PAUSED_STATES = (UNCERTAIN, BLOCKED)


@dataclass(frozen=True, slots=True)
class Health:
    channel: str
    state: str = HEALTHY
    why: str = ""
    consecutive_failures: int = 0

    @property
    def paused(self) -> bool:
        return self.state in PAUSED_STATES


class ChannelHealth:
    """Observations in, a state out. Holds no permission of its own."""

    def __init__(self, store=None, audit=None) -> None:
        self._audit = audit
        #: In memory on purpose. This is a view of right now, and a
        #: restart genuinely does not know whether the token is still
        #: good -- which is UNCERTAIN, and is what an empty table
        #: produces below. Persisting it would carry yesterday's
        #: verdict into a morning nobody has tested.
        self._seen: dict[str, list] = {}

    def observe(self, channel: str, observation: str, *,
                detail: str = "") -> Health:
        """Record what happened on a call, and say where that leaves us."""
        if observation not in OBSERVATIONS:
            raise ValueError(
                f"{observation!r} is not something this knows how to read; "
                f"the observations are {', '.join(OBSERVATIONS)}")
        log = self._seen.setdefault(channel, [])
        log.append((observation, detail))
        del log[:-20]
        health = self._derive(channel)
        if self._audit is not None and health.state != HEALTHY:
            try:
                self._audit.record(
                    event="channel.health", authority="channelhealth",
                    initiator="channelhealth", why=health.why[:300],
                    decision=health.state, input_ref=channel)
            except Exception:
                pass
        return health

    def health(self, channel: str) -> Health:
        return self._derive(channel)

    def paused(self, channel: str) -> tuple:
        """Should this channel be left alone, and why.

        A channel nobody has called is paused. That is deliberate and
        is the opposite of the usual default: an untested token is
        not a working token, and the first call of the day is the one
        that finds out. The caller's job is to make that first call
        deliberately rather than to assume.
        """
        health = self._derive(channel)
        return health.paused, health.why

    def all(self) -> dict:
        return {channel: self._derive(channel) for channel in self._seen}

    def _derive(self, channel: str) -> Health:
        log = self._seen.get(channel) or []
        if not log:
            return Health(channel, UNCERTAIN,
                          "nothing has been observed on this channel, so "
                          "nobody knows whether it answers; an untested "
                          "token is not a working one")
        # Walk backwards to the last success. Anything before it has
        # been recovered from and is not evidence about now.
        recent = []
        for observation, detail in reversed(log):
            if observation == OK:
                break
            recent.append((observation, detail))
        if not recent:
            return Health(channel, HEALTHY, "answering correctly")

        for observation, detail in recent:
            if observation in _DECISIVE:
                return Health(
                    channel, _DECISIVE[observation],
                    f"{observation}: {detail}" if detail else observation,
                    len(recent))

        worst, count = recent[0][0], len(recent)
        tolerance = _TOLERATED.get(worst, 1)
        if count >= tolerance:
            return Health(
                channel, UNCERTAIN,
                f"{count} consecutive {worst} observation(s), which is at "
                f"or past the {tolerance} this channel is given before its "
                "status stops being clear",
                count)
        return Health(
            channel, DEGRADED,
            f"{count} consecutive {worst} observation(s); still answering",
            count)
