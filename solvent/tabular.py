"""Reading a client's table as the client actually saved it.

Two facts about a CSV arrive before any of its contents: what bytes
encode its characters, and what character separates its fields. Until
this module existed DeskPilot assumed both -- UTF-8, comma -- and
refused everything else. An audit measured the cost: of thirteen
realistic client inputs, two were accepted. A file exported from Excel
in continental Europe is semicolon-separated cp1252 and was refused at
the door.

Refusing was never the wrong *behaviour*. Guessing an encoding and
quietly producing mojibake is far worse than declining the job, and
that is the trade this module has to respect while widening what can
be accepted.

So it does three things and advertises all of them:

**It decodes by a fixed ladder, not by a probability.** UTF-8 first,
because a file that decodes as UTF-8 almost certainly is UTF-8 and the
BOM settles it. Then UTF-16, which announces itself with a byte-order
mark. Then cp1252, which is the Windows default across Western Europe
and which -- this is the catch -- decodes almost *any* byte sequence
without complaining. That last property is why cp1252 is last and why
it is never silently trusted: :func:`decode` returns the encoding it
used so the caller can record it, and the verifier checks the result
for substitution damage independently.

**It detects the delimiter with the standard library.** ``csv.Sniffer``
is already installed on every Python and identified comma and semicolon
on the exact inputs that used to be refused. No dependency was added
for this and none was needed.

**It sniffs only the two separators this capability advertises.** A
semicolon ``.csv`` is what Excel writes across continental Europe --
same format, same extension, same contract. A tab-separated file is
``.tsv``, a *different* format, and ``supported_formats`` says this
capability reads ``csv``; there is a test asserting that proving CSV
does not imply TSV. Sniffing tab here would have quietly started
delivering work in a format nothing certified, which is a wider change
than reading more encodings and was never asked for.

**It falls back to comma rather than refusing on an unclear file.**
Comma is the advertised default, and the guards downstream are both
older and more specific than anything this module could say: a file
with no column names is refused as having no column names, a binary
blob is refused for its control bytes, and a file that comes out one
column wide is refused as a wrong delimiter. Refusing here instead
replaced those precise diagnoses with a vague one, which is a worse
message for the same outcome. Safety does not rest on this fallback:
it rests on the one-column guard, which catches a semicolon file read
as comma whether or not the sniffer had an opinion.

Nothing here transforms data. It turns bytes into rows and says how it
did it, so that everything downstream is working from a stated
assumption instead of an invisible one.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

#: Tried in this order. The order is the whole design: the two that
#: announce themselves come first, and the one that never fails comes
#: last so it is only ever reached when nothing else fits.
LADDER = ("utf-8-sig", "utf-16", "cp1252")

#: UTF-16 is only attempted when the bytes say so. Without this gate
#: it is not a rung, it is a trap: Python's utf-16 codec accepts any
#: even-length byte sequence as little-endian and returns garbage CJK
#: instead of raising. A cp1252 file of even length therefore decoded
#: "successfully" as UTF-16 and would have been cleaned, verified and
#: delivered as mojibake. Found by a test asserting the declared
#: encoding, which is the reason the declaration exists.
BOMS = (b"\xff\xfe", b"\xfe\xff")

#: What a decoder inserts when it meets a byte it cannot represent.
#: Its presence means characters were lost, which is the one outcome
#: worse than refusing the file.
REPLACEMENT = "�"

#: Delimiters worth sniffing for. Restricted twice over: letting the
#: sniffer consider any punctuation finds "delimiters" in prose, and
#: tab and pipe belong to formats this capability does not advertise.
CANDIDATES = ",;"

#: How much of the file the sniffer reads. Enough to see the shape,
#: small enough that a 200MB export does not become a 200MB string
#: before anything has decided whether to accept it.
SAMPLE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class Decoded:
    """Text, the encoding it came from, and whether to trust it."""

    text: str = ""
    encoding: str = ""
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


@dataclass(frozen=True, slots=True)
class Sniffed:
    delimiter: str = ","
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def decode(raw: bytes) -> Decoded:
    """Bytes to text, by the ladder, saying which rung worked.

    A successful decode that contains replacement characters is
    treated as a failure, not a success with damage: the byte that
    could not be represented was somebody's customer name.
    """
    if not raw:
        return Decoded(problem="the source is empty")
    for encoding in LADDER:
        if encoding == "utf-16" and not raw.startswith(BOMS):
            # No byte-order mark, no UTF-16. See BOMS above: this
            # codec does not fail on non-UTF-16 input, so "it
            # decoded" carries no information without the mark.
            continue
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
        if REPLACEMENT in text:
            # cp1252 will not raise, so this is the only place a
            # lossy decode can be caught. Keep walking rather than
            # accept a file with holes in it.
            continue
        if "\x00" in text:
            # A NUL after a successful decode means the bytes were
            # not text in this encoding -- most often UTF-16 content
            # read as single-byte. Not fatal here; try the next rung.
            continue
        return Decoded(text=text, encoding=encoding)
    if b"\x00" in raw[:SAMPLE_BYTES]:
        # Said separately because it is a different problem with a different
        # remedy. A .xlsx is a zip full of NUL bytes; telling its owner to
        # "re-save it as UTF-8" is advice they cannot act on, where "export
        # it to CSV first" is the actual next step.
        return Decoded(problem=(
            "it contains NUL bytes, so it is a binary file rather than text; "
            "a spreadsheet or document must be exported to CSV before it can "
            "be worked on"))
    return Decoded(problem=(
        "the source does not decode as "
        + ", ".join(LADDER)
        + " without losing characters; if it is in another encoding it "
        "must be re-saved as UTF-8 before it can be worked on"))


def sniff(text: str, *, sample_chars: int = SAMPLE_BYTES) -> Sniffed:
    """Which character separates the fields.

    Answers only when confident, and otherwise returns comma -- the
    format this capability advertises -- so that the file meets the
    same specific guards it met before this module existed. The
    failure that matters, a semicolon file read as one enormous column
    and "cleaned", is caught by those guards rather than by a refusal
    here, and it is caught whether or not the sniffer had an opinion.
    """
    sample = text[:sample_chars]
    if not sample.strip():
        return Sniffed(problem="the source has no content to inspect")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=CANDIDATES)
    except csv.Error:
        return Sniffed(delimiter=",")
    if dialect.delimiter not in CANDIDATES:
        return Sniffed(delimiter=",")
    return Sniffed(delimiter=dialect.delimiter)


def read(raw: bytes) -> tuple[list[list[str]], str, str, str]:
    """``(rows, encoding, delimiter, problem)`` from raw bytes.

    One call so that no caller has to remember to do the two steps in
    the right order, and so that every caller reports the same two
    facts about what it assumed.
    """
    decoded = decode(raw)
    if not decoded.ok:
        return [], "", "", decoded.problem
    sniffed = sniff(decoded.text)
    if not sniffed.ok:
        return [], decoded.encoding, "", sniffed.problem
    try:
        rows = list(csv.reader(io.StringIO(decoded.text),
                               delimiter=sniffed.delimiter))
    except csv.Error as exc:
        return [], decoded.encoding, sniffed.delimiter, (
            f"the source will not parse as a table: {exc}")
    return rows, decoded.encoding, sniffed.delimiter, ""


def describe(encoding: str, delimiter: str) -> str:
    """How the assumption reads in a report the owner will see."""
    shown = {",": "comma", ";": "semicolon", "\t": "tab",
             "|": "pipe"}.get(delimiter, repr(delimiter))
    return (f"read as {encoding or 'an undetermined encoding'}, "
            f"{shown}-separated")
