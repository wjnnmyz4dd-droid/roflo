"""Independent verification of document work. It does not trust the worker.

A **verifier**, and the point of it is that it does not import
:mod:`solvent.docwork`. It reads the source document and the deliverable, works
out for itself what the committed requirements demanded, and says whether the
deliverable satisfies them. If it shared the worker's extraction, two copies of
one bug would agree and the agreement would be reported as certification.

What that independence covers and what it does not, stated plainly:

* **The extraction is independent.** The label matcher here is written
  separately, by a different route: it splits a line at its separators and
  compares the head, where the worker scans for a label and checks what
  follows. Two routes to one specification is the point.
* **Every claimed value is checked against the document.** A field this module
  reports as extracted must appear verbatim in a line the source states. A
  value nobody can find in the document is the defect that matters most here,
  because it is indistinguishable from a correct extraction by reading the
  deliverable alone.
* **The format layer is shared.** Both sides read ``.docx`` through
  :mod:`solvent.docx`, exactly as both read CSV through the standard library's
  ``csv``. A bug there would fool both, which is why that module carries its
  own adversarial suite and its own cross-validation against another library.

Three verdicts and no fourth. ``PASS`` means checked and correct. ``FAIL``
means checked and wrong. ``UNVERIFIABLE`` means this module could not tell, and
it is reported as its own answer rather than folded into either of the others.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import docx, types

PASS, FAIL, UNVERIFIABLE = "PASS", "FAIL", "UNVERIFIABLE"

#: What a deliverable says when a field is not stated, and when it is stated
#: twice differently. Repeated here rather than imported: these strings are the
#: contract between the capability and the client, and a verifier that read
#: them from the worker could not notice the worker changing them.
NOT_FOUND = "NOT FOUND IN SOURCE"
AMBIGUOUS = "STATED MORE THAN ONCE WITH DIFFERENT VALUES"
_MARKERS = (NOT_FOUND, AMBIGUOUS)

CHECKS = frozenset({
    "extract_fields", "extract_table", "require_fields", "convert_format",
    "apply_template", "structure_headings", "document_opens",
    "fields_traceable", "no_invented_content", "absences_disclosed",
    "conversion_fidelity", "table_preserved", "headings_applied",
    "unsupported_declared",
})

#: Checks that need the whole brief rather than their own parameters.
WHOLE_JOB = frozenset({"no_invented_content", "absences_disclosed",
                       "fields_traceable", "conversion_fidelity",
                       "unsupported_declared"})

_READABLE = (".docx", ".txt", ".md", ".markdown")
_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_PIPE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_RULE_ROW = re.compile(r"^[\s|:-]+$")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_SEPARATORS = (":", " - ", " – ", " — ", "\t")


@dataclass
class Finding:
    requirement_id: str
    check: str
    verdict: str
    detail: str
    evidence: str = ""

    @property
    def result(self):
        """The pipeline's verdict type for this finding.

        Two vocabularies for one answer, and they have to be the same answer.
        This capability reports PASS / FAIL / UNVERIFIABLE; the job pipeline
        reads :class:`solvent.types.CheckResult`. Deriving one from the other
        rather than storing both means they cannot drift apart -- and they are
        the same three words deliberately, because UNVERIFIABLE blocks delivery
        in the pipeline exactly as it refuses an opinion here.
        """
        return types.CheckResult(self.verdict)

    @property
    def computed(self) -> dict:
        """What was measured, for the audit record the pipeline writes."""
        return {"requirement": self.requirement_id, "evidence": self.evidence}

    @property
    def passed(self) -> bool:
        return self.verdict == PASS


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        kinds = {f.verdict for f in self.findings}
        if not kinds:
            return UNVERIFIABLE
        if FAIL in kinds:
            return FAIL
        if UNVERIFIABLE in kinds:
            return UNVERIFIABLE
        return PASS

    def summary(self) -> str:
        lines = [f"{self.verdict}: {len(self.findings)} check(s)"]
        lines += [f"  {f.check} [{f.verdict}] {f.detail}" for f in self.findings]
        return "\n".join(lines)


# ------------------------------------------------------------- reading, again
class _Unreadable(Exception):
    pass


@dataclass(slots=True)
class Read:
    """A document as the lines and tables it states."""

    paragraphs: list = field(default_factory=list)   # (text, level)
    tables: list = field(default_factory=list)       # list of row tuples
    kind: str = ""

    def lines(self) -> list[str]:
        out = [text for text, _ in self.paragraphs if text]
        for rows in self.tables:
            for row in rows:
                out += [cell for cell in row if cell]
                if len(row) >= 2 and row[0] and row[1]:
                    out.append(f"{row[0]}: {row[1]}")
        return out


def read_any(path: str | Path) -> Read:
    """Read a document, or say why it cannot be read. Written here, separately."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in _READABLE:
        raise _Unreadable(f"{suffix or 'a file with no extension'} is not a "
                          "format this verifier reads")
    raw = path.read_bytes()
    if suffix == ".docx":
        try:
            document = docx.read_document(raw)
        except docx.UnsupportedDocument as exc:
            raise _Unreadable(str(exc)) from exc
        return Read(paragraphs=[(p.text, p.level) for p in document.paragraphs],
                    tables=[list(t.rows) for t in document.tables],
                    kind="a Word document")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _Unreadable(f"the file is not UTF-8 text: {exc}") from exc
    if suffix in (".md", ".markdown"):
        return _read_markdown(text)
    return Read(paragraphs=[(line.rstrip(), None) for line in text.splitlines()],
                kind="plain text")


