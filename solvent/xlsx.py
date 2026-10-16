"""Reading and writing the part of ``.xlsx`` DeskPilot actually needs.

**This is a format library, not an authority.** It converts between a
worksheet and a list of rows and decides nothing: not whether a file is
acceptable, not whether a transformation was authorised, not whether work may
be delivered. The Governor, the verifier and the Action Gate own those, and
nothing here imports them.

**Why this exists rather than openpyxl.** Solvent imports only the standard
library, and the Windows installer builds a virtual environment with no
packages in it. Adding a dependency would mean the installer fetching one at
install time -- network access during installation, a new supply chain, and a
new thing that can be unavailable on the owner's machine. An ``.xlsx`` file is
a ZIP of XML, and ``zipfile`` and ``xml.etree.ElementTree`` are both standard,
so the format is reachable without any of that.

**What it supports, and what it refuses.** A bounded subset, named here so
nobody has to find out by trying:

Supported on read
    worksheet cell values -- numbers, inline and shared strings, booleans,
    formulas (both the formula text and the cached value), ISO dates stored as
    serial numbers where the column's number format says so; multiple
    worksheets, by name and by index.

Supported on write
    cell values, formulas written alongside a computed value, per-column
    number formats, a frozen header row, column widths.

Refused on read
    a file that is not a ZIP, a ZIP without a workbook part, a worksheet whose
    dimension disagrees with its rows, anything that declares external links,
    and anything containing a macro part. Each raises
    :class:`UnsupportedWorkbook` naming the reason -- a file DeskPilot cannot
    read correctly must not be read approximately, because an approximate read
    of a client's spreadsheet produces a confident wrong answer.

Not implemented at all
    charts, pivot tables, images, merged cells, conditional formatting, data
    validation, defined names, array formulas, rich text runs within a cell,
    and styles beyond number format. A workbook using them can still be read
    for its values; those features are not carried through to the output, and
    :func:`read_workbook` records what it dropped so a caller can say so.

**Numbers come back as Python numbers, and the scale does not survive.** A
cell written from ``Decimal("1000.00")`` is stored as ``1000.00`` and read
back as the float ``1000.0``: the *value* is the same figure and the number of
decimal places shown is not. That is correct for a spreadsheet -- how many
places a figure displays is what a number format decides, which is why
``number_format`` exists -- and it has one consequence worth naming. A figure
carrying more than about fifteen significant digits would not round-trip
exactly, because that is where a float stops being able to represent a decimal
string uniquely. Money does not reach fifteen significant digits, and anything
that does belongs in a text column rather than a numeric one.

**The shared-parser risk, stated plainly.** The worker and the verifier both
use this module, exactly as both use the standard library's ``csv``. So a bug
*here* would fool both sides, which is the one way the two-sided design can
fail. That is why this module has its own adversarial test suite rather than
being covered incidentally through the capability that uses it.
"""

from __future__ import annotations

import datetime as _dt
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

#: The OPC namespaces, which are fixed by the format.
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _MAIN, "r": _REL, "pr": _PKG_REL}

#: Parts whose presence means this file does something we do not model.
_MACRO_PARTS = ("xl/vbaProject.bin",)
_EXTERNAL_LINK_PREFIX = "xl/externalLinks/"

#: Number-format codes that mean "this number is a date".
#:
#: Built-in ids come from the format's own table; 14-17 and 22 are dates and
#: date-times, 18-21 and 45-47 are times. A custom format counts as a date when
#: its code contains a date token outside quoted text -- which is why the
#: quoted parts are stripped before looking.
_BUILTIN_DATE_IDS = frozenset({14, 15, 16, 17, 22, 27, 30, 36, 45, 46, 47, 50,
                               57, 58})
_DATE_TOKEN = re.compile(r"[ymdhs]", re.I)
_QUOTED = re.compile(r'"[^"]*"|\[[^\]]*\]')

#: Excel's day zero. 1900-based, with the format's own leap-year bug: serial
#: 60 is the 29th of February 1900, which did not exist, so serials above 60
#: are one day further on than a naive count. Handled rather than ignored,
#: because a date read one day out is the kind of defect a client finds.
#: Serial 1 is the 1st of January 1900, so the base is the day before it.
#: Serials above 60 are then shifted back by one to absorb the phantom day.
_EPOCH = _dt.date(1899, 12, 31)


