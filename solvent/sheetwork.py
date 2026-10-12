"""Service capability #3 — spreadsheet work. Deterministic, and no model.

A **worker**, not an authority. It turns one workbook into another and reports
what it did. It decides nothing about whether the job was worth taking, whether
the result is acceptable, or whether anything may be delivered — the Governor,
the verifier and the Action Gate own those, and this module imports none of
them.

**It does only what it was told to do.** Every operation comes from an explicit
committed requirement with explicit parameters. "Tidy up this spreadsheet" is
not permission to delete a row that looks wrong, merge two columns that might
be the same thing, round a figure to something neater, or repair a total that
does not add up. A transformation nobody authorised is a defect even when it
improves the file, because the client did not ask for it and cannot check for
it.

**There is no model here.** The user's requirement is explicit and it is also
the only defensible design: *do not allow an AI model's unsupported confidence
statement to substitute for mathematical correctness*. Every number this
capability produces is computed by arithmetic in this module, from values in
the input, by a rule named in a requirement. Nothing is generated, inferred,
estimated or filled in.

**Formulas are written beside values, not instead of them.** When a
requirement asks for a computed column, this module computes the number *and*
writes the Excel formula that derives it. Three things follow, and all three
matter to a client:

* the file opens with a correct number even in a reader that does not evaluate
  formulas;
* a person opening it in Excel can see how each figure was derived, and can
  change an input and watch it update;
* the verifier can check the number and the formula **separately** — it
  recomputes the arithmetic from the inputs, and it parses the formula to
  confirm it refers to the cells it should. A number that is right by accident
  beside a formula pointing at the wrong column is a defect this catches.

What it will not do, named here so nobody finds out by trying: charts, pivot
tables, macros, conditional formatting, cell merging, images, and any
expression outside the small arithmetic vocabulary in :data:`EXPRESSIONS`. An
unsupported ask is a conversation with the client, not something to improvise.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import xlsx

VERSION = "spreadsheet-automation/1.0"

#: Operations this capability performs. Anything else is refused rather than
#: approximated.
OPERATIONS = frozenset({
    "require_columns",       # fail unless these headers exist
    "trim_whitespace",       # leading/trailing only; never inside a value
    "drop_empty_rows",       # a row with no value in any named column
    "rename_headers",        # explicit mapping only
    "select_columns",        # explicit list, in the order given
    "sort_rows",             # by named columns
    "compute_column",        # arithmetic over named columns, with a formula
    "total_row",             # a totals row for named numeric columns
    "consolidate_sheets",    # stack named sheets sharing a header
    "number_format",         # a display format for a named column
    "preserve_columns",      # a no-op that exists to be verified
})

#: Checked, never performed. "No row may be lost" is a property of the result
#: rather than a step to carry out.
VERIFY_ONLY = frozenset({"row_reconciliation", "opens_as_workbook",
                         "no_unauthorised_changes", "formulas_agree"})

#: The whole arithmetic vocabulary, and the Excel function each maps to.
#:
#: Small on purpose. A capability that accepted arbitrary expressions would be
#: one whose output nobody could verify without reimplementing an expression
#: evaluator in the verifier — and two evaluators that agree prove only that
#: they share a bug. Each entry here is checked by the verifier with its own
#: arithmetic, written separately.
EXPRESSIONS = {
    "sum": "SUM",             # add named columns together
    "difference": None,       # first column minus the rest
    "product": None,          # multiply named columns
    "quotient": None,         # first column divided by the second
    "percentage": None,       # first as a percentage of the second
}

#: How a totals row aggregates. Only operations whose result is defined for an
#: empty column, so a totals row on no data is 0 rather than an error.
AGGREGATES = {"sum": "SUM", "count": "COUNT", "average": "AVERAGE",
              "minimum": "MIN", "maximum": "MAX"}

_WS = re.compile(r"\s+")


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
class WorkReport:
    """What the worker did. Evidence for a human, never proof of success.

    Nothing here is trusted by the verifier, which recomputes from the files.
    """

    steps: list[Step] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    #: Rows the worker *added*, as opposed to rows that came from the input.
    #: Counted separately so "no row was lost" can be checked: a totals row
    #: folded into ``rows_out`` would hide one dropped data row behind it.
    rows_added: int = 0
    rows_dropped: int = 0
    sheets_in: int = 0
    dropped_features: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.refused

    def summary(self) -> str:
        lines = [f"{self.rows_in} data row(s) in, {self.rows_out} out "
                 f"({self.rows_dropped} dropped as asked, "
                 f"{self.rows_added} added by the worker)",
                 f"{self.sheets_in} worksheet(s) read"]
        lines += [f"{s.requirement_id}: {s.outcome}" for s in self.steps]
        if self.dropped_features:
            lines.append("not carried through: "
                         + "; ".join(self.dropped_features))
        if self.exceptions:
            lines.append(f"{len(self.exceptions)} exception(s) recorded")
        return "\n".join(lines)


def plan(requirements: list) -> tuple[list[Step], list[str]]:
    """Committed requirements into an ordered plan, or the reason why not.

    Returns ``(steps, refusals)``. A refusal is not a failure of the plan: it
    is the capability saying what it cannot do, which is information the owner
    needs before the job is accepted rather than after it is delivered.

    The order is fixed rather than taken from the requirements, because some
    operations only make sense after others. Consolidating sheets has to
    precede anything that reads a column; a totals row has to come after every
    computed column, or it would total figures that are not there yet. A plan
    that honoured the client's ordering would produce a different answer
    depending on how they happened to write the brief.
    """
    order = {op: i for i, op in enumerate((
        "consolidate_sheets", "require_columns", "trim_whitespace",
        "drop_empty_rows", "rename_headers", "compute_column",
        "select_columns", "sort_rows", "number_format", "total_row",
        "preserve_columns"))}
    steps: list[Step] = []
    refusals: list[str] = []
    for requirement in requirements:
        name = str(getattr(requirement, "check_name", "") or "")
        params = dict(getattr(requirement, "params", {}) or {})
        rid = str(getattr(requirement, "id", "") or "")
        if name in VERIFY_ONLY:
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
                          mandatory=str(getattr(requirement, "criticality",
                                                "MANDATORY")) != "ADVISORY"))
    steps.sort(key=lambda s: order.get(s.operation, len(order)))
    return steps, refusals


def _params_problem(op: str, params: dict) -> str:
    """Why this operation cannot be carried out as asked, or ``""``.

    Every message says what is missing rather than that something is invalid,
    because the reader is whoever has to go back to the client.
    """
    columns = params.get("columns")
    if op in ("require_columns", "drop_empty_rows", "select_columns",
              "sort_rows", "preserve_columns", "trim_whitespace"):
        if op == "trim_whitespace" and columns is None:
            return ""          # every text cell, which is the common ask
        if not isinstance(columns, (list, tuple)) or not columns:
            return "it needs a 'columns' list naming which columns"
        if any(not str(c).strip() for c in columns):
            return "one of the named columns is blank"
    if op == "rename_headers":
        mapping = params.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            return ("it needs a 'mapping' of old name to new name; this "
                    "capability never guesses a heading")
        if any(not str(k).strip() or not str(v).strip()
               for k, v in mapping.items()):
            return "a mapping entry has a blank name on one side"
    if op == "compute_column":
        into = str(params.get("into", "")).strip()
        using = params.get("using")
        expression = str(params.get("expression", "")).strip()
        if not into:
            return "it needs 'into', the name of the column to write"
        if expression not in EXPRESSIONS:
            return (f"it needs an 'expression' from "
                    f"{', '.join(sorted(EXPRESSIONS))}; {expression!r} is not "
                    f"one this capability can compute and verify")
        if not isinstance(using, (list, tuple)) or len(using) < 1:
            return "it needs 'using', the columns to compute from"
        if expression in ("quotient", "percentage") and len(using) != 2:
            return f"{expression} needs exactly two columns in 'using'"
        if expression == "difference" and len(using) < 2:
            return "difference needs at least two columns in 'using'"
    if op == "total_row":
        aggregates = params.get("aggregates")
        if not isinstance(aggregates, dict) or not aggregates:
            return ("it needs 'aggregates', a column name to one of "
                    + ", ".join(sorted(AGGREGATES)))
        unknown = sorted(set(map(str, aggregates.values())) - set(AGGREGATES))
        if unknown:
            return (f"it asks for {', '.join(unknown)}, and this capability "
                    f"computes {', '.join(sorted(AGGREGATES))}")
    if op == "consolidate_sheets":
        sheets = params.get("sheets")
        if not isinstance(sheets, (list, tuple)) or len(sheets) < 2:
            return "it needs 'sheets', naming at least two worksheets"
    if op == "number_format":
        if not str(params.get("column", "")).strip():
            return "it needs 'column', the column to format"
        if not str(params.get("format", "")).strip():
            return "it needs 'format', the display format code"
    return ""


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
#
# Decimal throughout, never float. A spreadsheet of money added up in binary
# floating point produces totals that are wrong in the last penny, and a
# client checking the column by hand finds it. Decimal also makes the
# verifier's independent arithmetic comparable: two Decimal computations of
# the same figures agree exactly or differ meaningfully, with no epsilon to
# argue about.

def as_number(value) -> Decimal | None:
    """``value`` as an exact decimal, or ``None`` if it is not a number.

    ``None`` rather than zero. A blank cell in a column of money is not a
    zero: treating it as one makes a total that looks right and is not, and
    nobody can see the difference by reading the output.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    # Thousands separators and a currency symbol are display, not data. A
    # column of "£1,234.50" is numeric to everybody except a parser.
    cleaned = text.replace(",", "").replace(" ", "").strip()
    for symbol in ("£", "$", "€", "¥"):
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    if cleaned.endswith("%"):
        # A percentage is kept as the number written, not divided by a hundred.
        # Dividing would be a transformation nobody asked for.
        cleaned = cleaned[:-1].strip()
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -number if negative else number


