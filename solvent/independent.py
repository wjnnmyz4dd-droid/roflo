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
from pathlib import Path

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
    # CSV was the hard one, and for a real reason: the worker parses with
    # the standard library's ``csv``, so a checker using ``csv`` would be
    # Solvent reading its own output twice. CleverCSV parses with its own
    # C parser -- it imports ``csv`` only to interoperate with dialect
    # objects, never to read a file -- which is what makes the second
    # opinion a second opinion.
    "csv": ("clevercsv", "CleverCSV, written and maintained independently of "
                         "this project, parsing with its own implementation "
                         "rather than the standard library's csv module"),
    # Markdown was the other hard one. The worker renders it and the
    # capability's own checker reads it back by matching line prefixes --
    # ``line.startswith("## ")`` -- which is one project's idea of Markdown
    # consulted twice. markdown-it-py is a CommonMark implementation ported
    # from the JavaScript markdown-it, written by other people from a
    # published specification, and it disagrees with a prefix scan exactly
    # where it matters: a heading inside a fenced code block is not a
    # heading, a setext underline is one, and an escaped pipe stays in its
    # cell.
    "md": ("markdown_it", "markdown-it-py, a CommonMark parser written and "
                          "maintained independently of this project, ported "
                          "from the JavaScript markdown-it rather than "
                          "sharing this project's reader"),
    "markdown": ("markdown_it", "markdown-it-py, a CommonMark parser written "
                                "and maintained independently of this "
                                "project"),
}

#: The subject line a review approval must carry. The digest is in it so an
#: approval cannot be reused for a different file: the signature covers the
#: subject, so changing the digest invalidates it.
REVIEW_SUBJECT = "independent review of artifact {digest}"

#: How much of a CSV the independent reader looks at to work out its
#: dialect. Enough to see quoting and embedded newlines in a real file,
#: bounded so a hostile multi-gigabyte deliverable cannot be read into
#: memory by the thing that is supposed to be checking it.
_CSV_SNIFF_BYTES = 64 * 1024


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

    if extension in ("md", "markdown"):
        return _recompute_markdown(source, delivered, requirements)
    if extension not in ("xlsx", "csv") or not source:
        return []
    rows, problem = _read_independently(module, extension, source)
    if problem or not rows:
        return []

    findings = []
    authorised = _authorised_sheets(requirements)
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
                if column not in out_header:
                    continue
                out_index = out_header.index(column)
                if authorised:
                    # A consolidated brief has more than one source. Judge the
                    # output against every sheet it authorised, and against no
                    # others.
                    supplied = _supplied_values(module, extension, source,
                                                column, authorised)
                    if not supplied:
                        continue
                elif column in source_header:
                    src_index = source_header.index(column)
                    supplied = {_text(r[src_index]) for r in source_body
                                if src_index < len(r)}
                else:
                    continue
                invented = [_text(r[out_index]) for r in out_body
                            if out_index < len(r)
                            and _text(r[out_index]) not in supplied
                            and _text(r[out_index]) != ""]
                findings.append((
                    f"{column} values all came from the source", not invented,
                    "every value is one the client supplied" if not invented
                    else f"{len(invented)} value(s) the source never had"))
    return findings


def _authorised_sheets(requirements) -> list[str]:
    """The source sheets the checklist puts in scope, in declared order.

    A job whose brief says *consolidate these two sheets* has two sources,
    and judging its output against only the first is how a correctly
    consolidated deliverable gets reported as inventing values. Measured:
    three Client ref values that appear in the second sheet were called
    "value(s) the source never had".

    Empty means no requirement widened the scope, and the caller keeps its
    existing behaviour of reading the first sheet. The list is what the
    *client agreed to*, never every sheet in the file: a value lifted from
    some other sheet in the workbook is still a value the brief did not
    authorise.
    """
    from .types import check_of

    names: list[str] = []
    for requirement in requirements:
        if check_of(requirement) != "consolidate_sheets":
            continue
        params = dict(getattr(requirement, "params", {}) or {})
        for name in list(params.get("sheets") or []):
            if name not in names:
                names.append(str(name))
    return names


