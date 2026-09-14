# Solvent — Master Architecture Audit

**Mode:** AUDIT ONLY. No code was modified, no dependencies installed, no configuration
changed, no external action taken. This document is the sole deliverable.

**Repository audited:** `wjnnmyz4dd-droid/roflo` @ `687cfc2` (single commit, entire history).
**Branches present:** `claude/solvent-architecture-audit-8ot9et`, `claude/ai-model-no-guardrails-24alg3` — byte-identical trees (`git diff --stat` returns empty).
**Date:** 2026-09-14.

---

## 0. HEADLINE FINDING — THE AUDIT PREMISE IS FALSE

The prompt directs me to "determine exactly what already exists in Solvent." The answer,
established by exhaustive search rather than inference:

**Solvent does not exist. No part of it has ever existed in this repository.**

Evidence:

- `git grep -il "solvent" $(git rev-list --all)` → **zero matches across all refs and all history.**
- The repository contains 15 Python source files totalling 2,308 lines including tests. Every
  one of them belongs to `roflo`, a self-hosted LLM inference CLI and OpenAI-compatible HTTP
  proxy. It is not a business system, and does not claim to be.
- Domain-term sweep over `roflo/` for the vocabulary this specification is built on:

  | Concept searched | Matches in source |
  | --- | --- |
  | `job` | 0 |
  | `opportunit*` | 0 |
  | `cost`, `spend`, `budget` | 0 (one false positive: `budget` = a token counter in `echo.py:19`) |
  | `price`, `pricing`, `invoice`, `payment`, `profit`, `margin` | 0 |
  | `approval`, `approve`, `permission`, `authoriz*` | 0 (two false positives: HTTP `Authorization` headers) |
  | `sqlite`, `database`, `persist`, `storage`, `memory`, `ledger`, `journal` | **0 — there is no persistence of any kind** |
  | `audit`, structured logging | 0 (two false positives: uvicorn `--log-level`) |
  | `worker`, `agent`, `orchestrat*` | 0 |
  | `client` (business sense) | 0 (all matches are `httpx.AsyncClient`) |

The required audit procedure (§40, STEP 1–10) therefore returns a uniform result:

| Step | Trace requested | Result |
| --- | --- | --- |
| 1 | Map repository structure | Done — see §1 below |
| 2 | Identify existing architectural authorities | **None.** One `Engine` class; it decides nothing but which backend to call |
| 3 | Trace job lifecycle end-to-end | **No job concept exists** |
| 4 | Trace financial decision path | **No financial concept exists** |
| 5 | Trace permissions | **None.** One shared bearer token on an HTTP port (§10.2) |
| 6 | Trace memory/storage | **None. Zero bytes are persisted.** Chat history is an in-process Python list discarded on exit (`cli.py:54`) |
| 7 | Trace execution/tool architecture | Exists only as "call one LLM backend." No tools, no capabilities, no workers |
| 8 | Trace opportunity discovery | **None** |
| 9 | Trace quality-control mechanisms | **None** |
| 10 | Trace security boundaries | One: an optional bearer-token check on the server port. See §10 |

Consequently **0 of the 46 specification sections are implemented**, and **0 of the 17
acceptance criteria in §45 can be evidenced.** Everything below §2 of this document is
therefore *prospective design analysis*, not an audit of running code, and is labelled as
such. I am not going to describe a system that isn't there as though it were.

**What this means practically:** this is a greenfield build, not an extension. That is
*good news* for the architectural law the specification insists on — there is no legacy
duplication to unwind, no competing authority to consolidate, and no migration cost. It
also means the specification's central risk is not "existing overlap" but **"overlap that
will be created on day one if all 46 sections are built as 46 modules."** Preventing that
is where the remainder of this audit spends its effort.

### A second finding, less obvious and more important

The one artifact that *does* exist, `roflo`, is not neutral with respect to Solvent. It is
**architecturally hostile to placing governance in the model layer**, by explicit design.
Its own source says so (`engine.py:1–7`):

> "there is no filter step, no classifier pass, no prompt rewriting and no post-processing
> of what the model returns. Text goes to the weights and comes back."

and the README: *"Read `roflo/engine.py` — it's the whole request path, and there is nowhere
for a filter to hide."*

This is a perfectly good property for a **transport**, and it is the correct place for
Solvent's inference layer to sit — a dumb, auditable pipe. But it forces an architectural
rule that must be written down now, before anyone builds on it:

> **Solvent's governance, policy, privacy and financial controls can never be enforced
> inside the inference layer. `roflo` will silently enforce nothing. Every control must be
> executed in code, above the model call, over structured data.**

If Solvent is ever built such that a control is implemented as an instruction in a system
prompt, `roflo` is the component that guarantees that control does not exist. See §10 and
§18 for the concrete failure paths this opens.

---

## 1. EXISTING ARCHITECTURE (what is actually in the repo)

```
roflo/
  cli.py            238  argparse CLI: chat REPL, complete, render, check, serve, info
  config.py         111  TOML + ROFLO_* env → Config dataclass. Rejects unknown keys
  engine.py         175  THE request path: messages → render → backend.stream() → chunks
  prompt.py         145  6 pure-function chat templates (chatml/llama3/mistral/gemma/alpaca/raw)
  server.py         259  FastAPI, OpenAI-compatible /v1/chat/completions, /v1/completions, /v1/models, /health
  types.py           92  Message, SamplingParams, Chunk, Usage, ModelInfo, BackendError
  backends/
    __init__.py      69  Lazy registry: name → class factory
    base.py         126  Backend ABC + HTTPBackend (httpx, SSE parsing, error wrapping)
    ollama.py        75  POST /api/generate {raw: true}
    llamacpp.py      65  POST /completion
    vllm.py          70  POST /v1/completions
    transformers_.py 114  In-process HF checkpoint, threaded streamer
    echo.py          25  Test double
tests/              726  6 test files (README claims 94 tests; not executed — see note)
```

**Dependencies:** `fastapi`, `uvicorn`, `httpx`, `pydantic`; optional `transformers`/`torch`/`accelerate`; dev `pytest`.

**Architecture in one line:** `Config` → `Engine` → one `Backend`. Stateless, single-process,
single-model, no storage, no auth beyond one optional shared token, no logging beyond
uvicorn's access log.

**Quality of what exists:** genuinely good for its stated scope. Clear single-responsibility
separation (rendering is not backend work; backends never template), lazy backend resolution
so `torch` is not an import cost for everyone, config that rejects typos instead of silently
ignoring them (`config.py:100–102`), and a careful streaming detail most implementations get
wrong — partial stop-marker hold-back across chunk boundaries (`engine.py:32–44`, `119–145`).
This is not a codebase that needs replacing. It needs to be correctly *scoped*: it is one
provider adapter beneath a future router, and nothing more.

> **Note on test execution:** the README claims 94 passing tests. I could not verify this —
> `pytest` is not installed in this environment, and installing it is prohibited by the
> audit's own stop condition. Test *status* is therefore unverified; test *existence* and
> content are confirmed by reading.

---

## 2. EXISTING FEATURES SATISFYING REQUIREMENTS

Exactly one specification section is even partially served by existing code.

| Spec § | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| §18 | Cost-aware model routing | **PARTIAL (~15%)** | A provider abstraction exists (`Backend` ABC, 5 adapters, lazy registry). The *abstraction* a router needs is present. The *router* is absent: one process serves one model chosen at construction (`engine.py:52`), and there is no cost, latency, privacy-class, complexity or budget input anywhere |
| §37 | Local/private processing path | **PARTIAL (~10%)** | `transformers` backend runs in-process with no network hop (`transformers_.py`), which is a genuine private-inference path. But nothing *classifies* data sensitivity or *enforces* that sensitive data uses it — see §10.3 |
| §31 | Tool/model failure resilience | **PARTIAL (~5%)** | Backend failures are typed and surfaced rather than swallowed (`BackendError`, `base.py:85–91`; `server.py:120–127` → HTTP 502). This is correct fail-closed *reporting*. There is no alternative-provider logic, no cost-difference check, no fallback at all |

**Everything else in the specification — §§1–17, 19–30, 32–36, 38–39 — is MISSING in full.**

---

## 3. PARTIAL MATCHES

See the table in §2. There are three, all thin, all in the inference layer. There are no
partial matches anywhere in the business, financial, governance, memory, or discovery
domains, because those domains have no code.

---

## 4. MISSING CAPABILITIES

All of them. Rather than restate 43 sections, here is what is missing grouped by the
*minimum viable business loop*, because the gap is total and the useful information is the
ordering:

**Cannot currently happen at all:** discover an opportunity · represent a client · represent
a job · hold any state between processes · make any decision · record any decision · compute
any cost · hold any budget · require any approval · refuse any action · remember any outcome
· receive any money · know whether money was received · pause anything.

The system cannot persist a single fact. Every other gap is downstream of that one.

---

## 5. DUPLICATE / OVERLAPPING PROPOSALS THAT SHOULD NOT BE ADDED

**This is the highest-value section of this audit.** The specification's own law is
"one responsibility = one authority, no duplication." Taken literally as a build order, the
specification violates that law: its 46 sections would become ~40 modules with heavy
overlap, several of them second financial authorities and second memory authorities — exactly
what §9, §10, §19, §29 and §32 each separately warn against.

The following sections are **real requirements** but **must not become their own authority**:

