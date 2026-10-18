"""A sealed examination of what this system does with a client's file.

The capacity exam in :mod:`tests_solvent.capacityexam` seals four
modules and asks whether the system stays honest under load. It does not
examine reading, and reading is what the capability-activation sweep
changed, so a second paper was set over that surface.

The rules are the first exam's rules and are not restated loosely:

**The paper is sealed.** :data:`DIGESTS` records the SHA-256 of every
module under examination at the freeze. If any of them moves, the exam
refuses to mark itself rather than quietly re-marking a different
system. A broken seal is not a failure, it is an invalidation: reseal,
re-set and re-sit.

**The seed comes from the freeze, never from the environment.** An exam
whose seed can vary is an exam that can be re-sat until it passes.

**Every answer is known before the system is asked.** Each question is
built by taking rows, encoding them with a chosen codec and separator,
and optionally damaging the bytes afterwards. The expected rows are the
rows that went in. Nothing is marked against what the implementation
returned.

**The inputs are ones development never used.** The development tests
use ASCII, cp1252 Latin-1 accents, one Chinese fixture, commas and
semicolons. This paper draws from scripts and shapes they do not touch:
Greek, Cyrillic, Hebrew, a four-byte emoji, fields that quote the
separator, fields holding an embedded newline, empty fields, and a
field longer than any fixture.

What is being examined is narrow and worth stating, so the result is
not read as more than it is: whether the characters a client saved come
back unchanged, whether the assumption used to read them is declared
truthfully, and whether a file that cannot be read honestly is refused
instead of guessed at.
"""

from __future__ import annotations

import hashlib
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from solvent import tabular  # noqa: E402
from solvent.types import Criticality, Requirement, RequirementSource  # noqa: E402

#: When the code under examination was frozen.
#:
#: RE-SEALED 2026-10-18. ``solvent/csvverify.py`` changed under an explicit
#: owner authorisation: ``check_preserve_columns`` compared protected values
#: against the source as it *arrived*, so a brief that authorised trimming
#: and protection on the same column failed correct work. The seal broke on
#: the next run, which is what it is for. The seal forbids an *unnoticed*
#: change, not an authorised one.
#:
#: Before re-sealing, the first paper was re-marked against the changed
#: source and every answer was the same -- 56 questions, 56 correct, 24
#: readable, 32 damaged, none wrong. The change is to a *verification*
#: check and this paper examines *reading*, so answer-neutrality was the
#: expected result; it was measured rather than assumed.
#:
#: The first sitting is preserved rather than overwritten: in
#: :data:`FIRST_SITTING` below, and in
#: ``docs/evidence/reading-exam-first-sitting.json``, which is the original
#: evidence file kept under its own name.
#:
#: Because SEED derives from FROZEN_AT, moving the date generates a
#: *different* paper, so what follows is a genuine second sitting rather
#: than the same questions re-marked.
FROZEN_AT = "2026-10-18T16:40:00Z"

#: SHA-256 of each module at the freeze.
DIGESTS = {
    "solvent/tabular.py":
        "acecd399cd4cefbd7bfa78dcddde55ab186e1d9ad37a9b5d0be8a929bb7fe44c",
    "solvent/csvwork.py":
        "82b6ab3ec2f073ed7dde38acd544395bd90671a711a7784df9a67d5a8200e00b",
    "solvent/csvverify.py":
        "5d52279c2f961ad99755a68ec5fd30cd95a45acce7c643cfd5c76a9e7d468a48",
    "solvent/entrywork.py":
        "80fc61d604eb459ce0be87eebeef9f5e6e70420f0386ead1d190d4562aeff4e0",
    "solvent/entryverify.py":
        "c5fad74ed8bd098e01cd483910db177bb31d6f9d77b380e26d919b5ef2f79686",
    "solvent/reportwork.py":
        "ed776ea3954212e8544bf14a7a28db655ff5a7a0e2524ee680eb3efec93dd056",
    "solvent/reportverify.py":
        "99192e9f3a3fd347fee15707638de67052715fed39345a0757cb98512694d2be",
    "solvent/opcpackage.py":
        "36926e748ec36ffa27938038078ce7cba0580e17ef78c81ea7d890bd4b575216",
}

#: Derived from the freeze, never from the environment.
SEED = int(hashlib.sha256(FROZEN_AT.encode()).hexdigest()[:8], 16)

#: Every sitting, keyed by the freeze it was sat against. Preserved, never
#: refreshed: a first run that gets rewritten is not a first run. The
#: capacity exam has kept its sittings this way since its own seal first
#: broke, and this one now does the same rather than leaving the record to
#: a single JSON file that each new sitting overwrites.
FIRST_SITTING = """Sat 2026-10-16T05:01:03Z, seed 2994720811.

56 questions, 56 correct, 0 wrong. 24 readable papers and 32 damaged
ones, so neither answer could score full marks on its own.

Recorded at the time in docs/evidence/reading-exam.json, and kept under
docs/evidence/reading-exam-first-sitting.json once a second sitting
needed the original name.
"""

