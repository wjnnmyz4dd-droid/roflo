"""Does a verifier actually catch wrong work? Measured, never assumed.

A capability may supply both its executor and a verifier that agrees with that
executor's assumptions. Different module names do not make them independent;
different identity strings do not either. The previous certification proved
this by building a capability whose verifier returned PASS unconditionally: it
delivered an invented total of 999.99 and an invented approver, and Solvent's
permanent audit log recorded six PASS rows each claiming the value had been
"recomputed from source and deliverable". Nothing had been recomputed. The
evidence was false in the one place that is supposed to be true.

So this module asks the only question that distinguishes a verifier from a
rubber stamp:

    shown work that is wrong in a way it claims to cover,
    does it say so?

**What this module is not.** It never verifies client work, never touches a
job, never records verification evidence and never authorizes a delivery. It
builds small artifacts whose correctness it already knows, hands them to a
verifier, and reports what came back. The registry decides what that means —
measurement here, judgement there.

**Hidden ground truth.** The verifier is handed a source and an output and
nothing else. It is never told which trial it is in, whether the artifact is
meant to be good or bad, or what the expected answer is. The trials are
generated fresh per run into a temporary directory, so a verifier cannot
recognise them by path.

**Both directions are failures.** A verifier that accepts everything cannot
protect a client. A verifier that rejects everything cannot ship one a
deliverable, and would be indistinguishable from a broken pipeline. Either one
fails certification here.
"""

from __future__ import annotations

import csv
import json
import hashlib
import inspect
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: A trial the verifier must ACCEPT: the artifact genuinely satisfies the check.
GOOD = "GOOD"
#: A trial the verifier must REJECT: the artifact is genuinely defective.
BAD = "BAD"


@dataclass(frozen=True, slots=True)
class Trial:
    """One hidden-ground-truth case. ``expect`` is never shown to the verifier."""

    name: str
    check: str
    expect: str            # GOOD or BAD
    params: dict = field(default_factory=dict)
    #: Which defect this case carries, or "CORRECT". Certification is recorded
    #: per defect class, so "it caught something" never reads as "it catches
    #: this kind of thing".
    defect_class: str = ""
    #: Why this case exists, for a human reading a failure report.
    note: str = ""


@dataclass
class TrialResult:
    trial: Trial
    verdict: str           # ACCEPT | REJECT | ERROR
    detail: str = ""

    @property
    def correct(self) -> bool:
        return ((self.trial.expect == GOOD and self.verdict == "ACCEPT")
                or (self.trial.expect == BAD and self.verdict == "REJECT"))


@dataclass
class TrialReport:
    """What a verifier did against a battery whose answers it could not see."""

    capability: str
    verifier_ref: str
    results: list = field(default_factory=list)

    # A false accept is work shipped wrong while reporting success. A false
    # reject is a verifier that cannot ship correct work. They are different
    # failures and are counted separately rather than averaged into a score.
    @property
    def false_accepts(self) -> list:
        return [r for r in self.results
                if r.trial.expect == BAD and r.verdict != "REJECT"]

    @property
    def false_rejects(self) -> list:
        return [r for r in self.results
                if r.trial.expect == GOOD and r.verdict != "ACCEPT"]

    #: The seed this battery came from, so a failure can be replayed exactly.
    seed: int = 0
    #: Which stream: development, certification or surprise.
    stream: str = "development"

    @property
    def checks_exercised(self) -> set:
        return {r.trial.check for r in self.results}

    def good_instances(self, check: str) -> int:
        """Distinct correct artifacts this check accepted.

        Distinct by content: ten copies of one fixture are one piece of
        evidence, not ten. Counting them as ten is how a battery reports
        volume it does not have.
        """
        return len({r.trial.name for r in self.results
                    if r.trial.check == check and r.trial.expect == GOOD
                    and r.correct})

    def class_instances(self, check: str) -> dict:
        """``{defect_class: instances_caught}`` for one check."""
        counts: dict = {}
        for result in self.results:
            if (result.trial.check != check or result.trial.expect != BAD
                    or not result.correct):
                continue
            name = result.trial.defect_class or "UNCLASSIFIED"
            counts[name] = counts.get(name, 0) + 1
        return counts

    def classes_attempted(self, check: str) -> set:
        return {r.trial.defect_class or "UNCLASSIFIED" for r in self.results
                if r.trial.check == check and r.trial.expect == BAD}

    def classes_missed(self, check: str) -> set:
        """Defect classes this check was shown and did not catch every time."""
        missed = {r.trial.defect_class or "UNCLASSIFIED" for r in self.results
                  if r.trial.check == check and r.trial.expect == BAD
                  and not r.correct}
        return missed

    def checks_passed(self) -> set:
        """Checks the verifier got entirely right, in **both** directions.

        A check counts only if the battery put the verifier through at least one
        artifact that should be accepted and at least one that should be
        rejected, and it was right about all of them. Demanding both directions
        is what a one-sided battery would miss: tested only on defective work, a
        reject-everything stub looks perfect, and tested only on good work, so
        does a rubber stamp. Either would then be certified for that check.
        """
        wrong = {r.trial.check for r in self.results if not r.correct}
        accepted_a_good = {r.trial.check for r in self.results
                           if r.trial.expect == GOOD}
        rejected_a_bad = {r.trial.check for r in self.results
                          if r.trial.expect == BAD}
        both_directions = accepted_a_good & rejected_a_bad
        return (self.checks_exercised & both_directions) - wrong

    @property
    def one_sided_checks(self) -> set:
        """Checks the battery only tested in one direction. A gap in the battery."""
        good = {r.trial.check for r in self.results if r.trial.expect == GOOD}
        bad = {r.trial.check for r in self.results if r.trial.expect == BAD}
        return (good | bad) - (good & bad)

    @property
    def summary(self) -> str:
        return (f"{sum(1 for r in self.results if r.correct)}/{len(self.results)} "
                f"trials correct, {len(self.false_accepts)} false accept(s), "
                f"{len(self.false_rejects)} false reject(s)")


