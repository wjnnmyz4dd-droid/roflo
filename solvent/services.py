"""Service selection — which work Solvent sells, and the contract for doing it.

This is **strategic** selection: *what line of business should Solvent open
first?* It is not job economics. Every individual job is still priced and
approved by the Financial Governor, which remains the only financial authority.
A scorecard that started deciding whether a job was worth doing would be a second
Governor, so this one deliberately produces a ranking and nothing else.

A :class:`ServiceDefinition` is registered through the existing Capability &
Conformance registry rather than living anywhere new. Solvent does not become a
spreadsheet company by selling spreadsheets first; the service is simply the
first proven revenue path, and the registry is built to hold more.

**Why the weights look the way they do.** For a *first* service the goal is not
maximum revenue — it is proving the loop safely. Cheap verification, low
ambiguity and low liability therefore outweigh headline rate. A job that cannot
be verified cheaply is one Solvent refuses on economics anyway, so a service made
of such jobs is a service that never trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Dimension -> (weight, higher_is_better). Weights favour provability over rate.
DIMENSIONS: dict[str, tuple[float, bool]] = {
    "verifiability": (3.0, True),
    "client_ambiguity": (2.5, False),
    "liability": (2.5, False),
    "capability_fit": (2.5, True),
    "startup_cost": (2.0, False),
    "external_tool_dependency": (2.0, False),
    "revision_risk": (2.0, False),
    "completion_time": (1.5, False),
    "demand": (1.5, True),
    "repeatability": (1.5, True),
    "payment_risk": (1.5, False),
    "expected_margin": (1.0, True),
    "automation_potential": (1.0, True),
    "ai_api_cost": (1.0, False),
    "location_dependency": (1.0, False),
    "competition": (0.5, False),
}


@dataclass(frozen=True, slots=True)
class ServiceCandidate:
    """One possible line of business, scored 1–5 on each dimension."""

    id: str
    name: str
    scores: dict[str, int]
    note: str = ""

    def score(self) -> float:
        total = 0.0
        for dimension, (weight, higher_better) in DIMENSIONS.items():
            raw = self.scores.get(dimension, 3)
            value = raw if higher_better else (6 - raw)
            total += weight * value
        return round(total, 1)


#: Candidates considered. Scores are judgements, stated so they can be argued
#: with rather than hidden in prose. Demand evidence for these niches is weak —
#: general 2026 freelance reporting covers broad categories, not these — so
#: `demand` is scored conservatively and weighted low on purpose.
CANDIDATES: tuple[ServiceCandidate, ...] = (
    ServiceCandidate(
        "spreadsheet_cleanup", "Spreadsheet / CSV cleanup with computed totals",
        {"verifiability": 5, "client_ambiguity": 2, "liability": 2,
         "capability_fit": 5, "startup_cost": 1, "external_tool_dependency": 1,
         "revision_risk": 2, "completion_time": 1, "demand": 3, "repeatability": 4,
         "payment_risk": 2, "expected_margin": 3, "automation_potential": 4,
         "ai_api_cost": 2, "location_dependency": 1, "competition": 4},
        "Deliverable is machine-checkable: totals reconcile, schema validates, the "
        "file opens. Verification is arithmetic, not opinion."),
    ServiceCandidate(
        "pdf_data_extraction", "PDF → structured data extraction",
        {"verifiability": 4, "client_ambiguity": 3, "liability": 3,
         "capability_fit": 4, "startup_cost": 2, "external_tool_dependency": 3,
         "revision_risk": 3, "completion_time": 2, "demand": 4, "repeatability": 4,
         "payment_risk": 2, "expected_margin": 3, "automation_potential": 5,
         "ai_api_cost": 3, "location_dependency": 1, "competition": 3},
        "Checkable against the source document, but parsing untrusted PDFs is the "
        "malicious-document surface, and client data is often sensitive."),
    ServiceCandidate(
        "document_formatting", "Business document formatting and templating",
        {"verifiability": 3, "client_ambiguity": 4, "liability": 2,
         "capability_fit": 4, "startup_cost": 1, "external_tool_dependency": 2,
         "revision_risk": 4, "completion_time": 2, "demand": 3, "repeatability": 3,
         "payment_risk": 2, "expected_margin": 2, "automation_potential": 3,
         "ai_api_cost": 2, "location_dependency": 1, "competition": 4},
        "'Make it look professional' is taste, not a specification. High revision "
        "risk and verification is subjective."),
    ServiceCandidate(
        "research_summary", "Research summaries and briefings",
        {"verifiability": 2, "client_ambiguity": 4, "liability": 4,
         "capability_fit": 4, "startup_cost": 1, "external_tool_dependency": 2,
         "revision_risk": 4, "completion_time": 3, "demand": 4, "repeatability": 3,
         "payment_risk": 3, "expected_margin": 3, "automation_potential": 3,
         "ai_api_cost": 4, "location_dependency": 1, "competition": 4},
        "Hard to verify objectively and a wrong fact carries real liability. A "
        "hallucination is invisible to a deterministic check."),
    ServiceCandidate(
        "presentation_creation", "Slide deck creation",
        {"verifiability": 2, "client_ambiguity": 5, "liability": 2,
         "capability_fit": 3, "startup_cost": 1, "external_tool_dependency": 3,
         "revision_risk": 5, "completion_time": 3, "demand": 3, "repeatability": 2,
         "payment_risk": 3, "expected_margin": 2, "automation_potential": 2,
         "ai_api_cost": 3, "location_dependency": 1, "competition": 4},
        "Highest revision risk of the set: design is iterative by nature."),
    ServiceCandidate(
        "simple_coding_task", "Small, well-specified coding tasks",
        {"verifiability": 5, "client_ambiguity": 3, "liability": 4,
         "capability_fit": 4, "startup_cost": 1, "external_tool_dependency": 2,
         "revision_risk": 3, "completion_time": 3, "demand": 4, "repeatability": 3,
         "payment_risk": 2, "expected_margin": 4, "automation_potential": 4,
         "ai_api_cost": 3, "location_dependency": 1, "competition": 3},
        "Tests make verification objective, and it pairs with the bounty channel. "
        "But shipped code carries liability, and many projects now restrict "
        "AI-generated contributions."),
    ServiceCandidate(
        "data_processing_automation", "Recurring data-processing automation",
        {"verifiability": 4, "client_ambiguity": 3, "liability": 4,
         "capability_fit": 3, "startup_cost": 2, "external_tool_dependency": 4,
         "revision_risk": 3, "completion_time": 4, "demand": 4, "repeatability": 5,
         "payment_risk": 2, "expected_margin": 4, "automation_potential": 5,
         "ai_api_cost": 3, "location_dependency": 1, "competition": 3},
        "Excellent repeat revenue, but it means running in a client's environment "
        "and owning an ongoing failure mode. Not a first job."),
    ServiceCandidate(
        "business_analysis", "Business analysis and reporting",
        {"verifiability": 2, "client_ambiguity": 5, "liability": 5,
         "capability_fit": 3, "startup_cost": 1, "external_tool_dependency": 2,
         "revision_risk": 4, "completion_time": 4, "demand": 4, "repeatability": 2,
         "payment_risk": 3, "expected_margin": 4, "automation_potential": 2,
         "ai_api_cost": 4, "location_dependency": 2, "competition": 3},
        "A client acting on a wrong analysis is the highest-liability option here."),
)


def scorecard() -> list[tuple[ServiceCandidate, float]]:
    """Every candidate, best first. Deterministic: same input, same order."""
    return sorted(((c, c.score()) for c in CANDIDATES),
                  key=lambda pair: (-pair[1], pair[0].id))


def recommended() -> tuple[ServiceCandidate, ServiceCandidate]:
    """``(service_1, backup)`` by score."""
    ranked = scorecard()
    return ranked[0][0], ranked[1][0]


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    """The contract for one sellable service.

    Registered through the Capability registry. It constrains what Solvent will
    accept; it does **not** price anything — the Governor does that per job, from
    jurisdiction-aware reference data.
    """

    id: str
    name: str
    description: str
    accepted_inputs: tuple[str, ...]
    deliverables: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    conformance_rules: tuple[str, ...]
    supported_formats: tuple[str, ...]
    unsupported_requests: tuple[str, ...]
    verification_requirements: tuple[str, ...]
    pricing_inputs: tuple[str, ...]
    risk_factors: tuple[str, ...]
    scope_boundaries: tuple[str, ...]
    rejection_conditions: tuple[str, ...]
    owner_approval_triggers: tuple[str, ...]
    expected_resources: str = ""

    def conforms(self, *, requested_formats: list[str],
                 requested: list[str]) -> tuple[bool, str]:
        """Does this request fall inside the service? Unknown is not conforming."""
        unsupported = [f for f in requested_formats
                       if f.lower() not in self.supported_formats]
        if unsupported:
            return False, f"unsupported format(s): {', '.join(unsupported)}"
        for ask in requested:
            for barred in self.unsupported_requests:
                if barred.lower() in ask.lower():
                    return False, f"outside the service: {barred}"
        if not requested:
            return False, "no stated request to conform to"
        return True, "within the service definition"


#: The recommended first service, as a contract. Nothing here sets a price.
SPREADSHEET_CLEANUP = ServiceDefinition(
    id="spreadsheet_cleanup",
    name="Spreadsheet / CSV cleanup with computed totals",
    description=(
        "Take a messy tabular file, normalise it into a clean sheet with "
        "consistent headers and types, and add the client's stated computed "
        "columns or category totals."),
    accepted_inputs=("a tabular file", "a written statement of the columns and "
                     "totals wanted"),
    deliverables=("a cleaned .xlsx or .csv", "a short note of what was changed",
                  "the reconciliation check output"),
    required_capabilities=("spreadsheet",),
    conformance_rules=(
        "the client states the columns and totals wanted, in writing",
        "the input file is supplied before work begins",
        "row count and money columns can be reconciled against the input"),
    supported_formats=("csv", "xlsx", "tsv"),
    unsupported_requests=(
        "financial advice", "tax filing", "audit opinion", "legal review",
        "forecast", "valuation", "macro", "vba"),
    verification_requirements=(
        "T1: output parses and opens",
        "T1: row count reconciles with the input, less any rows the client asked "
        "to drop",
        "T1: every computed total recomputes to the same value",
        "T3: client confirmation before the job is complete"),
    pricing_inputs=("row count", "column count", "number of computed totals",
                    "input cleanliness", "jurisdiction for any labour line"),
    risk_factors=("client supplies a file that is not really tabular",
                  "ambiguous category definitions",
                  "personal or financial data inside the file"),
    scope_boundaries=(
        "cleaning and computing only — no interpretation of what the numbers mean",
        "one round of corrections for genuine errors against the stated spec",
        "no ongoing maintenance"),
    rejection_conditions=(
        "no input file supplied", "no written statement of the totals wanted",
        "the request needs advice rather than computation",
        "the file contains data the client has not agreed may be processed"),
    owner_approval_triggers=(
        "value above the policy threshold",
        "the file appears to contain special-category personal data",
        "the client asks for anything on the unsupported list"),
    expected_resources="minutes of compute; no paid third-party tool required")
