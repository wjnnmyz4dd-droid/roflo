"""Deterministic checks: does this artifact satisfy this requirement?

**Independence here is computational, not a naming convention.** Every check
takes two file paths and a requirement, re-reads both files from disk, and
recomputes the answer. It never sees the worker's ``WorkReport``, never imports
:mod:`csvwork`, and has no way to learn what the worker believes it did. If the
worker removed nine duplicates and thinks it removed ten, the check counts the
duplicates in the output file and is unmoved by either number.

That is the property that matters. Two string identities in one process are not
isolation, and calling the same model twice is not review. Recomputing the
answer from the artifact is the real thing, and it is available here because
every operation this capability performs is deterministic.

A check returns :class:`CheckOutcome`. There are four results and only one of
them is PASS; a check that cannot run says so rather than shrugging and passing.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .types import CheckResult

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """One requirement, one artifact, one computed answer."""

    result: CheckResult
    detail: str
    computed: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.result.satisfies


def _read(path: str | Path) -> tuple[list[str], list[list[str]], str]:
    """``(header, body, problem)``. A file that will not parse is not a PASS."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
    except FileNotFoundError:
        return [], [], f"artifact not found: {path}"
    except (UnicodeDecodeError, csv.Error) as exc:
        return [], [], f"artifact does not parse as CSV: {exc}"
    if not rows:
        return [], [], "artifact is empty"
    # A blank line is not a row. Stated here independently rather than imported
    # from the worker: the checks must never depend on the worker's code, and
    # this rule is one line either way.
    return rows[0], [row for row in rows[1:] if row], ""


def _key(row: list[str]) -> tuple:
    return tuple(_WS.sub(" ", cell.strip()) for cell in row)


# --------------------------------------------------------------------- checks

def _accounted_columns(src_header, src_distinct, out_header, out_body) -> list[str]:
    """Columns whose output values are exactly the source's, counted as a multiset.

    This is how a check with no knowledge of the agreed transformations can
    still tell "this file came from that source" from "this file came from
    somewhere else". A cleanup leaves some columns untouched — an identifier, a
    quantity, an amount — and for those the output's values are precisely the
    source's distinct-row values, down to the counts. A different job's file,
    however well-formed, matches on none of them.

    Counted as a multiset rather than a set on purpose: two unrelated files both
    using the four compass regions share that column's *set* of values, and a
    set comparison would call them the same data.
    """
    from collections import Counter

    accounted = []
    for name in out_header:
        if name not in src_header:
            continue
        si, oi = src_header.index(name), out_header.index(name)
        src_values = Counter(r[si] for r in src_distinct if si < len(r))
        out_values = Counter(r[oi] for r in out_body if oi < len(r))
        if src_values and src_values == out_values:
            accounted.append(name)
    return accounted


def _distinct_rows(body: list) -> list:
    seen, out = set(), []
    for row in body:
        key = _key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def check_require_columns(source, output, params) -> CheckOutcome:
    header, _, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    missing = [c for c in params.get("columns", []) if c not in header]
    if missing:
        return CheckOutcome(CheckResult.FAIL, f"missing column(s): {missing}",
                            {"missing": missing, "header": header})
    return CheckOutcome(CheckResult.PASS, f"all {len(params['columns'])} present",
                        {"header": header})


def check_drop_exact_duplicates(source, output, params) -> CheckOutcome:
    """Two things must hold: none left, and none invented."""
    _, src_body, p1 = _read(source)
    _, out_body, p2 = _read(output)
    if p1 or p2:
        return CheckOutcome(CheckResult.UNVERIFIABLE, p1 or p2)

    seen, repeats = set(), 0
    for row in out_body:
        k = _key(row)
        if k in seen:
            repeats += 1
        seen.add(k)
    src_unique = {_key(r) for r in src_body}
    computed = {"source_rows": len(src_body), "output_rows": len(out_body),
                "source_unique": len(src_unique), "duplicates_remaining": repeats}
    if repeats:
        return CheckOutcome(CheckResult.FAIL,
                            f"{repeats} duplicate row(s) remain", computed)
    if len(out_body) != len(src_unique):
        # Removing duplicates must remove *only* duplicates. Fewer rows than
        # distinct source rows means something else was dropped.
        return CheckOutcome(
            CheckResult.FAIL,
            f"{len(out_body)} rows out but {len(src_unique)} distinct rows in: "
            "deduplication removed more than duplicates", computed)

    # Counting alone let another job's file through whenever the two happened to
    # have the same number of distinct rows. A count is not an identity.
    accounted = _accounted_columns(_read(source)[0], _distinct_rows(src_body),
                                   _read(output)[0], out_body)
    computed["accounted_columns"] = accounted
    if not accounted:
        return CheckOutcome(
            CheckResult.FAIL,
            "the row counts line up but no column's values match the source's; "
            "this deliverable does not appear to be made from this source",
            computed)
    return CheckOutcome(CheckResult.PASS,
                        f"no duplicates remain; {len(src_unique)} distinct rows "
                        f"kept, matching the source on {len(accounted)} column(s)",
                        computed)


