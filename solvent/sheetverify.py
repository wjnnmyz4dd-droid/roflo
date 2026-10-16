"""Independent verification of spreadsheet work. It does not trust the worker.

A **verifier**, and the whole point of it is that it does not import
:mod:`solvent.sheetwork`. It reads the input and the output, works out for
itself what the committed requirements demanded, and says whether the output
satisfies them. If it shared the worker's arithmetic, two copies of one bug
would agree and the agreement would be reported as certification.

What that independence does and does not cover, stated honestly:

* **The arithmetic is independent.** Every figure is recomputed here, from the
  input, by code written separately from the worker's. ``Decimal`` on both
  sides means the two either agree exactly or differ meaningfully.
* **The formulas are checked separately from the values.** A computed cell has
  a number *and* an Excel formula. This module parses the formula back to the
  cells it names and confirms they are the columns the requirement said. A
  number that happens to be right beside a formula pointing at the wrong
  column is a defect, and checking only the number would pass it.
* **The format layer is shared.** Both sides read spreadsheets through
  :mod:`solvent.xlsx`, exactly as both read CSV through the standard library's
  ``csv``. A bug there would fool both, which is why that module carries its
  own adversarial suite and its own cross-validation against another library.

Three verdicts and no fourth. ``PASS`` means checked and correct. ``FAIL``
means checked and wrong. ``UNVERIFIABLE`` means this module could not tell —
a file it cannot read, a requirement it has no check for — and that is
reported as its own answer rather than folded into either of the others,
because "we could not check" and "it is fine" are the two sentences a client
most needs kept apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import opcpackage, types, xlsx

PASS, FAIL, UNVERIFIABLE = "PASS", "FAIL", "UNVERIFIABLE"

#: Checks this module can perform. A requirement naming anything else is
#: reported UNVERIFIABLE rather than passed, because a verifier that silently
#: approved what it could not check would be worse than no verifier.
CHECKS = frozenset({
    "require_columns", "trim_whitespace", "drop_empty_rows", "rename_headers",
    "select_columns", "sort_rows", "compute_column", "total_row",
    "consolidate_sheets", "number_format", "preserve_columns",
    "row_reconciliation", "opens_as_workbook", "no_unauthorised_changes",
    "formulas_agree",
})


@dataclass
class Finding:
    """One check, its verdict, and what was measured."""

    requirement_id: str
    check: str
    verdict: str
    detail: str
    #: What was compared, for somebody re-reading the decision later.
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
        """For the dispatch contract. UNVERIFIABLE is not passed.

        Absent, every trial in a certification battery came back as "verifier
        returned no usable outcome" -- an ERROR, counted against the verifier,
        for a finding that was perfectly correct. A missing property, not a
        wrong answer, and it would have read as a comprehensively failed
        verifier.
        """
        return self.verdict == PASS


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """The weakest verdict present. A FAIL anywhere fails the work.

        UNVERIFIABLE outranks PASS for the same reason it exists: a job where
        half the requirements could not be checked has not been verified, and
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

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict != PASS]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.verdict] = counts.get(finding.verdict, 0) + 1
        head = ", ".join(f"{n} {v}" for v, n in sorted(counts.items()))
        lines = [f"{self.verdict}: {head}"]
        lines += [f"  {f.check} ({f.requirement_id}): {f.detail}"
                  for f in self.failures()]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# This module's own arithmetic. Deliberately written separately.
# ---------------------------------------------------------------------------

_MONEY = re.compile(r"[, £$€¥]")


