# DeskPilot — intelligence and capability gap audit

Read-only audit. No production code was modified, no dependency was
installed, no repository was integrated, no payment account connected,
nothing deployed. Evidence below was produced by reading the code and
by running probes against the existing workers from a scratch
directory outside the repository.

**Status vocabulary.** VERIFIED COMPLETE means tested against evidence
that could have failed. IMPLEMENTED BUT UNPROVEN means the code exists
and works in simulation with no real-world instance. PARTIALLY
IMPLEMENTED means a real subset works and a named part does not.
MISSING means no implementation. EXTERNALLY BLOCKED means implemented
to the boundary and stopped by somebody else's decision. NOT
APPLICABLE means the requirement does not apply.

A class existing is not a status. Every judgement below cites a file.

---

## A. Executive findings

**The answer to the closing question is no, and the reason is narrower
than it looks.**

If five genuine paying assignments arrived today — one per skill —
DeskPilot would very likely **refuse several of them at intake**, not
botch them. What it accepts, it executes correctly and checks
independently. What it cannot accept, it declines with a clear reason.
That is the right failure mode and it is still a failure to earn.

Four findings drive everything else.

1. **The acceptance surface is narrow and measurable.** Of 13
   realistic client inputs probed, 2 were accepted
   (`solvent/csvwork.py:213`, `solvent/docwork.py:81-105`). CSV is
   UTF-8-only and comma-only; PDF is not read at all.
2. **The biggest single gap is PDF.** Document processing reads
   `.docx`, `.txt`, `.md` and nothing else. PDF invoices and
   statements are the commonest real document job and produce an
   immediate refusal (`solvent/docwork.py:91`).
3. **One checker is not independent.** `solvent/reportverify.py:21`
   imports `load_source` from the worker it checks, and all six of its
   checks parse through it.
4. **Payment is at the first rung of its own ladder.**
   `operational_status: SELECTED`, `money_can_move: false`. Nothing
   has ever been received.

The smallest set of improvements that would change the answer is in
section W. It is four items, three of which need **no new
dependency**.

---

## B. Verified repository baseline

| | |
|---|---|
| Branch | `claude/solvent-architecture-audit-8ot9et` |
| HEAD | `5111815` |
| Working tree | clean |
| Tests | 4,647 passing, 0 failing, 1 skipped (passes standalone) |
| Mutation | solvent 143/143; installer 194/194 |
| Sealed exam | sixth sitting, 6/6, seal intact |

Verified by running them, not by reading a previous report.

---

## C. Full architecture and intelligence inventory

| Component | Status | Source |
|---|---|---|
| Capacity gate, eight limits | VERIFIED COMPLETE | `solvent/capacity.py` |
| Acquisition channels, bid freeze | IMPLEMENTED BUT UNPROVEN | `solvent/acquisition.py` |
| Five deterministic skills | PARTIALLY IMPLEMENTED | see G |
| Five checkers | PARTIALLY IMPLEMENTED | see H |
| Payment monitor (read-only) | IMPLEMENTED BUT UNPROVEN | `solvent/paymentmonitor.py` |
| Channel health | IMPLEMENTED BUT UNPROVEN | `solvent/channelhealth.py` |
| Operations supervision | IMPLEMENTED BUT UNPROVEN | `solvent/supervision.py` |
| Notifications, two routine + summaries | IMPLEMENTED BUT UNPROVEN | `solvent/briefing.py` |
| Authority separation, audit chain | VERIFIED COMPLETE | `solvent/store.py`, `solvent/audit.py` |
| Action Gate / egress guard | VERIFIED COMPLETE | `solvent/egress.py` |
| Probation | VERIFIED COMPLETE | `solvent/policy.py:670` |
| Crash recovery | VERIFIED COMPLETE | `solvent/runtime.py`, crash matrix |
| Worker knowledge bases | MISSING | no module exists |
| Checker knowledge bases | MISSING | no module exists |
| OCR | MISSING | no module exists |

"IMPLEMENTED BUT UNPROVEN" is used wherever the only evidence is
simulation. That is most of the newer work and is stated rather than
rounded up.

