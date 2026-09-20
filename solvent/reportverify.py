"""Deterministic checks for report-builder/1.0.

Same independence rule as the CSV checks: every check re-reads the source and the
produced document from disk and recomputes the answer. It never imports the
worker and never sees what the worker believes it did.

The check that matters most is :func:`check_no_invented_facts`. A report is
dangerous precisely when it is fluent, and the failure a client cannot see is a
figure that appears in the document and nowhere in the data they supplied. So
every number in the deliverable is traced back to the source, and one that
cannot be traced fails the job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .reportwork import load_source
from .types import CheckResult

#: Numbers in the document that need no source: list markers, table rules,
#: and the row count the document itself states.
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    result: CheckResult
    detail: str
    computed: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.result.satisfies


def _lines(text: str) -> set:
    """The document's lines, stripped. Checks compare whole lines, never
    substrings: ``client: Acme Corp`` is a substring of ``client: Acme
    Corporation``, so a substring test passes on a corrupted value. That is
    the same mistake that let nine rephrasings past the scope matcher and a
    bank-redirect demand past the feedback classifier, arriving a third time.
    """
    return {line.strip() for line in text.splitlines()}


def _read(path) -> tuple[str, str]:
    target = Path(path)
    if not target.exists():
        return "", f"artifact not found: {path}"
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"artifact is not readable text: {exc}"
    if not text.strip():
        return "", "artifact is empty"
    return text, ""


def _source_numbers(facts, rows) -> set:
    """Every number the client supplied, normalised for comparison."""
    found = set()
    values = [str(v) for v in facts.values()]
    values += [str(v) for row in rows for v in row.values()]
    for value in values:
        for match in _NUMBER.findall(value):
            normalised = _normalise(match)
            if normalised is not None:
                found.add(normalised)
    return found


def _normalise(raw: str):
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def check_report_has_sections(source, output, params) -> CheckOutcome:
    """Every required heading is present, in the order the client asked for."""
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    headings = [line[3:].strip() for line in text.splitlines()
                if line.startswith("## ")]
    required = params.get("headings") or []
    if not required:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "no headings named")
    missing = [h for h in required if h not in headings]
    computed = {"required": required, "found": headings}
    if missing:
        return CheckOutcome(CheckResult.FAIL, f"missing section(s): {missing}",
                            computed)
    if params.get("ordered"):
        positions = [headings.index(h) for h in required]
        if positions != sorted(positions):
            return CheckOutcome(CheckResult.FAIL,
                                "sections are not in the requested order", computed)
    return CheckOutcome(CheckResult.PASS,
                        f"all {len(required)} section(s) present", computed)


def check_no_invented_facts(source, output, params) -> CheckOutcome:
    """Every number in the document traces back to the source.

    The failure this exists for is the one a client cannot see: a fluent report
    containing a figure nobody supplied. Totals are allowed because they are
    derived by arithmetic the check repeats; everything else must appear in the
    data the client handed over.
    """
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    facts, rows, source_problem = load_source(source)
    if source_problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, source_problem)

    supplied = _source_numbers(facts, rows)
    derivable = set(supplied)
    derivable.add(Decimal(len(rows)))          # a stated row count
    for column in {k for row in rows for k in row}:
        total = Decimal(0)
        any_parsed = False
        for row in rows:
            parsed = _normalise(str(row.get(column, "")).replace("$", "").strip())
            if parsed is not None:
                total += parsed
                any_parsed = True
        if any_parsed:
            derivable.add(total)
    # A count of unreadable values is disclosed by the worker and is derivable.
    derivable.update(Decimal(n) for n in range(0, len(rows) + 1))

    unexplained = []
    for line in text.splitlines():
        if line.startswith("|---") or set(line.strip()) <= {"|", "-", " "}:
            continue
        for match in _NUMBER.findall(line):
            value = _normalise(match)
            if value is None or value in derivable:
                continue
            unexplained.append(f"{match!r} in {line.strip()[:48]!r}")
    computed = {"source_numbers": len(supplied), "unexplained": len(unexplained),
                "examples": unexplained[:4]}
    if unexplained:
        return CheckOutcome(
            CheckResult.FAIL,
            f"{len(unexplained)} number(s) appear in the report but not in the "
            "source", computed)
    return CheckOutcome(CheckResult.PASS,
                        "every figure traces to the supplied data", computed)


def _section_of(text: str, heading: str) -> list[str] | None:
    """The lines under ``## heading``, up to the next heading. None if absent."""
    wanted = f"## {heading}".strip()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == wanted:
            body = []
            for following in lines[index + 1:]:
                if following.startswith("## "):
                    break
                body.append(following.strip())
            return body
    return None


