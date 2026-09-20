"""What could mean more than one thing, and does it matter?

Certification made Solvent's verifiers hard to fool. It did nothing about a
different problem the same work exposed: some inputs have more than one
legitimate reading, and no amount of testing determines which the client meant.

``05/10/2026`` is 10 May or 5 October. Both are correct readings of what was
supplied. A verifier that picks one is asserting a fact nobody has; a verifier
that refuses both makes the job impossible. The previous run left this open and
said so, and this module closes it — not by choosing better, but by noticing
that a choice is being made and refusing to make it silently.

**This is not an authority.** Every function here is data in, data out: given a
source and a set of requirements, what is ambiguous? It records nothing, decides
no job state, sends no message and resolves nothing. The Orchestrator owns the
record of an open question because it already owns job state and the committed
checklist; Client Relations owns asking, because it already owns talking to
clients; and the answer becomes an ordinary confirmed requirement, because that
is already how an agreed fact about a job is represented.

**Materiality is the point.** Blocking a job over a presentational choice is as
much a failure as guessing at a semantic one. An ambiguity is reported here only
when the competing readings would produce materially different work — a
different date in the client's file, a different number in their report, a
different set of rows.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime

from .types import RequirementSource

#: The value could be read more than one way and the readings differ materially.
MATERIAL = "MATERIAL"
#: More than one reading exists but they produce the same work.
IMMATERIAL = "IMMATERIAL"


@dataclass(frozen=True, slots=True)
class Ambiguity:
    """One thing that could mean more than one thing.

    ``interpretations`` is the competing readings, in no significance order —
    deliberately, because ranking them is the first step towards picking one.
    ``resolved_by`` names the parameter a clarification must supply, which is
    what lets an answer become a requirement rather than a note.
    """

    kind: str
    subject: str
    observed: tuple
    interpretations: tuple
    materiality: str = MATERIAL
    requirement_id: str = ""
    resolved_by: str = ""
    question: str = ""

    @property
    def blocks_work(self) -> bool:
        return self.materiality == MATERIAL


_SLASHED = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\s*$")

#: What a supplied date format declaration may say. Stated as an explicit,
#: closed set: a free-text answer is not a format, and "the usual one" resolves
#: nothing.
DATE_FORMATS = ("MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD")


def date_readings(value: str, declared: str = "") -> list[str]:
    """Every day ``value`` could denote, as ISO strings.

    With ``declared`` set, only that convention is read — which is the whole
    point of asking: after the client answers, the value has one meaning, and
    both the worker and the checks must use that one.
    """
    text = (value or "").strip()
    if not text:
        return []
    match = _SLASHED.match(text)
    if not match:
        try:
            return [datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")]
        except ValueError:
            return []
    first, second, year = (int(g) for g in match.groups())
    candidates = []
    if declared != "DD/MM/YYYY":
        candidates.append((first, second))          # MM/DD
    if declared != "MM/DD/YYYY":
        candidates.append((second, first))          # DD/MM
    out = []
    for month, day in candidates:
        try:
            out.append(datetime(year, month, day).strftime("%Y-%m-%d"))
        except ValueError:
            continue
    return sorted(set(out))


def declared_date_format(requirements) -> str:
    """The format the client confirmed for this job, or "".

    Read from the committed requirements, which is where an agreed fact about a
    job lives. Not from a default, a locale, a majority vote across the file, or
    anything about the client's name or country — none of those is evidence
    about what this client meant.
    """
    for requirement in requirements or ():
        declared = (requirement.params or {}).get("date_format")
        if declared and requirement.source.may_gate_commitment:
            return declared
    return ""


def _csv_rows(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = [r for r in csv.reader(handle) if r]
    except (OSError, UnicodeDecodeError, csv.Error):
        return [], []
    return (rows[0], rows[1:]) if rows else ([], [])


def csv_ambiguities(source, requirements) -> list[Ambiguity]:
    """Ambiguity in a cleanup job's own data. Today: dates that read two ways."""
    columns = sorted({c for r in requirements
                      if r.check == "normalise_dates"
                      for c in (r.params or {}).get("columns", [])})
    if not columns:
        return []
    if declared_date_format(requirements):
        return []           # the client has already said; there is nothing to ask

    header, body = _csv_rows(source)
    out = []
    for name in columns:
        if name not in header:
            continue
        index = header.index(name)
        two_ways = {}
        for row in body:
            if index >= len(row):
                continue
            readings = date_readings(row[index])
            if len(readings) > 1:
                two_ways[row[index].strip()] = readings
        if not two_ways:
            continue
        examples = sorted(two_ways)[:4]
        requirement = next((r.id for r in requirements
                            if r.check == "normalise_dates"
                            and name in (r.params or {}).get("columns", [])), "")
        out.append(Ambiguity(
            kind="DATE_FORMAT", subject=name, observed=tuple(examples),
            interpretations=("MM/DD/YYYY", "DD/MM/YYYY"),
            materiality=MATERIAL, requirement_id=requirement,
            resolved_by="date_format",
            question=(
                f"The {name} column contains dates such as "
                f"{', '.join(examples[:2])}, which could mean either "
                f"{date_readings(examples[0])[0]} or "
                f"{date_readings(examples[0])[1]} depending on the convention. "
                "Should ambiguous dates be read as MM/DD/YYYY or DD/MM/YYYY?")))
    return out