def _read_markdown(text: str) -> Read:
    paragraphs, tables = [], []
    pending: list[tuple] = []
    for line in text.splitlines():
        stripped = line.rstrip()
        row = _PIPE_ROW.match(stripped)
        if row:
            inner = row.group(1)
            if not _RULE_ROW.match(inner):
                pending.append(tuple(c.strip().replace("\\|", "|")
                                     for c in inner.split("|")))
            continue
        if pending:
            tables.append(pending)
            pending = []
        heading = _ATX.match(stripped)
        if heading:
            hashes, body = heading.groups()
            paragraphs.append((body.strip(), len(hashes) - 1))
        elif stripped.strip():
            paragraphs.append((stripped.strip(), None))
    if pending:
        tables.append(pending)
    return Read(paragraphs=paragraphs, tables=tables, kind="Markdown")


def read_extraction(path: str | Path) -> dict:
    """The JSON deliverable, or an explanation of why it is not one."""
    try:
        parsed = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Unreadable(f"{path} is not a readable JSON extraction: "
                          f"{exc}") from exc
    if not isinstance(parsed, dict) or "fields" not in parsed:
        raise _Unreadable(f"{path} is JSON but not an extraction: it has no "
                          "fields list")
    if not isinstance(parsed["fields"], list):
        raise _Unreadable(f"{path} has a fields entry that is not a list")
    return parsed


# ------------------------------------------------------- extraction, again
def value_for(label: str, line: str) -> str | None:
    """What this line states for this label, or None.

    Written as a split-and-compare: the line is cut at each separator it
    contains and the head is compared with the label. The worker scans for the
    label and reads what follows. Same specification, opposite direction.
    """
    text = str(line)
    target = loose(label)
    for separator in _SEPARATORS:
        if separator not in text:
            continue
        parts = text.split(separator)
        for cut in range(1, len(parts)):
            head = separator.join(parts[:cut])
            if loose(head) == target:
                return separator.join(parts[cut:]).strip()
    return None


def loose(text: str) -> str:
    return " ".join(str(text).split()).strip(" \t.:-–—").casefold()


def extract(labels: dict, lines: list[str]) -> dict[str, str]:
    """``{field: value}`` with the markers for absence and contradiction."""
    out = {}
    for name, candidates in labels.items():
        if isinstance(candidates, str):
            candidates = [candidates]
        values = []
        for candidate in candidates:
            for line in lines:
                found = value_for(str(candidate), line)
                if found:
                    if found not in values:
                        values.append(found)
        if not values:
            out[str(name)] = NOT_FOUND
        elif len(values) == 1:
            out[str(name)] = values[0]
        else:
            out[str(name)] = AMBIGUOUS
    return out


def tables_matching(columns: list[str], tables: list) -> list[int]:
    wanted = [loose(c) for c in columns]
    found = []
    for index, rows in enumerate(tables):
        header = [loose(c) for c in (rows[0] if rows else ())]
        if all(column in header for column in wanted):
            found.append(index)
    return found


