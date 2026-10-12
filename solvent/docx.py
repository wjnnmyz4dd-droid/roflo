"""Reading and writing the part of ``.docx`` DeskPilot actually needs.

**This is a format library, not an authority.** It converts between a Word
document and a list of blocks and decides nothing: not whether a file is
acceptable, not whether an extraction is trustworthy, not whether work may be
delivered. Nothing here imports anything that does.

**Why this exists rather than python-docx.** Solvent imports only the standard
library, and the Windows installer builds a virtual environment with no
packages in it. A ``.docx`` is a ZIP of XML, the same as the ``.xlsx`` handled
by :mod:`solvent.xlsx`, so ``zipfile`` and ``xml.etree.ElementTree`` reach the
format without a dependency, an install-time download, or a new supply chain.

**What it supports, and what it refuses.**

Supported on read
    paragraph text with its outline level (``Heading 1``..``Heading 6``,
    ``Title``, ordinary body text, list items), tables as rows of cell text,
    and the document's declared order of all of those.

Supported on write
    paragraphs with a style, headings, and simple tables with a header row.

Refused on read
    a file that is not a ZIP, a ZIP with no ``word/document.xml``, anything
    carrying a macro part, anything declaring an external relationship target
    the reader would have to fetch, and a document larger than a stated block
    limit. Each raises :class:`UnsupportedDocument` naming the reason. A
    document DeskPilot cannot read correctly must not be read approximately:
    an approximate read of a client's contract produces a confident wrong
    answer, which is worse than a refusal in every case that matters.

Handled rather than ignored, because each one silently corrupts an extraction
    *Tracked deletions.* Text inside ``w:delText`` was deleted by a reviewer
    and is not in the document any more. A reader that collects every ``w:t``
    and ``w:delText`` alike returns sentences the author removed, which is how
    an extraction reports a price that was struck out.

    *Tracked insertions.* Text inside ``w:ins`` is present and is collected,
    and the document is flagged as carrying unaccepted revisions so a caller
    can say so rather than quietly picking one version of a disputed sentence.

    *Field codes.* ``w:instrText`` holds the instruction (``PAGE``,
    ``MERGEFIELD Name``), not content. It is excluded from text and recorded,
    because a document whose values come from fields has values this reader
    cannot see.

    *Comments and footnotes.* Separate parts, not body text. They are not
    merged into the paragraph stream; a reviewer's note is not what the
    document says.

Not implemented at all
    images, charts, equations, text boxes, headers and footers, footnote
    bodies, bookmarks, hyperlink targets, numbering values (a list item's text
    is read; the number Word would render is not), and every kind of
    formatting other than the paragraph style name. A document using them can
    still be read for its text; those features are not carried through, and
    :func:`read_document` records what it dropped so a caller can say so.

**The shared-parser risk, stated plainly.** A worker and its verifier both read
through this module, exactly as both read CSV through the standard library's
``csv``. A bug *here* fools both sides, which is the one way the two-sided
design can fail. That is why this module carries its own adversarial suite and
its own cross-validation against another library, rather than being covered
incidentally through the capability that uses it.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

#: The OPC namespaces, fixed by the format.
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_DOCUMENT_PART = "word/document.xml"

#: Parts whose presence means this file does something this reader does not model.
_MACRO_PARTS = ("word/vbaProject.bin", "word/vbaData.xml")

#: Relationship types whose target the reader would have to fetch to be correct.
#: A document whose text comes from a subdocument is not a document this module
#: can read; saying so beats returning the part of it that happens to be local.
_EXTERNAL_RELS = ("subDocument", "frame")

#: Heading styles, mapped to an outline level. ``0`` is the title.
_HEADING_LEVELS = {"Title": 0, "Subtitle": 0,
                   **{f"Heading{n}": n for n in range(1, 10)},
                   **{f"Heading {n}": n for n in range(1, 10)}}

#: Runs of spaces collapse; a tab does not. A tab is how a Word document
#: separates a label from its value ("Invoice<tab>A-1001"), so folding it into
#: a space loses the one piece of structure a field extractor can rely on.
_WS = re.compile(r" {2,}")


class UnsupportedDocument(Exception):
    """This document cannot be read correctly, and will not be read anyway."""


@dataclass(frozen=True, slots=True)
class Paragraph:
    """One paragraph: its text, its style, and its heading level if it has one."""

    text: str
    style: str = ""
    level: int | None = None

    @property
    def is_heading(self) -> bool:
        return self.level is not None


@dataclass(frozen=True, slots=True)
class Table:
    """One table as rows of cell text. The first row is not assumed to be a header.

    Whether row one is a header is a judgement about the document, and this
    module makes none. A capability that needs one says so and checks.
    """

    rows: tuple[tuple[str, ...], ...] = ()

    @property
    def header(self) -> tuple[str, ...]:
        return self.rows[0] if self.rows else ()

    @property
    def body(self) -> tuple[tuple[str, ...], ...]:
        return self.rows[1:]


@dataclass(slots=True)
class Document:
    """A document as the ordered blocks this module can read.

    ``blocks`` holds :class:`Paragraph` and :class:`Table` in document order,
    because order is content: "Total: 400" under the heading "Previous
    quarter" means something different two headings later.
    """

    blocks: list[object] = field(default_factory=list)
    #: What this read did not carry through, in words a caller can repeat.
    dropped: list[str] = field(default_factory=list)
    #: True when the document carries revisions nobody has accepted or
    #: rejected. The text read is the *current* text; this says the document
    #: has more than one version of itself in it.
    has_revisions: bool = False

    @property
    def paragraphs(self) -> list[Paragraph]:
        return [b for b in self.blocks if isinstance(b, Paragraph)]

    @property
    def tables(self) -> list[Table]:
        return [b for b in self.blocks if isinstance(b, Table)]

    @property
    def text(self) -> str:
        """The paragraph text, one paragraph per line. Tables are not text."""
        return "\n".join(p.text for p in self.paragraphs)

    def lines(self) -> list[str]:
        """Every line this document states: paragraphs and table cells.

        A field extractor needs both. "Invoice number: A-1001" is as likely to
        be a two-cell table row as a paragraph, and a reader that only looked
        at paragraphs would report the field absent from a document that
        plainly states it.
        """
        out = []
        for block in self.blocks:
            if isinstance(block, Paragraph):
                if block.text:
                    out.append(block.text)
            else:
                for row in block.rows:
                    out += [cell for cell in row if cell]
                    if len(row) >= 2 and row[0] and row[1]:
                        out.append(f"{row[0]}: {row[1]}")
        return out


def read_document(data: bytes, *, max_blocks: int = 50_000) -> Document:
    """Read a ``.docx`` from bytes, or refuse it by name.

    ``max_blocks`` bounds the work. A document past the limit is refused rather
    than truncated: half a contract read as a whole one is the failure this
    module exists to avoid.
    """
    try:
        archive = zipfile.ZipFile(_BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsupportedDocument(
            f"this is not a .docx file: it is not a ZIP archive ({exc})") from exc

    with archive:
        names = set(archive.namelist())
        present = sorted(set(_MACRO_PARTS) & names)
        if present:
            raise UnsupportedDocument(
                f"the document contains a macro part ({', '.join(present)}). "
                "DeskPilot does not open macro-enabled documents")
        if _DOCUMENT_PART not in names:
            raise UnsupportedDocument(
                f"the archive has no {_DOCUMENT_PART}, so it is not a Word "
                "document this reader recognises")
        external = _external_targets(archive, names)
        if external:
            raise UnsupportedDocument(
                "the document's text comes partly from somewhere else "
                f"({', '.join(external)}), and this reader will not fetch it")
        root = _parse(archive, _DOCUMENT_PART)
        return _read_body(root, max_blocks=max_blocks)


class _BytesIO:
    """The minimum file-like wrapper ``zipfile`` needs. No io import, no copy."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._at = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self._at, 2: len(self._data)}[whence]
        self._at = max(0, min(len(self._data), base + offset))
        return self._at

    def tell(self) -> int:
        return self._at

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size is None or size < 0 else self._at + size
        chunk = self._data[self._at:end]
        self._at += len(chunk)
        return chunk

    def seekable(self) -> bool:
        return True


