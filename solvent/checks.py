"""Which check library verifies which capability. One registry, one lookup.

This is a **dispatch table, not an authority**. It decides nothing about whether
a deliverable may ship — it only answers "given work produced by this
capability, which library knows how to re-check it?". The Guardian, the
checklist and the delivery gate are unchanged and remain single-owner.

It exists because that question was previously answered by whichever module
happened to be imported at the top of the file. ``feedback.investigate`` did
``from . import csvverify`` unconditionally, so re-checking a *report* ran every
requirement through the CSV checks, which returned UNVERIFIABLE for all of them
and concluded that Solvent had shipped a defective artifact and that its own
verifier had missed it. Both statements were false. A hardcoded import is a
guess about the work in front of you, and the second capability is where that
guess starts being wrong.
"""

from __future__ import annotations

from . import csvverify, reportverify

#: ``capability name -> (checks, run)``. Keyed by name; versions resolve to it.
REGISTRY = {
    "csv-cleanup": (csvverify.CHECKS, csvverify.run),
    "report-builder": (reportverify.CHECKS, reportverify.run),
}


def _name(capability: str) -> str:
    """``report-builder/1.0`` and ``report-builder`` name the same library."""
    return (capability or "").split("/", 1)[0]


def known(capability: str) -> bool:
    return _name(capability) in REGISTRY


def checks_for(capability: str) -> dict:
    """The check table for a capability. An unknown one has no checks at all.

    Returning ``{}`` rather than raising keeps the caller's own fail-closed
    logic in charge: a mandatory requirement whose check is not in the table is
    already refused, so an unrecognised capability refuses the job instead of
    being quietly verified by the wrong library.
    """
    return REGISTRY.get(_name(capability), ({}, None))[0]


def runner_for(capability: str):
    """The ``run(check, source=, output=, params=)`` for a capability, or None.

    ``None`` means "nobody here can re-check this", which callers must treat as
    *unknown*, never as pass and never as fail. Guessing in either direction
    invents a verdict about work this process cannot read.
    """
    return REGISTRY.get(_name(capability), ({}, None))[1]