def compute(expression: str, values: list[Decimal | None]) -> Decimal | None:
    """One arithmetic rule over the values of the named columns.

    ``None`` when any input is missing, and that propagation is the point. A
    computed column with a gap in its inputs has a gap in its output; filling
    it with zero would be inventing data, and a client's total would be wrong
    by exactly the rows they could not see.
    """
    if any(v is None for v in values):
        return None
    if expression == "sum":
        return sum(values, Decimal(0))
    if expression == "difference":
        total = values[0]
        for value in values[1:]:
            total -= value
        return total
    if expression == "product":
        total = Decimal(1)
        for value in values:
            total *= value
        return total
    if expression == "quotient":
        if values[1] == 0:
            return None          # division by zero is not a number
        return values[0] / values[1]
    if expression == "percentage":
        if values[1] == 0:
            return None
        return values[0] / values[1] * Decimal(100)
    raise ValueError(f"{expression!r} is not an expression this module computes")


def aggregate(operation: str, values: list[Decimal | None]) -> Decimal | None:
    """One aggregate over a column, ignoring blanks.

    Blanks are skipped rather than counted as zero, for the same reason
    :func:`as_number` returns ``None``: an average over a column with gaps is
    the average of the figures that are there, and pretending the gaps are
    zeros changes the answer.
    """
    present = [v for v in values if v is not None]
    if operation == "count":
        return Decimal(len(present))
    if not present:
        return None
    if operation == "sum":
        return sum(present, Decimal(0))
    if operation == "average":
        return sum(present, Decimal(0)) / Decimal(len(present))
    if operation == "minimum":
        return min(present)
    if operation == "maximum":
        return max(present)
    raise ValueError(f"{operation!r} is not an aggregate this module computes")