| Spec § | Proposed as | Verdict | Correct home |
| --- | --- | --- | --- |
| §10 | Opportunity-cost / portfolio engine | **DO_NOT_ADD** | A *ranking input*. Portfolio comparison is a query over candidate jobs; the accept/reject verdict stays with Qualification, the economics stay with the Governor |
| §29 | Spend anomaly protection | **DO_NOT_ADD** | A *detector inside the Financial Governor*. A second component that can THROTTLE/BLOCK spending is a second financial authority by definition |
| §32 | Revenue concentration risk | **DO_NOT_ADD** | A *read-only analysis* in the Analyst. It reports; it never gates |
| §23 | Reinvestment | **DO_NOT_ADD** | An *Analyst recommendation* + an *owner policy edit*. Solvent proposes, Policy governs. No module |
| §12 | Strategic / reputation value | **DO_NOT_ADD** | A *bounded scoring input* to ranking. Given its own module it becomes the override that launders unprofitable work past the margin floor — the specification anticipates this and is right to |
| §11 | Client quality | **DO_NOT_ADD as authority** | A *derived score* in Business Memory, consumed by ranking. Never an acceptance gate |
| §34 | Earned autonomy | **DO_NOT_ADD** | Not a module. It is *the owner editing the Policy Store*, using evidence. See §12.3 for why the evidence source matters enormously |
| §28 | Kill switch | **DO_NOT_ADD as module** | A *state flag in the Policy Store* plus a mandatory check at every spend and every external action. A separate kill-switch service is a second governance authority and a new bypass surface |
| §30 | Platform-rule change protection | **DO_NOT_ADD** | *Per-source adapter health/compliance state* inside Discovery. Same concern as §31 (the external world changed), different trigger |
| §15 | Client value-add | **DO_NOT_ADD as pipeline** | A value-add, once a client accepts it, **is a new opportunity**. It re-enters the *existing* Qualification pipeline. Giving it a parallel path creates a second way to commit to work — i.e. a Governor bypass |
| §17 | Quality control | **DO_NOT_ADD as authority** | A *capability* invoked by the orchestrator at a policy-determined intensity. QC that can independently block delivery without the orchestrator's state machine knowing is a silent stall |
| §24 §25 §35 | Voice · escalation · dashboard | **DO_NOT_ADD as three** | **One Owner Channel.** Three interfaces to one human, with one authentication story. Three modules = three places an instruction can enter with three different levels of verification |
| §6 | "Specialized capabilities" (13 listed) | **DO_NOT_ADD 13 agents** | The specification explicitly warns against this and is correct. One execution layer, a capability *registry*, and the smallest number of executors that safely covers the catalogue |

**Genuinely distinct authorities that look like duplicates but are not** — do not consolidate these:

- **Financial Governor (§9) vs. Ledger (§21/§22).** The Governor *decides* using estimates,
  before the fact. The Ledger *records* verified truth, after the fact. Merging them lets a
  forecast contaminate the books, and lets the thing that wants to say yes also keep score.
- **Business Memory (§19) vs. Audit Log (§36).** Merging these is the subtlest and most
  dangerous consolidation available, and I recommend against it emphatically. Memory is
  mutable and Learning writes to it (§20). If the audit trail lives in Memory, then Learning —
  the one subsystem explicitly permitted to modify stored state — can rewrite the record of
  what Solvent did. The Audit Log must be **append-only, deletion-incapable, and written by
  every component but rewritable by none, including Learning and including the owner's
  routine tooling.**
- **Execution-gap detection (§14) vs. value-add (§15).** The specification separates these
  correctly. "I cannot finish without X" and "the client would benefit from Y" have opposite
  urgency, opposite billing treatment, and opposite failure modes (silent fabrication vs.
  silent scope expansion).

---

## 6. EXISTING COMPONENTS THAT SHOULD BE EXTENDED

Only one, and only slightly.

**`roflo` — EXTEND, but not yet, and not much.** It becomes *one provider adapter* beneath
Solvent's Model Router (§18). The extensions eventually needed are narrow and additive:
per-request model selection (see the defect in §10.1), real token accounting rather than
`len/4` (see §11.2), and a declared privacy class per backend configuration. None of these
are P0. **`roflo` must not grow business logic, cost policy, or governance.** Its value to
Solvent is precisely that it is a thin, readable, auditable pipe; a governance feature added
here would be the first duplicate authority in the system.

**Everything else in the specification requires new components.** There is nothing else to
extend. Where the specification asks "can this be satisfied by extending an existing
authority?", the honest answer in every one of the 46 sections is: no, because there is no
existing authority.

---

## 7. COMPONENTS THAT SHOULD BE CONSOLIDATED

Nothing existing (there is nothing to consolidate). **Prospectively**, consolidate before
building, per §5: the 13 listed spec sections that must not become modules, most importantly
the three owner interfaces (§24 + §25 + §35 → one Owner Channel) and the four economic
sections (§10 + §29 + §32 + §23 → inputs and analyses around the single Financial Governor).

---

## 8. COMPONENTS THAT SHOULD BE REPLACED

**None.** `roflo` is well-built for its scope and correctly scoped as a transport. There is
no Solvent component to replace. Any recommendation to "replace" something here would be
fabricated.

---

## 9. RECOMMENDED SINGLE-AUTHORITY MODULE BOUNDARIES

The smallest set of authorities that covers all 46 sections without overlap. **Fourteen
modules, of which six are P0.** Full §42 reports follow in §21; the map first:

```
                      ┌───────────────────────────────────┐
   OWNER ────────────►│  1. OWNER POLICY STORE            │  owner-writable ONLY
      ▲               │  permissions · margin floors ·    │  (kill switch = a flag here)
      │               │  autonomy level · kill switch     │
      │               └──────────────┬────────────────────┘
      │                    consulted │ by everything
  ┌───┴────────────┐                 ▼
  │ 12. OWNER      │◄──── 2. SOLVENT CORE ──────────────────────────────┐
  │     CHANNEL    │      (job lifecycle state machine, supervision,     │
  │ dashboard ·    │       gap routing — the ONLY orchestrator)          │
  │ escalation ·   │         │        │        │        │               │
  │ voice          │         ▼        ▼        ▼        ▼               │
  └────────────────┘   ┌─────────┐ ┌──────┐ ┌──────┐ ┌──────────┐       │
                       │3.DISCO- │ │4.QUA-│ │10.EX-│ │13.CLIENT │       │
                       │  VERY   │►│ LIFI-│ │ECUTION│ │ CHANNEL │       │
                       │+adapters│ │CATION│ │ layer│ └──────────┘       │
                       └─────────┘ └──┬───┘ └───┬──┘                    │
                                      │         │                       │
                        asks, never   │         ▼                       │
                        decides ──────┤   ┌──────────┐                  │
                                      │   │11. MODEL │──► roflo ──► weights
                                      ▼   │   ROUTER │                  │
                       ┌──────────────────┴──┐└──────┘                  │
                       │ 6. FINANCIAL        │                          │
                       │    GOVERNOR         │ ◄── 5. CAPABILITY        │
                       │ the ONE money gate  │        REGISTRY          │
                       │ (+ spend anomaly)   │                          │
                       └──────────┬──────────┘                          │
                                  │                                     │
        ┌─────────────────────────┼─────────────────────────┐           │
        ▼                         ▼                         ▼           │
  ┌───────────┐          ┌────────────────┐        ┌──────────────┐     │
  │ 7. LEDGER │          │ 8. BUSINESS    │        │ 9. AUDIT LOG │◄────┘
  │ verified  │          │    MEMORY      │        │ append-only  │  everything
  │ money     │          │ mutable facts  │        │ NO deletes   │  writes here
  └─────┬─────┘          └───────┬────────┘        └──────┬───────┘
        │                        │                        │
        └────────────┬───────────┴────────────────────────┘
                     ▼                    ┌──────────────────────┐
          ┌────────────────────┐          │ 14. EXPANSION        │
          │ 13. ANALYST /      │─────────►│     PIPELINE         │
          │     LEARNING       │ proposes │ sandbox→pilot→deploy │
          │ READ-ONLY.         │          │ gated by Policy (1)  │
          │ Emits RECOMMENDA-  │          └──────────────────────┘
          │ TIONS ONLY.        │
          └────────────────────┘
```

**The four load-bearing rules of this map:**

1. **Only the Policy Store grants authority, and only the owner writes to it.** No component
   may write its own permissions. Learning may not write here at all.
2. **Only the Financial Governor authorises money.** Qualification, Execution and the Router
   all *ask* it. None of them computes a competing verdict.
3. **Only Solvent Core commits a job to a state.** Value-adds, revisions and expansions all
   re-enter through Qualification; there is no side door.
4. **Everything writes to the Audit Log; nothing edits it.**

---

## 10. SECURITY / PRIVACY FINDINGS

Findings **10.1–10.6 are defects in code that exists today** and are verifiable now.
**10.7–10.10 are forward-looking hazards** for the system that does not yet exist.

### 10.1 `model` in the API request is accepted and silently ignored — *the most consequential defect found*

`server.py:159` — `model_name = body.model or eng.model_name`. That value is used **only** to
populate the response body (`server.py:169, 186, 229, 244`). It is never passed to the
engine; `eng.chat(...)` and `eng.stream_chat(...)` take no model parameter, and the backend
is fixed at `Engine.__init__` (`engine.py:52`). A caller who requests `model: "cheap-local"`
is served by whatever model the process was started with, **and the response confidently
echoes back the model name they asked for.**

For `roflo`'s own stated scope this is a defensible simplification. For Solvent it is a
loaded gun: a §18 cost-aware router built on `roflo`'s OpenAI endpoint would believe it is
routing sensitive work to a private model and cheap work to a cheap model, receive
confirmation in every response that it did so, and in fact route **everything** to one
process-wide model. This is simultaneously a silent failure (§44), an unsafe fallback (§44),
a privacy-control bypass (§37), and a cost-attribution error (§22). **Any router must select
providers by configuration above `roflo`, never by the `model` field through it — until and
unless this is fixed.**

