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
    parameters each check needs. ``good_output`` is true by construction.

    Three capabilities in the library do not work in text. A spreadsheet and a
    Word document are ZIP archives, and a data-entry job hands over two files
    rather than one. So a case may carry bytes instead of text, name the
    deliverable explicitly, and bring companion files that sit beside it:

    ``source_bytes`` / ``good_output_bytes``
        Used in place of the text fields when the format is binary. Exactly one
        of each pair is set.
    ``output_name``
        The deliverable's filename, because its extension decides how both the
        worker and the verifier read it.
    ``companions``
        ``{suffix: bytes}``. Each is written beside the deliverable as
        ``<stem><suffix>``, which is how a data-entry job's exceptions file is
        found. A deliverable that is two files has to be certified as two files
        or the check that reconciles them is never exercised.
    ``requirements``
        The brief, as committed requirements. Some checks are properties of the
        whole job rather than of one parameter set -- whether the accept/reject
        split is the split the rules imply, whether a formula names the column
        its requirement named -- and a verifier is entitled to the brief. It is
        never told which trials are the defective ones.
    """

    source_name: str
    source_text: str = ""
    good_output: str = ""
    params: dict = field(default_factory=dict)
    #: Facts the generator knows and a verifier must not be told.
    truth: dict = field(default_factory=dict)
    source_bytes: bytes = b""
    good_output_bytes: bytes = b""
    output_name: str = ""
    companions: dict = field(default_factory=dict)
    requirements: tuple = ()


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
    source_bytes: bytes = b""
    output_bytes: bytes = b""
    output_name: str = ""
    companions: dict = field(default_factory=dict)
    requirements: tuple = ()


def _build(generator, rng, index: int) -> Case:
    """Build one case, telling the generator which number it is if it asks.

    A capability that produces more than one *kind* of deliverable needs its
    battery to cover each kind, and the shapes have to alternate
    deterministically rather than by a coin flip -- otherwise a run can leave
    one shape with too few instances to clear the evidence floor, and the
    certification would differ from seed to seed. ``document-processing``
    extracts, converts and fills templates, so its generator takes the index
    and cycles. The generators that produce one shape ignore it.
    """
    try:
        return generator(rng, index)
    except TypeError:
        return generator(rng)


def _trial(name: str, check: str, expect: str, params: dict, case: Case,
           text: str, raw: bytes, defect_class: str, note: str,
           companions: dict | None = None) -> GeneratedTrial:
    """One trial, carrying whichever of the two shapes this capability uses."""
    return GeneratedTrial(
        name=name, check=check, expect=expect, params=dict(params),
        source_text=case.source_text, source_name=case.source_name,
        output_text=text, defect_class=defect_class, note=note,
        source_bytes=case.source_bytes, output_bytes=raw,
        output_name=case.output_name,
        companions=dict(case.companions if companions is None else companions),
        requirements=tuple(case.requirements))


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
        case = _build(generator, rng, index)

        for check, params in case.params.items():
            trials.append(_trial(
                f"good-{index}-{check}", check, GOOD, params, case,
                case.good_output, case.good_output_bytes, "CORRECT", ""))

        for defect in defects:
            mutated = defect.inject(case, rng)
            if mutated is None:
                continue
            if defect.kind == "source":
                # The deliverable is the correct one; the *source* is a file
                # this capability must refuse. A refusal path shown only
                # sources there is nothing wrong with has been shown nothing.
                name, payload = mutated
                trials.append(GeneratedTrial(
                    name=f"bad-{index}-{defect.name}", check=defect.check,
                    expect=BAD, params=dict(case.params.get(defect.check, {})),
                    source_text=payload if isinstance(payload, str) else "",
                    source_name=name,
                    output_text=case.good_output,
                    defect_class=defect.name, note=defect.note,
                    source_bytes=payload if isinstance(payload, bytes) else b"",
                    output_bytes=case.good_output_bytes,
                    output_name=case.output_name,
                    companions=dict(case.companions),
                    requirements=tuple(case.requirements)))
                continue
            companions = dict(case.companions)
            if isinstance(mutated, tuple):
                # A defect in a multi-file deliverable: the accepted records and
                # the exceptions file have to move together, or a mutation of
                # one is contradicted by the other and the trial tests nothing.
                mutated, extra = mutated
                companions.update(extra)
            if isinstance(mutated, bytes):
                if mutated == case.good_output_bytes \
                        and companions == case.companions:
                    continue
                trials.append(_trial(
                    f"bad-{index}-{defect.name}", defect.check, BAD,
                    case.params.get(defect.check, {}), case, "", mutated,
                    defect.name, defect.note, companions))
                continue
            if mutated == case.good_output and companions == case.companions:
                continue
            trials.append(_trial(
                f"bad-{index}-{defect.name}", defect.check, BAD,
                case.params.get(defect.check, {}), case, mutated, b"",
                defect.name, defect.note, companions))

        # A correct deliverable paired with the previous case's source. Nothing
        # about the file is wrong; it is simply not this job's file.
        #
        # The checks below are the ones whose question this defect answers:
        # "are these the rows of this source?". ``compute_column`` is
        # deliberately not among them -- a wrongly delivered workbook's
        # computed column really is the product of its own inputs, so asking
        # that check to detect the wrong file is asking it to answer
        # row_reconciliation's question. The defect class is still in the
        # battery, aimed at the checks that own it, and both of them have to
        # catch it.
        if previous is not None:
            for check in ("row_reconciliation", "drop_exact_duplicates",
                          "preserve_columns", "report_facts_rendered",
                          "report_total_correct", "report_rows_tabulated",
                          "map_fields", "record_traceability",
                          "extract_fields", "fields_traceable"):
                if check not in case.params:
                    continue
                trials.append(GeneratedTrial(
                    name=f"bad-{index}-WRONG_SOURCE-{check}", check=check,
                    expect=BAD, params=case.params[check],
                    source_text=previous.source_text,
                    source_name=previous.source_name,
                    output_text=case.good_output, defect_class="WRONG_SOURCE",
                    note="a correct deliverable, checked against another "
                         "job's source",
                    source_bytes=previous.source_bytes,
                    output_bytes=case.good_output_bytes,
                    output_name=case.output_name,
                    companions=dict(case.companions),
                    requirements=tuple(case.requirements)))
                trials.append(GeneratedTrial(
                    name=f"bad-{index}-CROSS_JOB-{check}", check=check,
                    expect=BAD, params=case.params[check],
                    source_text=case.source_text, source_name=case.source_name,
                    output_text=previous.good_output, defect_class="CROSS_JOB",
                    note="another job's correct deliverable, same capability",
                    source_bytes=case.source_bytes,
                    output_bytes=previous.good_output_bytes,
                    output_name=previous.output_name or case.output_name,
                    companions=dict(previous.companions),
                    requirements=tuple(case.requirements)))
        previous = case
    return trials


# ===========================================================================
# The three capabilities whose work is not text
# ===========================================================================
#
# A spreadsheet and a Word document are ZIP archives, and a data-entry job
# hands over two files. Everything above still holds: the correct answer is
# built before the question, each bad case carries one defect at a position and
# value the generator chose, and the generator never asks a worker what it
# produced.
#
# The oracles below construct the correct deliverable from the clean rows
# directly, which means they encode the capability's output contract a third
# time -- the worker, the verifier, and here. Where that contract has a detail
# the specification leaves open (a computed column is appended as the last
# column; a totals row is labelled TOTAL in the first), it is named in a
# comment, because a battery that disagreed with the contract would reject
# correct work and the verifier would be blamed for it.

def _column_letter(index: int) -> str:
    """1 -> A. Written here rather than imported: a battery that shares the
    format layer's helper shares its bugs, and a wrong column letter would
    make every formula trial agree for the wrong reason."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _reference(rng) -> str:
    return f"CR-{rng.randrange(1000, 9999)}"


