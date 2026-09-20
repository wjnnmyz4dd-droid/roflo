"""Service capability #2 — structured reports from supplied facts.

Developed under an owner decision recorded through the capability registry. It
is a **worker**, not an authority: it turns a set of facts the client supplied
into a document, and decides nothing about whether the job was worth taking.

The hard part of document work is not formatting. It is **not making things up**.
A report that reads beautifully and contains one figure nobody supplied is worse
than no report, because the client cannot tell by looking. So this capability is
built the only way it can be verified: it renders **facts it was given**, and it
has no mechanism for producing a number, a name or a date that did not come from
the source. There is no model here and no generation — a fact that is missing
stays missing and is named as missing.

That is a real limitation and it is the point. "Write me a report about our
sales" is not this capability. "Here are the figures; lay them out under these
headings, total them, and tell me what is absent" is.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

#: Bumped when behaviour changes in a way that could alter an output.
VERSION = "report-builder/1.0"

#: What a section may do. Anything else is refused rather than approximated.
SECTION_KINDS = frozenset({
    "facts",      # render named facts from the source, verbatim
    "table",      # render source rows as a table
    "total",      # sum a numeric column and show it
    "count",      # count rows
    "missing",    # name the facts the client asked for and did not supply
    "text",       # literal text the client supplied, reproduced exactly
})

#: Checks that describe the finished document rather than a step to perform.
#: Named here so the worker can tell "do this" from "this must be true".
VERIFY_ONLY = frozenset({
    "report_has_sections", "report_no_invented_facts", "report_facts_rendered",
    "report_total_correct", "report_rows_tabulated", "report_missing_disclosed",
    "report_opens",
})

_NUMERIC = re.compile(r"-?[\d,]+\.?\d*")


@dataclass(slots=True)
class BuildReport:
    """What the builder did. Evidence for a reader; never proof of success."""

    sections: list = field(default_factory=list)
    facts_rendered: int = 0
    facts_missing: list = field(default_factory=list)
    rows_rendered: int = 0
    refused: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{len(self.sections)} section(s)",
                 f"{self.facts_rendered} fact(s) rendered",
                 f"{self.rows_rendered} row(s) tabulated"]
        if self.facts_missing:
            lines.append(f"{len(self.facts_missing)} fact(s) named as missing: "
                         + ", ".join(sorted(self.facts_missing)))
        return "\n".join(lines)


def load_source(path: str | Path) -> tuple[dict, list, str]:
    """``(facts, rows, problem)``.

    Accepts a JSON object of named facts (optionally with a ``rows`` list) or a
    CSV whose rows become the table. A file that will not parse is a refusal,
    never an empty report.
    """
    target = Path(path)
    if not target.exists():
        return {}, [], f"source not found: {target}"
    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [], f"source is not readable text: {exc}"
    if not text.strip():
        return {}, [], "source is empty; there are no facts to report"

    if target.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return {}, [], f"source is not valid JSON: {exc}"
        if not isinstance(data, dict):
            return {}, [], "source JSON must be an object of named facts"
        rows = data.get("rows") or []
        if rows and not all(isinstance(r, dict) for r in rows):
            return {}, [], "'rows' must be a list of objects"
        facts = {k: v for k, v in data.items() if k != "rows"}
        return facts, rows, ""

    try:
        rows = [dict(r) for r in csv.DictReader(text.splitlines())]
    except csv.Error as exc:
        return {}, [], f"source is not readable as CSV: {exc}"
    if not rows:
        return {}, [], "source has no data rows"
    return {}, rows, ""


def build(*, source: str | Path, destination: str | Path,
          requirements: list) -> BuildReport:
    """Render the report. Nothing is written unless every requirement can be met.

    A requirement this capability cannot satisfy refuses the whole job rather
    than producing a document with a quiet hole in it — a partial report looks
    exactly like a complete one.
    """
    report = BuildReport()
    facts, rows, problem = load_source(source)
    if problem:
        report.refused.append(problem)
        return report

    plan, refusals = _plan(requirements)
    report.refused.extend(refusals)
    if report.refused:
        return report

    lines: list[str] = []
    for spec in plan:
        rendered, problem = _render(spec, facts, rows, report)
        if problem:
            report.refused.append(f"{spec['requirement_id']}: {problem}")
            return report
        lines.extend(rendered)
        lines.append("")
        report.sections.append(spec["heading"])

    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report


def _plan(requirements: list) -> tuple[list, list]:
    plan, refusals = [], []
    for requirement in requirements:
        params = requirement.params or {}
        kind = params.get("kind")
        if requirement.check in VERIFY_ONLY:
            # A property of the finished document, not a step to carry out.
            # "Every figure traces to the source" is something to be true, not
            # something to do — and refusing those would refuse exactly the
            # requirements that catch the quietest failures.
            continue
        if requirement.check != "report_section":
            if requirement.criticality.blocks_delivery:
                refusals.append(f"{requirement.id}: {requirement.check!r} is not "
                                "an operation this capability performs")
            continue
        if kind not in SECTION_KINDS:
            refusals.append(f"{requirement.id}: section kind {kind!r} is not one "
                            f"of {', '.join(sorted(SECTION_KINDS))}")
            continue
        if not params.get("heading"):
            refusals.append(f"{requirement.id}: a section needs a heading")
            continue
        if kind in ("facts", "missing") and not params.get("fields"):
            refusals.append(f"{requirement.id}: a {kind} section needs the field "
                            "names to report; choosing them for the client would "
                            "be deciding what their report says")
            continue
        if kind in ("total", "count") and kind == "total" and not params.get("column"):
            refusals.append(f"{requirement.id}: a total needs the column to total")
            continue
        plan.append({"requirement_id": requirement.id, **params})
    return plan, refusals


def _render(spec, facts, rows, report) -> tuple[list, str]:
    kind, heading = spec["kind"], spec["heading"]
    out = [f"## {heading}", ""]

    if kind == "text":
        body = spec.get("body", "")
        if not body:
            return [], "a text section needs the text; this capability does not write it"
        out.append(body)
        return out, ""

    if kind == "facts":
        for field_name in spec["fields"]:
            value = facts.get(field_name)
            if value is None and rows and len(rows) == 1:
                value = rows[0].get(field_name)
            if value is None:
                # Named as absent, never invented and never silently dropped.
                report.facts_missing.append(field_name)
                out.append(f"- {field_name}: NOT SUPPLIED")
                continue
            report.facts_rendered += 1
            out.append(f"- {field_name}: {value}")
        return out, ""

    if kind == "missing":
        absent = [f for f in spec["fields"]
                  if facts.get(f) is None and not any(f in r for r in rows)]
        report.facts_missing.extend(absent)
        out.extend([f"- {f}" for f in absent] or ["- none"])
        return out, ""

    if kind == "table":
        if not rows:
            return [], "a table section needs rows, and the source supplied none"
        columns = spec.get("columns") or list(rows[0])
        missing = [c for c in columns if c not in rows[0]]
        if missing:
            return [], f"the source has no column(s) {missing}"
        out.append("| " + " | ".join(columns) + " |")
        out.append("|" + "|".join(["---"] * len(columns)) + "|")
        for row in rows:
            out.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
        report.rows_rendered += len(rows)
        return out, ""

    if kind == "count":
        out.append(f"- rows: {len(rows)}")
        return out, ""

    if kind == "total":
        column = spec["column"]
        if rows and column not in rows[0]:
            return [], f"the source has no column {column!r} to total"
        total, skipped = Decimal(0), 0
        for row in rows:
            raw = str(row.get(column, "")).strip()
            parsed = _to_decimal(raw)
            if parsed is None:
                skipped += 1
                continue
            total += parsed
        out.append(f"- {column} total: {total}")
        if skipped:
            # A value that would not parse is disclosed, not quietly dropped.
            out.append(f"- {skipped} value(s) could not be read as a number "
                       "and are excluded from this total")
        return out, ""

    return [], f"unreachable: unplanned section kind {kind!r}"


def _to_decimal(raw: str):
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
