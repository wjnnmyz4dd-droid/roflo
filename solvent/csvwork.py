"""Service capability #1 — CSV cleanup. The first thing Solvent can actually do.

This is a **worker**, not an authority. It transforms one file into another and
reports what it did. It decides nothing about whether the job was worth taking,
whether the result is acceptable, or whether anything may be delivered — the
Governor, the verifier and the Action Gate own those, and this module never
imports them.

**It does only what it was told to do.** Every operation is driven by an explicit
requirement with explicit parameters. "Clean this data" is not permission to drop
rows that look wrong, guess a missing value, merge two customers who might be the
same, repair an identifier or round a number. A transformation nobody authorised
is a defect even when it improves the file, because the client did not ask for it
and cannot check for it.

**It never touches the source.** The input is opened read-only and a new file is
written. The original stays as it arrived, which is what makes verification
possible at all: every check recomputes from the source, and a source that had
been edited in place would leave nothing to compare against.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .errors import FailClosed

#: Bumped when behaviour changes in a way that could alter an output. Recorded
#: on every artifact so a deliverable can always be traced to the code that made
#: it, and so a capability proven at one version is not assumed proven at another.
VERSION = "csv-cleanup/1.0"

#: Operations this capability performs. Anything not on this list is refused
#: rather than approximated — an unsupported ask is a conversation with the
#: client, not something to improvise.
OPERATIONS = frozenset({
    "require_columns",      # fail unless these headers exist
    "drop_exact_duplicates",  # byte-identical rows, after normalising whitespace
    "trim_whitespace",      # leading/trailing only; never inside a value
    "normalise_dates",      # named columns, from listed input formats, to ISO
    "map_values",           # named column, explicit old -> new mapping only
    "rename_headers",       # explicit mapping only
    "sort_rows",            # by named columns
    "preserve_columns",     # a no-op that exists to be verified: do not touch these
})

#: Requirements that are checked but never *performed*. "No row may be lost" and
#: "the file must still open" are properties of the result, not steps to carry
#: out — there is nothing to do for them, only something to be true. Treating
#: them as unplannable would refuse exactly the requirements that catch the
#: quietest failures.
VERIFY_ONLY = frozenset({"row_reconciliation", "parses_as_csv",
                         "no_unauthorised_changes"})

#: Date formats that mean exactly one day whoever reads them. A year-first date
#: and a date with a month name cannot be read two ways, so they need no
#: declaration from anybody.
UNAMBIGUOUS_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%Y/%m/%d",
                            "%d %B %Y", "%b %d, %Y")

#: Kept for callers that still name it. This list used to contain ``%m/%d/%Y``
#: and not ``%d/%m/%Y``, with a comment warning that guessing between them
#: corrupts dates — which is precisely what including one and not the other
#: does. ``05/10/2026`` became 10 May on no evidence at all. A slashed date is
#: now parsed only against a format the client declared for the job.
DATE_FORMATS = UNAMBIGUOUS_DATE_FORMATS

_WS = re.compile(r"\s+")


def digest_of(path: str | Path) -> str:
    """SHA-256 of a file's bytes. The identity of an artifact is its content."""
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            h.update(block)
    return h.hexdigest()


@dataclass(slots=True)
class Step:
    """One planned action, tied to the requirement that asked for it."""

    requirement_id: str
    operation: str
    params: dict
    outcome: str = ""
    #: An advisory step that cannot be carried out is recorded and skipped.
    #: A mandatory one stops the job, because delivering without it would mean
    #: delivering work the client is owed and did not get.
    mandatory: bool = True


@dataclass(slots=True)
class WorkReport:
    """What the worker did. Evidence for the audit trail, never proof of success.

    Nothing here is trusted by the verifier. The verifier recomputes from the
    files; this exists so a human can read what was attempted.
    """

    steps: list[Step] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    duplicates_removed: int = 0
    exceptions: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.refused

    def summary(self) -> str:
        lines = [f"{self.rows_in} rows in, {self.rows_out} rows out",
                 f"{self.duplicates_removed} duplicate row(s) removed"]
        lines += [f"{s.requirement_id}: {s.outcome}" for s in self.steps]
        if self.exceptions:
            lines.append(f"{len(self.exceptions)} exception(s) recorded")
        return "\n".join(lines)