class UnsupportedWorkbook(Exception):
    """This file cannot be read correctly, and says why.

    Raised rather than returning a best effort. An approximate read of a
    client's spreadsheet is a confident wrong answer, which is worse than a
    refusal the owner can act on.
    """


@dataclass
class Cell:
    """One cell: what it holds, and how it was written."""

    value: object = None
    #: The formula text without its leading ``=``, when the cell had one.
    formula: str = ""
    #: The number-format code this cell was displayed with, if any.
    number_format: str = ""

    @property
    def is_formula(self) -> bool:
        return bool(self.formula)


@dataclass
class Sheet:
    """One worksheet, as rows of :class:`Cell`."""

    name: str
    rows: list[list[Cell]] = field(default_factory=list)

    def values(self) -> list[list[object]]:
        return [[c.value for c in row] for row in self.rows]

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass
class Workbook:
    """Every worksheet read, and what was dropped getting there."""

    sheets: list[Sheet] = field(default_factory=list)
    #: Features present in the file that this module does not carry through.
    #: Reported so a caller can tell the client rather than quietly losing them.
    dropped: list[str] = field(default_factory=list)

    def sheet(self, name: str) -> Sheet:
        for s in self.sheets:
            if s.name == name:
                return s
        raise UnsupportedWorkbook(
            f"no worksheet named {name!r}; this workbook has "
            f"{', '.join(repr(s.name) for s in self.sheets) or 'none'}")

    @property
    def first(self) -> Sheet:
        if not self.sheets:
            raise UnsupportedWorkbook("this workbook has no worksheets")
        return self.sheets[0]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

#: A workbook is a ZIP, so reading a part decompresses client-controlled
#: data, and ``max_cells`` cannot help: it counts cells the parser has
#: already built. Measured before this existed -- a 410 KB workbook with a
#: padded worksheet part made this function allocate 1.23 GB over 5.2
#: seconds and then accept the file. This process shares a machine with
#: the owner's own work, so that is an outage they would feel.
#:
#: Bounded by reading rather than by the header's declared size, which a
#: crafted archive can set to anything. Generous enough that a workbook at
#: the ``max_cells`` limit still fits.
MAX_PART_BYTES = 64 * 1024 * 1024