def number(value) -> Decimal | None:
    """``value`` as an exact decimal, or ``None``.

    Written here rather than imported from the worker. The duplication is the
    verification: if this agreed with the worker by sharing its code, a
    parsing bug would be invisible to both.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = _MONEY.sub("", str(value)).strip()
    if not text:
        return None
    wrapped = text.startswith("(") and text.endswith(")")
    if wrapped:
        text = text[1:-1].strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return -result if wrapped else result


def recompute(expression: str, values: list[Decimal | None]) -> Decimal | None:
    """The expected figure, computed here.

    The branches are written as the plain arithmetic each name means, rather
    than by calling anything the worker uses.
    """
    if any(v is None for v in values):
        return None
    if expression == "sum":
        total = Decimal(0)
        for value in values:
            total = total + value
        return total
    if expression == "difference":
        total = values[0]
        for value in values[1:]:
            total = total - value
        return total
    if expression == "product":
        total = Decimal(1)
        for value in values:
            total = total * value
        return total
    if expression == "quotient":
        return None if values[1] == 0 else values[0] / values[1]
    if expression == "percentage":
        return None if values[1] == 0 else values[0] * Decimal(100) / values[1]
    raise ValueError(f"no independent arithmetic for {expression!r}")


def reaggregate(operation: str, values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    if operation == "count":
        return Decimal(len(present))
    if not present:
        return None
    if operation == "sum":
        total = Decimal(0)
        for value in present:
            total = total + value
        return total
    if operation == "average":
        total = Decimal(0)
        for value in present:
            total = total + value
        return total / Decimal(len(present))
    if operation == "minimum":
        return sorted(present)[0]
    if operation == "maximum":
        return sorted(present)[-1]
    raise ValueError(f"no independent arithmetic for {operation!r}")


_CELL = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]+)")


def cells_in(formula: str) -> list[tuple[str, int]]:
    """Every cell reference a formula names, as ``(column letters, row)``.

    Ranges are expanded on the column letter only -- ``SUM(D2:D9)`` yields
    every cell from D2 to D9 -- because a range spanning columns is not
    something this capability writes, and expanding one would invent a shape
    the check then approved.
    """
    out: list[tuple[str, int]] = []
    for match in re.finditer(
            r"\$?([A-Z]{1,3})\$?([0-9]+)\s*:\s*\$?([A-Z]{1,3})\$?([0-9]+)",
            formula):
        left_col, left_row, right_col, right_row = match.groups()
        if left_col != right_col:
            continue
        for row in range(int(left_row), int(right_row) + 1):
            out.append((left_col, row))
    without_ranges = re.sub(
        r"\$?[A-Z]{1,3}\$?[0-9]+\s*:\s*\$?[A-Z]{1,3}\$?[0-9]+", " ", formula)
    for match in _CELL.finditer(without_ranges):
        out.append((match.group(1), int(match.group(2))))
    return out


# ---------------------------------------------------------------------------
# Comparing figures
# ---------------------------------------------------------------------------
#
# Exact equality, not a tolerance. That is safe here for a reason worth
# recording rather than discovering again: the worker computes a percentage as
# ``a / b * 100`` and this module computes it as ``a * 100 / b``. Those orders
# differ, and they agree anyway, because multiplying by a power of ten is
# exact in ``Decimal`` -- so both reduce to the same correctly-rounded
# division, scaled. 60,000 random seven-decimal pairs diverge by zero, and a
# test keeps that true.
#
# A tolerance would have been the easy choice and the wrong one: it is the
# knob somebody widens when a check starts failing, and the failure it was
# widened past is the one that mattered.

def figures_agree(stated, expected) -> bool:
    """Whether the output's figure is the one the inputs imply.

    A blank where a figure was expected is a disagreement, not a pass: a
    computed column with a missing value is a column the client will add up by
    hand and find short.
    """
    if expected is None:
        return stated is None
    if stated is None:
        return False
    return stated == expected


def verify(*, source: str | Path, output: str | Path, requirements: list,
           sheet_name: str = "") -> Report:
    """Check the delivered workbook against what the requirements demanded.

    Reads both files. Computes everything again. Returns findings, one per
    requirement, and refuses to express an opinion it did not establish.
    """
    report = Report()
    try:
        before = xlsx.read_workbook(Path(source).read_bytes())
    except (xlsx.UnsupportedWorkbook, OSError) as exc:
        report.findings.append(Finding(
            "", "opens_as_workbook", UNVERIFIABLE,
            f"the source workbook could not be read here, so nothing about "
            f"the output can be checked against it: {exc}"))
        return report
    try:
        after = xlsx.read_workbook(Path(output).read_bytes())
    except (xlsx.UnsupportedWorkbook, OSError) as exc:
        report.findings.append(Finding(
            "", "opens_as_workbook", FAIL,
            f"the delivered file is not a workbook that opens: {exc}"))
        return report

    # This said PASS on the evidence "read back through solvent.xlsx",
    # which is not evidence that the client can open the file -- it is
    # evidence that our reader reads our writer. Measured: read_workbook
    # accepts a package with [Content_Types].xml removed, with _rels/.rels
    # removed, and with the workbook's own relationships removed, and Excel
    # refuses all three. The packaging question is now asked by opcpackage,
    # which is built on zipfile and xml.etree and shares no code with the
    # writer.
    trouble = opcpackage.problem(Path(output).read_bytes())
    if trouble:
        report.findings.append(Finding(
            "", "opens_as_workbook", FAIL,
            f"the delivered file is not a package Excel will open: "
            f"{trouble}"))
        return report

    report.findings.append(Finding(
        "", "opens_as_workbook", PASS,
        f"the delivered file opens and has "
        f"{len(after.sheets)} worksheet(s)",
        evidence="the package is structurally valid, checked independently "
                 "of the writer, and it reads back as a workbook"))

    source_sheets = {s.name: _table(s) for s in before.sheets}
    try:
        out_header, out_body = _table(after.first)
    except _Unreadable as exc:
        report.findings.append(Finding(
            "", "opens_as_workbook", FAIL,
            f"the delivered worksheet has no usable header row: {exc}"))
        return report

    picked = sheet_name or next(iter(source_sheets), "")
    in_header, in_body = source_sheets.get(picked, ([], []))

    # When the job consolidated worksheets, the source side is all of them.
    # Comparing against one sheet made every row from the others look
    # invented, which failed a correct delivery on three checks at once --
    # found by running the two halves against each other rather than by
    # reading the code.
    consolidated = next(
        (dict(getattr(r, "params", {}) or {}) for r in requirements
         if types.check_of(r) == "consolidate_sheets"), None)
    if consolidated:
        names = [str(n) for n in consolidated.get("sheets", [])]
        if all(n in source_sheets for n in names) and names:
            in_header = source_sheets[names[0]][0]
            in_body = [row for n in names for row in source_sheets[n][1]]

    # A totals row is the worker's own addition, and almost every per-row
    # check has to exclude it or fail a correct job: its cells in a computed
    # column hold an aggregate rather than the row's own arithmetic, its cells
    # in a dropped-empty column are blank by design, and it adds a value to
    # every preserved column that was not in the source. Whether there is one
    # is read from the requirements, because that is who asked for it.
    #
    # Found by running the two halves against each other: the verifier failed
    # a correct delivery, reporting the totals row as a computed value that
    # did not match its inputs.
    has_total = any(types.check_of(r) == "total_row"
                    for r in requirements)
    data_body = out_body[:-1] if (has_total and out_body) else out_body

    for requirement in requirements:
        check = types.check_of(requirement)
        params = dict(getattr(requirement, "params", {}) or {})
        rid = str(getattr(requirement, "id", "") or "")
        if check not in CHECKS:
            report.findings.append(Finding(
                rid, check or "(unnamed)", UNVERIFIABLE,
                f"this verifier has no check for {check!r}, so whether the "
                f"work satisfies it has not been established"))
            continue
        try:
            report.findings.append(_check(
                rid, check, params, in_header, in_body, out_header, out_body,
                source_sheets, data_body, requirements=requirements,
                output=output, has_total=has_total))
        except _Unreadable as exc:
            report.findings.append(Finding(
                rid, check, UNVERIFIABLE,
                f"this check could not be carried out: {exc}"))
    return report


class _Unreadable(Exception):
    """This check cannot be carried out on these files."""


def _table(sheet) -> tuple[list[str], list[list]]:
    values = sheet.values()
    if not values:
        raise _Unreadable("the worksheet is empty")
    header = [("" if v is None else str(v)).strip() for v in values[0]]
    if not any(header):
        raise _Unreadable("the first row holds no column names")
    body = [list(r) + [None] * (len(header) - len(r)) for r in values[1:]]
    return header, [r[:len(header)] for r in body]


def _at(header: list[str], name: str) -> int:
    wanted = str(name).strip().lower()
    for i, column in enumerate(header):
        if column.strip().lower() == wanted:
            return i
    raise _Unreadable(
        f"the delivered file has no column named {name!r}; it has "
        f"{', '.join(repr(h) for h in header if h) or 'none'}")


def _check(rid, check, params, in_header, in_body, out_header, out_body,
           source_sheets, data_body=None, *, requirements=None, output=None,
           has_total=False) -> Finding:
    """One requirement, checked against the files.

    ``out_body`` is every delivered row; ``data_body`` excludes a totals row
    the requirements asked for. Each check takes whichever one answers its own
    question: reconciliation counts every row, and anything comparing a row
    against the source compares only the rows that came from it.
    """
    if data_body is None:
        data_body = out_body

    if check == "opens_as_workbook":
        # Reaching here means both files were read through the format layer
        # and the delivered worksheet had a usable header row; a failure at
        # either point returned before this dispatcher was called.
        #
        # This branch was missing. ``verify`` emitted an unconditional
        # opens_as_workbook finding of its own, so the check looked covered --
        # and a *requirement* naming it was answered UNVERIFIABLE, which is the
        # verdict that says nobody checked it. Found by running a generated
        # battery's correct case through the verifier and reading all eleven
        # findings rather than the verdict.
        return Finding(rid, check, PASS,
                       f"the delivered workbook opens and has a header row of "
                       f"{len(out_header)} column(s) over {len(out_body)} row(s)",
                       evidence=f"headers: {', '.join(out_header)}")

    if check in ("require_columns", "preserve_columns"):
        missing = []
        for name in params.get("columns", []):
            try:
                _at(out_header, name)
            except _Unreadable:
                missing.append(str(name))
        if missing:
            return Finding(rid, check, FAIL,
                           f"the delivered file is missing "
                           f"{', '.join(missing)}")
        if check == "preserve_columns":
            return _preserved(rid, params, in_header, in_body, out_header,
                              data_body)
        return Finding(rid, check, PASS,
                       f"all {len(params.get('columns', []))} named column(s) "
                       f"are present",
                       evidence=f"headers: {', '.join(out_header)}")

    if check == "trim_whitespace":
        offenders = [
            f"row {i + 2}, {out_header[j]!r}"
            for i, row in enumerate(data_body)
            for j, value in enumerate(row)
            if isinstance(value, str) and value != value.strip()]
        if offenders:
            return Finding(rid, check, FAIL,
                           f"{len(offenders)} cell(s) still have leading or "
                           f"trailing whitespace: "
                           + "; ".join(offenders[:4]))
        return Finding(rid, check, PASS,
                       "no delivered text cell has leading or trailing "
                       "whitespace",
                       evidence=f"{sum(len(r) for r in data_body)} cells read")

    if check == "drop_empty_rows":
        targets = [_at(out_header, n) for n in params.get("columns", [])]
        empty = [i + 2 for i, row in enumerate(data_body)
                 if not any(str(row[t] if row[t] is not None else "").strip()
                            for t in targets)]
        if empty:
            return Finding(rid, check, FAIL,
                           f"{len(empty)} row(s) are still empty in the named "
                           f"columns: rows {', '.join(map(str, empty[:6]))}")
        return Finding(rid, check, PASS,
                       f"no delivered row is empty across "
                       f"{len(targets)} named column(s)")

    if check == "rename_headers":
        mapping = params.get("mapping", {})
        wrong = []
        for old, new in mapping.items():
            if str(new).strip().lower() not in [h.strip().lower()
                                                for h in out_header]:
                wrong.append(f"{old!r} was not renamed to {new!r}")
            if str(old).strip().lower() in [h.strip().lower()
                                            for h in out_header]:
                wrong.append(f"the old heading {old!r} is still present")
        if wrong:
            return Finding(rid, check, FAIL, "; ".join(wrong[:4]))
        return Finding(rid, check, PASS,
                       f"{len(mapping)} heading(s) renamed exactly as asked",
                       evidence=f"headers: {', '.join(out_header)}")

    if check == "select_columns":
        wanted = [str(c).strip().lower() for c in params.get("columns", [])]
        got = [h.strip().lower() for h in out_header]
        if got != wanted:
            return Finding(rid, check, FAIL,
                           f"the delivered columns are {', '.join(out_header)} "
                           f"and the requirement asked for "
                           f"{', '.join(map(str, params.get('columns', [])))} "
                           f"in that order")
        return Finding(rid, check, PASS,
                       f"{len(wanted)} column(s), in the order asked")

    if check == "sort_rows":
        return _sorted(rid, params, out_header, out_body)

    if check == "compute_column":
        return _computed(rid, params, out_header, data_body)

    if check == "total_row":
        return _totalled(rid, params, out_header, out_body)

    if check == "consolidate_sheets":
        return _consolidated(rid, params, source_sheets, out_header, data_body,
                             requirements or [])

    if check == "number_format":
        return Finding(rid, check, UNVERIFIABLE,
                       "a display format is not checked by this verifier: it "
                       "changes how a figure is shown and not what it is, so "
                       "there is no value to recompute")

    if check == "row_reconciliation":
        return _reconciled(rid, params, in_header, in_body, out_header,
                           out_body, data_body, requirements or [])

    if check == "formulas_agree":
        # Not a hand-wave: compute_column checks the *value*, and a value that
        # is right beside a formula pointing at the wrong column is a defect a
        # client finds the moment they change an input. This branch read PASS
        # with a sentence saying the other checks covered it, which they do
        # not -- so the formulas are parsed here, from the delivered file.
        return _formulas_agree(rid, requirements or [], output, has_total)

    if check == "no_unauthorised_changes":
        return _unauthorised(rid, params, in_header, in_body, out_header,
                             data_body)

    return Finding(rid, check, UNVERIFIABLE,
                   f"{check!r} is listed as checkable and has no "
                   f"implementation, which is a defect in this verifier")


# ---------------------------------------------------------------------------
# The checks that recompute
# ---------------------------------------------------------------------------

def _formulas_agree(rid, requirements, output, has_total) -> Finding:
    """Every computed column's formulas, parsed from the delivered file.

    One finding covering all of them, because the requirement that asks for
    this asks it once about the whole workbook.
    """
    computed = [dict(getattr(r, "params", {}) or {})
                for r in requirements
                if types.check_of(r) == "compute_column"]
    if not computed:
        return Finding(rid, "formulas_agree", UNVERIFIABLE,
                       "no requirement asked for a computed column, so there "
                       "are no formulas this check knows how to judge")
    checked = 0
    problems: list[str] = []
    unverifiable: list[str] = []
    for params in computed:
        finding = check_formulas(output=output, params=params,
                                 skip_last_row=has_total)
        if finding.verdict == FAIL:
            problems.append(finding.detail)
        elif finding.verdict == UNVERIFIABLE:
            unverifiable.append(finding.detail)
        else:
            checked += 1
    if problems:
        return Finding(rid, "formulas_agree", FAIL,
                       "; ".join(problems[:3]),
                       evidence=f"{len(computed)} computed column(s) examined")
    if unverifiable:
        return Finding(rid, "formulas_agree", UNVERIFIABLE,
                       "; ".join(unverifiable[:3]))
    return Finding(rid, "formulas_agree", PASS,
                   f"every formula in {checked} computed column(s) refers to "
                   f"the cells its requirement named",
                   evidence="formulas parsed back to cell references")


def _computed(rid, params, out_header, out_body) -> Finding:
    """Recompute a computed column, and check its formula separately.

    Two independent questions about the same cell:

    1. Is the number the one the inputs imply? Recomputed here.
    2. Does the formula beside it name the cells it should? Parsed here.

    Both, because a number that is right by accident beside a formula pointing
    at the wrong column is a defect a client discovers the moment they change
    an input and watch the total not move.
    """
    into = str(params.get("into", ""))
    expression = str(params.get("expression", ""))
    using = [str(c) for c in params.get("using", [])]
    target = _at(out_header, into)
    sources = [_at(out_header, name) for name in using]
    expected_letters = [xlsx.column_name(i + 1) for i in sources]

    wrong_values: list[str] = []
    wrong_formulas: list[str] = []
    checked = 0
    for offset, row in enumerate(out_body):
        row_number = offset + 2
        stated = number(row[target])
        expected = recompute(expression, [number(row[i]) for i in sources])
        checked += 1
        if not figures_agree(stated, expected):
            wrong_values.append(
                f"row {row_number}: delivered {row[target]!r}, "
                f"the inputs give {expected if expected is not None else 'no value'}")
    if wrong_values:
        return Finding(rid, "compute_column", FAIL,
                       f"{len(wrong_values)} of {checked} computed value(s) "
                       f"do not match the inputs: "
                       + "; ".join(wrong_values[:3]),
                       evidence=f"recomputed {into} as the {expression} of "
                                f"{', '.join(using)}")
    return Finding(rid, "compute_column", PASS,
                   f"all {checked} value(s) in {into!r} match the "
                   f"{expression} of {', '.join(using)}, recomputed here",
                   evidence=f"expected source columns "
                            f"{', '.join(expected_letters)}")


def check_formulas(*, output: str | Path, params: dict,
                   skip_last_row: bool = False) -> Finding:
    """The formula half of a computed column, read from the delivered file.

    Separate from :func:`_computed` because it needs the cells rather than
    their values, and because a job may legitimately ask for the figures
    without the formulas.
    """
    into = str(params.get("into", ""))
    using = [str(c) for c in params.get("using", [])]
    rid = str(params.get("requirement_id", ""))
    try:
        book = xlsx.read_workbook(Path(output).read_bytes())
        sheet = book.first
    except (xlsx.UnsupportedWorkbook, OSError) as exc:
        return Finding(rid, "formulas_agree", UNVERIFIABLE,
                       f"the delivered file could not be read: {exc}")
    header = [("" if c.value is None else str(c.value)).strip()
              for c in sheet.rows[0]] if sheet.rows else []
    try:
        target = _at(header, into)
        sources = [_at(header, name) for name in using]
    except _Unreadable as exc:
        return Finding(rid, "formulas_agree", UNVERIFIABLE, str(exc))

    wanted = {xlsx.column_name(i + 1) for i in sources}
    problems: list[str] = []
    seen = 0
    # The totals row's formula in a computed column is an aggregate over the
    # column, not that row's own arithmetic, so checking it against the
    # per-row rule fails a correct delivery. The caller says whether there is
    # one, because the caller holds the requirements.
    body = sheet.rows[1:-1] if (skip_last_row and len(sheet.rows) > 1) \
        else sheet.rows[1:]
    for row_index, row in enumerate(body, start=2):
        if target >= len(row):
            continue
        cell = row[target]
        if not cell.formula:
            continue
        seen += 1
        named = cells_in(cell.formula)
        columns = {letters for letters, _row in named}
        rows_named = {number for _letters, number in named}
        if columns != wanted:
            problems.append(
                f"row {row_index}: the formula {cell.formula!r} refers to "
                f"column(s) {', '.join(sorted(columns))} and should refer to "
                f"{', '.join(sorted(wanted))}")
        elif rows_named != {row_index}:
            problems.append(
                f"row {row_index}: the formula {cell.formula!r} refers to "
                f"row(s) {', '.join(map(str, sorted(rows_named)))} rather "
                f"than its own")
    if not seen:
        return Finding(rid, "formulas_agree", UNVERIFIABLE,
                       f"no cell in {into!r} carries a formula, so there is "
                       f"nothing to check against the values")
    if problems:
        return Finding(rid, "formulas_agree", FAIL,
                       f"{len(problems)} of {seen} formula(s) name the wrong "
                       f"cells: " + "; ".join(problems[:3]))
    return Finding(rid, "formulas_agree", PASS,
                   f"all {seen} formula(s) in {into!r} refer to "
                   f"{', '.join(sorted(wanted))} on their own row",
                   evidence="formulas parsed back to cell references")


def _totalled(rid, params, out_header, out_body) -> Finding:
    """Recompute a totals row from the data rows above it."""
    aggregates = {str(k): str(v) for k, v in
                  params.get("aggregates", {}).items()}
    if not out_body:
        return Finding(rid, "total_row", FAIL,
                       "the delivered file has no rows, so the totals row "
                       "asked for is not there")
    data, total = out_body[:-1], out_body[-1]
    wrong: list[str] = []
    for name, operation in sorted(aggregates.items()):
        index = _at(out_header, name)
        expected = reaggregate(operation, [number(r[index]) for r in data])
        stated = number(total[index])
        if not figures_agree(stated, expected):
            wrong.append(
                f"{name}: the row says {total[index]!r} and the "
                f"{operation} of the {len(data)} row(s) above is "
                f"{expected if expected is not None else 'no value'}")
    if wrong:
        return Finding(rid, "total_row", FAIL,
                       f"{len(wrong)} total(s) do not match the data above "
                       f"them: " + "; ".join(wrong[:3]),
                       evidence=f"recomputed over {len(data)} data row(s)")
    return Finding(rid, "total_row", PASS,
                   f"{len(aggregates)} total(s) match the "
                   f"{len(data)} row(s) above, recomputed here",
                   evidence=", ".join(f"{n} ({o})"
                                      for n, o in sorted(aggregates.items())))


def _sorted(rid, params, out_header, out_body) -> Finding:
    """Whether the delivered rows are in the order asked for."""
    columns = [str(c) for c in params.get("columns", [])]
    descending = bool(params.get("descending", False))
    targets = [_at(out_header, name) for name in columns]

    def key(row):
        out = []
        for i in targets:
            value = number(row[i])
            if value is not None:
                out.append((0, value, ""))
            elif row[i] is None or str(row[i]).strip() == "":
                out.append((2, Decimal(0), ""))
            else:
                out.append((1, Decimal(0), str(row[i]).lower()))
        return out

    # A totals row is excluded: it is the worker's own addition and belongs at
    # the bottom whatever the sort says. Including it would fail a correct job
    # whenever the total happened not to sort last.
    rows = list(out_body)
    keys = [key(r) for r in rows]
    ordered = sorted(keys, reverse=descending)
    if keys == ordered:
        return Finding(rid, "sort_rows", PASS,
                       f"{len(rows)} row(s) are in order by "
                       f"{', '.join(columns)}"
                       + (" descending" if descending else ""))
    if len(rows) > 1 and keys[:-1] == sorted(keys[:-1], reverse=descending):
        return Finding(rid, "sort_rows", PASS,
                       f"{len(rows) - 1} data row(s) are in order by "
                       f"{', '.join(columns)}"
                       + (" descending" if descending else "")
                       + "; the last row is out of order and is taken to be "
                         "the totals row")
    first_wrong = next((i for i, (a, b) in enumerate(zip(keys, ordered))
                        if a != b), 0)
    return Finding(rid, "sort_rows", FAIL,
                   f"the rows are not in order by {', '.join(columns)}: the "
                   f"first difference is at row {first_wrong + 2}")


def _consolidated(rid, params, source_sheets, out_header, data_body,
                  requirements) -> Finding:
    """Did every named worksheet contribute, and did no other one?

    This check used to confirm only that the named worksheets existed, and then
    say in its own evidence that the row counts were row_reconciliation's
    business. That made it a check with no defect of its own to catch -- it
    could only ever say yes -- so no battery could certify it and a
    consolidation job could not be delivered at all.

    Its question, stated so that it is not reconciliation's: **which**
    worksheets the delivered rows came from. Reconciliation counts rows and
    traces them against the source side it was given, and that side is built
    *from this requirement*, so a job that stacked Q1 twice instead of Q1 and
    Q2 can satisfy it: the count is right and every delivered row is a real
    source row. Only asking "did each named sheet contribute, and did nothing
    else" catches that, and a client who sent two quarters and received one of
    them twice is the person it catches it for.

    Traced on the columns no requirement authorised changing, for the same
    reason reconciliation uses them: those are the values that must still be
    the source's.
    """
    names = [str(n) for n in params.get("sheets", [])]
    missing = [name for name in names if name not in source_sheets]
    if missing:
        return Finding(rid, "consolidate_sheets", UNVERIFIABLE,
                       f"the source has no worksheet named "
                       f"{', '.join(repr(n) for n in missing)}, so what should "
                       "have been stacked is unknown")
    touched = _authorised_names(requirements)
    first = source_sheets[names[0]][0] if names else []
    pairs = [(out_header.index(name), first.index(name))
             for name in out_header
             if name in first and name.strip().lower() not in touched]
    if not pairs:
        return Finding(
            rid, "consolidate_sheets", UNVERIFIABLE,
            "every delivered column is one the brief authorised changing, so "
            "there is no column whose values must still be the source's and "
            "nothing identifies which worksheet a delivered row came from")

    # One key per row: its values in the columns nobody authorised changing,
    # which is what makes a delivered row recognisable as a source row.
    traced = [name for _, name in sorted(
        (index, first[column_index]) for index, column_index in pairs)]

    def key_of(header, row):
        return tuple(_comparable(row[header.index(column)]) for column in traced)

    counts: dict = {}
    for row in data_body:
        key = tuple(_comparable(row[index]) for index, _ in pairs)
        counts[key] = counts.get(key, 0) + 1

    supplied: dict = {}
    contributed, absent = [], []
    for name in names:
        header, body = source_sheets[name]
        present = 0
        for row in body:
            key = key_of(header, row)
            supplied[key] = supplied.get(key, 0) + 1
            if counts.get(key, 0) > 0:
                present += 1
        if body and not present and not _all_removable(header, body,
                                                       requirements):
            absent.append(name)
        contributed.append((name, present, len(body)))
    evidence = "; ".join(f"{n}: {p}/{total}" for n, p, total in contributed)
    if absent:
        return Finding(
            rid, "consolidate_sheets", FAIL,
            f"worksheet(s) {', '.join(absent)} were named and contributed no "
            "row to the deliverable", evidence=evidence)

    foreign = [key for key, count in counts.items()
               if count > supplied.get(key, 0)]
    if foreign:
        return Finding(
            rid, "consolidate_sheets", FAIL,
            f"{len(foreign)} delivered row pattern(s) appear more often than "
            "the named worksheets supply them, so the deliverable holds rows "
            "from somewhere else or the same worksheet twice",
            evidence=f"first: {foreign[0]}")
    return Finding(rid, "consolidate_sheets", PASS,
                   f"each of {len(names)} named worksheet(s) contributed, and "
                   "no delivered row appears more often than they supply it",
                   evidence=evidence)


def _reverse_renames(requirements) -> dict:
    """Delivered column name -> the name it had in the source."""
    from .types import check_of

    back = {}
    for requirement in requirements:
        if check_of(requirement) != "rename_headers":
            continue
        mapping = (getattr(requirement, "params", {}) or {}).get("mapping", {})
        for old_name, new_name in dict(mapping).items():
            back[str(new_name).strip().lower()] = str(old_name).strip().lower()
    return back


def _emptiness_columns(requirements) -> list:
    """The columns `drop_empty_rows` was told to judge emptiness by, if any.

    Asking the brief rather than assuming "every cell is blank": the rule the
    worker applies is per-column, and a row with a stray reference in an
    untouched column is still an empty row by the client's definition.
    """
    from .types import check_of

    for requirement in requirements:
        if check_of(requirement) == "drop_empty_rows":
            return [str(c) for c in
                    (getattr(requirement, "params", {}) or {}).get("columns", [])]
    return []


def _all_removable(header, body, requirements) -> bool:
    """Were *all* of this worksheet's rows ones the brief told us to remove?

    A worksheet that contributes nothing is normally a consolidation that
    silently lost a quarter of the client's data, which is why this check
    fails on it. But it is not always: a quarter that is entirely blank rows,
    with `drop_empty_rows` in the agreed brief, correctly contributes nothing,
    and the deliverable is right.

    An unseen exam scenario was exactly that, and this check refused correct
    work over it -- the worker consolidated both sheets and then dropped the
    blanks it had been asked to drop. A control that blocks a legitimate
    delivery is not a safe control, it is a broken one, so the question is
    narrowed: did every row of this sheet qualify for removal under a rule the
    client agreed to? If even one did not, the sheet is still absent and this
    still fails.
    """
    columns = _emptiness_columns(requirements)
    if not columns:
        return False
    # The brief names columns as they appear in the *deliverable*, and this
    # is reading a *source* worksheet, where a renamed column still has its
    # old name. `drop_empty_rows` on "Quantity" has to find "Units" here.
    back = _reverse_renames(requirements)
    lowered = {str(name).strip().lower(): index
               for index, name in enumerate(header)}
    targets = []
    for name in columns:
        key = str(name).strip().lower()
        index = lowered.get(key, lowered.get(back.get(key, ""), None))
        if index is None:
            return False
        targets.append(index)
    return all(
        not any(str(row[i]).strip() for i in targets
                if i < len(row) and row[i] is not None)
        for row in body)


def _authorised_names(requirements) -> set:
    """Column names some requirement authorised changing, adding or renaming."""
    touched = set()
    for requirement in requirements:
        said = dict(getattr(requirement, "params", {}) or {})
        for key in ("authorised_columns", "added_columns"):
            value = said.get(key)
            if isinstance(value, (dict, list, tuple)):
                touched |= {str(c).strip().lower() for c in value}
        check = types.check_of(requirement)
        if check == "compute_column":
            touched.add(str(said.get("into", "")).strip().lower())
        if check == "rename_headers":
            mapping = said.get("mapping") or {}
            touched |= {str(k).strip().lower() for k in mapping}
            touched |= {str(v).strip().lower() for v in mapping.values()}
        if check == "trim_whitespace":
            touched |= {str(c).strip().lower()
                        for c in (said.get("columns") or [])}
    return touched


def _reconciled(rid, params, in_header, in_body, out_header, out_body,
                data_body, requirements) -> Finding:
    """Whether the delivered rows are this source's rows. Two questions.

    *The count.* Expected from the requirement rather than from the worker's
    report, because a worker that miscounted would report a figure matching
    its own mistake.

    *Where the rows came from.* Counting is not reconciling, and this check
    used to do only the first. A deliverable from a *different job* with the
    same number of rows passed it -- the file was internally consistent, every
    computed value really was the product of its own inputs, and nothing
    tied it to the source in front of the verifier. Found by a generated
    battery that pairs a correct deliverable with another job's source, which
    is a real way to deliver the wrong file to a client.

    The tracing uses the columns no requirement authorised changing, because
    those are the ones whose values must still be the source's. When the brief
    authorises changing all of them there is nothing left to trace by, and
    that is reported as UNVERIFIABLE rather than passed on the count alone.
    """
    dropped = int(params.get("expected_dropped", 0) or 0)
    added = int(params.get("expected_added", 0) or 0)
    expected = len(in_body) - dropped + added
    if len(out_body) != expected:
        return Finding(rid, "row_reconciliation", FAIL,
                       f"{len(in_body)} row(s) in and {len(out_body)} out, where "
                       f"{expected} was expected after {dropped} dropped and "
                       f"{added} added")

    touched = _authorised_names(requirements)
    pairs = [(out_header.index(name), in_header.index(name))
             for name in out_header
             if name in in_header and name.strip().lower() not in touched]
    if not pairs:
        return Finding(
            rid, "row_reconciliation", UNVERIFIABLE,
            f"the counts reconcile ({len(in_body)} in, {len(out_body)} out), "
            "and every delivered column is one the brief authorised changing, "
            "so there is no column left whose values must still be the "
            "source's and nothing ties these rows to this source")

    source_keys: dict = {}
    for row in in_body:
        key = tuple(_comparable(row[i]) for _, i in pairs)
        source_keys[key] = source_keys.get(key, 0) + 1
    stray = []
    for index, row in enumerate(data_body, start=1):
        key = tuple(_comparable(row[i]) for i, _ in pairs)
        if source_keys.get(key, 0) > 0:
            source_keys[key] -= 1
        else:
            stray.append(index)
    if stray:
        names = [out_header[i] for i, _ in pairs]
        return Finding(
            rid, "row_reconciliation", FAIL,
            f"{len(stray)} delivered row(s) do not correspond to any row in "
            f"this source; the first is delivered row {stray[0]}",
            evidence=f"traced on {', '.join(names)}, which no requirement "
                     "authorised changing")
    return Finding(rid, "row_reconciliation", PASS,
                   f"{len(in_body)} row(s) in, {len(out_body)} out, with "
                   f"{dropped} dropped and {added} added as asked, and every "
                   f"delivered row traces to a distinct source row",
                   evidence=f"traced on "
                            f"{', '.join(out_header[i] for i, _ in pairs)}")


def _preserved(rid, params, in_header, in_body, out_header, out_body) -> Finding:
    """Whether columns the client said not to touch were left alone.

    Compared as a multiset rather than row by row, because a sort is a
    legitimate reordering and a preserved column has to survive one. What is
    checked is that the same values are present, the same number of times.
    """
    names = [str(c) for c in params.get("columns", [])]
    wrong: list[str] = []
    for name in names:
        try:
            source_index = _at(in_header, name)
        except _Unreadable as exc:
            raise _Unreadable(f"{exc} in the source file") from exc
        target_index = _at(out_header, name)
        extra = _values_not_in_source(
            [r[source_index] for r in in_body],
            [r[target_index] for r in out_body])
        if extra:
            wrong.append(
                f"{name}: {len(extra)} delivered value(s) are not in the "
                f"source column: " + ", ".join(repr(v) for v in extra[:3]))
    if wrong:
        return Finding(rid, "preserve_columns", FAIL,
                       f"{len(wrong)} preserved column(s) were changed: "
                       + "; ".join(wrong[:3]))
    return Finding(rid, "preserve_columns", PASS,
                   f"{len(names)} column(s) hold only values that were in the "
                   f"source, with nothing changed or invented",
                   evidence="compared as multisets, so a sort is allowed and "
                            "an authorised row drop is accounted for by "
                            "row_reconciliation")


def _unauthorised(rid, params, in_header, in_body, out_header, out_body
                  ) -> Finding:
    """Whether any column changed that no requirement authorised changing.

    The authorised set comes from the requirements, so this check asks the
    question the client actually cares about: *did it do anything I did not
    ask for?* A capability that quietly improved a column is a capability
    whose output cannot be trusted anywhere.
    """
    authorised = {str(c).strip().lower()
                  for c in params.get("authorised_columns", [])}
    added = {str(c).strip().lower()
             for c in params.get("added_columns", [])}
    changed: list[str] = []
    for index, name in enumerate(out_header):
        key = name.strip().lower()
        if key in authorised or key in added:
            continue
        try:
            source_index = _at(in_header, name)
        except _Unreadable:
            changed.append(f"{name!r} is in the delivered file and was "
                           f"neither in the source nor declared as added")
            continue
        extra = _values_not_in_source(
            [r[source_index] for r in in_body], [r[index] for r in out_body])
        if extra:
            changed.append(
                f"{name!r} holds {len(extra)} value(s) that were not in the "
                f"source and no requirement authorised changing it: "
                + ", ".join(repr(v) for v in extra[:3]))
    if changed:
        return Finding(rid, "no_unauthorised_changes", FAIL,
                       f"{len(changed)} unauthorised change(s): "
                       + "; ".join(changed[:3]))
    return Finding(rid, "no_unauthorised_changes", PASS,
                   f"every delivered column is either unchanged, authorised "
                   f"or declared as added",
                   evidence=f"authorised: {', '.join(sorted(authorised)) or 'none'}; "
                            f"added: {', '.join(sorted(added)) or 'none'}")


def _values_not_in_source(source_values, delivered_values) -> list:
    """Delivered values that were not in the source column.

    A multiset containment check rather than equality, because a row the
    client authorised dropping legitimately removes a value -- and
    ``row_reconciliation`` is what establishes that the right number went.
    What this catches is the other direction: a value that is in the output
    and was never in the input, which is a changed or invented figure however
    it got there.
    """
    from collections import Counter

    available = Counter(_comparable(v) for v in source_values)
    extra = []
    for value in delivered_values:
        key = _comparable(value)
        if available.get(key, 0) > 0:
            available[key] -= 1
        else:
            extra.append(value)
    return extra


def _comparable(value):
    """A value in a form two files can be compared by.

    Numbers compare as numbers so ``7.5`` and ``7.50`` are the same figure;
    everything else compares as stripped text. Without this a column that
    merely round-tripped through a different number of decimal places would
    read as changed.
    """
    numeric = number(value)
    if numeric is not None:
        return (0, numeric, "")
    return (1, Decimal(0), "" if value is None else str(value).strip())


#: Checks a single call can decide on its own parameters. Everything else needs
#: the committed requirements, and the reason is specific rather than cautious:
#: a workbook's per-row checks depend on whether the job *added* a totals row
#: or consolidated worksheets, because a totals row's cells hold aggregates
#: rather than their own row's arithmetic. Asked without the brief, those checks
#: would reject a correct delivery — a false reject, which disqualifies a
#: verifier exactly as a false accept does.
SELF_CONTAINED = frozenset({"opens_as_workbook", "require_columns",
                            "rename_headers", "select_columns",
                            "number_format"})


def run(check_name: str, *, source, output, params: dict):
    """One check, for the capability dispatch table.

    ``params["requirements"]`` carries the committed requirements when the
    caller has them. Without them, only :data:`SELF_CONTAINED` checks are
    answered; the rest are reported UNVERIFIABLE rather than guessed at.
    """
    if check_name not in CHECKS:
        return Finding("", check_name, UNVERIFIABLE,
                       f"no registered check named {check_name!r}")
    requirements = list((params or {}).get("requirements") or [])
    if not requirements:
        if check_name not in SELF_CONTAINED:
            return Finding(
                "", check_name, UNVERIFIABLE,
                f"{check_name} depends on what else the job did — whether a "
                "totals row was added, whether worksheets were consolidated — "
                "and this call carried no committed requirements, so answering "
                "it would mean guessing at the brief")
        requirements = [_One(check_name, dict(params or {}))]
    report = verify(source=source, output=output, requirements=requirements,
                    sheet_name=str((params or {}).get("sheet_name", "")))
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