def formula_for(expression: str, columns: list[int], row: int) -> str:
    """The Excel formula for a computed cell, as text without its ``=``.

    Written so the verifier can parse it back and confirm it names the cells
    the requirement said. Built from column indices rather than from a string
    the caller supplied, so a formula can never reference a column that is not
    the one the arithmetic used.
    """
    refs = [f"{xlsx.column_name(c)}{row}" for c in columns]
    if expression == "sum":
        return f"SUM({','.join(refs)})"
    if expression == "difference":
        return refs[0] + "".join(f"-{r}" for r in refs[1:])
    if expression == "product":
        return "*".join(refs)
    if expression == "quotient":
        return f"{refs[0]}/{refs[1]}"
    if expression == "percentage":
        return f"{refs[0]}/{refs[1]}*100"
    raise ValueError(f"{expression!r} has no formula in this module")


def aggregate_formula(operation: str, column: int, first: int, last: int) -> str:
    name = AGGREGATES[operation]
    ref = xlsx.column_name(column)
    return f"{name}({ref}{first}:{ref}{last})"


# ---------------------------------------------------------------------------
# Doing the work
# ---------------------------------------------------------------------------

def execute(*, source: str | Path, destination: str | Path,
            requirements: list, sheet_name: str = "") -> WorkReport:
    """Carry out the plan. Returns what was done; proves nothing.

    The verifier is what establishes whether the output is correct, and it
    recomputes from the two files rather than reading this report. A worker
    that could certify its own output would be a worker whose bugs certify
    themselves.

    The source is read once into memory. A capability that re-read the input
    while writing the output could produce a file derived from two different
    versions of it, and nothing downstream could tell.
    """
    report = WorkReport()
    source, destination = Path(source), Path(destination)
    steps, refusals = plan(requirements)
    report.steps, report.refused = steps, list(refusals)

    try:
        book = xlsx.read_workbook(source.read_bytes())
    except xlsx.UnsupportedWorkbook as exc:
        report.refused.append(f"the source workbook cannot be processed: {exc}")
        return report
    except OSError as exc:
        report.refused.append(f"the source workbook could not be read: {exc}")
        return report

    report.sheets_in = len(book.sheets)
    report.dropped_features = list(book.dropped)
    if report.refused:
        # A refusal is recorded before anything is written, and nothing is
        # written after one. Producing a partial deliverable for a brief this
        # capability could not honour is how a client receives work that looks
        # finished and is not.
        return report

    consolidate = next((s for s in steps
                        if s.operation == "consolidate_sheets"), None)
    try:
        if consolidate is not None:
            header, body = _consolidate(book, consolidate)
        else:
            sheet = book.sheet(sheet_name) if sheet_name else book.first
            header, body = _split_header(sheet)
    except xlsx.UnsupportedWorkbook as exc:
        report.refused.append(str(exc))
        return report
    except _Refused as refusal:
        report.refused.append(str(refusal))
        return report

    report.rows_in = len(body)
    formulas: dict[tuple[int, int], str] = {}
    formats: dict[int, str] = {}

    for step in steps:
        if step.operation == "consolidate_sheets":
            step.outcome = (f"stacked {len(step.params['sheets'])} worksheets "
                            f"into {len(body)} data row(s)")
            continue
        try:
            before = len(body)
            header, body = _apply(step, header, body, formulas, formats)
            if step.operation == "drop_empty_rows":
                report.rows_dropped += max(0, before - len(body))
            elif step.operation == "total_row":
                report.rows_added += max(0, len(body) - before)
        except _Refused as refusal:
            if step.mandatory:
                report.refused.append(f"{step.requirement_id}: {refusal}")
                return report
            step.outcome = f"skipped: {refusal}"
            report.exceptions.append(f"{step.requirement_id}: {refusal}")

    report.rows_out = len(body)
    out = xlsx.SheetOut(
        name="Result", rows=[list(header)] + [list(r) for r in body],
        formulas=formulas, number_formats=formats, freeze_below_row=2,
        widths={i: max(10.0, min(40.0, len(str(h)) + 4.0))
                for i, h in enumerate(header, 1)})
    try:
        destination.write_bytes(xlsx.write_workbook([out]))
    except (OSError, ValueError) as exc:
        report.refused.append(f"the result could not be written: {exc}")
    return report