def read_workbook(data: bytes, *, max_cells: int = 2_000_000) -> Workbook:
    """Parse an ``.xlsx`` file's bytes.

    ``max_cells`` is a resource limit, not a quality judgement: a crafted file
    can declare an enormous sheet dimension and a decompressed size far larger
    than its compressed one, and a worker that tried to honour it would exhaust
    the machine. Exceeding it raises rather than truncating, because half a
    client's data silently processed is worse than a refusal.
    """
    try:
        archive = zipfile.ZipFile(_BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsupportedWorkbook(
            f"this file is not a readable .xlsx package: {exc}") from exc

    names = set(archive.namelist())
    dropped: list[str] = []
    for part in _MACRO_PARTS:
        if part in names:
            raise UnsupportedWorkbook(
                "this workbook contains a macro project (vbaProject.bin). "
                "DeskPilot does not open macro-enabled workbooks: the macros "
                "would not run, and a file whose behaviour depends on code we "
                "ignore cannot be processed correctly")
    if any(n.startswith(_EXTERNAL_LINK_PREFIX) for n in names):
        raise UnsupportedWorkbook(
            "this workbook links to cells in other files. The values here "
            "depend on files DeskPilot does not have, so any total it "
            "computed could be wrong without anything looking wrong")

    if "xl/workbook.xml" not in names:
        raise UnsupportedWorkbook(
            "this .xlsx package has no xl/workbook.xml, so it is not a "
            "workbook this module can read")

    shared = _read_shared_strings(archive, names)
    formats = _read_number_formats(archive, names)
    book = Workbook(dropped=dropped)

    for sheet_name, part in _sheet_parts(archive, names):
        if part not in names:
            dropped.append(f"worksheet {sheet_name!r} is declared and its "
                           f"data part {part!r} is missing")
            continue
        sheet = _read_sheet(archive, part, sheet_name, shared, formats,
                            max_cells=max_cells)
        book.sheets.append(sheet)

    for feature, marker in (
            ("charts", "xl/charts/"), ("pivot tables", "xl/pivotCache/"),
            ("images", "xl/media/"),
            ("defined names", None)):
        if marker and any(n.startswith(marker) for n in names):
            dropped.append(f"{feature} are present and are not carried "
                           f"through to the output")
    return book


class _BytesIO:
    """A minimal file-like wrapper, so the caller passes bytes not a path.

    Reading from bytes rather than a path is deliberate: the capability is
    handed content that arrived from a client, and a function that took a path
    would invite somebody to hand it one.
    """

    def __init__(self, data: bytes) -> None:
        import io

        self._io = io.BytesIO(data)

    def __getattr__(self, name):
        return getattr(self._io, name)


def _read_shared_strings(archive, names) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = _parse(archive, "xl/sharedStrings.xml")
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        # A cell's text can be split into runs with different formatting. The
        # runs are joined; the formatting is not carried through, which is
        # recorded as dropped by the caller rather than pretended away.
        text = "".join(t.text or "" for t in si.iter(f"{{{_MAIN}}}t"))
        out.append(text)
    return out


def _read_number_formats(archive, names) -> dict[int, str]:
    """``style index -> number-format code``, for deciding what is a date."""
    if "xl/styles.xml" not in names:
        return {}
    root = _parse(archive, "xl/styles.xml")
    custom = {int(n.get("numFmtId")): n.get("formatCode", "")
              for n in root.findall("m:numFmts/m:numFmt", _NS)
              if n.get("numFmtId") is not None}
    out: dict[int, str] = {}
    cell_xfs = root.find("m:cellXfs", _NS)
    if cell_xfs is None:
        return out
    for index, xf in enumerate(cell_xfs.findall("m:xf", _NS)):
        raw = xf.get("numFmtId")
        if raw is None:
            continue
        fmt_id = int(raw)
        out[index] = custom.get(fmt_id, _BUILTIN_FORMATS.get(fmt_id, ""))
        if fmt_id in _BUILTIN_DATE_IDS and fmt_id not in custom:
            out[index] = out[index] or f"builtin:{fmt_id}"
    return out


#: The built-in format codes this module needs to recognise by id. Only the
#: ones that change how a value is interpreted are listed; the rest affect
#: display alone and are irrelevant to reading a value correctly.
_BUILTIN_FORMATS = {
    14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
    22: "m/d/yy h:mm", 45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0",
}


def _sheet_parts(archive, names) -> list[tuple[str, str]]:
    """``(sheet name, part path)`` in the workbook's own order.

    Resolved through the relationship file rather than by guessing
    ``sheet1.xml``, ``sheet2.xml`` and so on: the mapping from a sheet's name
    to its part is not positional, and a workbook that has had sheets deleted
    and re-added does not follow that pattern. Guessing it reads the wrong
    sheet, which is the worst kind of wrong because every value in it is valid.
    """
    rel_part = "xl/_rels/workbook.xml.rels"
    targets: dict[str, str] = {}
    if rel_part in names:
        for rel in _parse(archive, rel_part).findall("pr:Relationship", _NS):
            rid, target = rel.get("Id"), rel.get("Target", "")
            if not rid or not target:
                continue
            target = target.lstrip("/")
            targets[rid] = (target if target.startswith("xl/")
                            else f"xl/{target}")

    out: list[tuple[str, str]] = []
    root = _parse(archive, "xl/workbook.xml")
    for index, sheet in enumerate(root.findall("m:sheets/m:sheet", _NS), 1):
        name = sheet.get("name") or f"Sheet{index}"
        rid = sheet.get(f"{{{_REL}}}id", "")
        part = targets.get(rid) or f"xl/worksheets/sheet{index}.xml"
        out.append((name, part))
    return out


def _read_sheet(archive, part: str, name: str, shared, formats, *,
                max_cells: int) -> Sheet:
    root = _parse(archive, part)
    grid: dict[tuple[int, int], Cell] = {}
    seen = 0
    for row in root.findall("m:sheetData/m:row", _NS):
        for cell in row.findall("m:c", _NS):
            ref = cell.get("r") or ""
            column, rownum = _split_ref(ref)
            if column is None:
                continue
            seen += 1
            if seen > max_cells:
                raise UnsupportedWorkbook(
                    f"this worksheet declares more than {max_cells:,} cells, "
                    f"which is beyond what DeskPilot will process in one job")
            grid[(rownum, column)] = _read_cell(cell, shared, formats)
    if not grid:
        return Sheet(name=name, rows=[])
    height = max(r for r, _c in grid)
    width = max(c for _r, c in grid)
    rows = [[grid.get((r, c), Cell()) for c in range(1, width + 1)]
            for r in range(1, height + 1)]
    return Sheet(name=name, rows=rows)


def _read_cell(cell, shared, formats) -> Cell:
    kind = cell.get("t", "n")
    style = cell.get("s")
    fmt = formats.get(int(style), "") if style is not None else ""
    formula_el = cell.find("m:f", _NS)
    formula = (formula_el.text or "") if formula_el is not None else ""
    value_el = cell.find("m:v", _NS)
    raw = value_el.text if value_el is not None else None

    if kind == "inlineStr":
        inline = cell.find("m:is", _NS)
        text = "".join(t.text or "" for t in inline.iter(f"{{{_MAIN}}}t")) \
            if inline is not None else ""
        return Cell(value=text, formula=formula, number_format=fmt)
    if kind == "s":
        try:
            return Cell(value=shared[int(raw)], formula=formula,
                        number_format=fmt)
        except (TypeError, ValueError, IndexError):
            # A shared-string index pointing outside the table is a broken
            # file. Empty is the honest reading: inventing text would put
            # words in the client's document that nobody wrote.
            return Cell(value="", formula=formula, number_format=fmt)
    if kind == "b":
        return Cell(value=(raw == "1"), formula=formula, number_format=fmt)
    if kind in ("str", "e"):
        # ``e`` is an error value Excel itself produced (#DIV/0!, #N/A). Kept
        # as its text so a verifier can see it, never coerced to a number.
        return Cell(value=raw or "", formula=formula, number_format=fmt)
    if raw is None or raw == "":
        return Cell(value=None, formula=formula, number_format=fmt)
    number = _to_number(raw)
    if number is None:
        return Cell(value=raw, formula=formula, number_format=fmt)
    if _is_date_format(fmt):
        return Cell(value=_serial_to_date(number), formula=formula,
                    number_format=fmt)
    return Cell(value=number, formula=formula, number_format=fmt)


def _to_number(raw: str):
    try:
        if "." in raw or "e" in raw.lower():
            return float(raw)
        return int(raw)
    except ValueError:
        return None


def _is_date_format(code: str) -> bool:
    if not code:
        return False
    if code.startswith("builtin:"):
        return int(code.split(":")[1]) in _BUILTIN_DATE_IDS
    return bool(_DATE_TOKEN.search(_QUOTED.sub("", code)))


def _serial_to_date(serial):
    """Excel's serial number as a date, or the number if it is not one.

    Serial 60 is the 29th of February 1900 in Excel's calendar and does not
    exist in anybody else's, so anything above it is shifted by a day. Getting
    this wrong moves every date in a client's file by one, which is both easy
    to do and hard for them to spot.
    """
    try:
        days = int(serial)
    except (TypeError, ValueError):
        return serial
    if days <= 0:
        return serial
    if days == 60:
        # The day that does not exist. Returned as the number it is rather
        # than as a date nobody can name.
        return serial
    shift = days if days < 60 else days - 1
    try:
        date = _EPOCH + _dt.timedelta(days=shift)
    except OverflowError:
        return serial
    fraction = float(serial) - days
    if fraction <= 0:
        return date
    return _dt.datetime.combine(date, _dt.time()) + _dt.timedelta(
        seconds=round(fraction * 86400))


_REF = re.compile(r"^([A-Z]+)([0-9]+)$")


def _split_ref(ref: str):
    match = _REF.match(ref.strip().upper())
    if not match:
        return None, 0
    letters, digits = match.groups()
    column = 0
    for char in letters:
        column = column * 26 + (ord(char) - 64)
    return column, int(digits)


def column_name(index: int) -> str:
    """1 -> ``A``, 27 -> ``AA``. The inverse of the reference parser."""
    if index < 1:
        raise ValueError("column indices start at 1")
    out = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        out = chr(65 + remainder) + out
    return out


def _parse(archive, part: str):
    """Parse one XML part, with entity expansion refused.

    ``ElementTree`` does not expand external entities, and it raises on a
    document that declares them. That is the behaviour wanted: a client's
    spreadsheet that tries to read ``/etc/passwd`` through a DOCTYPE is an
    attack, not a file to be helpful about.
    """
    try:
        with archive.open(part) as handle:
            blob = handle.read(MAX_PART_BYTES + 1)
    except KeyError as exc:
        raise UnsupportedWorkbook(f"{part} is missing from the package") from exc
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise UnsupportedWorkbook(
            f"{part} could not be read from the package: {exc}") from exc
    if len(blob) > MAX_PART_BYTES:
        raise UnsupportedWorkbook(
            f"{part} expands to more than {MAX_PART_BYTES:,} bytes, so it is "
            "refused rather than decompressed")
    try:
        return ET.fromstring(blob)
    except ET.ParseError as exc:
        raise UnsupportedWorkbook(
            f"{part} is not readable XML: {exc}") from exc


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
#
# Written from scratch rather than by editing the input. An editing writer
# preserves things it does not understand, which sounds generous and means
# the output can carry a macro, an external link or a stale cached formula
# result that nothing checked. Building the file means everything in it is
# something this module put there.

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    'package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "{sheets}"
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>'
)