# ---------------------------------------------------------------- the brief
def job_spec(requirements: list) -> dict:
    """The whole brief as one structure, from the committed requirements."""
    spec = {"labels": {}, "columns": [], "required": [], "template": [],
            "convert_to": "", "levels": {}}
    for requirement in requirements:
        check = types.check_of(requirement)
        params = dict(getattr(requirement, "params", {}) or {})
        if check == "extract_fields":
            spec["labels"].update(params.get("labels") or {})
        elif check == "extract_table":
            spec["columns"] = list(params.get("columns") or [])
        elif check == "require_fields":
            spec["required"] += [str(f) for f in (params.get("fields") or [])]
        elif check == "apply_template":
            spec["template"] = list(params.get("blocks") or [])
        elif check == "convert_format":
            spec["convert_to"] = f".{str(params.get('to', '')).lstrip('.').lower()}"
        elif check == "structure_headings":
            spec["levels"] = dict(params.get("levels") or {})
    return spec


def verify(*, source: str | Path, output: str | Path,
           requirements: list) -> Report:
    """Check a delivered document job against its committed requirements."""
    report = Report()
    spec = job_spec(requirements)
    output = Path(output)

    try:
        document = read_any(source)
    except (_Unreadable, OSError) as exc:
        # An unreadable source is not automatically a failure: the brief may
        # have named a format this capability refuses, and then the correct
        # outcome is a refusal with no deliverable.
        return _unsupported_source(requirements, output, str(exc))

    lines = document.lines()
    extraction, delivered = None, None
    trouble = ""
    if output.suffix.lower() == ".json":
        try:
            extraction = read_extraction(output)
        except _Unreadable as exc:
            trouble = str(exc)
    else:
        try:
            delivered = read_any(output)
        except (_Unreadable, OSError) as exc:
            trouble = str(exc)

    for requirement in requirements:
        check = types.check_of(requirement)
        rid = str(getattr(requirement, "id", "") or "")
        params = dict(getattr(requirement, "params", {}) or {})
        if not check:
            continue
        if check not in CHECKS:
            report.findings.append(Finding(
                rid, check, UNVERIFIABLE,
                f"{check!r} is not a check this verifier performs, so this "
                "requirement has not been checked by anybody"))
            continue
        if trouble and check != "unsupported_declared":
            report.findings.append(Finding(
                rid, check, FAIL,
                f"the deliverable could not be read, so nothing in it "
                f"satisfies this requirement: {trouble}"))
            continue
        report.findings.append(_decide(
            rid, check, params, spec, document, lines, extraction, delivered,
            output))

    if not report.findings:
        report.findings.append(Finding(
            "", "document_opens", UNVERIFIABLE,
            "no requirement named a check, so there was nothing to verify"))
    return report


def _unsupported_source(requirements, output: Path, why: str) -> Report:
    """A source this capability does not read. Was the refusal honoured?"""
    report = Report()
    wrote = output.exists() and output.stat().st_size > 0
    for requirement in requirements:
        check = types.check_of(requirement)
        rid = str(getattr(requirement, "id", "") or "")
        if check == "unsupported_declared":
            report.findings.append(Finding(
                rid, check, FAIL if wrote else PASS,
                (f"a deliverable was written from a source that cannot be "
                 f"processed ({why})") if wrote else
                (f"the source cannot be processed and no deliverable was "
                 f"written: {why}"), why))
        elif check:
            report.findings.append(Finding(
                rid, check, UNVERIFIABLE,
                f"the source cannot be read, so nothing can be compared "
                f"against it: {why}"))
    if not report.findings:
        report.findings.append(Finding(
            "", "unsupported_declared", FAIL if wrote else PASS,
            f"the source cannot be processed: {why}"))
    return report


