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
    return Requirement(id=requirement.id, text=requirement.text, source=source,
                       confirmed_by=by)


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