class _Refused(Exception):
    """One step cannot be carried out as asked."""


def _split_header(sheet) -> tuple[list[str], list[list]]:
    values = sheet.values()
    if not values:
        raise _Refused("this worksheet is empty, so there is nothing to work on")
    header = [("" if v is None else str(v)).strip() for v in values[0]]
    if not any(header):
        raise _Refused("the first row is blank, so there are no column names "
                       "to work from; this capability does not invent them")
    body = [list(row) + [None] * (len(header) - len(row))
            for row in values[1:]]
    return header, [row[:len(header)] for row in body]


def _consolidate(book, step) -> tuple[list[str], list[list]]:
    """Stack worksheets that share a header.

    Refuses when the headers differ. Stacking columns that merely happen to
    be in the same position would silently put one sheet's prices into
    another's quantity column, and every figure in the result would be valid.
    """
    names = [str(n) for n in step.params["sheets"]]
    header: list[str] = []
    body: list[list] = []
    for name in names:
        sheet = book.sheet(name)
        this_header, this_body = _split_header(sheet)
        if not header:
            header = this_header
        elif [h.lower() for h in this_header] != [h.lower() for h in header]:
            raise _Refused(
                f"worksheet {name!r} has the columns "
                f"{', '.join(this_header) or '(none)'} and the first sheet has "
                f"{', '.join(header)}. This capability does not stack sheets "
                f"whose columns differ: doing so by position would put one "
                f"sheet's figures into another sheet's column")
        body.extend(this_body)
    return header, body


