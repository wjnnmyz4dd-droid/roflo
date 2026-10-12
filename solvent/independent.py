"""T2 evidence: a second judgement, by something that did not make the first.

The architecture contract (§C-HIGH) asks for "T1 + **T2 with verifier ≠
executor**", and the verification audit records why that wording is strict:

    A T2 "independent review" using the same model, on the same context,
    re-reading the same poisoned document, is not independent -- it can be
    instructed to approve. T2 requires a different instance and, where
    possible, a check that does not re-read the attacker-controlled input.

So the question this module answers is not "did something check it twice?"
but **"who or what formed the second opinion, and in what sense is it not the
first one again?"** Two producers qualify, and they are trusted by different
means because they are different kinds of attester:

``owner_review``
    A person looked at this exact file and said so, through the signed Owner
    Channel. The trust anchor is the signature: HMAC over the approval,
    single-use nonce, expiry, and an owner identity the policy knows. A human
    is unambiguously a different instance and cannot be argued into approving
    by text inside the document.

``differential_review``
    The deliverable is read again by an implementation **written by different
    people** -- openpyxl for .xlsx, python-docx for .docx -- and its values
    compared against the source. The trust anchor is authorship: a mistake or
    a parser-level exploit in Solvent's own reader does not transfer to a
    library it shares no code with.

**What is deliberately not here.** A second reader written by whoever wrote
the first is not independent in the sense that matters: the two share an
author, a session and a misunderstanding, and both would read a malformed
file the same confident wrong way. Shipping one and calling it T2 would be
exactly the verification theatre the contract names. So there is no
Solvent-authored "second verifier", and where neither producer above is
available this module says so rather than manufacturing a tier.

**Availability is not uniform, and that matters on the VPS.** The shipped
Windows payload is standard-library only, so `differential_review` is absent
there: on that machine the only T2 producer is a person. :func:`available`
reports what this installation actually has, and the pre-execution
eligibility check reads it before any money is committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import FailClosed
from .types import ActionClass, VerificationTier

#: Which third-party reader gives a genuinely separate opinion on which format.
#: Keyed by the deliverable's extension rather than by capability: the question
#: is who can read *this file*, and a document job may deliver .docx, .md or
#: .json depending on its brief.
_DIFFERENTIAL_READERS = {
    "xlsx": ("openpyxl", "openpyxl, written and maintained independently of "
                         "this project"),
    "docx": ("docx", "python-docx, written and maintained independently of "
                     "this project"),
}

#: The subject line a review approval must carry. The digest is in it so an
#: approval cannot be reused for a different file: the signature covers the
#: subject, so changing the digest invalidates it.
REVIEW_SUBJECT = "independent review of artifact {digest}"


@dataclass(frozen=True, slots=True)
class Producer:
    """One way of getting a second opinion, and whether it is usable here."""

    name: str
    usable: bool
    why: str
    #: True when a person has to do something before this can produce evidence.
    needs_a_person: bool = False


@dataclass
class Availability:
    """What this installation can actually produce T2 evidence with."""

    producers: list[Producer] = field(default_factory=list)

    @property
    def any_usable(self) -> bool:
        return any(p.usable for p in self.producers)

    @property
    def automated(self) -> list[Producer]:
        return [p for p in self.producers if p.usable and not p.needs_a_person]

    def summary(self) -> str:
        if not self.producers:
            return "no independent review producer is known to this build"
        return "; ".join(
            f"{p.name}: {'available' if p.usable else 'unavailable'} ({p.why})"
            for p in self.producers)


def _reader_for(extension: str):
    """The third-party module for this format, or None."""
    entry = _DIFFERENTIAL_READERS.get((extension or "").lstrip(".").lower())
    if entry is None:
        return None, ""
    module_name, description = entry
    try:
        import importlib

        return importlib.import_module(module_name), description
    except ImportError:
        return None, description


def available(*, extension: str = "", owner_channel=None) -> Availability:
    """What could produce T2 evidence for a deliverable of this format, here.

    Asked *before* work starts. A job that will need a second opinion nobody
    can give should be refused while it is still a quote, not after the
    compute has been spent and the client is waiting.
    """
    report = Availability()

    signed_ready = bool(owner_channel is not None
                        and getattr(owner_channel, "available", False))
    report.producers.append(Producer(
        name="owner review",
        usable=signed_ready,
        why=("a signed owner approval naming the artifact can be issued"
             if signed_ready else
             getattr(owner_channel, "key_refusal", None)
             or "no Owner Channel with a signing key is configured"),
        needs_a_person=True))

    module, description = _reader_for(extension)
    if description:
        report.producers.append(Producer(
            name=f"differential review ({extension})",
            usable=module is not None,
            why=(description if module is not None else
                 f"{description} is not installed in this environment")))
    elif extension:
        report.producers.append(Producer(
            name=f"differential review ({extension})",
            usable=False,
            why=(f"no independently authored reader for .{extension} is known "
                 "to this build, so a second automated opinion would be "
                 "Solvent reading its own output twice")))
    return report


# --------------------------------------------------------------- owner review


def owner_review(audit, *, job_id: str, approval, owner_channel,
                 reviewer_identity: str, requirement_ids, artifact,
                 consequence, note: str = "") -> list[str]:
    """Record a person's review of **this exact artifact** as T2 evidence.

    The signature is what makes this evidence rather than a claim. It is
    verified before anything is written, it names the digest in its subject so
    it cannot be moved to another file, and it is consumed afterwards so the
    same review cannot be replayed onto a corrected deliverable.

    Returns the evidence ids, one per requirement reviewed.
    """
    if approval is None:
        raise FailClosed(
            "an independent review must be signed; an unsigned assertion that "
            "somebody looked at the file is the self-report T2 exists to "
            "replace")
    if owner_channel is None or not getattr(owner_channel, "available", False):
        raise FailClosed(
            "no Owner Channel with a signing key is configured, so a review "
            "cannot be authenticated and will not be recorded as T2")

    problem = owner_channel.verify(
        approval, action_class=ActionClass.C0_INTERNAL_READ, job_id=job_id,
        amount_cents=0)
    if problem:
        raise FailClosed(f"the review approval does not verify: {problem}")

    expected = REVIEW_SUBJECT.format(digest=artifact.digest)
    if approval.subject != expected:
        # The digest is inside the signed payload, so this cannot be edited
        # after the fact -- but an approval issued for a *different* file
        # would verify perfectly well, and this is what stops it being used
        # here. A corrected deliverable needs its own review.
        raise FailClosed(
            f"the approval reviews {approval.subject!r}, not this artifact. "
            f"Expected {expected!r}")
    if reviewer_identity == "worker":
        raise FailClosed(
            "the executor cannot be its own independent reviewer")

    requirement_ids = list(requirement_ids)
    if not requirement_ids:
        raise FailClosed("a review must say which requirements it covers")

    recorded = []
    for requirement_id in requirement_ids:
        recorded.append(audit.record_independent_review(
            subject_ref=job_id, requirement_id=requirement_id,
            artifact_digest=artifact.digest, artifact_id=artifact.id,
            reviewer_identity=reviewer_identity, consequence=consequence,
            method="reviewed by a person against the agreed checklist",
            detail=note or "reviewed and accepted",
            approval_nonce=approval.nonce))
    owner_channel.consume(approval)
    return recorded


# ---------------------------------------------------------- differential review


def differential_review(audit, *, job_id: str, source: str, artifact,
                        requirement_ids, consequence, requirements=()) -> list[str]:
    """Read the deliverable again with somebody else's code, and compare.

    Independent by authorship. Solvent's own `.xlsx` reader and openpyxl were
    written by different people from different specifications of the same
    format, so a bug in one -- or an input crafted to exploit one -- does not
    reach the other. That is a weaker independence than a person, and it is
    not nothing: it is exactly the class of error a single implementation
    cannot catch about itself.

    Refuses rather than degrades when the reader is absent, because a T2 that
    silently becomes a T1 is worse than no T2 at all.
    """
    extension = str(artifact.path).rsplit(".", 1)[-1].lower()
    module, description = _reader_for(extension)
    if module is None:
        raise FailClosed(
            f"no independently authored reader for .{extension} is available "
            f"here ({description or 'none is known to this build'}), so a "
            "second automated opinion is not possible")

    delivered, problem = _read_independently(module, extension, artifact.path)
    if problem:
        raise FailClosed(f"the independent reader could not read the "
                         f"deliverable: {problem}")
    if not delivered:
        raise FailClosed(
            "the independent reader found nothing in the deliverable, which "
            "is not a pass")

    # Reading the file back is not a review. "It parses with somebody else's
    # library" says the bytes are well-formed; it says nothing about whether
    # the figures are right, and recording that as T2 would be the theatre
    # this module exists to avoid. So the second opinion recomputes the
    # numbers the client's money actually depends on, from the source, with
    # the independent reader on *both* sides.
    findings = _recompute_independently(module, extension, source, delivered,
                                        list(requirements))
    disagreements = [f for f in findings if not f[1]]
    if disagreements:
        raise FailClosed(
            "the independent reader disagrees with the deliverable: "
            + "; ".join(f"{name}: {detail}" for name, _, detail in disagreements))
    if not findings:
        raise FailClosed(
            f"nothing in this brief can be recomputed independently with "
            f"{module.__name__}, so a second automated opinion would only be "
            "confirming that the file parses. Review by a person is required")

    requirement_ids = list(requirement_ids)
    if not requirement_ids:
        raise FailClosed("a review must say which requirements it covers")

    checked = ", ".join(name for name, _, _ in findings)
    recorded = []
    for requirement_id in requirement_ids:
        recorded.append(audit.record_independent_review(
            subject_ref=job_id, requirement_id=requirement_id,
            artifact_digest=artifact.digest, artifact_id=artifact.id,
            reviewer_identity=f"differential:{module.__name__}",
            consequence=consequence,
            method=f"recomputed with {module.__name__}, an independently "
                   "authored implementation, from the client's own source",
            detail=f"{len(delivered)} row(s) read back; independently "
                   f"recomputed: {checked}",
            approval_nonce=""))
    return recorded


def _recompute_independently(module, extension: str, source: str, delivered,
                             requirements) -> list:
    """Recompute what can be recomputed, using only the third-party reader.

    Returns ``[(what, agreed, detail)]``. An empty list means this brief has
    nothing a second implementation can independently check -- which is a
    refusal, not a pass.

    Deliberately narrow. It checks the arithmetic and the provenance, because
    those are the two things a client loses money on: a total that is wrong,
    and a row that came from nowhere. It does not try to re-implement every
    check; a second full verifier written by the same hand would not be
    independent anyway.
    """
    from .types import check_of

    if extension != "xlsx" or not source:
        return []
    rows, problem = _read_independently(module, extension, source)
    if problem or not rows:
        return []

    findings = []
    source_header, source_body = rows[0], rows[1:]
    out_header, out_body = delivered[0], delivered[1:]

    for requirement in requirements:
        name = check_of(requirement)
        params = dict(getattr(requirement, "params", {}) or {})

        if name == "compute_column":
            into, using = params.get("into"), list(params.get("using") or [])
            if not into or into not in out_header or len(using) != 2:
                continue
            try:
                target = out_header.index(into)
                left, right = (out_header.index(u) for u in using)
            except ValueError:
                continue
            wrong = []
            for row in out_body:
                if target >= len(row) or row[target] in (None, ""):
                    continue
                try:
                    expected = float(row[left]) * float(row[right])
                    if abs(float(row[target]) - expected) > 0.005:
                        wrong.append(f"{row[target]} != {expected:.2f}")
                except (TypeError, ValueError):
                    continue
            findings.append((
                f"{into} = {' x '.join(using)}", not wrong,
                "every row agrees" if not wrong else "; ".join(wrong[:3])))

        if name == "total_row":
            for column, how in dict(params.get("aggregates") or {}).items():
                if how != "sum" or column not in out_header:
                    continue
                index = out_header.index(column)
                numbers = []
                stated = None
                for row in out_body:
                    if index >= len(row):
                        continue
                    first = str(row[0]).strip().upper() if row and row[0] else ""
                    try:
                        value = float(row[index])
                    except (TypeError, ValueError):
                        continue
                    if first == "TOTAL":
                        stated = value
                    else:
                        numbers.append(value)
                if stated is None:
                    continue
                findings.append((
                    f"total of {column}", abs(sum(numbers) - stated) < 0.005,
                    f"stated {stated}, independently {sum(numbers)}"))

        if name == "preserve_columns":
            for column in list(params.get("columns") or []):
                if column not in out_header or column not in source_header:
                    continue
                out_index = out_header.index(column)
                src_index = source_header.index(column)
                supplied = {_text(r[src_index]) for r in source_body
                            if src_index < len(r)}
                invented = [_text(r[out_index]) for r in out_body
                            if out_index < len(r)
                            and _text(r[out_index]) not in supplied
                            and _text(r[out_index]) != ""]
                findings.append((
                    f"{column} values all came from the source", not invented,
                    "every value is one the client supplied" if not invented
                    else f"{len(invented)} value(s) the source never had"))
    return findings


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _read_independently(module, extension: str, path: str):
    """(values, problem). Uses the third-party API, never Solvent's reader."""
    try:
        if extension == "xlsx":
            book = module.load_workbook(path, data_only=False)
            sheet = book[book.sheetnames[0]]
            return [[cell.value for cell in row] for row in sheet.iter_rows()], ""
        if extension == "docx":
            document = module.Document(path)
            return [p.text for p in document.paragraphs], ""
    except Exception as problem:   # a third-party reader may raise anything
        return [], f"{type(problem).__name__}: {problem}"
    return [], f"no independent reader is wired for .{extension}"
