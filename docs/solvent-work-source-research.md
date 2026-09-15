# Solvent — Work Source Research

How a place that has paid work becomes a place Solvent is allowed to look.

**Nothing in this document is a verified claim about any platform.** No platform
was contacted while writing it, no terms of service were fetched, and none of the
candidate assessments below has been researched. Terms change, and a confident
statement from memory about what a platform permits is exactly the kind of
fabrication Solvent is built to refuse. Every row therefore starts at
`RESEARCH_REQUIRED`, and only a human reading current terms can move it.

---

## The readiness ladder

Each rung is a separate, checkable claim. **Being able to scrape a page is not
marketplace readiness.** `solvent/discovery.py` enforces the ladder: a source may
not be marked `PERMITTED` for automation until it has reached
`PERMITTED_AUTOMATION`, and it may not reach that without a recorded written
determination.

| Rung | What it claims | Evidence required |
| --- | --- | --- |
| `RESEARCH_REQUIRED` | Nothing. Default for every source. | — |
| `RESEARCHED` | Someone has read the current terms and documented them | The determination text, dated |
| `SUPPORTED_BY_API` | An official documented API exists for this purpose | API docs reference |
| `SUPPORTED_BY_BROWSER` | Reachable only by driving a browser | Working access without circumventing a protection |
| `PERMITTED_AUTOMATION` | The terms permit the automation Solvent would do | **A human determination naming the clause** |
| `IMPLEMENTED` | An adapter exists | Code |
| `TESTED` | The adapter is covered by tests | Passing tests |
| `ENFORCED` | Access runs through the Action Gate with an allowlist entry | Gate records |
| `PRODUCTION_READY` | All of the above **and** OD-1/2/3 resolved | Owner sign-off |

## Hard limits, regardless of readiness

No anti-bot circumvention. No CAPTCHA bypass. No impersonation. No shared or
borrowed credentials. No rate-limit evasion. No scraping a surface a platform has
asked automated clients not to take.

A source that would require any of these is recorded `PROHIBITED` and never
polled. This is not a preference; `Compliance.PROHIBITED` cannot be polled, and
`Discovery` has no path around itself.

## The per-source research template

`§24` of the mission brief asks for these fields. Reproduced here as the form to
fill in, once, per candidate — before any adapter is written.

```
SOURCE:
TYPE OF WORK:
API AVAILABLE:                  (official / unofficial / none)
AUTOMATION PERMITTED:           (quote the clause; do not summarise it)
TOS CONSTRAINTS:
PERSONAL-PERFORMANCE CLAUSE:    (does it require the contracted party to do the work?)
IDENTITY REQUIREMENTS:          (individual / business / verified identity / KYC)
PAYMENT METHOD:
PAYMENT VERIFICATION SIGNAL:    (webhook / API / statement only / none)
FEES:
RATE LIMITS:
TECHNICAL DIFFICULTY:
JOB QUALITY:
TYPICAL JOB VALUE:              (only if verifiable; otherwise UNKNOWN)
CAPABILITY FIT:                 (against the Capability Registry, not aspiration)
RISK:
DETERMINATION:                  (PERMITTED / PROHIBITED / UNDETERMINED + why)
DETERMINED BY / DATE:
RECOMMENDED PRIORITY:
```

## What makes a good *first* source

Not popularity. Not job volume. A first source should be chosen on whether
Solvent can operate there **legally, verifiably and cheaply**:

1. **A legal automation path that is explicit**, not merely unmentioned. Silence
   in the terms is not permission.
2. **Official programmatic access**, so the offer arrives as structured fields
   rather than prose. This matters more than convenience: Solvent's injection
   defence treats free text as untrusted, so a source with structured offer terms
   can be qualified automatically while a scraped one always needs a human to
   confirm the requirements. That is a deliberate design consequence, visible in
   `Qualification._requirements_for`.
3. **A payment verification signal.** Without one, `PAID` is unreachable, profit
   cannot be computed, and the Governor never gets a calibration sample. A source
   that pays by an unverifiable route cannot close Solvent's loop at all.
4. **Work that matches a *registered, proven* capability** — currently a very
   short list.
5. **No upfront cost**, per the bootstrap model.
6. **An identity posture Solvent can honestly satisfy.** If a platform requires a
   named individual to personally perform the work, an AI operator does not
   qualify, and no wording makes that acceptable.

## Candidate categories

Structural observations only. **Every one is a hypothesis to verify, not a
finding.** Named platforms are deliberately omitted: naming one here would invite
exactly the unverified assumption this document exists to prevent.

| Category | Structural attraction | Structural concern to verify first |
| --- | --- | --- |
| **Agent-oriented / machine task APIs** | Built for programmatic clients, so automation permission is likely explicit and offers arrive structured | Whether pay is real and verifiable, and whether the work matches a proven capability |
| **Open-source bounty programmes** | Deliverable is machine-checkable (a merged change); outcome is externally verified by definition | Payment rail and its verification signal; whether the project accepts AI-assisted contributions |
| **Public RFP / tender feeds** | Frequently published deliberately for machine consumption | Bidding almost always requires a legal entity and often registration — OD-1 blocks this outright |
| **General freelance marketplaces** | Largest volume of work | **Highest-risk category.** Personal-performance clauses, identity verification, and restrictions on automated bidding are all common patterns that must be checked before anything is built |
| **Direct inbound leads** | No platform terms at all; Solvent's own terms apply | No discovery to automate — this is a channel, not a source |

**Recommended first source *category*: agent-oriented task APIs or bounty
programmes** — because both tend to offer structured terms and externally
verifiable completion, which are the two properties Solvent's architecture most
needs. **The specific platform is an owner decision** and must follow a completed
research template above.

## Why discovery is not urgent

Solvent can prove the whole loop without any platform: `solvent acquire` runs a
full acquisition cycle over a fixture board today, and `solvent demo` carries one
job to verified profit. The remaining blockers to a first real job (OD-1, OD-2,
OD-3) are not discovery blockers. **Building a marketplace adapter first would be
building an automated pipeline into a business process that has never completed
once.**

The progression, in order:

```
SIMULATED JOB  →  MANUALLY SOURCED REAL JOB  →  CONTROLLED DISCOVERY
→  OWNER-APPROVED FIRST PLATFORM  →  LIMITED AUTOMATED DISCOVERY
→  CONTROLLED ACCEPTANCE  →  EARNED AUTONOMY
```

Solvent is at step one. Step two is blocked on three owner decisions, none of
them technical.
