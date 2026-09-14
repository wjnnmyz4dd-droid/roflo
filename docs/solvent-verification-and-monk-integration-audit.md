# Solvent — Repository Identity Verification + Monk Concept Integration Audit

**Mode:** FORENSIC VERIFICATION AND ARCHITECTURE AUDIT ONLY. No code, config, dependency or
structural change. No agents created. No PR. Every command run was read-only.

**Produced from:** `wjnnmyz4dd-droid/roflo`, branch `claude/solvent-architecture-audit-8ot9et`,
commit `70fbc3e42ee1e518f63ab6e562eb473ee10379d7`. **Date:** 2026-09-14.

**Companion document:** `docs/solvent-architecture-audit.md` (the prior audit). This document
supersedes parts of it; every supersession is stated explicitly in §5 and §6.

---

## 1. REPOSITORY IDENTITY REPORT

Re-verified live, not recalled:

| Item | Verified value |
| --- | --- |
| Repository root | `/home/user/roflo` |
| Repository name | `roflo` |
| Origin URL | `https://github.com/wjnnmyz4dd-droid/roflo` |
| All remotes | **exactly one** — `origin`. No upstream, no second remote |
| Current branch | `claude/solvent-architecture-audit-8ot9et` |
| Current commit | `70fbc3e42ee1e518f63ab6e562eb473ee10379d7` |
| Commit subject | "Add Solvent master architecture audit (audit only, no implementation)" — my own prior audit commit |
| `git status` | clean — 0 modified, 0 untracked |
| Worktrees | **1** (`/home/user/roflo`) |
| Tags | **0** |
| Total commits in all history | **2** |
| Shallow? | **false** — no `.git/shallow`, no grafts, no replace refs. Nothing truncated |

**Every ref that exists:**

```
687cfc2  refs/heads/claude/ai-model-no-guardrails-24alg3
70fbc3e  refs/heads/claude/solvent-architecture-audit-8ot9et
687cfc2  refs/remotes/origin/claude/ai-model-no-guardrails-24alg3
70fbc3e  refs/remotes/origin/claude/solvent-architecture-audit-8ot9et
```

**Live `git ls-remote --refs origin`** (queries GitHub, not the local cache) returns those same
two branches and nothing more. Independently confirmed by the GitHub API `list_branches`.
Remote default branch is `claude/ai-model-no-guardrails-24alg3` — **this repository has no
`main` or `master` at all.**

---

## 2. BRANCH / WORKTREE / REF VERIFICATION

| Check | Result |
| --- | --- |
| Local branches | 2 |
| Remote branches (live) | 2 — identical SHAs |
| Unfetched refs | none — `git fetch --dry-run --all --tags` reports nothing |
| Tags | none |
| Worktrees | 1 |
| Submodules | none (`.gitmodules` absent) |
| Other `.git` dirs on disk | **none** — `find /home/user -name .git` returns one result |
| Orphan / unreachable objects | **none** — `git fsck --lost-found --unreachable` returns empty |
| Root commits | **1** — `687cfc2`, verified parentless (`git cat-file -p` shows `tree`/`author`/`committer`/`gpgsig`, no `parent` line) |
| Branch divergence | none — `70fbc3e` is a direct child of `687cfc2` |
| `roflo/` subtree on both branches | **identical hash** `15fab5b1b7e03b18c50da3bee347ebb987811938` |

**The two branches differ by exactly one file: my 1,277-line audit document.** No source file
differs between them.

**History:** `687cfc2` authored `2026-08-24T03:06:31Z` by `Claude <noreply@anthropic.com>`
(confirmed identically by the local object and the GitHub API). Cloned into this session
`2026-09-14T12:25`. Its message describes scaffolding an inference stack from scratch. The
repository has **0 issues and 0 pull requests**.

---

## 3. PRECISELY WHERE SOLVENT EXISTS (Part 7 terminology)

The phrase "Solvent does not exist" is retired. Here are the six distinct claims, each
answered separately:

| Claim | Status | Evidence |
| --- | --- | --- |
| **Solvent REPOSITORY exists** | **NOT FOUND — cannot be disproven globally** | `list_repos` returns **13** repositories for `wjnnmyz4dd-droid`, **including private ones** (so the connector has private visibility); none is named Solvent. `search_repositories "solvent user:wjnnmyz4dd-droid"` → 0. `search_code "solvent user:wjnnmyz4dd-droid"` → 0. A repository under a **different account or organisation** remains possible and is not visible from here |
| **Solvent DOCUMENTATION exists** | **YES** | Your specification prompts, plus `docs/solvent-architecture-audit.md` (85,009 bytes) and this document — both in this repository |
| **Solvent BLUEPRINT exists** | **YES — as design, never as code** | The prior audit's proposed authority map. A proposal, not an implementation, and never owner-approved |
| **Solvent FOUNDATION exists** | **AMBIGUOUS** | An inference layer that *could* serve Solvent §18 exists. Nothing establishes it was built for that purpose. See §4 |
| **Solvent BUSINESS LOGIC exists** | **NO — disproven for every ref, worktree, commit and file reachable from this session** | §2 and the sweeps below |
| **Solvent PRODUCTION IMPLEMENTATION exists** | **NO** | Same evidence |

### Search verification (Part 2), re-run with the expanded term list

`git grep -i "solvent" 687cfc2` → **zero files.** The only "solvent" strings anywhere in
history are in my own audit document at `70fbc3e`.

Concept sweep of the original codebase `687cfc2` (whole-word, case-insensitive). These returned
**0**: `job · opportunity · business · worker · agent · orchestrator · profit · margin · spend ·
invoice · payment · ledger · approval · permission · memory · learning · audit · governor ·
freelance · bounty · marketplace`.

Six terms returned non-zero. **Every one inspected line by line; all are false positives:**

| Term | Hits | What they actually are |
| --- | --- | --- |
| `client` | 54 | `httpx.AsyncClient` and OpenAI client sample code |
| `state` | 7 | FastAPI's `app.state` attribute container (`server.py:91–118`, tests) |
| `project` | 5 | `[project]` TOML section headers in `pyproject.toml`; prose "the project" |
| `budget` | 2 | `echo.py:19` — a token counter in the test-double backend |
| `policy` | 2 | README/docstring prose: "no **policy** preamble" — i.e. describing the *absence* of policy |
| `cost` | 1 | README prose: "import-time cost" |

**Behavioural search** for `sqlite3|psycopg|pymongo|redis|sqlalchemy|open(…'w')|.save()|.commit()|
CREATE TABLE|stripe|paypal|decimal|currency|cron|schedule|celery` → **zero matches.** There is no
persistence, no money handling and no scheduling primitive in the codebase.

