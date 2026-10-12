"""Independent verification of data-entry work. It does not trust the worker.

A **verifier**, and the point of it is that it does not import
:mod:`solvent.entrywork`. It reads the source, the delivered records and the
exceptions file, works out for itself which records the stated rules accept,
and compares. Nothing is taken from the worker's report: a worker that could
certify its own output would be a worker whose bugs certify themselves.

Everything that decides a verdict is written again here, on purpose and
differently — the number parser, the date parser, the email shape, the
whitespace rule. Two copies of one implementation agree about its bugs, and the
agreement gets reported as certification. These two were written to the same
specification, not from the same code.

The failure this module exists to catch is not a mangled value. It is the
**quietly rejected valid record**. A worker that drops the rows it finds awkward
and files them as exceptions produces a deliverable that is internally
consistent, passes every field-level check, and is short by an amount only the
source can reveal. So the central check is not "are the delivered records
correct" but "is this split the split the client's rules imply" — in both
directions.

Three verdicts and no fourth. ``PASS`` means checked and correct. ``FAIL`` means
checked and wrong. ``UNVERIFIABLE`` means this module could not tell, and it is
reported as its own answer rather than folded into either of the others.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import types

PASS, FAIL, UNVERIFIABLE = "PASS", "FAIL", "UNVERIFIABLE"

#: Checks this module can perform. A requirement naming anything else is
#: reported UNVERIFIABLE rather than passed.
CHECKS = frozenset({
    "map_fields", "constant_field", "require_field", "validate_type",
    "validate_range", "validate_enum", "validate_pattern",
    "normalise_whitespace", "normalise_case", "deduplicate_by",
    "select_fields", "records_reconcile", "no_invented_values",
    "exceptions_justified", "record_traceability", "parses_as_delivered",
})

#: Checks that need the whole brief rather than their own parameters, because
#: what they decide is a property of the accept/reject split as a whole.
WHOLE_JOB = frozenset({"records_reconcile", "no_invented_values",
                       "exceptions_justified", "record_traceability"})


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
        """For the dispatch contract. UNVERIFIABLE is not passed."""
        return self.verdict == PASS


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """The weakest verdict present. A FAIL anywhere fails the work.

        UNVERIFIABLE outranks PASS for the reason it exists: a job where half
        the requirements could not be checked has not been verified, and
        reporting the half that passed would be the misleading answer.
        """
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


# ------------------------------------------------------- value readers, again
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def number(value) -> Decimal | None:
    """``value`` as a Decimal, or None. Written separately from the worker's.

    Deliberately strict about shape before converting: the text is stripped of
    the currency and grouping decorations a client's export carries, and then it
    must *look* like a number before ``Decimal`` sees it. The worker reaches the
    same answers by handing cleaned text to ``Decimal`` and catching the
    exception. Two routes to one specification is the point.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    sign = Decimal(-1) if text.startswith("(") and text.endswith(")") else Decimal(1)
    if sign < 0:
        text = text[1:-1].strip()
    for decoration in ("USD", "GBP", "EUR", "$", "£", "€"):
        if text.startswith(decoration):
            text = text[len(decoration):].strip()
            break
    text = text.replace(",", "")
    if not _NUMERIC.match(text):
        return None
    try:
        return sign * Decimal(text)
    except InvalidOperation:
        return None


def day(value):
    """``value`` as a date, or None. ISO 8601 and a real calendar day only."""
    text = str(value or "").strip()
    if len(text) != 10:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def email_shaped(value) -> bool:
    """Structurally an address: one ``@``, something either side, a dotted domain.

    Says nothing about whether the address exists. Nothing available to this
    process could establish that, and a check that implied otherwise would be
    the kind of unsupported confidence this codebase refuses to ship.
    """
    text = str(value or "").strip()
    if text.count("@") != 1 or any(c.isspace() for c in text):
        return False
    local, domain = text.split("@")
    if not local or any(c in local for c in ",;"):
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(labels) and not any(
        c in domain for c in ",;")


def squeeze(value) -> str:
    """Trim the ends and collapse internal runs, via split rather than a regex."""
    return " ".join(str(value or "").split())


