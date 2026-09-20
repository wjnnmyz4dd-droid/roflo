"""Generated certification evidence: many varied cases, one controlled defect each.

The hand-written battery in :mod:`solvent.verifiercert` measured agreement with
about twenty fixed fixtures. That turned out to certify the wrong thing. Four
verifiers were built that simply hardcode those fixtures — one of them decided a
total was correct if it was not ``999.99`` — and every one of them earned full
CERTIFIED status and was trusted for the exact check it was faking. A fixed,
small, public set of examples measures *familiarity with the examples*, not the
ability to detect a defect.

So certification evidence is generated instead, and three properties make it
evidence rather than volume:

**The answer is generated before the question.** Clean rows are invented first
and the *correct* output is known by construction; the messy source is then
derived from it by adding duplicates, padding and alternative date formats. The
worker never produces both the artifact and the oracle, so a verifier that
agrees with the worker is not thereby agreeing with the truth.

**Each bad case carries exactly one defect, at a location and value the
generator chose.** A verifier is not asked "is this the wrong total I showed you
last time?" but "is this total wrong?", about a figure it has not seen. Catching
one instance of a defect class proves nothing; the floor requires several, each
at a different place with different values.

**Development cases and certification cases come from different seeds.** A
verifier fixed until it passes its development fixtures has demonstrated that it
passes its development fixtures. The seeds are recorded so every run is
reproducible, and a surprise battery drawn from yet another seed is what keeps a
certified verifier honest afterwards.

Nothing here is an authority. It builds files and reports what a verifier said
about them; the capability registry decides what that means.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

#: Seeds are recorded rather than chosen at run time, so a certification run is
#: reproducible and a failure can be replayed exactly. Three disjoint streams:
#: what a verifier is built against, what certifies it, and what ambushes it
#: afterwards. A verifier tuned until the development stream passes has proven
#: only that.
DEVELOPMENT_SEED = 20260920
CERTIFICATION_SEED = 81174903
SURPRISE_SEED = 55219648

_FIRST = ("Acme", "Borealis", "Cyngor", "Delta", "Ellsworth", "Fenwick",
          "Gravesend", "Halcyon", "Ibarra", "Jandali", "Kowalski", "Lindqvist",
          "Marchetti", "Nakamura", "Oyelaran", "Pemberton", "Quist", "Rasmussen",
          "Szabó", "Thackeray", "Ueda", "Vasquez", "Whitcombe", "Ximenes")
_SUFFIX = ("Ltd", "LLC", "GmbH", "Corp", "Holdings", "Partners", "Group",
           "& Sons", "Industries", "Trading Co")
_REGIONS = ("North", "South", "East", "West")
_ITEMS = ("Consulting", "Support", "Licensing", "Training", "Hosting",
          "Retainer", "Integration", "Audit", "Migration", "Advisory")


@dataclass(frozen=True, slots=True)
class Case:
    """One generated job: a source, the output that is correct for it, and the
    parameters each check needs. ``good_output`` is true by construction."""

    source_name: str
    source_text: str
    good_output: str
    params: dict = field(default_factory=dict)
    #: Facts the generator knows and a verifier must not be told.
    truth: dict = field(default_factory=dict)


def _company(rng) -> str:
    return f"{rng.choice(_FIRST)} {rng.choice(_SUFFIX)}"


def _amount(rng) -> str:
    return f"{rng.randrange(1, 40000) / 100:.2f}"


def _date(rng) -> tuple[str, str, bool]:
    """``(iso, as_supplied, unambiguous)`` — the same day, and whether it is one day.

    The third value matters for ground truth. ``05/10/2026`` with no stated
    convention genuinely denotes either 10 May or 5 October, so a deliverable
    that picks the other reading is not *wrong* — it is a coin flip on data the
    client left ambiguous. Marking such a case BAD would be asserting a truth
    nobody has, and a battery that does that punishes a verifier for refusing to
    guess. Only unambiguous dates are used to build transposition defects.
    """
    year = rng.choice((2025, 2026))
    month = rng.randrange(1, 13)
    day = rng.randrange(1, 29)
    iso = f"{year:04d}-{month:02d}-{day:02d}"
    style = rng.randrange(4)
    if style == 0:
        return iso, iso, True
    if style == 1:
        return iso, f"{month:02d}/{day:02d}/{year:04d}", day > 12
    if style == 2:
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        return iso, f"{day:02d}-{months[month - 1]}-{year:04d}", True
    if day > 12:
        return iso, f"{day:02d}/{month:02d}/{year:04d}", True
    return iso, iso, True


CSV_HEADER = "Order ID,Customer,Order Date,Region,Units,Total"


def _quote(value: str) -> str:
    if any(c in value for c in ',"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def _rows_to_csv(rows, header=None) -> str:
    lines = [",".join(_quote(h) for h in header) if header else CSV_HEADER]
    lines += [",".join(_quote(str(cell)) for cell in row) for row in rows]
    return "\n".join(lines) + "\n"


def csv_case(rng: random.Random) -> Case:
    """A cleanup job whose correct output is known before the source exists.

    The clean rows are invented first. The source is then *dirtied* from them —
    duplicated, padded, dates rewritten, regions lower-cased and the whole thing
    shuffled — so the cleaned output is true by construction rather than by
    asking the worker what it produced.
    """
    count = rng.randrange(3, 26)
    clean, supplied, unambiguous_ids = [], [], set()
    used_ids = set()
    for _ in range(count):
        while True:
            order_id = rng.randrange(1000, 9999)
            if order_id not in used_ids:
                used_ids.add(order_id)
                break
        customer = _company(rng)
        iso, as_supplied, unambiguous = _date(rng)
        region = rng.choice(_REGIONS)
        units = rng.randrange(1, 500)
        total = _amount(rng)
        clean.append([order_id, customer, iso, region, units, total])
        if unambiguous:
            unambiguous_ids.add(order_id)
        supplied.append([order_id,
                         f"  {customer}  " if rng.random() < 0.4 else customer,
                         as_supplied,
                         region.lower() if rng.random() < 0.5 else region,
                         units, total])

    duplicates = rng.randrange(0, max(2, count // 3))
    dirty = list(supplied)
    for _ in range(duplicates):
        dirty.append(list(rng.choice(supplied)))
    rng.shuffle(dirty)

    # The correct output carries the two operations the owner asked to have
    # certified, applied in the order the plan fixes: rename before anything
    # refers to the new names, sort last. "Units" is renamed because no other
    # requirement names it, so the rename cannot collide with another check's
    # column. Sorting is by Order ID, which is unique and fixed-width, so the
    # ordering is total and lexicographic ordering is the numeric one — there
    # is no hidden ambiguity for the battery to depend on.
    renamed_from, renamed_to = "Units", rng.choice(
        ("Quantity", "Qty", "Unit Count", "Volume"))
    clean_header = [renamed_to if h == renamed_from else h
                    for h in CSV_HEADER.split(",")]
    ordered = sorted(clean, key=lambda row: str(row[0]))
    # Indices into the *delivered* rows, not the invented ones. Sorting moved
    # them, and a defect builder that transposes "row 3" must transpose the
    # row that is third in the file the verifier sees — otherwise it transposes
    # a date the client wrote ambiguously and asserts a truth nobody has.
    unambiguous_rows = [i for i, row in enumerate(ordered)
                        if row[0] in unambiguous_ids]

    mapping = {r.lower(): r for r in _REGIONS}
    return Case(
        source_name="client_supplied.csv",
        source_text=_rows_to_csv(dirty),
        good_output=_rows_to_csv(ordered, header=clean_header),
        params={
            "rename_headers": {"mapping": {renamed_from: renamed_to}},
            "sort_rows": {"columns": ["Order ID"]},
            "drop_exact_duplicates": {},
            "row_reconciliation": {"duplicates_removed": True},
            "preserve_columns": {"columns": ["Total"]},
            "normalise_dates": {"columns": ["Order Date"]},
            "require_columns": {"columns": ["Order ID", "Order Date",
                                            "Region", "Total"]},
            "trim_whitespace": {"columns": ["Customer"]},
            "map_values": {"column": "Region", "case_insensitive": True,
                           "mapping": mapping},
            "parses_as_csv": {},
            "no_unauthorised_changes": {
                "authorised_columns": {"Order Date": "full", "Region": "full",
                                       "Customer": "whitespace",
                                       renamed_from: "rename",
                                       renamed_to: "rename"},
                "renamed_columns": {renamed_from: renamed_to}},
        },
        truth={"clean_rows": ordered, "duplicates": duplicates,
               "unambiguous_rows": unambiguous_rows,
               "renamed_from": renamed_from, "renamed_to": renamed_to,
               "sort_columns": ["Order ID"]})


def report_case(rng: random.Random) -> Case:
    """A report whose correct rendering is known before the document exists."""
    client = _company(rng)
    period = f"Q{rng.randrange(1, 5)} {rng.choice((2025, 2026))}"
    row_count = rng.randrange(2, 9)
    rows = [{"item": item, "amount": _amount(rng)}
            for item in rng.sample(_ITEMS, row_count)]
    total = sum(float(r["amount"]) for r in rows)
    supplied_contact = rng.random() < 0.5
    contact = _company(rng).split()[0] + " " + rng.choice(
        ("Okonkwo", "Bergström", "Ferrari", "Nakagawa", "Oyelowo", "Petrov"))

    facts = {"client": client, "period": period}
    if supplied_contact:
        facts["prepared_for"] = contact
    facts["rows"] = rows

    lines = ["## Overview", "",
             f"- client: {client}",
             f"- period: {period}",
             f"- prepared_for: {contact}" if supplied_contact
             else "- prepared_for: NOT SUPPLIED",
             "- approved_by: NOT SUPPLIED",
             "", "## Lines", "", "| item | amount |", "|---|---|"]
    lines += [f"| {r['item']} | {r['amount']} |" for r in rows]
    lines += ["", "## Totals", "", f"- amount total: {total:.2f}", ""]

    return Case(
        source_name="facts.json",
        source_text=json.dumps(facts, indent=2, ensure_ascii=False),
        good_output="\n".join(lines).rstrip() + "\n",
        params={
            "report_facts_rendered": {
                "fields": ["client", "period", "prepared_for", "approved_by"]},
            "report_missing_disclosed": {
                "fields": ["client", "period", "prepared_for", "approved_by"]},
            "report_total_correct": {"column": "amount"},
            "report_rows_tabulated": {},
            "report_no_invented_facts": {},
            "report_opens": {},
            "report_has_sections": {
                "headings": ["Overview", "Lines", "Totals"], "ordered": True},
            "report_section": {"kind": "facts", "heading": "Overview",
                               "fields": ["client", "period"]},
        },
        truth={"client": client, "period": period, "total": f"{total:.2f}",
               "rows": rows, "contact": contact if supplied_contact else None})


GENERATORS = {"csv-cleanup": csv_case, "report-builder": report_case}

#: Mirrors verifiercert; repeated here so this module has no import of it.
GOOD = "GOOD"
BAD = "BAD"


# --------------------------------------------------------------- defect classes
#
# One controlled defect per case, placed and valued by the generator. A verifier
# is never asked "is this the wrong total I showed you before?" but "is this
# total wrong?", about a figure it has not seen. The defect classes are
# capability-specific on purpose: what can go wrong with a cleaned spreadsheet
# and what can go wrong with a written report are different lists, and a shared
# global list would be wrong for both.

@dataclass(frozen=True, slots=True)
class Defect:
    """One way work can be wrong, and how to make a case wrong in that way.

    ``inject`` returns the mutated text, or ``None`` when this case cannot carry
    this defect — a two-row file has no middle row to drop. A case that cannot
    carry a defect is skipped rather than faked, so the count of instances is a
    count of real instances.
    """

    name: str
    check: str
    inject: object
    #: "output" mutates the deliverable; "source" pairs a correct deliverable
    #: with the wrong source, which is a different failure entirely.
    kind: str = "output"
    note: str = ""


def _csv_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _rebuild(lines) -> str:
    return "\n".join(lines) + "\n"


def _split(line: str) -> list[str]:
    import csv as _csv
    import io

    return next(_csv.reader(io.StringIO(line)))


def _join(cells) -> str:
    return ",".join(_quote(str(c)) for c in cells)


def _pick_body(lines, rng):
    """A random body row index, or None when there is nothing to choose from."""
    if len(lines) < 2:
        return None
    return rng.randrange(1, len(lines))


# ------------------------------------------------------------------- CSV defects
def _row_loss(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None or len(lines) < 3:
        return None
    return _rebuild(lines[:index] + lines[index + 1:])


def _row_addition(case, rng):
    """A duplicate left behind, at a random position — not always the last."""
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    at = rng.randrange(1, len(lines) + 1)
    return _rebuild(lines[:at] + [lines[index]] + lines[at:])


def _protected_value_changed(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    cells = _split(lines[index])
    cells[5] = _amount(rng)
    lines[index] = _join(cells)
    return _rebuild(lines)


def _protected_value_one_digit(case, rng):
    """The hardest kind: 450.50 becomes 450.05."""
    lines = _csv_lines(case.good_output)
    for _ in range(12):
        index = _pick_body(lines, rng)
        if index is None:
            return None
        cells = _split(lines[index])
        digits = [i for i, c in enumerate(cells[5]) if c.isdigit()]
        if not digits:
            continue
        position = rng.choice(digits)
        original = cells[5][position]
        replacement = str((int(original) + rng.randrange(1, 10)) % 10)
        if replacement == original:
            continue
        cells[5] = cells[5][:position] + replacement + cells[5][position + 1:]
        lines[index] = _join(cells)
        return _rebuild(lines)
    return None


def _wrong_date_conversion(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    cells = _split(lines[index])
    year, month, day = cells[2].split("-")
    cells[2] = f"{month}/{day}/{year}"
    lines[index] = _join(cells)
    return _rebuild(lines)


def _date_transposed(case, rng):
    """Day and month swapped; still looks exactly like an ISO date.

    Restricted to rows whose supplied date had exactly one reading. Transposing
    a date the client wrote ambiguously produces a case whose ground truth
    nobody knows, and a battery must not assert one.
    """
    rows = case.truth.get("unambiguous_rows") or []
    if not rows:
        return None
    lines = _csv_lines(case.good_output)
    candidates = [r for r in rows if r + 1 < len(lines)]
    rng.shuffle(candidates)
    for row_index in candidates:
        index = row_index + 1               # skip the header line
        cells = _split(lines[index])
        year, month, day = cells[2].split("-")
        if month == day or int(day) > 12:
            continue
        cells[2] = f"{year}-{day}-{month}"
        lines[index] = _join(cells)
        return _rebuild(lines)
    return None


def _ragged_row(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    lines[index] = lines[index] + ",extra"
    return _rebuild(lines)


def _binary_content(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    lines[index] = lines[index] + "\x00\x07"
    return _rebuild(lines)


def _missing_column(case, rng):
    lines = _csv_lines(case.good_output)
    drop = rng.choice((0, 2, 3, 5))       # a column some requirement names
    out = []
    for line in lines:
        cells = _split(line)
        out.append(_join([c for i, c in enumerate(cells) if i != drop]))
    return _rebuild(out)


def _whitespace_remains(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    cells = _split(lines[index])
    cells[1] = f"  {cells[1]}  "
    lines[index] = _join(cells)
    return _rebuild(lines)


def _unmapped_value(case, rng):
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    cells = _split(lines[index])
    cells[3] = cells[3].lower()
    lines[index] = _join(cells)
    return _rebuild(lines)


def _unauthorised_transformation(case, rng):
    """An improvement nobody asked for: the customer name, upper-cased."""
    lines = _csv_lines(case.good_output)
    index = _pick_body(lines, rng)
    if index is None:
        return None
    cells = _split(lines[index])
    if cells[1] == cells[1].upper():
        return None
    cells[1] = cells[1].upper()
    lines[index] = _join(cells)
    return _rebuild(lines)


def _columns_reordered(case, rng):
    """Somebody's spreadsheet rearranged without being asked.

    The other class found by inventing defects after the battery existed. Every
    check compared columns by name, so a shuffled file matched perfectly.
    """
    lines = _csv_lines(case.good_output)
    header = _split(lines[0])
    if len(header) < 2:
        return None
    order = list(range(len(header)))
    a, b = rng.sample(order, 2)
    order[a], order[b] = order[b], order[a]
    return _rebuild([_join([_split(line)[i] for i in order]) for line in lines])


def _rename_not_applied(case, rng):
    """The header is exactly as the client wrote it: the rename did not happen."""
    old, new = case.truth.get("renamed_from"), case.truth.get("renamed_to")
    lines = _csv_lines(case.good_output)
    header = _split(lines[0])
    if not old or not new or new not in header:
        return None
    header[header.index(new)] = old
    lines[0] = _join(header)
    return _rebuild(lines)


def _rename_wrong_name(case, rng):
    """Renamed — to a name the client did not ask for.

    The failure that looks most like success: the old name is gone, the header
    reads well, and only the agreed mapping says it is wrong. A check that only
    confirmed the old name had disappeared would accept this.
    """
    old, new = case.truth.get("renamed_from"), case.truth.get("renamed_to")
    lines = _csv_lines(case.good_output)
    header = _split(lines[0])
    if not old or not new or new not in header:
        return None
    alternatives = [n for n in ("Quantity", "Qty", "Unit Count", "Volume",
                                "Item Count", "Number of Units")
                    if n != new and n not in header]
    if not alternatives:
        return None
    header[header.index(new)] = rng.choice(alternatives)
    lines[0] = _join(header)
    return _rebuild(lines)


def _unrequested_rename(case, rng):
    """A second column renamed, which no requirement asked for.

    Belongs to ``no_unauthorised_changes`` rather than to ``rename_headers``:
    the requested rename was performed correctly, so the rename check is
    satisfied and right to be. What went wrong is that something *else* changed.
    Before renames were certified this was invisible — a column absent from the
    output under its agreed name simply dropped out of every name-based
    comparison.
    """
    new = case.truth.get("renamed_to")
    lines = _csv_lines(case.good_output)
    header = _split(lines[0])
    victims = [i for i, name in enumerate(header)
               if name != new and name != "Order ID"]
    if not victims:
        return None
    at = rng.choice(victims)
    header[at] = header[at].replace(" ", "") + "_v2"
    lines[0] = _join(header)
    return _rebuild(lines)


def _renamed_column_moved(case, rng):
    """The renamed column swapped with the one beside it.

    Found while certifying ``rename_headers``. A renamed column shares no name
    between source and deliverable, so it was invisible to every comparison
    made by name — and swapping it with its neighbour moved one of the client's
    *other* columns while leaving the projection of shared names untouched.
    Nine instances were accepted. The class stays in the battery so the fix
    cannot quietly come undone.
    """
    new = case.truth.get("renamed_to")
    lines = _csv_lines(case.good_output)
    header = _split(lines[0])
    if not new or new not in header:
        return None
    at = header.index(new)
    other = at + 1 if at + 1 < len(header) else at - 1
    if other < 0:
        return None
    order = list(range(len(header)))
    order[at], order[other] = order[other], order[at]
    return _rebuild([_join([_split(line)[i] for i in order]) for line in lines])


def _sort_column_index(case, lines):
    columns = case.truth.get("sort_columns") or []
    header = _split(lines[0])
    if len(columns) != 1 or columns[0] not in header:
        return None
    return header.index(columns[0])


def _rows_unsorted(case, rng):
    """Sorted everywhere except one adjacent pair.

    A near-miss on purpose. "Is this file sorted?" answered by glancing at the
    first rows, or by trusting that the worker called sort, accepts this.
    """
    lines = _csv_lines(case.good_output)
    if _sort_column_index(case, lines) is None or len(lines) < 4:
        return None
    body = lines[1:]
    at = rng.randrange(0, len(body) - 1)
    body[at], body[at + 1] = body[at + 1], body[at]
    if body == lines[1:]:
        return None
    return _rebuild([lines[0]] + body)


def _sorted_by_wrong_column(case, rng):
    """Ordered, tidily, by a column the client did not name."""
    lines = _csv_lines(case.good_output)
    index = _sort_column_index(case, lines)
    if index is None or len(lines) < 4:
        return None
    header = _split(lines[0])
    others = [i for i in range(len(header)) if i != index]
    rng.shuffle(others)
    for other in others:
        body = sorted(lines[1:], key=lambda line: str(_split(line)[other]))
        if body != lines[1:]:
            return _rebuild([lines[0]] + body)
    return None


def _sort_reversed(case, rng):
    """Descending where the requirement says ascending."""
    lines = _csv_lines(case.good_output)
    if _sort_column_index(case, lines) is None or len(lines) < 4:
        return None
    body = list(reversed(lines[1:]))
    if body == lines[1:]:
        return None
    return _rebuild([lines[0]] + body)


def _row_values_rotated(case, rng):
    """One column shifted onto the neighbouring rows: every value present, every
    value against the wrong record.

    What sorting a column instead of sorting the rows produces. ``Total`` is
    used rather than the sort key, so the deliverable still reads as correctly
    ordered — the defect is invisible to a check that looks at each column on
    its own, which was every check in the capability until this class was added.
    """
    lines = _csv_lines(case.good_output)
    if len(lines) < 4:
        return None
    header = _split(lines[0])
    if "Total" not in header:
        return None
    at = header.index("Total")
    rows = [_split(line) for line in lines[1:]]
    values = [row[at] for row in rows]
    shifted = values[1:] + values[:1]
    if shifted == values:
        return None
    for row, value in zip(rows, shifted):
        row[at] = value
    return _rebuild([lines[0]] + [_join(row) for row in rows])


CSV_DEFECTS = (
    Defect("ROW_VALUES_ROTATED", "row_reconciliation", _row_values_rotated,
           note="every value present, every value on the wrong row"),
    Defect("COLUMNS_REORDERED", "no_unauthorised_changes", _columns_reordered,
           note="rearranged without a requirement asking for it"),
    Defect("RENAMED_COLUMN_MOVED", "no_unauthorised_changes", _renamed_column_moved,
           note="the renamed column swapped with its neighbour"),
    Defect("UNREQUESTED_RENAME", "no_unauthorised_changes", _unrequested_rename,
           note="a column renamed that no requirement named"),
    Defect("RENAME_NOT_APPLIED", "rename_headers", _rename_not_applied),
    Defect("RENAME_WRONG_NAME", "rename_headers", _rename_wrong_name,
           note="old name gone, new name not the agreed one"),
    Defect("ROWS_UNSORTED", "sort_rows", _rows_unsorted,
           note="one adjacent pair out of order"),
    Defect("SORTED_BY_WRONG_COLUMN", "sort_rows", _sorted_by_wrong_column),
    Defect("SORT_REVERSED", "sort_rows", _sort_reversed),
    Defect("ROW_LOSS", "row_reconciliation", _row_loss),
    Defect("ROW_ADDITION", "drop_exact_duplicates", _row_addition),
    Defect("PROTECTED_VALUE_CHANGE", "preserve_columns", _protected_value_changed),
    Defect("PROTECTED_VALUE_ONE_DIGIT", "preserve_columns",
           _protected_value_one_digit, note="450.50 becomes 450.05"),
    Defect("WRONG_DATE_CONVERSION", "normalise_dates", _wrong_date_conversion),
    Defect("DATE_TRANSPOSED", "normalise_dates", _date_transposed,
           note="day and month swapped; still a valid-looking ISO date"),
    Defect("RAGGED_ROW", "parses_as_csv", _ragged_row),
    Defect("BINARY_CONTENT", "parses_as_csv", _binary_content),
    Defect("MISSING_COLUMN", "require_columns", _missing_column),
    Defect("WHITESPACE_REMAINS", "trim_whitespace", _whitespace_remains),
    Defect("UNMAPPED_VALUE", "map_values", _unmapped_value),
    Defect("UNAUTHORIZED_TRANSFORMATION", "no_unauthorised_changes",
           _unauthorised_transformation),
)


# ---------------------------------------------------------------- report defects
def _lines_of(text):
    return text.splitlines()


def _fact_line(lines, field_name):
    for index, line in enumerate(lines):
        if line.strip().startswith(f"- {field_name}:"):
            return index
    return None


def _present_fields(case):
    fields = ["client", "period"]
    if case.truth.get("contact"):
        fields.append("prepared_for")
    return fields


def _omission(case, rng):
    lines = _lines_of(case.good_output)
    index = _fact_line(lines, rng.choice(_present_fields(case)))
    if index is None:
        return None
    return "\n".join(lines[:index] + lines[index + 1:]) + "\n"


def _alteration(case, rng):
    lines = _lines_of(case.good_output)
    field_name = rng.choice(_present_fields(case))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    lines[index] = f"- {field_name}: {_company(rng)}"
    return "\n".join(lines) + "\n"


def _similar_name(case, rng):
    """One character different. Jon Rivera instead of John Rivera."""
    lines = _lines_of(case.good_output)
    field_name = rng.choice(_present_fields(case))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    prefix, _, value = lines[index].partition(": ")
    letters = [i for i, c in enumerate(value) if c.isalpha()]
    if not letters:
        return None
    position = rng.choice(letters)
    original = value[position]
    replacement = rng.choice([c for c in "abcdefghijklmnopqrstuvwxyz"
                              if c != original.lower()])
    replacement = replacement.upper() if original.isupper() else replacement
    lines[index] = f"{prefix}: {value[:position]}{replacement}{value[position + 1:]}"
    return "\n".join(lines) + "\n"


def _substring_confusion(case, rng):
    """The correct value, extended. Acme Corp becomes Acme Corporation."""
    lines = _lines_of(case.good_output)
    field_name = rng.choice(_present_fields(case))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    lines[index] = lines[index] + rng.choice(
        ("oration", " International", " (UK)", " Holdings", "shire"))
    return "\n".join(lines) + "\n"


def _value_qualified(case, rng):
    lines = _lines_of(case.good_output)
    field_name = rng.choice(_present_fields(case))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    lines[index] = lines[index] + rng.choice(
        (" (provisional)", " — estimated", " [draft]", " approx."))
    return "\n".join(lines) + "\n"


def _fabricated_absence(case, rng):
    """A blank filled in with something plausible."""
    lines = _lines_of(case.good_output)
    index = _fact_line(lines, "approved_by")
    if index is None:
        return None
    lines[index] = ("- approved_by: "
                    + rng.choice(("J. Rivera", "M. Okafor", "S. Lindqvist",
                                  "A. Whitcombe", "T. Nakagawa")))
    return "\n".join(lines) + "\n"


def _absence_dropped(case, rng):
    lines = _lines_of(case.good_output)
    index = _fact_line(lines, "approved_by")
    if index is None:
        return None
    return "\n".join(lines[:index] + lines[index + 1:]) + "\n"


def _wrong_total(case, rng):
    lines = _lines_of(case.good_output)
    for index, line in enumerate(lines):
        if "amount total:" in line:
            lines[index] = f"- amount total: {_amount(rng)}"
            return "\n".join(lines) + "\n"
    return None


def _total_digit_swap(case, rng):
    """450.50 becomes 450.05 — two digits transposed, sum still plausible."""
    lines = _lines_of(case.good_output)
    for index, line in enumerate(lines):
        if "amount total:" not in line:
            continue
        value = line.split(": ", 1)[1]
        digits = [i for i, c in enumerate(value) if c.isdigit()]
        for _ in range(12):
            a, b = rng.choice(digits), rng.choice(digits)
            if a == b or value[a] == value[b]:
                continue
            chars = list(value)
            chars[a], chars[b] = chars[b], chars[a]
            lines[index] = f"- amount total: {''.join(chars)}"
            return "\n".join(lines) + "\n"
        return None
    return None


def _unsupported_inference(case, rng):
    return (case.good_output.rstrip() + "\n\n## Outlook\n\n"
            f"- Projected next period: {_amount(rng)}\n")


def _invented_number(case, rng):
    lines = _lines_of(case.good_output)
    index = _fact_line(lines, "period")
    if index is None:
        return None
    lines.insert(index + 1, f"- headcount: {rng.randrange(11, 999)}")
    return "\n".join(lines) + "\n"


def _row_omitted(case, rng):
    lines = _lines_of(case.good_output)
    table = [i for i, line in enumerate(lines)
             if line.startswith("| ") and not line.startswith("| item")]
    if len(table) < 2:
        return None
    index = rng.choice(table)
    return "\n".join(lines[:index] + lines[index + 1:]) + "\n"


def _missing_section(case, rng):
    heading = rng.choice(("## Overview", "## Lines", "## Totals"))
    return case.good_output.replace(heading, "## Appendix", 1)


def _wrong_section(case, rng):
    """Correct content, wrong place: a fact moved out of its section."""
    lines = _lines_of(case.good_output)
    field_name = rng.choice(("client", "period"))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    moved = lines.pop(index)
    for position, line in enumerate(lines):
        if line.strip() == "## Lines":
            lines.insert(position + 2, moved)
            return "\n".join(lines) + "\n"
    return None


def _emptied_document(case, rng):
    """Nothing at all, or whitespace pretending to be a document."""
    return rng.choice(("", "   \n\n  \n", "\n"))


def _no_headings(case, rng):
    """Text, but not a report: every heading stripped out."""
    lines = [l for l in _lines_of(case.good_output) if not l.startswith("## ")]
    body = "\n".join(lines).strip()
    return (body + "\n") if body else None


def _duplicated_fact(case, rng):
    """The same fact stated twice, with two different values.

    Added after the main battery was written, deliberately, to find out what an
    unmodelled defect would do. Nothing caught it: the correct line really was
    present, so every check passed while the document named two different
    clients. The class exists now because that question was asked.
    """
    lines = _lines_of(case.good_output)
    field_name = rng.choice(_present_fields(case))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    lines.insert(index + 1, f"- {field_name}: {_company(rng)}")
    return "\n".join(lines) + "\n"


def _restated_fact(case, rng):
    """The same fact stated twice, identically."""
    lines = _lines_of(case.good_output)
    field_name = rng.choice(_present_fields(case))
    index = _fact_line(lines, field_name)
    if index is None:
        return None
    lines.insert(index + 1, lines[index])
    return "\n".join(lines) + "\n"


REPORT_DEFECTS = (
    Defect("DUPLICATED_FACT", "report_facts_rendered", _duplicated_fact,
           note="two different values for one fact"),
    Defect("RESTATED_FACT", "report_facts_rendered", _restated_fact),
    Defect("EMPTY_DOCUMENT", "report_opens", _emptied_document),
    Defect("NO_SECTIONS", "report_opens", _no_headings,
           note="readable text that is not a report"),
    Defect("OMISSION", "report_facts_rendered", _omission),
    Defect("ALTERATION", "report_facts_rendered", _alteration),
    Defect("SIMILAR_NAME", "report_facts_rendered", _similar_name,
           note="one character different"),
    Defect("SUBSTRING_CONFUSION", "report_facts_rendered", _substring_confusion,
           note="the correct value, extended"),
    Defect("VALUE_QUALIFIED", "report_facts_rendered", _value_qualified),
    Defect("FABRICATED_ABSENCE", "report_missing_disclosed", _fabricated_absence,
           note="a blank filled with a plausible name"),
    Defect("ABSENCE_DROPPED", "report_missing_disclosed", _absence_dropped),
    Defect("WRONG_NUMBER", "report_total_correct", _wrong_total),
    Defect("TOTAL_DIGIT_SWAP", "report_total_correct", _total_digit_swap,
           note="450.50 becomes 450.05"),
    Defect("UNSUPPORTED_INFERENCE", "report_no_invented_facts",
           _unsupported_inference),
    Defect("INVENTED_NUMBER", "report_no_invented_facts", _invented_number),
    Defect("ROW_OMITTED", "report_rows_tabulated", _row_omitted),
    Defect("MISSING_SECTION", "report_has_sections", _missing_section),
    Defect("WRONG_SECTION", "report_section", _wrong_section,
           note="correct content, wrong place"),
)

#: Defects that do not mutate the deliverable at all. The output is *correct* —
#: for a different job. Relative correctness against the wrong source is still
#: wrong, and a verifier that never reads the source cannot tell.
SOURCE_DEFECTS = ("WRONG_SOURCE", "CROSS_JOB")

DEFECTS = {"csv-cleanup": CSV_DEFECTS, "report-builder": REPORT_DEFECTS}


def _generator_for(capability: str) -> str:
    """Which generator builds cases for this capability, and why that one.

    Not simply its name. A new capability that produces documents and claims to
    decide the report checks is certified against report cases — otherwise a
    capability escapes certification merely by being called something no
    generator is registered under, which is how the first attempt at this
    blocked a malicious verifier for the wrong reason.
    """
    name = (capability or "").split("/", 1)[0]
    if name in GENERATORS:
        return name
    try:
        from .checks import checks_for
    except ImportError:  # pragma: no cover - defensive
        return ""
    claimed = set(checks_for(capability))
    if not claimed:
        return ""
    best, overlap = "", 0
    for candidate, defects in DEFECTS.items():
        covered = len(claimed & {d.check for d in defects})
        if covered > overlap:
            best, overlap = candidate, covered
    return best


@dataclass(frozen=True, slots=True)
class GeneratedTrial:
    """One generated case, ready to be put in front of a verifier."""

    name: str
    check: str
    expect: str                 # GOOD or BAD
    params: dict
    source_text: str
    source_name: str
    output_text: str
    defect_class: str = ""
    note: str = ""


def generate(capability: str, *, seed: int, cases: int) -> list[GeneratedTrial]:
    """Build a certification battery. Deterministic for a given ``seed``.

    Each case contributes one GOOD trial per check and one BAD trial per defect
    class that applies to it. Cases that cannot carry a defect are skipped for
    that defect rather than faked, so an instance count is a count of real
    instances.
    """
    name = _generator_for(capability)
    if not name:
        return []
    generator, defects = GENERATORS[name], DEFECTS[name]
    trials: list[GeneratedTrial] = []
    previous: Case | None = None

    for index in range(cases):
        rng = random.Random(f"{name}:{seed}:{index}")
        case = generator(rng)

        for check, params in case.params.items():
            trials.append(GeneratedTrial(
                name=f"good-{index}-{check}", check=check, expect=GOOD,
                params=params, source_text=case.source_text,
                source_name=case.source_name, output_text=case.good_output,
                defect_class="CORRECT"))

        for defect in defects:
            mutated = defect.inject(case, rng)
            if mutated is None or mutated == case.good_output:
                continue
            trials.append(GeneratedTrial(
                name=f"bad-{index}-{defect.name}", check=defect.check,
                expect=BAD, params=case.params.get(defect.check, {}),
                source_text=case.source_text, source_name=case.source_name,
                output_text=mutated, defect_class=defect.name,
                note=defect.note))

        # A correct deliverable paired with the previous case's source. Nothing
        # about the file is wrong; it is simply not this job's file.
        if previous is not None:
            for check in ("row_reconciliation", "drop_exact_duplicates",
                          "preserve_columns", "report_facts_rendered",
                          "report_total_correct", "report_rows_tabulated"):
                if check not in case.params:
                    continue
                trials.append(GeneratedTrial(
                    name=f"bad-{index}-WRONG_SOURCE-{check}", check=check,
                    expect=BAD, params=case.params[check],
                    source_text=previous.source_text,
                    source_name=previous.source_name,
                    output_text=case.good_output, defect_class="WRONG_SOURCE",
                    note="a correct deliverable, checked against another "
                         "job's source"))
                trials.append(GeneratedTrial(
                    name=f"bad-{index}-CROSS_JOB-{check}", check=check,
                    expect=BAD, params=case.params[check],
                    source_text=case.source_text, source_name=case.source_name,
                    output_text=previous.good_output, defect_class="CROSS_JOB",
                    note="another job's correct deliverable, same capability"))
        previous = case
    return trials
