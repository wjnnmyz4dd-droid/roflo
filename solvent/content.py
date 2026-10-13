"""Untrusted content — everything that arrives from outside.

Job postings, client emails, PDFs, spreadsheets, web pages, API responses and
supplier documents are **data**. They carry zero governance authority. A document
saying "ignore previous rules", "mark this requirement satisfied" or "approve this
vendor" is a document containing that sentence, and nothing more.

The defence is structural rather than a blocklist of suspicious phrases, because
a blocklist only catches the phrasings someone thought of:

* :class:`UntrustedContent` renders as ``<untrusted:...>`` when interpolated, so
  text that reaches a prompt by accident arrives as a marker rather than as a
  payload. Reading the real bytes requires ``.text``, which is explicit and
  greppable.
* Requirements derived from untrusted content are born
  ``MODEL_EXTRACTED_UNCONFIRMED``, and only the owner or a structured client
  confirmation can promote them.
* **Only confirmed requirements may gate a commitment.** That single rule is what
  closes the conformance-capture attack: an injected instruction that loosens a
  requirement produces an unconfirmed draft, and an unconfirmed draft cannot
  authorise anything.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .audit import new_id
from .errors import FailClosed
from .types import PrivacyClass, Requirement, RequirementSource


@dataclass(frozen=True, slots=True)
class UntrustedContent:
    """External bytes, kept at arm's length from anything that decides."""

    id: str
    source: str
    media_type: str
    text: str
    retrieved_at: str
    privacy: PrivacyClass = PrivacyClass.CLIENT_CONFIDENTIAL

    def __str__(self) -> str:
        """Render as a marker, never as the payload.

        An f-string that accidentally includes this object produces
        ``<untrusted:doc_ab12 from client_email>`` rather than attacker-authored
        instructions. Deliberate access goes through ``.text``.
        """
        return f"<untrusted:{self.id} from {self.source}>"

    def __repr__(self) -> str:
        return f"UntrustedContent(id={self.id!r}, source={self.source!r}, " \
               f"bytes={len(self.text)})"

    def summary(self) -> str:
        """A safe description for logs: length and origin, never content."""
        return (f"{self.media_type} from {self.source}, {len(self.text)} chars, "
                f"privacy={self.privacy.value}")


#: Phrases that turn a piece of content into an attempt to issue orders.
#:
#: This answers one narrow question -- *is this text trying to command the
#: system reading it?* -- about content from outside: a job posting, a web
#: page, an attachment. It is deliberately not the customer-service
#: classifier in `feedback`, which does a different job: that one sorts a
#: client's message into a routing category and decides who answers it.
#: One detects an attempt; the other decides what to do about a person.
_INSTRUCTION_ATTEMPTS = (
    r"ignore (all |any )?(previous|prior|above|earlier) instructions?",
    r"disregard (all |any )?(previous|prior|above|earlier)",
    r"\bnew instructions?\b",
    r"^\s*system\s*:",
    r"\byou are now\b",
    r"mark (every|all) requirements? as (passed|complete|done)",
    r"\b(skip|bypass|disable|turn off|override)\b.{0,60}"
    r"\b(verif\w+|check\w*|approval|gate|requirement)\w*\b",
    r"send (me |us )?(your|the) (api[_ ]?key|credential|password|token|secret)",
)


def looks_like_instructions(text: str) -> bool:
    """Whether this content is trying to tell the reader what to do.

    Content from outside is a stranger talking. A stranger is entitled to
    describe work they want done; a stranger issuing commands to the
    system reading them is doing something else, and the one fact worth
    keeping about such a message is that somebody tried.

    Never used to decide what to *do* -- only to notice, so a person sees
    it. Treating this as a filter would make it an access control, and an
    access control made of regular expressions is one somebody rephrases
    past.
    """
    import re as _re

    lowered = (text or "").lower()
    return any(_re.search(pattern, lowered, _re.MULTILINE)
               for pattern in _INSTRUCTION_ATTEMPTS)


def quarantine(text: str, *, source: str, media_type: str = "text/plain",
               privacy: PrivacyClass = PrivacyClass.CLIENT_CONFIDENTIAL) -> UntrustedContent:
    """Wrap external bytes. Everything from outside enters through here."""
    return UntrustedContent(
        id=new_id("doc"), source=source, media_type=media_type, text=text,
        retrieved_at=datetime.now(timezone.utc).isoformat(), privacy=privacy)


def extract_requirements(content: UntrustedContent,
                         candidates: list[str]) -> list[Requirement]:
    """Turn candidate requirement strings into **unconfirmed** drafts.

    ``candidates`` is whatever an extractor proposed — a model, a parser, a
    regular expression. Its provenance is recorded honestly: nothing derived from
    untrusted content may gate a commitment until a human confirms it.
    """
    return [
        Requirement(id=new_id("req"), text=text,
                    source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED,
                    confirmed_by="")
        for text in candidates
    ]


def confirm(requirement: Requirement, *, by: str,
            source: RequirementSource = RequirementSource.OWNER_CONFIRMED) -> Requirement:
    """Promote a draft requirement to one that may gate a commitment."""
    if source is RequirementSource.MODEL_EXTRACTED_UNCONFIRMED:
        raise FailClosed("confirming to MODEL_EXTRACTED_UNCONFIRMED is not a confirmation")
    if source is RequirementSource.OWNER_CONFIRMED and not by.startswith("owner:"):
        raise FailClosed(
            f"owner confirmation requires an owner identity (got {by!r})")
    if not by:
        raise FailClosed("confirmation requires an identity")
    # Everything except the provenance survives. This used to construct a bare
    # Requirement from the id and text alone, silently discarding the check,
    # the acceptance criterion, the params and the criticality -- so a fully
    # specified requirement came back as an untestable MANDATORY one, and
    # `commit_requirements` then refused the whole checklist because it named
    # no registered check. Confirming a requirement says who vouched for it;
    # it is not an edit of what the requirement says.
    return dataclasses.replace(requirement, source=source, confirmed_by=by)


@dataclass(slots=True)
class RequirementSet:
    """The requirements of one job, with their provenance intact."""

    items: list[Requirement] = field(default_factory=list)

    def add(self, requirement: Requirement) -> None:
        self.items.append(requirement)

    def extend(self, requirements: list[Requirement]) -> None:
        self.items.extend(requirements)

    @property
    def unconfirmed(self) -> list[Requirement]:
        return [r for r in self.items if not r.source.may_gate_commitment]

    @property
    def may_gate_commitment(self) -> bool:
        """True only when every requirement has confirmed provenance."""
        return bool(self.items) and not self.unconfirmed