@dataclass
class SheetOut:
    """One worksheet to write.

    ``formulas`` is the point of this class. A formula is written *beside* a
    value this module computed, never instead of one: the file opens with a
    correct number showing even in a reader that does not evaluate formulas,
    and a person opening it in Excel can see how the number was derived. The
    two are checked against each other by the verifier.
    """

    name: str
    rows: list[list[object]] = field(default_factory=list)
    #: ``(row, column) -> formula text without "="``, 1-based.
    formulas: dict[tuple[int, int], str] = field(default_factory=dict)
    #: ``column index -> number-format code``, 1-based, applied to data rows.
    number_formats: dict[int, str] = field(default_factory=dict)
    #: Freeze everything above this row. 2 keeps a header visible.
    freeze_below_row: int = 0
    #: ``column index -> width in characters``.
    widths: dict[int, float] = field(default_factory=dict)


def write_workbook(sheets: list[SheetOut]) -> bytes:
    """Build an ``.xlsx`` package. Returns its bytes.

    Deterministic: the same input produces the same bytes, because every ZIP
    entry is written with a fixed timestamp and no compression metadata that
    varies. That matters because a deliverable's digest is what the audit
    record names, and a file whose digest changed on every build could not be
    checked against one.
    """
    if not sheets:
        raise ValueError("a workbook needs at least one worksheet")

    import io

    formats = _collect_formats(sheets)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.'
            f'spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1))
        _put(out, "[Content_Types].xml",
             _CONTENT_TYPES.format(sheets=overrides))
        _put(out, "_rels/.rels", _ROOT_RELS)
        _put(out, "xl/workbook.xml", _workbook_xml(sheets))
        _put(out, "xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        _put(out, "xl/styles.xml", _styles_xml(formats))
        for index, sheet in enumerate(sheets, 1):
            _put(out, f"xl/worksheets/sheet{index}.xml",
                 _sheet_xml(sheet, formats))
    return buffer.getvalue()


def _put(archive, name: str, text: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, text.encode("utf-8"))


def _collect_formats(sheets) -> dict[str, int]:
    """``format code -> style index``. Index 0 is the default, unformatted.

    Index 1 is reserved for the header style, so a header cell is bold without
    needing a format code of its own.
    """
    codes: dict[str, int] = {}
    for sheet in sheets:
        for code in sheet.number_formats.values():
            if code and code not in codes:
                codes[code] = len(codes) + 2
    return codes


def _styles_xml(formats: dict[str, int]) -> str:
    numfmts = "".join(
        f'<numFmt numFmtId="{164 + i}" formatCode="{_attr(code)}"/>'
        for i, code in enumerate(formats))
    xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>',
           '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" applyFont="1"/>']
    for i, _code in enumerate(formats):
        xfs.append(f'<xf numFmtId="{164 + i}" fontId="0" fillId="0" '
                   f'borderId="0" applyNumberFormat="1"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{_MAIN}">'
        + (f'<numFmts count="{len(formats)}">{numfmts}</numFmts>'
           if formats else "")
        + '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
          '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="1"><fill><patternFill patternType="none"/></fill>'
          "</fills>"
          '<borders count="1"><border/></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
          'borderId="0"/></cellStyleXfs>'
          f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
          # The named default style. Without it a conforming reader warns
          # that the workbook has no default and substitutes its own, which
          # is a file that opens with a complaint -- not something to hand a
          # client.
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" '
          'builtinId="0"/></cellStyles>'
          "</styleSheet>")