---

## 4. ROFLO'S RELATIONSHIP TO SOLVENT (Part 4)

| Option | Verdict | Evidence |
| --- | --- | --- |
| A. Solvent under another/internal name | **REFUTED** | Architecture is `Config → Engine → one Backend`. No business primitive, no persistence, no decision point |
| B. Infrastructure/foundation intended for Solvent | **INCONCLUSIVE** | *Functionally* it is the inference layer Solvent §18 needs. **No evidence of intent**: no doc, comment, TODO, commit message, issue or PR anywhere references Solvent |
| C. Earlier Solvent prototype | **REFUTED** | The single root commit is already a complete inference tool. It was never a business system at any point |
| D. Dependency intended to support Solvent | **INCONCLUSIVE** | Same as B — plausible by function, unevidenced by intent |
| E. Separate project accidentally audited as Solvent | **SUPPORTED as a live possibility** | Its stated purpose is unrelated to business operations |
| F. Unrelated | **SUPPORTED as a live possibility** | Same |
| **G. Impossible to determine from available evidence** | **This is the verdict on intent** | Architecture is certain; purpose is not recorded anywhere |

Classification on *architecture* is certain: roflo is an LLM inference CLI and OpenAI-compatible
proxy. Classification on *intent* is **G**, and I will not infer it from README wording.

---

## 5. HYPOTHESES (Part 5)

| # | Hypothesis | Verdict | Evidence |
| --- | --- | --- | --- |
| **H1** | Previous audit inspected the wrong repository | **INCONCLUSIVE** | The repo inspected is definitively the one this session is attached to (sole remote, sole worktree). Whether it is the one *you* intended cannot be verified from here |
| **H2** | Right repo, wrong branch | **REFUTED** | Two branches; identical `roflo/` subtree hashes; the only delta is my audit doc |
| **H3** | Solvent on another remote branch | **REFUTED** | Live `ls-remote` and GitHub API both return exactly two branches; `fetch --dry-run` finds nothing |
| **H4** | Solvent in another worktree | **REFUTED** | One worktree; one `.git` on the entire filesystem; no `*solvent*` path on disk except my doc and the ref files |
| **H5** | This repo contains only infrastructure intended to support Solvent | **SUPPORTED but UNPROVEN** | Functionally coherent; documentary evidence of intent is absent |
| **H6** | Solvent business implementation genuinely not yet built | **SUPPORTED — strongly** | Two commits, one root, zero business terminology, zero persistence, no deleted files, no tags, no PRs, no issues |
| **H7** | Solvent existed and was deleted/reset/overwritten | **REFUTED for tracked history; INCONCLUSIVE for destroyed history** | `git fsck` clean, no orphan refs, no deleted files in history. A fresh clone **cannot** detect a force-push that destroyed remote history before 2026-09-14. The sole commit being a genuine initial scaffold, with no PRs/issues, argues strongly against it |

---

## 6. RE-EVALUATION OF PREVIOUS AUDIT (Part 6)

I attempted to falsify each conclusion rather than defend it.

| Prior conclusion | Classification | Reasoning |
| --- | --- | --- |
| "Solvent does not exist" | **PARTIALLY_CONFIRMED — the sentence was too broad** | Confirmed: no Solvent in this repository, any ref, any history. **Not** confirmed as a global claim — I had no evidence about repositories outside this session's visibility, and asserted one anyway |
| "This repo contains one project: roflo" | **CONFIRMED** | 25 tracked source files, all roflo; single root commit |
| "0 of 46 Solvent requirements implemented" | **CONFIRMED for this repository** | Expanded concept sweep found six non-zero terms, all verified false positives |
| "0 of 17 acceptance criteria evidenced" | **CONFIRMED for this repository** | No decision point exists to evidence any of them |
| "There is zero persistence" | **CONFIRMED** | No DB driver, no ORM, no file write, no migration directory anywhere |
| "No job/opportunity/business authorities" | **CONFIRMED** | Verified line by line |

### Two errors in my prior work, stated plainly

1. **Overbroad headline.** "Solvent does not exist" asserted a global negative from
   repository-scoped evidence. The supportable claim was always the narrower one.
2. **Module count inconsistency.** The prior document says "**Fourteen** modules, of which six
   are P0," but its §21 then lists **fifteen** numbered module reports (two entries share the
   number 13). Per Part 26 I have not carried that count forward — §12 below re-derives the
   authority set from scratch, and the result differs from the prior list in composition.

---

## 7. EXISTING AUTHORITY MAP (Part 26 — measured, not assumed)

**Authorities that exist in code: zero.** To avoid the category error the prior audit made, every
component below carries two independent statuses.

**What actually exists in `roflo` (the complete inventory):**

| Component | Responsibility | Is it a Solvent authority? |
| --- | --- | --- |
| `config.py` | Load TOML + `ROFLO_*` env into a `Config`; reject unknown keys | No — process configuration, not policy |
| `prompt.py` | Six pure-function chat templates | No |
| `engine.py` | `messages → render → backend.stream() → chunks` | No — the entire request path, no decision |
| `backends/` | `Backend` ABC + 5 adapters, lazy registry | **The only reusable asset** — a provider abstraction |
| `server.py` | FastAPI OpenAI-compatible endpoints + optional bearer token | No — one shared token, no identity, no scope |
| `cli.py` | argparse commands | No |

**Nothing else exists.** Every Solvent component discussed below has **CODE STATUS: MISSING**.
Where I write EXISTS/EXTEND/CONSOLIDATE, that refers to the *prior audit's design*, which is a
proposal that has never been approved or built. **No Monk concept can "already exist" in code,
because no Solvent code exists.**

---

## 8. MONK CONCEPTS — DISPOSITION SUMMARY (Parts 9–14)

| Monk concept | Verdict | Owner |
| --- | --- | --- |
| **#1 Continuous Project State** | **DO_NOT_ADD as authority** → a read-only **projection** | Composed from Orchestrator + Ledger + Memory + Audit + Capability Registry |
| **#2 OBSERVE→DIAGNOSE→PROPOSE→ACT→VERIFY** | **DO_NOT_ADD** → an **execution contract** enforced by the state machine | Job Orchestrator |
| **#3 Permission & approval gates** | **EXTEND existing design** — one Policy Store, enforced at two chokepoints | Policy Store + Action Gate |
| **#4 Gap detection vs value-add** | **EXTEND** — two separate analyses, neither a module | Execution (detect) → Orchestrator (route) → Qualification (re-enter) |
| **#5 Cost & Value intelligence** | **DO_NOT_ADD** → **split across two existing authorities in a fixed order** | Capability/Conformance (filter) → Financial Governor (rank) |
| **Verify-before-learn** (Part 11) | **NEW ARCHITECTURAL LAW** — not a module | Enforced by Orchestrator; evidence stored in Audit Log |