def _decide(rid, check, params, spec, document, lines, extraction, delivered,
            output) -> Finding:
    if check == "document_opens":
        if extraction is not None:
            return Finding(rid, check, PASS,
                           f"the deliverable is a readable extraction of "
                           f"{len(extraction['fields'])} field(s)")
        return Finding(rid, check, PASS,
                       f"the deliverable opens as {delivered.kind}: "
                       f"{len(delivered.paragraphs)} paragraph(s), "
                       f"{len(delivered.tables)} table(s)")

    if check == "unsupported_declared":
        return Finding(rid, check, PASS,
                       f"the source is {document.kind}, which this capability "
                       "reads, so there was no refusal to honour")

    if check == "extract_fields":
        return _extracted(rid, check, params, lines, extraction, delivered, spec)
    if check == "fields_traceable":
        return _traceable(rid, check, spec, lines, extraction, delivered)
    if check == "absences_disclosed":
        return _disclosed(rid, check, spec, lines, extraction, delivered)
    if check == "require_fields":
        return _required(rid, check, params, spec, lines, extraction, delivered)
    if check == "extract_table":
        return _table(rid, check, params, document, extraction, delivered)
    if check == "table_preserved":
        return _table_preserved(rid, check, spec, document, extraction, delivered)
    if check == "convert_format":
        return _converted(rid, check, params, output, delivered)
    if check == "conversion_fidelity":
        return _fidelity(rid, check, spec, document, delivered)
    if check == "headings_applied":
        return _headings(rid, check, spec, document, delivered)
    if check == "apply_template":
        return _templated(rid, check, params, spec, lines, delivered)
    if check == "no_invented_content":
        return _not_invented(rid, check, spec, lines, document, extraction,
                             delivered)
    if check == "structure_headings":
        return _headings(rid, check, spec, document, delivered)
    return Finding(rid, check, UNVERIFIABLE,
                   f"{check!r} is in the check list and has no implementation, "
                   "which is a defect in this module")


def _claimed(extraction, delivered, spec) -> dict[str, str]:
    """What the deliverable says each field is, whichever shape it took.

    For an extraction the deliverable states each field directly. For a filled
    template it does not: the values are inside the template's own sentences,
    so each literal is turned into a pattern and the delivered line is read
    back through it.
    """
    if extraction is not None:
        return {str(f.get("field")): str(f.get("value", ""))
                for f in extraction["fields"]}
    values = {}
    text = "\n".join(t for t, _ in (delivered.paragraphs if delivered else []))
    for block in spec["template"]:
        literal = str(block.get("text", ""))
        names = _ordered_unique(_PLACEHOLDER.findall(literal))
        if not names:
            continue
        pattern, groups = _template_pattern(literal, names)
        match = re.search(pattern, text)
        if match:
            for index, name in enumerate(groups):
                values[name] = match.group("f%d" % index).strip()
    return values


def _ordered_unique(names) -> list[str]:
    out = []
    for name in names:
        trimmed = str(name).strip()
        if trimmed not in out:
            out.append(trimmed)
    return out


def _template_pattern(literal: str, names: list[str]) -> tuple[str, list[str]]:
    """``(pattern, group order)`` for reading a filled template line back.

    A placeholder used twice gets one capture group and a backreference, not
    two groups with the same name -- which is a regex error, so a template
    that mentioned a field in both its heading and its body made this function
    raise rather than return. Backreference rather than a second group because
    the same placeholder must have been filled with the same value, and a
    pattern that allowed two different ones would read a corrupted deliverable
    as a fine one.
    """
    pattern = re.escape(literal)
    for index, name in enumerate(names):
        token = re.escape("{" + name + "}")
        spaced = re.escape("{" + name + " }")
        for form in (token, spaced):
            first = pattern.find(form)
            if first == -1:
                continue
            pattern = (pattern[:first] + "(?P<f%d>.*)" % index
                       + pattern[first + len(form):].replace(
                           form, "(?P=f%d)" % index))
    return pattern, names


def _extracted(rid, check, params, lines, extraction, delivered, spec) -> Finding:
    """Re-extract independently and compare, field by field."""
    labels = params.get("labels") or {}
    expected = extract(labels, lines)
    claimed = _claimed(extraction, delivered, spec)
    missing = [name for name in expected if name not in claimed]
    if missing:
        return Finding(rid, check, FAIL,
                       f"the deliverable does not state field(s): "
                       f"{', '.join(missing)}")
    for name, want in expected.items():
        got = claimed[name]
        if want == AMBIGUOUS:
            if got != AMBIGUOUS:
                return Finding(
                    rid, check, FAIL,
                    f"{name} is stated more than once in the document with "
                    f"different values, and the deliverable reports {got!r} as "
                    "though the document were unambiguous",
                    "choosing between two stated values is a coin flip "
                    "presented as an extraction")
            continue
        if got != want:
            return Finding(rid, check, FAIL,
                           f"{name} is delivered as {got!r} where the document "
                           f"states {want!r}")
    return Finding(rid, check, PASS,
                   f"{len(expected)} field(s) re-extracted independently and "
                   "every value agrees with the document")