def cased(case: str, value) -> str:
    text = str(value or "")
    return {"upper": text.upper(), "lower": text.lower(),
            "title": text.title()}.get(case, text)


def type_holds(kind: str, value) -> bool:
    """Does ``value`` satisfy ``kind``? An empty value is not judged here."""
    text = str(value or "").strip()
    if not text or kind == "text":
        return True
    if kind == "integer":
        parsed = number(text)
        return parsed is not None and parsed == parsed.to_integral_value()
    if kind == "decimal":
        return number(text) is not None
    if kind == "date":
        return day(text) is not None
    if kind == "email":
        return email_shaped(text)
    return False


def within(bound: dict, value) -> bool | None:
    """Is ``value`` inside ``bound``? None when there is no ordering to use."""
    text = str(value or "").strip()
    if not text:
        return True
    stated = [str(bound[k]) for k in ("minimum", "maximum") if k in bound]
    if not stated:
        return None
    read = number if all(number(s) is not None for s in stated) else day
    if not all(read(s) is not None for s in stated):
        return None
    here = read(text)
    if here is None:
        return False
    low = read(str(bound["minimum"])) if "minimum" in bound else None
    high = read(str(bound["maximum"])) if "maximum" in bound else None
    if low is not None and here < low:
        return False
    if high is not None and here > high:
        return False
    return True


# -------------------------------------------------------------------- reading
class _Unreadable(Exception):
    pass


def read_table(path: str | Path) -> tuple[list[str], list[list[str]]]:
    """``(header, rows)`` from a delivered CSV. Ragged rows are not repaired."""
    try:
        text = Path(path).read_bytes().decode("utf-8-sig")
    except OSError as exc:
        raise _Unreadable(f"{path} could not be read: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise _Unreadable(f"{path} is not UTF-8 text: {exc}") from exc
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise _Unreadable(f"{path} will not parse as CSV: {exc}") from exc
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise _Unreadable(f"{path} has no rows at all, not even a header")
    return [str(c) for c in rows[0]], [[str(c) for c in r] for r in rows[1:]]


def read_source(path: str | Path) -> tuple[list[str], list[dict], dict[int, str]]:
    """``(columns, records, faults)`` from the source, independently of the worker.

    ``faults`` maps a source row number to why that record is structurally
    unusable. A record with a fault is expected in the exceptions file, and a
    verifier that ignored them would call a correctly held record a loss.
    """
    path = Path(path)
    if path.suffix.lower() == ".json":
        return _source_json(path)
    return _source_csv(path)


def _source_csv(path: Path) -> tuple[list[str], list[dict], dict[int, str]]:
    columns, rows = read_table(path)
    if len(set(columns)) != len(columns):
        raise _Unreadable("the source header names the same column twice")
    records, faults = [], {}
    # read_table drops blank rows, so re-read to keep the source row numbers the
    # worker reported. A verifier whose row numbers drift from the worker's
    # cannot tell a wrongly-held record from a correctly-held one.
    raw = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))
    numbered = [(i, r) for i, r in enumerate(raw, start=1)
                if any(str(c).strip() for c in r)]
    for position, (_, row) in enumerate(numbered[1:], start=2):
        if len(row) != len(columns):
            faults[position] = (f"the row has {len(row)} value(s) where the "
                                f"header names {len(columns)} column(s)")
            records.append((position, {}))
            continue
        records.append((position, {n: str(c) for n, c in zip(columns, row)}))
    return columns, records, faults