### Concept #1 — Continuous Project State (Part 9)

**DO_NOT_ADD as an authority.** A "Project State Engine" that stores its own copy of scope, cost,
deadlines and status would become a second source of truth that drifts from the Ledger and the
job state machine. That is precisely the duplicate-authority failure the Solvent law forbids.

**Correct form — the Project View:** a composed, read-only projection with **no persistent state
of its own**, assembled on demand from:

| Field group | Authoritative source |
| --- | --- |
| objective, scope, client requirements | Business Memory (+ Client Channel record for scope changes) |
| workflow status, blockers, current/remaining work | Job Orchestrator (state machine) |
| costs, budget consumed, expected/actual profit | **Ledger** (actuals) + Financial Governor (authorised remaining budget) |
| decisions, authority used, approvals | **Audit Log** |
| capabilities assigned, quality state | Capability Registry + Execution QC records |
| files, dependencies | Execution layer work-product index |

**Two rules that keep it from becoming an authority:**

1. **Nothing may store into the View.** It is derived; if it caches for performance, the cache is
   explicitly non-authoritative and rebuildable from sources.
2. **No gate may read *from* the View.** The Governor reads cost from the Ledger, not the View.
   The moment a decision depends on the projection, the projection has become authoritative by
   accident — this is the single most likely way this concept turns into a duplicate authority.

### Concept #2 — OBSERVE → DIAGNOSE → PROPOSE → ACT → VERIFY (Part 10)

**DO_NOT_ADD as a module.** This is the **execution contract** for every capability invocation,
enforced structurally by the Job Orchestrator's state machine rather than by a supervisor
component (a supervisor would be a second orchestrator).

Enforcement mechanism: a dispatch produces a typed record with all five phases populated, and
**the state machine cannot advance a job to any terminal or delivery state without a VERIFY
record that satisfies §9's tier requirements.** ACT is the only phase that may cause an external
effect, and it must pass the Action Gate.

| Phase | May it cause an external effect? | Gate |
| --- | --- | --- |
| OBSERVE | No (internal reads only) | — |
| DIAGNOSE | No | — |
| PROPOSE | No — proposals are free, and this is what makes "ask first" cheap | — |
| **ACT** | **Yes** | **Action Gate** (permission + operating mode + privacy + budget) |
| VERIFY | Read-only external checks permitted (also via the Gate) | Action Gate |

**Uncontrolled one-shot actions are structurally impossible** because ACT is reachable only from
a PROPOSE record that the Gate has authorised.

**Cost caveat (see §23, A6):** verification is not free. Verification intensity must scale with
consequence exactly as QC intensity does, or ODPAV doubles the token cost of every trivial task
and destroys margin on small jobs.

### Concept #3 — Permission & approval gates (Part 12)

**One Policy Store. No second permission system.** The refinement Monk adds is *where* the line
falls. The useful boundary is **not** "risky vs safe" but **"does this action create an external
effect or an obligation."** Proposals cost nothing and need no approval; that is what makes
"I found a supplier 14% cheaper — shall I request pricing?" the natural default behaviour rather
than a special case.

Recommended action classes, each assigned a permission level by Policy:

| Class | Example | Typical level |
| --- | --- | --- |
| C0 internal read | query Memory, compute an estimate | AUTONOMOUS |
| C1 external read | fetch a public page, read a marketplace listing | AUTONOMOUS (rate/ToS bound) |
| C2 external communication | message a client, ask a supplier for a quote | CONDITIONAL (bounded by job scope) |
| C3 financial commitment | accept a job, place a bid, pay a vendor | **OWNER_APPROVAL above Policy thresholds** |
| C4 credential/config/integration change | add a provider, rotate a key | **OWNER_APPROVAL** |
| C5 authority change | edit Policy, expand autonomy | **OWNER ONLY — Solvent may never initiate** |

### Concept #4 — Gap detection vs value-add (Part 13)

Two different questions, deliberately **not** merged, and **neither becomes a module**.

| | EXECUTION GAP | CLIENT VALUE-ADD |
| --- | --- | --- |
| Question | "What prevents correct completion of contracted work?" | "What would make the client's outcome substantially better?" |
| Urgency | Blocking | Non-blocking |
| Detected by | Execution layer | Execution layer |
| Routed by | Orchestrator | Orchestrator |
| Resolution | Resolve **within existing authority and budget**, else escalate | Produce a **proposal** — never perform it |
| Billing | Inside current job | **Outside** — requires a new commercial decision |

**The anti-scope-creep invariant, stated as two prohibitions:**

- An execution gap may **never** be resolved by expanding paid scope.
- A value-add may **never** be performed inside the current job's budget.

When a client accepts a value-add, it becomes a **new opportunity** and re-enters the full
pipeline: CAPABILITY → COST → PROFITABILITY → RISK → PERMISSION → EXECUTION. There is no fast
path. This closes the side door through which work could be committed without passing the
Governor.

### Concept #5 — Cost & Value intelligence (Part 14)

**DO_NOT_ADD as an engine.** But it does not belong wholly inside the Financial Governor either —
that would make the Governor decide *technical conformance*, which it is not competent to judge.
It splits cleanly across two existing authorities **in a fixed order**:

```
   candidate options
        │
        ▼
 ┌───────────────────────────────────┐
 │ STAGE 1 — CONFORMANCE FILTER      │   Capability & Conformance Registry
 │ Does this option actually satisfy │   Output: CONFORMS / FAILS / UNKNOWN
 │ the stated requirement?           │   UNKNOWN is NOT conforming (fail closed)
 └───────────────┬───────────────────┘
                 │  only CONFORMING options continue
                 ▼
 ┌───────────────────────────────────┐
 │ STAGE 2 — TOTAL-VALUE RANKING     │   Financial Governor
 │ purchase price · total cost ·     │   Output: ranked, with risk allowance
 │ maintenance · switching cost ·    │
 │ reliability · time · opportunity  │
 └───────────────────────────────────┘
```

This makes the "$8,900 versus $12,000" trap **architecturally impossible** rather than a matter
of good judgement: a non-conforming option is not a cheaper option, it is **not an option**, and
it never reaches the stage where price is compared. Quality, reliability and risk enter as
Governor inputs (risk allowance, expected life, maintenance) — they are not a separate authority.

