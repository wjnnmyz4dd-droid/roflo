"""One job per scenario, run through the whole business pipeline and graded.

The expected answer is **built before the question**. Every scenario comes
from :mod:`solvent.certgen`, which constructs a source file and the output
that is correct for it in the same step, from a seed. The worker never sees
the answer; the grader never asks the worker what it did. A scenario passes
only when the file DeskPilot delivered is the file that was correct for the
input before DeskPilot ran.

Two matrices use this, and the only difference between them is the seed:

``DEVELOPMENT_SEED``
    The 45 scenarios used while building. They are allowed to have informed
    the implementation, and a green result on them means very little on its
    own -- which is why the other one exists.

``EXAM_SEED``
    45 scenarios from a seed used nowhere else in this repository, generated
    after the implementation was frozen. Nothing was tuned against them and
    nothing may be: a failure here is a finding, not a prompt to adjust a
    threshold until it passes.

Grading is deliberately strict and deliberately narrow. It asks one question
per scenario -- *is this the right file?* -- because a scenario that checks
twenty things tells you nothing about which of them broke.
"""

from __future__ import annotations

import pathlib
import random
import tempfile
from dataclasses import dataclass, field

from solvent import certgen
from solvent.discovery import Compliance, ManualSource, Readiness
from solvent.harness import (
    CAPABILITIES, OWNER, Solvent, owner_promotions, promote_capability,
    run_business_cycle,
)
from solvent.types import Criticality, Requirement, RequirementSource

#: Used nowhere else. Distinct from DEVELOPMENT_SEED (what was built against),
#: CERTIFICATION_SEED (what the verifiers were certified on) and SURPRISE_SEED
#: (what ambushes the verifiers), so an exam scenario cannot coincide with
#: anything already seen.
EXAM_SEED = 30411277

#: Nine per capability, five capabilities: 45.
PER_CAPABILITY = 9

#: Checks no evidence can certify are not examinable either. Asking a scenario
#: to prove `number_format` would be grading a question the system has already
#: said it cannot answer.
from solvent.harness import UNCERTIFIABLE  # noqa: E402  (after the constants)


#: What the correct outcome for a scenario is. Not every job should end in a
#: delivery: a source the client left ambiguous should end in a question, and
#: a scenario that treated the question as a failure would be rewarding a
#: system that guessed.
DELIVER, ASK = "DELIVER", "ASK"


def expected_outcome(capability: str, case) -> str:
    """Should this job produce a file, or a question?

    Read off the case's own ground truth, before the run. csv-cleanup is the
    one that varies: `_date` in certgen deliberately emits dates like
    05/10/2026, which denote two different days, and marks which rows are
    unambiguous. If any row is not, nobody knows what the right answer is --
    including the generator, whose `good_output` picked one reading when it
    built the source.
    """
    if capability == "csv-cleanup":
        # Asked of the source itself, not of the generator's flag. `certgen`
        # marks a date ambiguous whenever the day is 12 or under, which is
        # the right rule for *building defects* and the wrong one here:
        # 10/10/2025 is the tenth of October under either convention, so
        # there is nothing to ask and a system that asked would be the
        # nuisance the clarification design warns about. The question is
        # whether a value genuinely denotes two days.
        import csv
        import io

        from solvent.ambiguity import date_readings

        text = (case.source_text
                or (case.source_bytes or b"").decode("utf-8", "replace"))
        rows = list(csv.reader(io.StringIO(text)))
        if rows and "Order Date" in rows[0]:
            column = rows[0].index("Order Date")
            for row in rows[1:]:
                if column < len(row) and len(date_readings(row[column])) > 1:
                    return ASK
    return DELIVER