SECOND_SITTING = """Sat 2026-10-18T16:40:00Z, seed 3726124977.

56 questions, 56 correct, 0 wrong. 24 readable, 32 damaged.

The second sitting, and the first time this seal has done its job rather
than merely held. solvent/csvverify.py changed under an explicit owner
authorisation -- check_preserve_columns was comparing protected values
against the source as it arrived, which failed correct trim-then-dedupe
work -- and the digests stopped matching, so the exam refused to mark
itself.

Before re-sealing, the first paper was re-marked against the changed
source and every answer was the same: 56 correct, none wrong. That the
change is to a verification check while this paper examines reading made
answer-neutrality the expected result, but it was measured rather than
assumed. Because SEED derives from FROZEN_AT, moving the freeze made this
a different paper and not a re-mark of the first.
"""

SITTINGS = {
    "2026-10-16T05:01:03Z": FIRST_SITTING,
    "2026-10-18T16:40:00Z": SECOND_SITTING,
}

#: Scripts development never used. Latin-1 accents are deliberately
#: absent: those are the cases the sweep was built against.
UNSEEN_NAMES = (
    "Αθήνα ΕΠΕ",          # Greek
    "Москва ООО",    # Cyrillic
    "תל אביב בעמ",   # Hebrew
    "Nguyễn Thị",                                        # Vietnamese
    "Łukasiewicz",                                            # Polish
    "Çağlayan",                                          # Turkish
)

#: Shapes development never used.
AWKWARD = (
    "a field, with the separator in it",
    'a field with "quotes" inside it',
    "a field\nwith an embedded newline",
    "",
    "x" * 300,
)

#: Encodings a client's export actually arrives in, with whether each can
#: represent the whole repertoire above.
CODECS = ("utf-8", "utf-8-sig", "utf-16", "cp1252")
SEPARATORS = (",", ";")

#: Ways a file is genuinely unreadable. Each must be refused, and
#: refusing is the right answer rather than a shortfall.
DAMAGE = ("utf16_without_a_mark", "a_byte_valid_nowhere", "nul_bytes",
          "tab_separated")


class Question:
    """One paper question, with the answer fixed before it is asked."""

    def __init__(self, number, rows, codec, separator, damage=""):
        self.number = number
        self.rows = rows
        self.codec = codec
        self.separator = separator
        self.damage = damage

    @property
    def must_be_read(self) -> bool:
        return not self.damage

    def _text(self) -> str:
        out = []
        for row in self.rows:
            cells = []
            for cell in row:
                if any(c in cell for c in (self.separator, '"', "\n")):
                    cells.append('"' + cell.replace('"', '""') + '"')
                else:
                    cells.append(cell)
            out.append(self.separator.join(cells))
        return "\n".join(out) + "\n"

    def payload(self) -> bytes:
        text = self._text()
        if self.damage == "utf16_without_a_mark":
            return text.encode("utf-16-le")
        if self.damage == "tab_separated":
            return text.replace(self.separator, "\t").encode("utf-8")
        raw = text.encode(self.codec)
        if self.damage == "a_byte_valid_nowhere":
            # What counts as a byte valid nowhere depends on the codec,
            # and the first version of this paper ignored that: appending
            # 0x81 0x8d 0x8f 0x90 0x9d to a UTF-16 file damages nothing,
            # because those are perfectly good UTF-16 code units. For a
            # wide codec the equivalent damage is an odd trailing byte,
            # which truncates the last unit.
            if self.codec.startswith("utf-16"):
                return raw + b"\x81"
            return raw + b"\x81\x8d\x8f\x90\x9d\n"
        if self.damage == "nul_bytes":
            return raw + b"\x00\x00\x00\x00\n"
        return raw

    @property
    def expected_encoding(self) -> str:
        """Which rung the ladder should truthfully report.

        Not the codec the question used: UTF-8 is tried first and is the
        right answer whenever the bytes are valid UTF-8, which includes
        every pure-ASCII file whatever it was saved as. Marked against
        the ladder's stated order, not against what it happens to do --
        and the characters themselves are marked against the rows that
        went in, which no ordering rule can fudge.
        """
        raw = self.payload()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "utf-16"
        try:
            raw.decode("utf-8-sig")
            return "utf-8-sig"
        except UnicodeDecodeError:
            return "cp1252"


def _can_encode(rows, codec) -> bool:
    try:
        "".join(c for row in rows for c in row).encode(codec)
    except UnicodeEncodeError:
        return False
    return True


