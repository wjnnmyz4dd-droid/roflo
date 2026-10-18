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

#: The client's file may arrive in whatever their spreadsheet exported. The
#: deliverable may not: Solvent writes UTF-8 with commas, and :func:`_read`
#: above stays strict about that on purpose, because a worker that emitted
#: cp1252 would be a defect rather than an input.
#:
#: Written here rather than imported from :mod:`solvent.tabular`, which is
#: what the worker uses, for the reason the whole module exists: if the check
#: shared the worker's decoder, a file both misread the same way would
#: reconcile perfectly against itself and every check would pass. The cost of
#: stating it twice is that an ambiguous file can be read two ways and the
#: checks then refuse the job. That is the safe direction to fail.
_SOURCE_LADDER = ("utf-8-sig", "utf-16", "cp1252")
_SOURCE_DELIMITERS = ",;\t|"
_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _decode_source(raw: bytes) -> tuple[str, str]:
    """``(text, problem)`` for a client file of unknown encoding.

    UTF-16 is tried only behind a byte-order mark. Without one it is a trap:
    ordinary Western text in a single-byte encoding decodes under UTF-16
    without raising, into CJK, and an encoding that never refuses would sit at
    the top of any ladder and swallow everything below it.
    """
    if not raw:
        return "", "artifact is empty"
    for encoding in _SOURCE_LADDER:
        if encoding == "utf-16" and not raw.startswith(_BOMS):
            continue
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
        # A replacement character means this codec already lost a byte, and a
        # NUL means the file is not text at all. Either way the next rung is
        # a better answer than a damaged string.
        if "�" in text or "\x00" in text:
            continue
        return text, ""
    return "", ("artifact is not readable as text under any supported "
                "encoding (tried " + ", ".join(_SOURCE_LADDER) + ")")


def _source_delimiter(text: str) -> str:
    """Which separator holds this file together?

    The one that gives every line the same number of fields, and the most
    fields, wins. A file genuinely separated by semicolons has one comma
    column; the reverse never happens by accident.
    """
    lines = [line for line in text.splitlines()[:50] if line.strip()]
    if not lines:
        return ","
    best, best_score = ",", (0, 0)
    for candidate in _SOURCE_DELIMITERS:
        try:
            rows = list(csv.reader(lines, delimiter=candidate))
        except csv.Error:
            continue
        widths = {len(row) for row in rows if row}
        if len(widths) != 1:
            continue
        width = widths.pop()
        if width < 2:
            continue
        score = (width, -_SOURCE_DELIMITERS.index(candidate))
        if score > best_score:
            best, best_score = candidate, score
    return best