---

## 9. VERIFY BEFORE LEARN (Part 11) — NEW ARCHITECTURAL LAW

> **An agent reporting COMPLETE does not prove SUCCESSFUL. Learning may consume only outcomes
> carrying a verification record whose verifier is distinct from the executor.**

**Verification tiers** — each learning consumer is restricted to a minimum tier:

| Tier | Evidence | Independent? | May feed |
| --- | --- | --- | --- |
| **T0** | Executor self-report ("done", "looks correct") | No | **Nothing. Never learning evidence.** |
| **T1** | Deterministic machine check — tests pass, schema validates, file parses, arithmetic reconciles, checksum matches | Yes (code, not judgement) | Technical/quality learning |
| **T2** | Independent review by a *different* capability instance, different model where possible, **and not re-reading the same attacker-controlled input** | Partially | Quality learning, weakly |
| **T3** | External fact — client acceptance, **verified payment in the Ledger**, no dispute/refund after N days | Yes | **Business/economic learning, and autonomy evidence — T3 only** |

**Where verification evidence lives:** the **Audit Log** (append-only, deletion-incapable), never
Business Memory. If verification records lived in the store that Learning writes to, Learning
could manufacture its own evidence — see §23, A3.

**How it is protected:** the verification record names the verifier identity, the method, the
tier, and the raw check output. A record whose verifier equals its executor is rejected at write
time — a code-enforced rule, not a convention.

---

## 10. LEARNING / ESTIMATOR LOOPHOLE — RESOLUTION (Part 16)

**The loophole:** the Governor compares `estimate` against `floor`. If Learning makes the
estimator optimistic, estimated cost falls, apparent margin rises, marginal jobs clear an
unchanged floor. The policy was never edited; the same effect was achieved.

**Smallest solution — remove the writer rather than police the write.** Three parts, no new
module, no second Governor:

**1. Learning has no write path to any parameter used in a gate decision.**
The Governor **derives its own calibration from the Ledger** (verified actuals). Learning may
publish advisory estimates and observations; it cannot alter what the Governor tests against.
This is the core of the fix: there is nothing to police because there is no writer.

**2. The Governor gates on a conservative bound, not a point estimate.**
Effective cost = estimate × conservatism factor, where the factor is the Policy-set percentile
(e.g. p80) of the historical `realised ÷ estimated` ratio computed from the Ledger.
**This is self-correcting:** an optimistic estimator produces worse realised ratios → the factor
rises → the effective gate tightens automatically, with no owner intervention and no detector
needed for the primary failure mode.

**3. Asymmetric change control.**
- Any recalculation that **tightens** (raises effective cost) applies **immediately**.
- Any recalculation that **loosens** requires ≥ N verified samples, is rate-limited to ≤ X% per
  period, and both N and X are owner Policy.
- **Automatic rollback:** if realised-versus-estimated accuracy degrades past a Policy threshold,
  the Governor reverts to the last calibration that met the standard and escalates.

> **Principle: getting safer is automatic; getting looser requires verified evidence, time, and
> owner-set bounds.**

**Residual risk I could not eliminate — selection bias (see §23, A4).** Calibration is computed
over *accepted and completed* jobs. If job selection quietly narrows to easy work, realised error
looks excellent while capability degrades. Detector: **a rising acceptance rate with flat or
falling realised margin is the signature of this loophole being exploited.** That comparison
belongs in the Analyst as a monitored alarm, reported to the owner — not as a gate.

---

## 11. MEMORY / LEDGER / AUDIT BOUNDARIES (Part 17)

**Three stores. Not merged, at any module count.**