#: How a supplied date might be written. Each pattern yields the days it could
#: mean — several, when the notation is genuinely ambiguous.
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _readings(value: str, declared: str = "") -> set[str]:
    """Every day a supplied date could denote, as ISO strings.

    ``05/10/2026`` is genuinely two days: 10 May and 5 October. Both are
    returned, because the check must not invent a convention the client never
    stated. ``2026-05-10`` returns exactly one, which is what makes a
    transposed output detectable.

    ``declared`` is the convention the client confirmed for this job, and it
    narrows the reading to one. Computed here rather than imported from the
    worker — these checks never depend on the code they are checking — but from
    the *same* declaration, which is what stops the two of them resolving the
    same date differently. Where they must agree is on what the client said, not
    on how to work it out.
    """
    value = value.strip()
    if not value:
        return set()
    out: set[str] = set()

    if _ISO.match(value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            out.add(value)
        except ValueError:
            pass
        return out

    parts = re.split(r"[/\-.]", value)
    if len(parts) == 3:
        a, b, c = (p.strip() for p in parts)
        if c.isdigit() and len(c) == 4 and a.isdigit() and b.isdigit():
            orders = [(b, a), (a, b)]                # MM/DD and DD/MM
            if declared == "MM/DD/YYYY":
                orders = [(b, a)]
            elif declared == "DD/MM/YYYY":
                orders = [(a, b)]
            for day, month in orders:
                try:
                    out.add(datetime(int(c), int(month), int(day))
                            .strftime("%Y-%m-%d"))
                except ValueError:
                    pass
        if c.isdigit() and len(c) == 4 and a.isdigit() and b[:3].lower() in _MONTHS:
            try:
                out.add(datetime(int(c), _MONTHS[b[:3].lower()], int(a))
                        .strftime("%Y-%m-%d"))
            except ValueError:
                pass
        if a.isdigit() and len(a) == 4 and b.isdigit() and c.isdigit():
            try:
                out.add(datetime(int(a), int(b), int(c)).strftime("%Y-%m-%d"))
            except ValueError:
                pass
    return out


def check_normalise_dates(source, output, params) -> CheckOutcome:
    """The dates are ISO **and** they are still the days the client supplied.

    This check used to verify the format alone. Generated certification caught
    it accepting ``2026-05-10`` rewritten as ``2026-10-05`` — a perfectly
    well-formed ISO date, a different day, and a client's order dated five
    months wrong. "Is it shaped like a date" and "is it the right date" are
    different questions, and only the second one protects anybody.

    The day check is deliberately permissive where the source is ambiguous: a
    value like ``05/10/2026`` could be either day, so either reading is
    accepted. A value that matches *no* reading of any supplied date is
    refused. That direction is the safe one — it never rejects correct work, and
    it catches every transposition of a date the client wrote unambiguously.
    """
    header, body, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    columns = params.get("columns") or []
    if not columns:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION,
                            "no columns named, so there is nothing to check")

    declared = params.get("date_format", "")
    source_header, source_body, source_problem = _read(source)
    bad, unsupported = [], []
    for name in columns:
        if name not in header:
            return CheckOutcome(CheckResult.FAIL, f"column {name!r} is absent")
        i = header.index(name)

        supplied: set[str] = set()
        if not source_problem and name in source_header:
            si = source_header.index(name)
            for row in source_body:
                if si < len(row):
                    supplied |= _readings(row[si], declared)

        for n, row in enumerate(body, start=2):
            if i >= len(row):
                continue
            value = row[i].strip()
            if not value:
                continue
            if not _ISO.match(value):
                bad.append(f"row {n}: {value!r}")
                continue
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                bad.append(f"row {n}: {value!r} is not a real date")
                continue
            if supplied and value not in supplied:
                unsupported.append(f"row {n}: {value!r} is not any day the "
                                   "source supplied")

    computed = {"columns": columns, "non_iso": len(bad),
                "unsupported": len(unsupported), "date_format": declared,
                "examples": (bad + unsupported)[:5]}
    if bad:
        return CheckOutcome(CheckResult.FAIL,
                            f"{len(bad)} value(s) are not YYYY-MM-DD", computed)
    if unsupported:
        return CheckOutcome(
            CheckResult.FAIL,
            f"{len(unsupported)} date(s) are well-formed but denote a day the "
            "source never supplied", computed)
    return CheckOutcome(CheckResult.PASS,
                        "every dated value is ISO and denotes a supplied day",
                        computed)