### 10.2 There is exactly one credential, it is a shared bearer token, and it is compared non-constant-time

`server.py:114`: `if token != expected`. Ordinary Python string comparison short-circuits on
first mismatch and is theoretically timing-attackable; `hmac.compare_digest` is the standard
fix. Low severity in isolation. Structurally more important: **this single token is the
entire access-control model.** There are no identities, no roles, no scopes, no per-caller
attribution. Solvent's §27 permission classes (AUTONOMOUS / CONDITIONAL / OWNER_APPROVAL /
PROHIBITED) have no foothold here and must be built entirely above this layer.

### 10.3 Nothing distinguishes a private inference path from a third-party one

`base_url` is free-form (`config.py:20`) and may point anywhere on the internet; `api_key`
(`config.py:22`) exists precisely so it can point at a commercial endpoint. The in-process
`transformers` backend is genuinely local; the other three are HTTP to wherever the operator
aimed them. **No code classifies, labels, or enforces this distinction.** A §37 policy of
"confidential client documents are processed locally only" is unenforceable and, worse,
*unobservable* at this layer: nothing records which class of endpoint a given request went to.

### 10.4 `/health` is unauthenticated while every other route is gated

Compare `server.py:129` (`@app.get("/health")`, no dependency) with `server.py:139, 151, 210`
(all carry `dependencies=[Depends(require_key)]`). Anyone who can reach the port learns the
backend kind, the model name and the template without credentials. Minor information
disclosure; trivially fixed; worth knowing before binding beyond localhost.

### 10.5 No rate limiting, no request size cap, no concurrency cap

Nothing in `server.py` bounds request rate, prompt length, `max_tokens`, or in-flight
requests. Against a local model this costs compute and electricity. Against a `base_url`
pointed at a paid endpoint it is **unbounded spend with no ceiling in-process** — directly
contrary to §29 and §45's "spending is bounded." The Financial Governor must therefore
enforce budget **before** the call is made, because `roflo` will not stop anything. (Credit
where due: `cli.py:166–168` does warn when serving beyond localhost without a key.)

### 10.6 Secrets live in plaintext config and environment

`backend.api_key` and `server.api_key` come from `roflo.toml` or `ROFLO_*` env vars
(`config.py:69, 79`). `.gitignore` covers `.env` but **not `roflo.toml`**, and a `roflo.toml`
*is committed to this repository* (with `api_key` commented out — fine today, a trap
tomorrow). §37's "credential isolation" and "secret management" have no implementation.

### 10.7 Prompt injection is the deepest architectural hazard in the entire specification

Solvent's inputs are, by design, hostile: job postings, client documents, client messages,
web pages, CI output, marketplace listings. §37 says external content "must never be able to
rewrite Solvent's governance through embedded instructions." That is the right requirement,
and it is **not achievable by instruction.** It is achievable only by architecture:

> **Every gate — qualification, financial, permission, privacy, delivery — must be executed
> in code over typed, enumerated, range-checked fields. No gate may be satisfied by free-form
> model output. A model may propose; only code may decide.**

Concretely: a model may emit `{estimated_hours: 4.0, capability: "SPREADSHEET", confidence: 0.7}`,
and code compares those numbers against policy. A model may **never** emit "this job is
approved" or "margin check passed" and have that string believed. Given §0's finding that
`roflo` enforces nothing, this rule is the only thing standing between a crafted job posting
and Solvent's bank balance.

### 10.8 Document intake (§13) is a remote-code-execution surface

Parsing untrusted PDF/DOCX/XLSX in-process, using libraries with long CVE histories, on
attacker-supplied files, is how systems get owned. This needs process isolation, resource
limits, and no network egress from the parser. Separately, §13's own warning is correct and
under-stated: shipping a confidential client document to a third-party OCR or AI service is
an exfiltration event that no amount of later policy undoes.

### 10.9 Client-data isolation has no substrate

There is no storage, so there is no tenancy model, no per-client segregation, no retention
policy, no deletion path, and no sensitivity classification. §19's requirements (provenance,
timestamp, access control, retention, sensitivity, deletion) must be designed into Business
Memory's schema from the first migration. Retrofitting data classification onto a populated
store is significantly harder than starting with it.

### 10.10 CORS is permissive when enabled

`server.py:100–106` sets `allow_methods=["*"], allow_headers=["*"]` for configured origins.
`allow_credentials` is not set, which limits the damage. But an operator who sets both
`cors_origins` and `api_key` is inviting browser-side exposure of that key.

---

## 11. FINANCIAL-GOVERNANCE FINDINGS

### 11.1 There is no financial governance of any kind

Zero matches for cost, price, budget, spend, margin, profit, invoice or payment. §45's
"Financial Governor cannot be bypassed" and "spending is bounded" are **not merely unmet —
they are unrepresentable.** There is nothing to bypass.

### 11.2 Do not build accounting on `roflo`'s usage numbers

`engine.py:21–29` — `estimate_tokens` is `len(text) / 4`, and the docstring says plainly:
*"treat these numbers as indicative, not exact."* That heuristic is a rough fit for English
prose and badly wrong for code, JSON, CJK text, and any tokenizer that isn't the one being
approximated. The HTTP backends report real token counts upstream and `roflo` discards them.

If Solvent's §22 profit accounting derives API cost from these figures, **actual profit will
be computed from a character count.** With a self-hosted local model the marginal API cost is
near zero and the error is harmless; the moment `base_url` points at a metered endpoint, the
books are wrong in a way that will not be noticed, because nothing compares them to an
invoice. **The Ledger must take cost from the provider's own reported usage or billing, never
from a length heuristic.**

### 11.3 Estimate-versus-actual must be structurally separated, not merely compared

§22 asks for ESTIMATED vs ACTUAL comparison — good. The architectural point is stronger: the
estimate is a *forecast produced by a component that wants the job to proceed*, and the actual
is *verified fact*. These must live in different stores with different write permissions
(Governor writes forecasts; Ledger accepts only verified inflows and receipted costs).
Otherwise the comparison compares a number to itself.

### 11.4 "Expected revenue is not collected revenue" needs enforcement, not documentation

§21 states this correctly. The enforcement is: **the Ledger's profit figures, the operating
reserve, and any reinvestment recommendation may only be computed from `PAID` states backed
by an external verification event** (payout webhook, bank reconciliation, marketplace release).
A job marked delivered is not revenue. If any code path lets `INVOICED` count toward reserve,
Solvent will recommend reinvesting money it does not have.

---

## 12. PERMISSION / GOVERNANCE FINDINGS

### 12.1 No permission model exists

Confirmed by search. The only authorisation primitive in the codebase is an optional shared
bearer token on one HTTP port. §27's four classes, §28's kill switch, §34's earned autonomy
and §7's acquisition limits all have zero foundation.

### 12.2 The kill switch has a fail-open trap and a fail-closed cost — choose deliberately

A kill switch checked only at job *start* does not stop an in-flight job from spending,
calling APIs, or messaging a client. It must be checked **immediately before every spend and
every external action**, not at lifecycle boundaries.

The corollary is uncomfortable and should be accepted explicitly: if the Policy Store is
unreachable, the safe behaviour is to **halt** — which means a policy-store outage stops the
business. That is the correct trade (§45: "unknown critical information fails closed"), but
it makes the Policy Store a deliberate single point of failure and it must be built to be
boring, local, durable, and trivially readable.

### 12.3 Earned autonomy (§34) contains a self-expansion loophole

§34 proposes expanding autonomy based on evidence: successful jobs, accurate estimates, low
dispute rate. **Every one of those metrics would be computed by the system whose autonomy is
at stake, from a store that Learning is permitted to write to.** That is a closed loop in
which the beneficiary produces the evidence.

Mitigation, which must be architectural rather than procedural:

> **Autonomy evidence may be drawn only from the Ledger (verified cash movements) and the
> Audit Log (append-only, deletion-incapable). It may never be drawn from Business Memory,
> which Learning writes to.**

This is the single strongest argument for the Memory/Audit-Log split recommended in §5, and
I would treat it as non-negotiable.

### 12.4 Learning must not be able to move a policy floor indirectly

§20 correctly forbids Learning from rewriting financial policy. The subtle bypass: Learning
*is* permitted to improve cost and time estimation. An estimation model that drifts optimistic
lowers estimated cost, raises apparent margin, and pushes marginal jobs over an unchanged
floor. **The floor was never edited; the same effect was achieved.**

I do not have a clean solution, and I am flagging it as an **open design question for P0**
rather than pretending it is solved. Partial mitigations: calibrate estimate models only
against *verified* Ledger actuals; alarm when accepted-work volume or average estimated margin
shifts beyond a band without a corresponding shift in realised margin; require owner approval
to promote a new estimation model. None of these is airtight.

### 12.5 Voice and escalation must authenticate the instruction, not the channel

§24 already says voice does not bypass permissions. Strengthen it: a phone call is an
*unauthenticated, spoofable, replayable* channel, and §25 makes it the **primary** path for
high-urgency approvals — exactly the conditions (time pressure, voice, a 20-minute expiry)
under which social engineering works best. Consequential approvals need out-of-band
confirmation or a pre-shared challenge, and the granted authority must be recorded as a
specific, scoped, single-use grant ("approve job J-1041 up to $X"), never as a session-wide
elevation.

---

## 13. MEMORY / LEARNING FINDINGS

**Zero persistence exists.** No database, no file store, no schema, no cache. `cli.py:54`
holds chat history in a Python list that dies with the process.

Findings for the build:

1. **One memory authority, as §19 demands — but split along the integrity axis, not the
   subject axis.** Business Memory (mutable, derived, Learning-writable) and the Audit Log
   (append-only, immutable, universally-written) are *not* two memory systems in the sense
   §19 prohibits. They are one memory authority and one evidence authority, and conflating
   them destroys the evidence (§5, §12.3).