def _supplied_values(module, extension, source, column, sheets) -> set:
    """Every value the authorised source sheets carry for one column.

    Each sheet is read with its own header rather than the first sheet's,
    because consolidated sources are not obliged to agree on column order.
    """
    supplied: set = set()
    for name in sheets:
        rows, problem = _read_independently(module, extension, source,
                                            sheet_name=name)
        if problem or not rows:
            continue
        header = [_text(c) for c in rows[0]]
        if column not in header:
            continue
        index = header.index(column)
        supplied |= {_text(r[index]) for r in rows[1:] if index < len(r)}
    return supplied


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _read_independently(module, extension: str, path: str, *,
                        sheet_name: str | None = None):
    """(values, problem). Uses the third-party API, never Solvent's reader."""
    try:
        if extension == "xlsx":
            book = module.load_workbook(path, data_only=False)
            if sheet_name is None:
                sheet = book[book.sheetnames[0]]
            elif sheet_name in book.sheetnames:
                sheet = book[sheet_name]
            else:
                return [], f"the source has no sheet named {sheet_name!r}"
            return [[cell.value for cell in row] for row in sheet.iter_rows()], ""
        if extension == "docx":
            document = module.Document(path)
            text = [p.text for p in document.paragraphs]
            # Tables are content. A review that read only paragraphs could
            # not say whether a converted document's table rows came from
            # the client's file or from nowhere, which is the single thing
            # a reader of an invoice most needs to be sure of.
            for table in getattr(document, "tables", []):
                for row in table.rows:
                    text.extend(cell.text for cell in row.cells)
            return text, ""
        if extension == "csv":
            # Let the reader work out the dialect itself. Being told the
            # delimiter by the side under review would make the second
            # opinion agree by construction on exactly the question --
            # how this file is punctuated -- that a delimiter bug gets
            # wrong.
            with open(path, newline="", encoding="utf-8",
                      errors="replace") as handle:
                sample = handle.read(_CSV_SNIFF_BYTES)
                handle.seek(0)
                dialect = module.Sniffer().sniff(sample)
                return [list(row) for row in module.reader(handle, dialect)], ""
        if extension in ("md", "markdown"):
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            parser = module.MarkdownIt("commonmark").enable("table")
            return _markdown_blocks(parser.parse(text)), ""
    except Exception as problem:   # a third-party reader may raise anything
        return [], f"{type(problem).__name__}: {problem}"
    return [], f"no independent reader is wired for .{extension}"


def _markdown_blocks(tokens) -> list[dict]:
    """The document as blocks, from the parser's token stream.

    Flattened deliberately. What the review needs to know about a report is
    which headings it really has, which rows its tables really carry and
    what its lines really say -- not the shape of the whole tree. Taking
    that from a token stream rather than from the text is the whole point:
    the tokens already know that a ``##`` inside a fence is four characters
    of code, and that an underlined line is a heading.
    """
    blocks: list[dict] = []
    row: list[str] = []
    table: list[list[str]] = []
    cell = ""
    state = ""
    level = 0
    # A list item holds a paragraph, so the paragraph that opens inside one
    # would otherwise overwrite the fact that we are in a list. Counted
    # rather than flagged, because lists nest.
    in_list_item = 0
    for token in tokens:
        kind = token.type
        if kind == "heading_open":
            state, level = "heading", int(token.tag[1:] or 0)
        elif kind == "paragraph_open":
            state = "paragraph"
        elif kind == "list_item_open":
            in_list_item += 1
            state = "list_item"
        elif kind in ("th_open", "td_open"):
            state, cell = "cell", ""
        elif kind == "tr_open":
            row = []
        elif kind == "tr_close":
            table.append(row)
        elif kind == "table_open":
            table = []
        elif kind == "table_close":
            blocks.append({"kind": "table", "rows": table, "text": ""})
            table = []
        elif kind in ("th_close", "td_close"):
            row.append(cell.strip())
            state = ""
        elif kind in ("fence", "code_block"):
            blocks.append({"kind": "code", "text": token.content.strip(),
                           "level": 0})
        elif kind == "inline":
            text = _inline_text(token)
            if state == "cell":
                cell += text
            elif state == "heading":
                blocks.append({"kind": "heading", "text": text.strip(),
                               "level": level})
            elif state in ("paragraph", "list_item"):
                blocks.append(
                    {"kind": "list_item" if in_list_item else "paragraph",
                     "text": text.strip(), "level": 0})
        elif kind == "list_item_close":
            in_list_item = max(0, in_list_item - 1)
            state = ""
        elif kind in ("heading_close", "paragraph_close"):
            state = ""
    return blocks