def check_preserve_columns(source, output, params) -> CheckOutcome:
    """Protected values must survive byte-for-byte, as a multiset.

    Compared as a multiset rather than row by row because deduplication and
    sorting legitimately move rows about. What may never change is the collection
    of values itself — so an amount that was altered, dropped or added shows up
    even if the rows were reordered.
    """
    src_header, src_body, p1 = _read(source)
    out_header, out_body, p2 = _read(output)
    if p1 or p2:
        return CheckOutcome(CheckResult.UNVERIFIABLE, p1 or p2)

    columns = params.get("columns") or []
    if not columns:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "no columns named")

    # A rename in the same job would make this ambiguous; say so rather than
    # comparing the wrong pair of columns.
    renamed = params.get("renamed_from") or {}
    problems, computed = [], {}
    for name in columns:
        src_name = renamed.get(name, name)
        if src_name not in src_header:
            return CheckOutcome(CheckResult.UNVERIFIABLE,
                                f"column {src_name!r} is not in the source")
        if name not in out_header:
            problems.append(f"{name!r} is missing from the output")
            continue
        si, oi = src_header.index(src_name), out_header.index(name)
        src_values = sorted(r[si] for r in src_body if si < len(r))
        out_values = sorted(r[oi] for r in out_body if oi < len(r))

        # Deduplication removes whole rows, so the protected values of removed
        # duplicate rows go with them. Compare against the source's distinct rows.
        seen, distinct = set(), []
        for r in src_body:
            k = _key(r)
            if k in seen:
                continue
            seen.add(k)
            distinct.append(r)
        expected = sorted(r[si] for r in distinct if si < len(r))
        computed[name] = {"source": len(src_values), "expected": len(expected),
                          "output": len(out_values)}
        if out_values != expected:
            lost = _difference(expected, out_values)
            added = _difference(out_values, expected)
            problems.append(
                f"{name!r} changed: {len(lost)} value(s) lost or altered"
                + (f" (e.g. {lost[:3]})" if lost else "")
                + (f", {len(added)} appeared (e.g. {added[:3]})" if added else ""))
    if problems:
        return CheckOutcome(CheckResult.FAIL, "; ".join(problems), computed)
    return CheckOutcome(CheckResult.PASS,
                        f"{len(columns)} protected column(s) unchanged", computed)


def check_map_values(source, output, params) -> CheckOutcome:
    header, body, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    name, mapping = params.get("column"), params.get("mapping") or {}
    if not name or not mapping:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION,
                            "a mapping check needs a column and an explicit mapping")
    if name not in header:
        return CheckOutcome(CheckResult.FAIL, f"column {name!r} is absent")
    i = header.index(name)
    allowed = set(mapping.values())
    stale = set(mapping) - allowed
    offenders = sorted({row[i] for row in body
                        if i < len(row) and row[i] in stale})
    computed = {"column": name, "allowed": sorted(allowed),
                "unmapped_remaining": offenders}
    if offenders:
        return CheckOutcome(CheckResult.FAIL,
                            f"unmapped value(s) remain: {offenders}", computed)
    return CheckOutcome(CheckResult.PASS, "every mapped value was applied", computed)


def check_rename_headers(source, output, params) -> CheckOutcome:
    header, _, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    mapping = params.get("mapping") or {}
    missing = [new for new in mapping.values() if new not in header]
    left = [old for old in mapping if old in header]
    if missing or left:
        return CheckOutcome(CheckResult.FAIL,
                            f"expected header(s) absent: {missing}; old header(s) "
                            f"still present: {left}", {"header": header})
    return CheckOutcome(CheckResult.PASS, f"{len(mapping)} header(s) renamed",
                        {"header": header})