def _external_targets(archive, names) -> list[str]:
    """Relationship types that would make this document partly somewhere else."""
    found = []
    for name in sorted(n for n in names if n.endswith(".rels")):
        try:
            root = _parse(archive, name)
        except UnsupportedDocument:
            continue
        for rel in root.findall(f"{{{_PKG_REL}}}Relationship"):
            kind = (rel.get("Type") or "").rsplit("/", 1)[-1]
            if kind in _EXTERNAL_RELS:
                found.append(kind)
    return sorted(set(found))


def _parse(archive, part: str):
    try:
        raw = archive.read(part)
    except KeyError as exc:
        raise UnsupportedDocument(f"the archive has no {part}") from exc
    try:
        # resolve_entities is off by default in ElementTree, so an entity
        # expansion cannot reach the filesystem through this parser. Stated
        # because the input is a client's file and this is the parser boundary.
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise UnsupportedDocument(f"{part} is not well-formed XML: {exc}") from exc


def _read_body(root, *, max_blocks: int) -> Document:
    document = Document()
    body = root.find(f"{{{_W}}}body")
    if body is None:
        raise UnsupportedDocument(
            "the document part has no body element, so there is nothing in it "
            "this reader could be reading")
    dropped: set[str] = set()
    for element in list(body):
        tag = element.tag.split("}", 1)[-1]
        if len(document.blocks) >= max_blocks:
            raise UnsupportedDocument(
                f"the document has more than {max_blocks} blocks")
        if tag == "p":
            document.blocks.append(_paragraph(element, document, dropped))
        elif tag == "tbl":
            document.blocks.append(_table(element, document, dropped))
        elif tag == "sdt":
            # A content control wrapping real content. Its text is read; the
            # control itself -- its binding to a data source, its placeholder
            # behaviour -- is not modelled, and a value Word would substitute
            # from a binding is not a value this reader can see.
            dropped.add("structured document tags (content controls): their "
                        "text is read, their data bindings are not")
            content = element.find(f"{{{_W}}}sdtContent")
            for child in list(content if content is not None else []):
                child_tag = child.tag.split("}", 1)[-1]
                if child_tag == "p":
                    document.blocks.append(_paragraph(child, document, dropped))
                elif child_tag == "tbl":
                    document.blocks.append(_table(child, document, dropped))
        elif tag in ("sectPr", "bookmarkStart", "bookmarkEnd", "proofErr"):
            continue
        else:
            dropped.add(f"a {tag} element, which this reader does not model")
    document.dropped = sorted(dropped)
    return document