---

## D. Marketplace operational intelligence

| Capability | Status | Source |
|---|---|---|
| Permission model, per activity | VERIFIED COMPLETE | `solvent/sourcecatalog.py` |
| Freelancehunt discovery adapter | IMPLEMENTED BUT UNPROVEN | `solvent/marketplaces.py:290+` |
| Live listing retrieval | VERIFIED COMPLETE (one request) | 10 live projects, 2026-10-15 |
| Bid submission | EXTERNALLY BLOCKED | clause 3.1.11 |
| Contract acceptance | EXTERNALLY BLOCKED (unread terms) | `terms_for(ACCEPT_CONTRACT)` = UNDETERMINED |
| Account health monitoring | IMPLEMENTED BUT UNPROVEN | `solvent/channelhealth.py` |
| Competitor/market rate data | MISSING | no source; `market_hourly_cents` empty |

**What is genuinely absent:** DeskPilot has no view of what work is
*available* over time. It can read the board once; it has no
historical record of volume, price distribution, or how quickly
postings are taken. Every demand figure in every report so far is a
what-if for that reason.

---

## E. Pricing and bidding intelligence

| Capability | Status | Source |
|---|---|---|
| Cost-based floor | VERIFIED COMPLETE | `solvent/finder.py:624` |
| Hourly floor on occupied time | VERIFIED COMPLETE | `solvent/finder.py:650-660` |
| Minimum fee, margin, platform fee floor | VERIFIED COMPLETE | `solvent/policy.py` commercial |
| Exchange rate with mandatory citation | IMPLEMENTED BUT UNPROVEN | `solvent/cli.py` `--exchange-rate` |
| Market rate evidence | MISSING | `market_hourly_cents` empty, citation required |
| Win-rate model | MISSING | zero observations |
| Competitor pricing | MISSING | no source |

The pricing engine is sound and **has no market data to price
against**. It prices from the owner's own rate and its own costs,
which is defensible and is not market intelligence.

---

## F. Direct-hire intelligence

| Capability | Status | Source |
|---|---|---|
| Channel classification | VERIFIED COMPLETE | `solvent/acquisition.py:classify` |
| Competitive-bid freeze | VERIFIED COMPLETE | `acquisition.py:BID_FREEZE`, mutant-killed |
| Standing authority, bounded | VERIFIED COMPLETE | `acquisition.py:StandingAuthority` |
| `is_personal` invitation detection | IMPLEMENTED BUT UNPROVEN | `marketplaces.py`, fixture only |
| Unattended acceptance of an invitation | EXTERNALLY BLOCKED | platform terms unread |
| Inbound enquiry handling | PARTIALLY IMPLEMENTED | `solvent/clientrelations.py` prepares, never sends |

**The honest position:** the direct-hire *mechanism* is complete and
the direct-hire *channel* has never carried a job. No invitation has
ever been received, because no account exists.

---

## G. Five worker capability audits

Probed by feeding realistic inputs to each worker from outside the
repository. Operation counts are the committed check vocabulary from
`solvent/checks.py`.

### csv-cleanup — PARTIALLY IMPLEMENTED
`solvent/csvwork.py` (569 lines), 11 operations.

Works: duplicate removal, header renaming, whitespace trimming, date
normalisation, value mapping, sorting, column requirements, row
reconciliation, and an explicit no-unauthorised-changes check.

**Measured limits:**
- **UTF-8 only.** `csvwork.py:213` opens with `utf-8-sig`. A cp1252
  file containing `Café` refuses: `'utf-8' codec can't decode byte
  0xe9`. The same text as UTF-8 is accepted.
- **Comma only.** Semicolon- and tab-separated files refuse at
  `csvwork.py:428` with "every row has exactly one field". Semicolon
  is the default CSV delimiter across much of continental Europe.
- **No locale numbers.** `1.234,56` passes through untouched only
  because nothing computes on it; no check interprets it.
- Correctly refuses a `.xlsx` renamed `.csv` (`csvwork.py:224`) — a
  genuine safety win, not a gap.