def _source_json(path: Path) -> tuple[list[str], list[dict], dict[int, str]]:
    try:
        parsed = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Unreadable(f"{path} will not parse as JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise _Unreadable("the source is JSON but not a list of records")
    columns, records, faults = [], [], {}
    for position, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            faults[position] = (f"the record is a {type(item).__name__}, "
                                "not an object")
            records.append((position, {}))
            continue
        record, bad = {}, ""
        for key, value in item.items():
            if str(key) not in columns:
                columns.append(str(key))
            if isinstance(value, (dict, list)):
                bad = f"the value of {key!r} is a {type(value).__name__}"
                continue
            if isinstance(value, bool):
                record[str(key)] = "true" if value else "false"
            else:
                record[str(key)] = "" if value is None else str(value)
        if bad:
            faults[position] = bad
            records.append((position, {}))
            continue
        records.append((position, record))
    return columns, records, faults


def read_exceptions(path: str | Path) -> list[dict]:
    """The exceptions file as records. Raises when it is not that file."""
    header, rows = read_table(path)
    wanted = ["source_row", "rules", "detail", "record"]
    if header != wanted:
        raise _Unreadable(
            f"{path} does not look like an exceptions file: its header is "
            f"{', '.join(header)} where {', '.join(wanted)} was expected")
    out = []
    for row in rows:
        if len(row) != len(wanted):
            raise _Unreadable(f"{path} has a row with {len(row)} value(s)")
        entry = dict(zip(wanted, row))
        try:
            entry["source_row"] = int(entry["source_row"])
        except ValueError as exc:
            raise _Unreadable(
                f"{path} names source row {entry['source_row']!r}") from exc
        entry["rules"] = tuple(r for r in entry["rules"].split(";") if r)
        out.append(entry)
    return out


def exceptions_beside(output: str | Path) -> Path:
    """Where the exceptions file sits for a given deliverable.

    A naming convention, and part of the deliverable contract rather than an
    assumption about the worker: a data-entry job hands over two files and the
    client has to be able to find the second one. A requirement may name it
    explicitly instead, and then that wins.
    """
    output = Path(output)
    return output.with_name(output.stem + "-exceptions.csv")


# ---------------------------------------------------------------- the re-derivation
def job_spec(requirements: list) -> dict:
    """The whole brief as one structure, derived from committed requirements.

    The whole-job checks need every rule, not their own parameters: whether a
    record *should* have been accepted depends on all of them at once. Building
    this here rather than accepting it from the caller means the verifier works
    from the committed requirements, which is the only version of the brief that
    anybody agreed to.
    """
    spec = {"mapping": {}, "constants": {}, "require": [], "types": {},
            "ranges": {}, "allowed": {}, "patterns": {},
            "normalise_whitespace": None, "normalise_case": {},
            "deduplicate_by": [], "fields": []}
    for requirement in requirements:
        check = types.check_of(requirement)
        params = dict(getattr(requirement, "params", {}) or {})
        if check == "map_fields":
            spec["mapping"].update(
                {str(k): str(v) for k, v in (params.get("mapping") or {}).items()})
        elif check == "constant_field":
            spec["constants"].update(
                {str(k): str(v) for k, v in (params.get("fields") or {}).items()})
        elif check == "require_field":
            spec["require"] += [str(f) for f in (params.get("fields") or [])]
        elif check == "validate_type":
            spec["types"].update(
                {str(k): str(v) for k, v in (params.get("types") or {}).items()})
        elif check == "validate_range":
            spec["ranges"].update(
                {str(k): v for k, v in (params.get("ranges") or {}).items()})
        elif check == "validate_enum":
            spec["allowed"].update(
                {str(k): [str(v) for v in values]
                 for k, values in (params.get("allowed") or {}).items()})
        elif check == "validate_pattern":
            spec["patterns"].update(
                {str(k): str(v) for k, v in (params.get("patterns") or {}).items()})
        elif check == "normalise_whitespace":
            named = params.get("fields")
            spec["normalise_whitespace"] = (
                [str(f) for f in named] if named else [])
        elif check == "normalise_case":
            spec["normalise_case"].update(
                {str(k): str(v) for k, v in (params.get("fields") or {}).items()})
        elif check == "deduplicate_by":
            spec["deduplicate_by"] = [str(f) for f in (params.get("fields") or [])]
        elif check == "select_fields":
            spec["fields"] = [str(f) for f in (params.get("fields") or [])]
    if not spec["fields"]:
        spec["fields"] = list(spec["mapping"]) + [
            f for f in spec["constants"] if f not in spec["mapping"]]
    return spec


def transcribe(record: dict, spec: dict) -> dict:
    """One source record as the delivered record the brief implies."""
    row = {target: record.get(column, "")
           for target, column in spec["mapping"].items()}
    row.update(spec["constants"])
    named = spec["normalise_whitespace"]
    if named is not None:
        for name in (named or list(spec["mapping"])):
            if name in row:
                row[name] = squeeze(row[name])
    for name, case in spec["normalise_case"].items():
        if name in row:
            row[name] = cased(case, row[name])
    return row


def broken_rules(row: dict, spec: dict) -> tuple[list[str], list[str]]:
    """Every rule this transcribed record breaks, and why. Written here, again."""
    broken, why = [], []

    def note(rule: str, detail: str) -> None:
        if rule not in broken:
            broken.append(rule)
        why.append(detail)

    for name in spec["require"]:
        if not str(row.get(name, "")).strip():
            note("require_field", f"{name} is required and is empty")
    for name, kind in spec["types"].items():
        if not type_holds(kind, row.get(name, "")):
            note("validate_type",
                 f"{name}: {row.get(name, '')!r} is not a {kind}")
    for name, bound in spec["ranges"].items():
        here = row.get(name, "")
        verdict = within(bound, here)
        if verdict is False:
            stated = [str(bound[k]) for k in ("minimum", "maximum") if k in bound]
            read = number if all(number(s) is not None for s in stated) else day
            if read(str(here)) is None:
                note("validate_range", f"{name}: {here!r} cannot be compared "
                                       "against the stated range")
            else:
                note("validate_range",
                     f"{name}: {here!r} is outside the stated range")
        elif verdict is None:
            note("validate_range", f"{name}: the stated range has no ordering")
    for name, values in spec["allowed"].items():
        here = str(row.get(name, ""))
        if here.strip() and here not in values:
            note("validate_enum", f"{name}: {here!r} is not an allowed value")
    for name, pattern in spec["patterns"].items():
        here = str(row.get(name, ""))
        if not here.strip():
            continue
        try:
            matched = re.match(pattern, here) is not None
        except re.error:
            note("validate_pattern", f"{name}: the stated pattern is unusable")
            continue
        if not matched:
            note("validate_pattern",
                 f"{name}: {here!r} does not match the stated pattern")
    return broken, why


def expected_split(records: list, faults: dict, spec: dict) -> tuple[list, dict, list]:
    """``(accepted, rejected, duplicates)`` the brief implies, re-derived here.

    ``accepted`` is ``(source_row, delivered_record)``; ``rejected`` maps a
    source row to the sorted rules it breaks; ``duplicates`` holds the source
    rows a stated deduplication key legitimately drops.
    """
    accepted, rejected, duplicates = [], {}, []
    seen = {}
    for position, record in records:
        if position in faults:
            rejected[position] = ["parses_as_delivered"]
            continue
        row = transcribe(record, spec)
        broken, _ = broken_rules(row, spec)
        if broken:
            rejected[position] = sorted(broken)
            continue
        if spec["deduplicate_by"]:
            key = tuple(row.get(f, "") for f in spec["deduplicate_by"])
            if key in seen:
                duplicates.append(position)
                continue
            seen[key] = position
        accepted.append((position, {f: row.get(f, "") for f in spec["fields"]}))
    return accepted, rejected, duplicates


# --------------------------------------------------------------------- checks
def verify(*, source: str | Path, output: str | Path, requirements: list,
           exceptions: str | Path = "") -> Report:
    """Check a delivered data-entry job against its committed requirements."""
    report = Report()
    spec = job_spec(requirements)
    exceptions_path = Path(exceptions) if exceptions else exceptions_beside(output)

    try:
        _, records, faults = read_source(source)
    except _Unreadable as exc:
        report.findings.append(Finding(
            "", "parses_as_delivered", UNVERIFIABLE,
            f"the source could not be read, so nothing can be compared: {exc}"))
        return report
    try:
        out_header, out_rows = read_table(output)
    except _Unreadable as exc:
        report.findings.append(Finding(
            "", "parses_as_delivered", FAIL,
            f"the deliverable is not a readable CSV: {exc}"))
        return report
    try:
        held = read_exceptions(exceptions_path)
    except _Unreadable as exc:
        held = None
        held_why = str(exc)

    delivered = [dict(zip(out_header, row)) for row in out_rows
                 if len(row) == len(out_header)]
    ragged = len(out_rows) - len(delivered)
    accepted, rejected, duplicates = expected_split(records, faults, spec)

    seen_checks = set()
    for requirement in requirements:
        check = types.check_of(requirement)
        rid = str(getattr(requirement, "id", "") or "")
        params = dict(getattr(requirement, "params", {}) or {})
        if not check:
            continue
        seen_checks.add(check)
        if check not in CHECKS:
            report.findings.append(Finding(
                rid, check, UNVERIFIABLE,
                f"{check!r} is not a check this verifier performs, so this "
                "requirement has not been checked by anybody"))
            continue
        if check in WHOLE_JOB and held is None:
            report.findings.append(Finding(
                rid, check, UNVERIFIABLE,
                f"the exceptions file could not be read: {held_why}"))
            continue
        report.findings.append(_decide(
            rid, check, params, spec, out_header, delivered, ragged,
            accepted, rejected, duplicates, held, records, faults))

    if not report.findings:
        report.findings.append(Finding(
            "", "parses_as_delivered", UNVERIFIABLE,
            "no requirement named a check, so there was nothing to verify"))
    return report


def _decide(rid, check, params, spec, out_header, delivered, ragged,
            accepted, rejected, duplicates, held, records, faults) -> Finding:
    if check == "parses_as_delivered":
        if ragged:
            return Finding(rid, check, FAIL,
                           f"{ragged} delivered row(s) do not have as many "
                           f"values as the header names {len(out_header)}")
        return Finding(rid, check, PASS,
                       f"the deliverable parses as CSV: {len(delivered)} "
                       f"record(s) across {len(out_header)} field(s)",
                       ", ".join(out_header))

    if check == "map_fields":
        return _mapped(rid, check, params, spec, delivered, accepted)
    if check == "constant_field":
        return _constant(rid, check, params, delivered)
    if check == "select_fields":
        wanted = [str(f) for f in params.get("fields", [])]
        if out_header == wanted:
            return Finding(rid, check, PASS,
                           "the delivered fields are the ones named, in order",
                           ", ".join(out_header))
        return Finding(rid, check, FAIL,
                       f"the delivered header is {', '.join(out_header)} where "
                       f"{', '.join(wanted)} was asked for")
    if check in ("require_field", "validate_type", "validate_range",
                 "validate_enum", "validate_pattern"):
        return _rule_holds(rid, check, params, delivered)
    if check in ("normalise_whitespace", "normalise_case"):
        return _normalised(rid, check, params, spec, delivered, accepted)
    if check == "deduplicate_by":
        return _deduplicated(rid, check, params, delivered, duplicates)
    if check == "record_traceability":
        return _traceable(rid, check, spec, delivered, accepted)
    if check == "records_reconcile":
        return _reconciled(rid, check, delivered, held, records, accepted,
                           rejected, duplicates)
    if check == "exceptions_justified":
        return _justified(rid, check, held, rejected, accepted)
    if check == "no_invented_values":
        return _not_invented(rid, check, spec, delivered, records)
    return Finding(rid, check, UNVERIFIABLE,
                   f"{check!r} is in the check list and has no implementation, "
                   "which is a defect in this module")


def _mapped(rid, check, params, spec, delivered, accepted) -> Finding:
    """Every delivered field the mapping names holds the value it should."""
    mapping = {str(k): str(v) for k, v in (params.get("mapping") or {}).items()}
    missing = [t for t in mapping if t not in set(
        f for row in delivered for f in row)] if delivered else list(mapping)
    if delivered and missing:
        return Finding(rid, check, FAIL,
                       f"the deliverable has no field(s): {', '.join(missing)}")
    expected = [row for _, row in accepted]
    if len(expected) != len(delivered):
        return Finding(rid, check, UNVERIFIABLE,
                       f"{len(delivered)} record(s) were delivered where the "
                       f"brief implies {len(expected)}; the mapping cannot be "
                       "compared record by record until that is reconciled")
    for index, (want, got) in enumerate(zip(expected, delivered), start=1):
        for target in mapping:
            if str(want.get(target, "")) != str(got.get(target, "")):
                return Finding(
                    rid, check, FAIL,
                    f"delivered record {index} has {target}="
                    f"{got.get(target, '')!r} where the source gives "
                    f"{want.get(target, '')!r}",
                    f"source column {mapping[target]}")
    return Finding(rid, check, PASS,
                   f"{len(mapping)} mapped field(s) match the source across "
                   f"{len(delivered)} record(s)")


def _constant(rid, check, params, delivered) -> Finding:
    fields = {str(k): str(v) for k, v in (params.get("fields") or {}).items()}
    for name, value in fields.items():
        wrong = [i for i, row in enumerate(delivered, start=1)
                 if str(row.get(name, "")) != value]
        if wrong:
            return Finding(rid, check, FAIL,
                           f"{len(wrong)} record(s) do not carry the stated "
                           f"constant {name}={value!r}; the first is record "
                           f"{wrong[0]}")
    return Finding(rid, check, PASS,
                   f"{len(fields)} constant field(s) carry the stated value in "
                   f"all {len(delivered)} record(s)")


def _rule_holds(rid, check, params, delivered) -> Finding:
    """No delivered record breaks this rule. Re-applied, not taken on trust."""
    failures = []
    for index, row in enumerate(delivered, start=1):
        if check == "require_field":
            for name in (str(f) for f in params.get("fields", [])):
                if not str(row.get(name, "")).strip():
                    failures.append(f"record {index}: {name} is empty")
        elif check == "validate_type":
            for name, kind in (params.get("types") or {}).items():
                if not type_holds(str(kind), row.get(str(name), "")):
                    failures.append(f"record {index}: {name}="
                                    f"{row.get(str(name), '')!r} is not a {kind}")
        elif check == "validate_range":
            for name, bound in (params.get("ranges") or {}).items():
                if within(bound, row.get(str(name), "")) is not True:
                    failures.append(f"record {index}: {name}="
                                    f"{row.get(str(name), '')!r} is outside "
                                    "the stated range")
        elif check == "validate_enum":
            for name, values in (params.get("allowed") or {}).items():
                here = str(row.get(str(name), ""))
                if here.strip() and here not in [str(v) for v in values]:
                    failures.append(f"record {index}: {name}={here!r} is not "
                                    "an allowed value")
        elif check == "validate_pattern":
            for name, pattern in (params.get("patterns") or {}).items():
                here = str(row.get(str(name), ""))
                if not here.strip():
                    continue
                try:
                    matched = re.match(str(pattern), here) is not None
                except re.error:
                    return Finding(rid, check, UNVERIFIABLE,
                                   f"the pattern stated for {name} is not a "
                                   "usable regular expression")
                if not matched:
                    failures.append(f"record {index}: {name}={here!r} does not "
                                    "match the stated pattern")
    if failures:
        return Finding(rid, check, FAIL,
                       f"{len(failures)} delivered value(s) break a rule the "
                       f"client stated. First: {failures[0]}",
                       "; ".join(failures[:5]))
    return Finding(rid, check, PASS,
                   f"every delivered record satisfies this rule across "
                   f"{len(delivered)} record(s)")


def _normalised(rid, check, params, spec, delivered, accepted) -> Finding:
    """The authorised normalisation was applied, and to nothing else.

    The second half is the one that matters. A worker that title-cases every
    field because one requirement asked for one field has changed client data
    nobody authorised changing, and the result looks tidier than the source,
    which is exactly why nobody queries it.
    """
    expected = [row for _, row in accepted]
    if len(expected) != len(delivered):
        return Finding(rid, check, UNVERIFIABLE,
                       f"{len(delivered)} record(s) delivered against "
                       f"{len(expected)} implied; normalisation cannot be "
                       "compared until the split is reconciled")
    for index, (want, got) in enumerate(zip(expected, delivered), start=1):
        for name in got:
            if str(want.get(name, "")) != str(got.get(name, "")):
                return Finding(
                    rid, check, FAIL,
                    f"delivered record {index} has {name}={got[name]!r} where "
                    f"the authorised transcription gives {want.get(name, '')!r}")
    named = (list((params.get("fields") or {}))
             if check == "normalise_case" else list(params.get("fields") or []))
    return Finding(rid, check, PASS,
                   f"the normalisation was applied where authorised"
                   + (f" ({', '.join(str(n) for n in named)})" if named
                      else " (every mapped field)")
                   + ", and no other value differs from the source")


def _deduplicated(rid, check, params, delivered, duplicates) -> Finding:
    fields = [str(f) for f in params.get("fields", [])]
    keys = [tuple(str(row.get(f, "")) for f in fields) for row in delivered]
    repeated = {k for k in keys if keys.count(k) > 1}
    if repeated:
        return Finding(rid, check, FAIL,
                       f"{len(repeated)} deduplication key(s) still appear "
                       "more than once in the deliverable",
                       f"first: {sorted(repeated)[0]}")
    return Finding(rid, check, PASS,
                   f"every delivered record has a distinct "
                   f"{'+'.join(fields)}; {len(duplicates)} source record(s) "
                   "were duplicates of an earlier one")


def _traceable(rid, check, spec, delivered, accepted) -> Finding:
    """Each delivered record is one distinct source record, transcribed."""
    expected = [row for _, row in accepted]
    if len(expected) != len(delivered):
        return Finding(rid, check, FAIL,
                       f"{len(delivered)} record(s) were delivered where the "
                       f"source and the stated rules imply {len(expected)}")
    rows = [tuple(sorted(r.items())) for r in delivered]
    if len(set(rows)) != len(rows):
        # Not automatically wrong — two source records can be identical — so
        # this is only a finding when deduplication was asked for.
        if spec["deduplicate_by"]:
            return Finding(rid, check, FAIL,
                           "two delivered records are identical although a "
                           "deduplication key was stated")
    for index, (want, got) in enumerate(zip(expected, delivered), start=1):
        if tuple(sorted(want.items())) != tuple(sorted(got.items())):
            return Finding(rid, check, FAIL,
                           f"delivered record {index} is not the transcription "
                           f"of source record {accepted[index - 1][0]}",
                           f"expected {want}, found {got}")
    return Finding(rid, check, PASS,
                   f"each of {len(delivered)} delivered record(s) is the "
                   "transcription of one distinct source record")


def _reconciled(rid, check, delivered, held, records, accepted, rejected,
                duplicates) -> Finding:
    """Every source record is accounted for exactly once, in one of three places."""
    total = len(records)
    counted = len(delivered) + len(held) + len(duplicates)
    if counted != total:
        return Finding(
            rid, check, FAIL,
            f"{total} source record(s) produced {len(delivered)} delivered + "
            f"{len(held)} held + {len(duplicates)} dropped as duplicates = "
            f"{counted}. {abs(total - counted)} record(s) are unaccounted for",
            "a record that is in none of the three places has been lost")
    overlap = {e["source_row"] for e in held} & {p for p, _ in accepted}
    if overlap:
        return Finding(rid, check, FAIL,
                       f"source row(s) {sorted(overlap)} appear both as "
                       "delivered and as held")
    return Finding(rid, check, PASS,
                   f"all {total} source record(s) are accounted for: "
                   f"{len(delivered)} delivered, {len(held)} held, "
                   f"{len(duplicates)} dropped as duplicates")


def _justified(rid, check, held, rejected, accepted) -> Finding:
    """Every held record truly breaks the rules named, and no valid one is held.

    Both directions, and the second is the one that catches the damaging case.
    A worker that holds back the records it found awkward delivers a file that
    is short and self-consistent; only the source says so.
    """
    expected_rows = set(rejected)
    held_rows = {e["source_row"] for e in held}
    wrongly_held = sorted(held_rows - expected_rows)
    if wrongly_held:
        return Finding(
            rid, check, FAIL,
            f"source row(s) {wrongly_held} were held as exceptions and break "
            "none of the stated rules; they should have been delivered",
            "a valid record held back is a short deliverable nobody can see")
    unheld = sorted(expected_rows - held_rows)
    if unheld:
        return Finding(
            rid, check, FAIL,
            f"source row(s) {unheld} break a stated rule and are not in the "
            "exceptions file")
    for entry in held:
        want = rejected.get(entry["source_row"], [])
        if sorted(entry["rules"]) != want:
            return Finding(
                rid, check, FAIL,
                f"source row {entry['source_row']} is held for "
                f"{', '.join(entry['rules']) or 'no stated rule'} where it "
                f"breaks {', '.join(want)}",
                "a rejection reason that does not match the defect is not an "
                "auditable rejection")
        if not str(entry["detail"]).strip():
            return Finding(rid, check, FAIL,
                           f"source row {entry['source_row']} is held with no "
                           "detail, so the client cannot act on it")
    return Finding(rid, check, PASS,
                   f"{len(held)} held record(s) each break the rules named, and "
                   f"each of the {len(accepted)} valid record(s) was delivered")


def _not_invented(rid, check, spec, delivered, records) -> Finding:
    """Every delivered value traces to the source or to a declared constant.

    Multiset containment, not set membership. Each delivered value consumes one
    occurrence from its source column, so a value delivered three times where
    the source holds it twice is an invented third copy — which a set
    comparison calls present and passes.

    What this check reaches and what it does not, stated plainly because the
    difference is where a real defect hides. It is a *column-wise* check: it
    establishes that every delivered value came from the right column of some
    source record. It cannot see a value grafted from the wrong *record* —
    record 4's reference beside record 5's client name is two real values in
    two real columns. That one belongs to ``record_traceability``, which
    compares whole records against the source records they claim to be, and
    these two checks are both required for exactly that reason.
    """
    remaining: dict[str, list[list[str]]] = {}
    for _, record in records:
        for column, value in record.items():
            remaining.setdefault(column, []).append(_forms(str(value)))
    constants = {str(v) for v in spec["constants"].values()}
    invented = []
    for index, row in enumerate(delivered, start=1):
        for name, value in row.items():
            text = str(value)
            if not text.strip() or text in constants:
                continue
            column = spec["mapping"].get(name)
            pool = remaining.get(column) if column else None
            position = next((i for i, forms in enumerate(pool or [])
                             if text in forms), None)
            if position is None:
                invented.append(f"record {index}: {name}={text!r}")
                continue
            pool.pop(position)
    if invented:
        return Finding(
            rid, check, FAIL,
            f"{len(invented)} delivered value(s) do not appear in the source "
            f"as often as they were delivered, and are not a declared "
            f"constant. First: {invented[0]}",
            "; ".join(invented[:5]))
    return Finding(rid, check, PASS,
                   f"every value in {len(delivered)} delivered record(s) "
                   "traces to a distinct occurrence in the source column it "
                   "was mapped from, or to a declared constant")


def _forms(source_value: str) -> set[str]:
    """Every value an authorised normalisation could turn this source cell into."""
    forms = {source_value, squeeze(source_value), source_value.strip()}
    for case in ("upper", "lower", "title"):
        forms.add(cased(case, source_value))
        forms.add(cased(case, squeeze(source_value)))
    return forms


def run(check_name: str, *, source, output, params: dict):
    """One check, for the capability dispatch table.

    The whole-job checks need the committed requirements rather than their own
    parameters, so they are reported UNVERIFIABLE through this entry point
    unless the caller passes ``params["requirements"]``. Saying so is the point:
    silently answering a question this entry point cannot answer is how a
    verifier ends up certifying a split it never examined.
    """
    if check_name not in CHECKS:
        return Finding("", check_name, UNVERIFIABLE,
                       f"no registered check named {check_name!r}")
    requirements = list((params or {}).get("requirements") or [])
    if not requirements:
        if check_name in WHOLE_JOB:
            return Finding("", check_name, UNVERIFIABLE,
                           f"{check_name} is a property of the whole accept/"
                           "reject split and needs the committed requirements, "
                           "which this call did not carry")
        requirements = [_One(check_name, dict(params or {}))]
    report = verify(source=source, output=output, requirements=requirements,
                    exceptions=(params or {}).get("exceptions", ""))
    for finding in report.findings:
        if finding.check == check_name:
            return finding
    return Finding("", check_name, UNVERIFIABLE,
                   f"{check_name} produced no finding, which is a defect in "
                   "this module")


@dataclass(frozen=True, slots=True)
class _One:
    """A single requirement, for the one-check entry point."""

    check: str
    params: dict
    id: str = ""