2. **Schema-first on classification.** Provenance, timestamp, sensitivity class, retention
   policy and deletion rule must be columns from migration #1, not a later addition.
3. **Memory contamination is a live risk.** Client A's documents, pricing and failure patterns
   must not leak into Client B's work through a shared "learnings" table. Learning must
   operate on *aggregates and patterns*, with raw client content partitioned and never
   retrieved cross-tenant.
4. **Learning emits recommendations only** — typed proposals into the Owner Channel, never
   direct writes to Policy, margins, or permissions (§20, §45).

---

## 14. OPPORTUNITY-DISCOVERY FINDINGS

**Nothing exists.** Beyond that, three findings that materially affect whether P1 is buildable
at all — and I want to be direct, because the specification's business case depends on them:

1. **Automating freelance marketplaces very commonly violates their Terms of Service.** Many
   platforms prohibit automated account operation, automated bidding, or require that the
   contracted human perform the work. §30 and the fail-closed law together mean: **no account
   is operated programmatically until that specific platform's ToS has been read, a written
   determination recorded, and the owner has approved it.** Discovery of a source is not
   permission to operate on it. This is not a technical blocker I can engineer around; it is a
   legal precondition on the revenue engine.
2. **Someone is legally accepting a contract.** When Solvent accepts a job, a real person or
   entity takes on a real obligation — deliverables, deadlines, liability, disputes, tax
   reporting (W-9/1099 or local equivalent), and potentially sanctions/KYC screening of
   counterparties. The specification treats acceptance as a business decision; it is also a
   legal act binding the owner. This belongs in §27's PROHIBITED/OWNER_APPROVAL classification
   explicitly.
3. **Payment rails carry their own prohibitions.** §21's "approved P2P rails" deserves a
   flag: most consumer P2P services (Zelle, Venmo, Cash App) prohibit business use on personal
   accounts, and commercial processing brings KYC/AML obligations. "Approved" must mean
   approved *by the rail's terms*, not merely approved by the owner.

---

## 15. LICENSING FINDINGS

1. **This repository has no licence. This is a real finding, not a formality.** There is no
   `LICENSE`/`COPYING`/`NOTICE` file, and `pyproject.toml` has no `license` field and no
   licence classifier. Under default copyright, "no licence" means **all rights reserved** —
   no one may use, copy, modify or distribute it. The README meanwhile instructs readers to
   `git clone` it. For the owner's private use this is harmless; for any contribution, reuse,
   or commercial deployment involving another party it is unresolved. **The owner should
   decide a licence before Solvent is built on top of this code.**
2. **Direct dependencies are believed permissive** — FastAPI (MIT), Uvicorn (BSD-3), httpx
   (BSD-3), Pydantic (MIT), transformers (Apache-2.0), PyTorch (BSD-3), accelerate
   (Apache-2.0), pytest (MIT) — all commercial-use-friendly with attribution. **I could not
   verify this in-session:** none of these packages is installed in this environment and
   installing them is prohibited by the audit's stop condition. Treat the list as "expected,
   unverified" and confirm from the installed distributions before commercial deployment.
3. **Model weights are the licence risk that actually bites, and it is not in this repo.**
   `roflo` ships no weights, but Solvent would *earn money from their output*. Open-weights
   licences differ sharply on commercial use: Llama's community licence has conditions
   (including a monthly-active-user threshold), Gemma has a use policy, Qwen and Mistral vary
   by model, and some community fine-tunes inherit restrictions or are simply unlicensed. The
   default in `roflo.toml` is `qwen2.5:14b-instruct`. **Every checkpoint used for paid client
   work needs its licence cleared for commercial use and recorded**, and that record belongs in
   the Capability Registry, not in someone's memory.
4. **§38's question about outside repositories cannot be answered yet.** Nothing in this
   repository references or derives from an external project. The specification's §2 alludes to
   "zero-capital autonomous work systems" as inspiration — if specific projects are intended
   (AutoGPT-lineage agents and similar), the owner must name them for a licence review before
   any code or structure is drawn from them. Architectural *inspiration* is not copyrightable;
   copied code is. Prefer reimplementation, as §38 says.

---

## 16. DEPENDENCIES

**Between modules** (the build-order constraint that actually matters):

```
1  Policy Store        ← depends on nothing. Build first.
9  Audit Log           ← nothing. Build first (in parallel).
8  Business Memory     ← Policy (classification/retention rules)
7  Ledger              ← Audit Log
2  Solvent Core        ← Policy, Audit, Memory  (the job state machine)
6  Financial Governor  ← Policy (floors, budgets), Ledger (actuals)
5  Capability Registry ← Policy (what is approved)
4  Qualification       ← Core, Governor, Capability Registry, Memory
3  Discovery           ← Policy (approved sources), Core
10 Execution           ← Core, Capability Registry, Router
11 Model Router        ← Policy (privacy class), Governor (budget), roflo
12 Owner Channel       ← Core, Policy  (never writes Policy directly)
13 Client Channel      ← Core, Audit
13 Analyst/Learning    ← Ledger, Memory, Audit  (READ-ONLY on all three)
14 Expansion Pipeline  ← Policy, Analyst, Capability Registry
```

**Critical path:** Policy + Audit Log → Memory + Ledger → Core → Governor → everything else.
**No module may be built before its dependencies, and nothing at all may be built before the
Policy Store**, or it will be retrofitted with governance later — which is how governance
becomes bypassable.

**External dependencies:** the existing four runtime packages, plus (eventually, all requiring
owner approval) a database, a payment/marketplace integration, a telephony provider for §25,
and document-parsing libraries for §13.

---

## 17. IMPLEMENTATION DIFFICULTY

Per module, assuming a competent single developer and counting *design* effort, not just code:

| Module | Difficulty | Note |
| --- | --- | --- |
| Policy Store | **Low** code / **High** design | A config store with an approval workflow. Getting the *authority model* right is the hard part |
| Audit Log | **Low** | Append-only table + a discipline. Easy to build, easy to undermine later |
| Business Memory | **Medium** | The classification/retention schema is the work |
| Ledger | **Medium–High** | Money correctness, idempotency, reconciliation. Boring and unforgiving |
| Solvent Core | **High** | A durable, resumable state machine with no silent stalls |
| Financial Governor | **High** | Estimation is genuinely hard; the *gate* is easy, the *numbers* are not |
| Capability Registry | **Low–Medium** | A catalogue with honest self-assessment. The honesty is the hard part |
| Qualification | **High** | Must orchestrate four inputs and own no economics of its own |
| Discovery + adapters | **High** | Per-source, ToS-bound, externally fragile, and the part most likely to be legally blocked |
| Execution layer | **Medium–Very High** | Scales with the capability catalogue. Start with one capability |
| Model Router | **Medium** | The abstraction exists; see the §10.1 defect |
| Owner Channel | **Medium** (**High** with voice) | Telephony + authenticated approvals |
| Client Channel | **Medium** | Scope-change recording is the requirement, not messaging |
| Analyst / Learning | **Medium–High** | Easy to build, easy to build *wrongly* (see §12.4) |
| Expansion Pipeline | **High** | "Sandbox" is doing a lot of work in §33; see §19 |

---

## 18. TECHNICAL RISKS

| # | Risk | Severity | Note |
| --- | --- | --- | --- |
| T1 | Prompt injection reaching a decision path | **Critical** | §10.7. The single most dangerous failure mode in the design |
| T2 | Governance implemented as prompt instructions | **Critical** | §0. `roflo` guarantees such controls do not exist |
| T3 | Silent model mis-routing via ignored `model` field | **High** | §10.1. Verified defect in existing code |
| T4 | Cost computed from a `len/4` heuristic | **High** | §11.2. Wrong books, silently |
| T5 | Unbounded spend — no rate/size/concurrency limits | **High** | §10.5. Governor must gate *before* the call |
| T6 | Solvent Core as bottleneck and single point of failure | **High** | Mitigate: durable resumable state machine, not a long-lived brain |
| T7 | Learning contaminating the evidence that governs autonomy | **High** | §12.3. Mitigated only by the Memory/Audit split |
| T8 | Optimistic estimate drift moving the effective margin floor | **High** | §12.4. **Unsolved — open P0 design question** |
| T9 | Malicious document parsing (RCE) | **High** | §10.8. Needs process isolation |
| T10 | Kill switch not checked at spend/action granularity | **High** | §12.2 |
| T11 | Cross-client memory contamination | **Medium–High** | §13.3 |
| T12 | Secrets in plaintext config; `roflo.toml` not gitignored | **Medium** | §10.6 |
| T13 | Silent stalls — a job blocked with no one notified | **Medium** | §16's explicit requirement; needs timeouts on every state |
| T14 | External platform API/ToS drift breaking discovery | **Medium** | §30. Fail closed on ambiguity |
| T15 | Non-constant-time token compare; unauthenticated `/health` | **Low** | §10.2, §10.4 |

---

## 19. BUSINESS RISKS

| # | Risk | Severity | Note |
| --- | --- | --- | --- |
| B1 | Marketplace ToS prohibits automated operation | **Critical** | §14.1. Can block the revenue engine outright. Resolve *before* building adapters |
| B2 | Legal capacity to contract — obligations, liability, tax, KYC | **Critical** | §14.2. A bot accepting work binds a real person |
| B3 | Payment rails prohibiting business use | **High** | §14.3 |
| B4 | Model-weight licences not cleared for commercial output | **High** | §15.3 |
| B5 | Repository has no licence at all | **Medium–High** | §15.1 |
| B6 | Quality failure → disputes, refunds, account termination | **High** | Account termination can end the business overnight; §32 concentration risk is really *survival* risk |
| B7 | Unprofitable automation — engineering cost exceeds lifetime margin | **High** | Very real for low-value freelance work. §3's "minimise out-of-pocket" must include *build* cost, which the specification omits |
| B8 | Owner-approval latency capping throughput | **Medium** | Accepted consequence of §25. Letting opportunities expire is correct |
| B9 | "Sandbox" in §33 is not a real sandbox for capabilities that make external calls | **Medium** | Piloting against real clients with real money is not testing |
| B10 | Reputation damage from a value-add read as upselling | **Medium** | §15's "do not secretly expand paid scope" is the right instinct |