def check_trim_whitespace(source, output, params) -> CheckOutcome:
    header, body, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    columns = params.get("columns") or header
    untrimmed = 0
    for row in body:
        for name in columns:
            if name not in header:
                return CheckOutcome(CheckResult.FAIL, f"column {name!r} is absent")
            i = header.index(name)
            if i < len(row) and row[i] != row[i].strip():
                untrimmed += 1
    if untrimmed:
        return CheckOutcome(CheckResult.FAIL,
                            f"{untrimmed} value(s) still carry padding",
                            {"untrimmed": untrimmed})
    return CheckOutcome(CheckResult.PASS, "no leading or trailing whitespace",
                        {"untrimmed": 0})


def check_sort_rows(source, output, params) -> CheckOutcome:
    header, body, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.UNVERIFIABLE, problem)
    columns = params.get("columns") or []
    if not columns:
        return CheckOutcome(CheckResult.NEEDS_INFORMATION, "no sort columns named")
    missing = [c for c in columns if c not in header]
    if missing:
        return CheckOutcome(CheckResult.FAIL, f"unknown sort column(s): {missing}")
    keys = [header.index(c) for c in columns]
    extracted = [tuple(r[k] if k < len(r) else "" for k in keys) for r in body]
    if extracted != sorted(extracted):
        return CheckOutcome(CheckResult.FAIL, f"rows are not ordered by {columns}")
    return CheckOutcome(CheckResult.PASS, f"ordered by {', '.join(columns)}")


def check_row_reconciliation(source, output, params) -> CheckOutcome:
    """No row may vanish except through an authorised removal.

    This is the check that catches the quiet failure: a row that was neither a
    duplicate nor anything the client asked to remove, simply gone.
    """
    src_header, src_body, p1 = _read(source)
    out_header, out_body, p2 = _read(output)
    if p1 or p2:
        return CheckOutcome(CheckResult.UNVERIFIABLE, p1 or p2)
    distinct_rows = _distinct_rows(src_body)
    distinct = len(distinct_rows)
    expected = distinct if params.get("duplicates_removed", True) else len(src_body)
    computed = {"source_rows": len(src_body), "distinct_source_rows": distinct,
                "output_rows": len(out_body), "expected_rows": expected}
    if len(out_body) != expected:
        return CheckOutcome(
            CheckResult.FAIL,
            f"expected {expected} rows, found {len(out_body)}", computed)

    # "The right number of rows" said nothing about *whose* rows. Another job's
    # deliverable reconciled perfectly whenever the counts happened to agree.
    accounted = _accounted_columns(src_header, distinct_rows, out_header, out_body)
    computed["accounted_columns"] = accounted
    if not accounted:
        return CheckOutcome(
            CheckResult.FAIL,
            f"{len(out_body)} rows is the right number, but no column's values "
            "match the source's; these are not this source's rows", computed)
    return CheckOutcome(CheckResult.PASS,
                        f"{expected} rows reconcile, matching the source on "
                        f"{len(accounted)} column(s)", computed)


def check_no_unauthorised_changes(source, output, params) -> CheckOutcome:
    """Nothing changed that the plan did not authorise.

    Found by the false-completion battery: every earlier check asks whether the
    *requested* work was done, and none asks whether anything *else* was. So an
    output that did the job correctly and also helpfully uppercased a column
    nobody mentioned passed every check and shipped.

    A cleanup the client did not ask for is a defect even when it looks like an
    improvement, because they cannot review what they did not request. This
    protects every column outside the authorised set — including the ones the
    client never thought to mention, which are exactly the ones nobody would
    have written a requirement for.
    """
    src_header, src_body, p1 = _read(source)
    out_header, out_body, p2 = _read(output)
    if p1 or p2:
        return CheckOutcome(CheckResult.UNVERIFIABLE, p1 or p2)

    # {column: mode}. A column authorised only for whitespace is still protected
    # against every other kind of edit.
    authorised = dict(params.get("authorised_columns") or {})
    protected = [c for c in src_header
                 if c in out_header and authorised.get(c) != "full"]
    if not protected:
        return CheckOutcome(CheckResult.PASS,
                            "every column was authorised for change",
                            {"authorised": authorised})

    seen, distinct = set(), []
    for row in src_body:
        k = _key(row)
        if k in seen:
            continue
        seen.add(k)
        distinct.append(row)

    changed, computed = [], {"protected": protected, "authorised": authorised}
    for name in protected:
        si, oi = src_header.index(name), out_header.index(name)
        # A whitespace-authorised column is compared with padding removed, so a
        # trim passes and anything else does not.
        norm = ((lambda v: v.strip()) if authorised.get(name) == "whitespace"
                else (lambda v: v))
        expected = sorted(norm(r[si]) for r in distinct if si < len(r))
        actual = sorted(norm(r[oi]) for r in out_body if oi < len(r))
        if actual != expected:
            lost = _difference(expected, actual)
            computed[name] = {"expected": len(expected), "found": len(actual),
                              "altered_examples": lost[:3]}
            changed.append(f"{name!r} was modified without a requirement "
                           f"authorising it (e.g. {lost[:2]})")
    if changed:
        return CheckOutcome(CheckResult.FAIL, "; ".join(changed), computed)
    return CheckOutcome(CheckResult.PASS,
                        f"{len(protected)} unauthorised-change column(s) intact",
                        computed)