def _traceable(rid, check, spec, lines, extraction, delivered) -> Finding:
    """Every value the deliverable claims appears verbatim in the document.

    The defect this catches is a value nobody can find in the source. Reading
    the deliverable alone, it is indistinguishable from a correct extraction,
    which is exactly why it has to be checked against the document rather than
    for internal consistency.
    """
    claimed = _claimed(extraction, delivered, spec)
    if not claimed:
        return Finding(rid, check, UNVERIFIABLE,
                       "the deliverable states no extracted field, so there is "
                       "nothing to trace")
    haystack = "\n".join(lines)
    untraceable = []
    for name, value in claimed.items():
        if value in _MARKERS or not value.strip():
            continue
        if value not in haystack:
            untraceable.append(f"{name}={value!r}")
    if untraceable:
        return Finding(
            rid, check, FAIL,
            f"{len(untraceable)} delivered value(s) do not appear anywhere in "
            f"the document. First: {untraceable[0]}",
            "; ".join(untraceable[:5]))
    if extraction is not None:
        for entry in extraction["fields"]:
            value, evidence = str(entry.get("value", "")), str(
                entry.get("evidence", ""))
            if value in _MARKERS or not value.strip():
                continue
            if not evidence.strip():
                return Finding(rid, check, FAIL,
                               f"{entry.get('field')} is reported as extracted "
                               "with no evidence line, so nothing supports it")
            if evidence not in haystack and " | " not in evidence:
                return Finding(rid, check, FAIL,
                               f"{entry.get('field')} cites a line the document "
                               f"does not contain: {evidence!r}")
    return Finding(rid, check, PASS,
                   f"every one of {len(claimed)} delivered value(s) appears "
                   "verbatim in a line the document states")


def _disclosed(rid, check, spec, lines, extraction, delivered) -> Finding:
    """A field the document does not state is marked, not filled."""
    if not spec["labels"]:
        return Finding(rid, check, UNVERIFIABLE,
                       "no requirement named any field to extract, so there is "
                       "no absence to disclose")
    expected = extract(spec["labels"], lines)
    claimed = _claimed(extraction, delivered, spec)
    for name, want in expected.items():
        if want not in _MARKERS:
            continue
        got = claimed.get(name)
        if got is None:
            return Finding(rid, check, FAIL,
                           f"{name} is not stated in the document and the "
                           "deliverable does not mention it at all")
        if got != want:
            return Finding(
                rid, check, FAIL,
                f"{name} is not stated in the document and the deliverable "
                f"gives it as {got!r}",
                "a gap filled with something plausible is the one defect a "
                "client cannot see")
    absent = [n for n, v in expected.items() if v in _MARKERS]
    return Finding(rid, check, PASS,
                   f"{len(absent)} field(s) the document does not state are "
                   "disclosed as such" if absent else
                   "the document states every field asked for, so there is no "
                   "absence to disclose")


def _required(rid, check, params, spec, lines, extraction, delivered) -> Finding:
    named = [str(f) for f in params.get("fields", [])]
    expected = extract(spec["labels"], lines)
    claimed = _claimed(extraction, delivered, spec)
    for name in named:
        if name not in claimed:
            return Finding(rid, check, FAIL,
                           f"{name} is required and the deliverable does not "
                           "state it at all")
        if expected.get(name) in _MARKERS and claimed[name] != expected.get(name):
            return Finding(rid, check, FAIL,
                           f"{name} is required, is not stated in the document, "
                           f"and the deliverable gives it as {claimed[name]!r}")
        # The other direction, which this check did not ask. A required field
        # the document plainly states, delivered as not stated, passed: the
        # loop above only looked at fields the document *omits*, and the
        # summary below only counted those too. So a deliverable could tell a
        # client their invoice number was missing while it sat in the first
        # line of their own document -- a disclosed gap that is not a gap,
        # which is as misleading as an undisclosed one and rather harder to
        # argue with. Found by a generated battery, not by reading the code.
        if expected.get(name) not in _MARKERS and claimed[name] in _MARKERS:
            return Finding(
                rid, check, FAIL,
                f"{name} is required, the document states it as "
                f"{expected.get(name)!r}, and the deliverable reports it as "
                f"{claimed[name]}",
                "a field reported absent from a document that states it is a "
                "gap the client will go looking for and find")
    absent = [n for n in named if expected.get(n) in _MARKERS]
    if absent:
        return Finding(
            rid, check, FAIL,
            f"required field(s) {', '.join(absent)} are not stated in the "
            "document. The absence is correctly disclosed; the requirement is "
            "still not met",
            "a disclosed gap is honest and still not the work the client asked "
            "for, which is the delivery gate's decision rather than this one's")
    return Finding(rid, check, PASS,
                   f"all {len(named)} required field(s) are stated in the "
                   "document and delivered")