### spreadsheet-automation — PARTIALLY IMPLEMENTED
`solvent/sheetwork.py` (772 lines), 15 operations.

Works: column computation, sheet consolidation, totals, number
formats, formula agreement, column selection, empty-row removal.

**Absent:** pivot tables, cross-sheet lookup (the VLOOKUP shape),
charts, conditional formatting, multi-criteria aggregation. A client
saying "automate my spreadsheet" most often means one of the first
two.

### document-processing — PARTIALLY IMPLEMENTED
`solvent/docwork.py` (804 lines), 14 operations.

Works on `.docx`, `.txt`, `.md`: field extraction with per-field
evidence, table extraction, template filling, format conversion,
heading structure, and explicit `NOT_FOUND` / `AMBIGUOUS` markers
rather than guesses.

**Absent and decisive: PDF.** `docwork.py:91` declares `.pdf`
unreadable with an accurate reason (text lives in content streams
with arbitrary character mapping; scanned pages have no text at all).
`.doc`, `.rtf`, `.odt`, `.pages` likewise. **No OCR anywhere in the
package.**

### data-entry — PARTIALLY IMPLEMENTED
`solvent/entrywork.py` (818 lines), 16 operations — the richest
validation vocabulary of the five.

Works: field mapping, type/range/pattern/enum validation, required
fields, deduplication, case and whitespace normalisation, constant
fields, record traceability, and a justified exceptions file.

**Absent:** fuzzy matching against a reference list, address
normalisation, similarity-based deduplication. Also inherits the CSV
encoding and delimiter limits above.

**Measured performance risk:** verification is superlinear.
553 bytes verifies in 14.9 ms; 69 KB in 2,710 ms —
126× the input for ~180× the time, curve steepening
(`docs/evidence/representative-benchmarks.json`). A megabyte-scale
client file is a deadline risk, not a throughput footnote.

### report-builder — VERIFIED COMPLETE, within a deliberately narrow scope
`solvent/reportwork.py` (260 lines), 8 operations.

Renders supplied facts into sections with totals and names what is
absent. It has no mechanism to produce a number, name or date that
was not supplied. That is the design and it is correct.

**Scope limit, by design:** "write me a report about our sales" is not
this capability. "Here are the figures, lay them out and tell me
what's missing" is.

---

## H. Five independent checker audits

Independence was checked by looking at imports, not at docstrings.

| Checker | Independent of its worker? | Source |
|---|---|---|
| `csvverify.py` | **Yes** — no worker import | verified |
| `sheetverify.py` | **Yes** | verified |
| `docverify.py` | **Yes** | verified |
| `entryverify.py` | **Yes** | verified |
| `reportverify.py` | **No** | `reportverify.py:21` |

**The one defect.** `solvent/reportverify.py:21` does
`from .reportwork import load_source`, and all six of its checks
(lines 118, 209, 267, 326, 354, 412) parse the source through it. If
that parser misreads a source, the worker and the checker misread it
identically and every check passes on the same wrong facts.

Blast radius is narrow — report-builder only, and `load_source` is a
simple JSON/CSV read — but this is exactly the class of fault the
whole verification design exists to prevent, and it should not be
described as independent verification until it is fixed.

Tier machinery (`solvent/independent.py`) supports T0–T3 with a
differential-review path that recomputes from source using a separate
reader. Status: IMPLEMENTED BUT UNPROVEN against real client work.

---

## I. GitHub repository comparison

Researched, compared, **not installed and not integrated**. Licensing
is weighted heavily because DeskPilot is a commercial service.

### PDF text extraction

| Candidate | Licence | Strength | Against |
|---|---|---|---|
| **pdfplumber** | MIT | best table reconstruction; spatial analysis rather than ruled lines | slowest; whitespace-aligned tables need tuning |
| **PyMuPDF** | **AGPL-3.0** | fastest by a wide margin; has `find_tables` | AGPL obliges open-sourcing network-served code unless a commercial licence is bought |
| **pypdf** | BSD | lightweight, most-installed | limited tables; no OCR |