def _index(header: list[str], name: str) -> int:
    """The 0-based position of a column, matched case-insensitively.

    Raises rather than returning -1. A step that silently skipped a column the
    client named would produce output missing exactly the work they asked for.
    """
    wanted = str(name).strip().lower()
    for i, column in enumerate(header):
        if column.strip().lower() == wanted:
            return i
    raise _Refused(
        f"there is no column named {name!r}; this worksheet has "
        f"{', '.join(repr(h) for h in header if h) or 'no named columns'}")


def _apply(step: Step, header: list[str], body: list[list],
           formulas: dict, formats: dict) -> tuple[list[str], list[list]]:
    op, params = step.operation, step.params

    if op == "require_columns":
        for name in params["columns"]:
            _index(header, name)
        step.outcome = f"{len(params['columns'])} required column(s) present"
        return header, body

    if op == "preserve_columns":
        for name in params["columns"]:
            _index(header, name)
        step.outcome = (f"{len(params['columns'])} column(s) left untouched; "
                        f"the verifier checks this")
        return header, body

    if op == "trim_whitespace":
        names = params.get("columns")
        targets = (range(len(header)) if names is None
                   else [_index(header, n) for n in names])
        changed = 0
        for row in body:
            for i in targets:
                if isinstance(row[i], str):
                    trimmed = row[i].strip()
                    if trimmed != row[i]:
                        row[i] = trimmed
                        changed += 1
        step.outcome = f"trimmed {changed} cell(s); no value altered inside"
        return header, body

    if op == "drop_empty_rows":
        targets = [_index(header, n) for n in params["columns"]]
        kept = [row for row in body
                if any(str(row[i] if row[i] is not None else "").strip()
                       for i in targets)]
        step.outcome = f"dropped {len(body) - len(kept)} empty row(s)"
        return header, kept

    if op == "rename_headers":
        mapping = {str(k).strip().lower(): str(v)
                   for k, v in params["mapping"].items()}
        for old in mapping:
            _index(header, old)
        renamed = [mapping.get(h.strip().lower(), h) for h in header]
        step.outcome = f"renamed {len(mapping)} heading(s)"
        return renamed, body

    if op == "select_columns":
        picks = [_index(header, n) for n in params["columns"]]
        step.outcome = (f"kept {len(picks)} column(s) in the order asked, "
                        f"dropped {len(header) - len(picks)}")
        return ([header[i] for i in picks],
                [[row[i] for i in picks] for row in body])

    if op == "sort_rows":
        targets = [_index(header, n) for n in params["columns"]]
        descending = bool(params.get("descending", False))

        def key(row):
            # Numbers before text, blanks last, and never comparing a string
            # with a number -- which raises in Python and would fail the job
            # on a column that merely has one stray value in it.
            out = []
            for i in targets:
                number = as_number(row[i])
                if number is not None:
                    out.append((0, number, ""))
                elif row[i] is None or str(row[i]).strip() == "":
                    out.append((2, Decimal(0), ""))
                else:
                    out.append((1, Decimal(0), str(row[i]).lower()))
            return out

        step.outcome = (f"sorted by {', '.join(params['columns'])}"
                        + (" descending" if descending else ""))
        return header, sorted(body, key=key, reverse=descending)

    if op == "compute_column":
        return _compute_column(step, header, body, formulas, formats)

    if op == "number_format":
        formats[_index(header, params["column"]) + 1] = str(params["format"])
        step.outcome = (f"{params['column']} displays as "
                        f"{params['format']!r}; the stored value is unchanged")
        return header, body

    if op == "total_row":
        return _total_row(step, header, body, formulas)

    raise _Refused(f"{op!r} reached the executor without an implementation")