def _table(rid, check, params, document, extraction, delivered) -> Finding:
    columns = list(params.get("columns") or [])
    matches = tables_matching(columns, document.tables)
    if not matches:
        return Finding(rid, check, FAIL,
                       f"no table in the document has a header row with "
                       f"{', '.join(str(c) for c in columns)}")
    if len(matches) > 1:
        return Finding(rid, check, UNVERIFIABLE,
                       f"{len(matches)} tables in the document match these "
                       "columns, so which one was meant cannot be established "
                       "here either")
    want = [list(row) for row in document.tables[matches[0]]]
    got = _delivered_tables(extraction, delivered)
    if not got:
        return Finding(rid, check, FAIL,
                       "the deliverable carries no table at all")
    if not any(_same_table(want, candidate) for candidate in got):
        return Finding(rid, check, FAIL,
                       f"the delivered table is not the table the document "
                       f"states: {len(want)} row(s) expected, delivered "
                       f"{', '.join(str(len(c)) for c in got)}",
                       f"expected header {want[0] if want else []}")
    return Finding(rid, check, PASS,
                   f"a table of {len(want)} row(s) is delivered exactly as the "
                   "document states it")


def _table_preserved(rid, check, spec, document, extraction, delivered) -> Finding:
    got = _delivered_tables(extraction, delivered)
    if delivered is not None and delivered.kind == "plain text" \
            and document.tables and not got:
        return Finding(
            rid, check, UNVERIFIABLE,
            "the deliverable is plain text, which has no way to express a "
            "table, so whether the table structure survived cannot be "
            "established from the file. The cells themselves are covered by "
            "conversion_fidelity")
    if not got:
        if not document.tables:
            return Finding(rid, check, PASS,
                           "the document states no table, and none is delivered")
        return Finding(rid, check, FAIL,
                       f"the document states {len(document.tables)} table(s) "
                       "and the deliverable carries none")
    source = [[list(row) for row in rows] for rows in document.tables]
    for candidate in got:
        if not any(_same_table(original, candidate) for original in source):
            return Finding(
                rid, check, FAIL,
                "a delivered table is not any table the document states",
                f"delivered header {candidate[0] if candidate else []}")
    return Finding(rid, check, PASS,
                   f"{len(got)} delivered table(s) each match a table the "
                   "document states, cell for cell")


def _delivered_tables(extraction, delivered) -> list:
    if extraction is not None:
        return [[list(row) for row in table]
                for table in extraction.get("tables") or []]
    return [[list(row) for row in rows] for rows in (delivered.tables
                                                     if delivered else [])]


def _same_table(want, got) -> bool:
    """Cell for cell, allowing a ragged source row padded to a rectangle."""
    if len(want) != len(got):
        return False
    width = max((len(row) for row in want), default=0)
    for left, right in zip(want, got):
        padded = list(left) + [""] * (width - len(left))
        other = list(right) + [""] * (width - len(right))
        if [str(c) for c in padded] != [str(c) for c in other]:
            return False
    return True


def _converted(rid, check, params, output, delivered) -> Finding:
    target = f".{str(params.get('to', '')).lstrip('.').lower()}"
    if output.suffix.lower() != target:
        return Finding(rid, check, FAIL,
                       f"the conversion asked for {target} and the deliverable "
                       f"is {output.suffix}")
    return Finding(rid, check, PASS,
                   f"the deliverable is {target}, read back as "
                   f"{delivered.kind}")