**Assessment.** PyMuPDF's speed is irrelevant here — the measured
bottleneck is verification, not extraction — and its AGPL terms are a
real commercial hazard for a service the owner intends to sell. On
licence and table quality, **pdfplumber (MIT) is the better candidate**,
with pypdf (BSD) as a lighter fallback if only flat text is needed.

**Licences verified from the repositories' own licence files**, not
from the blog comparisons: `pdfplumber/LICENSE.txt` reads "MIT
License"; `PyMuPDF/COPYING` reads "GNU AFFERO GENERAL PUBLIC
LICENSE"; `charset_normalizer/LICENSE` reads "MIT License". That
matters because the licence is the part of this recommendation that
cannot be changed by a benchmark.

Published benchmark figures conflict between sources and none is
independently verified; they are not a basis for a decision. The
decision basis should be a trial on the owner's own PDFs.

**None of the three does OCR.** Scanned PDFs need Tesseract or a
vision model, which is a separate decision with its own cost and its
own accuracy question.

### Encoding detection

| Candidate | Licence | Note |
|---|---|---|
| **charset-normalizer** | MIT | claims 98% / 10 ms; that figure is from its own suite |
| **chardet** | LGPL-2.1 | claims 96.8% vs 89.1% the other way on *its* suite; reports language |
| **stdlib ladder** | — | no dependency at all |

Each project's benchmark comes from its own test data, so neither is
independent evidence of the other's accuracy.

**Measured here:** a three-step stdlib ladder
(`utf-8-sig` → `utf-16` → `cp1252`) correctly decoded all five
encodings tested, including accented cp1252 and UTF-16, with **no new
dependency**. For a service that should *declare* its assumption
rather than silently guess, the ladder plus a stated assumption in the
job report is the better fit than a probabilistic detector.

### Delimiter detection

`csv.Sniffer` is in the standard library and is **already available**.
Measured: it correctly identified `,` `;` `\t` and `|` on the exact
inputs that currently refuse. Zero dependency cost.

---

## J. Architectural improvement opportunities

| Opportunity | Status now | Note |
|---|---|---|
| Intake format negotiation | MISSING | nothing tells a client "send it as X and I can do it" |
| Shared input-normalisation layer | MISSING | encoding and delimiter logic would otherwise be duplicated per skill |
| `reportverify` independence | PARTIALLY IMPLEMENTED | one import to remove |
| Demand history store | MISSING | no record of what the board offered over time |

Nothing here argues for a rewrite. The architecture — authorities,
gates, workers that decide nothing, checkers that recompute — held up
under every probe in this audit.

---

## K. Software subscriptions and licensing

Current declared dependencies (`pyproject.toml`): fastapi, uvicorn,
httpx, pydantic. Optional: openpyxl, python-docx (verification extra);
transformers/torch/accelerate (local extra); pytest (dev).

All permissive. **No subscription is required to run DeskPilot
today.** Recurring costs are the VPS (~$98/month, owner-stated) and,
if activated, marketplace and payment fees.

If PDF is added: pdfplumber MIT adds no licence obligation; **PyMuPDF
AGPL-3.0 would**, and should not be adopted without the owner
deciding about the commercial licence.

---

## L. Worker knowledge bases — MISSING

There is no module. Workers hold no reference data: no country or
currency tables, no date-format catalogue, no address formats, no
industry field-name synonyms.

This is partly deliberate — a worker that "knows" a default is a
worker that can invent one — but it is why `map_fields` requires the
client to state every mapping, and why a header called `Cust. Ref.`
is not recognised as `Customer Reference` without being told.

---

## M. Checker knowledge bases — MISSING

No module. Checkers verify against the committed requirements only,
which is the correct authority model: the contract is the standard.

The gap is in **acceptance criteria the client did not state**. If a
client says "clean this file" and the agreed requirements miss
something a professional would check anyway, nothing catches it. The
requirement set is the ceiling on verification.

---

## N. Controlled self-learning — PARTIALLY IMPLEMENTED

`solvent/finder.py:170` `ExecutionHistory` records observed execution
times per capability and reads empty as "unknown", never as fast.
`NOT_CLIENT_WORK = ("BENCHMARK",)` excludes benchmark rows from the
history that prices work — so generated timings cannot become
delivery promises.

