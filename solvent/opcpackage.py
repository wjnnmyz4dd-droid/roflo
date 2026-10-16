"""Is this file a package Word or Excel will actually open?

**Verifier-side only.** Nothing that produces a deliverable may import
this module, and a test enforces that. Its whole value is being a second
opinion about a file :mod:`solvent.xlsx` and :mod:`solvent.docx` wrote,
and a second opinion that shares the first one's code is one opinion.

Why it exists. ``sheetverify`` reported ``opens_as_workbook`` as a PASS
on the evidence "read back through solvent.xlsx", and that is not
evidence that the file opens -- it is evidence that our reader reads our
writer. Measured: both readers accept a package with
``[Content_Types].xml`` removed, with ``_rels/.rels`` removed, and with
the main part's relationship file removed. All three are mandatory in
the Open Packaging Conventions, and Excel and Word refuse a package
missing any of them. So the one check whose job was "the client can open
this" would have passed three files the client cannot open.

What is checked here is the packaging, not the content: that the archive
is intact, that the parts the specification requires are present and are
well-formed XML, that the root relationships name a main document part,
that the part they name exists, and that its content type is declared.
A file can pass all of that and still hold the wrong numbers -- that is
what every other check is for. This one answers the narrower question
nothing else was answering.

Built on ``zipfile`` and ``xml.etree``, both of which are already here.
"""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile

CONTENT_TYPES = "[Content_Types].xml"
ROOT_RELS = "_rels/.rels"

_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_OFFICE_DOCUMENT = f"{_R_NS}/officeDocument"


def _resolve(rels_part: str, target: str) -> str:
    """A relationship target as a part name inside the package.

    Relative targets in ``<dir>/_rels/<name>.rels`` are relative to
    ``<dir>`` -- the folder holding the part the relationships belong to --
    and not to the ``_rels`` folder they are stored in. Resolving against
    ``_rels`` instead turns ``xl/workbook.xml`` into
    ``_rels/xl/workbook.xml``, which is in no package, and the check then
    rejects every valid file it is shown.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    owner_directory = posixpath.dirname(posixpath.dirname(rels_part))
    return posixpath.normpath(posixpath.join(owner_directory, target))


def problem(data: bytes) -> str:
    """Why this is not an openable package, or ``""`` when it is."""
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        return f"the file is not a readable archive: {exc}"

    try:
        damaged = archive.testzip()
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        return f"the archive could not be checked: {exc}"
    if damaged:
        return f"the archive is damaged: {damaged} fails its checksum"

    names = set(archive.namelist())

    for required in (CONTENT_TYPES, ROOT_RELS):
        if required not in names:
            return (f"the package has no {required}, which the Open "
                    "Packaging Conventions require; Word and Excel refuse a "
                    "file without it")

    # Every XML part must parse. A part that does not is a file that opens
    # as far as the ZIP and then fails in the application.
    parsed: dict[str, ET.Element] = {}
    for name in sorted(names):
        if not (name.endswith(".xml") or name.endswith(".rels")):
            continue
        try:
            parsed[name] = ET.fromstring(archive.read(name))
        except (ET.ParseError, KeyError, OSError) as exc:
            return f"{name} is not well-formed XML: {exc}"

    root_rels = parsed[ROOT_RELS]
    main = ""
    for relationship in root_rels.findall(f"{{{_RELS_NS}}}Relationship"):
        if relationship.get("Type") == _OFFICE_DOCUMENT:
            main = _resolve(ROOT_RELS, relationship.get("Target", ""))
            break
    if not main:
        return ("the package's root relationships name no main document "
                "part, so an application has nothing to open")
    if main not in names:
        return (f"the root relationships point at {main}, which is not in "
                "the package")

    types = parsed[CONTENT_TYPES]
    declared = {
        override.get("PartName", "").lstrip("/")
        for override in types.findall(f"{{{_TYPES_NS}}}Override")}
    defaults = {
        default.get("Extension", "").lower()
        for default in types.findall(f"{{{_TYPES_NS}}}Default")}
    if main not in declared and main.rsplit(".", 1)[-1].lower() not in defaults:
        return (f"{main} has no content type declared in {CONTENT_TYPES}, so "
                "an application cannot tell what the part is")

    # A workbook names its worksheets, and a document names its styles, by
    # relationship id rather than by filename. Those ids are resolved through
    # the part's own relationships file, so a package missing that file holds
    # a main part pointing at nothing -- which is the case both of our own
    # readers accepted, because neither of them resolves ids at all.
    main_rels = posixpath.join(posixpath.dirname(main), "_rels",
                               posixpath.basename(main) + ".rels")
    referenced = {
        value for element in parsed[main].iter()
        for key, value in element.attrib.items()
        if key == f"{{{_R_NS}}}id"}
    if referenced and main_rels not in parsed:
        return (f"{main} refers to {len(referenced)} part(s) by relationship "
                f"id but the package has no {main_rels}, so nothing can "
                "resolve them")

    defined = {}
    for relationship in parsed.get(main_rels,
                                   ET.Element("none")).findall(
                                       f"{{{_RELS_NS}}}Relationship"):
        defined[relationship.get("Id", "")] = relationship
    missing = sorted(referenced - set(defined))
    if missing:
        return (f"{main} refers to relationship id(s) {', '.join(missing)} "
                f"that {main_rels} does not define")

    for identifier, relationship in defined.items():
        if relationship.get("TargetMode") == "External":
            continue
        target = _resolve(main_rels, relationship.get("Target", ""))
        if target not in names:
            return (f"{main} references {target} (relationship "
                    f"{identifier}), which is not in the package")
    return ""