def _decimal(value) -> str:
    from decimal import Decimal

    return str(Decimal(str(value)))


# ----------------------------------------------------- spreadsheet-automation
def sheet_case(rng: random.Random) -> Case:
    """A spreadsheet job whose correct output is known before the source exists.

    Every operation the capability performs, in one job, because certifying
    them one at a time says nothing about what they do to each other. Two
    worksheets are stacked, padding is trimmed, a stray row carrying only a
    reference is dropped, a heading is renamed, a column is computed with a
    formula beside each value, the delivered columns are reordered, the rows
    are sorted, a display format is applied and a totals row is added.

    The clean rows are invented first and the source is dirtied from them. The
    correct deliverable is then built directly in its *final* layout by
    arithmetic written here, so it is true by construction rather than by
    asking the worker what it produced. ``Amount`` is a product of two
    ``Decimal`` figures, which is exact: there is one right answer and no
    epsilon to argue about.
    """
    from decimal import Decimal

    from . import types, xlsx

    count = rng.randrange(3, 13)
    # Item names must be distinct, because the job sorts by Item and a tie
    # would make the correct ordering ambiguous -- a battery that depended on
    # how ties happened to break would punish a verifier for being right.
    items = [f"{name} {index + 1:02d}"
             for index, name in enumerate(
                 rng.choice(_ITEMS) for _ in range(count))]
    rng.shuffle(items)

    clean = []
    for item in items:
        clean.append([item, _reference(rng), rng.randrange(1, 400),
                      Decimal(str(rng.randrange(50, 50000) / 100))])

    header = ["Item", "Client ref", "Units", "Rate"]
    supplied = [[f"  {row[0]} " if rng.random() < 0.4 else row[0],
                 row[1], row[2], row[3]] for row in clean]
    #: A stray row carrying only a reference. Empty across every column the
    #: brief names, so the job drops it; present in the file, so the format
    #: layer still reports it and the dropped count means something.
    strays = [["", _reference(rng), None, None]
              for _ in range(rng.randrange(1, 3))]
    everything = supplied + strays
    rng.shuffle(everything)

    split = max(1, len(everything) // 2)
    first, second = everything[:split], everything[split:]
    source = xlsx.write_workbook([
        xlsx.SheetOut(name="Q1 data", rows=[header] + first, freeze_below_row=2),
        xlsx.SheetOut(name="Q2 data", rows=[header] + second, freeze_below_row=2),
    ])

    # The contract this oracle encodes, stated so a disagreement is a bug in
    # one named place: the delivered column order is the one the selection
    # asked for; a computed column's formula refers to the columns it computes
    # from *after* the selection has moved them; a totals row is appended last
    # with TOTAL in the first column.
    delivered_header = ["Item", "Quantity", "Rate", "Amount", "Client ref"]
    quantity_letter = _column_letter(2)
    rate_letter = _column_letter(3)
    amount_index = 4

    ordered = sorted(clean, key=lambda row: str(row[0]))
    rows = [delivered_header]
    formulas, total = {}, Decimal(0)
    for offset, row in enumerate(ordered):
        amount = Decimal(str(row[2])) * Decimal(str(row[3]))
        total += amount
        rows.append([row[0], row[2], row[3], amount, row[1]])
        sheet_row = offset + 2
        formulas[(sheet_row, amount_index)] = (
            f"{quantity_letter}{sheet_row}*{rate_letter}{sheet_row}")
    total_row_number = len(ordered) + 2
    rows.append(["TOTAL", None, None, total, None])
    amount_letter = _column_letter(amount_index)
    formulas[(total_row_number, amount_index)] = (
        f"SUM({amount_letter}2:{amount_letter}{len(ordered) + 1})")

    good = xlsx.write_workbook([xlsx.SheetOut(
        name="Q1 data", rows=rows, formulas=formulas,
        number_formats={amount_index: "#,##0.00"}, freeze_below_row=2)])

    # The brief: every requirement the job carries. Column names are the
    # *delivered* names throughout, because the rename is the first step and
    # from then on there is one name for each column.
    brief = {
        "opens_as_workbook": {},
        "consolidate_sheets": {"sheets": ["Q1 data", "Q2 data"]},
        "rename_headers": {"mapping": {"Units": "Quantity"}},
        "require_columns": {"columns": ["Item", "Quantity", "Rate"]},
        "trim_whitespace": {"columns": ["Item"]},
        "drop_empty_rows": {"columns": ["Item", "Quantity", "Rate"]},
        "compute_column": {"into": "Amount", "expression": "product",
                           "using": ["Quantity", "Rate"]},
        "select_columns": {"columns": list(delivered_header)},
        "sort_rows": {"columns": ["Item"]},
        "number_format": {"column": "Amount", "format": "#,##0.00"},
        "total_row": {"aggregates": {"Amount": "sum"}},
        "preserve_columns": {"columns": ["Client ref"]},
        "row_reconciliation": {"expected_dropped": len(strays),
                               "expected_added": 1},
        "no_unauthorised_changes": {
            "authorised_columns": ["Item", "Units", "Amount"],
            "added_columns": ["Quantity", "Amount"]},
        "formulas_agree": {},
    }
    requirements = tuple(
        types.Requirement(id=f"r{index}", text=check,
                          source=types.RequirementSource.OWNER_CONFIRMED,
                          check=check, params=dict(args))
        for index, (check, args) in enumerate(brief.items(), start=1))
    # What the battery grades is a subset of the brief, and the one check left
    # out is left out for a stated reason rather than because it is hard. See
    # ``harness.UNCERTIFIABLE``: a display format has no value to recompute, so
    # the verifier answers UNVERIFIABLE by design and there is no defect to
    # show it. Including it would generate GOOD trials a correct verifier
    # cannot pass, which would read as a broken verifier rather than as an
    # honest limit.
    params = {check: args for check, args in brief.items()
              if check not in ("number_format",)}
    return Case(
        source_name="client_data.xlsx", source_bytes=source,
        good_output_bytes=good, output_name="delivered.xlsx",
        params=params, requirements=requirements,
        truth={"rows": ordered, "total": _decimal(total),
               "amount_column": amount_index, "strays": len(strays),
               "second_sheet_items": sorted(
                   str(row[0]).strip() for row in second if str(row[0]).strip())})


def _sheet_tables(data: bytes):
    """``(header, body, formulas)`` of a delivered workbook, for a defect builder."""
    from . import xlsx

    book = xlsx.read_workbook(data)
    sheet = book.first
    rows, formulas = [], {}
    for r, row in enumerate(sheet.rows, start=1):
        values = []
        for c, cell in enumerate(row, start=1):
            values.append(cell.value)
            if getattr(cell, "formula", ""):
                formulas[(r, c)] = cell.formula
        rows.append(values)
    return rows[0] if rows else [], rows[1:], formulas


def _sheet_rebuild(header, body, formulas) -> bytes:
    from . import xlsx

    return xlsx.write_workbook([xlsx.SheetOut(
        name="Client data", rows=[header] + body, formulas=formulas,
        freeze_below_row=2)])


def _at(header, name: str) -> int | None:
    """The delivered position of a column, by name. ``None`` when it is absent.

    By name rather than by position, because the delivered order is the one the
    selection asked for: "the last column" was the computed one until the brief
    started reordering, and a defect builder that edits the wrong column either
    crashes or quietly tests nothing.
    """
    return header.index(name) if name in header else None


def _wrong_computed_value(case, rng):
    """One computed cell wrong, by a plausible amount."""
    from decimal import Decimal

    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    column = _at(header, "Amount")
    if not data or column is None:
        return None
    index = rng.randrange(len(data))
    current = data[index][column]
    if current is None:
        return None
    data[index][column] = Decimal(str(current)) + Decimal(
        str(rng.choice((1, -1, 10, -10, "0.01"))))
    return _sheet_rebuild(header, data + [body[-1]], formulas)


def _formula_wrong_column(case, rng):
    """A correct value beside a formula that names the wrong cells.

    The defect this capability writes formulas beside values in order to
    catch. The number is right, so recomputing the arithmetic passes it. Only
    parsing the formula back to the cells it names finds it, and a client finds
    it the moment they change an input and the total does not move.
    """
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    if not data:
        return None
    amount = _at(header, "Amount")
    quantity, rate = _at(header, "Quantity"), _at(header, "Rate")
    if amount is None or quantity is None or rate is None:
        return None
    index = rng.randrange(len(data))
    sheet_row = index + 2
    key = (sheet_row, amount + 1)
    if key not in formulas:
        return None
    wrong = _column_letter(rate + 1)
    right = _column_letter(quantity + 1)
    formulas[key] = formulas[key].replace(f"{right}{sheet_row}",
                                          f"{wrong}{sheet_row}", 1)
    return _sheet_rebuild(header, body, formulas)


def _sheet_row_loss(case, rng):
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    if len(data) < 2:
        return None
    index = rng.randrange(len(data))
    return _sheet_rebuild(header, data[:index] + data[index + 1:] + [body[-1]],
                          formulas)


def _sheet_total_wrong(case, rng):
    from decimal import Decimal

    header, body, formulas = _sheet_tables(case.good_output_bytes)
    column = _at(header, "Amount")
    if not body or column is None:
        return None
    total = list(body[-1])
    if total[column] is None:
        return None
    total[column] = Decimal(str(total[column])) * Decimal("0.9")
    return _sheet_rebuild(header, body[:-1] + [total], formulas)


def _sheet_total_digit_swap(case, rng):
    """1,250.50 becomes 1,250.05. The kind of wrong a reader skims past."""
    from decimal import Decimal, InvalidOperation

    header, body, formulas = _sheet_tables(case.good_output_bytes)
    column = _at(header, "Amount")
    if not body or column is None:
        return None
    total = list(body[-1])
    text = str(total[column] or "")
    if "." not in text or len(text.split(".")[1]) < 2:
        return None
    whole, fraction = text.split(".")
    swapped = f"{whole}.{fraction[1]}{fraction[0]}{fraction[2:]}"
    try:
        total[column] = Decimal(swapped)
    except InvalidOperation:
        return None
    if total[column] == Decimal(text):
        return None
    return _sheet_rebuild(header, body[:-1] + [total], formulas)


def _sheet_unauthorised_edit(case, rng):
    """A column nobody authorised changing, quietly tidied."""
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    column = _at(header, "Client ref")
    if not data or column is None:
        return None
    index = rng.randrange(len(data))
    data[index][column] = str(data[index][column]).replace("CR-", "REF-")
    return _sheet_rebuild(header, data + [body[-1]], formulas)


def _sheet_sort_broken(case, rng):
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    if len(data) < 2:
        return None
    first, second = rng.sample(range(len(data)), 2)
    data[first], data[second] = data[second], data[first]
    return _sheet_rebuild(header, data + [body[-1]], formulas)


def _sheet_blank_as_zero(case, rng):
    """A blank input filled with zero, which makes the total wrong by the gap."""
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    column = _at(header, "Rate")
    if not data or column is None:
        return None
    index = rng.randrange(len(data))
    data[index][column] = 0
    return _sheet_rebuild(header, data + [body[-1]], formulas)


def _sheet_extra_row(case, rng):
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    if not data:
        return None
    invented = list(rng.choice(data))
    invented[0] = _company(rng)
    return _sheet_rebuild(header, data + [invented] + [body[-1]], formulas)


def _sheet_column_dropped(case, rng):
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    column = _at(header, "Client ref")
    if column is None:
        return None
    trimmed = [h for i, h in enumerate(header) if i != column]
    rows = [[c for i, c in enumerate(row) if i != column] for row in body]
    return _sheet_rebuild(trimmed, rows, {})


def _sheet_whitespace_kept(case, rng):
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    if not data:
        return None
    index = rng.randrange(len(data))
    data[index][0] = f"  {data[index][0]} "
    return _sheet_rebuild(header, data + [body[-1]], formulas)


def _not_a_workbook(case, rng):
    """A deliverable that is not a workbook at all."""
    return rng.choice((
        b"Item,Units,Rate,Amount\nConsulting,1,2,2\n",
        b"\x00\x01\x02 not a zip",
        b"PK\x03\x04 truncated right after the signature",
    ))


def _required_column_missing(case, rng):
    """A column the brief requires, absent from the deliverable."""
    header, body, _ = _sheet_tables(case.good_output_bytes)
    column = _at(header, "Rate")
    if column is None:
        return None
    trimmed = [h for i, h in enumerate(header) if i != column]
    rows = [[c for i, c in enumerate(row) if i != column] for row in body]
    return _sheet_rebuild(trimmed, rows, {})


def _empty_row_kept(case, rng):
    """The stray row the brief said to drop, still in the deliverable."""
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    data = body[:-1]
    column = _at(header, "Client ref")
    if column is None:
        return None
    stray = [None] * len(header)
    stray[column] = _reference(rng)
    return _sheet_rebuild(header, data + [stray] + [body[-1]], formulas)


def _sheet_skipped(case, rng):
    """One named worksheet contributed nothing, and the counts hide it badly.

    A client who sent two quarters and received one of them. Reconciliation can
    pass this when the counts happen to work out, because every delivered row
    really is a source row -- which is why the consolidation check has to ask
    which worksheet each row came from.
    """
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    items = set(case.truth.get("second_sheet_items") or ())
    if not items:
        return None
    column = _at(header, "Item")
    if column is None:
        return None
    data = [row for row in body[:-1]
            if str(row[column] or "").strip() not in items]
    if len(data) == len(body) - 1:
        return None
    return _sheet_rebuild(header, data + [body[-1]], formulas)


def _header_not_renamed(case, rng):
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    if "Quantity" not in header:
        return None
    renamed = ["Units" if h == "Quantity" else h for h in header]
    return _sheet_rebuild(renamed, body, formulas)


def _columns_reordered(case, rng):
    """Every column delivered, in an order nobody asked for."""
    header, body, formulas = _sheet_tables(case.good_output_bytes)
    if len(header) < 3:
        return None
    order = [1, 0] + list(range(2, len(header)))
    return _sheet_rebuild([header[i] for i in order],
                          [[row[i] for i in order] for row in body], {})


SHEET_DEFECTS = (
    Defect("NOT_A_WORKBOOK", "opens_as_workbook", _not_a_workbook),
    Defect("EMPTY_ROW_KEPT", "drop_empty_rows", _empty_row_kept),
    Defect("HEADER_NOT_RENAMED", "rename_headers", _header_not_renamed),
    Defect("COLUMNS_REORDERED", "select_columns", _columns_reordered),
    Defect("SHEET_SKIPPED", "consolidate_sheets", _sheet_skipped,
           note="one named worksheet contributed nothing"),
    Defect("REQUIRED_COLUMN_MISSING", "require_columns",
           _required_column_missing),
    Defect("WRONG_COMPUTED_VALUE", "compute_column", _wrong_computed_value),
    Defect("FORMULA_WRONG_COLUMN", "formulas_agree", _formula_wrong_column,
           note="a right value beside a formula naming the wrong column"),
    Defect("ROW_LOSS", "row_reconciliation", _sheet_row_loss),
    Defect("EXTRA_ROW", "row_reconciliation", _sheet_extra_row),
    Defect("TOTAL_WRONG", "total_row", _sheet_total_wrong),
    Defect("TOTAL_DIGIT_SWAP", "total_row", _sheet_total_digit_swap,
           note="1250.50 becomes 1250.05"),
    Defect("UNAUTHORISED_EDIT", "no_unauthorised_changes",
           _sheet_unauthorised_edit),
    Defect("SORT_BROKEN", "sort_rows", _sheet_sort_broken),
    Defect("BLANK_AS_ZERO", "compute_column", _sheet_blank_as_zero),
    Defect("COLUMN_DROPPED", "preserve_columns", _sheet_column_dropped),
    Defect("WHITESPACE_KEPT", "trim_whitespace", _sheet_whitespace_kept),
)


# ------------------------------------------------------------- data-entry
#: Records crafted to break exactly one rule each, so the oracle knows the
#: rule set without reimplementing the whole evaluator. Each entry is
#: ``(source row values, the rules it breaks)``, and the rule sets are the
#: ones the capability's contract states: a figure that is not a number fails
#: its type *and* cannot be compared against its range, so it breaks two.
def _entry_bad_rows(rng) -> list[tuple[list, list[str]]]:
    return [
        ([f"A-{rng.randrange(1000, 9999)}", "", _amount(rng), "2026-04-10",
          "open"], ["require_field"]),
        ([f"A-{rng.randrange(1000, 9999)}", _company(rng), "not a number",
          "2026-04-11", "open"], ["validate_range", "validate_type"]),
        ([f"A-{rng.randrange(1000, 9999)}", _company(rng), "9000000.00",
          "2026-04-12", "open"], ["validate_range"]),
        ([f"A-{rng.randrange(1000, 9999)}", _company(rng), _amount(rng),
          "2026-04-13", "disputed"], ["validate_enum"]),
        ([f"A-{rng.randrange(1000, 9999)}", _company(rng), _amount(rng),
          "05/10/2026", "open"], ["validate_type"]),
        ([f"Z{rng.randrange(10, 99)}", _company(rng), _amount(rng),
          "2026-04-14", "open"], ["validate_pattern"]),
    ]


ENTRY_SOURCE_HEADER = ["Ref", "Client Name", "Invoiced", "Due", "Status"]
ENTRY_FIELDS = ["Reference", "Client", "Amount", "Due Date", "State", "Batch"]
ENTRY_EXCEPTION_HEADER = ["source_row", "rules", "detail", "record"]


def entry_case(rng: random.Random) -> Case:
    """A data-entry job whose accept/reject split is known before the source.

    Valid records are invented first and dirtied (padded whitespace,
    lower-case status). Invalid records are crafted to break a named rule set.
    The two delivered files are then built from that knowledge, so the split
    is true by construction rather than by asking the worker what it decided.
    """
    from . import types

    batch = f"2026-W{rng.randrange(1, 53):02d}"
    valid, supplied = [], []
    used = set()
    for _ in range(rng.randrange(3, 10)):
        while True:
            reference = f"A-{rng.randrange(1000, 9999)}"
            if reference not in used:
                used.add(reference)
                break
        client = _company(rng)
        amount = _amount(rng)
        due = f"2026-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}"
        state = rng.choice(("open", "paid"))
        valid.append([reference, client, amount, due, state])
        supplied.append([reference,
                         f"  {client}  " if rng.random() < 0.5 else client,
                         amount, due,
                         state.upper() if rng.random() < 0.3 else state])

    bad = _entry_bad_rows(rng)
    rows: list[tuple[list, list[str]]] = [(row, []) for row in supplied]
    rows += bad
    rng.shuffle(rows)
    duplicate_of = None
    if len(valid) >= 2 and rng.random() < 0.6:
        index = next((i for i, (_, broken) in enumerate(rows) if not broken), None)
        if index is not None:
            duplicate_of = list(rows[index][0])
            rows.append((list(duplicate_of), ["DUPLICATE"]))

    source_lines = [",".join(_quote(str(c)) for c in ENTRY_SOURCE_HEADER)]
    accepted, held = [], []
    for position, (row, broken) in enumerate(rows, start=2):
        source_lines.append(",".join(_quote(str(c)) for c in row))
        if broken == ["DUPLICATE"]:
            continue
        if broken:
            held.append([str(position), ";".join(sorted(broken)),
                         f"the record breaks {len(broken)} stated rule(s)",
                         json.dumps(dict(zip(ENTRY_SOURCE_HEADER,
                                             [str(c) for c in row])),
                                    sort_keys=True, ensure_ascii=False)])
            continue
        accepted.append([str(row[0]), " ".join(str(row[1]).split()),
                         str(row[2]), str(row[3]), str(row[4]).upper(), batch])

    params = {
        "parses_as_delivered": {},
        "map_fields": {"mapping": {
            "Reference": "Ref", "Client": "Client Name", "Amount": "Invoiced",
            "Due Date": "Due", "State": "Status"}},
        "constant_field": {"fields": {"Batch": batch}},
        "normalise_whitespace": {"fields": ["Client"]},
        "normalise_case": {"fields": {"State": "upper"}},
        "require_field": {"fields": ["Reference", "Client"]},
        "validate_type": {"types": {"Amount": "decimal", "Due Date": "date"}},
        "validate_range": {"ranges": {
            "Amount": {"minimum": "0", "maximum": "100000"}}},
        "validate_enum": {"allowed": {"State": ["OPEN", "PAID"]}},
        "validate_pattern": {"patterns": {"Reference": r"^A-\d{4}$"}},
        "deduplicate_by": {"fields": ["Reference"]},
        "select_fields": {"fields": list(ENTRY_FIELDS)},
        "records_reconcile": {},
        "no_invented_values": {},
        "exceptions_justified": {},
        "record_traceability": {},
    }
    requirements = tuple(
        types.Requirement(id=f"r{index}", text=check,
                          source=types.RequirementSource.OWNER_CONFIRMED,
                          check=check, params=dict(args))
        for index, (check, args) in enumerate(params.items(), start=1))
    return Case(
        source_name="client_records.csv",
        source_text="\n".join(source_lines) + "\n",
        good_output=_csv_text(ENTRY_FIELDS, accepted),
        output_name="delivered.csv",
        companions={"-exceptions.csv":
                    _csv_text(ENTRY_EXCEPTION_HEADER, held).encode("utf-8")},
        params=params, requirements=requirements,
        truth={"accepted": accepted, "held": held, "batch": batch,
               "duplicate_of": duplicate_of})


def _csv_text(header, rows) -> str:
    lines = [",".join(_quote(str(c)) for c in header)]
    lines += [",".join(_quote(str(c)) for c in row) for row in rows]
    return "\n".join(lines) + "\n"


def _entry_files(case):
    """``(fields, accepted_rows, exception_rows)`` of the correct delivery."""
    import csv as _csv
    import io as _io

    accepted = list(_csv.reader(_io.StringIO(case.good_output)))
    held = list(_csv.reader(_io.StringIO(
        case.companions["-exceptions.csv"].decode("utf-8"))))
    accepted = [r for r in accepted if any(str(c).strip() for c in r)]
    held = [r for r in held if any(str(c).strip() for c in r)]
    return accepted[0], [list(r) for r in accepted[1:]], [list(r) for r in held[1:]]


def _entry_pack(fields, accepted, held):
    return (_csv_text(fields, accepted),
            {"-exceptions.csv": _csv_text(ENTRY_EXCEPTION_HEADER,
                                          held).encode("utf-8")})


def _valid_record_held(case, rng):
    """A record that breaks no rule, held back with a plausible reason.

    The defect this capability's central check exists for. The deliverable is
    short, internally consistent, and passes every field-level check; the
    rejection reason reads perfectly well. Only the source says otherwise.
    """
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    index = rng.randrange(len(accepted))
    dropped = accepted.pop(index)
    held.append([str(900 + index), "validate_type",
                 "Amount could not be read", json.dumps({"Ref": dropped[0]})])
    return _entry_pack(fields, accepted, held)


def _wrong_rejection_reason(case, rng):
    fields, accepted, held = _entry_files(case)
    if not held:
        return None
    index = rng.randrange(len(held))
    current = held[index][1]
    replacement = "validate_range" if "validate_range" not in current \
        else "validate_enum"
    held[index][1] = replacement
    return _entry_pack(fields, accepted, held)


def _rejection_with_no_detail(case, rng):
    fields, accepted, held = _entry_files(case)
    if not held:
        return None
    held[rng.randrange(len(held))][2] = ""
    return _entry_pack(fields, accepted, held)


def _entry_record_lost(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    accepted.pop(rng.randrange(len(accepted)))
    return _entry_pack(fields, accepted, held)


def _invalid_delivered(rule: str, column: int, value):
    """A held record put into the deliverable, breaking one named rule."""
    def inject(case, rng):
        fields, accepted, held = _entry_files(case)
        row = [f"A-{rng.randrange(1000, 9999)}", _company(rng), _amount(rng),
               "2026-05-01", "OPEN", case.truth["batch"]]
        row[column] = value(rng) if callable(value) else value
        accepted.append(row)
        return _entry_pack(fields, accepted, held)
    inject.__name__ = f"_invalid_delivered_{rule}"
    return inject


def _entry_value_altered(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    index = rng.randrange(len(accepted))
    accepted[index][1] = _company(rng)
    return _entry_pack(fields, accepted, held)


def _entry_grafted(case, rng):
    """Two real values, two real columns, and no such record in the source."""
    fields, accepted, held = _entry_files(case)
    if len(accepted) < 2:
        return None
    first, second = rng.sample(range(len(accepted)), 2)
    accepted[first][1], accepted[second][1] = (accepted[second][1],
                                               accepted[first][1])
    return _entry_pack(fields, accepted, held)


def _entry_invented_value(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    accepted[rng.randrange(len(accepted))][1] = (
        _company(rng) + " " + rng.choice(("Holdings", "International")))
    return _entry_pack(fields, accepted, held)


def _entry_duplicate_left(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    accepted.append(list(accepted[rng.randrange(len(accepted))]))
    return _entry_pack(fields, accepted, held)


def _entry_constant_changed(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    accepted[rng.randrange(len(accepted))][5] = (
        f"2026-W{rng.randrange(1, 53):02d}x")
    return _entry_pack(fields, accepted, held)


def _entry_field_order(case, rng):
    fields, accepted, held = _entry_files(case)
    order = [1, 0] + list(range(2, len(fields)))
    return _entry_pack([fields[i] for i in order],
                       [[row[i] for i in order] for row in accepted], held)


def _entry_unauthorised_case(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    index = rng.randrange(len(accepted))
    accepted[index][1] = accepted[index][1].upper()
    return _entry_pack(fields, accepted, held)


def _entry_unauthorised_whitespace(case, rng):
    fields, accepted, held = _entry_files(case)
    if not accepted:
        return None
    index = rng.randrange(len(accepted))
    accepted[index][4] = accepted[index][4].lower()
    return _entry_pack(fields, accepted, held)


def _entry_ragged_row(case, rng):
    fields, accepted, held = _entry_files(case)
    text, companions = _entry_pack(fields, accepted, held)
    return text + "A-9999,Short Ltd\n", companions


ENTRY_DEFECTS = (
    Defect("VALID_RECORD_HELD", "exceptions_justified", _valid_record_held,
           note="a valid record held back, with a plausible reason"),
    Defect("WRONG_REJECTION_REASON", "exceptions_justified",
           _wrong_rejection_reason),
    Defect("NO_DETAIL", "exceptions_justified", _rejection_with_no_detail),
    Defect("RECORD_LOST", "records_reconcile", _entry_record_lost),
    Defect("VALUE_ALTERED", "map_fields", _entry_value_altered),
    Defect("GRAFTED_RECORD", "record_traceability", _entry_grafted,
           note="every value real, every column right, no such record"),
    Defect("INVENTED_VALUE", "no_invented_values", _entry_invented_value),
    Defect("DUPLICATE_LEFT", "deduplicate_by", _entry_duplicate_left),
    Defect("CONSTANT_CHANGED", "constant_field", _entry_constant_changed),
    Defect("FIELD_ORDER", "select_fields", _entry_field_order),
    Defect("UNAUTHORISED_CASE", "normalise_case", _entry_unauthorised_case),
    Defect("UNAUTHORISED_WHITESPACE", "normalise_whitespace",
           _entry_unauthorised_whitespace),
    Defect("RAGGED_ROW", "parses_as_delivered", _entry_ragged_row),
    Defect("EMPTY_REQUIRED_DELIVERED", "require_field",
           _invalid_delivered("require_field", 1, "")),
    Defect("BAD_TYPE_DELIVERED", "validate_type",
           _invalid_delivered("validate_type", 3, "05/10/2026")),
    Defect("OUT_OF_RANGE_DELIVERED", "validate_range",
           _invalid_delivered("validate_range", 2, "9000000.00")),
    Defect("NOT_ALLOWED_DELIVERED", "validate_enum",
           _invalid_delivered("validate_enum", 4, "DISPUTED")),
    Defect("PATTERN_BROKEN_DELIVERED", "validate_pattern",
           _invalid_delivered("validate_pattern", 0,
                              lambda rng: f"Z{rng.randrange(10, 99)}")),
)


# ------------------------------------------------------- document-processing
DOCUMENT_LABELS = {
    "Invoice Number": ["Invoice Number", "Invoice No"],
    "Client": ["Client", "Client Name"],
    "Amount Due": ["Amount Due", "Total Due"],
    "Purchase Order": ["Purchase Order", "PO Number"],
}


def document_case(rng: random.Random, index: int = 0) -> Case:
    """One document job. Three shapes, cycled, because the capability has three.

    An extraction, a conversion and a filled template are different
    deliverables with different ways of being wrong, and a battery that only
    ever produced one of them would certify a third of the capability and say
    nothing about the rest. The shape cycles on the case index rather than on a
    draw, so every run covers each one the same number of times.
    """
    shape = ("extraction", "conversion", "template")[index % 3]
    # One case in four states every field; the rest leave one out, because the
    # defects worth certifying here are about a field the document does *not*
    # state and they need a case that has one.
    complete = (index // 3) % 4 == 0
    if shape == "conversion":
        return _document_conversion_case(rng, complete=complete)
    if shape == "template":
        return _document_template_case(rng, complete=complete)
    return _document_extraction_case(rng, complete=complete)


def _document_source(rng, *, supplies_purchase_order=None):
    """``(bytes, values, label_for, rows, blocks)`` of an invented invoice.

    The field values are invented first and the document is written around
    them, so the correct extraction is known before the document exists.
    """
    from . import docx

    reference = f"{rng.choice('ABCDE')}-{rng.randrange(1000, 9999)}"
    client = _company(rng)
    amount = _amount(rng)
    # Whether the document states a purchase order is decided by the caller
    # where it matters, not by a draw. The absence defects can only be built
    # from a case that *has* an absence, and leaving that to chance left two
    # defect classes below the five-instance floor on some seeds -- a
    # certification that varies with the seed is not a certification.
    if supplies_purchase_order is None:
        supplies_purchase_order = rng.random() < 0.35
    purchase_order = (f"PO-{rng.randrange(10000, 99999)}"
                      if supplies_purchase_order else None)
    label_for = {name: rng.choice(options)
                 for name, options in DOCUMENT_LABELS.items()}

    rows = [("Item", "Amount")]
    for item in rng.sample(_ITEMS, rng.randrange(2, 6)):
        rows.append((item, _amount(rng)))

    blocks = [docx.Out(text="Invoice", style="Title"),
              docx.Out(text="Summary", style="Heading1"),
              docx.Out(text=f"{label_for['Invoice Number']}: {reference}"),
              docx.Out(text=f"{label_for['Client']}: {client}"),
              docx.Out(text=f"{label_for['Amount Due']}: {amount}")]
    if purchase_order:
        blocks.append(docx.Out(
            text=f"{label_for['Purchase Order']}: {purchase_order}"))
    blocks += [docx.Out(text="Lines", style="Heading2"),
               docx.Out(kind="table", rows=tuple(rows)),
               docx.Out(text=f"Payable within {rng.choice((14, 30, 60))} days.")]
    values = {"Invoice Number": reference, "Client": client,
              "Amount Due": amount,
              "Purchase Order": purchase_order or _NOT_FOUND}
    return docx.write_document(blocks), values, label_for, rows, blocks


def _document_requirements(brief: dict) -> tuple:
    from . import types

    return tuple(
        types.Requirement(id=f"r{index}", text=check,
                          source=types.RequirementSource.OWNER_CONFIRMED,
                          check=check, params=dict(args))
        for index, (check, args) in enumerate(brief.items(), start=1))


def _document_extraction_case(rng: random.Random, *, complete: bool = False) -> Case:
    """Labelled values and a table, delivered as a structured extraction."""
    source, values, label_for, rows, _ = _document_source(
        rng, supplies_purchase_order=complete)

    fields = []
    for name in DOCUMENT_LABELS:
        found = values[name] != _NOT_FOUND
        fields.append({
            "field": name, "value": values[name],
            "evidence": f"{label_for[name]}: {values[name]}" if found else "",
            "label": label_for[name] if found else "", "found": found})
    extraction = {
        "capability": "document-processing/1.0",
        "source_format": "a Word document",
        "fields": fields,
        "tables": [[list(row) for row in rows]],
        "not_stated": [f["field"] for f in fields if not f["found"]],
        "source_carries_unaccepted_revisions": False,
        "not_carried_through": [],
    }

    brief = {
        "document_opens": {},
        "extract_fields": {"labels": {k: list(v)
                                      for k, v in DOCUMENT_LABELS.items()}},
        "extract_table": {"columns": ["Item", "Amount"]},
        # Two fields every one of these documents states, so a correct job
        # satisfies the requirement and a defect is a delivered absence rather
        # than a document that happened not to say something.
        "require_fields": {"fields": ["Invoice Number", "Client"]},
        "fields_traceable": {},
        "absences_disclosed": {},
        "no_invented_content": {},
        "table_preserved": {},
        "unsupported_declared": {},
    }
    return Case(
        source_name="client_invoice.docx", source_bytes=source,
        good_output=json.dumps(extraction, indent=2, ensure_ascii=False) + "\n",
        output_name="extracted.json", params=brief,
        requirements=_document_requirements(brief),
        truth={"shape": "extraction", "values": values, "rows": rows,
               "labels": label_for})


def _document_conversion_case(rng: random.Random, *, complete: bool = False) -> Case:
    """The same document, carried into Markdown with its structure intact."""
    source, values, label_for, rows, blocks = _document_source(
        rng, supplies_purchase_order=complete)

    lines = []
    for block in blocks:
        if block.kind == "table":
            width = max(len(row) for row in block.rows)
            lines.append("| " + " | ".join(block.rows[0]) + " |")
            lines.append("|" + "|".join("---" for _ in range(width)) + "|")
            for row in block.rows[1:]:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
            continue
        level = {"Title": 0, "Heading1": 1, "Heading2": 2}.get(block.style)
        lines.append(f"{'#' * (level + 1)} {block.text}" if level is not None
                     else block.text)
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()

    brief = {
        "document_opens": {},
        "convert_format": {"to": "md"},
        "conversion_fidelity": {},
        "structure_headings": {},
        "headings_applied": {},
        "table_preserved": {},
        "no_invented_content": {},
        "unsupported_declared": {},
    }
    return Case(
        source_name="client_invoice.docx", source_bytes=source,
        good_output="\n".join(lines) + "\n", output_name="converted.md",
        params=brief, requirements=_document_requirements(brief),
        truth={"shape": "conversion", "values": values, "rows": rows,
               "paragraphs": [b.text for b in blocks
                              if b.kind != "table" and b.text]})


def _document_template_case(rng: random.Random, *, complete: bool = False) -> Case:
    """A client's template, filled only from values the document states."""
    source, values, label_for, rows, _ = _document_source(
        rng, supplies_purchase_order=complete)

    template = [
        {"style": "Title", "text": "Statement of Account"},
        {"style": "Heading1", "text": "Details"},
        {"text": "Invoice: {Invoice Number}"},
        {"text": "Client: {Client}"},
        {"text": "Due: {Amount Due}"},
        {"text": "Purchase order: {Purchase Order}"},
    ]
    lines = []
    for block in template:
        text = block["text"]
        for name, value in values.items():
            text = text.replace("{" + name + "}", str(value))
        level = {"Title": 0, "Heading1": 1}.get(block.get("style"))
        lines.append(f"{'#' * (level + 1)} {text}" if level is not None else text)
        lines.append("")
    width = max(len(row) for row in rows)
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("|" + "|".join("---" for _ in range(width)) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    brief = {
        "document_opens": {},
        "extract_fields": {"labels": {k: list(v)
                                      for k, v in DOCUMENT_LABELS.items()}},
        "extract_table": {"columns": ["Item", "Amount"]},
        "apply_template": {"blocks": [dict(b) for b in template]
                           + [{"kind": "table", "rows": "extracted"}]},
        "fields_traceable": {},
        "absences_disclosed": {},
        "no_invented_content": {},
        "table_preserved": {},
        "unsupported_declared": {},
    }
    return Case(
        source_name="client_invoice.docx", source_bytes=source,
        good_output="\n".join(lines) + "\n", output_name="statement.md",
        params=brief, requirements=_document_requirements(brief),
        truth={"shape": "template", "values": values, "rows": rows,
               "template": template})


#: Repeated rather than imported, so a worker that changed the marker would be
#: caught by the battery rather than agreed with by it.
_NOT_FOUND = "NOT FOUND IN SOURCE"
_AMBIGUOUS = "STATED MORE THAN ONCE WITH DIFFERENT VALUES"


def _extraction_of(case) -> dict | None:
    """The delivered extraction, or None when this case is not one.

    The capability has three deliverable shapes and only one of them is JSON.
    A defect builder that ran against the wrong shape would either raise or
    quietly produce a trial that tests nothing, and a trial that tests nothing
    still counts towards an evidence floor.
    """
    if case.truth.get("shape") != "extraction":
        return None
    return json.loads(case.good_output)


def _extraction_text(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _doc_field(payload, name):
    return next((f for f in payload["fields"] if f["field"] == name), None)


def _doc_gap_filled(case, rng):
    """A field the document never states, delivered as something plausible."""
    payload = _extraction_of(case)
    if payload is None:
        return None
    entry = _doc_field(payload, "Purchase Order")
    if entry is None or entry["found"]:
        return None
    invented = f"PO-{rng.randrange(10000, 99999)}"
    entry.update(value=invented, found=True,
                 evidence=f"Purchase Order: {invented}")
    payload["not_stated"] = []
    return _extraction_text(payload)


def _doc_absence_dropped(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    absent = [f["field"] for f in payload["fields"] if not f["found"]]
    if not absent:
        return None
    payload["fields"] = [f for f in payload["fields"] if f["found"]]
    payload["not_stated"] = []
    return _extraction_text(payload)


def _doc_value_altered(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    found = [f for f in payload["fields"] if f["found"]]
    if not found:
        return None
    entry = rng.choice(found)
    entry["value"] = str(entry["value"]) + rng.choice(("0", "1", " Ltd", "x"))
    return _extraction_text(payload)


def _doc_evidence_missing(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    found = [f for f in payload["fields"] if f["found"]]
    if not found:
        return None
    rng.choice(found)["evidence"] = ""
    return _extraction_text(payload)


def _doc_evidence_fabricated(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    found = [f for f in payload["fields"] if f["found"]]
    if not found:
        return None
    entry = rng.choice(found)
    entry["evidence"] = f"{entry['evidence']} (confirmed by telephone)"
    return _extraction_text(payload)


def _doc_field_dropped(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    if len(payload["fields"]) < 2:
        return None
    index = rng.randrange(len(payload["fields"]))
    dropped = payload["fields"].pop(index)
    payload["not_stated"] = [f for f in payload["not_stated"]
                             if f != dropped["field"]]
    return _extraction_text(payload)


def _doc_table_row_dropped(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    if not payload["tables"] or len(payload["tables"][0]) < 3:
        return None
    payload["tables"][0] = payload["tables"][0][:-1]
    return _extraction_text(payload)


def _doc_table_cell_changed(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    if not payload["tables"] or len(payload["tables"][0]) < 2:
        return None
    row = rng.randrange(1, len(payload["tables"][0]))
    payload["tables"][0][row][1] = _amount(rng)
    return _extraction_text(payload)


def _doc_table_dropped(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    if not payload["tables"]:
        return None
    payload["tables"] = []
    return _extraction_text(payload)


def _doc_not_an_extraction(case, rng):
    """A deliverable that is JSON and is not an extraction of anything."""
    if case.truth.get("shape") != "extraction":
        return None
    return json.dumps({"summary": "extraction completed successfully",
                       "confidence": round(rng.uniform(0.9, 0.99), 2)},
                      indent=2) + "\n"


def _doc_unreadable(case, rng):
    if case.truth.get("shape") != "extraction":
        return None
    return "not json at all, just a sentence about the invoice\n"


def _doc_required_absent(case, rng):
    """A field the document plainly states, delivered as not stated."""
    payload = _extraction_of(case)
    if payload is None:
        return None
    entry = _doc_field(payload, rng.choice(["Invoice Number", "Client"]))
    if entry is None or not entry["found"]:
        return None
    entry.update(value=_NOT_FOUND, found=True, evidence="")
    payload["not_stated"] = sorted(set(payload["not_stated"])
                                   | {entry["field"]})
    return _extraction_text(payload)


# ------------------------------------------------- conversion and template
def _document_lines(case) -> list[str] | None:
    """The delivered document as lines, for a shape whose deliverable is text."""
    if case.truth.get("shape") not in ("conversion", "template"):
        return None
    return case.good_output.splitlines()


def _rebuilt(lines) -> str:
    return "\n".join(lines) + "\n"


def _body_line(lines, rng):
    """A delivered line that is ordinary text, or None if there is none."""
    candidates = [i for i, line in enumerate(lines)
                  if line.strip() and not line.startswith(("#", "|"))]
    return rng.choice(candidates) if candidates else None


def _doc_paragraph_lost(case, rng):
    lines = _document_lines(case)
    if lines is None:
        return None
    index = _body_line(lines, rng)
    if index is None:
        return None
    return _rebuilt(lines[:index] + lines[index + 1:])


def _doc_paragraph_added(case, rng):
    """A sentence nobody wrote, in a document that otherwise checks out."""
    lines = _document_lines(case)
    if lines is None:
        return None
    return _rebuilt(lines + ["", f"Interest accrues at "
                                 f"{rng.randrange(2, 9)}% per month."])


def _doc_paragraphs_reordered(case, rng):
    """Every line present, and the document no longer says the same thing."""
    lines = _document_lines(case)
    if lines is None:
        return None
    body = [i for i, line in enumerate(lines)
            if line.strip() and not line.startswith(("#", "|"))]
    if len(body) < 2:
        return None
    first, second = body[0], body[-1]
    out = list(lines)
    out[first], out[second] = out[second], out[first]
    if out == lines:
        return None
    return _rebuilt(out)


def _doc_heading_flattened(case, rng):
    lines = _document_lines(case)
    if lines is None:
        return None
    headings = [i for i, line in enumerate(lines) if line.startswith("#")]
    if not headings:
        return None
    index = rng.choice(headings)
    out = list(lines)
    out[index] = out[index].lstrip("# ")
    return _rebuilt(out)


def _doc_heading_level_wrong(case, rng):
    lines = _document_lines(case)
    if lines is None:
        return None
    headings = [i for i, line in enumerate(lines)
                if line.startswith("##")]
    if not headings:
        return None
    index = rng.choice(headings)
    out = list(lines)
    out[index] = out[index][1:]
    return _rebuilt(out)


def _doc_table_gone(case, rng):
    lines = _document_lines(case)
    if lines is None:
        return None
    out = [line for line in lines if not line.startswith("|")]
    if out == lines:
        return None
    return _rebuilt(out)


def _doc_cell_changed(case, rng):
    lines = _document_lines(case)
    if lines is None:
        return None
    rows = [i for i, line in enumerate(lines)
            if line.startswith("|") and "---" not in line]
    if len(rows) < 2:
        return None
    index = rows[-1]
    cells = [c.strip() for c in lines[index].strip("|").split("|")]
    cells[-1] = _amount(rng)
    out = list(lines)
    out[index] = "| " + " | ".join(cells) + " |"
    if out == lines:
        return None
    return _rebuilt(out)


def _doc_not_converted(case, rng):
    """A file with the right extension that was never converted at all."""
    if case.truth.get("shape") not in ("conversion", "template"):
        return None
    return case.source_bytes


def _doc_template_line_missing(case, rng):
    """A line the client's template states, absent from the deliverable."""
    if case.truth.get("shape") != "template":
        return None
    lines = case.good_output.splitlines()
    index = next((i for i, line in enumerate(lines)
                  if line.startswith(("Invoice:", "Client:", "Due:"))), None)
    if index is None:
        return None
    return _rebuilt(lines[:index] + lines[index + 1:])


def _doc_template_gap_filled(case, rng):
    """A placeholder with no value, filled with something plausible."""
    if case.truth.get("shape") != "template":
        return None
    if _NOT_FOUND not in case.good_output:
        return None
    return case.good_output.replace(
        _NOT_FOUND, f"PO-{rng.randrange(10000, 99999)}")


def _doc_table_row_invented(case, rng):
    payload = _extraction_of(case)
    if payload is None:
        return None
    if not payload["tables"]:
        return None
    payload["tables"][0].append([rng.choice(_ITEMS), _amount(rng)])
    return _extraction_text(payload)


def _doc_source_unsupported(case, rng):
    """The deliverable is correct and the source is a format this must refuse.

    A client sends a PDF; the capability refuses it by name. The defect is a
    deliverable existing anyway -- which is what happens when a refusal is
    recorded in a log and not honoured in the filesystem.
    """
    return ("client_invoice.pdf",
            b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
            + f"% {_company(rng)}\n".encode("utf-8"))


DOCUMENT_DEFECTS = (
    Defect("SOURCE_UNSUPPORTED", "unsupported_declared",
           _doc_source_unsupported, kind="source",
           note="a deliverable written from a source that must be refused"),
    Defect("GAP_FILLED", "absences_disclosed", _doc_gap_filled,
           note="a field the document never states, filled plausibly"),
    Defect("ABSENCE_DROPPED", "absences_disclosed", _doc_absence_dropped),
    Defect("VALUE_ALTERED", "extract_fields", _doc_value_altered),
    Defect("FIELD_DROPPED", "extract_fields", _doc_field_dropped),
    Defect("EVIDENCE_MISSING", "fields_traceable", _doc_evidence_missing),
    Defect("EVIDENCE_FABRICATED", "fields_traceable", _doc_evidence_fabricated,
           note="a citation to a line the document does not contain"),
    Defect("TABLE_ROW_DROPPED", "extract_table", _doc_table_row_dropped),
    Defect("TABLE_CELL_CHANGED", "table_preserved", _doc_table_cell_changed),
    Defect("TABLE_DROPPED", "table_preserved", _doc_table_dropped),
    Defect("TABLE_ROW_INVENTED", "no_invented_content", _doc_table_row_invented),
    Defect("NOT_AN_EXTRACTION", "document_opens", _doc_not_an_extraction,
           note="valid JSON that is not an extraction of anything"),
    Defect("UNREADABLE", "document_opens", _doc_unreadable),
    Defect("REQUIRED_FIELD_ABSENT", "require_fields", _doc_required_absent,
           note="a field the document states, delivered as not stated"),
    Defect("PARAGRAPH_LOST", "conversion_fidelity", _doc_paragraph_lost),
    Defect("PARAGRAPH_ADDED", "conversion_fidelity", _doc_paragraph_added,
           note="a sentence nobody wrote"),
    Defect("PARAGRAPHS_REORDERED", "conversion_fidelity",
           _doc_paragraphs_reordered,
           note="every line present, in an order that changes the meaning"),
    Defect("HEADING_FLATTENED", "headings_applied", _doc_heading_flattened),
    Defect("HEADING_LEVEL_WRONG", "structure_headings",
           _doc_heading_level_wrong),
    Defect("TABLE_GONE", "table_preserved", _doc_table_gone),
    Defect("CELL_CHANGED", "table_preserved", _doc_cell_changed),
    Defect("NOT_CONVERTED", "convert_format", _doc_not_converted,
           note="the right extension on a file that was never converted"),
    Defect("TEMPLATE_LINE_MISSING", "apply_template",
           _doc_template_line_missing),
    Defect("TEMPLATE_GAP_FILLED", "apply_template", _doc_template_gap_filled,
           note="a placeholder with no value, filled plausibly"),
)


GENERATORS.update({
    "spreadsheet-automation": sheet_case,
    "data-entry": entry_case,
    "document-processing": document_case,
})
DEFECTS.update({
    "spreadsheet-automation": SHEET_DEFECTS,
    "data-entry": ENTRY_DEFECTS,
    "document-processing": DOCUMENT_DEFECTS,
})
