"""Service capability #5 — document processing. Deterministic, and no model.

A **worker**, not an authority. It reads a document, pulls out the fields a
requirement named, converts between the formats it can convert between
reliably, and fills a template the client supplied. It decides nothing about
whether the result may be delivered.

**It never invents document content.** This is the rule everything else here
follows from. A field the document does not state comes out as
:data:`NOT_FOUND`, in the deliverable, where the client can see it. A field the
document states twice with two different values comes out as
:data:`AMBIGUOUS`, naming both, because choosing one would be a coin flip
presented as an extraction. A template placeholder with no value is marked, not
filled. Nothing is inferred from context, nothing is completed from a similar
document, and no figure is computed that the document did not state.

**Extraction accuracy is not promised, and the limits are named.** A label is
found by matching text the requirement supplied against lines the document
states. That works on documents that label their fields and does not work on
documents that do not, and the report says which fields were found rather than
implying all of them were. Nobody here claims an accuracy percentage: the
honest output of an extractor is the evidence for each field it claims.

**Unsupported formats are refused by name, with the reason.** See
:data:`REFUSED_FORMATS`. PDF is the one worth reading, because it is the format
clients ask for most: a PDF's text lives in content streams whose character
mapping depends on the embedded fonts, so extraction from an arbitrary PDF is
unreliable in a way that is invisible in the output -- ligatures silently
merge, columns interleave, and a scanned page contains no text at all. An
unreliable extraction of a client's invoice is worse than a refusal, because
the refusal is visible. This capability refuses PDF rather than approximating
it, and the refusal says what would be needed instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import docx, types

VERSION = "document-processing/1.0"

#: What the deliverable says when the document does not state a field. It is a
#: value in the output rather than an omission, because an omitted field looks
#: like a field nobody asked for.
NOT_FOUND = "NOT FOUND IN SOURCE"

#: What the deliverable says when the document states a field twice, with two
#: different values. Both are named. A document that contradicts itself is a
#: question for the client, and answering it here would be a guess reported as
#: a fact.
AMBIGUOUS = "STATED MORE THAN ONCE WITH DIFFERENT VALUES"

#: Operations this capability performs.
OPERATIONS = frozenset({
    "extract_fields",      # labelled values, by labels a requirement supplied
    "extract_table",       # a table identified by the columns it must have
    "require_fields",      # fields whose absence must be disclosed, not hidden
    "convert_format",      # between the formats below, and no others
    "apply_template",      # a client's template, filled only from known values
    "structure_headings",  # heading levels carried into the output format
})

#: Checked, never performed.
VERIFY_ONLY = frozenset({
    "document_opens",         # the deliverable is readable as what it claims
    "fields_traceable",       # every extracted value is in the source verbatim
    "no_invented_content",    # every delivered line traces to source or template
    "absences_disclosed",     # a field not stated is marked, not filled
    "conversion_fidelity",    # every source paragraph is in the output, in order
    "table_preserved",        # an extracted table is the table that was there
    "headings_applied",       # declared structure is in the output
    "unsupported_declared",   # a refusal is reported and nothing partial is written
})

#: Input formats this capability reads.
READABLE = {".docx": "a Word document", ".txt": "plain text",
            ".md": "Markdown", ".markdown": "Markdown"}

#: Output formats this capability writes.
WRITABLE = {".md": "Markdown", ".txt": "plain text",
            ".docx": "a Word document", ".json": "a structured extraction"}

#: Formats refused, each with the reason. Named here rather than discovered by
#: a client, and phrased as what would be needed instead where there is one.
REFUSED_FORMATS = {
    ".pdf": ("a PDF's text lives in content streams whose character mapping "
             "depends on the fonts embedded in the file, so extraction is "
             "unreliable in ways that do not show in the output, and a scanned "
             "page contains no text at all. Converting the PDF to .docx before "
             "sending it, or supplying the original document, both work"),
    ".doc": ("the pre-2007 binary Word format is a different format from "
             ".docx and is not read here. Saving it as .docx in Word converts "
             "it losslessly"),
    ".docm": ("a macro-enabled document is not opened. Saving it as .docx "
              "removes the macros and keeps the content"),
    ".rtf": ("Rich Text Format is not read here. Saving it as .docx in any "
             "word processor converts it"),
    ".odt": ("OpenDocument Text is not read here. Exporting it as .docx "
             "converts it"),
    ".pages": "Apple Pages documents are not read here. Exporting as .docx works",
    ".xls": "a spreadsheet is spreadsheet-automation's work, not this capability's",
    ".xlsx": "a spreadsheet is spreadsheet-automation's work, not this capability's",
    ".png": ("an image contains no text to read. Recognising characters in a "
             "picture is optical character recognition, which this capability "
             "does not do and does not approximate"),
    ".jpg": ("an image contains no text to read; see the note for .png"),
    ".jpeg": ("an image contains no text to read; see the note for .png"),
    ".tif": ("an image contains no text to read; see the note for .png"),
    ".tiff": ("an image contains no text to read; see the note for .png"),
    ".html": ("markup whose rendered text depends on styling rules is not read "
              "here. Supplying the document itself works"),
    ".htm": ("markup whose rendered text depends on styling rules is not read "
             "here. Supplying the document itself works"),
}

#: A label and its value, as documents separate them.
_SEPARATORS = (":", " - ", " – ", " — ", "\t")

#: How long a document this reads. Past it, a conversation about scope.
MAX_BLOCKS = 50_000

_PIPE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_RULE_ROW = re.compile(r"^[\s|:-]+$")
_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def digest_of(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(slots=True)
class Step:
    requirement_id: str
    operation: str
    params: dict
    outcome: str = ""
    mandatory: bool = True


@dataclass(frozen=True, slots=True)
class Extracted:
    """One field, its value, and where in the document that value came from.

    ``evidence`` is the line the value was read from, verbatim. It is the whole
    basis on which anybody can believe the extraction: a value with no evidence
    is a value this module should not be reporting.
    """

    field: str
    value: str
    evidence: str = ""
    label_used: str = ""

    @property
    def found(self) -> bool:
        return self.value not in (NOT_FOUND, AMBIGUOUS)


@dataclass(slots=True)
class WorkReport:
    """What the worker did. Evidence for a human, never proof of success."""

    steps: list[Step] = field(default_factory=list)
    fields: list[Extracted] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    paragraphs_in: int = 0
    blocks_out: int = 0
    source_format: str = ""
    output_format: str = ""
    #: What the read did not carry through, in the reader's own words.
    dropped_features: list[str] = field(default_factory=list)
    #: True when the source carries revisions nobody has accepted or rejected.
    has_revisions: bool = False
    refused: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.refused

    @property
    def found(self) -> list[Extracted]:
        return [f for f in self.fields if f.found]

    @property
    def absent(self) -> list[Extracted]:
        return [f for f in self.fields if not f.found]

    def summary(self) -> str:
        lines = [f"{self.paragraphs_in} paragraph(s) read from "
                 f"{self.source_format or 'an unrecorded format'}",
                 f"{len(self.found)} of {len(self.fields)} field(s) found; "
                 f"{len(self.absent)} not stated in the document"]
        for item in self.absent:
            lines.append(f"  {item.field}: {item.value}")
        if self.tables:
            lines.append(f"{len(self.tables)} table(s) carried through")
        if self.has_revisions:
            lines.append("the source carries revisions nobody has accepted or "
                         "rejected; the text read is its current text")
        if self.dropped_features:
            lines.append("not carried through: " + "; ".join(self.dropped_features))
        lines += [f"{s.requirement_id}: {s.outcome}" for s in self.steps]
        for refusal in self.refused:
            lines.append(f"refused: {refusal}")
        return "\n".join(lines)


# ------------------------------------------------------------------ planning
_ORDER = {op: i for i, op in enumerate((
    "extract_fields", "extract_table", "require_fields", "structure_headings",
    "apply_template", "convert_format"))}


def plan(requirements: list) -> tuple[list[Step], list[str]]:
    """Committed requirements into an ordered plan, or the reason why not."""
    steps: list[Step] = []
    refusals: list[str] = []
    for requirement in requirements:
        name = types.check_of(requirement)
        params = dict(getattr(requirement, "params", {}) or {})
        rid = str(getattr(requirement, "id", "") or "")
        if name in VERIFY_ONLY:
            continue
        if not name:
            refusals.append(f"{rid}: names no check, so there is nothing to "
                            "carry out and nothing to verify")
            continue
        if name not in OPERATIONS:
            refusals.append(
                f"{rid}: {name!r} is not something this capability does. It "
                f"performs {', '.join(sorted(OPERATIONS))}")
            continue
        problem = _params_problem(name, params)
        if problem:
            refusals.append(f"{rid}: {name} cannot be carried out: {problem}")
            continue
        steps.append(Step(requirement_id=rid, operation=name, params=params,
                          mandatory=_mandatory(requirement)))
    steps.sort(key=lambda s: _ORDER[s.operation])
    return steps, refusals


def _mandatory(requirement) -> bool:
    criticality = getattr(requirement, "criticality", "MANDATORY")
    blocks = getattr(criticality, "blocks_delivery", None)
    if blocks is not None:
        return bool(blocks)
    return str(criticality) != "ADVISORY"


def _params_problem(op: str, params: dict) -> str:
    if op == "extract_fields":
        labels = params.get("labels")
        if not isinstance(labels, dict) or not labels:
            return ("no field labels were given, and which text in a document "
                    "is a field label is not something to guess")
        for name, candidates in labels.items():
            if not str(name).strip():
                return "a label was given for a field with no name"
            if isinstance(candidates, str):
                candidates = [candidates]
            if not isinstance(candidates, (list, tuple)) or not candidates:
                return f"no label text was given for {name!r}"
            if any(not str(c).strip() for c in candidates):
                return f"a blank label was given for {name!r}"
        return ""
    if op == "extract_table":
        columns = params.get("columns")
        if not isinstance(columns, (list, tuple)) or not columns:
            return ("no columns were named, so which table in the document is "
                    "the one asked for cannot be established")
        return ""
    if op == "require_fields":
        fields = params.get("fields")
        if not isinstance(fields, (list, tuple)) or not fields:
            return "no fields were named"
        return ""
    if op == "convert_format":
        target = str(params.get("to", "")).strip().lower()
        if not target:
            return "no target format was named"
        if f".{target.lstrip('.')}" not in WRITABLE:
            return (f"{target!r} is not a format this capability writes. It "
                    f"writes {', '.join(sorted(WRITABLE))}")
        return ""
    if op == "apply_template":
        blocks = params.get("blocks")
        if not isinstance(blocks, (list, tuple)) or not blocks:
            return "the template has no blocks in it"
        for block in blocks:
            if not isinstance(block, dict):
                return "a template block is not an object with text and a style"
            style = str(block.get("style", "Normal"))
            if style not in docx._STYLES and block.get("kind") != "table":
                return (f"the template names a style {style!r} that no output "
                        f"format defines. Available: {', '.join(docx._STYLES)}")
        return ""
    if op == "structure_headings":
        levels = params.get("levels")
        if levels is not None and not isinstance(levels, dict):
            return "the heading levels were given as something other than a map"
        return ""
    return f"{op} has no parameter contract, which is a defect in this module"


# -------------------------------------------------------------------- reading
class _Unreadable(Exception):
    pass


@dataclass(slots=True)
class Source:
    """A document as blocks, whatever format it arrived in."""

    blocks: list = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    has_revisions: bool = False
    kind: str = ""

    @property
    def paragraphs(self):
        return [b for b in self.blocks if isinstance(b, docx.Paragraph)]

    @property
    def tables(self):
        return [b for b in self.blocks if isinstance(b, docx.Table)]

    def lines(self) -> list[str]:
        out = []
        for block in self.blocks:
            if isinstance(block, docx.Paragraph):
                if block.text:
                    out.append(block.text)
            else:
                for row in block.rows:
                    out += [cell for cell in row if cell]
                    if len(row) >= 2 and row[0] and row[1]:
                        out.append(f"{row[0]}: {row[1]}")
        return out


def refusal_for(suffix: str) -> str:
    """Why this format is not read, or ``""`` when it is."""
    suffix = suffix.lower()
    if suffix in READABLE:
        return ""
    if suffix in REFUSED_FORMATS:
        return f"{suffix} is not a format this capability reads: " \
               f"{REFUSED_FORMATS[suffix]}"
    return (f"{suffix or 'a file with no extension'} is not a format this "
            f"capability reads. It reads {', '.join(sorted(READABLE))}")


def read_source(path: str | Path) -> Source:
    """Read a document into blocks, or refuse it by name with the reason."""
    path = Path(path)
    problem = refusal_for(path.suffix)
    if problem:
        raise _Unreadable(problem)
    raw = path.read_bytes()
    if path.suffix.lower() == ".docx":
        try:
            document = docx.read_document(raw, max_blocks=MAX_BLOCKS)
        except docx.UnsupportedDocument as exc:
            raise _Unreadable(str(exc)) from exc
        return Source(blocks=list(document.blocks), dropped=list(document.dropped),
                      has_revisions=document.has_revisions,
                      kind="a Word document")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _Unreadable(f"the file is not UTF-8 text: {exc}") from exc
    if path.suffix.lower() in (".md", ".markdown"):
        return _read_markdown(text)
    return Source(blocks=[docx.Paragraph(text=line.rstrip())
                          for line in text.splitlines()],
                  kind="plain text")


def _read_markdown(text: str) -> Source:
    """Markdown as blocks. ATX headings and pipe tables; everything else is text.

    Deliberately a small subset. A full Markdown parser would make this module
    the authority on what a client's document means, and the formats it
    converts between are the ones it can convert between reliably.
    """
    blocks, dropped = [], set()
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        heading = _ATX.match(line)
        if heading:
            hashes, body = heading.groups()
            blocks.append(docx.Paragraph(
                text=body.strip(), style=_style_for_level(len(hashes) - 1),
                level=len(hashes) - 1))
            index += 1
            continue
        if _PIPE_ROW.match(line):
            rows, index = _pipe_table(lines, index)
            blocks.append(docx.Table(rows=rows))
            continue
        if line.startswith(("```", "~~~")):
            dropped.add("a fenced code block, whose contents are carried "
                        "through as plain lines")
            index += 1
            continue
        if line.strip():
            blocks.append(docx.Paragraph(text=line.strip()))
        index += 1
    return Source(blocks=blocks, dropped=sorted(dropped), kind="Markdown")


def _pipe_table(lines, index) -> tuple[tuple, int]:
    rows = []
    while index < len(lines):
        match = _PIPE_ROW.match(lines[index].rstrip())
        if not match:
            break
        inner = match.group(1)
        if not _RULE_ROW.match(inner):
            rows.append(tuple(_cells(inner)))
        index += 1
    return tuple(rows), index


def _cells(inner: str) -> list[str]:
    """Split a pipe-table row, honouring a backslash-escaped pipe.

    ``str.split("|")`` cuts a cell containing a pipe in half, and the writer
    escapes pipes precisely so that a client's "x | y" survives. A reader that
    does not unescape makes the writer's care pointless and silently adds a
    column.
    """
    out, current, escaped = [], [], False
    for character in inner:
        if escaped:
            current.append(character if character == "|" else "\\" + character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            out.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    out.append("".join(current).strip())
    return out


def _style_for_level(level: int) -> str:
    if level <= 0:
        return "Title"
    return f"Heading{min(level, 6)}"


def _level_for_style(style: str) -> int | None:
    if style == "Title":
        return 0
    if style.startswith("Heading"):
        try:
            return int(style[len("Heading"):].strip())
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------- extraction
def extract(labels: dict, lines: list[str]) -> list[Extracted]:
    """Pull labelled values out of the lines a document states.

    A label matches at the start of a line, followed by one of the separators
    documents use. Matching is case-insensitive and tolerant of spacing,
    because a document writes "Invoice Number:" and a brief says "invoice
    number", and neither is wrong.

    Three outcomes and no fourth. One distinct value is the value. No match is
    :data:`NOT_FOUND`. Two different values under the same label is
    :data:`AMBIGUOUS`, with both named -- the document contradicts itself, and
    resolving that is a question for the client rather than a decision to make
    quietly in their deliverable.
    """
    out = []
    for name, candidates in labels.items():
        if isinstance(candidates, str):
            candidates = [candidates]
        hits: list[tuple[str, str, str]] = []
        for candidate in candidates:
            for line in lines:
                value = _value_after(str(candidate), line)
                if value is not None and value != "":
                    hits.append((value, line, str(candidate)))
        distinct = []
        for value, line, candidate in hits:
            if value not in [v for v, _, _ in distinct]:
                distinct.append((value, line, candidate))
        if not distinct:
            out.append(Extracted(field=str(name), value=NOT_FOUND))
        elif len(distinct) == 1:
            value, line, candidate = distinct[0]
            out.append(Extracted(field=str(name), value=value, evidence=line,
                                 label_used=candidate))
        else:
            out.append(Extracted(
                field=str(name), value=AMBIGUOUS,
                evidence=" | ".join(line for _, line, _ in distinct),
                label_used=distinct[0][2]))
    return out


def _value_after(label: str, line: str) -> str | None:
    """The value a line states for this label, or None if it does not state one."""
    text = str(line)
    wanted = _loose(label)
    for separator in _SEPARATORS:
        position = text.find(separator)
        while position != -1:
            head = text[:position]
            if _loose(head) == wanted:
                return text[position + len(separator):].strip()
            position = text.find(separator, position + 1)
    return None


def _loose(text: str) -> str:
    """A label, comparably: case-folded, spacing collapsed, punctuation trimmed."""
    return " ".join(str(text).split()).strip(" \t.:-–—").casefold()


def find_table(columns: list[str], tables: list) -> tuple[int, str]:
    """``(index, why)`` of the table whose header row has these columns.

    Refuses rather than picks when two tables match. A document with two tables
    sharing a header is a document whose brief needs to say which one, and
    choosing the first would be a guess that reads as a result.
    """
    wanted = [_loose(c) for c in columns]
    matches = []
    for index, table in enumerate(tables):
        header = [_loose(c) for c in table.header]
        if all(column in header for column in wanted):
            matches.append(index)
    if not matches:
        return -1, (f"no table in the document has a header row with "
                    f"{', '.join(str(c) for c in columns)}")
    if len(matches) > 1:
        return -1, (f"{len(matches)} tables in the document have a header row "
                    f"with {', '.join(str(c) for c in columns)}, and which one "
                    "was meant is a question for the client")
    return matches[0], ""


# ------------------------------------------------------------------ execution
def execute(*, source: str | Path, destination: str | Path,
            requirements: list) -> WorkReport:
    """Carry out the plan. Returns what was done; proves nothing.

    Nothing is written when anything was refused. A document job that produced
    half a deliverable would be the worst of both: a client with a file that
    looks finished and a capability that knew it was not.
    """
    report = WorkReport()
    source, destination = Path(source), Path(destination)
    steps, refusals = plan(requirements)
    report.steps, report.refused = steps, list(refusals)
    report.output_format = WRITABLE.get(destination.suffix.lower(), "")
    if not report.output_format:
        report.refused.append(
            f"{destination.suffix or 'a file with no extension'} is not a "
            f"format this capability writes. It writes "
            f"{', '.join(sorted(WRITABLE))}")

    try:
        document = read_source(source)
    except _Unreadable as exc:
        report.refused.append(f"the source cannot be processed: {exc}")
        return report
    except OSError as exc:
        report.refused.append(f"the source could not be read: {exc}")
        return report

    report.source_format = document.kind
    report.paragraphs_in = len(document.paragraphs)
    report.dropped_features = list(document.dropped)
    report.has_revisions = document.has_revisions

    convert = next((s for s in steps if s.operation == "convert_format"), None)
    template = next((s for s in steps if s.operation == "apply_template"), None)
    extracting = [s for s in steps
                  if s.operation in ("extract_fields", "extract_table")]

    if not (convert or template or extracting):
        report.refused.append(
            "no requirement says what to do with the document: name an "
            "extraction, a conversion or a template")
    if convert and template:
        report.refused.append(
            "one requirement asks for the document converted and another for a "
            "template filled from it, which are two different deliverables")
    if extracting and not (template or destination.suffix.lower() == ".json"):
        report.refused.append(
            "an extraction has nowhere to go: deliver it to .json, or name a "
            "template that puts the fields in a document")
    if convert:
        target = f".{str(convert.params['to']).lstrip('.').lower()}"
        if target != destination.suffix.lower():
            report.refused.append(
                f"the conversion asks for {target} and the deliverable is "
                f"{destination.suffix}; two requirements disagree about the "
                "format the client receives")
    if report.refused:
        return report

    lines = document.lines()
    for step in extracting:
        if step.operation == "extract_fields":
            report.fields += extract(step.params["labels"], lines)
            step.outcome = (
                f"{len([f for f in report.fields if f.found])} of "
                f"{len(report.fields)} field(s) found in the document")
        else:
            index, why = find_table(list(step.params["columns"]),
                                    document.tables)
            if index < 0:
                report.refused.append(f"{step.requirement_id}: {why}")
            else:
                table = document.tables[index]
                report.tables.append([list(row) for row in table.rows])
                step.outcome = (f"a table of {len(table.rows)} row(s) carried "
                                "through from the document")

    for step in (s for s in steps if s.operation == "require_fields"):
        named = [str(f) for f in step.params["fields"]]
        known = {f.field for f in report.fields}
        unasked = [f for f in named if f not in known]
        if unasked:
            report.refused.append(
                f"{step.requirement_id}: require_fields names field(s) no "
                f"extraction looks for: {', '.join(unasked)}")
        else:
            absent = [f.field for f in report.absent if f.field in named]
            step.outcome = (
                "every required field is stated in the document" if not absent
                else f"{len(absent)} required field(s) are not stated in the "
                     f"document and are disclosed as such: {', '.join(absent)}")

    if report.refused:
        return report

    if template is not None:
        blocks = _fill_template(template.params["blocks"], report)
        template.outcome = f"{len(blocks)} template block(s) filled"
    elif convert is not None:
        blocks = _carry_through(document, steps)
        convert.outcome = (f"{len(blocks)} block(s) carried through to "
                           f"{report.output_format}")
    else:
        blocks = []

    try:
        _write(destination, blocks, report)
    except docx.UnsupportedDocument as exc:
        report.refused.append(f"the deliverable could not be written: {exc}")
        return report
    report.blocks_out = len(blocks) if blocks else len(report.fields)
    return report


def _fill_template(blocks: list, report: WorkReport) -> list:
    """The client's template, filled only from values the document stated."""
    values = {f.field: f.value for f in report.fields}
    out = []
    for block in blocks:
        if block.get("kind") == "table":
            rows = block.get("rows")
            if rows == "extracted":
                for table in report.tables:
                    out.append(docx.Out(kind="table",
                                        rows=tuple(tuple(r) for r in table)))
                continue
            out.append(docx.Out(kind="table",
                                rows=tuple(tuple(str(c) for c in row)
                                           for row in (rows or []))))
            continue
        text = _substitute(str(block.get("text", "")), values)
        out.append(docx.Out(text=text, style=str(block.get("style", "Normal"))))
    return out