def _paragraph(element, document: Document, dropped: set) -> Paragraph:
    style = _style_of(element)
    text = _runs_text(element, document, dropped)
    level = _HEADING_LEVELS.get(style)
    if level is None and style.replace(" ", "") in _HEADING_LEVELS:
        level = _HEADING_LEVELS[style.replace(" ", "")]
    return Paragraph(text=text, style=style, level=level)


def _style_of(element) -> str:
    properties = element.find(f"{{{_W}}}pPr")
    if properties is None:
        return ""
    style = properties.find(f"{{{_W}}}pStyle")
    return (style.get(f"{{{_W}}}val") or "") if style is not None else ""


def _runs_text(element, document: Document, dropped: set) -> str:
    """The text this paragraph *currently says*.

    Deletions are excluded because they are not in the document. Insertions are
    included because they are. Field instructions are excluded because they are
    instructions. Each exclusion is recorded, so a caller can report that the
    document holds more than what was read rather than discovering it later.
    """
    pieces: list[str] = []
    for node in element.iter():
        tag = node.tag.split("}", 1)[-1]
        if tag == "t":
            if _inside(element, node, "delText"):
                continue
            pieces.append(node.text or "")
        elif tag == "delText":
            dropped.add("text deleted by a tracked change, which the document "
                        "no longer says")
            document.has_revisions = True
        elif tag == "instrText":
            dropped.add("field instructions (such as MERGEFIELD or PAGE): the "
                        "value Word would substitute is not in this file")
        elif tag == "ins":
            document.has_revisions = True
        elif tag == "br":
            pieces.append(" ")
        elif tag == "tab":
            pieces.append("\t")
        elif tag in ("drawing", "pict", "object"):
            dropped.add("an embedded image or object, whose content is not text")
        elif tag == "footnoteReference":
            dropped.add("footnote bodies, which live in a separate part")
        elif tag == "commentReference":
            dropped.add("comments, which are a reviewer's notes rather than "
                        "what the document says")
    return _WS.sub(" ", "".join(pieces)).strip()


def _inside(root, node, tag: str) -> bool:
    """Is ``node`` within an element of this tag? ElementTree has no parents."""
    target = f"{{{_W}}}{tag}"
    for candidate in root.iter(target):
        if any(child is node for child in candidate.iter()):
            return True
    return False


def _table(element, document: Document, dropped: set) -> Table:
    rows = []
    for row in element.findall(f"{{{_W}}}tr"):
        cells = []
        for cell in row.findall(f"{{{_W}}}tc"):
            parts = [_runs_text(p, document, dropped)
                     for p in cell.findall(f"{{{_W}}}p")]
            cells.append(" ".join(part for part in parts if part).strip())
            span = cell.find(f"{{{_W}}}tcPr/{{{_W}}}gridSpan")
            if span is not None:
                dropped.add("merged table cells: a spanned cell is read once, "
                            "in the first column it covers")
        rows.append(tuple(cells))
    return Table(rows=tuple(rows))


# ------------------------------------------------------------------- writing
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.\
relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>\
<Override PartName="/word/document.xml" ContentType="application/vnd.\
openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\
<Override PartName="/word/styles.xml" ContentType="application/vnd.\
openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/\
relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/\
officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/\
relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/\
officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""

#: Styles this writer defines. A document that referred to a style it did not
#: define would open with a complaint in a conforming reader, and a file that
#: opens with a complaint is not something to hand a client.
_STYLES = ("Title", "Heading1", "Heading2", "Heading3", "Heading4", "Heading5",
           "Heading6", "Normal", "ListParagraph")