def _fidelity(rid, check, spec, document, delivered) -> Finding:
    """Every paragraph the source states is in the output, in order, and no more.

    Stated as two questions rather than one list comparison, because the two
    answers are different kinds of defect and because the output format changes
    what the lines look like:

    *Nothing lost, nothing reordered.* The source's paragraphs must appear in
    the deliverable in the order the document states them. Order is content:
    "Total: 400" under one heading means something different two headings
    later, so a conversion that keeps every line and shuffles them has lost
    the document while passing any check that only counts lines.

    *Nothing added.* Every other line in the deliverable has to be explainable
    -- a row of a table the document states, rendered in the target format's
    way of rendering tables. A line that is neither is content nobody wrote,
    and in a document deliverable that is the most expensive kind of defect.

    This is deliberately not a comparison against the worker's own rendering.
    Re-implementing the worker's formatting here would mean a formatting bug
    agreeing with itself, which is the failure the two-sided design exists to
    prevent.
    """
    if delivered is None:
        return Finding(rid, check, UNVERIFIABLE,
                       "the deliverable is an extraction rather than a "
                       "converted document, so there is no conversion to check")
    want = [text for text, _ in document.paragraphs if text]
    got = [text for text, _ in delivered.paragraphs if text]

    position, lost = 0, []
    for text in want:
        try:
            position = got.index(text, position) + 1
        except ValueError:
            lost.append(text)
    if lost:
        present = [text for text in lost if text in got]
        if present:
            return Finding(
                rid, check, FAIL,
                f"{len(present)} paragraph(s) are in the deliverable in a "
                f"different order from the document. First: {present[0]!r}",
                f"document order begins {want[:3]}, deliverable {got[:3]}")
        return Finding(rid, check, FAIL,
                       f"{len(lost)} paragraph(s) in the document are not in "
                       f"the deliverable. First: {lost[0]!r}")

    explainable = set(want) | _table_renderings(document)
    added = [text for text in got if text not in explainable]
    if added:
        return Finding(rid, check, FAIL,
                       f"{len(added)} line(s) in the deliverable are not in "
                       f"the document. First: {added[0]!r}",
                       "content a conversion added is content nobody wrote")
    return Finding(rid, check, PASS,
                   f"all {len(want)} paragraph(s) are carried through in the "
                   f"order the document states them, and every other delivered "
                   f"line is a row of a table the document states")


def _table_renderings(document) -> set[str]:
    """How a row of each source table could legitimately appear as a line.

    A format that cannot express a table writes its cells out as text, and the
    separator is the format's business rather than this module's -- so each
    plausible separator is accepted, and a line that matches none of them is
    still an addition.
    """
    out = set()
    for rows in document.tables:
        for row in rows:
            cells = [str(cell) for cell in row]
            for separator in ("\t", " | ", ", ", " "):
                out.add(separator.join(cells))
            out.update(cells)
    return out


def _headings(rid, check, spec, document, delivered) -> Finding:
    if delivered is None:
        return Finding(rid, check, UNVERIFIABLE,
                       "the deliverable is an extraction, which carries no "
                       "heading structure")
    if delivered.kind == "plain text":
        return Finding(
            rid, check, UNVERIFIABLE,
            "the deliverable is plain text, which has no way to express a "
            "heading level, so whether one was applied cannot be established "
            "from the file. The heading text itself is covered by "
            "conversion_fidelity",
            "a requirement that structure be applied is not one plain text can "
            "satisfy; that is a conversation about the target format")
    want = [(text, level) for text, level in document.paragraphs
            if level is not None]
    levels = {str(k): int(v) for k, v in (spec["levels"] or {}).items()}
    got = {text: level for text, level in delivered.paragraphs
           if level is not None}
    if not want and not levels:
        return Finding(rid, check, PASS,
                       "the document declares no heading structure, and none "
                       "is claimed in the deliverable")
    for text, level in want:
        if text not in got:
            return Finding(rid, check, FAIL,
                           f"{text!r} is a heading in the document and is not "
                           "a heading in the deliverable")
        if got[text] != level:
            return Finding(rid, check, FAIL,
                           f"{text!r} is at level {level} in the document and "
                           f"level {got[text]} in the deliverable")
    return Finding(rid, check, PASS,
                   f"{len(want)} heading(s) carry their level through to the "
                   "deliverable")


def _templated(rid, check, params, spec, lines, delivered) -> Finding:
    """The template's literal text is in the deliverable, and only its text."""
    if delivered is None:
        return Finding(rid, check, UNVERIFIABLE,
                       "the deliverable is an extraction rather than a filled "
                       "template")
    blocks = list(params.get("blocks") or [])
    values = extract(spec["labels"], lines) if spec["labels"] else {}
    text_blocks = [b for b in blocks if b.get("kind") != "table"]
    got = [text for text, _ in delivered.paragraphs]
    expected = []
    for block in text_blocks:
        literal = str(block.get("text", ""))
        expected.append(_PLACEHOLDER.sub(
            lambda m: values.get(m.group(1).strip(), m.group(0)), literal))
    for line in expected:
        if line and line not in got:
            return Finding(rid, check, FAIL,
                           f"the template states {line!r} and the deliverable "
                           "does not")
    extra = [line for line in got
             if line and line not in expected
             and not any(line in str(b.get("text", "")) for b in text_blocks)]
    if extra:
        return Finding(rid, check, FAIL,
                       f"{len(extra)} line(s) in the deliverable are not in the "
                       f"template. First: {extra[0]!r}",
                       "a filled template contains the template and the values, "
                       "and nothing else")
    return Finding(rid, check, PASS,
                   f"{len(expected)} template block(s) are present, filled only "
                   "from values the document states")