def _compute_column(step, header, body, formulas, formats):
    params = step.params
    into, expression = str(params["into"]), str(params["expression"])
    sources = [_index(header, n) for n in params["using"]]

    if into.strip().lower() in [h.strip().lower() for h in header]:
        raise _Refused(
            f"a column named {into!r} already exists. This capability will "
            f"not overwrite a column the client supplied: if the intention is "
            f"to replace it, that is a different instruction")

    header = list(header) + [into]
    gaps = 0
    for offset, row in enumerate(body):
        values = [as_number(row[i]) for i in sources]
        result = compute(expression, values)
        row.append(result)
        if result is None:
            gaps += 1
            continue
        # Row 1 is the header, so a body row at offset 0 is spreadsheet row 2.
        formulas[(offset + 2, len(header))] = formula_for(
            expression, [i + 1 for i in sources], offset + 2)
    if "format" in params:
        formats[len(header)] = str(params["format"])
    step.outcome = (
        f"computed {into} as the {expression} of "
        f"{', '.join(params['using'])}, with an Excel formula beside each "
        f"value" + (f"; {gaps} row(s) left blank because an input was missing "
                    f"or the divisor was zero" if gaps else ""))
    return header, body


def _total_row(step, header, body, formulas):
    aggregates = {str(k): str(v) for k, v in step.params["aggregates"].items()}
    targets = {_index(header, name): op for name, op in aggregates.items()}
    label = str(step.params.get("label", "TOTAL"))

    row: list = [None] * len(header)
    if header:
        row[0] = label
    first_data_row, last_data_row = 2, len(body) + 1
    total_row_number = len(body) + 2
    for index, operation in sorted(targets.items()):
        column_values = [as_number(r[index]) for r in body]
        row[index] = aggregate(operation, column_values)
        if body:
            formulas[(total_row_number, index + 1)] = aggregate_formula(
                operation, index + 1, first_data_row, last_data_row)
    if header and targets and 0 in targets:
        # The label would overwrite an aggregate asked for on the first
        # column. The aggregate is what was asked for; the label is decoration.
        row[0] = aggregate(targets[0], [as_number(r[0]) for r in body])
    step.outcome = (f"added a {label!r} row aggregating "
                    + ", ".join(f"{name} ({op})"
                                for name, op in sorted(aggregates.items())))
    return header, list(body) + [row]