# --------------------------------------------------------------------- CSV
_CSV_HEAD = "Order ID,Customer,Order Date,Region,Units,Total\n"
_CSV_SOURCE = (_CSV_HEAD
               + "1001,  Acme  ,01/15/2026,north,10,250.00\n"
               + "1002,Beta,2026-01-16,South,5,200.00\n"
               + "1003,Gamma,2026-01-17,East,8,100.00\n"
               + "1001,  Acme  ,01/15/2026,north,10,250.00\n")

#: The output a correct csv-cleanup produces from the source above.
_CSV_GOOD = (_CSV_HEAD
             + "1001,Acme,2026-01-15,North,10,250.00\n"
             + "1002,Beta,2026-01-16,South,5,200.00\n"
             + "1003,Gamma,2026-01-17,East,8,100.00\n")


def _csv_variants() -> list[tuple[Trial, str]]:
    """``(trial, output_text)`` pairs. Each defect is one the check claims to catch."""
    dupe_ok = {"duplicates_removed": True}
    out: list[tuple[Trial, str]] = []

    def add(name, check, expect, text, params=None, note=""):
        out.append((Trial(name, check, expect, params or {}, note), text))

    add("clean-passes", "drop_exact_duplicates", GOOD, _CSV_GOOD,
        note="a correct artifact must be accepted, or nothing can ship")
    add("duplicate-remains", "drop_exact_duplicates", BAD,
        _CSV_GOOD + "1001,Acme,2026-01-15,North,10,250.00\n",
        note="the defect this check exists for")

    add("rows-reconcile", "row_reconciliation", GOOD, _CSV_GOOD, dupe_ok)
    add("row-dropped", "row_reconciliation", BAD,
        _CSV_HEAD + "1001,Acme,2026-01-15,North,10,250.00\n"
        + "1002,Beta,2026-01-16,South,5,200.00\n", dupe_ok,
        note="valid CSV, correct format, one row quietly gone")

    totals = {"columns": ["Total"]}
    add("totals-preserved", "preserve_columns", GOOD, _CSV_GOOD, totals)
    add("total-changed", "preserve_columns", BAD,
        _CSV_GOOD.replace("250.00", "1.00"), totals,
        note="a protected amount altered; the client's money")
    add("total-subtly-changed", "preserve_columns", BAD,
        _CSV_GOOD.replace("100.00", "100.01"), totals,
        note="one cent: plausible enough to be mistaken for rounding")

    dates = {"columns": ["Order Date"]}
    add("dates-normalised", "normalise_dates", GOOD, _CSV_GOOD, dates)
    add("date-wrong-way-round", "normalise_dates", BAD,
        _CSV_GOOD.replace("2026-01-15", "2026-15-01"), dates,
        note="day and month transposed; still looks like an ISO date")

    cols = {"columns": ["Order ID", "Order Date", "Region", "Total"]}
    add("columns-present", "require_columns", GOOD, _CSV_GOOD, cols)
    add("column-missing", "require_columns", BAD,
        "Order ID,Customer,Units\n1001,Acme,10\n", cols)

    add("parses", "parses_as_csv", GOOD, _CSV_GOOD)
    add("not-csv", "parses_as_csv", BAD, "\x00\x01\x02 this is not a csv at all\n")

    trim = {"columns": ["Customer"]}
    add("trimmed", "trim_whitespace", GOOD, _CSV_GOOD, trim)
    add("still-padded", "trim_whitespace", BAD,
        _CSV_GOOD.replace("Acme", "  Acme  "), trim)

    region = {"column": "Region", "case_insensitive": True,
              "mapping": {"north": "North", "south": "South", "east": "East"}}
    add("regions-mapped", "map_values", GOOD, _CSV_GOOD, region)
    add("region-unmapped", "map_values", BAD,
        _CSV_GOOD.replace("North", "north"), region)

    authorised = {"authorised_columns": {"Order Date": "full", "Region": "full",
                                         "Customer": "whitespace"}}
    add("nothing-else-changed", "no_unauthorised_changes", GOOD, _CSV_GOOD, authorised)
    add("unauthorised-transformation", "no_unauthorised_changes", BAD,
        _CSV_GOOD.replace("Beta", "BETA"), authorised,
        note="an improvement nobody asked for is still an unauthorised change")
    return out


