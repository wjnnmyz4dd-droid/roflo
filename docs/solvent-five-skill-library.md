# The five-skill library

Status: implemented and certified against generated evidence. One of the five
is cleared by the owner for client work; four are not, and that is a decision
rather than an omission.

## What exists

| # | Capability | Version | Does | Delivers |
|---|---|---|---|---|
| 1 | `csv-cleanup` | 1.0 | tidies a delimited file to a stated specification | `.csv` |
| 2 | `report-builder` | 1.0 | renders supplied facts as a document, inventing none | `.md` |
| 3 | `spreadsheet-automation` | 1.0 | transforms a workbook by arithmetic nobody guessed | `.xlsx` |
| 4 | `data-entry` | 1.0 | maps records into a schema and holds the ones that fail its rules | `.csv` + exceptions `.csv` |
| 5 | `document-processing` | 1.0 | extracts, converts and formats documents without inventing their content | `.json`, `.md`, `.txt`, `.docx` |

Each is a **worker** and an **independent verifier**. The verifier does not
import the worker: it reads the source and the deliverable, works out for
itself what the committed requirements demanded, and says whether the output
satisfies them. Where both sides need to parse a number, a date, an email
shape or a label, each has its own implementation written to the same
specification by a different route — so the two either agree exactly or
disagree meaningfully.

No capability uses a model. Every figure any of them produces is computed by
arithmetic in its own module, from values in the input, by a rule a
requirement named.

## What certification says, and what it does not

Certification is measured, not asserted. Each verifier is put through a
battery generated from a recorded seed: the correct answer is built *before*
the question, each defective case carries exactly one defect at a position and
value the generator chose, and the trials never say which is which.

At the current head, through the production path
(`harness.ensure_verifier_certified`):

| Capability | State | Checks certified | Of the checks it lists |
|---|---|---|---|
| `csv-cleanup` | CERTIFIED | 11 | 11 |
| `report-builder` | CERTIFIED | 8 | 8 |
| `spreadsheet-automation` | CERTIFIED | 14 | 15 |
| `data-entry` | CERTIFIED | 16 | 16 |
| `document-processing` | CERTIFIED | 14 | 14 |

A check is credited only when it was right in both directions over enough
distinct evidence to mean something: ten distinct correct artifacts accepted,
every defect class it was shown caught, and five distinct instances of each
class. A check nobody put a defect in front of is not certified, however many
correct artifacts it approved.

**It does not mean a verifier is correct.** A defect class nobody modelled is
not in the battery. The honest reading is "met the stated evidence
requirements", and the stated requirements are the limit of the claim.

### The one check no evidence can certify

`spreadsheet-automation.number_format`. A display format changes how a figure
is shown and not what it is, so there is no value to recompute and nothing a
wrong answer would look like; the verifier answers UNVERIFIABLE by design.
`harness.UNCERTIFIABLE` records it with that reason, `harness.certifiable_scope`
excludes it, and a promotion claiming it is refused.

`consolidate_sheets` was in that list and is not any more, and the difference
matters. It used to confirm only that the named worksheets existed and leave
the rest to `row_reconciliation` — a check that could only ever say yes. The
fix was not an exemption: it was giving it the question it should always have
owned, *which* worksheets the delivered rows came from. Reconciliation cannot
answer that, because the source side it compares against is built from this
very requirement, so a job that stacked one quarter twice instead of two
different quarters satisfied it. An exemption would have left that client
receiving half their data twice.

## Promotion: one of five

| Capability | Owner decision |
|---|---|
| `csv-cleanup` | **APPROVED** — registered, proven, cleared for client work |
| `report-builder` | LIMITED — build and test only |
| `spreadsheet-automation` | LIMITED — build and test only |
| `data-entry` | LIMITED — build and test only |
| `document-processing` | LIMITED — build and test only |

"The owner said build and test this" and "the owner said sell this" are
different sentences, and the registry keeps them apart. The four LIMITED
capabilities are technically certified and cannot be used for client delivery:
delivery asks the registry for permission and the registry has no promotion to
show it. Clearing any of them is the owner's act and nothing in this codebase
performs it.

## Where each one stops

Stated here rather than discovered by a client.

**csv-cleanup** does not convert between date conventions it was not told
about; an ambiguous date is a question, not a guess.

**report-builder** renders facts it was handed. It computes no figure the
facts do not support and discloses every field that was not supplied.

**spreadsheet-automation** does charts, pivot tables, macros, conditional
formatting, cell merging and images not at all, and its arithmetic vocabulary
is five operations (sum, difference, product, quotient, percentage). A
computed cell carries a number *and* an Excel formula, checked separately: a
right number beside a formula pointing at the wrong column is a defect the
arithmetic check passes and the formula check catches.

**data-entry** reads CSV and JSON, not spreadsheets. A date means ISO 8601 and
a real calendar day; anything else makes the record an exception rather than
being reformatted. Its email check is structural — it says the string is
shaped like an address, not that anybody is there.

**document-processing** reads `.docx`, `.txt` and `.md`. Fifteen formats are
refused by name with a reason each, and PDF is the one worth reading: a PDF's
text lives in content streams whose character mapping depends on the embedded
fonts, so extraction from an arbitrary PDF is unreliable in ways that do not
show in the output — ligatures merge, columns interleave, a scanned page has
no text at all. An unreliable extraction of a client's invoice is worse than a
refusal, because the refusal is visible. Converting the PDF to `.docx` first,
or supplying the original document, both work.

Plain text cannot express a heading level or a table. Converting to `.txt`
carries the heading text and the cells through and records both structures as
not carried; the checks that would ask about them answer UNVERIFIABLE with
that reason rather than failing work the worker could not have done
differently.

## The two format layers

`.xlsx` and `.docx` are both ZIP archives of XML, so `solvent/xlsx.py` and
`solvent/docx.py` reach them with `zipfile` and `xml.etree.ElementTree` alone.
Solvent imports only the standard library and the Windows installer builds a
virtual environment with no packages in it; a dependency would mean network
access at install time, a new supply chain, and a new thing that can be
unavailable on the owner's machine.

Both are cross-validated against another library as a **test-only**
dependency — `openpyxl` and `python-docx` — in both directions, because a
round trip through one implementation proves only that it agrees with itself.

Both sides of a capability read through the same format layer, exactly as both
read CSV through the standard library's `csv`. A bug *there* would fool both,
which is the one way the two-sided design can fail, and it is why each layer
carries its own adversarial suite rather than being covered incidentally.

`solvent/docx.py` handles tracked changes rather than ignoring them. A
document with a struck-out price contains two prices, and a reader that
collects every text run returns the one the author deleted.

## Evidence

- `tests_solvent/test_five_skill_library.py` — the certification record as a
  test: the library is five, each verifier reaches CERTIFIED through the
  production path, every certified check clears the evidence floor, a third
  seed ambushes each one, the Skills Lab lifecycle holds, and promotion stays
  the owner's.
- `tests_solvent/test_csv_capability.py`, `test_report_capability.py`,
  `test_sheet_capability.py`, `test_entry_capability.py`,
  `test_document_capability.py` — the per-capability suites, each with
  negative controls that take a correct delivery, put one defect in it, and
  assert which check notices.
- `tests_solvent/test_xlsx_format.py`, `test_docx_format.py` — the format
  layers and their cross-validation.
- `tests_solvent/blackbox/scenarios.py` — the black-box run. Every capability
  faces ordinary work at three complexity levels, boundaries, hostile input,
  false-success traps and real client behaviour, graded against ground truth
  written before any run.