def _substitute(text: str, values: dict) -> str:
    """Replace ``{Field}`` with its value; a field with no value is marked.

    An unknown placeholder is left as it was written rather than emptied. A
    template that refers to something this job never extracted is a mistake in
    the template, and silently deleting the reference hides it.
    """
    def one(match):
        name = match.group(1).strip()
        if name in values:
            return values[name]
        return match.group(0)
    return _PLACEHOLDER.sub(one, text)


def _carry_through(document: Source, steps: list[Step]) -> list:
    """The source's blocks as output blocks, with heading levels mapped."""
    levels = {}
    for step in steps:
        if step.operation == "structure_headings":
            levels = {str(k): int(v)
                      for k, v in (step.params.get("levels") or {}).items()}
    out = []
    for block in document.blocks:
        if isinstance(block, docx.Table):
            out.append(docx.Out(kind="table",
                                rows=tuple(tuple(row) for row in block.rows)))
            continue
        if not block.text:
            continue
        level = block.level
        if block.style in levels:
            level = levels[block.style]
        style = _style_for_level(level) if level is not None else "Normal"
        out.append(docx.Out(text=block.text, style=style))
    return out


def _write(destination: Path, blocks: list, report: WorkReport) -> None:
    suffix = destination.suffix.lower()
    if suffix == ".json":
        destination.write_text(json.dumps({
            "capability": VERSION,
            "source_format": report.source_format,
            "fields": [{"field": f.field, "value": f.value,
                        "evidence": f.evidence, "label": f.label_used,
                        "found": f.found} for f in report.fields],
            "tables": report.tables,
            "not_stated": [f.field for f in report.absent],
            "source_carries_unaccepted_revisions": report.has_revisions,
            "not_carried_through": report.dropped_features,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    if suffix == ".docx":
        destination.write_bytes(docx.write_document(blocks))
        return
    markdown = suffix in (".md", ".markdown")
    if not markdown:
        if any(_level_for_style(b.style) is not None for b in blocks
               if b.kind != "table"):
            report.dropped_features.append(
                "heading levels, which plain text cannot express: the heading "
                "text is carried through and its level is not")
        if any(b.kind == "table" for b in blocks):
            report.dropped_features.append(
                "table structure, which plain text cannot express: the cells "
                "are carried through tab-separated and the table is not")
    destination.write_text(_as_text(blocks, markdown=markdown),
                           encoding="utf-8")


def _as_text(blocks: list, *, markdown: bool) -> str:
    lines = []
    for block in blocks:
        if block.kind == "table":
            lines += _table_lines(block.rows, markdown=markdown)
            lines.append("")
            continue
        level = _level_for_style(block.style)
        if markdown and level is not None:
            lines.append(f"{'#' * (level + 1)} {block.text}")
        else:
            lines.append(block.text)
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _table_lines(rows, *, markdown: bool) -> list[str]:
    if not rows:
        return []
    if not markdown:
        return ["\t".join(str(cell) for cell in row) for row in rows]
    width = max(len(row) for row in rows)
    out = ["| " + " | ".join(_pipe_safe(rows[0], width)) + " |",
           "|" + "|".join("---" for _ in range(width)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(_pipe_safe(row, width)) + " |")
    return out


def _pipe_safe(row, width: int) -> list[str]:
    """Cells padded to the table's width, with any pipe escaped."""
    cells = [str(cell).replace("|", "\\|") for cell in row]
    return cells + [""] * (width - len(cells))