def _inline_text(token) -> str:
    """The visible text of an inline token, links and emphasis flattened.

    ``token.content`` is the raw source, so an escaped pipe would still look
    like a delimiter and a link would still carry its URL. The children are
    what the parser decided those characters mean.
    """
    children = getattr(token, "children", None)
    if not children:
        return token.content
    out = []
    for child in children:
        if child.type in ("text", "code_inline"):
            out.append(child.content)
        elif child.type == "softbreak":
            out.append(" ")
    return "".join(out)


def _recompute_markdown(source: str, delivered, requirements) -> list:
    """Re-derive a report's claims from the client's own facts.

    Reading the document with somebody else's parser says it is well-formed.
    It says nothing about whether the figures in it are the client's, and
    recording that as a second opinion would be the theatre this module
    exists to avoid. So this recomputes the four things a reader of a report
    is actually relying on:

    * **structure** -- the sections the client asked for are present, as
      headings the parser recognises rather than as lines that start with
      two hashes;
    * **provenance** -- every row in the table is a row the client supplied;
    * **completeness** -- every row the client supplied reached the table;
    * **arithmetic** -- the stated total is the sum of the client's own
      figures, added up here.

    The facts file is read with the standard library's ``json``. That is not
    a weakness in the independence claim: the format under review is the
    Markdown the worker rendered, and JSON has one unambiguous reading. What
    must not be shared is the reader of the artifact being judged.
    """
    from .types import check_of

    facts, rows, problem = _load_facts(source)
    if problem:
        return _recompute_markdown_against_document(source, delivered,
                                                    requirements)

    headings = [b["text"] for b in delivered if b["kind"] == "heading"]
    tables = [b["rows"] for b in delivered if b["kind"] == "table"]
    lines = [b["text"] for b in delivered
             if b["kind"] in ("list_item", "paragraph")]

    findings = []
    for requirement in requirements:
        if check_of(requirement) != "report_section":
            continue
        params = dict(getattr(requirement, "params", {}) or {})
        heading = str(params.get("heading") or "")
        kind = str(params.get("kind") or "")
        if heading:
            findings.append((
                f"the {heading!r} section is present",
                heading in headings,
                "found as a heading" if heading in headings
                else f"no heading {heading!r}; the document has {headings}"))

        if kind == "facts":
            for field in [str(f) for f in params.get("fields") or []]:
                if field not in facts:
                    continue   # not supplied; the worker says so, not this
                stated = f"{field}: {facts[field]}"
                findings.append((
                    f"{field} is rendered as the client supplied it",
                    stated in lines,
                    "matches the source" if stated in lines
                    else f"the document does not state {stated!r}"))

        if kind == "table" and rows:
            columns = [str(c) for c in params.get("columns") or []]
            supplied = [[str(row.get(c, "")) for c in columns] for row in rows]
            present = [r for table in tables for r in table[1:]]
            missing = [r for r in supplied if r not in present]
            invented = [r for r in present if r not in supplied]
            findings.append((
                "every supplied row reached the table", not missing,
                "all rows present" if not missing
                else f"{len(missing)} row(s) missing, e.g. {missing[:2]}"))
            findings.append((
                "the table invents no rows", not invented,
                "no rows the client did not supply" if not invented
                else f"{len(invented)} invented, e.g. {invented[:2]}"))

        if kind == "total" and rows:
            column = str(params.get("column") or "")
            total, countable = _sum_column(rows, column)
            if not countable:
                continue
            stated = [line for line in lines if line.startswith(f"{column} total:")]
            said = _number(stated[0].split(":", 1)[1]) if stated else None
            findings.append((
                f"the stated {column} total is the sum of the source",
                said is not None and abs(said - total) < 0.005,
                f"{said} matches the independently computed {total:.2f}"
                if said is not None and abs(said - total) < 0.005
                else f"the document says {said}; adding the source gives "
                     f"{total:.2f}"))
    return findings