@dataclass
class ScenarioResult:
    """One scenario's outcome, in the terms a reader can check."""

    id: str
    capability: str
    seed: int
    index: int
    source_bytes: int
    requirements: int
    expected: str = DELIVER
    passed: bool = False
    delivered: bool = False
    stopped_at: str = ""
    detail: str = ""
    wall_seconds: float = 0.0
    invoiced_cents: int = 0
    operating_cost_cents: int = 0

    @property
    def mark(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def line(self) -> str:
        return (f"{self.id:34s} {self.expected:7s} {self.mark:4s} "
                f"{self.wall_seconds:6.2f}s  {self.detail[:76]}")


@dataclass
class MatrixReport:
    name: str
    seed: int
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> list[ScenarioResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        return (f"{self.name} (seed {self.seed}): "
                f"{self.passed}/{len(self.results)} passed")


def _requirements_for(capability: str, case) -> list[Requirement]:
    """The checklist the client agreed to, from the generated case.

    Some generators hand over requirements already; the rest give a params
    dict keyed by check name, which is the same information in the shape the
    verifier wants. Either way the uncertifiable checks are dropped, because a
    promotion does not cover them and a job resting on one is refused at
    delivery -- grading that as a skill failure would be grading the wrong
    thing.
    """
    withheld = set(UNCERTIFIABLE.get(capability, {}))
    if case.requirements:
        return [r for r in case.requirements if r.check not in withheld]

    if capability == "report-builder":
        # A report's plan is its sections, and `params` cannot express it: it
        # carries one `report_section` entry because a verifier only ever
        # checks one section at a time, while the document the case builds has
        # three. Reconstructing them from the generator's own structure --
        # Overview, Lines, Totals -- is reading its intent, which is what a
        # client's brief would have said.
        sections = [
            ("R1", "report_section", {"kind": "facts", "heading": "Overview",
                                      "fields": ["client", "period",
                                                 "prepared_for", "approved_by"]}),
            ("R2", "report_section", {"kind": "table", "heading": "Lines",
                                      "columns": ["item", "amount"]}),
            ("R3", "report_section", {"kind": "total", "heading": "Totals",
                                      "column": "amount"}),
        ]
        properties = [(f"R{index}", check, dict(params or {}))
                      for index, (check, params)
                      in enumerate(sorted(case.params.items()), 4)
                      if check not in withheld and check != "report_section"]
        return [
            Requirement(id=rid, text=check,
                        source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
                        acceptance=check, check=check, params=params,
                        criticality=Criticality.MANDATORY)
            for rid, check, params in sections + properties]

    return [
        Requirement(
            id=f"R{index}", text=check,
            source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
            acceptance=check, check=check, params=dict(params or {}),
            criticality=Criticality.MANDATORY)
        for index, (check, params) in enumerate(sorted(case.params.items()), 1)
        if check not in withheld]


def _expected(case) -> bytes:
    # Truthiness, not `is not None`: a text case carries an empty bytes field
    # beside its populated text field, and reading the empty one handed the
    # grader a zero-byte expectation it then compared against real output.
    return case.good_output_bytes or (case.good_output or "").encode("utf-8")


def _source(case) -> bytes:
    return case.source_bytes or (case.source_text or "").encode("utf-8")


def _generate(capability: str, seed: int, index: int):
    """One case, from a stream this (capability, seed, index) owns alone."""
    import inspect

    rng = random.Random(f"{capability}:{seed}:{index}")
    generator = certgen.GENERATORS[capability]
    # `document_case` takes the index too, because it cycles three deliverable
    # shapes and needs to know which one this is. It has a default, so calling
    # it without the index silently produces shape zero every time -- nine
    # identical scenarios wearing nine different names.
    if "index" in inspect.signature(generator).parameters:
        return generator(rng, index)
    return generator(rng)


def _configured(box: pathlib.Path) -> Solvent:
    s = Solvent(str(box / "matrix.db"))
    for name in owner_promotions():
        promote_capability(s, name, owner_identity=OWNER,
                           why="skill matrix: cleared for client work")
    s.discovery.register_source(
        ManualSource("owner_entered", []), owner_identity=OWNER,
        readiness=Readiness.PERMITTED_AUTOMATION, compliance=Compliance.PERMITTED,
        determination="owner-entered: no platform terms, no external call")
    s.policy.amend({
        "discovery": {"approved_sources": ["owner_entered"]},
        "qualification": {"minimum_value_cents": 1_000,
                          "max_concurrent_jobs": 1},
        "egress": {"allowlist": [{"host": "client.example.com",
                                  "max_privacy": "CLIENT_CONFIDENTIAL",
                                  "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                   "simulation_only": True},
        # TEST-ONLY operating costs, so the margin the Governor checks is a
        # margin against something rather than against zero.
        "commercial": {"platform_fee_fraction": 0.10,
                       "payment_fee_fraction": 0.029,
                       "payment_fee_flat_cents": 30,
                       "compute_cents_per_cpu_hour": 50},
    }, OWNER, "skill matrix")
    return s


def run_scenario(capability: str, seed: int, index: int) -> ScenarioResult:
    """Generate one job, run it through the pipeline, grade the file."""
    case = _generate(capability, seed, index)
    requirements = _requirements_for(capability, case)
    expected = _expected(case)
    source_bytes = _source(case)

    result = ScenarioResult(
        id=f"{capability}/{seed}#{index:02d}", capability=capability,
        seed=seed, index=index, source_bytes=len(source_bytes),
        requirements=len(requirements),
        expected=expected_outcome(capability, case))

    if not requirements:
        result.detail = "the generator produced no checkable requirement"
        return result

    with tempfile.TemporaryDirectory() as raw:
        box = pathlib.Path(raw)
        s = _configured(box)
        try:
            source = box / (case.source_name or "source.dat")
            source.write_bytes(source_bytes)
            cycle = run_business_cycle(
                s, source_name="owner_entered", source_file=str(source),
                workdir=str(box), capability=capability,
                posting={"ref": result.id, "title": f"{capability} job",
                         "quoted_cents": 40_000, "needs": [capability],
                         "project_state": "CT", "client_ref": "client:matrix",
                         "body": "generated scenario", "deliverables": []},
                agreed_requirements=requirements,
                owner_accepts_quote=True, simulate_payment=True)

            result.stopped_at = cycle.stopped_at
            result.delivered = cycle.delivered
            result.wall_seconds = cycle.execution_wall_seconds
            result.invoiced_cents = cycle.invoiced_cents
            result.operating_cost_cents = cycle.operating_cost_cents

            if result.expected == ASK:
                # The source is ambiguous, so the right outcome is a question.
                # A delivery here would be a guess dressed as an answer.
                if cycle.delivered:
                    result.detail = ("delivered a file for a source nobody "
                                     "can read unambiguously — that is a guess")
                elif cycle.clarifications:
                    result.passed = True
                    result.detail = ("asked the client: "
                                     + cycle.clarifications[0][:60])
                else:
                    result.detail = (f"neither delivered nor asked — stopped "
                                     f"at {cycle.stopped_at}: "
                                     f"{cycle.why_stopped}")
                return result

            if not cycle.delivered:
                result.detail = (f"not delivered — stopped at "
                                 f"{cycle.stopped_at}: {cycle.why_stopped}")
                return result

            produced = pathlib.Path(cycle.deliverable_path).read_bytes()
            if _matches(capability, produced, expected):
                result.passed = True
                result.detail = "delivered the file that was correct for this input"
            else:
                result.detail = (f"delivered {len(produced)} bytes; the correct "
                                 f"output for this input is {len(expected)} bytes")
        except Exception as problem:  # a crash is a scenario failure, not an abort
            result.detail = f"{type(problem).__name__}: {problem}"[:300]
        finally:
            try:
                s.store.close()
            except Exception:
                pass
    return result


def _matches(capability: str, produced: bytes, expected: bytes) -> bool:
    """Is this the right answer?

    Byte equality for text. For the two ZIP formats it is the wrong question:
    two archives with identical content differ in timestamps and member order,
    so they are compared on what they *say* rather than on their bytes.
    """
    if produced == expected:
        return True
    # Line endings are not content. A CSV writer emits CRLF because RFC 4180
    # says to; the generator builds its expected rows with LF because it is
    # joining strings. Grading on that difference would fail every text
    # scenario for a reason that has nothing to do with the work.
    if _normalised(produced) == _normalised(expected):
        return True
    if capability == "spreadsheet-automation":
        from solvent import xlsx
        return _sheet_values(xlsx, produced) == _sheet_values(xlsx, expected)
    if capability == "document-processing":
        text_a, text_b = produced.decode("utf-8", "replace"), expected.decode(
            "utf-8", "replace")
        if text_a == text_b:
            return True
        try:
            from solvent import docx
            return (_doc_text(docx, produced) == _doc_text(docx, expected))
        except Exception:
            return False
    return False


def _normalised(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").rstrip(b"\n")


def _sheet_values(xlsx, raw: bytes):
    book = xlsx.read_workbook(raw)
    return [[cell.value for cell in row] for row in book.first.rows]


def _doc_text(docx, raw: bytes):
    document = docx.read_document(raw)
    return [p.text for p in document.paragraphs]


def run_matrix(name: str, seed: int, *, per_capability: int = PER_CAPABILITY
               ) -> MatrixReport:
    """Every capability, `per_capability` scenarios each, in a fixed order."""
    report = MatrixReport(name=name, seed=seed)
    for capability in sorted(CAPABILITIES):
        for index in range(1, per_capability + 1):
            report.results.append(run_scenario(capability, seed, index))
    return report
