"""Which skill to build next, answered from evidence rather than appetite.

The rule this module exists to enforce: **expand only where there is
evidence of paying demand, and only where the result can be certified.**
Both halves matter, and the second is the one that quietly rules out most
of what sounds attractive.

DeskPilot's five skills are deterministic. A verifier recomputes the
client's figures from the client's own file and gets the same answer twice,
which is why T1 verification means anything. A skill whose output cannot be
recomputed -- writing, summarising, design, translation, anything where two
competent attempts differ -- cannot be certified the way these are. It is
not that such work is unwelcome; it is that selling it would require a
different and weaker promise than the one DeskPilot makes.

So a candidate must clear two bars:

``VERIFIABLE``
    The output is a deterministic function of the input and the stated
    rules. Judged here, once, from the nature of the task.
``DEMANDED``
    Real opportunities have actually been seen asking for it. Judged from
    the evidence base, every time this is called -- so the answer changes
    on its own when demand appears, instead of waiting for somebody to
    revisit a list.

Nothing is built on one bar. A verifiable skill nobody wants is wasted
work; a wanted skill that cannot be certified is a promise DeskPilot
cannot keep.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verifiability(Enum):
    #: Recomputable from the source and the stated rules.
    DETERMINISTIC = "DETERMINISTIC"
    #: Checkable only against a human judgement of quality.
    JUDGEMENT = "JUDGEMENT"
    #: Depends on facts outside the client's file.
    EXTERNAL_FACT = "EXTERNAL_FACT"


class Verdict(Enum):
    BUILD = "BUILD"
    #: Verifiable, but nobody has asked for it yet.
    AWAIT_DEMAND = "AWAIT_DEMAND"
    #: Asked for, and not certifiable the way the five are.
    NOT_CERTIFIABLE = "NOT_CERTIFIABLE"
    #: Neither.
    NO = "NO"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A task category somebody might pay for."""

    name: str
    description: str
    verifiability: Verifiability
    #: Words that would appear in a posting asking for this. Used to count
    #: demand against real opportunities, never to match a job to a skill.
    signals: tuple
    #: Why it is or is not deterministic, in one sentence.
    note: str = ""

    @property
    def certifiable(self) -> bool:
        return self.verifiability is Verifiability.DETERMINISTIC


#: Task categories people pay freelancers for that sit near the five.
#: Deliberately includes the ones that fail, because a list of only the
#: plausible ones hides the reasoning that excluded the rest.
CANDIDATES = (
    Candidate(
        "pdf-table-extraction",
        "Pull tables out of PDFs into a spreadsheet",
        Verifiability.DETERMINISTIC,
        ("pdf", "extract", "table", "scrape pdf", "pdf to excel"),
        note="Extraction from a digital PDF is recomputable: the same file "
             "and the same rules give the same table twice. Scanned pages "
             "are not, and would have to be refused rather than guessed."),
    Candidate(
        "file-format-conversion",
        "Convert between tabular and document formats in bulk",
        Verifiability.DETERMINISTIC,
        ("convert", "batch convert", "xlsx to csv", "file format"),
        note="A format conversion is a function of its input."),
    Candidate(
        "data-validation",
        "Check a dataset against rules and produce an exceptions report",
        Verifiability.DETERMINISTIC,
        ("validate", "data quality", "check data", "audit spreadsheet"),
        note="Validation against stated rules is exactly the shape the "
             "existing verifiers already have."),
    Candidate(
        "invoice-generation",
        "Produce invoices from a schedule of work",
        Verifiability.DETERMINISTIC,
        ("invoice", "billing", "generate invoices"),
        note="Arithmetic and a template. Already close to what "
             "document-processing does."),
    Candidate(
        "web-scraping",
        "Collect data from websites into a spreadsheet",
        Verifiability.EXTERNAL_FACT,
        ("scrape", "crawl", "collect from website", "data mining"),
        note="The output depends on what a third party published at a "
             "moment, which cannot be recomputed later -- and most sites "
             "forbid it, which is a separate and sufficient reason."),
    Candidate(
        "copywriting",
        "Write marketing or website copy",
        Verifiability.JUDGEMENT,
        ("copywriting", "write content", "blog post", "product description"),
        note="Two competent attempts differ, so there is nothing to "
             "recompute and no verifier that could pass or fail one."),
    Candidate(
        "data-analysis-and-insight",
        "Analyse a dataset and say what it means",
        Verifiability.JUDGEMENT,
        ("insight", "analysis", "what does this data show", "recommendations"),
        note="The figures are recomputable; the conclusion drawn from them "
             "is the deliverable, and it is a judgement."),
    Candidate(
        "translation",
        "Translate documents between languages",
        Verifiability.JUDGEMENT,
        ("translate", "translation", "localisation"),
        note="Correctness is a matter of fluency, which no deterministic "
             "check establishes."),
)

BY_NAME = {c.name: c for c in CANDIDATES}

#: How many real opportunities must ask for something before it counts as
#: demand. Three rather than one: a single posting is an anecdote, and
#: building a skill for one job that may never recur is how a service
#: business acquires capabilities nobody buys twice.
DEMAND_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class Assessment:
    candidate: Candidate
    verdict: Verdict
    demand_seen: int
    why: str


def evaluate(opportunities, *, threshold: int = DEMAND_THRESHOLD) -> list:
    """Assess every candidate against the evidence actually available.

    ``opportunities`` is whatever Discovery has recorded. An empty evidence
    base is not a reason to build nothing *in principle* -- it is a reason
    to build nothing *yet*, and the two read differently in a report.
    """
    out = []
    for candidate in CANDIDATES:
        seen = _demand_for(candidate, opportunities)
        if not candidate.certifiable:
            verdict = Verdict.NOT_CERTIFIABLE if seen else Verdict.NO
            why = candidate.note
        elif seen >= threshold:
            verdict = Verdict.BUILD
            why = (f"{seen} real opportunit{'y' if seen == 1 else 'ies'} "
                   f"asked for this, and it can be verified deterministically")
        else:
            verdict = Verdict.AWAIT_DEMAND
            why = (f"verifiable, but only {seen} of the {threshold} real "
                   f"opportunities needed to call it demand")
        out.append(Assessment(candidate, verdict, seen, why))
    return out


def _demand_for(candidate: Candidate, opportunities) -> int:
    """How many recorded opportunities asked for this. Text, not guesswork.

    Counts a posting once however many signals it contains, so a listing
    that says "scrape" four times is one data point rather than four.
    """
    count = 0
    for row in opportunities or ():
        haystack = " ".join(
            str(row.get(field, "")) for field in ("title", "needs", "raw_ref")
        ).lower()
        if any(signal in haystack for signal in candidate.signals):
            count += 1
    return count


def recommendation(opportunities, **kwargs) -> str:
    """One sentence for a person deciding what to build next."""
    assessments = evaluate(opportunities, **kwargs)
    build = [a for a in assessments if a.verdict is Verdict.BUILD]
    if build:
        names = ", ".join(a.candidate.name for a in build)
        return f"Build next, on observed demand: {names}."
    waiting = sum(1 for a in assessments
                  if a.verdict is Verdict.AWAIT_DEMAND)
    return (f"Build nothing yet. {waiting} candidate(s) could be certified "
            f"if demand appeared; none has reached {DEMAND_THRESHOLD} real "
            "opportunities, and no real opportunity has been recorded at "
            "all.")