def _read_source(path: str | Path) -> tuple[list[str], list[list[str]], str]:
    """``(header, body, problem)`` for the client's file, decoded here."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return [], [], f"artifact not found: {path}"
    except OSError as exc:
        return [], [], f"artifact could not be read: {exc}"
    text, problem = _decode_source(raw)
    if problem:
        return [], [], problem
    try:
        rows = list(csv.reader(text.splitlines(True),
                               delimiter=_source_delimiter(text)))
    except csv.Error as exc:
        return [], [], f"artifact does not parse as CSV: {exc}"
    if not rows:
        return [], [], "artifact is empty"
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


def _row_key_column(src_header, src_distinct, out_header, out_body,
                    exclude=()) -> str:
    """A column that names the same row in both files, or ``""``.

    A check that compares one column's values as a *set* cannot tell a value
    moved onto the wrong row from a value that was always there. Certification
    caught exactly that: a date transposed on the row the client wrote
    unambiguously landed on a day that a *different* row's ambiguous date could
    also have meant, so the set of days was unchanged and the check passed. The
    deliverable was still wrong — that client's order was dated two months out.

    Sorting makes this more than a curiosity. A worker that reorders one column
    independently of the rest produces precisely this: every value still
    present, every value on the wrong row.

    A column qualifies only when its values are unique on both sides and the
    two sets are equal, which forces the pairing: nothing else could have
    produced those values. When no column qualifies the caller falls back to
    the weaker comparison rather than guessing at a pairing.
    """
    for name in out_header:
        if name in exclude or name not in src_header:
            continue
        si, oi = src_header.index(name), out_header.index(name)
        src_values = [r[si] for r in src_distinct if si < len(r)]
        out_values = [r[oi] for r in out_body if oi < len(r)]
        if not src_values or len(src_values) != len(src_distinct):
            continue
        if len(out_values) != len(out_body):
            continue
        if len(set(src_values)) != len(src_values):
            continue
        if len(set(out_values)) != len(out_values):
            continue
        if set(src_values) != set(out_values):
            continue
        return name
    return ""


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
    _, src_body, p1 = _read_source(source)
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
    accounted = _accounted_columns(_read_source(source)[0], _distinct_rows(src_body),
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
    source_header, source_body, source_problem = _read_source(source)

    # Prefer comparing each date with the day *its own row* supplied. Falling
    # back to the set of all supplied days is weaker on purpose rather than by
    # oversight: with no column that identifies a row, no pairing is knowable,
    # and inventing one would reject correct work.
    key = ""
    by_key: dict[str, list[str]] = {}
    if not source_problem and source_body:
        key = _row_key_column(source_header, _distinct_rows(source_body),
                              header, body, exclude=set(columns))
    if key:
        ks, ko = source_header.index(key), header.index(key)
        by_key = {row[ks]: row for row in source_body if ks < len(row)}

    bad, unsupported = [], []
    for name in columns:
        if name not in header:
            return CheckOutcome(CheckResult.FAIL, f"column {name!r} is absent")
        i = header.index(name)

        si = source_header.index(name) if (
            not source_problem and name in source_header) else -1
        supplied: set[str] = set()
        if si >= 0:
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
            source_row = by_key.get(row[ko]) if key and ko < len(row) else None
            if source_row is not None and si >= 0 and si < len(source_row):
                allowed = _readings(source_row[si], declared)
                if allowed and value not in allowed:
                    unsupported.append(
                        f"row {n}: {value!r} is not a day the source gave for "
                        f"{key} {row[ko]!r}")
                continue
            if supplied and value not in supplied:
                unsupported.append(f"row {n}: {value!r} is not any day the "
                                   "source supplied")

    computed = {"columns": columns, "non_iso": len(bad),
                "unsupported": len(unsupported), "date_format": declared,
                "row_key": key, "examples": (bad + unsupported)[:5]}
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


#: Transformations this check can model: an authorised change whose effect on
#: a protected value it can reproduce exactly, so that "preserved" can still
#: mean something after the work was done. Deliberately one entry long.
#: ``trim_whitespace`` removes leading and trailing padding and nothing else,
#: which is what the worker's own operation list promises.
_NORMALISERS = {"trim_whitespace": str.strip}

#: Authorised operations that *rewrite* a value rather than normalise it. A
#: column that is both protected and rewritten is a contradiction in the brief,
#: not a defect in the deliverable, and this check says so instead of reporting
#: the rewrite as a loss.
_REWRITERS = ("normalise_dates", "map_values", "compute_column", "number_format")


def _brief(params: dict) -> list:
    """The committed requirements travelling with this check, or ``[]``."""
    return list((params or {}).get("requirements") or [])


def _columns_of(requirement) -> list[str]:
    said = dict(getattr(requirement, "params", {}) or {})
    named = said.get("columns") or said.get("column") or []
    if isinstance(named, str):
        named = [named]
    return [str(c) for c in named]


def _authorised_for(column: str, brief: list) -> tuple[list, str]:
    """``(normalisers, conflict)`` for one protected column.

    A trim requirement that names no columns covers every column, which is how
    the worker reads it too. Authorisation is per column: trimming ``Email``
    does not license trimming ``Name``.
    """
    from .types import check_of

    normalisers, conflict = [], ""
    for requirement in brief:
        check = check_of(requirement)
        named = _columns_of(requirement)
        if check in _NORMALISERS and (not named or column in named):
            normalisers.append(_NORMALISERS[check])
        if check in _REWRITERS and column in named:
            conflict = (f"the brief both protects {column!r} and asks "
                        f"{check} to rewrite it; those cannot both be "
                        "checked, so the client has to say which they meant")
    return normalisers, conflict


def _apply(values: list[str], normalisers: list) -> list[str]:
    out = []
    for value in values:
        for normalise in normalisers:
            value = normalise(value)
        out.append(value)
    return sorted(out)


def check_preserve_columns(source, output, params) -> CheckOutcome:
    """Protected values must survive, as a multiset, allowing for the
    normalisation the same brief authorised.

    Compared as a multiset rather than row by row because deduplication and
    sorting legitimately move rows about. What may never change is the
    collection of values itself — so an amount that was altered, dropped or
    added shows up even if the rows were reordered.

    **Byte-for-byte was too strong.** The worker trims before it dedupes, by a
    fixed operation order, so a brief that says "trim Name" and "preserve
    Name" leaves the protected values legitimately changed by the time the
    duplicates go. Comparing them against the values as they *arrived* failed
    correct work. Comparing them against the values as the *authorised*
    transformations leave them is the question the client actually asked.

    The widening is deliberately narrow, and it costs nothing:

    * values that match exactly still pass without consulting the brief;
    * a difference is re-tested only under the normalisations this brief
      authorised **for that column**, and only ones whose effect can be
      reproduced exactly;
    * a difference that survives that is a genuine loss, addition or
      alteration, and still fails;
    * a padding-only difference with no brief to read is UNVERIFIABLE, which
      blocks delivery exactly as a FAIL does. Not knowing is not a pass.
    """
    src_header, src_body, p1 = _read_source(source)
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
        if out_values == expected:
            continue

        # Only now is the brief worth reading: an exact match needed no
        # permission, and a difference the normalisations cannot explain will
        # fail whatever the brief says.
        normalisers, conflict = _authorised_for(name, _brief(params))
        if conflict:
            return CheckOutcome(CheckResult.UNVERIFIABLE, conflict, computed)
        # Two separate questions, kept separate on purpose. "Does the brief
        # let this column change this way?" is what grants a pass, and only
        # the normalisers authorised for *this* column answer it. "Could any
        # normalisation explain the difference?" is a diagnostic, and it only
        # ever decides how to describe a difference that is not authorised.
        if normalisers and _apply(expected, normalisers) == _apply(out_values,
                                                                   normalisers):
            computed[name]["normalised_by"] = sorted(
                check for check, fn in _NORMALISERS.items() if fn in normalisers)
            continue
        explained = (_apply(expected, list(_NORMALISERS.values()))
                     == _apply(out_values, list(_NORMALISERS.values())))
        if explained and not _brief(params):
            return CheckOutcome(
                CheckResult.UNVERIFIABLE,
                f"{name!r} differs from the source only by padding, and this "
                "call carried no committed requirements, so whether trimming "
                "was authorised cannot be established here",
                computed)
        lost = _difference(expected, out_values)
        added = _difference(out_values, expected)
        why = (" (padding was removed from a column the brief did not "
               "authorise trimming)" if explained else "")
        problems.append(
            f"{name!r} changed: {len(lost)} value(s) lost or altered"
            + (f" (e.g. {lost[:3]})" if lost else "")
            + (f", {len(added)} appeared (e.g. {added[:3]})" if added else "")
            + why)
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
    src_header, src_body, p1 = _read_source(source)
    out_header, out_body, p2 = _read(output)
    if p1 or p2:
        return CheckOutcome(CheckResult.UNVERIFIABLE, p1 or p2)
    distinct_rows = _distinct_rows(src_body)
    distinct = len(distinct_rows)
    dedup = params.get("duplicates_removed", True)
    # Compare against the rows the deliverable is supposed to contain: the
    # distinct ones when duplicates were removed, all of them when they were not.
    basis = distinct_rows if dedup else src_body
    expected = distinct if dedup else len(src_body)
    computed = {"source_rows": len(src_body), "distinct_source_rows": distinct,
                "output_rows": len(out_body), "expected_rows": expected}
    if len(out_body) != expected:
        return CheckOutcome(
            CheckResult.FAIL,
            f"expected {expected} rows, found {len(out_body)}", computed)

    # "The right number of rows" said nothing about *whose* rows. Another job's
    # deliverable reconciled perfectly whenever the counts happened to agree.
    accounted = _accounted_columns(src_header, basis, out_header, out_body)
    computed["accounted_columns"] = accounted
    if not accounted:
        return CheckOutcome(
            CheckResult.FAIL,
            f"{len(out_body)} rows is the right number, but no column's values "
            "match the source's; these are not this source's rows", computed)

    # And "every value is present" said nothing about *which row* it is on.
    # Each column above was compared on its own, so a deliverable that reordered
    # one column and left the others where they were matched every one of them:
    # every value still in the file, every value against the wrong record. That
    # is not an exotic failure — it is what sorting a column instead of sorting
    # the rows produces, and it was accepted by every check in the capability
    # until sort_rows was certified. The client would have received Beta LLC's
    # order under Acme Corp's order number.
    #
    # Rows are therefore compared as rows, projected onto the columns that did
    # not change. The columns the job was asked to transform are excluded by
    # construction, so this says nothing about work that was requested.
    if len(accounted) >= 2:
        from collections import Counter

        si = [src_header.index(c) for c in accounted]
        oi = [out_header.index(c) for c in accounted]

        def project(row, indices):
            return tuple(row[i] if i < len(row) else "" for i in indices)

        want = Counter(project(r, si) for r in basis)
        got = Counter(project(r, oi) for r in out_body)
        if want != got:
            invented = sorted(got - want)
            computed["unpaired_rows"] = len(invented)
            computed["unpaired_examples"] = [list(r) for r in invented[:3]]
            return CheckOutcome(
                CheckResult.FAIL,
                f"every value is accounted for but {len(invented)} row(s) are "
                f"not: {accounted[:4]} are combined in ways no source row "
                f"combines them (e.g. {[list(r) for r in invented[:2]]})",
                computed)

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
    src_header, src_body, p1 = _read_source(source)
    out_header, out_body, p2 = _read(output)
    if p1 or p2:
        return CheckOutcome(CheckResult.UNVERIFIABLE, p1 or p2)

    # {column: mode}. A column authorised only for whitespace — or only for a
    # change of name — is still protected against every other kind of edit.
    authorised = dict(params.get("authorised_columns") or {})
    # {old: new} for the renames the plan authorised, so a renamed column is
    # still an anchor here rather than a blind spot. Every comparison below is
    # by name, and a renamed column shares no name between the two headers —
    # which let an output swap the renamed column with the one beside it, moving
    # one of the client's columns, and pass. Found by certifying rename_headers
    # against the generated battery: nine of this check's own defect instances
    # were accepted once a rename was in the plan.
    renamed = dict(params.get("renamed_columns") or {})
    expected_header = [renamed.get(c, c) for c in src_header]

    # A column the client handed over and did not get back. Renaming a column
    # the plan did not authorise looks exactly like this, which is right: under
    # its agreed name, the column is gone.
    gone = [c for c, e in zip(src_header, expected_header) if e not in out_header]
    if gone:
        return CheckOutcome(
            CheckResult.FAIL,
            f"column(s) the client supplied are not in the deliverable and no "
            f"requirement authorised removing or renaming them: {gone[:4]}",
            {"missing_columns": gone, "renamed": renamed,
             "output_columns": out_header})

    # Column order is part of what the client handed over. Every other test here
    # compares columns *by name*, so a file whose columns had been shuffled
    # matched perfectly — found by inventing a defect class after the battery
    # was written. Rearranging somebody's spreadsheet is a small thing to do
    # without being asked, and "we did not ask for that" is the whole subject of
    # this check.
    if not params.get("column_order_may_change"):
        shared_src = [c for c in expected_header if c in out_header]
        shared_out = [c for c in out_header if c in expected_header]
        if shared_src != shared_out:
            return CheckOutcome(
                CheckResult.FAIL,
                f"the columns were reordered without a requirement authorising "
                f"it: {shared_src[:4]} became {shared_out[:4]}",
                {"source_order": shared_src, "output_order": shared_out})

    protected = [c for c in src_header
                 if renamed.get(c, c) in out_header and authorised.get(c) != "full"]
    if not protected:
        return CheckOutcome(CheckResult.PASS,
                            "every column was authorised for change",
                            {"authorised": authorised})

    # Which source rows the deliverable is supposed to contain. Comparing
    # against the distinct rows unconditionally was a false *rejection*: a job
    # nobody asked to deduplicate keeps its duplicates, and every protected
    # column then had more values in the output than in the comparison — so
    # correct work was reported as an unauthorised change, with a detail line
    # naming no altered value because none had been altered.
    if params.get("duplicates_removed", True):
        seen, distinct = set(), []
        for row in src_body:
            k = _key(row)
            if k in seen:
                continue
            seen.add(k)
            distinct.append(row)
    else:
        distinct = list(src_body)

    changed, computed = [], {"protected": protected, "authorised": authorised}
    for name in protected:
        si = src_header.index(name)
        oi = out_header.index(renamed.get(name, name))
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
        _, source_body, source_problem = _read_source(source)
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
#: Printable ASCII, plus the three whitespace bytes a CSV actually uses.
_ASCII_PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}

#: Above this share of printable-ASCII bytes, a source is Latin-script
#: business data and its deliverable has to stay Latin-script too. Below
#: it the rule has no standing and says nothing.
_SOURCE_IS_ASCII_ENOUGH = 0.80

#: Honest work on such a source never falls below this. Whole-file
#: mojibake lands near zero, so the gap between the two is wide enough
#: that renaming, mapping, trimming and date normalisation cannot cross
#: it.
_OUTPUT_MUST_STAY_ASCII = 0.50

_SNIFF_BYTES = 64 * 1024


def _is_utf16_family(raw: bytes) -> bool:
    """Is this source one of the encodings where ASCII is not itself?

    Nearly every encoding a client exports maps bytes 0x20-0x7E to the
    same characters: UTF-8, cp1252, every ISO-8859, KOI8, mac-roman. The
    UTF-16 and UTF-32 families do not. Which family a file belongs to is
    a fact about its bytes rather than a judgement call: either it opens
    with a byte-order mark, or its ASCII is interleaved with the NUL
    bytes those encodings pad with. Nothing here is a choice, which is
    the point - a check that may choose its own encoding can always
    choose the one that excuses the output.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    sample = raw[:_SNIFF_BYTES]
    if not sample:
        return False
    return sample.count(0) / len(sample) > 0.20