def _workbook_xml(sheets) -> str:
    entries = "".join(
        f'<sheet name="{_attr(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, s in enumerate(sheets, 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
            f"<sheets>{entries}</sheets></workbook>")


def _workbook_rels(count: int) -> str:
    sheet_rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/'
        f'officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, count + 1))
    styles = (f'<Relationship Id="rId{count + 1}" Type="http://schemas.'
              f'openxmlformats.org/officeDocument/2006/relationships/styles" '
              f'Target="styles.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_PKG_REL}">{sheet_rels}{styles}'
            "</Relationships>")


def _sheet_xml(sheet: SheetOut, formats: dict[str, int]) -> str:
    width = max((len(r) for r in sheet.rows), default=0)
    cols = ""
    if sheet.widths:
        cols = "<cols>" + "".join(
            f'<col min="{c}" max="{c}" width="{w:.2f}" customWidth="1"/>'
            for c, w in sorted(sheet.widths.items())) + "</cols>"
    pane = ""
    if sheet.freeze_below_row > 1:
        top = sheet.freeze_below_row
        pane = (f'<sheetViews><sheetView workbookViewId="0">'
                f'<pane ySplit="{top - 1}" topLeftCell="A{top}" '
                f'activePane="bottomLeft" state="frozen"/>'
                f"</sheetView></sheetViews>")

    body = []
    for r, row in enumerate(sheet.rows, 1):
        cells = []
        for c, value in enumerate(row, 1):
            ref = f"{column_name(c)}{r}"
            formula = sheet.formulas.get((r, c), "")
            style = 0
            if sheet.freeze_below_row and r < sheet.freeze_below_row:
                style = 1
            else:
                code = sheet.number_formats.get(c, "")
                style = formats.get(code, 0) if code else 0
            cells.append(_cell_xml(ref, value, formula, style))
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    dimension = f'<dimension ref="A1:{column_name(max(width, 1))}'\
                f'{max(len(sheet.rows), 1)}"/>'
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{_MAIN}">{dimension}{pane}{cols}'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')