No adaptive path writes policy, authorisation or commercial rules;
that boundary holds.

**What is missing:** learning from outcomes rather than durations. No
record of revision frequency, client acceptance, dispute rate or
deadline accuracy, because there have been none.

---

## O. Proof-of-capability certification — IMPLEMENTED BUT UNPROVEN

`tests_solvent/skillmatrix.py` plus five certification suites run each
skill against generated cases with known-correct answers, including
cases whose correct outcome is a *question* rather than a delivery.
`solvent/certgen.py` builds the source and its correct output in the
same step from a seed.

**What it proves:** the skills do what they claim on inputs DeskPilot
generated. **What it does not prove:** behaviour on a client's file.
Zero client files have been processed.

---

## P. Realistic performance and throughput

From `docs/evidence/representative-benchmarks.json`:

| Skill | Input | Execute p50 | Verify p50 |
|---|---|---|---|
| csv-cleanup | 944 B | 1.2 ms | 1.6 ms |
| csv-cleanup | 92 KB | 61.5 ms | 119.8 ms |
| data-entry | 553 B | 0.7 ms | 14.9 ms |
| data-entry | 69 KB | 53.9 ms | 2,709.8 ms |

Three skills could not be scaled at all (`.xlsx`/`.docx` are zip
containers; report-builder takes one JSON object), so their rows stay
synthetic and overstate throughput.

Throughput is not the constraint: ~1,303 jobs/hour on one worker at
the slowest measured skill, against a target needing 1–2. **Demand is
the constraint, and it has zero observations.**

---

## Q. Stripe and payment activation — EXTERNALLY BLOCKED

Measured state: `operational_status: SELECTED`, `rail: ""`,
`money_can_move: false`, `next_state: ENGINEERING_READY`. Crypto
receipt is empty on every field.

`solvent/policy.py:820` enforces a forward-only ladder and refuses to
record CONFIGURED or LIVE_VERIFIED from inside the repository, because
those are claims about a credential in somebody's secret store and a
payment that actually arrived — things Solvent cannot see. Correct,
and it means **payment confirmation cannot be proven here at all**.

---

## R. Marketplace permissions and acceptance

| Activity | Freelancehunt | Freelancer | Others |
|---|---|---|---|
| Public discovery | PERMITTED | FORBIDDEN §33 | forbidden/unread |
| Authenticated discovery | PERMITTED | FORBIDDEN | unread |
| Prepare bid | PERMITTED | FORBIDDEN | unread |
| **Submit bid** | **FORBIDDEN 3.1.11** | **FORBIDDEN §33** | unread |
| **Accept contract** | **UNDETERMINED** | unread | unread |
| Send message | UNDETERMINED | unread | unread |

**The single highest-value unknown in this audit** is Freelancehunt's
position on automated contract acceptance. It is what separates
discover-and-notify from unattended direct hire, and it is a question
for the platform, not a thing to build.

---

## S. First-party acquisition feasibility

| Route | Status | Note |
|---|---|---|
| Owner-entered | VERIFIED COMPLETE | needs no platform permission |
| Direct client enquiry | PARTIALLY IMPLEMENTED | intake exists; no inbound channel |
| Own website / storefront | PARTIALLY IMPLEMENTED | `solvent/storefront.py` exists, never served publicly |
| Direct invitation on a platform | EXTERNALLY BLOCKED | acceptance terms unread |

First-party acquisition is the one route nobody can forbid. It is also
the one with no traffic.

---

## T. Adversarial intelligence risks

Covered and tested: prompt injection in postings and attachments,
forged payment confirmation (nine kinds refused by name), malicious
client messages, duplicate acquisition and acceptance, concurrent
awards, restart races, resource exhaustion, probation revocation,
commercial-policy bypass.

**Residual risks, honestly:**
- A client file crafted to exploit `openpyxl`/`python-docx` parsing is
  not something DeskPilot can defend against beyond quarantine.
- A marketplace returning plausible-but-false data is treated as data,
  not instructions — but a false *price* would be priced on.