**An honest note on B7, since no one else in this loop will say it:** the specification
describes an operating system sophisticated enough to run a company, aimed initially at
freelance tasks worth tens to hundreds of dollars. The P0 foundation alone is weeks of
careful engineering. It is entirely possible to build all of this correctly and never recover
the build cost from the work it qualifies for. That is not an argument against building it —
it is an argument for **proving the revenue loop with one real, manually-found job before
building any discovery automation**, which is what the roadmap below does.

---

## 20. RECOMMENDED IMPLEMENTATION ORDER

The specification asks for the smallest architecture capable of becoming the largest business.
The smallest thing that is *actually* load-bearing is not a module at all — it is **one
completed, paid, fully-accounted job**, performed with the owner in the loop at every step,
recorded in a real Ledger and a real Audit Log.

Sequence, with gates that must pass before the next stage begins:

1. **Resolve the blockers that can invalidate everything downstream** (§22 below). Legal and
   licensing questions first, because they are cheap to ask and expensive to discover late.
2. **P0 foundation**: Policy Store → Audit Log → Memory + Ledger → Core state machine →
   Financial Governor. No discovery, no automation, no external actions.
3. **Prove the loop manually**: owner finds one job by hand; it moves through the real state
   machine, the real Governor gate and the real Ledger. **Gate: estimated vs. actual profit
   is computed from verified payment.**
4. **P1 revenue engine**: Qualification, Capability Registry, one execution capability, QC,
   payment verification. **Gate: three jobs completed profitably with accurate estimates.**
5. **P1b discovery** — *only after* the ToS/legal determinations in step 1 are recorded and
   approved. This is deliberately last in P1; it is the most legally exposed component and
   the least useful before the loop works.
6. **P2 operating company**, then **P3 intelligent growth**, then **P4 advanced** — per §21's
   roadmap.

---

## 21. MODULE REPORTS (§42 format)

Fourteen proposed modules. **All are STATUS: MISSING** — nothing exists to extend, and saying
otherwise would be fabrication. "Existing component extended" is `none` throughout except
where noted.

---

### 1. OWNER POLICY STORE

- **STATUS:** MISSING
- **WHY IT EXISTS:** §45 requires that owner governance cannot be bypassed. That is only true if there is exactly one place where authority is defined.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Hold the owner's granted authority — permission classes, margin floors, spend ceilings, approved sources, approved capabilities, autonomy level, kill-switch state.
- **AUTHORITY IT HAS:** Final say on what any component may do. Its answer is definitive.
- **AUTHORITY IT DOES NOT HAVE:** Cannot initiate anything. Cannot modify itself. Cannot be written by any component — **owner writes only**.
- **INPUTS:** Owner decisions, via authenticated approval.
- **OUTPUTS:** Policy verdicts; kill-switch state.
- **DEPENDENCIES:** None. Deliberately the root.
- **OVERLAP CHECK:** Absorbs §27, §28, §34, and the authorisation halves of §7/§8/§33. No other component may store permissions.
- **FAILURE MODE:** Unreachable or corrupt store.
- **FAIL-CLOSED BEHAVIOUR:** **HALT.** Unreadable policy = no authority = no action. Accepted cost: the business stops (§12.2).
- **SECURITY BOUNDARY:** The system's trust root. Strictest write control anywhere; every read and write audited.
- **FINANCIAL AUTHORITY:** Defines limits; spends nothing.
- **OWNER APPROVAL:** Every write.
- **MEMORY ACCESS:** None (it is not memory).
- **AUDIT:** Every read and write, append-only.
- **TESTING:** Bypass tests (can any component write policy?), fail-closed tests, kill-switch propagation to in-flight work.
- **DIFFICULTY:** Low code / High design.

### 2. SOLVENT CORE