def check_section_present(source, output, params) -> CheckOutcome:
    """The named section exists and contains what that kind of section is for.

    This check used to be an alias for "the document opens", which meant a
    requirement saying *"an Overview section naming the client and period"* was
    satisfied by any document with any heading at all. Verifier certification
    caught it: the check name was registered, so requirements naming it could be
    committed, but it had never demonstrated it could detect anything about a
    section.

    Checking the *section* rather than the whole document is also what catches a
    fact rendered under the wrong heading — correct content, wrong place — which
    a document-wide search cannot see.
    """
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    heading = params.get("heading")
    if not heading:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION,
                            "no heading named, so there is no section to find")
    body = _section_of(text, heading)
    if body is None:
        return CheckOutcome(CheckResult.FAIL,
                            f"the document has no section headed {heading!r}",
                            {"heading": heading})
    content = [line for line in body if line]
    if not content:
        return CheckOutcome(CheckResult.FAIL,
                            f"section {heading!r} is empty", {"heading": heading})

    kind = params.get("kind")
    within = set(content)
    computed = {"heading": heading, "kind": kind, "lines": len(content)}

    if kind == "facts":
        facts, rows, source_problem = load_source(source)
        if source_problem:
            return CheckOutcome(CheckResult.UNVERIFIABLE, source_problem)
        missing = []
        for name in params.get("fields") or []:
            value = facts.get(name)
            if value is None and len(rows) == 1:
                value = rows[0].get(name)
            expected = (f"- {name}: NOT SUPPLIED" if value is None
                        else f"- {name}: {value}")
            if expected not in within:
                missing.append(name)
        computed["missing"] = missing
        if missing:
            return CheckOutcome(
                CheckResult.FAIL,
                f"section {heading!r} does not carry fact(s) {missing}; a fact "
                "rendered elsewhere is not a fact rendered here", computed)

    elif kind == "table":
        columns = params.get("columns") or []
        header = [line for line in content if line.startswith("|")]
        if not header:
            return CheckOutcome(CheckResult.FAIL,
                                f"section {heading!r} contains no table", computed)
        cells = {c.strip() for c in header[0].strip("|").split("|")}
        missing = [c for c in columns if c not in cells]
        computed["missing_columns"] = missing
        if missing:
            return CheckOutcome(CheckResult.FAIL,
                                f"table in {heading!r} lacks column(s) {missing}",
                                computed)

    elif kind == "total":
        column = params.get("column")
        if column and not any(line.startswith(f"- {column} total:")
                              for line in content):
            return CheckOutcome(
                CheckResult.FAIL,
                f"section {heading!r} states no total for {column!r}", computed)

    elif kind == "text":
        body_text = (params.get("body") or "").strip()
        if body_text and body_text not in within:
            return CheckOutcome(CheckResult.FAIL,
                                f"section {heading!r} does not carry the supplied "
                                "text", computed)

    return CheckOutcome(CheckResult.PASS,
                        f"section {heading!r} present with {len(content)} line(s)",
                        computed)


def check_facts_rendered(source, output, params) -> CheckOutcome:
    """Named facts appear with the client's own values, byte for byte."""
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    facts, rows, source_problem = load_source(source)
    if source_problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, source_problem)
    fields = params.get("fields") or []
    if not fields:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "no fields named")

    wrong, absent = [], []
    for name in fields:
        value = facts.get(name)
        if value is None and len(rows) == 1:
            value = rows[0].get(name)
        if value is None:
            if f"- {name}: NOT SUPPLIED" not in _lines(text):
                absent.append(name)
            continue
        if f"- {name}: {value}" not in _lines(text):
            wrong.append(name)
    computed = {"fields": fields, "wrong": wrong, "unlabelled_absent": absent}
    if wrong:
        return CheckOutcome(CheckResult.FAIL,
                            f"fact(s) missing or altered: {wrong}", computed)
    if absent:
        return CheckOutcome(
            CheckResult.FAIL,
            f"fact(s) the source does not supply were neither rendered nor "
            f"marked NOT SUPPLIED: {absent}", computed)
    return CheckOutcome(CheckResult.PASS, f"{len(fields)} fact(s) match the source",
                        computed)


def check_total_correct(source, output, params) -> CheckOutcome:
    """The stated total is the sum the check computes itself."""
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    facts, rows, source_problem = load_source(source)
    if source_problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, source_problem)
    column = params.get("column")
    if not column:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "no column named")
    if rows and column not in rows[0]:
        return CheckOutcome(CheckResult.FAIL, f"the source has no column {column!r}")

    expected = Decimal(0)
    for row in rows:
        parsed = _normalise(str(row.get(column, "")).replace("$", "").strip())
        if parsed is not None:
            expected += parsed
    stated = f"- {column} total: {expected}"
    computed = {"column": column, "expected": str(expected)}
    if stated not in _lines(text):
        return CheckOutcome(CheckResult.FAIL,
                            f"the report does not state {column} total = {expected}",
                            computed)
    return CheckOutcome(CheckResult.PASS, f"{column} totals to {expected}", computed)