def _printable_share(values) -> float:
    """What fraction of these code points are printable ASCII?"""
    total = len(values)
    if not total:
        return 0.0
    return sum(1 for v in values if v in _ASCII_PRINTABLE) / total


def check_no_character_substitution(source, output, params) -> CheckOutcome:
    """Did reading the source under the wrong encoding mangle its text?

    The worker now detects an encoding instead of assuming UTF-8, which
    widens what can be accepted and introduces exactly one new way to be
    wrong: choosing the wrong encoding and delivering mojibake that every
    other check happily passes, because every other check compares the
    output against the same misread source.

    An earlier version of this check asked whether every delivered
    character appears in the source under *some* decoding this check
    performs itself. That question cannot fail. Mojibake is by
    construction a valid decoding of the source under the wrong codec, so
    a check free to try several codecs simply rediscovers the worker's
    mistake and certifies it. It passed cp1252 read as UTF-16 - the exact
    corruption it was written to catch - and the reason it passed is that
    its own candidate list contained UTF-16.

    What is tested here instead are two things the check cannot talk
    itself out of:

    1. The delivered artifact contains no replacement character. One
       means a byte was lost on the way in or out, and the lost byte was
       somebody's customer name.
    2. A source that is overwhelmingly printable ASCII must yield a
       deliverable that is still substantially printable ASCII. Reading
       single-byte text as UTF-16 pairs the bytes and turns an invoice
       into CJK; the share collapses from above 0.80 to near zero. This
       rule engages only where it has standing, so a genuinely Chinese or
       Cyrillic client file is not held to a Latin expectation.

    The non-ASCII characters are then checked against the source, but the
    family of encodings tried is fixed by the bytes rather than chosen:
    UTF-16 is considered only for a source that demonstrably is UTF-16.
    Within a family the particular codec is still left open, because a
    file can be genuinely ambiguous between cp1252 and Latin-1 and the
    question worth asking is not "did it pick the one I would have
    picked" but "are the characters it delivered actually in the client's
    file".
    """
    from pathlib import Path as _Path

    try:
        delivered = _Path(output).read_bytes()
        raw = _Path(source).read_bytes()
    except OSError as exc:
        return CheckOutcome(CheckResult.FAIL, f"files unreadable: {exc}")

    try:
        text = delivered.decode("utf-8")
    except UnicodeDecodeError as exc:
        return CheckOutcome(
            CheckResult.FAIL,
            f"the delivered artifact is not UTF-8: {exc}")
    if "�" in text:
        return CheckOutcome(
            CheckResult.FAIL,
            "the delivered artifact contains the replacement character, "
            "so at least one character of the client's data was lost in "
            "reading or writing it")

    utf16_source = _is_utf16_family(raw)

    if not utf16_source:
        source_ascii = _printable_share(raw[:_SNIFF_BYTES])
        output_ascii = _printable_share(
            [ord(c) for c in text[:_SNIFF_BYTES]])
        if (source_ascii >= _SOURCE_IS_ASCII_ENOUGH
                and output_ascii < _OUTPUT_MUST_STAY_ASCII):
            return CheckOutcome(
                CheckResult.FAIL,
                f"the source is {source_ascii:.0%} printable ASCII but the "
                f"deliverable is only {output_ascii:.0%}; single-byte text "
                "has been read as a wide encoding and the deliverable is "
                "mojibake",
                computed={"source_ascii_share": round(source_ascii, 4),
                          "output_ascii_share": round(output_ascii, 4)})

    exotic = {c for c in text if ord(c) > 127}
    if not exotic:
        return CheckOutcome(CheckResult.PASS,
                            "the deliverable is plain ASCII; no character "
                            "could have been substituted")

    # Decoded here, by this check, with no reference to what the worker
    # decided - and with the encoding family pinned by the bytes above, so
    # that the one codec capable of manufacturing an excuse is unavailable
    # unless the source really is written in it.
    candidates = ("utf-16", "utf-16-le", "utf-16-be") if utf16_source else (
        "utf-8-sig", "cp1252", "latin-1")
    for encoding in candidates:
        try:
            candidate = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
        if "�" in candidate:
            continue
        if exotic <= set(candidate):
            return CheckOutcome(
                CheckResult.PASS,
                f"every non-ASCII character delivered is present in the "
                f"source when read as {encoding}")
    sample = "".join(sorted(exotic))[:24]
    return CheckOutcome(
        CheckResult.FAIL,
        f"the deliverable contains characters ({sample!r}) that appear in "
        "the source under no reading of it in its own encoding family; the "
        "source was very likely decoded as the wrong encoding and the "
        "deliverable is mojibake")


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
    "no_character_substitution": check_no_character_substitution,
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