- **STATUS:** MISSING
- **WHY IT EXISTS:** §1 requires one business orchestration authority, not competing managers.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Own the job lifecycle state machine and the transitions between states (§16's 15 states).
- **AUTHORITY IT HAS:** Advance a job's state; dispatch to capabilities; route gaps and escalations.
- **AUTHORITY IT DOES NOT HAVE:** Cannot approve money (→ Governor), cannot grant permission (→ Policy), cannot accept work on its own (→ Qualification), cannot edit the Audit Log.
- **INPUTS:** Qualification verdicts, execution results, client/owner messages, policy state.
- **OUTPUTS:** State transitions, dispatches, escalations.
- **DEPENDENCIES:** Policy, Audit, Memory.
- **OVERLAP CHECK:** Absorbs §16 supervision and §14 gap *routing*. The only component that may commit a job to a state — closing the §15 value-add side door.
- **FAILURE MODE:** Crash mid-job; a job stuck in a state with no owner.
- **FAIL-CLOSED BEHAVIOUR:** Durable state, resumable from the last committed transition. **Every state has a timeout; a timeout escalates.** No silent stalls (§16).
- **SECURITY BOUNDARY:** Trusted core; consumes only typed structures, never free-form model text as instruction (§10.7).
- **FINANCIAL AUTHORITY:** **None.** Asks the Governor.
- **OWNER APPROVAL:** Per the Policy class of the action it is dispatching.
- **MEMORY ACCESS:** Read/write job state.
- **AUDIT:** Every transition, with cause.
- **TESTING:** Crash-recovery, no-orphan-state, timeout-escalation, kill-switch-mid-job.
- **DIFFICULTY:** High.

### 3. OPPORTUNITY DISCOVERY

- **STATUS:** MISSING
- **WHY IT EXISTS:** §2. Work must be found before it can be evaluated.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Produce *candidate* opportunities from approved sources, normalised to one schema.
- **AUTHORITY IT HAS:** Query approved sources; mark a source unhealthy or non-compliant.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot accept, bid, commit, or create an account.** Discovery is not acceptance (§45).
- **INPUTS:** Approved source list (Policy), adapter responses.
- **OUTPUTS:** Candidate records + per-source compliance/health state.
- **DEPENDENCIES:** Policy, Core.
- **OVERLAP CHECK:** Absorbs §30 (platform-rule change) as per-adapter compliance state.
- **FAILURE MODE:** Source unavailable; ToS changed; anti-bot block encountered.
- **FAIL-CLOSED BEHAVIOUR:** On any compliance ambiguity, **stop using that source and escalate**. Never circumvent a protection; never retry around a block.
- **SECURITY BOUNDARY:** **Everything it returns is untrusted attacker-controlled text** and must be treated as data, never instruction (§10.7).
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** Required per source before first use; re-required on material ToS change.
- **MEMORY ACCESS:** Write candidates and source history.
- **AUDIT:** Every source query and every compliance determination.
- **TESTING:** Injection-resistance of the normaliser; fail-closed on ToS ambiguity; rate-limit compliance.
- **DIFFICULTY:** High (and legally gated — §14.1).

### 4. QUALIFICATION & RANKING

- **STATUS:** MISSING
- **WHY IT EXISTS:** §4. One authoritative accept/reject/escalate decision.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Combine capability, economics, capacity, client history and portfolio position into **one** verdict: QUALIFIED / REQUIRES_REVIEW / REJECT / CAPABILITY_MISSING / HIGH_RISK.
- **AUTHORITY IT HAS:** Reject work outright. Rank candidates. Escalate for approval.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot compute its own economics** (→ Governor) and cannot override a Governor rejection. Cannot self-approve anything Policy marks OWNER_APPROVAL.
- **INPUTS:** Candidates, Governor verdict, Capability Registry verdict, client history, current capacity.
- **OUTPUTS:** Ranked verdicts with reasons.
- **DEPENDENCIES:** Core, Governor, Capability Registry, Memory.
- **OVERLAP CHECK:** Absorbs §10 (portfolio), §11 (client quality), §12 (strategic value) **as scoring inputs only**. This is the design's highest duplication risk: if it ever computes margin itself, there are two financial authorities.
- **FAILURE MODE:** Missing information; conflicting inputs.
- **FAIL-CLOSED BEHAVIOUR:** Unknown cost/scope/requirement/payment terms → **REQUEST_INFORMATION or REJECT. Never assume.**
- **SECURITY BOUNDARY:** Consumes untrusted candidate text; all gates evaluated in code over typed fields.
- **FINANCIAL AUTHORITY:** **None — it asks.**
- **OWNER APPROVAL:** Per Policy thresholds.
- **MEMORY ACCESS:** Read client/job history; write verdicts.
- **AUDIT:** Every verdict with full input snapshot (§36 explainability).
- **TESTING:** **Governor-bypass tests are mandatory.** Strategic-value-cannot-override-floor tests. Fail-closed-on-unknown tests.
- **DIFFICULTY:** High.

### 5. CAPABILITY REGISTRY

- **STATUS:** MISSING
- **WHY IT EXISTS:** §5. Understanding a job is not proof of ability to do it.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Record what Solvent can actually do, at what proven quality, and answer CAN_EXECUTE / CAN_EXECUTE_WITH_ASSISTANCE / REQUIRES_NEW_CAPABILITY / REQUIRES_HUMAN / CANNOT_EXECUTE.
- **AUTHORITY IT HAS:** Declare a capability unavailable (a veto on execution).
- **AUTHORITY IT DOES NOT HAVE:** **Cannot add capabilities to itself** (→ Expansion Pipeline + owner approval). Cannot judge profitability.
- **INPUTS:** Approved capability list (Policy), proven performance (Ledger/Audit — not self-report), Expansion Pipeline results.
- **OUTPUTS:** Capability verdicts; capability-gap records; licence status of each model/tool used.
- **DEPENDENCIES:** Policy.
- **OVERLAP CHECK:** Absorbs §6 catalogue and §7 gap *identification* (not acquisition). Also the correct home for §15.3's per-checkpoint commercial-licence record.
- **FAILURE MODE:** Overstated capability → accepted work that cannot be delivered.
- **FAIL-CLOSED BEHAVIOUR:** Unproven capability = CANNOT_EXECUTE. Absence of evidence is not capability.
- **SECURITY BOUNDARY:** Write-restricted; only the Expansion Pipeline may promote, only with owner approval.
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** Every capability addition.
- **MEMORY ACCESS:** Read performance history; write gaps.
- **AUDIT:** Every capability change.
- **TESTING:** Self-promotion-bypass tests; degradation detection.
- **DIFFICULTY:** Low–Medium.

### 6. FINANCIAL GOVERNOR

- **STATUS:** MISSING
- **WHY IT EXISTS:** §9, §45. Exactly one component may authorise money.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Decide whether a proposed commitment or spend is permitted: PROFITABLE / MARGIN_TOO_LOW / HIGH_RISK / REQUIRES_APPROVAL / REJECT.
- **AUTHORITY IT HAS:** Block any spend. Throttle. Force escalation. **Its rejection is final and unappealable by any component.**
- **AUTHORITY IT DOES NOT HAVE:** Cannot set its own floors or ceilings (→ Policy). Cannot record actuals (→ Ledger). Cannot accept work (→ Qualification).
- **INPUTS:** Policy floors/ceilings, job estimates, Ledger actuals, live spend telemetry.
- **OUTPUTS:** Spend verdicts; anomaly signals; budget state.
- **DEPENDENCIES:** Policy, Ledger.
- **OVERLAP CHECK:** Absorbs §29 (anomaly) outright. Consumes §10/§32 as inputs. **No other component may throttle or block spending.**
- **FAILURE MODE:** Estimates wrong; telemetry stale; runaway loop.
- **FAIL-CLOSED BEHAVIOUR:** Unknown cost → REQUEST_INFORMATION or REJECT. Stale telemetry → BLOCK, not proceed. **Checked before every spend** — because `roflo` and the backends enforce nothing (§10.5).
- **SECURITY BOUNDARY:** Money-critical. Structured inputs only; never a model-authored verdict (§10.7).
- **FINANCIAL AUTHORITY:** **Total, and exclusive.**
- **OWNER APPROVAL:** Above Policy thresholds; always for policy changes.
- **MEMORY ACCESS:** Read cost history; write verdicts.
- **AUDIT:** Every verdict, every anomaly, every throttle.
- **TESTING:** **Bypass tests are the single most important test suite in the system**: no path to an external paid call may exist that does not pass through this gate. Plus runaway-loop, stale-telemetry, and boundary tests on the floors.
- **DIFFICULTY:** High.

### 7. LEDGER

- **STATUS:** MISSING
- **WHY IT EXISTS:** §21, §22, §45. Expected revenue is not collected revenue.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Record verified money — quotes, invoices, **externally verified receipts**, receipted costs, actual profit per job.
- **AUTHORITY IT HAS:** Declare what was actually earned and spent. Its figures are the system's financial truth.
- **AUTHORITY IT DOES NOT HAVE:** Cannot authorise anything (→ Governor). Cannot record unverified revenue.
- **INPUTS:** Payment-rail verification events, provider billing/usage, receipted costs.
- **OUTPUTS:** Payment states (QUOTED/INVOICED/AUTHORIZED/PAID/PARTIALLY_PAID/OVERDUE/REFUNDED/DISPUTED); actual profit; estimate-vs-actual deltas.
- **DEPENDENCIES:** Audit Log.
- **OVERLAP CHECK:** Absorbs §21 + §22. **Distinct from the Governor by design** (§5): records, never decides.
- **FAILURE MODE:** Double-counting; unreconciled payout; assumed payment.
- **FAIL-CLOSED BEHAVIOUR:** **No external verification = not PAID.** Unverifiable inflow → DISPUTED/UNKNOWN, never revenue. Idempotent writes.
- **SECURITY BOUNDARY:** Financial record; append-oriented with corrections as new entries, never in-place edits.
- **FINANCIAL AUTHORITY:** Records only.
- **OWNER APPROVAL:** For write-offs, refunds, corrections.
- **MEMORY ACCESS:** Writes financial history; **Learning may read, never write** (§12.3).
- **AUDIT:** Every entry.
- **TESTING:** Idempotency, reconciliation, double-count, "INVOICED never counts as reserve."
- **DIFFICULTY:** Medium–High. **Do not source cost from `roflo`'s `len/4` estimator** (§11.2).

### 8. BUSINESS MEMORY

- **STATUS:** MISSING
- **WHY IT EXISTS:** §19. One structured operational memory.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Store operational facts and derived statistics with provenance, timestamp, sensitivity class, retention policy and deletion rules.
- **AUTHORITY IT HAS:** None. It is a store.
- **AUTHORITY IT DOES NOT HAVE:** Decides nothing. **Is not the audit trail** (§5).
- **INPUTS:** Job outcomes, client behaviour, tool/model performance, QC results, capability gaps.
- **OUTPUTS:** Queries and derived scores (e.g. client quality).
- **DEPENDENCIES:** Policy (classification/retention rules).
- **OVERLAP CHECK:** Absorbs §19's 16 categories and §11's derived scoring. **Must not absorb §36.**
- **FAILURE MODE:** Cross-client contamination; unbounded retention; stale scores.
- **FAIL-CLOSED BEHAVIOUR:** Unclassified data is treated as maximum sensitivity. No sensitivity class = no external transmission.
- **SECURITY BOUNDARY:** Per-client partitioning; sensitivity-gated reads; retention enforcement.
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** For retention/classification policy changes.
- **MEMORY ACCESS:** It *is* memory. Learning may write **derived aggregates only**, never raw client content and never financial truth.
- **AUDIT:** Schema changes, deletions, cross-partition reads.
- **TESTING:** Cross-tenant leakage, retention/deletion, classification enforcement.
- **DIFFICULTY:** Medium.

### 9. AUDIT LOG

- **STATUS:** MISSING
- **WHY IT EXISTS:** §36, and — critically — §12.3: autonomy evidence must be untamperable.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Immutably record what happened, who initiated it, when, why, inputs, decision, authority used, cost, result, and owner approval if applicable.
- **AUTHORITY IT HAS:** None. It witnesses.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot be edited or deleted by any component, including Learning, including Solvent Core.**
- **INPUTS:** Events from every component.
- **OUTPUTS:** Explainability queries; autonomy evidence.
- **DEPENDENCIES:** None.
- **OVERLAP CHECK:** Absorbs §36. **Deliberately separate from Business Memory** — see §5 and §12.3.
- **FAILURE MODE:** Log unavailable; log full.
- **FAIL-CLOSED BEHAVIOUR:** **If the action cannot be logged, the action does not proceed.** An unlogged consequential action is a governance hole.
- **SECURITY BOUNDARY:** Append-only at the storage layer, not merely by convention. No delete path in code.
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** N/A (owner cannot edit it either).
- **MEMORY ACCESS:** Write-only from components; read for explanation.
- **AUDIT:** It is the audit.
- **TESTING:** Tamper-resistance, no-delete-path, log-unavailable-halts-action, completeness of consequential actions.
- **DIFFICULTY:** Low to build; the discipline is what must be tested.

### 10. EXECUTION LAYER

- **STATUS:** MISSING
- **WHY IT EXISTS:** §6, §13, §14, §17. Contracted work must actually be performed.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Perform approved capabilities on a dispatched job and report results, gaps and value-add observations.
- **AUTHORITY IT HAS:** Execute within a dispatched, budgeted scope.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot spend beyond its grant, expand scope, contact a client directly, or accept a value-add.** Cannot override QC. Cannot override the Governor (§9).
- **INPUTS:** Dispatch from Core (with budget and capability grant), client materials.
- **OUTPUTS:** Work product; execution gaps (§14); value-add observations (§15); QC results (§17).
- **DEPENDENCIES:** Core, Capability Registry, Router.
- **OVERLAP CHECK:** Absorbs §6, §13 (intake normalisation), §17 (QC as a capability at policy-set intensity), §14 gap *detection*. **Not 13 agents** (§5).
- **FAILURE MODE:** Blocked, over-budget, quality failure, malicious input file.
- **FAIL-CLOSED BEHAVIOUR:** **Missing information is never fabricated** (§45) — raise a gap and wait. Over-budget → stop, escalate.
- **SECURITY BOUNDARY:** **The primary untrusted-content boundary.** Document parsing isolated (§10.8); client data never leaves an approved privacy class; no capability may install software or acquire credentials.
- **FINANCIAL AUTHORITY:** Spends only within a Governor-issued grant.
- **OWNER APPROVAL:** For anything outside the dispatched scope.
- **MEMORY ACCESS:** Read job context; write results.
- **AUDIT:** Every capability invocation with cost.
- **TESTING:** Budget-escape, scope-escape, malicious-document, injection-via-client-file, fabrication tests ("does it invent a missing figure?").
- **DIFFICULTY:** Medium → Very High with catalogue size. **Start with one capability.**

### 11. MODEL / TOOL ROUTER

- **STATUS:** MISSING (the provider abstraction beneath it — `roflo` — is **PARTIAL/EXTEND**)
- **WHY IT EXISTS:** §18, §31.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Select a provider for one inference or tool call, subject to privacy class, budget and capability.
- **AUTHORITY IT HAS:** Choose among *approved* providers; fail over to an *approved* alternative.
- **AUTHORITY IT DOES NOT HAVE:** Cannot exceed the Governor's budget, cannot downgrade a privacy class to save money, cannot use an unapproved provider.
- **INPUTS:** Task class, privacy class, remaining margin (Governor), Policy's approved provider list.
- **OUTPUTS:** Provider selection; failure/fallback events.
- **DEPENDENCIES:** Policy, Governor, `roflo` (as one adapter).
- **EXISTING COMPONENT EXTENDED:** **`roflo`** — the `Backend` ABC and lazy registry are a sound foundation. Must add per-request model selection (**§10.1 defect**), real token accounting (§11.2), and a declared privacy class per backend.
- **OVERLAP CHECK:** Absorbs §18 + §31 fallback. **Must never become a second financial authority** — it *asks* the Governor for remaining budget.
- **FAILURE MODE:** Provider down; all approved providers down.
- **FAIL-CLOSED BEHAVIOUR:** **Privacy overrides cost, always** (§18). No approved provider available in the required privacy class → **BLOCK and escalate**, never silently downgrade.
- **SECURITY BOUNDARY:** The point where client data crosses to an external service. Enforces the privacy classification §10.3 shows `roflo` cannot.
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** Every new provider.
- **MEMORY ACCESS:** Write provider performance/cost history.
- **AUDIT:** Every selection and fallback, with reason and privacy class.
- **TESTING:** **Privacy-downgrade-under-cost-pressure tests**; verify actual routing rather than trusting the echoed `model` field (§10.1).
- **DIFFICULTY:** Medium.

### 12. OWNER CHANNEL (dashboard + escalation + voice)

- **STATUS:** MISSING
- **WHY IT EXISTS:** §24, §25, §35. One business voice; one human; one authentication story.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Carry information to the owner and authenticated decisions back.
- **AUTHORITY IT HAS:** None of its own. It *transports* owner authority.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot approve anything itself. Cannot bypass Policy. An approval arriving through it is a scoped, single-use grant, never a session elevation** (§12.5).
- **INPUTS:** Escalations, status, dashboard queries; owner responses.
- **OUTPUTS:** Authenticated, scoped approval grants; notifications.
- **DEPENDENCIES:** Core, Policy.
- **OVERLAP CHECK:** Absorbs §24 + §25 + §35 as **one** module (§5). Sub-agents stay silent.
- **FAILURE MODE:** Owner unreachable; spoofed instruction; call/SMS failure.
- **FAIL-CLOSED BEHAVIOUR:** **No authenticated approval = no action. Let the opportunity expire** (§25). Escalation: CALL → TEXT → retry for ≤20-minute expiries.
- **SECURITY BOUNDARY:** **A spoofable, replayable channel under time pressure — the ideal social-engineering target** (§12.5). Consequential approvals need out-of-band confirmation.
- **FINANCIAL AUTHORITY:** None (it carries the owner's).
- **OWNER APPROVAL:** It is the approval mechanism.
- **MEMORY ACCESS:** Read for dashboard; write approval records.
- **AUDIT:** Every escalation, response, and granted authority.
- **TESTING:** Spoofing, replay, scope-escalation ("approve job X" must not authorise job Y), expiry-with-no-answer.
- **DIFFICULTY:** Medium; High with telephony.

### 13. CLIENT CHANNEL

- **STATUS:** MISSING
- **WHY IT EXISTS:** §26.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Conduct and **formally record** all client communication, especially anything touching scope, price, deadline, obligation, legal terms or deliverables.
- **AUTHORITY IT HAS:** Send approved messages; request information; deliver approved work.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot agree to a scope, price or deadline change** — those re-enter Qualification and the Governor (§5). Cannot deliver work that has not passed QC.
- **INPUTS:** Core dispatch, client messages, approved value-add proposals.
- **OUTPUTS:** Recorded communications; scope-change requests routed to Qualification.
- **DEPENDENCIES:** Core, Audit.
- **OVERLAP CHECK:** Absorbs §26 and §15's *delivery* of proposals. **Does not decide** on them.
- **FAILURE MODE:** Ambiguous client instruction; silent scope creep; injection via client message.
- **FAIL-CLOSED BEHAVIOUR:** Ambiguity → REQUEST_INFORMATION. **Never agree to anything not already approved.**
- **SECURITY BOUNDARY:** Untrusted inbound content (§10.7); outbound must not leak other clients' data.
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** Per Policy for commitments.
- **MEMORY ACCESS:** Write communications, partitioned per client.
- **AUDIT:** Every scope-affecting message.
- **TESTING:** Scope-creep, injection-via-client-message, cross-client leakage.
- **DIFFICULTY:** Medium.

### 14. ANALYST / LEARNING

- **STATUS:** MISSING
- **WHY IT EXISTS:** §20, §23, §32, §8, §33.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Observe verified outcomes and produce **recommendations**.
- **AUTHORITY IT HAS:** **None. It proposes.**
- **AUTHORITY IT DOES NOT HAVE:** **Cannot write Policy, margins, permissions, governance, or the Ledger. Cannot approve its own proposals. Cannot expand capability.** (§20, §45.)
- **INPUTS:** Ledger (verified), Memory, Audit Log — **read-only on all three**.
- **OUTPUTS:** Typed recommendations: pricing, estimation calibration, routing, capability gaps, reinvestment (§23), concentration risk (§32), autonomy evidence (§34).
- **DEPENDENCIES:** Ledger, Memory, Audit.
- **OVERLAP CHECK:** Absorbs §20, §23, §32, and the analysis half of §7/§8/§33/§34. **Creates no decision authority.**
- **FAILURE MODE:** Overfitting to few samples; optimistic drift (§12.4).
- **FAIL-CLOSED BEHAVIOUR:** Insufficient evidence → no recommendation. **Speculation is not evidence** (§33).
- **SECURITY BOUNDARY:** Read-only; must not re-identify or leak client data through aggregates.
- **FINANCIAL AUTHORITY:** None.
- **OWNER APPROVAL:** Every recommendation it emits requires approval before it changes anything.
- **MEMORY ACCESS:** Read all; write **derived aggregates only**, never the Ledger or Audit Log.
- **AUDIT:** Every recommendation and its evidence.
- **TESTING:** **Policy-write-bypass tests; the §12.4 estimate-drift alarm; the §12.3 evidence-source test (autonomy evidence must come from Ledger/Audit, never Memory).**
- **DIFFICULTY:** Medium–High.

### 15. EXPANSION PIPELINE

- **STATUS:** MISSING (**P3** — initially a manual owner process, no module)
- **WHY IT EXISTS:** §8, §33. No capability goes from idea to production.
- **SINGLE AUTHORITATIVE RESPONSIBILITY:** Take an owner-approved capability proposal through sandbox → test → small pilot → measure → deploy or reject.
- **AUTHORITY IT HAS:** Run isolated tests within an approved budget.
- **AUTHORITY IT DOES NOT HAVE:** **Cannot approve its own expansion, purchase services, install uncontrolled software, grant credentials, or self-deploy** (§7).
- **INPUTS:** Approved proposals; pilot results.
- **OUTPUTS:** Validated (or rejected) capabilities for the Registry.
- **DEPENDENCIES:** Policy, Analyst, Capability Registry.
- **OVERLAP CHECK:** Absorbs §8 + §33 + §7's acquisition *execution*.
- **FAILURE MODE:** "Sandbox" that isn't isolated; pilot that is really production with real clients and real money (**B9**).
- **FAIL-CLOSED BEHAVIOUR:** Unvalidated → not deployed. Isolation failure → abort.
- **SECURITY BOUNDARY:** Strongest isolation in the system — new, unproven code and external calls.
- **FINANCIAL AUTHORITY:** A capped, owner-approved experiment budget.
- **OWNER APPROVAL:** At proposal, at pilot, and at deploy.
- **MEMORY ACCESS:** Write experiment results.
- **AUDIT:** Every stage transition.
- **TESTING:** Isolation-escape; self-promotion-bypass; budget-cap.
- **DIFFICULTY:** High.

---

## 22. PRIORITIZED ROADMAP (§43)

### P0 — FOUNDATION / SAFETY

*Nothing may be built before this, and nothing here earns money.*

1. **Resolve the blocking external questions first** (all cheap to ask, expensive to discover late):
   - **B1** Which specific sources are intended, and does each one's ToS permit automated operation?
   - **B2** Which legal entity contracts, and who bears liability and tax reporting?
   - **B4** Are the model checkpoints licensed for commercial output?
   - **B5** What licence applies to this repository?
2. **Owner Policy Store** — permission classes, floors, ceilings, approved lists, kill-switch flag.
3. **Audit Log** — append-only, no delete path, "cannot log = cannot act."
4. **Business Memory** — schema with provenance/sensitivity/retention from migration #1.
5. **Ledger** — verified money only, idempotent, reconciling.
6. **Solvent Core** — durable job state machine, timeouts on every state, resumable.
7. **Financial Governor** — checked before every spend, with anomaly detection.
8. **Anti-duplication architectural tests** (the specification asks for these; they are the mechanism that keeps the law true over time):
   - no component other than Policy may write permissions;
   - no path to a paid external call bypasses the Governor;
   - Learning has no write path to Policy, Ledger, or Audit;
   - the Audit Log has no delete path;
   - the kill switch is checked before every spend and every external action.
9. **Open design question to settle before P1:** the §12.4 estimate-drift loophole.

**Gate:** the owner can pause everything; nothing spends without the Governor; nothing acts without being logged.

### P1 — REVENUE ENGINE — *"Can Solvent safely make real money?"*

10. **Prove the loop manually first** — one owner-found job, through the real state machine, the real Governor and the real Ledger. **Gate: estimate vs. actual computed from verified payment.**
11. **Capability Registry** + **one** execution capability (the narrowest, highest-confidence one).
12. **Qualification & Ranking** — including the ability to reject.
13. **QC** at policy-set intensity; delivery.
14. **Payment verification** into the Ledger.
15. **Discovery — last in P1, and only if step 1's ToS determinations permit it.**

**Gate:** three jobs completed profitably, with estimates that matched actuals within a stated band.

### P2 — OPERATING COMPANY — *"Can it operate repeatedly and reliably?"*

16. Additional capabilities (justified by observed demand, not speculation).
17. Supervision hardening; execution-gap detection; document intake (isolated).
18. Client Channel; value-add proposals; client-quality scoring.
19. Model Router (with the §10.1 defect resolved); provider resilience.
20. Owner dashboard.

### P3 — INTELLIGENT GROWTH — *"Can it improve without losing control?"*

21. Analyst / Learning (recommendations only).
22. Portfolio optimisation; concentration risk; reinvestment recommendations.
23. Capability-gap business cases; Expansion Pipeline.
24. Earned-autonomy evidence — **drawn from Ledger and Audit only** (§12.3).

### P4 — ADVANCED / OPTIONAL — *"Which advanced capabilities have proven business value?"*

25. Voice; phone/SMS escalation (§25's 20-minute rule).
26. Controlled automatic acceptance within proven, narrow bands.
27. Multi-agent QC; advanced media/design; broader business models.

---

## 23. ADVERSARIAL REVIEW (§44)

Attacking the architecture proposed in §9 and §21, not defending it.

**A1 — Solvent Core is a bottleneck and a single point of failure.** Everything routes through
it. If it is down, nothing moves; and it is where "just this once" exceptions will accumulate.
*Partial mitigation:* make it a durable state machine over storage rather than a long-running
brain, so any step is resumable and no state is in-process. **The concentration of authority is
real and is the price of the "one orchestrator" law. It should be accepted knowingly, not
waved away.**

**A2 — Qualification is one refactor away from being a second financial authority.** It
consumes margin, portfolio position and strategic value. The instant someone adds "but if
strategic value is high, allow 5% below the floor," there are two margin rules. *Mitigation:*
Qualification must hold **no** margin arithmetic at all, and an architectural test must assert
it. **This is the most likely duplication to actually occur.**

**A3 — Learning can move the effective floor without touching policy.** §12.4. **Unsolved.**
Flagged as a P0 design question rather than papered over.

**A4 — The kill switch has an in-flight gap.** A job already executing keeps spending unless
the flag is checked before every spend and every external action. And checking on every action
makes the Policy Store a hot dependency whose outage halts the business (§12.2). Both horns
are real; halting is the right one.

**A5 — Prompt injection is the most likely real-world compromise.** Discovery text, client
documents and client messages are all attacker-controlled and all flow toward decisions. The
only defence is that gates are code over typed fields (§10.7). **If any gate is ever satisfied
by model-generated prose, the entire governance structure is decorative** — and §0 shows the
inference layer will not catch it.

**A6 — Fourteen modules may be too many for P0.** Six are P0; the others could sag into
"planned but unbuilt," and half-built governance is worse than none because it implies
protection that is absent. *Mitigation:* the P0 gate is behavioural, not structural — pause,
gate, log — and the manual-loop step in P1 deliberately delays module proliferation.

**A7 — The Memory/Audit split adds cost and will be attacked as duplication.** Someone will
propose merging them for simplicity. §12.3 is the answer: merge them and the subsystem that
writes memory can rewrite the evidence that governs its own autonomy. **Hold this line.**

**A8 — Voice + 20-minute expiry is a social-engineering gift.** Urgency, a phone call, a
financial approval. If §25 is built as specified without out-of-band confirmation and scoped
single-use grants, it is the cheapest way into the system. **Consider deferring voice
indefinitely; the business value is small and the attack surface is large.**

**A9 — Payment verification may be structurally impossible on some rails.** "Verified
payment" assumes a webhook, API or reconciliation feed. Some rails offer none, leaving
`PAID` to be set by a human eyeballing a screen — which is an assumption wearing a verified
label. *Mitigation:* record the verification *method* per payment and let the Ledger's
confidence vary; never let a manually-asserted payment feed autonomy evidence.

**A10 — The economics may never work (B7).** The build cost of this architecture plausibly
exceeds the lifetime margin of the work it initially qualifies for. The roadmap's answer is to
prove the loop with one manual job before building discovery, so the failure is cheap and
early.

**A11 — `roflo` will be mistaken for a control point.** It is called "no guardrails" and it
means it. Anyone assuming the inference layer filters, limits, or enforces *anything* will be
wrong (§0, §10.1, §10.5). This must be documented at the Router boundary, not left to be
rediscovered.

**A12 — Discovery may be legally unbuildable as specified (B1).** If the intended marketplaces
prohibit automated operation, P1's automation step cannot be built, and Solvent becomes an
owner-fed work system. That is still viable — **but it must be discovered in step 1, not after
the adapters are written.**

**What should be removed:**

- **§24 voice and §25 phone escalation** — defer indefinitely (A8). High risk, low value, and
  the fail-closed answer ("let it expire") already handles the deadline case.
- **§12 strategic value as a scoring input** — consider dropping entirely for P1. It is the
  most abusable input in the ranking and the hardest to evidence.
- **§6's 13 capability categories** — build one, add the second only on observed repeated
  demand (§33).
- **§35 dashboard in P2** — a query over the Ledger and Audit Log, not a subsystem. Do not let
  it become one.

---

## 24. ACCEPTANCE STANDARD (§45)

| # | Criterion | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | One authority per responsibility | **NOT MET** | No authorities exist |
| 2 | No material duplicate logic | **NOT MET** (vacuously true today) | No logic exists |
| 3 | No overlapping authority | **NOT MET** (vacuously) | — |
| 4 | Owner governance cannot be bypassed | **NOT MET** | No governance exists |
| 5 | Financial Governor cannot be bypassed | **NOT MET** | No Governor exists |
| 6 | Unknown critical info fails closed | **NOT MET** | No decision points exist |
| 7 | Expansion requires authorization | **NOT MET** | — |
| 8 | New capabilities require validation | **NOT MET** | — |
| 9 | Business Memory has one authority | **NOT MET** | Zero persistence (§13) |
| 10 | Learning cannot rewrite governance | **NOT MET** | — |
| 11 | External content cannot rewrite governance | **NOT MET** | And `roflo` guarantees no enforcement at the model layer (§0) |
| 12 | Spending is bounded | **NOT MET** | No limits anywhere (§10.5) |
| 13 | Owner can pause the system | **NOT MET** | No kill switch |
| 14 | Payment verified, not assumed | **NOT MET** | No payment concept |
| 15 | Real profit distinguished from revenue | **NOT MET** | No financial concept |
| 16 | Discovery does not imply acceptance | **NOT MET** | Neither exists |
| 17 | Solvent can reject bad work | **NOT MET** | — |
| 18 | Architecture can grow without a rebuild | **N/A** | Nothing built; the §9 boundaries are designed for this |

**0 of 17 met.**

---

## 25. FINAL DISPOSITION

# FAIL

**Evidence:** Solvent does not exist. `git grep -il "solvent"` across every ref in the
repository's history returns zero matches. The repository contains one project, `roflo`, a
2,308-line LLM inference CLI and proxy with no persistence, no business domain, no financial
concept, no permission model, no memory, and no orchestration. Zero of the 46 specification
sections are implemented; three are partially served, all three within the inference layer.
Zero of the 17 acceptance criteria in §45 can be evidenced.

**Why FAIL and not CONDITIONAL PASS:** a conditional pass would assert that a viable
architecture exists with resolvable blockers. No architecture exists. Reporting a conditional
pass would misrepresent an empty repository as a partially-built system, and the specification
explicitly forbids fabricating information to keep moving.

**Important qualification — this is "not yet built," not "built wrong."** There is no bad
architecture here to unwind. That is genuinely favourable: the one-authority-per-responsibility
law is far easier to satisfy from zero than to retrofit, and `roflo` is well-built, correctly
scoped, and usable unchanged as one provider adapter beneath a future router.

**Three findings that must be carried forward regardless of what is built:**

1. **`roflo` is architecturally guaranteed to enforce nothing** (§0). Every control must be
   executed in code above the model call. Any governance expressed as a prompt instruction
   does not exist.
2. **The specification, built literally as 46 modules, would violate its own anti-duplication
   law on day one.** §5 identifies the 13 sections that must be inputs, analyses or flags
   rather than authorities; §9 reduces the whole specification to 14 non-overlapping modules,
   6 of which are P0.
3. **Two questions can invalidate the entire revenue engine and are cheap to answer now:**
   do the intended platforms' Terms of Service permit automated operation (B1), and which
   legal entity bears the contractual obligation (B2)? Both belong before any code.

**The proposed module boundaries in §9 and §21 are a design proposal awaiting approval. They
have not been implemented, and nothing in this audit should be read as a claim that they have
been validated in practice.**

---

## STOP

Audit complete. No code was modified. No implementation has begun and none will begin without
explicit owner approval.

**Recommended next decision from the owner, in order:**

1. Answer the four P0 blocking questions (B1 ToS, B2 legal entity, B4 model licences, B5 repository licence).
2. Approve, amend, or reject the 14-module boundary map in §9.
3. Rule on the §23 removals — particularly deferring voice/phone escalation (A8).
4. Approve or reject the P0 build list in §22.