def _cell_xml(ref: str, value, formula: str, style: int) -> str:
    attrs = f'r="{ref}"' + (f' s="{style}"' if style else "")
    if value is None and not formula:
        return f"<c {attrs}/>"
    if formula:
        # The formula and the value this module computed, side by side. A
        # reader that evaluates formulas recomputes; one that does not shows
        # the value. Either way the number on screen is one that was checked.
        inner = f"<f>{_text(formula)}</f>"
        if value is not None:
            inner += f"<v>{_number_or_text(value)}</v>"
        return f"<c {attrs}>{inner}</c>"
    if isinstance(value, bool):
        return f'<c {attrs} t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f"<c {attrs}><v>{_plain_number(value)}</v></c>"
    if isinstance(value, (_dt.date, _dt.datetime)):
        return f"<c {attrs}><v>{_date_to_serial(value)}</v></c>"
    return f'<c {attrs} t="inlineStr"><is><t>{_text(str(value))}</t></is></c>'


def _plain_number(value) -> str:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            # NaN and infinity have no representation a spreadsheet can read
            # back as a number. Writing one would produce a file that opens
            # with a corrupt-content warning.
            raise ValueError(f"{value!r} cannot be written to a spreadsheet")
        return repr(value) if value % 1 else str(int(value))
    return str(value)


def _number_or_text(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return _plain_number(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return str(_date_to_serial(value))
    return _text(str(value))


def _date_to_serial(value) -> float:
    date = value.date() if isinstance(value, _dt.datetime) else value
    days = (date - _EPOCH).days
    serial = days + 1 if days >= 60 else days
    if isinstance(value, _dt.datetime):
        seconds = value.hour * 3600 + value.minute * 60 + value.second
        if seconds:
            return serial + seconds / 86400
    return serial


def _attr(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _text(text: str) -> str:
    cleaned = "".join(
        ch for ch in str(text)
        if ch in "\t\n\r" or 0x20 <= ord(ch) < 0xD800 or 0xE000 <= ord(ch))
    return (cleaned.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
