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


#: Consequential verbs that an owner approval is *not* enough to complete from
#: a spool file. Each one is here for its own reason, and none of them is
#: "we ran out of time".
#:
#: ``record_contracting`` carries the owner's legal name and contracting
#: address. That is personal data, and routing it through a file the web
#: process wrote — in a directory the web service can read back — would put it
#: exactly where the rest of this design spent its effort keeping it from
#: going. It belongs on the terminal path, which writes it straight into policy.
#:
#: ``authorize_work_source`` needs a source *implementation*. An adapter that
#: fetches from a platform is code; a row naming one is a registration, not a
#: definition, which is the same distinction Discovery's rehydration makes.
#:
#: ``enable_real_execution`` is the single change that converts a simulation
#: into a business that can spend money and contact strangers. Approving a file
#: is too easy a gesture for it. It stays a thing the owner types.
TERMINAL_ONLY = frozenset({
    "record_contracting", "authorize_work_source", "enable_real_execution",
})

#: What to type instead, named so a refusal is directions rather than a wall.
TERMINAL_COMMAND = {
    "record_contracting": "solvent setup contracting",
    "authorize_work_source": "solvent setup work-source",
    "enable_real_execution": "solvent setup capability",
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


class WriteOnlySpool:
    """Writes intents and cannot read one back. This is what the web gets.

    The split is structural rather than a convention. ``record_contracting``
    carries the owner's legal name and contracting address, and most intents
    carry a reason the owner typed. Handing the web surface an object that can
    enumerate the spool would mean whoever took over the website could read
    back everything the owner had asked for since the runtime last drained it
    — which is the disclosure this boundary exists to prevent. So the surface
    is given an object with no method that reads, and a test asserts that no
    code path in :mod:`solvent.web` calls one.

    The approvals page still shows what is queued. It reads that from the
    audit log, which the runtime writes and the web cannot — so the list comes
    from the runtime's own record instead of from a directory the web process
    can write to, and a stuffed spool file cannot fabricate a row on the
    owner's approvals page either.

    What this does **not** do, said plainly because a reader deserves to know
    which half of the problem is solved: it is not an access-control boundary.
    Both Windows boot services run as SYSTEM, so file permissions do not
    separate them, and a process that can write the spool can read the
    directory whatever object this module hands it. Enforcing it at that level
    needs the control centre to run under its own lower-privileged service
    account, which it does not yet. What holds today is that the web code
    carries no reader, and that the runtime drains the spool on its timer so an
    intent is not sitting there for long.
    """

    def __init__(self, spool: str = INTENT_SPOOL) -> None:
        self.spool = Path(spool)

    def write(self, *, verb: str, requested_by: str, why: str,
              subject: str = "", params: dict | None = None) -> dict:
        """Record one request. Returns the record, including its class.

        Written under its final name, in one call, rather than to a temporary
        name and renamed. The rename was better in one respect — a reader could
        never see half an intent — and it is gone for something worth more:
        ``os.replace`` needs DELETE permission on the source file, and a
        process holding DELETE in this directory can remove a *pending request*
        as easily as its own partial. The Windows drop box grants add-file and
        traverse and nothing else, which is what makes "cannot delete a pending
        request" a permission rather than a promise.

        A reader can therefore catch a partly written file. That is handled
        where it is read: :func:`solvent.harness.consume_owner_intents` leaves
        an unparseable intent alone while it is new and refuses it once it is
        old, because a truncated file is either a write in progress or a broken
        request, and which one it is depends only on how long it has been
        sitting there.
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
        try:
            self.spool.mkdir(parents=True, exist_ok=True)
        except OSError:
            # The installer creates this directory and grants add-file on
            # it without granting the right to create it. A mkdir that
            # fails because the directory is already there, or because
            # this identity may not create one, is not a reason to refuse
            # the owner's request -- the write below is the real test.
            pass
        target = self.spool / f"{record['id']}.intent"
        # One call, one buffer. Two writes could be interleaved with a read.
        target.write_bytes(json.dumps(record, indent=2).encode("utf-8"))
        return record


class IntentWriter(WriteOnlySpool):
    """A spool handle that can also read it back — for the terminal side.

    ``solvent approvals`` has to show the owner the intent they are about to
    approve, and that is a terminal process running with the owner's own
    authority. The runtime reads the spool for the same reason. Neither is the
    web, and the web does not get this class.
    """

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