| | BUSINESS MEMORY | LEDGER | AUDIT LOG |
| --- | --- | --- | --- |
| Holds | Operational knowledge that may evolve | Authoritative financial records | Immutable evidence of consequential actions and decisions |
| Mutability | Mutable; derived values recomputed | Append-oriented; corrections are **new entries**, never in-place edits | **Append-only. No delete path in code.** |
| Learning may **read** | Yes | Yes | Yes |
| Learning may **write** | **Derived aggregates only** — never raw client content, never financial truth | **Never** | **Never** |
| Orchestrator may write | Job knowledge | No (only the Ledger's verified-inflow adapters write money) | Yes (append) |
| Owner may edit | Retention/classification policy | Corrections as new entries, approved | **No — the owner cannot edit it either** |
| Fail-closed | Unclassified data = maximum sensitivity; no class = no external transmission | **No external verification = not PAID** | **If an action cannot be logged, the action does not proceed** |
| Feeds autonomy evidence | **NEVER** | **Yes (T3)** | **Yes (T3)** |

**The boundary that matters most:** autonomy evidence and financial calibration are drawn from
the Ledger and Audit Log **only**. Business Memory is the one store Learning writes to, so it can
never be the source of evidence that governs Learning's own expansion.

---

## 12. TARGET AUTHORITY MAP (Part 26) — RE-DERIVED

Per instruction I did **not** carry forward the prior count. Re-derivation yields **14
authorities plus one non-authoritative projection**, with **two components from the prior map
dissolved** and **one added**:

**DISSOLVED — Model Router.** Its two jobs split cleanly into existing owners: *selection* is a
Policy-driven lookup belonging to the Capability Registry (which provider serves which capability
at which privacy class); *enforcement* (privacy, budget, kill switch) belongs to the Action Gate.
Keeping it separate risked a second budget authority. **CONSOLIDATE.**

**DISSOLVED — Expansion Pipeline.** An expansion proposal **is an internal opportunity**: it runs
through Qualification → Governor → Orchestrator with the owner as the client. Sandbox isolation
is a property of the Execution layer, not a module. A parallel pipeline would be a second way to
commit resources. **CONSOLIDATE.**

**ADDED — Action Gate.** Emerged from the kill-switch question (Part 22) and is the single most
valuable structural change in this revision. It is an **enforcement point, not a decision
authority**: it decides nothing itself, it asks Policy and the Governor and records to Audit.

```
OWNER
  │  writes policy (only the owner writes here)
  ▼
┌──────────────────┐        ┌──────────────┐        ┌──────────────┐
│ 1 POLICY STORE   │        │ 3 LEDGER     │        │ 2 AUDIT LOG  │
│ trust root       │        │ verified $   │        │ append-only  │
│ kill switch flag │        └──────┬───────┘        └──────▲───────┘
└────────┬─────────┘               │ actuals + calibration │ everything
         │ consulted               ▼                       │ writes here
         │              ┌────────────────────┐             │
         │              │ 5 FINANCIAL        │             │
         │              │   GOVERNOR         │             │
         │              │ the ONE money gate │             │
         │              └─────────┬──────────┘             │
         │                        │ authorises             │
         ▼                        ▼                        │
┌─────────────────────────────────────────────────┐        │
│ 10 ACTION GATE   ← THE ONLY EGRESS              │────────┘
│ every external effect passes here:              │
│ permission · operating mode · privacy class ·   │
│ budget pre-check · rate limit · audit           │
└───┬──────────────┬───────────────┬──────────────┘
    ▼              ▼               ▼
 providers      clients        marketplaces
 (roflo)        (email)        (APIs)
```

| # | Authority | P | Code status | Single responsibility |
| --- | --- | --- | --- | --- |
| 1 | **Policy Store** | P0 | MISSING | Hold owner-granted authority, floors, ceilings, approved lists, operating mode |
| 2 | **Audit Log** | P0 | MISSING | Immutable evidence of consequential actions and decisions |
| 3 | **Ledger** | P0 | MISSING | Verified money; actual profit; estimate-vs-actual |
| 4 | **Business Memory** | P1 | MISSING | Mutable operational knowledge with provenance/sensitivity/retention |
| 5 | **Financial Governor** | P0 | MISSING | The one money gate; anomaly detection; own calibration from Ledger |
| 6 | **Capability & Conformance Registry** | P1 | MISSING | What Solvent can do; whether an option satisfies a requirement; which provider at which privacy class |
| 7 | **Qualification & Ranking** | P1 | MISSING | One accept/reject/escalate verdict; owns **no** economics |
| 8 | **Job Orchestrator** | P0 | MISSING | Job state machine; ODPAV enforcement; supervision; no silent stalls |
| 9 | **Execution Layer** | P1 | MISSING | Perform capabilities incl. QC, document intake, verification execution |
| 10 | **Action Gate** | P0 | MISSING | **Single egress chokepoint** — enforce permission, mode, privacy, budget, rate, audit |
| 11 | **Owner Channel** | P1 | MISSING | Carry information out, authenticated scoped approvals in |
| 12 | **Client Channel** | P2 | MISSING | Conduct and formally record client communication; route scope changes |
| 13 | **Analyst** | P3 | MISSING | Read-only observation → recommendations only |
| 14 | **Discovery** | P1b | MISSING | Candidate sourcing + per-source compliance state |
| — | *Project View* | P2 | MISSING | **Not an authority** — a read-only projection (§8) |

**P0 = six: Policy Store, Audit Log, Ledger, Financial Governor, Job Orchestrator, Action Gate.**
Business Memory moves to P1 — the first real job needs *job state* (Orchestrator) and *money*
(Ledger), not accumulated learning knowledge.

### Full reports for the two authorities that changed

**ACTION GATE (new)**
- **Responsibility:** the single point through which every external effect passes.
- **Why it exists:** without one chokepoint, the kill switch, privacy enforcement, spend caps and
  egress auditing must each be re-implemented at every call site — and each site is a bypass.
- **Authority it HAS:** refuse any external action. Refusal is final.
- **Authority it DOES NOT have:** cannot *grant* permission (asks Policy), cannot *authorise*
  spend (asks the Governor), cannot decide business outcomes.
- **Inputs:** proposed action + class + privacy class + job/budget context; Policy verdict;
  Governor verdict; operating mode.
- **Outputs:** allow/deny + audit record.
- **Dependencies:** Policy, Governor, Audit Log.
- **State ownership:** none (rate-limit counters only).
- **Financial authority:** none — enforces the Governor's.
- **Permission authority:** none — enforces Policy's.
- **Memory access:** none.
- **Failure mode:** gate unavailable; or **an action path that bypasses it** (§23, A1).
- **Fail-closed:** unavailable ⇒ no external action occurs. Unknown action class ⇒ deny.
- **Audit:** every allow and every deny.
- **Owner approval:** per Policy for the action's class.
- **Overlap check:** absorbs enforcement previously scattered across Router/Execution/Channels.
  Adds no decision authority.

**CAPABILITY & CONFORMANCE REGISTRY (extended)**
- Absorbs, beyond the prior design: **requirement-conformance judgement** (§8 Concept #5 Stage 1)
  and **provider selection** (from the dissolved Model Router).
- **Authority it DOES NOT have:** cannot add capabilities to itself; cannot judge profitability;
  cannot authorise a provider (Policy approves providers, the Gate enforces).
- **Fail-closed:** unproven capability = CANNOT_EXECUTE; **UNKNOWN conformance = NOT conforming.**
- Also the correct home for each model checkpoint's **commercial-licence clearance record**.

---

## 13. FINANCIAL GOVERNOR BOUNDARIES (Part 15)

**Inside the Governor:** expected revenue · AI/API · compute · tools · vendors · platform fees ·
payment fees · labour/resource · revision allowance · risk allowance · acquisition cost · time ·
expected profit · expected margin · **opportunity cost** · **spend anomaly detection** ·
**its own calibration derived from the Ledger** (§10).

**Outside the Governor:** recording actuals (Ledger) · requirement conformance (Capability
Registry) · accept/reject verdict (Qualification) · permission (Policy) · enforcement (Action
Gate).

**Invariants:**
- No worker, capability or channel may override it. Its rejection is final and unappealable.
- **Checked before every spend**, because roflo and the backends enforce nothing (§15, D2).
- Reinvestment analysis is an **Analyst recommendation + owner Policy edit** — never a competing
  spending authority.
- Unknown cost ⇒ REQUEST_INFORMATION or REJECT. Stale telemetry ⇒ BLOCK, never proceed.

---

## 14. PERMISSION BOUNDARIES, KILL SWITCH, VOICE (Parts 12, 22, 23)

### Kill switch (Part 22) — smallest correct architecture

**Not an agent. Not a module.** A single `operating_mode` value in the Policy Store, enforced at
**exactly two chokepoints**: the **Financial Governor** (before any spend) and the **Action Gate**
(before any external effect).

> **If every spend and every external effect funnel through two gates, the kill switch is two
> conditionals. If it needs to be checked in more than two places, the architecture is wrong.**

| Mode | New work | In-flight work | External actions | Spending |
| --- | --- | --- | --- | --- |
| NORMAL | yes | yes | yes | within budget |
| PAUSE_NEW_WORK | **no** | yes | yes | yes |
| PAUSE_SPEND | no | yes (non-spending steps) | yes | **no** |
| PAUSE_EXTERNAL | no | internal only | **no** | no |
| SAFE_MODE | no | **finish existing obligations only** | minimum necessary | minimum necessary |
| HALT | no | **stopped** | **no** | **no** |

**A tension the owner must decide consciously, not discover later:** HALT mid-contract may cause
breach of an accepted client obligation. SAFE_MODE exists precisely so that "stop expanding" does
not have to mean "stop honouring." **HALT must warn, at the moment of use, which obligations it
is abandoning.** A kill switch that silently creates legal exposure is not a safety feature.

**Fail-closed:** if the Policy Store is unreachable, both chokepoints deny. A policy outage halts
the business — the correct trade, and the reason the Policy Store must be local, durable and
boring.

### Voice / phone / remote approval (Part 23) — corrected

My prior recommendation to "defer voice indefinitely" was **wrong as stated** — it conflated two
different things. The correct separation:

| Function | Channel | Verdict |
| --- | --- | --- |
| **Notification / briefing / urgency escalation** | phone call, SMS, app push | **Keep. Safe. May be primary for ≤20-minute expiries** |
| **Authorisation of a consequential action** | **authenticated owner channel only** (device-bound key, signed approval) | **A phone call is never sufficient** |

Escalation for a ≤20-minute expiry: **CALL → TEXT/APP NOTIFICATION → retry.** If an
**authenticated** approval is not obtained before expiry: **let the opportunity expire.**

The pressure-release valve is **preauthorization as Policy, not as voice**: the owner may
pre-authorise an *action class with bounded parameters* ("accept jobs ≤ $300 from clients with ≥3
verified payments, ≤ $900/week"). That is a Policy entry, evaluated in code, auditable — and it
removes the incentive to weaken the authentication rule under time pressure, which is how these
controls actually fail.

Implementation deferred until the authenticated channel exists. Notification-only may ship
earlier because it grants no authority.

---

## 15. ROFLO DEFECTS — RE-VERIFIED (Parts 19, 25)

Every prior finding was re-tested against source before being treated as fact. **All six
confirmed.** Line references are to `687cfc2`.

### D1 — `model` field accepted, echoed, never routed · **CRITICAL for Solvent**

Proof chain, each element verified independently:

| Evidence | Finding |
| --- | --- |
| `server.py:159, 216` | `model_name = body.model or eng.model_name` |
| `server.py:169, 186, 229, 243` | `model_name` used **only** in response payloads |
| `engine.py` signatures | **No** Engine method accepts a model or backend argument |
| `engine.py:52` | `self.backend = load_backend(config.backend)` — chosen once, at construction |
| `backends/*.py` | `stream(self, prompt, params)` — **no model parameter in any backend** |
| `base.py:32` | `self.model = config.model` — set once, **never reassigned anywhere** |

**Aggravating factor found this pass:** `/v1/models` (`server.py:139`) *does* enumerate the
upstream provider's real model list (`ollama.py:61–70` reads `/api/tags`). **The endpoint
advertises a menu it cannot honour.** A caller can list five models, request any one of them, and
be served the single process-wide model — with the requested name echoed back in the response.

**Consequence for Solvent:** a router that believes it selected a private or cheap model would be
confirmed in that belief by every response while every request went to one fixed model. This
defeats privacy control and cost attribution simultaneously, silently.

**Recommended remediation — containment, not a code change.** Do **not** make per-request model
selection through roflo's HTTP API a load-bearing control. Instead:

> **Run one roflo process per (model, privacy class). The Action Gate selects the process by URL.
> The `model` field is never used for routing by anything.**

This requires **no change to roflo**, and it is strictly safer than fixing the field: it makes the
privacy boundary a **process and network boundary** rather than a string in a JSON body that a
prompt-injected component could set incorrectly.

### D2–D6

| ID | Defect | Verified evidence | Severity | Must fix before Solvent relies on roflo? |
| --- | --- | --- | --- | --- |
| **D2** | No rate limit, no request-size cap, no concurrency cap; `max_tokens` has `ge=1` (**lower bound only, no upper bound**) | `server.py:38`; no limiter/semaphore/size check anywhere in `roflo/` | **HIGH** | **No — if contained.** The Action Gate and Governor must enforce caps *before* the call. roflo will stop nothing |
| **D3** | Bearer token compared with `!=` (not constant-time); `hmac`/`compare_digest` **never imported** | `server.py:114`; import search empty | LOW | No. Fix when convenient |
| **D4** | `/health` unauthenticated while all three other routes carry `dependencies=[Depends(require_key)]` | `server.py:129` vs `139/151/210` | LOW–MED | Only before binding beyond localhost |
| **D5** | Usage numbers from `len(text)/4`; **backends never read the real token counts upstream already returns** (ollama's `eval_count`/`prompt_eval_count`, vLLM's `usage`) | `engine.py:21–29`; grep for those keys in `backends/` → zero | **HIGH** | **YES — for accounting.** The Ledger must source cost from provider usage or billing, never from this heuristic |
| **D6** | No `LICENSE`/`COPYING`/`NOTICE`; no `license` field or classifier in `pyproject.toml` → default **all rights reserved**, while the README instructs readers to clone | filesystem + `pyproject.toml` | MED | Owner decision; needed before any third-party involvement or commercial deployment |

**Bottom line:** with roflo contained as a private, single-purpose process behind the Action Gate,
**only D5 must be solved before Solvent can rely on it** — and D5 is solved in the Ledger (source
cost from provider billing), not necessarily in roflo.

---

## 16. CONTROLLED SELF-GROWTH (Part 18)

Loop retained as specified. The architectural point is that it needs **no new pipeline**:

```
Analyst observes demand (read-only)
  → identifies repeated opportunity + capability gap
  → estimates lost revenue, implementation cost, ROI
  → emits a PROPOSAL                         ← Solvent may propose
  → Owner Channel → OWNER APPROVES           ← Solvent may NOT authorise
  → the approved proposal becomes an INTERNAL OPPORTUNITY
  → Qualification → Financial Governor → Orchestrator   ← the SAME controls as client work
  → sandboxed execution → test → small pilot → VERIFY (T1/T3)
  → Capability Registry promotion (owner-approved)
  → monitor; automatic rollback on degradation
```

**Why this matters:** running growth through the same pipeline as paid work means there is exactly
one path to committing resources. A separate expansion pipeline would be a second one.

**Weakness I must flag (§23, A7):** for an internal opportunity the owner is both the client and
the approver, so there is no external counterparty whose money validates the value. Internal
expansions should therefore require **stricter** evidence than client work, not looser — a
minimum of T1 verification plus a measured pilot, never a self-assessed "it worked."

---

## 17. BOOTSTRAP / SELF-FUNDING (Part 19)

Progression retained: low/no-upfront opportunities → existing/free tools → low compute cost →
real work → real revenue → operating reserve → owner-approved reinvestment → added capabilities →
higher-value work.

**Two rules that keep this honest:**

1. **All costs remain visible.** "Zero cost" is never claimed where cost exists. Self-hosted
   inference has compute and electricity cost; the Ledger records it even when it is small.
2. **Build cost counts.** The prior audit omitted this and it is the single most likely way the
   venture fails economically: the engineering cost of the P0 foundation is a real investment
   that must be recovered from margin. It belongs in the reinvestment analysis as a sunk-cost
   baseline the Analyst reports against, not as an invisible subsidy.

---

## 18. MARKETPLACE / LEGAL PRECONDITIONS (Part 21)

**These are not engineering problems and I cannot resolve them from the repository.** They are
preconditions on P1b, and they can invalidate the revenue engine entirely.

| # | Precondition | Why it is blocking | Resolved by |
| --- | --- | --- | --- |
| **L1** | Do the intended marketplaces permit the proposed level of automated operation? | Many platforms prohibit automated account operation, automated bidding, or require the contracted human to perform the work. Discovery of a source is **not** permission to operate on it | Reading each platform's ToS; a **recorded written determination** per source; owner approval. **No platform protection may be bypassed** |
| **L2** | Which legal person/entity bears contractual responsibility for work Solvent accepts? | Acceptance is a legal act: deliverables, liability, disputes, tax reporting (W-9/1099 or local equivalent), possibly counterparty screening | Owner/legal decision, recorded in Policy as an explicit authority |
| **L3** | Which payment rails are permitted for business use? | Most consumer P2P services prohibit business use on personal accounts; commercial processing carries KYC/AML obligations | Rail terms review; "approved" must mean approved *by the rail*, not merely by the owner |
| **L4** | Are the model checkpoints licensed for commercial output? | Solvent earns money from model output. Open-weights licences differ sharply on commercial use; some community fine-tunes are restricted or unlicensed. `roflo.toml` defaults to `qwen2.5:14b-instruct` | Per-checkpoint clearance, **recorded in the Capability Registry** |
| **L5** | What licence applies to this repository? (D6) | No licence = all rights reserved by default | Owner decision |

**Fail-closed rule:** where compliance is uncertain, the source is not used. Ambiguity is not
permission.

---

## 19. RECOMMENDED P0 ARCHITECTURE (Part 21 output)

**Six authorities, plus the architectural tests that keep the law true over time.**

1. **Policy Store** — permissions by action class (C0–C5), margin floors, spend ceilings,
   approved lists, `operating_mode`. Owner-writable only.
2. **Audit Log** — append-only, no delete path in code. "Cannot log ⇒ cannot act."
3. **Ledger** — verified money only; idempotent; corrections as new entries; cost sourced from
   provider billing (**never** from roflo's `len/4` estimator).
4. **Job Orchestrator** — durable resumable state machine; a timeout on **every** state;
   ODPAV contract enforced; no silent stalls.
5. **Financial Governor** — checked before every spend; conservative-bound gating with
   self-derived calibration (§10); spend anomaly detection.
6. **Action Gate** — the single egress chokepoint; enforces permission, operating mode, privacy
   class, budget pre-check, rate limits; writes to Audit.

**Mandatory architectural tests (these ARE the non-duplication law, in executable form):**

- No component other than the Policy Store may write permissions.
- **No path to a paid external call exists that does not pass the Financial Governor.**
- **No path to an external effect exists that does not pass the Action Gate** — enforced at the
  **network layer** (egress deny-by-default; only the Gate's identity may egress), not by code
  convention (§23, A1).
- Learning has no write path to Policy, Ledger, Audit, or any estimation parameter used in a gate.
- The Audit Log has no delete path.
- `operating_mode` is honoured at both chokepoints, including mid-job.
- A verification record whose verifier equals its executor is rejected at write time.
- No gate reads from the Project View.

---

## 20. RECOMMENDED FIRST REAL-JOB PROOF (Part 20)

**The prior recommendation is correct and I reaffirm it**, with three refinements. The milestone
answers *"Can Solvent safely and profitably complete ONE real job end-to-end?"* before
*"Can Solvent automatically find thousands?"*

```
REAL OPPORTUNITY (owner-sourced, manually)
  → INTAKE → CAPABILITY MATCH → COST ESTIMATE
  → FINANCIAL GOVERNOR → PERMISSION
  → EXECUTION (ODPAV) → VERIFY (T1) → QC
  → DELIVERY → PAYMENT → LEDGER
  → ACTUAL PROFIT → LEARNING (gated on T3)
```

**Refinements:**

1. **Bounded downside.** Choose a first job the owner could complete manually if Solvent fails.
   The point is to test the machinery, not to gamble a client relationship.
2. **The economic assertion is the deliverable, not the work product.** Exit criteria: actual
   profit computed from a **verified** payment (T3), **and** the estimate-versus-actual error
   recorded, **and** the Governor's decision reproducible from the Audit Log.
3. **Learning stays off until T3 lands.** The job teaches nothing until payment is verified. This
   is the first live exercise of the verify-before-learn law.

**Why discovery must come last in P1:** it is the most legally exposed component (§18, L1) and the
least useful before the loop works. Building it first risks constructing an automated pipeline
into a business process that has never completed once.

---

## 21. ADVERSARIAL REVIEW (Part 27)

Attacking the architecture proposed above, including the parts added in this revision.

| # | Attack | Severity | Assessment |
| --- | --- | --- | --- |
| **A1** | **Action Gate bypass at the library level.** Any dependency that makes its own network call — a document parser fetching a remote entity, an SDK, a webhook client, a model library — egresses without passing the Gate. "Single egress point" is a *claim about code*, and code has transitive dependencies | **CRITICAL** | The Gate is only real if enforced at the **network layer**: egress deny-by-default, only the Gate's identity permitted. Without that, the kill switch, privacy class and spend caps are all advisory. **This is the highest-priority finding in this document** |
| **A2** | **Prompt injection into a decision path.** Job postings, client documents and client messages are attacker-controlled and flow toward gates | **CRITICAL** | Only defence: gates evaluate **typed, enumerated, range-checked fields in code**. A model may propose; only code may decide. roflo guarantees nothing is filtered (§15) |
| **A3** | **Verification theatre.** A T2 "independent review" using the same model, on the same context, re-reading the same poisoned document, is not independent — it can be instructed to approve | **HIGH** | Hence the tier table (§9): T2 requires a different instance and, where possible, a check that does **not** re-read the attacker-controlled input. Business learning requires T3 (external fact), not T2 |
| **A4** | **Calibration selection bias.** The Governor's self-calibration is computed over accepted+completed jobs. Narrow selection to easy work and error statistics look excellent while capability degrades | **HIGH** | **Not fully solved.** Detector: rising acceptance rate with flat/falling realised margin (§10). Reported by the Analyst, not gated |
| **A5** | **Conformance capture.** Concept #5 is only as good as the requirement spec. If requirements are extracted from client text by a model, injection can loosen them, and the "cheap non-conforming option" then conforms | **HIGH** | Requirements that gate a purchase must be **owner- or client-confirmed structured fields**, not model-inferred prose. UNKNOWN ⇒ not conforming |
| **A6** | **ODPAV cost inversion.** Verification on every action can double token cost and destroy margin on small jobs — the verification law makes cheap work unprofitable | **MED–HIGH** | Verification intensity must scale with consequence, as QC does. A trivial formatting task gets a T1 machine check, not an independent review |
| **A7** | **Internal-expansion self-dealing.** For growth proposals the owner is client *and* approver; no external party validates the value | **MED** | Internal expansions need **stricter** evidence than client work (§16) |
| **A8** | **Kill switch causes the harm it prevents.** HALT mid-contract may breach an accepted obligation | **MED** | SAFE_MODE separates "stop expanding" from "stop honouring"; HALT must name the obligations it abandons (§14) |
| **A9** | **Project View drift into authority.** The moment a gate reads cost from the projection instead of the Ledger, there are two financial truths | **MED** | Architectural test: no gate reads from the View (§19) |
| **A10** | **Qualification acquires economics.** One "if strategic value is high, allow 5% below floor" and there are two margin rules | **MED–HIGH** | Qualification must contain **no** margin arithmetic; assert it in tests. Still the most likely duplication to actually occur |
| **A11** | **Policy Store as single point of failure.** Fail-closed means a policy outage halts the business | **MED** | Accepted deliberately. Mitigation: local, durable, boring, trivially readable |
| **A12** | **Orchestrator bottleneck and exception accumulation.** Everything routes through it; it is where "just this once" bypasses will collect | **MED** | Durable state machine over storage, not a long-lived brain. The concentration is the price of the one-orchestrator law |
| **A13** | **Model-routing deception** (D1) | **HIGH** | Contained by one process per (model, privacy class); never route by the `model` field (§15) |
| **A14** | **Unbounded spend** (D2) | **HIGH** | Governor pre-authorises; Gate enforces caps. roflo stops nothing |
| **A15** | **Unverified payment treated as revenue** | **HIGH** | Ledger: no external verification ⇒ not PAID. Where a rail offers no verification feed, record the **verification method** and never let a manually-asserted payment feed autonomy evidence |
| **A16** | **Cross-client memory contamination** | **MED** | Per-client partitioning; Learning operates on aggregates; no cross-tenant retrieval |
| **A17** | **Malicious document parsing (RCE)** | **HIGH** | Parse untrusted files in an isolated process with no egress and resource limits |
| **A18** | **Unprofitable automation** | **HIGH** | Build cost plausibly exceeds lifetime margin of low-value freelance work. The one-job proof (§20) makes this failure cheap and early |

**What should be removed or reduced:**

- **Nothing added in this revision should be dropped**, but **A1 must be solved at the network
  layer or the Action Gate should not be claimed as a control** — an enforcement point that can be
  bypassed by any dependency is worse than none, because it produces false confidence.
- **Discovery** stays last in P1 and is contingent on L1.
- **Voice authorisation** stays deferred; voice *notification* may ship early (§14).

---

## 22. DISPOSITIONS

### Repository identity

# A — CORRECT REPOSITORY / SOLVENT IMPLEMENTATION NOT YET BUILT

**Why A rather than F this time:** the account-wide search is now complete (13 repositories
including private ones, no Solvent; repository search 0; code search 0), no alternative
repository identifier has been supplied, and this repository is the one this session is scoped to
and the one in which the Solvent branch and Solvent documentation now live. Continuing to return
INCONCLUSIVE in the face of exhaustive negative evidence would be refusing to conclude.

**Falsifier, in one step:** name a different repository (`owner/repo` or URL). If one exists and
differs from `wjnnmyz4dd-droid/roflo`, this disposition becomes **D — WRONG REPOSITORY**
immediately, and everything in §§7–21 must be re-derived against the real codebase.

**Alternative reading, if you intend it:** if roflo was *deliberately* built as Solvent's
inference foundation, the disposition is **E — SOLVENT FOUNDATION EXISTS BUT BUSINESS LAYER IS
NOT YET BUILT**. The repository cannot answer that question; only you can. The architectural
consequences are identical either way.

### Architecture readiness

# P0_BLOCKERS_REMAIN

Not READY_FOR_P0_DESIGN, and explicitly **not** a PASS merely because the blueprint reads well.
Four blockers, in order:

| # | Blocker | Owner action |
| --- | --- | --- |
| **B1** | **A1 — Action Gate enforceability.** If egress cannot be enforced at the network layer, the kill switch, privacy class and spend caps are advisory | Decide the deployment model (process/network isolation) before building the Gate |
| **B2** | **L1/L2 — marketplace ToS and legal entity.** Can invalidate the entire revenue engine | External/legal verification; recorded determination |
| **B3** | **Authority map approval.** 14 authorities + 1 projection, 6 in P0, with Model Router and Expansion Pipeline dissolved | Approve, amend or reject §12 |
| **B4** | **L4/L5 — model-weight and repository licensing** | Owner decision |

---

## 23. RECOMMENDED NEXT ACTION

**Stop. No implementation.** In order:

1. **Resolve repository identity** — confirm `wjnnmyz4dd-droid/roflo` is the intended target, or
   name the other repository. Everything downstream depends on this one answer.
2. **Answer B2 and B4** (ToS, legal entity, payment rails, model licences, repo licence). These
   are cheap to ask and expensive to discover late, and B2 can invalidate P1b entirely.
3. **Decide B1** — whether egress can be network-enforced. This determines whether the Action Gate
   is a real control or a convention.
4. **Approve, amend or reject the §12 authority map**, including the two dissolutions.
5. **Only then** authorise P0 design-to-code for the six P0 authorities.

**STOP CONDITION HONOURED:** no code modified, no dependencies installed, no agents created, no
modules created, no restructuring, no marketplaces connected, no money spent, no PR opened.
Awaiting explicit owner approval.