def _not_invented(rid, check, spec, lines, document, extraction,
                  delivered) -> Finding:
    """Every line delivered traces to the document, the template, or a marker."""
    haystack = "\n".join(lines)
    if extraction is not None:
        stray = [f"{e.get('field')}={e.get('value')!r}"
                 for e in extraction["fields"]
                 if str(e.get("value", "")) not in _MARKERS
                 and str(e.get("value", "")).strip()
                 and str(e.get("value")) not in haystack]
        # The tables, too. This check looked only at the field values, so a
        # delivered table with a row appended out of nowhere passed it --
        # a fabricated line item in a client's invoice, which is the most
        # expensive thing an extraction can invent. Found by a generated
        # battery, not by reading the code.
        source_rows = [tuple(str(cell) for cell in row)
                       for rows in document.tables for row in rows]
        for position, table in enumerate(
                _delivered_tables(extraction, delivered), start=1):
            for index, row in enumerate(table, start=1):
                if tuple(str(cell) for cell in row) not in source_rows:
                    stray.append(f"table {position} row {index}: "
                                 f"{[str(c) for c in row]}")
        if stray:
            return Finding(rid, check, FAIL,
                           f"{len(stray)} extracted value(s) or table row(s) "
                           f"are not in the document. First: {stray[0]}")
        return Finding(rid, check, PASS,
                       f"every value and every table row in the extraction "
                       f"appears in the document, and "
                       f"{len(extraction.get('not_stated') or [])} absence(s) "
                       "are marked rather than filled")

    literals = [str(b.get("text", "")) for b in spec["template"]
                if b.get("kind") != "table"]
    # A table the document states is content, however the target format renders
    # it. In plain text its cells come through tab-separated, which is in the
    # document and is not in the document's *paragraphs*; a check that looked
    # only at paragraph text would call a correctly converted table invented.
    renderings = _table_renderings(document)
    invented = []
    for text, _ in (delivered.paragraphs if delivered else []):
        if not text.strip():
            continue
        if text in haystack or text in renderings \
                or any(marker in text for marker in _MARKERS):
            continue
        if any(_matches_literal(text, literal) for literal in literals):
            continue
        invented.append(text)
    if invented:
        return Finding(
            rid, check, FAIL,
            f"{len(invented)} delivered line(s) are neither in the document nor "
            f"in the template. First: {invented[0]!r}",
            "; ".join(repr(t) for t in invented[:3]))
    return Finding(rid, check, PASS,
                   f"every delivered line traces to the document, to the "
                   "template, or to a disclosed absence")


def _matches_literal(text: str, literal: str) -> bool:
    """Could this line be that template literal with its placeholders filled?"""
    if not literal:
        return False
    names = _PLACEHOLDER.findall(literal)
    if not names:
        return text == literal
    pattern = re.escape(literal)
    for name in names:
        pattern = pattern.replace(re.escape("{" + name + "}"), ".*")
    return re.fullmatch(pattern, text) is not None


def run(check_name: str, *, source, output, params: dict):
    """One check, for the capability dispatch table."""
    if check_name not in CHECKS:
        return Finding("", check_name, UNVERIFIABLE,
                       f"no registered check named {check_name!r}")
    requirements = list((params or {}).get("requirements") or [])
    if not requirements:
        if check_name in WHOLE_JOB:
            return Finding("", check_name, UNVERIFIABLE,
                           f"{check_name} is a property of the whole job and "
                           "needs the committed requirements, which this call "
                           "did not carry")
        requirements = [_One(check_name, dict(params or {}))]
    report = verify(source=source, output=output, requirements=requirements)
    for finding in report.findings:
        if finding.check == check_name:
            return finding
    return Finding("", check_name, UNVERIFIABLE,
                   f"{check_name} produced no finding, which is a defect in "
                   "this module")


@dataclass(frozen=True, slots=True)
class _One:
    check: str
    params: dict
    id: str = ""