# ------------------------------------------------------------------ report
_REPORT_FACTS = {
    "client": "Acme Corp",
    "period": "Q1 2026",
    "rows": [{"item": "Consulting", "amount": "100.00"},
             {"item": "Support", "amount": "350.50"}],
}
#: Correct output for the facts above. True total is 450.50.
_REPORT_GOOD = """## Overview

- client: Acme Corp
- period: Q1 2026
- approved_by: NOT SUPPLIED

## Lines

| item | amount |
|---|---|
| Consulting | 100.00 |
| Support | 350.50 |

## Totals

- amount total: 450.50
"""


def _report_variants() -> list[tuple[Trial, str]]:
    fields = {"fields": ["client", "period"]}
    absent = {"fields": ["client", "approved_by"]}
    out: list[tuple[Trial, str]] = []

    def add(name, check, expect, text, params=None, note=""):
        out.append((Trial(name, check, expect, params or {}, note), text))

    add("clean-passes", "report_facts_rendered", GOOD, _REPORT_GOOD, fields)
    add("fact-altered", "report_facts_rendered", BAD,
        _REPORT_GOOD.replace("Acme Corp", "Acme Holdings"), fields)
    add("similar-but-not-identical-name", "report_facts_rendered", BAD,
        _REPORT_GOOD.replace("client: Acme Corp", "client: Acme Corporation"), fields,
        note="the substring defect: a longer name contains the right one")
    add("fact-omitted", "report_facts_rendered", BAD,
        _REPORT_GOOD.replace("- period: Q1 2026\n", ""), fields)
    add("fact-qualified", "report_facts_rendered", BAD,
        _REPORT_GOOD.replace("- period: Q1 2026",
                             "- period: Q1 2026 (provisional)"), fields,
        note="text appended after a correct value still changes it")

    add("nothing-invented", "report_no_invented_facts", GOOD, _REPORT_GOOD)
    add("number-invented", "report_no_invented_facts", BAD,
        _REPORT_GOOD + "\n## Outlook\n\n- Projected Q2 2026: 720.00\n",
        note="an unsupported inference appended as fact")
    add("number-changed", "report_no_invented_facts", BAD,
        _REPORT_GOOD.replace("350.50", "3500.50"))

    add("total-correct", "report_total_correct", GOOD, _REPORT_GOOD,
        {"column": "amount"})
    add("total-wrong", "report_total_correct", BAD,
        _REPORT_GOOD.replace("450.50", "999.99"), {"column": "amount"})
    add("total-plausibly-wrong", "report_total_correct", BAD,
        _REPORT_GOOD.replace("450.50", "450.00"), {"column": "amount"},
        note="off by fifty pence; the shape of a rounding bug")

    add("absence-disclosed", "report_missing_disclosed", GOOD, _REPORT_GOOD, absent)
    add("plausible-approver-invented", "report_missing_disclosed", BAD,
        _REPORT_GOOD.replace("- approved_by: NOT SUPPLIED",
                             "- approved_by: J. Rivera"), absent,
        note="a name nobody supplied, in the field that says who signed off")
    add("absence-silently-dropped", "report_missing_disclosed", BAD,
        _REPORT_GOOD.replace("- approved_by: NOT SUPPLIED\n", ""), absent)

    add("rows-tabulated", "report_rows_tabulated", GOOD, _REPORT_GOOD)
    add("row-missing-from-table", "report_rows_tabulated", BAD,
        _REPORT_GOOD.replace("| Support | 350.50 |\n", ""))

    sections = {"headings": ["Overview", "Lines", "Totals"], "ordered": True}
    add("sections-in-order", "report_has_sections", GOOD, _REPORT_GOOD, sections)
    add("section-missing", "report_has_sections", BAD,
        _REPORT_GOOD.replace("## Totals", "## Appendix"), sections)

    add("opens", "report_opens", GOOD, _REPORT_GOOD)
    add("empty-document", "report_opens", BAD, "")

    overview = {"kind": "facts", "heading": "Overview",
                "fields": ["client", "period"]}
    add("section-present", "report_section", GOOD, _REPORT_GOOD, overview)
    add("section-absent", "report_section", BAD,
        _REPORT_GOOD.replace("## Overview", "## Introduction"), overview)
    add("fact-under-wrong-heading", "report_section", BAD,
        """## Overview

- period: Q1 2026

## Lines

- client: Acme Corp

| item | amount |
|---|---|
| Consulting | 100.00 |
""", overview,
        note="correct content, wrong place; a document-wide search cannot see it")
    add("section-empty", "report_section", BAD,
        _REPORT_GOOD.replace("- client: Acme Corp\n- period: Q1 2026\n"
                             "- approved_by: NOT SUPPLIED\n", ""), overview)

    table = {"kind": "table", "heading": "Lines", "columns": ["item", "amount"]}
    add("table-section-present", "report_section", GOOD, _REPORT_GOOD, table)
    add("table-section-missing-column", "report_section", BAD,
        _REPORT_GOOD.replace("| item | amount |", "| item | value |"), table)

    total = {"kind": "total", "heading": "Totals", "column": "amount"}
    add("total-section-present", "report_section", GOOD, _REPORT_GOOD, total)
    add("total-section-states-nothing", "report_section", BAD,
        _REPORT_GOOD.replace("- amount total: 450.50", "- see above"), total)
    return out