- `reportverify`'s shared parser (section H) is an adversarial
  weakness as well as a design one.

---

## U. K-LEAN and iFixAi suitability

**iFixAi** — executed natively (v4.2.0, deterministic mode, telemetry
disabled). Its scorecard reads `Provider: mock`, `Fixture: NimbusForge
Deploy Copilot`. It audits a *live AI assistant endpoint* for
behavioural failures; DeskPilot's skills are deterministic and call no
model, so there is no endpoint to point it at. 40 of 60 inspections
needed an LLM judge that is not available here.
**Findings about DeskPilot: none.** Suitability: **NOT APPLICABLE** to
a deterministic codebase.

**K-LEAN** — **did not run.** Archived; `src/klean/reviews.py` requires
a LiteLLM proxy at `localhost:4000` federating OpenAI/Gemini/DeepSeek,
which needs provider keys that do not exist here. Nothing in any report
is a K-LEAN finding. Suitability: it reviews *diffs by model
consensus*, which is a code-review aid, not a capability audit.

Both are recorded in `docs/evidence/advisory/README.md`.

---

## V. Complete gap matrix

| # | Gap | Status | Priority | Source |
|---|---|---|---|---|
| 1 | Payment rail not connected | EXTERNALLY BLOCKED | P0 | `policy.py:820` |
| 2 | No marketplace account | EXTERNALLY BLOCKED | P0 | — |
| 3 | Contracting identity unset | MISSING (owner) | P0 | `setup contracting` |
| 4 | Commercial terms unset | MISSING (owner) | P0 | `setup commercial` |
| 5 | PDF unreadable | MISSING | P1 | `docwork.py:91` |
| 6 | CSV UTF-8 only | PARTIALLY IMPLEMENTED | P1 | `csvwork.py:213` |
| 7 | CSV comma only | PARTIALLY IMPLEMENTED | P1 | `csvwork.py:428` |
| 8 | `reportverify` not independent | PARTIALLY IMPLEMENTED | P1 | `reportverify.py:21` |
| 9 | data-entry verification superlinear | IMPLEMENTED BUT UNPROVEN | P1 | benchmarks |
| 10 | No pivot/lookup in spreadsheets | MISSING | P2 | `sheetwork.py` |
| 11 | No market rate data | MISSING | P2 | `market_hourly_cents` |
| 12 | No demand history | MISSING | P2 | — |
| 13 | No win-rate model | MISSING | P2 | zero observations |
| 14 | No outcome learning | PARTIALLY IMPLEMENTED | P3 | `finder.py:170` |
| 15 | Worker knowledge bases | MISSING | P3 | — |
| 16 | Checker knowledge bases | MISSING | P3 | — |
| 17 | VPS never exercised | IMPLEMENTED BUT UNPROVEN | P3 | — |
| 18 | OCR | MISSING | P4 | — |
| 19 | Autonomous client messaging | EXTERNALLY BLOCKED | P4 | terms unread |
| 20 | Browser control | MISSING | P4 | — |

---

## W. Prioritized implementation roadmap

No dates and no cost figures: neither is supportable from evidence.

### P0 — First-dollar blockers

All four are **owner or external**, none is code.

| Item | Complexity | Benefit | Risk | Recurring cost | Resources | Tests | External dependency |
|---|---|---|---|---|---|---|---|
| Connect a payment rail | Low (config) | Decisive — nothing is income without it | Medium: real money | Processing fees | Owner time | Rail-event verification exists | Stripe account |
| Marketplace account | Low | Opens direct-hire | Low | Platform fees | Owner identity check | Adapter tested on fixture | Platform |
| Contracting identity | Trivial | Invoices become issuable | Low | None | Owner time | Covered | None |
| Commercial terms + fees | Trivial | Margins stop being provisional | Low | None | Owner time | Covered | None |

### P1 — Professional capability blockers