def plan(requirements: list) -> tuple[list[Step], list[str]]:
    """Turn committed requirements into an ordered plan, or say why not.

    Returns ``(steps, refusals)``. A refusal is not a failure of the plan — it is
    the plan, correctly saying that a requirement cannot be executed as written.
    """
    steps, refusals = [], []
    for req in requirements:
        op = req.check
        if not op:
            if req.criticality.blocks_delivery:
                # Should be unreachable: the Orchestrator refuses to commit a
                # mandatory requirement with no check. Kept as a refusal rather
                # than an assert so a future caller that skips commitment cannot
                # quietly execute a job with an untestable mandatory item.
                refusals.append(
                    f"{req.id}: mandatory but has no verification method")
            # An advisory requirement with no check is recorded and reported,
            # not executed. There is nothing to do for it.
            continue
        if op in VERIFY_ONLY:
            continue
        if op not in OPERATIONS:
            refusals.append(f"{req.id}: {op!r} is not an operation this capability "
                            "performs")
            continue
        problem = _params_problem(op, req.params)
        if problem:
            refusals.append(f"{req.id}: {problem}")
            continue
        steps.append(Step(requirement_id=req.id, operation=op,
                          params=dict(req.params),
                          mandatory=req.criticality.blocks_delivery))
    # Ordering is fixed rather than requirement order: validate before touching
    # anything, rename before anything refers to new names, sort last.
    rank = {"require_columns": 0, "rename_headers": 1, "trim_whitespace": 2,
            "normalise_dates": 3, "map_values": 4, "drop_exact_duplicates": 5,
            "preserve_columns": 6, "sort_rows": 7}
    steps.sort(key=lambda s: rank[s.operation])
    return steps, refusals


def _params_problem(op: str, params: dict) -> str:
    """Is this operation fully specified? Underspecified means ask, not guess."""
    if op in ("require_columns", "preserve_columns"):
        if not params.get("columns"):
            return "needs an explicit column list"
    if op == "normalise_dates":
        if not params.get("columns"):
            return "needs the columns to normalise; guessing which are dates is "\
                   "how a reference number becomes a date"
    if op == "map_values":
        if not params.get("column"):
            return "needs a column"
        mapping = params.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            return "needs an explicit old -> new mapping; inferring categories "\
                   "from the data is a business decision, not a cleanup"
    if op == "rename_headers":
        if not isinstance(params.get("mapping"), dict) or not params["mapping"]:
            return "needs an explicit header mapping"
    if op == "sort_rows" and not params.get("columns"):
        return "needs the columns to sort by"
    return ""


