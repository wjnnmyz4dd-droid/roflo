"""Owner intents: the only thing the web process writes, and it decides nothing.

An intent is a *request*, written to a spool directory as a file. The Solvent
runtime reads it and acts under its own authorities — so an intent is exactly as
powerful as the authority that executes it, and no more.

Two classes, because they are different risks:

**SAFE** intents are ones whose worst outcome is Solvent doing less. Stopping is
the safe direction, so a stolen session that halts the business is a nuisance and
not a loss. The runtime executes these.

**CONSEQUENTIAL** intents are ones whose worst outcome is Solvent doing
something. Promoting a capability, clearing HALT, activating a rail, spending.
The runtime will **not** execute these from a spooled intent: they require an
owner approval from the Owner Channel, whose key this process does not hold. The
web surface can therefore *ask* for them and never *perform* them, which is the
whole reason a compromised control centre is not a compromised business.

Nothing here writes to Solvent's database. The spool is a directory.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

#: Where intents are written. Writable by the web user, read by the runtime.
INTENT_SPOOL = "/var/lib/solvent/owner-intents"

SAFE = "SAFE"
CONSEQUENTIAL = "CONSEQUENTIAL"

#: Every intent the surface may write, and what executing it would mean.
#:
#: A closed list on purpose: an open one would make "what can the website do?"
#: a question nobody can answer, and adding a verb would stop being a decision.
INTENTS = {
    # --- safe: the worst case is Solvent doing less -----------------------
    "halt": (SAFE, "stop taking new work and stop external effects"),
    "acknowledge_alert": (SAFE, "mark an alert as seen by the owner"),
    "recheck_readiness": (SAFE, "recompute the readiness projection"),
    "mark_relayed": (SAFE, "record that the owner passed a drafted message on"),
    "abandon_skill_project": (SAFE, "stop work on a proposed skill"),
    "deprecate_skill_version": (SAFE, "warn that a version is going away"),
    # --- consequential: refused without an owner approval ----------------
    "resume": (CONSEQUENTIAL, "clear HALT and allow work again"),
    "promote_capability": (CONSEQUENTIAL, "register a capability as proven"),
    "decide_proposal": (CONSEQUENTIAL, "answer a capability proposal"),
    "authorize_work_source": (CONSEQUENTIAL, "allow work from a source"),
    "advance_payment_rail": (CONSEQUENTIAL, "record payment-rail progress"),
    "retire_skill_version": (CONSEQUENTIAL, "withdraw a capability version"),
    "rollback_skill": (CONSEQUENTIAL, "point new work at an earlier version"),
    "record_contracting": (CONSEQUENTIAL, "change who Solvent contracts as"),
    "enable_real_execution": (CONSEQUENTIAL,
                              "turn off simulation and allow real effects"),
}


def classify(verb: str) -> str:
    """``SAFE`` or ``CONSEQUENTIAL``. An unknown verb is neither — it is refused.

    Defaulting an unrecognised verb to SAFE is how a new action arrives without
    anybody deciding it is safe.
    """
    if verb not in INTENTS:
        raise ValueError(
            f"{verb!r} is not an intent this surface may write; a verb that is "
            "not on the list is not safe by default")
    return INTENTS[verb][0]


def describe(verb: str) -> str:
    return INTENTS.get(verb, (None, "unknown"))[1]


class IntentWriter:
    """Appends intents to the spool. Holds no database handle at all."""

    def __init__(self, spool: str = INTENT_SPOOL) -> None:
        self.spool = Path(spool)

    def write(self, *, verb: str, requested_by: str, why: str,
              subject: str = "", params: dict | None = None) -> dict:
        """Record one request. Returns the record, including its class.

        Written to a temporary name and renamed, so a reader never sees half an
        intent — a truncated intent would be an action nobody asked for.
        """
        intent_class = classify(verb)
        if not why.strip():
            raise ValueError("an intent must say why; an unexplained request to "
                             "the runtime is not reviewable afterwards")
        record = {
            "id": "int_" + secrets.token_hex(8),
            "verb": verb,
            "intent_class": intent_class,
            "subject": subject,
            "params": dict(params or {}),
            "why": why.strip(),
            "requested_by": requested_by,
            "requires_owner_approval": intent_class == CONSEQUENTIAL,
        }
        self.spool.mkdir(parents=True, exist_ok=True)
        target = self.spool / f"{record['id']}.intent"
        partial = target.with_suffix(".partial")
        partial.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(partial, target)
        return record

    def pending(self) -> list[dict]:
        if not self.spool.is_dir():
            return []
        out = []
        for path in sorted(self.spool.glob("*.intent")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                out.append({"id": path.stem, "verb": "UNREADABLE",
                            "intent_class": CONSEQUENTIAL,
                            "why": "could not be parsed"})
        return out