| Item | Complexity | Benefit | Risk | Recurring cost | Resources | Tests | External dependency |
|---|---|---|---|---|---|---|---|
| **Delimiter detection** via stdlib `csv.Sniffer` | **Low** | Unblocks every EU semicolon/tab export | Low — detect, declare, never silently re-interpret | **None** | Negligible | New refusal/accept matrix; mutants on the declaration | **None** |
| **Encoding ladder** utf-8-sig → utf-16 → cp1252 | **Low** | Unblocks accented non-UTF-8 files | Medium: a wrong guess corrupts text, so the assumption must be declared in the report | **None** | Negligible | Round-trip tests per encoding; a mutant on the declaration | **None** |
| **PDF text extraction** (pdfplumber, MIT) | Medium | Opens the largest document market | Medium: extraction quality varies; must keep `NOT_FOUND`/`AMBIGUOUS` discipline | New dependency, no fee | Moderate | Per-field evidence tests; refusal on scanned/no-text PDFs | pdfplumber |
| **`reportverify` independence** | **Low** | Restores the property the design claims | Low | None | Small | A mutant on the worker's parser that the checker must catch | None |
| **data-entry verification cost** | Medium | Removes a deadline risk on real files | Low | None | Moderate | Benchmark assertion already exists and would fail if it regresses | None |

### P2 — Commercial intelligence

Demand history store; market-rate capture with citations; win-rate
model once bids exist; spreadsheet pivot/lookup. Complexity medium;
benefit real but **none of it is testable until work is flowing**.

### P3 — Reliability and learning

Outcome learning (revisions, acceptance, disputes); worker/checker
reference data; a supervised first week on the actual VPS. All
blocked on real jobs existing.

### P4 — Optional

OCR; autonomous client messaging (externally blocked); browser
control; additional marketplaces.

---

## X. Owner decisions and external dependencies

1. **Ask Freelancehunt whether automated contract acceptance is
   permitted.** Highest-value question in this audit.
2. **Connect Stripe**, or decide payment stays manual.
3. **Decide on PDF**: adopt pdfplumber (MIT), or keep declining PDFs.
4. **Decide on an encoding policy**: declare-and-proceed, or keep
   refusing non-UTF-8.
5. **Record commercial terms and contracting identity.**
6. **Authorise a supervised first week on the VPS** at one worker.

---

## Y. Final readiness assessment

### Could five genuine paying assignments be handled today?

**No.** Precisely why:

| Skill | Would it complete? | Why not |
|---|---|---|
| csv-cleanup | **Only if** UTF-8 and comma-delimited | Otherwise refused at intake |
| spreadsheet-automation | **Likely yes** for compute/consolidate/total; no for pivot or lookup | Operation not implemented |
| document-processing | **No, if the file is a PDF** | `.pdf` not read; no OCR |
| data-entry | **Yes**, with a deadline risk on large files | Verification superlinear |
| report-builder | **Yes**, within its stated scope | — |

And across all five:

- **Payment could not be confirmed.** The rail is SELECTED;
  `money_can_move: false`. The work could be delivered and invoiced;
  the money could not be verified as received.
- **Report-builder's checker is not independent**, so "every checker
  independently establishes correctness" is false as stated.
- **Queue replenishment and delivery would work** — demonstrated end
  to end with a timestamped trail, payment the only simulated stage.
- **Routine owner intervention would not be needed** for accepted
  work, given standing authority — but would be needed for every
  refused intake.

### The smallest evidence-based set that makes the answer yes

Four items. Three need no new dependency.

1. **Delimiter detection** — `csv.Sniffer`, stdlib, measured working
   on the exact inputs that currently refuse.
2. **Encoding ladder** — stdlib, measured decoding all five tested
   encodings, with the assumption declared in the job report.
3. **`reportverify` independence** — remove one import and give the
   checker its own parse.
4. **PDF text extraction** — pdfplumber (MIT), the only item needing a
   dependency, and the only one that opens a market rather than
   widening an existing one.

Plus the two external items nobody can code around: **a connected
payment rail**, and **an answer from Freelancehunt on contract
acceptance**.

That is the whole list. No rewrite, no new agent, no new architecture.
The system's problem is not its design — it is that the design accepts
a narrower range of real files than clients send, and has never been
paid.
