"""Deliberate fault injection. **Test-only, and structurally so.**

Nothing in ``solvent/`` imports this module, and a test asserts that. Production
code containing a way to inject a fault is production code containing a way to
inject a fault, however carefully the flag is named.

The seams are real ones: each fault replaces a method an authority actually
calls, so the failure arrives where a genuine failure would arrive rather than
at a hook added for the purpose. A fault injected into a place nothing uses
tests the harness rather than the system.
"""

from __future__ import annotations

import contextlib


class InjectedFault(RuntimeError):
    """Raised by an injected fault. Distinguishable from a real defect."""


@contextlib.contextmanager
def failing(target, attribute: str, *, exception=None, after: int = 0):
    """Make ``target.attribute`` raise, optionally after ``after`` calls.

    ``after`` is what makes a *crash mid-workflow* testable rather than only a
    crash at the start. Failing on the first call exercises the guard clause;
    failing on the third exercises the part where half the work is done and
    something durable has already been written.
    """
    original = getattr(target, attribute)
    calls = {"n": 0}
    problem = exception or InjectedFault(f"injected fault in {attribute}")

    def fail(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > after:
            raise problem
        return original(*args, **kwargs)

    setattr(target, attribute, fail)
    try:
        yield calls
    finally:
        setattr(target, attribute, original)


@contextlib.contextmanager
def slow_then_failing(target, attribute: str, *, failures: int):
    """Fail a fixed number of times, then work. For breaker and canary tests."""
    original = getattr(target, attribute)
    calls = {"n": 0}

    def maybe(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise InjectedFault(f"injected transient fault in {attribute}")
        return original(*args, **kwargs)

    setattr(target, attribute, maybe)
    try:
        yield calls
    finally:
        setattr(target, attribute, original)


#: The stages a fault can be injected at, and the seam each one uses.
#:
#: Written out so that adding a stage is a decision somebody makes rather than
#: something that happens, and so a reader can see at a glance which parts of
#: the pipeline have had a failure driven through them.
STAGES = {
    "intake": ("orchestrator", "intake"),
    "requirements": ("orchestrator", "commit_requirements"),
    "artifact_registration": ("orchestrator", "register_artifact"),
    "verification": ("audit", "record_verification"),
    "delivery_gate": ("orchestrator", "mark_verified"),
    "delivery": ("orchestrator", "record_delivery"),
    "ledger": ("ledger", "open_payment"),
    "customer_service": ("feedback", "receive"),
    "skills_lab": ("skillslab", "record_need"),
    "notification": ("notifier", "notify"),
    "incident_recording": ("resilience", "record_incident"),
}


def inject(solvent, stage: str, **kwargs):
    """A context manager failing whichever seam ``stage`` names."""
    if stage not in STAGES:
        raise KeyError(f"{stage!r} is not an injectable stage; "
                       f"one of {sorted(STAGES)}")
    attribute_owner, attribute = STAGES[stage]
    return failing(getattr(solvent, attribute_owner), attribute, **kwargs)