def check_rows_tabulated(source, output, params) -> CheckOutcome:
    """Every source row reaches the table; none is added."""
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    facts, rows, source_problem = load_source(source)
    if source_problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, source_problem)
    if not rows:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "the source has no rows")

    body = [line for line in text.splitlines()
            if line.startswith("| ") and not set(line.strip()) <= {"|", "-", " "}]
    data_rows = max(len(body) - 1, 0)      # minus the header row
    computed = {"source_rows": len(rows), "table_rows": data_rows}
    if data_rows != len(rows):
        return CheckOutcome(CheckResult.FAIL,
                            f"{len(rows)} source row(s), {data_rows} tabulated",
                            computed)

    # Counting was all this check used to do, and a count is not an identity:
    # another job's report with the same number of lines passed. Generated
    # certification caught it on a cross-job artifact, which is the shape of the
    # worst version of this — a real, correct, well-formed report belonging to
    # somebody else. So the values have to be here too.
    # Compared column by column, and only for the columns the table actually
    # carries. A client may legitimately ask for two of six columns, so
    # demanding every supplied value appear would refuse correct work — an
    # earlier version of this fix did exactly that. What must hold is that the
    # columns which *are* tabulated carry this source's values, counted as a
    # multiset so a reordering passes and a substitution does not.
    from collections import Counter

    columns = [c.strip() for c in body[0].strip().strip("|").split("|")]
    table = [[c.strip() for c in line.strip().strip("|").split("|")]
             for line in body[1:]]
    mismatched = []
    for position, name in enumerate(columns):
        if not any(name in row for row in rows):
            continue
        supplied = Counter(str(row.get(name, "")).strip() for row in rows)
        rendered = Counter(row[position].strip() for row in table
                           if position < len(row))
        if supplied != rendered:
            only_supplied = sorted((supplied - rendered).elements())[:3]
            mismatched.append(f"{name}: {only_supplied} missing from the table")
    computed["mismatched_columns"] = mismatched[:4]
    if mismatched:
        return CheckOutcome(
            CheckResult.FAIL,
            f"the table has {data_rows} rows but {len(mismatched)} column(s) do "
            f"not carry this source's values ({'; '.join(mismatched[:2])})",
            computed)
    return CheckOutcome(CheckResult.PASS,
                        f"{len(rows)} row(s) tabulated with the supplied values",
                        computed)


def check_missing_disclosed(source, output, params) -> CheckOutcome:
    """A fact the client asked for and did not supply is named, not skipped."""
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    facts, rows, source_problem = load_source(source)
    if source_problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, source_problem)
    fields = params.get("fields") or []
    if not fields:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "no fields named")
    absent = [f for f in fields
              if facts.get(f) is None and not any(f in r for r in rows)]
    undisclosed = [f for f in absent
                   if f"- {f}" not in _lines(text)
                   and f"- {f}: NOT SUPPLIED" not in _lines(text)]
    computed = {"absent": absent, "undisclosed": undisclosed}
    if undisclosed:
        return CheckOutcome(CheckResult.FAIL,
                            f"absent fact(s) not disclosed: {undisclosed}", computed)
    return CheckOutcome(CheckResult.PASS,
                        f"{len(absent)} absent fact(s) disclosed", computed)


def check_opens_as_text(source, output, params) -> CheckOutcome:
    """The deliverable must open and contain something."""
    text, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.FAIL, problem)
    headings = sum(1 for line in text.splitlines() if line.startswith("## "))
    if not headings:
        return CheckOutcome(CheckResult.FAIL, "the document has no sections")
    return CheckOutcome(CheckResult.PASS,
                        f"opens; {headings} section(s), {len(text)} characters",
                        {"sections": headings, "characters": len(text)})


CHECKS = {
    "report_has_sections": check_report_has_sections,
    "report_no_invented_facts": check_no_invented_facts,
    "report_facts_rendered": check_facts_rendered,
    "report_total_correct": check_total_correct,
    "report_rows_tabulated": check_rows_tabulated,
    "report_missing_disclosed": check_missing_disclosed,
    "report_opens": check_opens_as_text,
    # A section requirement is executed by the worker and checked as a whole by
    # the document-level checks above.
    "report_section": check_section_present,
}


def run(check_name: str, *, source, output, params: dict) -> CheckOutcome:
    fn = CHECKS.get(check_name)
    if fn is None:
        return CheckOutcome(CheckResult.UNVERIFIABLE,
                            f"no registered check named {check_name!r}")
    try:
        return fn(source, output, params)
    except Exception as exc:  # noqa: BLE001 - a crashed check is not a pass
        return CheckOutcome(CheckResult.UNVERIFIABLE,
                            f"{check_name} raised {type(exc).__name__}: {exc}")