def check_parses_as_csv(source, output, params) -> CheckOutcome:
    """The deliverable must open. A file the client cannot read is not a file.

    "Parses" cannot mean "``csv.reader`` did not raise", because it raises at
    almost nothing: a file of NUL bytes reads back as one column and no rows.
    Verifier certification caught this check accepting exactly that, and this is
    the SYSTEM_SAFETY requirement carried by every cleanup job — the one that is
    supposed to guarantee the client can open what they were sent.

    So two further conditions, both about what a reader actually gets:

    * no binary control bytes, which no spreadsheet will open as text
    * not an empty result from a source that had rows, because a deliverable
      where every row vanished is not a cleaned file
    """
    header, body, problem = _read(output)
    if problem:
        return CheckOutcome(CheckResult.FAIL, problem)

    try:
        raw = Path(output).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return CheckOutcome(CheckResult.FAIL, f"artifact is not readable text: {exc}")
    # Stated here rather than imported from the worker: these checks never
    # depend on the code they are checking.
    control = sorted({c for c in raw
                      if ord(c) < 32 and c not in "\t\n\r"} | {c for c in raw
                                                                 if ord(c) == 127})
    if control:
        return CheckOutcome(
            CheckResult.FAIL,
            f"artifact contains {len(control)} binary control byte(s) "
            f"({', '.join(hex(ord(c)) for c in control[:4])}); it will not open "
            "as a spreadsheet whatever a CSV parser makes of it",
            {"control_bytes": [hex(ord(c)) for c in control]})

    if not header or (len(header) == 1 and not header[0].strip()):
        return CheckOutcome(CheckResult.FAIL,
                            "artifact has no column names", {"columns": 0})

    widths = {len(r) for r in body}
    if len(widths) > 1:
        return CheckOutcome(CheckResult.FAIL,
                            f"ragged rows: widths {sorted(widths)}",
                            {"widths": sorted(widths)})

    if not body:
        _, source_body, source_problem = _read(source)
        if not source_problem and source_body:
            return CheckOutcome(
                CheckResult.FAIL,
                f"the deliverable has no data rows, but the source had "
                f"{len(source_body)}; every row is gone",
                {"rows": 0, "source_rows": len(source_body)})

    return CheckOutcome(CheckResult.PASS,
                        f"parses; {len(header)} columns, {len(body)} rows",
                        {"columns": len(header), "rows": len(body)})


#: The registry a requirement's ``check`` name resolves against. A requirement
#: naming something absent is refused at commitment rather than skipped later.
CHECKS = {
    "require_columns": check_require_columns,
    "drop_exact_duplicates": check_drop_exact_duplicates,
    "normalise_dates": check_normalise_dates,
    "preserve_columns": check_preserve_columns,
    "map_values": check_map_values,
    "rename_headers": check_rename_headers,
    "trim_whitespace": check_trim_whitespace,
    "sort_rows": check_sort_rows,
    "row_reconciliation": check_row_reconciliation,
    "no_unauthorised_changes": check_no_unauthorised_changes,
    "parses_as_csv": check_parses_as_csv,
}


def run(check_name: str, *, source, output, params: dict) -> CheckOutcome:
    """Run one registered check. An unknown check is never a pass."""
    fn = CHECKS.get(check_name)
    if fn is None:
        return CheckOutcome(CheckResult.UNVERIFIABLE,
                            f"no registered check named {check_name!r}")
    try:
        return fn(source, output, params)
    except Exception as exc:  # noqa: BLE001 - a crashed check is not a pass
        return CheckOutcome(CheckResult.UNVERIFIABLE,
                            f"{check_name} raised {type(exc).__name__}: {exc}")


def _difference(a: list[str], b: list[str]) -> list[str]:
    """Multiset difference a - b, order preserved."""
    remaining = list(b)
    out = []
    for item in a:
        if item in remaining:
            remaining.remove(item)
        else:
            out.append(item)
    return out