#: ``capability name -> (source_filename, source_text, variants)``
BATTERIES = {
    "csv-cleanup": ("client_supplied.csv", _CSV_SOURCE, _csv_variants),
    "report-builder": ("facts.json", json.dumps(_REPORT_FACTS, indent=2),
                       _report_variants),
}


def battery_for(capability: str) -> str:
    """Which battery certifies a verifier for this capability, and why that one.

    Not simply the capability's name. A new capability that produces documents
    and claims to decide the report checks must be certified against the report
    battery — otherwise a capability could escape certification merely by being
    called something no battery is registered under, which is exactly what the
    first approve-everything verifier did: it was blocked for having no battery
    rather than for being wrong.

    So the battery is chosen by **the checks the verifier claims to decide**.
    A verifier says, through the check registry, "I can decide these"; this
    picks the battery that tests those claims. A capability whose claimed checks
    match no battery gets no trials, and no trials is not a clean sheet.
    """
    name = (capability or "").split("/", 1)[0]
    if name in BATTERIES:
        return name
    try:
        from .checks import checks_for
    except ImportError:  # pragma: no cover - defensive
        return ""
    claimed = set(checks_for(capability))
    if not claimed:
        return ""
    best, overlap = "", 0
    for battery, (_, _, variants) in BATTERIES.items():
        covered = len(claimed & {trial.check for trial, _ in variants()})
        if covered > overlap:
            best, overlap = battery, covered
    return best


def trials_for(capability: str) -> list:
    battery = battery_for(capability)
    if not battery:
        return []
    return [trial for trial, _ in BATTERIES[battery][2]()]


def run_trials(verify, *, capability: str, verifier_ref: str) -> TrialReport:
    """Put a verifier through its capability's battery. Reports; decides nothing.

    ``verify`` is called exactly as the pipeline calls it — one check name, a
    source path, an output path, and the check's parameters. It receives no
    indication of which trial it is in.
    """
    battery = battery_for(capability)
    report = TrialReport(capability=capability, verifier_ref=verifier_ref)
    if not battery:
        return report

    filename, source_text, variants = BATTERIES[battery]
    workdir = Path(tempfile.mkdtemp(prefix="vcert-"))
    try:
        source = workdir / filename
        source.write_text(source_text, encoding="utf-8")
        for index, (trial, output_text) in enumerate(variants()):
            # A fresh, unremarkable filename per trial: nothing in the path
            # tells the verifier whether this one is supposed to be wrong.
            output = workdir / f"deliverable.{index}{Path(filename).suffix}"
            output.write_text(output_text, encoding="utf-8")
            try:
                outcome = verify(trial.check, source=str(source),
                                 output=str(output), params=dict(trial.params))
            except Exception as exc:  # noqa: BLE001 - a crash is not an answer
                report.results.append(TrialResult(
                    trial, "ERROR", f"{type(exc).__name__}: {exc}"))
                continue
            passed = getattr(outcome, "passed", None)
            if passed is None:
                report.results.append(TrialResult(
                    trial, "ERROR", "verifier returned no usable outcome"))
                continue
            detail = str(getattr(outcome, "detail", ""))[:120]
            report.results.append(TrialResult(
                trial, "ACCEPT" if passed else "REJECT", detail))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return report