#: Words that name a business decision rather than a transformation.
#:
#: The test is never "does this word appear". It is "has anybody said which one
#: they mean", and a structured requirement usually already has. "A Totals
#: section summing the amounts" with ``column: amount`` is arithmetic over a
#: named column, not a choice between gross and net — asking about it would be
#: the unnecessary question that makes a clarification system a nuisance rather
#: than a safeguard. So each term carries what pins it: a check whose name is
#: itself the answer, or a parameter that names the thing.
#:
#: Fields: term, kind, readings, pinning checks, pinning params, the parameter
#: an answer supplies, and the question.
_LOADED_TERMS = (
    ("duplicate", "DUPLICATE_IDENTITY",
     ("an exactly identical row", "another row for the same customer or key"),
     ("drop_exact_duplicates", "row_reconciliation"), ("duplicate_key",),
     "duplicate_key",
     "Does 'duplicate' mean a row that is identical in every column, or a "
     "second row for the same customer or reference?"),
    ("latest", "LATEST_BY",
     ("the most recently uploaded", "the most recent event date",
      "the highest revision", "the most recently approved"),
     (), ("latest_by",), "latest_by",
     "Which sense of 'latest' should be used: most recently uploaded, latest "
     "event date, highest revision, or most recently approved?"),
    ("approved", "APPROVED_BY_WHOM",
     ("approved by the client", "approved by a named person",
      "carrying an approval flag in the data"),
     (), ("approved_by", "fields"), "approved_by",
     "'Approved' by whom? Is there a field in the data that records it, or a "
     "named person whose approval counts?"),
    ("total", "TOTAL_BASIS",
     ("gross", "net", "subtotal before tax", "tax-inclusive"),
     (), ("total_basis", "column", "columns"), "total_basis",
     "Which total is wanted: gross, net, subtotal before tax, or tax-inclusive?"),
    ("normalise names", "NAME_NORMALISATION",
     ("capitalisation and spacing only", "changing the recorded legal name"),
     (), ("name_normalisation", "mapping"), "name_normalisation",
     "Should normalising names change only capitalisation and spacing, or may "
     "the recorded legal name itself be altered?"),
    ("normalize names", "NAME_NORMALISATION",
     ("capitalisation and spacing only", "changing the recorded legal name"),
     (), ("name_normalisation", "mapping"), "name_normalisation",
     "Should normalising names change only capitalisation and spacing, or may "
     "the recorded legal name itself be altered?"),
    ("clean address", "ADDRESS_CLEANING",
     ("reformatting what is there", "correcting the address content"),
     (), ("address_cleaning",), "address_cleaning",
     "Should cleaning addresses reformat what is there, or correct addresses "
     "that appear to be wrong?"),
    ("blank row", "BLANK_ROW_MEANING",
     ("empty rows are noise to remove", "empty rows are meaningful separators"),
     (), ("blank_rows",), "blank_rows",
     "Are the empty rows noise to be removed, or deliberate separators that "
     "should be kept?"),
    ("combine customer", "CUSTOMER_IDENTITY",
     ("a matching name is the same customer",
      "only a matching identifier is the same customer"),
     (), ("customer_identity",), "customer_identity",
     "Does a matching customer name establish that two records are the same "
     "customer, or should only a matching identifier count?"),
    ("merge customer", "CUSTOMER_IDENTITY",
     ("a matching name is the same customer",
      "only a matching identifier is the same customer"),
     (), ("customer_identity",), "customer_identity",
     "Does a matching customer name establish that two records are the same "
     "customer, or should only a matching identifier count?"),
)


def requirement_ambiguities(requirements) -> list[Ambiguity]:
    """Requirements whose wording names a business decision nobody has made.

    A term is only reported when nothing has pinned it. A requirement saying
    "remove duplicate records" whose check is ``drop_exact_duplicates`` has
    already been pinned — the structured form *is* the answer, and asking again
    would be the unnecessary question §16 warns about. The same text with no
    check behind it has not been pinned, and quietly choosing one reading is how
    a client's second invoice for the same customer disappears.
    """
    out = []
    for requirement in requirements or ():
        # Solvent's own derived and safety requirements are not the client
        # saying something imprecise; they are Solvent saying something it
        # already decided. Asking the client to clarify Solvent's own wording
        # would be asking them about a sentence they never wrote.
        if not requirement.source.may_gate_commitment:
            continue
        if requirement.source in (RequirementSource.DERIVED,
                                  RequirementSource.SYSTEM_SAFETY):
            continue
        text = (requirement.text or "").lower()
        params = requirement.params or {}
        for (term, kind, readings, pinning_checks, pinning_params, resolves,
             question) in _LOADED_TERMS:
            if term not in text:
                continue
            if requirement.check in pinning_checks:
                continue
            if any(params.get(name) for name in pinning_params):
                continue
            out.append(Ambiguity(
                kind=kind, subject=requirement.id, observed=(requirement.text,),
                interpretations=readings, materiality=MATERIAL,
                requirement_id=requirement.id, resolved_by=resolves,
                question=question))
            break
    return out


#: ``capability -> detector``. A dispatch table of pure functions, the same
#: shape as :mod:`solvent.checks`, and for the same reason: which detector reads
#: which work should be answered by a lookup rather than by whichever module
#: happened to be imported at the top of a file.
DETECTORS = {
    "csv-cleanup": csv_ambiguities,
    "report-builder": lambda source, requirements: [],
}


def detect(capability: str, source, requirements) -> list[Ambiguity]:
    """Everything ambiguous about this job, data and wording alike."""
    name = (capability or "").split("/", 1)[0]
    found = list(requirement_ambiguities(requirements))
    detector = DETECTORS.get(name)
    if detector is not None:
        found.extend(detector(source, requirements))
    return [a for a in found if a.blocks_work]
