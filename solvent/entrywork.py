"""Service capability #4 — structured data entry and validation. No model.

A **worker**, not an authority. It takes a source of records, maps them into a
target schema the client named, checks each record against rules the client
stated, and writes two files: the records that passed, and the records that did
not with the reason they did not. It decides nothing about whether the job was
worth taking or whether anything may be delivered.

Three rules shape everything here, and each exists because of a specific way
data entry goes wrong in practice:

**A value is never invented.** A required field that is empty does not become a
sensible default, a copy of the row above, or a blank that reads as zero. The
record becomes an exception, and the exception names the field. Filling a gap
is the single most damaging thing a data-entry service can do, because the
client cannot see it: the deliverable looks complete and one figure in it came
from nowhere.

**A record is never corrected.** A date in the wrong format, a total outside
the stated range, a code not in the client's list — each makes the record an
exception. It is not reformatted, clamped or mapped to the nearest allowed
value. Reformatting is :mod:`solvent.csvwork`'s job and only where a
requirement asked for it; doing it silently here would mean the client's rule
had been applied to data this capability had already changed.

**A rejection is as auditable as an acceptance.** Every exception carries the
source row number, the rules it violated, and the record as supplied. That is
what lets the verifier re-derive the whole split and catch the failure nobody
notices: a *valid* record quietly dropped and labelled invalid, which makes a
deliverable short and self-consistent.

What it will not do: read spreadsheets (``.xlsx`` is
:mod:`solvent.sheetwork`'s), convert between date conventions, merge records
across sources, look anything up externally, or judge whether an email address
exists. The email check here is structural and says so.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field

from . import tabular
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import types

VERSION = "data-entry/1.0"

#: Operations this capability performs. Anything else is refused.
OPERATIONS = frozenset({
    "map_fields",            # explicit target <- source column mapping
    "constant_field",        # a value the client supplied, the same for every row
    "require_field",         # empty here makes the record an exception
    "validate_type",         # integer | decimal | date | email | text
    "validate_range",        # numeric or date bounds, inclusive
    "validate_enum",         # one of an explicit list
    "validate_pattern",      # an explicit regular expression
    "normalise_whitespace",  # leading/trailing and runs within, where authorised
    "normalise_case",        # upper | lower | title, where authorised
    "deduplicate_by",        # drop later records sharing a key
    "select_fields",         # the delivered field order
})

#: Checked, never performed. Properties of the split rather than steps in it.
VERIFY_ONLY = frozenset({
    "records_reconcile",     # accepted + rejected + duplicates == records in
    "no_invented_values",    # every delivered value traces to source or constant
    "exceptions_justified",  # every rejection names rules the record truly breaks
    "record_traceability",   # each delivered record is one distinct source record
    "parses_as_delivered",   # the deliverable is readable as what it claims to be
})

#: The whole type vocabulary. Small on purpose: every one of these is decidable
#: from the value alone, with no convention to guess and nothing to look up.
TYPES = ("text", "integer", "decimal", "date", "email")

CASES = ("upper", "lower", "title")

#: A date is ISO 8601 and a real calendar day. Nothing else is a date here.
#:
#: ``05/10/2026`` is not rejected because it is ugly — it is rejected because it
#: denotes either 10 May or 5 October and this module has no basis to choose.
#: Converting formats is a *cleanup* requirement, and running one capability's
#: rule over another capability's guess is how a client's validation ends up
#: applied to data nobody authorised changing.
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

#: Structural only, and named so nobody reads more into it. It says the string
#: is shaped like an address, not that anybody is there.
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;.]+(\.[^@\s,;.]+)+$")

_WS = re.compile(r"\s+")

#: How long a regular expression a requirement may carry. A pattern is client
#: text reaching a regex engine, so it is bounded, and it is compiled once and
#: matched against single field values rather than whole documents.
MAX_PATTERN = 200

#: Records read from one source. A job larger than this is a conversation about
#: scope, not something to attempt and time out halfway through.
MAX_RECORDS = 200_000


def digest_of(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(slots=True)
class Step:
    """One planned action, tied to the requirement that asked for it."""

    requirement_id: str
    operation: str
    params: dict
    outcome: str = ""
    mandatory: bool = True


@dataclass(slots=True)
class Rejection:
    """One record that did not pass, and exactly why.

    ``rules`` is every rule the record broke, not the first one. A client
    fixing their data wants the whole list; and a verifier re-deriving the
    split compares the whole list, which is what makes a plausible-looking
    rejection reason checkable rather than merely readable.
    """

    source_row: int
    rules: tuple[str, ...]
    detail: str
    record: dict

    @property
    def rule_text(self) -> str:
        return ";".join(self.rules)


@dataclass(slots=True)
class WorkReport:
    """What the worker did. Evidence for a human, never proof of success."""

    steps: list[Step] = field(default_factory=list)
    records_in: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates_dropped: int = 0
    fields_out: list[str] = field(default_factory=list)
    exceptions: list[Rejection] = field(default_factory=list)
    exceptions_path: str = ""
    refused: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.refused

    def summary(self) -> str:
        lines = [f"{self.records_in} record(s) read: {self.accepted} accepted, "
                 f"{self.rejected} held as exceptions, "
                 f"{self.duplicates_dropped} dropped as duplicates",
                 "delivered fields: " + (", ".join(self.fields_out) or "none")]
        lines += [f"{s.requirement_id}: {s.outcome}" for s in self.steps]
        if self.exceptions:
            lines.append(f"exceptions written to {self.exceptions_path}")
        for refusal in self.refused:
            lines.append(f"refused: {refusal}")
        return "\n".join(lines)


# ------------------------------------------------------------------ planning
#: Fixed order. Mapping first because every later step names target fields;
#: authorised normalisation before validation, so a rule the client wrote is
#: applied to the value they authorised rather than to raw input; deduplication
#: after validation, so an invalid record is never the one that survives a key
#: collision; field selection last, because it only decides the output shape.
_ORDER = {op: i for i, op in enumerate((
    "map_fields", "constant_field", "normalise_whitespace", "normalise_case",
    "require_field", "validate_type", "validate_range", "validate_enum",
    "validate_pattern", "deduplicate_by", "select_fields"))}


def plan(requirements: list) -> tuple[list[Step], list[str]]:
    """Committed requirements into an ordered plan, or the reason why not.

    Returns ``(steps, refusals)``. A refusal is the capability saying what it
    cannot do, which the owner needs before the job is accepted rather than
    after it is delivered.
    """
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
    """Why this operation cannot be carried out as asked, or ``""``.

    Every message says what is missing rather than that something is invalid,
    because the reader is whoever has to go back to the client.
    """
    if op == "map_fields":
        mapping = params.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            return ("no mapping was given, and a target schema cannot be "
                    "guessed from column names")
        for target, column in mapping.items():
            if not str(target).strip() or not str(column).strip():
                return (f"the mapping entry {target!r} -> {column!r} names a "
                        "blank field, so it says nothing")
        return ""
    if op == "constant_field":
        fields = params.get("fields")
        if not isinstance(fields, dict) or not fields:
            return "no constant fields were given"
        for name in fields:
            if not str(name).strip():
                return "a constant was given for a field with no name"
        return ""
    if op in ("require_field", "deduplicate_by", "select_fields"):
        fields = params.get("fields")
        if not isinstance(fields, (list, tuple)) or not fields:
            return "no fields were named"
        if any(not str(f).strip() for f in fields):
            return "a blank field name was given"
        return ""
    if op == "validate_type":
        table = params.get("types")
        if not isinstance(table, dict) or not table:
            return "no field types were given"
        unknown = sorted({str(v) for v in table.values()} - set(TYPES))
        if unknown:
            return (f"{', '.join(unknown)} is not a type this capability "
                    f"decides. It decides {', '.join(TYPES)}")
        return ""
    if op == "validate_range":
        ranges = params.get("ranges")
        if not isinstance(ranges, dict) or not ranges:
            return "no ranges were given"
        for name, bound in ranges.items():
            if not isinstance(bound, dict) or not (
                    "minimum" in bound or "maximum" in bound):
                return (f"the range for {name!r} states neither a minimum nor "
                        "a maximum, so nothing is bounded")
            kind = _bound_kind(bound)
            if not kind:
                return (f"the bounds for {name!r} are neither both numbers nor "
                        "both ISO dates, so there is no ordering to compare in")
        return ""
    if op == "validate_enum":
        allowed = params.get("allowed")
        if not isinstance(allowed, dict) or not allowed:
            return "no allowed values were given"
        for name, values in allowed.items():
            if not isinstance(values, (list, tuple)) or not values:
                return f"the allowed list for {name!r} is empty"
        return ""
    if op == "validate_pattern":
        patterns = params.get("patterns")
        if not isinstance(patterns, dict) or not patterns:
            return "no patterns were given"
        for name, pattern in patterns.items():
            text = str(pattern)
            if not text:
                return f"the pattern for {name!r} is empty"
            if len(text) > MAX_PATTERN:
                return (f"the pattern for {name!r} is {len(text)} characters; "
                        f"{MAX_PATTERN} is the limit")
            try:
                re.compile(text)
            except re.error as exc:
                return f"the pattern for {name!r} is not a usable one: {exc}"
        return ""
    if op == "normalise_whitespace":
        fields = params.get("fields")
        if fields is not None and not isinstance(fields, (list, tuple)):
            return "the fields to normalise were given as something other than a list"
        return ""
    if op == "normalise_case":
        table = params.get("fields")
        if not isinstance(table, dict) or not table:
            return "no fields and cases were given"
        unknown = sorted({str(v) for v in table.values()} - set(CASES))
        if unknown:
            return (f"{', '.join(unknown)} is not a case conversion this "
                    f"capability performs. It performs {', '.join(CASES)}")
        return ""
    return f"{op} has no parameter contract, which is a defect in this module"


def _bound_kind(bound: dict) -> str:
    """``"number"``, ``"date"`` or ``""`` when the two bounds disagree.

    A minimum of ``1`` with a maximum of ``2026-01-01`` is not a range. Mixing
    them would silently compare a number against a string, which in Python 3
    raises and in a looser language would quietly answer.
    """
    given = [str(bound[k]) for k in ("minimum", "maximum") if k in bound]
    if all(as_number(v) is not None for v in given):
        return "number"
    if all(as_date(v) is not None for v in given):
        return "date"
    return ""


# ------------------------------------------------------------------- values
def as_number(value) -> Decimal | None:
    """``value`` as a Decimal, or None when it is not a number.

    None rather than zero. A field that will not parse is an exception, and
    treating it as zero would make it an accepted record with a wrong figure in
    it — the deliverable a client cannot tell from a correct one.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = text.replace(",", "")
    for symbol in ("$", "£", "€", "USD", "GBP", "EUR"):
        if text.startswith(symbol):
            text = text[len(symbol):].strip()
    if not _plain_number(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:  # pragma: no cover - _plain_number precedes it
        return None
    return -number if negative else number


def _plain_number(text: str) -> bool:
    """Is this text a plain decimal figure? Digits, one point, an optional sign.

    Written as a scan rather than a pattern so that it is a second
    implementation of the rule and not a second copy of one. The rule itself is
    deliberately narrow: ``Decimal`` would happily accept ``1e5``, ``NaN`` and
    ``Infinity``, and none of those is a figure a client meant to put in an
    invoice field. Accepting ``1e5`` would mean delivering the string ``1e5``
    in a column the client's schema calls a decimal, which their next tool
    reads as text or as something else entirely.
    """
    body = text[1:] if text[:1] in "+-" else text
    if not body or body.count(".") > 1:
        return False
    digits = body.replace(".", "")
    return bool(digits) and all(c.isdigit() and c.isascii() for c in digits)


def as_date(value) -> _date | None:
    """``value`` as a date, or None. ISO 8601 and a real calendar day only."""
    match = _ISO_DATE.match(str(value or "").strip())
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return _date(year, month, day)
    except ValueError:
        return None


def type_problem(kind: str, value: str) -> str:
    """Why ``value`` is not a ``kind``, or ``""``. An empty value is not judged.

    Emptiness is :data:`require_field`'s question. Answering it here too would
    mean an optional field could be rejected for being absent, which is the
    opposite of optional.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if kind == "text":
        return ""
    if kind == "integer":
        number = as_number(text)
        if number is None:
            return f"{text!r} is not a number"
        if number != number.to_integral_value():
            return f"{text!r} is not a whole number"
        return ""
    if kind == "decimal":
        return "" if as_number(text) is not None else f"{text!r} is not a number"
    if kind == "date":
        if as_date(text) is not None:
            return ""
        if _ISO_DATE.match(text):
            return f"{text!r} is not a day that exists"
        return f"{text!r} is not an ISO 8601 date (YYYY-MM-DD)"
    if kind == "email":
        return ("" if _EMAIL.match(text)
                else f"{text!r} is not shaped like an email address")
    return f"{kind!r} is not a type this capability decides"


def range_problem(bound: dict, value: str) -> str:
    """Why ``value`` falls outside ``bound``, or ``""``."""
    text = str(value or "").strip()
    if not text:
        return ""
    kind = _bound_kind(bound)
    parse = as_number if kind == "number" else as_date
    here = parse(text)
    if here is None:
        return f"{text!r} cannot be compared against the stated range"
    low = parse(str(bound["minimum"])) if "minimum" in bound else None
    high = parse(str(bound["maximum"])) if "maximum" in bound else None
    if low is not None and here < low:
        return f"{text} is below the stated minimum {bound['minimum']}"
    if high is not None and here > high:
        return f"{text} is above the stated maximum {bound['maximum']}"
    return ""


def normalise_whitespace(value: str) -> str:
    """Trim the ends and collapse runs within. Never removes all spacing."""
    return _WS.sub(" ", str(value or "")).strip()


def apply_case(case: str, value: str) -> str:
    text = str(value or "")
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return text.title()
    return text


# -------------------------------------------------------------------- source
class _Unreadable(Exception):
    """The source cannot be read as what it claims to be."""


def read_records(path: str | Path) -> tuple[list[str], list[dict], list[str]]:
    """``(columns, records, problems)`` from a CSV or JSON source.

    ``problems`` holds per-record structural faults — a ragged CSV row, a JSON
    object with a nested value — as ``"<row>: <why>"``. They are returned
    rather than raised, and rather than repaired: a short row is not padded and
    a nested value is not flattened, because both repairs invent the shape of
    data this capability was asked to transcribe faithfully.
    """
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        return _read_json(raw)
    return _read_csv(raw)


def _read_csv(raw: bytes) -> tuple[list[str], list[dict], list[str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _Unreadable(f"the source is not UTF-8 text: {exc}") from exc
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise _Unreadable(f"the source will not parse as CSV: {exc}") from exc
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        raise _Unreadable("the source has no rows at all, not even a header")
    columns = [str(cell) for cell in rows[0]]
    if len(set(columns)) != len(columns):
        raise _Unreadable(
            "the source header names the same column twice, so a mapping "
            "could not say which one it meant")
    records, problems = [], []
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != len(columns):
            problems.append(f"{index}: the row has {len(row)} value(s) where "
                            f"the header names {len(columns)} column(s)")
            records.append({})
            continue
        records.append({name: str(cell) for name, cell in zip(columns, row)})
        if len(records) > MAX_RECORDS:
            raise _Unreadable(
                f"the source carries more than {MAX_RECORDS} records")
    return columns, records, problems


def _read_json(raw: bytes) -> tuple[list[str], list[dict], list[str]]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Unreadable(f"the source will not parse as JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise _Unreadable(
            "the source is a JSON document but not a list of records, and this "
            "capability will not guess which part of it is the data")
    if len(parsed) > MAX_RECORDS:
        raise _Unreadable(f"the source carries more than {MAX_RECORDS} records")
    columns: list[str] = []
    records, problems = [], []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            problems.append(f"{index}: the record is a "
                            f"{type(item).__name__}, not an object")
            records.append({})
            continue
        record = {}
        bad = ""
        for key, value in item.items():
            # The field names are collected whatever the values turn out to be.
            # A record whose one nested value makes it unusable still says that
            # the column exists, and dropping it from the source's shape turned
            # a per-record exception into a job-level refusal: "the mapping
            # names a column the file does not have", about a column the client
            # can see in their own file.
            if str(key) not in columns:
                columns.append(str(key))
            if isinstance(value, (dict, list)):
                bad = (f"the value of {key!r} is a {type(value).__name__}; "
                       "this capability transcribes values, and flattening one "
                       "would invent a shape nobody asked for")
                continue
            record[str(key)] = "" if value is None else _scalar(value)
        if bad:
            problems.append(f"{index}: {bad}")
            records.append({})
            continue
        records.append(record)
    return columns, records, problems


def _scalar(value) -> str:
    """A JSON scalar as the text a CSV cell would hold.

    ``True`` becomes ``"true"`` rather than ``"True"``: the source said JSON,
    and a client reading their own export back should see their own spelling.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ------------------------------------------------------------------ execution
def execute(*, source: str | Path, destination: str | Path, requirements: list,
            exceptions: str | Path = "") -> WorkReport:
    """Carry out the plan. Returns what was done; proves nothing.

    Two files come out of a data-entry job and both are part of the
    deliverable: the accepted records at ``destination``, and the rejected ones
    beside it. Writing only the first would hand a client a file that is short
    by an unknown amount for unstated reasons.

    Nothing is written when anything was refused. A partial deliverable for a
    brief this capability could not honour is how a client receives work that
    looks finished and is not.
    """
    report = WorkReport()
    source, destination = Path(source), Path(destination)
    report.exceptions_path = str(
        Path(exceptions) if exceptions
        else destination.with_name(destination.stem + "-exceptions.csv"))

    steps, refusals = plan(requirements)
    report.steps, report.refused = steps, list(refusals)

    try:
        columns, records, structural = read_records(source)
    except _Unreadable as exc:
        report.refused.append(f"the source could not be read: {exc}")
        return report
    except OSError as exc:
        report.refused.append(f"the source could not be read: {exc}")
        return report

    report.records_in = len(records)
    mapping_step = next((s for s in steps if s.operation == "map_fields"), None)
    if mapping_step is None:
        report.refused.append(
            "no requirement says which source column fills which target field, "
            "and a target schema cannot be inferred from column names")
    else:
        missing = [column for column in mapping_step.params["mapping"].values()
                   if str(column) not in columns]
        if missing:
            report.refused.append(
                f"the mapping names source column(s) the file does not have: "
                f"{', '.join(sorted(set(missing)))}. The file has: "
                f"{', '.join(columns)}")

    constants: dict[str, str] = {}
    for step in (s for s in steps if s.operation == "constant_field"):
        constants.update({str(k): str(v) for k, v in step.params["fields"].items()})

    if mapping_step is not None:
        mapping = {str(k): str(v) for k, v in mapping_step.params["mapping"].items()}
        clash = sorted(set(mapping) & set(constants))
        if clash:
            report.refused.append(
                f"field(s) {', '.join(clash)} are both mapped from the source "
                "and given as a constant, so two requirements disagree about "
                "where the value comes from")
    else:
        mapping = {}

    fields = list(mapping) + [c for c in constants if c not in mapping]
    select = next((s for s in steps if s.operation == "select_fields"), None)
    if select is not None:
        wanted = [str(f) for f in select.params["fields"]]
        unknown = [f for f in wanted if f not in fields]
        if unknown:
            report.refused.append(
                f"the delivered field order names field(s) nothing produces: "
                f"{', '.join(unknown)}")
        fields = wanted

    for step in steps:
        named = _fields_named(step)
        unknown = [f for f in named if f not in list(mapping) + list(constants)]
        if unknown:
            report.refused.append(
                f"{step.requirement_id}: {step.operation} names target "
                f"field(s) nothing produces: {', '.join(sorted(set(unknown)))}")

    if report.refused:
        return report

    rules = _rules(steps)
    accepted: list[dict] = []
    seen: dict[tuple, int] = {}
    dedup = next((s for s in steps if s.operation == "deduplicate_by"), None)

    for index, record in enumerate(records, start=2 if _is_csv(source) else 1):
        fault = next((p for p in structural if p.startswith(f"{index}:")), "")
        if fault:
            report.exceptions.append(Rejection(
                source_row=index, rules=("parses_as_delivered",),
                detail=fault.split(": ", 1)[1], record=dict(record)))
            continue
        row = {target: record.get(column, "") for target, column in mapping.items()}
        row.update(constants)
        row = _normalised(row, steps, mapping)
        broken, detail = _violations(row, rules)
        if broken:
            report.exceptions.append(Rejection(
                source_row=index, rules=tuple(broken), detail="; ".join(detail),
                record=dict(record)))
            continue
        if dedup is not None:
            key = tuple(row.get(str(f), "") for f in dedup.params["fields"])
            if key in seen:
                report.duplicates_dropped += 1
                continue
            seen[key] = index
        accepted.append({name: row.get(name, "") for name in fields})

    report.accepted = len(accepted)
    report.rejected = len(report.exceptions)
    report.fields_out = list(fields)
    for step in steps:
        step.outcome = _outcome(step, report)

    _write_csv(destination, fields, [[r[name] for name in fields]
                                     for r in accepted])
    _write_csv(Path(report.exceptions_path),
               ["source_row", "rules", "detail", "record"],
               [[str(r.source_row), r.rule_text, r.detail,
                 json.dumps(r.record, sort_keys=True, ensure_ascii=False)]
                for r in report.exceptions])
    return report


def _is_csv(source: Path) -> bool:
    """CSV row numbers count the header; JSON records do not have one."""
    return source.suffix.lower() != ".json"


def _fields_named(step: Step) -> list[str]:
    """Target fields a step refers to, so an unknown one is a refusal."""
    params = step.params
    if step.operation in ("require_field", "deduplicate_by"):
        return [str(f) for f in params.get("fields", [])]
    if step.operation == "validate_type":
        return [str(f) for f in params.get("types", {})]
    if step.operation == "validate_range":
        return [str(f) for f in params.get("ranges", {})]
    if step.operation == "validate_enum":
        return [str(f) for f in params.get("allowed", {})]
    if step.operation == "validate_pattern":
        return [str(f) for f in params.get("patterns", {})]
    if step.operation == "normalise_case":
        return [str(f) for f in params.get("fields", {})]
    if step.operation == "normalise_whitespace":
        return [str(f) for f in (params.get("fields") or [])]
    return []


def _normalised(row: dict, steps: list[Step], mapping: dict) -> dict:
    """Apply only the normalisations a requirement authorised, to only its fields."""
    out = dict(row)
    for step in steps:
        if step.operation == "normalise_whitespace":
            named = [str(f) for f in (step.params.get("fields") or [])]
            targets = named or list(mapping)
            for name in targets:
                if name in out:
                    out[name] = normalise_whitespace(out[name])
        elif step.operation == "normalise_case":
            for name, case in step.params["fields"].items():
                if str(name) in out:
                    out[str(name)] = apply_case(str(case), out[str(name)])
    return out


def _rules(steps: list[Step]) -> list[tuple[str, str, object]]:
    """``(rule, field, parameter)`` for every validation, in a fixed order.

    One flat list so the worker and the verifier can both say "these are the
    rules this job stated" and compare the same sentence.
    """
    rules: list[tuple[str, str, object]] = []
    for step in steps:
        if step.operation == "require_field":
            rules += [("require_field", str(f), None)
                      for f in step.params["fields"]]
        elif step.operation == "validate_type":
            rules += [("validate_type", str(f), str(k))
                      for f, k in step.params["types"].items()]
        elif step.operation == "validate_range":
            rules += [("validate_range", str(f), b)
                      for f, b in step.params["ranges"].items()]
        elif step.operation == "validate_enum":
            rules += [("validate_enum", str(f), [str(v) for v in values])
                      for f, values in step.params["allowed"].items()]
        elif step.operation == "validate_pattern":
            rules += [("validate_pattern", str(f), str(p))
                      for f, p in step.params["patterns"].items()]
    return rules


def _violations(row: dict, rules: list) -> tuple[list[str], list[str]]:
    """Every rule this record breaks, and why. Not the first one — all of them."""
    broken, detail = [], []
    for rule, name, parameter in rules:
        value = str(row.get(name, ""))
        problem = ""
        if rule == "require_field":
            if not value.strip():
                problem = f"{name} is required and is empty"
        elif rule == "validate_type":
            said = type_problem(str(parameter), value)
            if said:
                problem = f"{name}: {said}"
        elif rule == "validate_range":
            said = range_problem(parameter, value)
            if said:
                problem = f"{name}: {said}"
        elif rule == "validate_enum":
            if value.strip() and value not in parameter:
                problem = (f"{name}: {value!r} is not one of the "
                           f"{len(parameter)} allowed value(s)")
        elif rule == "validate_pattern":
            if value.strip() and not re.match(str(parameter), value):
                problem = f"{name}: {value!r} does not match the stated pattern"
        if problem:
            if rule not in broken:
                broken.append(rule)
            detail.append(problem)
    return broken, detail


def _outcome(step: Step, report: WorkReport) -> str:
    if step.operation == "map_fields":
        return f"{len(step.params['mapping'])} field(s) mapped from the source"
    if step.operation == "deduplicate_by":
        return (f"{report.duplicates_dropped} later record(s) dropped as "
                "duplicates of an earlier one")
    if step.operation.startswith("validate") or step.operation == "require_field":
        return (f"applied; {report.rejected} record(s) held as exceptions "
                "across all rules")
    return "applied"


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8", newline="")