def implementation_fingerprint(verify) -> str:
    """A hash of the verifier's actual source, not of what it is called.

    Certification is evidence about a particular piece of code. A name is not
    that code: rename the function, edit the body, and a certification earned by
    the old implementation would otherwise transfer to the new one silently.
    So the fingerprint is taken from the source text, and a certification whose
    fingerprint no longer matches is treated as being about something else —
    because it is.

    Falls back to the qualified name when source is unavailable (a C function, a
    stripped install). That fallback is weaker and is recorded as such rather
    than pretended to be a fingerprint.
    """
    name = getattr(verify, "__qualname__", None) or repr(verify)
    parts = [getattr(verify, "__module__", ""), name]
    got_source = False
    for target in (inspect.getmodule(verify), verify):
        if target is None:
            continue
        try:
            parts.append(inspect.getsource(target))
            got_source = True
        except (OSError, TypeError):
            continue
    if not got_source:
        return f"unfingerprinted:{parts[0]}.{name}"
    # The module's source *and* the callable's own — the module because a check
    # this function dispatches to can be weakened without touching the
    # dispatcher, the callable because two lambdas in one module would otherwise
    # be indistinguishable, which silently made them share a measurement.
    blob = "\n--\n".join(parts)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


#: Measurements are pure: the same code against the same seed gives the same
#: answer every time. Caching them is caching arithmetic, not sharing state —
#: every Solvent still records its own certification decision from the result.
_MEASURED: dict = {}


def run_generated(verify, *, capability: str, verifier_ref: str, seed: int,
                  cases: int = 14, stream: str = "certification") -> TrialReport:
    """Put a verifier through a generated battery it has never seen.

    Same contract as :func:`run_trials` — one check name, two paths, the check's
    parameters, and no indication of what the answer should be — but the cases
    are generated from ``seed`` rather than drawn from a fixed list. That is the
    whole difference between measuring competence and measuring familiarity:
    four verifiers that hardcoded the fixed fixtures earned full certification,
    including one that decided a total was correct whenever it was not 999.99.
    """
    from . import certgen

    key = (capability, verifier_ref, implementation_fingerprint(verify),
           seed, cases, stream)
    cached = _MEASURED.get(key)
    if cached is not None:
        return TrialReport(capability=capability, verifier_ref=verifier_ref,
                           results=list(cached.results), seed=seed, stream=stream)

    report = TrialReport(capability=capability, verifier_ref=verifier_ref,
                         seed=seed, stream=stream)
    generated = certgen.generate(capability, seed=seed, cases=cases)
    if not generated:
        return report

    workdir = Path(tempfile.mkdtemp(prefix="vgen-"))
    try:
        for index, case in enumerate(generated):
            source = workdir / f"{index}_{case.source_name}"
            source.write_text(case.source_text, encoding="utf-8")
            suffix = ".csv" if case.source_name.endswith(".csv") else ".md"
            output = workdir / f"{index}_deliverable{suffix}"
            output.write_text(case.output_text, encoding="utf-8")
            trial = Trial(name=case.name, check=case.check, expect=case.expect,
                          params=dict(case.params),
                          defect_class=case.defect_class, note=case.note)
            try:
                outcome = verify(case.check, source=str(source),
                                 output=str(output), params=dict(case.params))
            except Exception as exc:  # noqa: BLE001 - a crash is not an answer
                report.results.append(TrialResult(
                    trial, "ERROR", f"{type(exc).__name__}: {exc}"))
                continue
            passed = getattr(outcome, "passed", None)
            if passed is None:
                report.results.append(TrialResult(
                    trial, "ERROR", "verifier returned no usable outcome"))
                continue
            report.results.append(TrialResult(
                trial, "ACCEPT" if passed else "REJECT",
                str(getattr(outcome, "detail", ""))[:120]))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    _MEASURED[key] = report
    return report