def paper() -> list:
    """The paper. Deterministic from the seal, unseen by the implementation."""
    rng = random.Random(SEED)
    questions, number = [], 0
    for codec in CODECS:
        for separator in SEPARATORS:
            for damage in ("",) * 3 + DAMAGE:
                number += 1
                body = []
                for _ in range(rng.randrange(2, 6)):
                    body.append([
                        f"{rng.randrange(1000, 9999)}",
                        rng.choice(UNSEEN_NAMES),
                        rng.choice(AWKWARD),
                        f"{rng.randrange(1, 90000) / 100:.2f}"])
                rows = [["Reference", "Client", "Note", "Amount"]] + body
                if not _can_encode(rows, codec):
                    # cp1252 cannot hold Greek. Substituting a name the
                    # codec can represent keeps the question honest rather
                    # than asking the system to do the impossible.
                    rows = [[c.encode(codec, "replace").decode(codec)
                             for c in row] for row in rows]
                questions.append(Question(number, rows, codec, separator,
                                          damage))
    return questions


def frozen_digests() -> dict:
    return {name: hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()
            for name in sorted(DIGESTS)}


def seal_is_intact() -> tuple[bool, str]:
    now = frozen_digests()
    moved = [name for name, digest in DIGESTS.items()
             if now.get(name) != digest]
    if moved:
        return False, ("the seal is broken: " + ", ".join(sorted(moved))
                       + " changed after the freeze, so this exam is no "
                       "longer the paper that was set")
    return True, "the seal is intact"


#: The brief every question is run under. It names this paper's own
#: columns on purpose: a brief the files cannot satisfy would see every
#: file refused, damaged or not, and the paper would score full marks
#: while measuring nothing. Because an undamaged file is *delivered*
#: under this brief, a refusal is caused by the damage.
_BRIEF = (
    Requirement(
        id="X-OPEN", text="The deliverable must open as CSV.",
        source=RequirementSource.SYSTEM_SAFETY, acceptance="parses",
        check="parses_as_csv", params={},
        criticality=Criticality.MANDATORY),
    Requirement(
        id="X-COLS", text="Keep the agreed columns.",
        source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance="the four agreed columns are present",
        check="require_columns",
        params={"columns": ["Reference", "Client", "Note", "Amount"]},
        criticality=Criticality.MANDATORY),
)


def _delivered(payload: bytes) -> tuple[bool, str]:
    """Run the whole job over these bytes. ``(delivered, why not)``.

    Asked of the whole pipeline rather than of
    :func:`solvent.tabular.read` or of the worker alone, because the
    question worth marking is whether a client receives a file, and that
    is answerable only end to end. The first two versions of this paper
    asked lower layers and reported defects that were not there: a
    tab-separated file is read by the ladder as one column, which is the
    honest answer to what it was shown, and is then refused above --
    sometimes by the plausibility guard, sometimes by verification. A
    paper that marks a layer for a decision that layer does not make is
    measuring the wrong thing.
    """
    import tempfile

    from solvent.harness import run_csv_job

    directory = pathlib.Path(tempfile.mkdtemp(prefix="readingexam-"))
    source = directory / "client.csv"
    source.write_bytes(payload)
    report = run_csv_job(source=str(source), requirements=list(_BRIEF),
                         workdir=str(directory))
    return report.delivered, (report.escalated or "")


def mark(question: Question) -> tuple[bool, str]:
    """``(correct, why)`` for one question, against the answer set first."""
    delivered, why_not = _delivered(question.payload())

    if not question.must_be_read:
        if not delivered:
            return True, f"refused: {why_not[:70]}"
        return False, (f"delivered a file damaged by {question.damage!r} "
                       "instead of refusing it")

    if not delivered:
        return False, f"refused a readable file: {why_not[:70]}"

    # Delivered is necessary and not sufficient. The characters the client
    # saved have to be the characters that came back, and the assumption
    # used to read them has to be stated truthfully.
    rows, encoding, separator, problem = tabular.read(question.payload())
    if problem:
        return False, f"refused a readable file: {problem[:70]}"
    if rows != question.rows:
        for expected, got in zip(question.rows, rows):
            if expected != got:
                return False, f"expected {expected!r} but read {got!r}"
        return False, (f"expected {len(question.rows)} row(s), "
                       f"read {len(rows)}")
    if separator != question.separator:
        return False, (f"declared {separator!r} as the separator where the "
                       f"file uses {question.separator!r}")
    if encoding != question.expected_encoding:
        return False, (f"declared {encoding!r} where the ladder should "
                       f"report {question.expected_encoding!r}")
    return True, f"read as {encoding}, {separator!r}"


def sit() -> dict:
    """Sit the paper once and report it, marked or refused."""
    intact, why = seal_is_intact()
    if not intact:
        return {"sealed": False, "why": why, "questions": 0}
    results = [(q, *mark(q)) for q in paper()]
    wrong = [{"question": q.number, "codec": q.codec,
              "separator": q.separator, "damage": q.damage or "none",
              "why": why} for q, ok, why in results if not ok]
    return {"sealed": True, "frozen_at": FROZEN_AT, "seed": SEED,
            "questions": len(results),
            "correct": sum(1 for _, ok, _ in results if ok),
            "readable_questions": sum(1 for q in paper() if q.must_be_read),
            "damaged_questions": sum(1 for q in paper() if not q.must_be_read),
            "wrong": wrong}


if __name__ == "__main__":
    import json

    print(json.dumps(sit(), indent=2, ensure_ascii=False))