def _load_facts(source: str) -> tuple[dict, list, str]:
    """``(facts, rows, problem)`` from the client's JSON."""
    import json

    try:
        data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as problem:
        return {}, [], f"{type(problem).__name__}: {problem}"
    if not isinstance(data, dict):
        return {}, [], "the source is not an object of named facts"
    rows = data.get("rows") or []
    if not all(isinstance(r, dict) for r in rows):
        return {}, [], "'rows' is not a list of objects"
    return ({k: v for k, v in data.items() if k != "rows"}, list(rows), "")


def _sum_column(rows: list, column: str) -> tuple[float, bool]:
    total, countable = 0.0, False
    for row in rows:
        value = _number(str(row.get(column, "")))
        if value is not None:
            total += value
            countable = True
    return total, countable


def _number(text: str):
    try:
        return float(str(text).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _recompute_markdown_against_document(source: str, delivered,
                                         requirements) -> list:
    """Provenance for a Markdown document rendered from another document.

    The converted-invoice case: the client sends a ``.docx``, the worker
    extracts fields and a table and renders them as Markdown. Two
    independently authored readers meet here -- python-docx on the source,
    markdown-it-py on the deliverable -- and neither is the code that did
    the work.

    What it can establish is provenance: every value the deliverable states
    is a string the client's own document contains. What it cannot
    establish from flattened text is that nothing was *omitted*, so it does
    not claim to. A finding this check does not make is left to the
    capability's own checker and to the owner, which is the honest division
    rather than a second opinion that quietly covers less than it says.
    """
    from .types import check_of

    extension = str(source).rsplit(".", 1)[-1].lower()
    module, _ = _reader_for(extension)
    if module is None:
        return []
    supplied, problem = _read_independently(module, extension, source)
    if problem or not supplied:
        return []
    haystack = {_text(value).strip() for value in supplied}
    haystack.discard("")
    blob = "\n".join(sorted(haystack))

    wanted = {check_of(r) for r in requirements}
    if not wanted & {"extract_fields", "extract_table", "apply_template",
                     "convert_format", "table_preserved", "fields_traceable"}:
        return []

    stated: list[str] = []
    for block in delivered:
        if block["kind"] == "table":
            stated.extend(cell for row in block["rows"][1:] for cell in row)
        elif block["kind"] in ("list_item", "paragraph"):
            # "label: value" states a value; prose states itself.
            stated.append(block["text"].split(":", 1)[-1].strip()
                          if ":" in block["text"] else block["text"])

    checkable = [value for value in stated
                 if value and value.upper() != "NOT SUPPLIED"
                 and not _is_structural(value)]
    if not checkable:
        return []
    invented = [value for value in checkable if value not in blob]
    return [("every value stated in the document came from the client's file",
             not invented,
             f"{len(checkable)} stated value(s), all present in the source"
             if not invented
             else f"{len(invented)} value(s) the source never had, "
                  f"e.g. {invented[:3]}")]


#: Text a rendered document adds of its own accord: headings the template
#: names, and the words a worker uses to say something was missing. Matching
#: these against the source would fail every correct document.
_STRUCTURAL = frozenset({"not supplied", "none", "n/a", "-", "total"})


def _is_structural(value: str) -> bool:
    return value.strip().lower() in _STRUCTURAL