#: A fixed timestamp for every ZIP entry, so the same content produces the same
#: bytes. A deliverable whose digest changes when nothing changed cannot be the
#: thing a verifier checked.
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class Out:
    """One block to write: a paragraph with a style, or a table."""

    kind: str = "paragraph"          # "paragraph" | "table"
    text: str = ""
    style: str = "Normal"
    rows: tuple[tuple[str, ...], ...] = ()


def write_document(blocks: list[Out]) -> bytes:
    """Build a minimal, valid ``.docx`` from these blocks. Deterministic."""
    unknown = sorted({b.style for b in blocks
                      if b.kind == "paragraph" and b.style not in _STYLES})
    if unknown:
        raise UnsupportedDocument(
            f"this writer defines no style named {', '.join(unknown)}; it "
            f"defines {', '.join(_STYLES)}")
    archive_bytes = _BytesIO(b"")
    buffer = bytearray()

    class _Sink:
        def write(self, data):
            buffer.extend(data)
            return len(data)

        def flush(self):
            return None

        def tell(self):
            return len(buffer)

        def seekable(self):
            return False

    with zipfile.ZipFile(_Sink(), "w", zipfile.ZIP_DEFLATED) as out:
        _put(out, "[Content_Types].xml", _CONTENT_TYPES)
        _put(out, "_rels/.rels", _ROOT_RELS)
        _put(out, "word/_rels/document.xml.rels", _DOC_RELS)
        _put(out, "word/styles.xml", _styles_xml())
        _put(out, _DOCUMENT_PART, _document_xml(blocks))
    del archive_bytes
    return bytes(buffer)


def _put(archive, name: str, text: str) -> None:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, text.encode("utf-8"))


def _styles_xml() -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             f'<w:styles xmlns:w="{_W}">',
             '<w:docDefaults><w:rPrDefault><w:rPr>'
             '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
             '<w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>']
    for name in _STYLES:
        default = ' w:default="1"' if name == "Normal" else ""
        label = name if name in ("Title", "Normal") else _spaced(name)
        outline = ""
        if name.startswith("Heading"):
            level = int(name[-1])
            size = {1: 32, 2: 26, 3: 24}.get(level, 22)
            outline = (f'<w:pPr><w:outlineLvl w:val="{level - 1}"/></w:pPr>'
                       f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr>')
        elif name == "Title":
            outline = '<w:rPr><w:b/><w:sz w:val="40"/></w:rPr>'
        parts.append(
            f'<w:style w:type="paragraph"{default} w:styleId="{name}">'
            f'<w:name w:val="{_attr(label)}"/>{outline}</w:style>')
    parts.append("</w:styles>")
    return "".join(parts)


def _spaced(name: str) -> str:
    """``Heading1`` is the style id; ``heading 1`` is the name Word shows."""
    if name.startswith("Heading"):
        return f"heading {name[len('Heading'):]}"
    if name == "ListParagraph":
        return "List Paragraph"
    return name


def _document_xml(blocks: list[Out]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             f'<w:document xmlns:w="{_W}"><w:body>']
    for block in blocks:
        if block.kind == "table":
            parts.append(_table_xml(block))
        else:
            parts.append(_paragraph_xml(block.text, block.style))
    parts.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>')
    parts.append("</w:body></w:document>")
    return "".join(parts)


def _paragraph_xml(text: str, style: str) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
    if not text:
        return f"<w:p>{properties}</w:p>"
    runs = []
    for index, line in enumerate(str(text).split("\n")):
        if index:
            runs.append("<w:r><w:br/></w:r>")
        runs.append('<w:r><w:t xml:space="preserve">'
                    f"{_text(line)}</w:t></w:r>")
    return f"<w:p>{properties}{''.join(runs)}</w:p>"


def _table_xml(block: Out) -> str:
    width = max((len(row) for row in block.rows), default=0)
    grid = "".join('<w:gridCol w:w="2000"/>' for _ in range(width))
    rows = []
    for row in block.rows:
        cells = []
        for index in range(width):
            value = row[index] if index < len(row) else ""
            cells.append(
                '<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>'
                f'{_paragraph_xml(value, "Normal")}</w:tc>')
        rows.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblBorders>'
            '<w:top w:val="single" w:sz="4" w:color="auto"/>'
            '<w:left w:val="single" w:sz="4" w:color="auto"/>'
            '<w:bottom w:val="single" w:sz="4" w:color="auto"/>'
            '<w:right w:val="single" w:sz="4" w:color="auto"/>'
            '<w:insideH w:val="single" w:sz="4" w:color="auto"/>'
            '<w:insideV w:val="single" w:sz="4" w:color="auto"/>'
            '</w:tblBorders></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')


def _attr(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _text(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