def execute(*, source: str | Path, destination: str | Path, requirements: list,
            report_path: str | Path | None = None) -> WorkReport:
    """Run the plan against ``source`` and write ``destination``.

    ``source`` is opened read-only and is never written to. If any requirement
    cannot be executed as written, nothing is written at all: a partial
    deliverable that silently skipped a requirement is the exact failure this
    whole pipeline exists to prevent.
    """
    steps, refusals = plan(requirements)
    report = WorkReport(steps=steps, refused=refusals)
    if refusals:
        return report

    source, destination = Path(source), Path(destination)
    if not source.exists():
        report.refused.append(f"source artifact not found: {source}")
        return report

    try:
        with open(source, newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        report.refused.append(f"source is not readable as CSV: {exc}")
        return report

    if not rows:
        report.refused.append("source is empty; there is nothing to clean")
        return report

    problem = _not_plausibly_csv(rows)
    if problem:
        # A .xlsx is a zip, and a zip header happens to decode as UTF-8, so
        # "it decoded" is not "it is a CSV". Without this, a binary file was
        # read as one enormous text row, transformed, verified against itself
        # and delivered — every check passed because they compare the source to
        # the output and both were the same garbage. Refusing here is the only
        # place that can tell, because by the time an artifact exists the
        # evidence of what it should have been is gone.
        report.refused.append(f"source is not a CSV file: {problem}")
        return report

    # csv.reader yields [] for a blank line, and a file that ends with a
    # newline is the normal shape. Treating that as a data row made it survive
    # deduplication, appear in the output as a blank line, and then fail the
    # "parses" check as a ragged row — three wasted correction attempts for a
    # file that was never malformed. A zero-field row carries no data, so it is
    # not a record. A row of the right width whose values happen to be empty
    # IS a record and is left alone.
    header = rows[0]
    body = [row for row in rows[1:] if row]
    blank_lines = len(rows) - 1 - len(body)
    if blank_lines:
        report.exceptions.append(
            f"{blank_lines} blank line(s) in the source were not treated as rows")
    report.rows_in = len(body)

    # Duplicate headers make every column reference ambiguous. Refuse rather
    # than silently bind to the first match.
    duplicates = {h for h in header if header.count(h) > 1}
    if duplicates:
        report.refused.append(
            f"duplicate header(s) {sorted(duplicates)}: a column reference would "
            "be ambiguous, so no transformation is safe")
        return report

    for step in steps:
        header, body, outcome, problem = _apply(step, header, body, report)
        if problem:
            if step.mandatory:
                report.refused.append(f"{step.requirement_id}: {problem}")
                return report
            # Advisory: recorded for the client, and the job goes on.
            step.outcome = f"not done ({problem})"
            report.exceptions.append(f"{step.requirement_id}: {problem}")
            continue
        step.outcome = outcome

    report.rows_out = len(body)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(body)

    if report_path is not None:
        Path(report_path).write_text(report.summary() + "\n", encoding="utf-8")
    return report


def _apply(step: Step, header: list[str], body: list[list[str]],
           report: WorkReport) -> tuple[list[str], list[list[str]], str, str]:
    """Apply one step. Returns ``(header, body, outcome, problem)``."""
    op, params = step.operation, step.params
    index = {name: i for i, name in enumerate(header)}

    if op == "require_columns":
        missing = [c for c in params["columns"] if c not in index]
        if missing:
            return header, body, "", (f"required column(s) missing: {missing}")
        return header, body, f"all {len(params['columns'])} required columns present", ""

    if op == "preserve_columns":
        missing = [c for c in params["columns"] if c not in index]
        if missing:
            return header, body, "", f"cannot preserve absent column(s): {missing}"
        return header, body, f"{len(params['columns'])} column(s) left untouched", ""

    if op == "rename_headers":
        unknown = [old for old in params["mapping"] if old not in index]
        if unknown:
            return header, body, "", f"cannot rename absent column(s): {unknown}"
        new_header = [params["mapping"].get(h, h) for h in header]
        if len(set(new_header)) != len(new_header):
            return header, body, "", "renaming would create duplicate headers"
        return new_header, body, f"renamed {len(params['mapping'])} header(s)", ""

    if op == "trim_whitespace":
        columns = params.get("columns") or header
        missing = [c for c in columns if c not in index]
        if missing:
            return header, body, "", f"unknown column(s): {missing}"
        touched = 0
        for row in body:
            for name in columns:
                i = index[name]
                if i < len(row) and row[i] != row[i].strip():
                    row[i] = row[i].strip()
                    touched += 1
        return header, body, f"trimmed {touched} value(s)", ""

    if op == "normalise_dates":
        missing = [c for c in params["columns"] if c not in index]
        if missing:
            return header, body, "", f"unknown date column(s): {missing}"
        converted = 0
        for n, row in enumerate(body, start=2):
            for name in params["columns"]:
                i = index[name]
                if i >= len(row):
                    continue
                raw = row[i].strip()
                if not raw:
                    continue
                iso = _to_iso(raw, params.get("date_format", ""))
                if iso is None:
                    # An unparseable or ambiguous date is an exception, not a
                    # guess and not a deletion. The row survives and the client
                    # is told which it was.
                    if _SLASHED_DATE.match(raw):
                        report.exceptions.append(
                            f"row {n}, column {name!r}: {raw!r} could be read "
                            "either day-first or month-first and the job records "
                            "no declared convention; left unchanged")
                    else:
                        report.exceptions.append(
                            f"row {n}, column {name!r}: {raw!r} matches none of "
                            f"the accepted date formats; left unchanged")
                    continue
                if iso != row[i]:
                    row[i] = iso
                    converted += 1
        return header, body, f"normalised {converted} date(s)", ""

    if op == "map_values":
        name = params["column"]
        if name not in index:
            return header, body, "", f"unknown column {name!r}"
        i, mapping = index[name], params["mapping"]
        case_insensitive = bool(params.get("case_insensitive"))
        lookup = ({k.casefold(): v for k, v in mapping.items()} if case_insensitive
                  else dict(mapping))
        changed, unmapped = 0, set()
        for row in body:
            if i >= len(row):
                continue
            key = row[i].casefold() if case_insensitive else row[i]
            if key in lookup:
                if row[i] != lookup[key]:
                    row[i] = lookup[key]
                    changed += 1
            elif row[i].strip():
                unmapped.add(row[i])
        for value in sorted(unmapped):
            report.exceptions.append(
                f"column {name!r}: {value!r} is not in the supplied mapping; "
                "left unchanged")
        return header, body, f"mapped {changed} value(s)", ""

    if op == "drop_exact_duplicates":
        seen, kept, removed = set(), [], 0
        for row in body:
            key = tuple(_WS.sub(" ", cell.strip()) for cell in row)
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(row)
        report.duplicates_removed = removed
        return header, kept, f"removed {removed} duplicate row(s)", ""

    if op == "sort_rows":
        missing = [c for c in params["columns"] if c not in index]
        if missing:
            return header, body, "", f"unknown sort column(s): {missing}"
        keys = [index[c] for c in params["columns"]]
        body.sort(key=lambda row: tuple(row[k] if k < len(row) else "" for k in keys))
        return header, body, f"sorted by {', '.join(params['columns'])}", ""

    raise FailClosed(f"unreachable: unplanned operation {op!r}")


#: Control characters that never appear in a text CSV. Tab, newline and
#: carriage return are excluded because they legitimately can.
_BINARY = frozenset(chr(c) for c in range(32)) - {"\t", "\n", "\r"}


def _not_plausibly_csv(rows: list[list[str]]) -> str:
    """Does this look like a CSV at all? Returns a reason, or "" if it does.

    Deliberately conservative: it rejects things no text CSV contains rather
    than trying to recognise every valid one. A file that passes here may still
    be wrong; a file that fails here is certainly not a spreadsheet export.
    """
    header = rows[0]
    if not header or not any(cell.strip() for cell in header):
        return "the first row has no column names"
    for row in rows[:20]:
        for cell in row:
            found = _BINARY.intersection(cell)
            if found:
                names = ", ".join(f"0x{ord(c):02x}" for c in sorted(found))
                return (f"it contains binary control bytes ({names}); a real "
                        "spreadsheet file is not a CSV and must be exported first")
    if len(header) == 1 and len(rows) > 1 and all(len(r) == 1 for r in rows[:20]):
        # One column everywhere is legal but is also what a binary blob or a
        # wrong delimiter looks like. Say so rather than proceed silently.
        return ("every row has exactly one field; if this is tab- or "
                "semicolon-separated it must be converted to comma-separated first")
    return ""


_SLASHED_DATE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\s*$")


def _to_iso(raw: str, declared: str = "") -> str | None:
    """Parse a date, or return None. Never guesses between ambiguous formats.

    ``declared`` is the convention the client confirmed for this job, and it is
    the only thing that makes ``05/10/2026`` mean a particular day. Without it,
    a slashed date whose first two components are both twelve or under is
    refused rather than resolved — the client is asked instead. A slashed date
    where one component exceeds twelve resolves itself and needs no declaration.
    """
    for fmt in UNAMBIGUOUS_DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return date(parsed.year, parsed.month, parsed.day).isoformat()

    match = _SLASHED_DATE.match(raw)
    if match is None:
        return None
    first, second, year = (int(g) for g in match.groups())
    if declared == "MM/DD/YYYY":
        order = [(first, second)]
    elif declared == "DD/MM/YYYY":
        order = [(second, first)]
    elif first > 12 and second <= 12:
        order = [(second, first)]          # only one reading is a real date
    elif second > 12 and first <= 12:
        order = [(first, second)]
    else:
        return None                         # genuinely two days; do not choose
    for month, day in order:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


#: Which columns each operation is allowed to alter. Used to derive the
#: "nothing else changed" requirement, so the protected set is computed from the
#: plan rather than from anyone's recollection of it.
def authorised_columns(requirements: list, header: list[str]) -> dict:
    """``{column: mode}`` — how each column may change, not merely whether.

    The distinction matters. Trimming a column authorises whitespace changes and
    nothing else; found by the false-completion battery, where a blanket
    authorisation let an output that trimmed *and* uppercased a column pass. A
    column whose only permission is "trim" is still protected against every other
    edit, which is the protection the client actually agreed to.

    Modes: ``"full"`` (the transformation itself is separately verified) and
    ``"whitespace"`` (only padding may differ).
    """
    allowed: dict[str, str] = {}
    for req in requirements:
        op, params = req.check, req.params
        if op == "normalise_dates":
            for column in params.get("columns") or []:
                allowed[column] = "full"
        elif op == "map_values" and params.get("column"):
            allowed[params["column"]] = "full"
        elif op == "rename_headers":
            mapping = params.get("mapping") or {}
            for column in set(mapping) | set(mapping.values()):
                allowed[column] = "full"
        elif op == "trim_whitespace":
            # No column list means every column, which is a broad thing to
            # authorise — but it is still only whitespace.
            for column in params.get("columns") or header:
                allowed.setdefault(column, "whitespace")
    return allowed
