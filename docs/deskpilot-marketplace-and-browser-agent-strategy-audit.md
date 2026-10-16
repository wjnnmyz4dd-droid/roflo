# Marketplace strategy and browser-agent architecture — an audit

**Research, not implementation.** No production file was modified, no
dependency installed into DeskPilot, no marketplace automation
activated, no bid submitted, no contract accepted, no funds moved, MT5
untouched. Repositories were cloned read-only into a scratch directory;
every browser benchmark ran against a loopback server serving synthetic
fixtures. No live marketplace page, API or account was exercised.

Date: 2026-10-16. Branch `claude/solvent-architecture-audit-8ot9et`,
baseline verified clean at `4445869` before starting.
Measurements: `docs/evidence/marketplace-and-browser-strategy-measurements.json`.

**Reading the evidence.** *Measured* means I ran it or read it at the
commit named. *Reported* means a third party says so and I could not
verify it. *Judgment* means I weighed facts against DeskPilot's
constraints. Weights are judgment; inputs are measured. Where evidence
could not be obtained, the question is marked **UNRESOLVED** rather
than filled with a guess.

**The five distinctions, applied throughout.** Technical feasibility,
contractual permission, API capability, owner approval, and production
readiness are tracked separately and never collapsed. Technical
feasibility is not permission. A human approval does not authorise an
automation the platform's contract forbids: the owner can authorise
DeskPilot to act on their behalf, but cannot grant DeskPilot a
permission the owner does not themselves hold.

---

## A. Executive findings

**The best legitimate job-acquisition strategy is direct hire, supported
by permitted read-only discovery on one platform, with an owner-operated
service site as the medium-term destination rather than the starting
point.**

Four things decide it, and three were already established in the
codebase rather than discovered here:

1. **Automated submission is forbidden everywhere evidence exists.**
   Not undetermined — forbidden, by name, on the one platform whose
   agreement was read. No platform in the catalogue permits DeskPilot
   to submit a proposal.
2. **Exactly one platform permits automated reading on recorded
   evidence**: Freelancehunt, via its official API 2.0, determination
   recorded 2026-10-15. Two more permit *crawling of listing pages* by
   robots.txt — Contra and PeoplePerHour — and neither has had its
   terms of service read.
3. **The binding constraint on revenue is not discovery.** It is that
   the payment rail cannot move money (`money_can_move: False`) and
   that no channel except direct hire can close a sale without a human.
   A browser capability does not touch either.
4. **A browser capability is still worth building** — but for
   *observation*, not acquisition. Its value is feeding Finder and
   pricing with evidence, and the measurements below show it can do
   that at a fixed 42-token cost with full fidelity.

The sharpest new technical finding: **`browser_snapshot(filename=…)`
writes the complete page to disk and returns 42 tokens.** Measured:
169-character response, 46,198-character file, 120 of 120 listings and
120 of 120 URLs recovered. This removes the token-scaling objection the
previous audit raised, because DeskPilot parses the file with ordinary
stdlib code and no model sees the page at all.

The sharpest new security finding: **`--allowed-origins` does not
constrain `browser_run_code_unsafe`.** Measured — the tool made an
outbound request to a host the browser never visited and no allowlist
mentioned. The network control and the code-execution tool operate at
different layers.

---

## B. Marketplace permission matrix

DeskPilot already holds a researched catalogue in
`solvent/sourcecatalog.py` with a fifteen-verb permission model in
`solvent/sourceaccess.py`. This audit re-verified what could be
re-verified today and extends it; it does not restate it as new.

Verbs are separated because conflating them is how accounts get banned:
`DISCOVER`, `DISCOVER_AUTHENTICATED`, `READ`, `IMPORT`, `PREPARE_BID`,
`SUBMIT_BID`, `SUBMIT_APPLICATION`, `SEND_MESSAGE`, `ACCEPT_CONTRACT`,
`DELIVER_THROUGH_PLATFORM`, `SPEND_MONEY` and four more.

### B.1 The matrix

| platform | technical feasibility | contractual permission | API capability | owner approval needed | production readiness |
|---|---|---|---|---|---|
| **Freelancehunt** | API answers unauthenticated (verified 2026-10-15) | **DISCOVER / READ / PREPARE_BID permitted; SUBMIT_BID and SUBMIT_APPLICATION forbidden** (agreement cl. 3.1.11) | Official API 2.0, owner-generated token | Record determination; generate token | **Adapter implemented**, not enabled |
| **Contra** | robots.txt permits listing paths | **UNRESOLVED** — ToS unread | Read-only public API behind `X-API-Key`; covers profiles, not briefs | Read ToS; decide on Contra Pro | Blocked: the job board is behind a paid tier |
| **PeoplePerHour** | robots.txt permits listing paths, publishes a jobs sitemap | **UNRESOLVED** — ToS unread | No public API found | Read ToS | Not started |
| **Upwork** | API exists (GraphQL, OAuth 2.0) | **UNRESOLVED — evidence unobtainable by machine** | Reported: no proposal-submission mutation; "Bulk Access" broadly restricted | Owner must read terms while signed in | Not started |
| **Fiverr** | — | `DISCOVER` **forbidden** (robots.txt `Disallow: /search/`) | No public seller API found | — | Not started |
| **Freelancer.com** | — | `DISCOVER`, `DISCOVER_AUTHENTICATED`, `SUBMIT_BID` all **forbidden** | API exists; bidding may consume paid credits | — | **UNSUPPORTED_AUTOMATION** |
| **Guru** | — | **UNRESOLVED** — site refuses machine reads | — | — | Must not be worked around |

### B.2 What was re-verified today, and what could not be

**Re-verified (measured, 2026-10-16).** robots.txt fetched from seven
hosts. Six returned 200. Guru returned **403 for the third time on a
third date** (2026-10-13, 10-14, 10-16) — a site consistently declining
to be read by a machine. That is an answer, and working around it is
out of the question.

Confirmed unchanged: Upwork `Disallow: /jobs/`; Fiverr
`Disallow: /search/`; Freelancer `Disallow: /jobs/?*` and `/work/*`.

**New this round.** PeoplePerHour's robots.txt not only permits listing
pages (it disallows `/*?`, i.e. query strings, and `/job/new*`,
`/job/bidders*`) but **publishes job sitemaps** —
`sitemap.live.jobs.index.xml` and `sitemap.live.freelance_jobs.0.xml`.
A published sitemap is an explicit invitation to crawl those listings.
That raises PPH from "nothing known" to "crawler permission is
affirmative, contract unread" — the same standing as Contra, and PPH's
board is not behind a paywall, which Contra's is.

Also new: Contra's robots.txt carries
`Content-Signal: ai-train=no, search=yes, ai-input=yes` — explicitly
permitting inference-time use. Freelancehunt carries
`search=yes, ai-train=no, use=reference` and blocks `ClaudeBot`
entirely while leaving search and citation agents alone.

**Could not be re-verified (measured failure).** Every policy page that
matters refuses automated reads:

| document | result today |
|---|---|
| `freelancehunt.com/en/service/rules` (cl. 3.1.11) | **403** |
| `freelancehunt.com/en/pages/agreement.html` | **403** |
| `www.upwork.com/legal/api` | **403** |
| `www.upwork.com/legal` | **403** |
| Upwork GraphQL API documentation | **403** |

Consequences, stated plainly rather than papered over:

- The Freelancehunt determination of **2026-10-15 stands as the most
  recent verified reading**; today's re-verification was blocked and I
  did not work around it. Its posture is coherent — read the API, not
  the website — and today's 403 is consistent with that.
- **Upwork remains UNRESOLVED.** A third-party search result quoted a
  broad "Bulk Access" definition covering "automated or systematic
  enumeration, retrieval, access, viewing… or processing of all or a
  substantial portion of any category of Upwork Content", and reported
  that no proposal-submission mutation exists. **I could not verify
  either against the official document.** Recorded as *reported*, not
  as evidence. Resolving it requires the owner to read the terms while
  signed in — a human action, not an engineering one.

### B.3 The distinction that matters most

Freelancehunt is the clearest illustration of why these columns are
kept apart. Technically it is the *easiest* platform to automate end to
end: a public API, no token needed to read, documented endpoints.
Contractually, submission is the one thing named as prohibited. So
maximum technical feasibility coincides with explicit contractual
prohibition on the step that earns money, and **no owner approval can
change that** — the owner cannot grant a permission the platform
withholds from them.

---

## C. Best acquisition channels, ranked

### C.1 The weighted model

Thirteen criteria the brief named. Weights are **judgment**; the
underlying facts are measured or marked unresolved.

| criterion | weight | rationale |
|---|---:|---|
| Automation permission | 18 | a channel that forbids the step is worth nothing downstream |
| Payment accessibility | 15 | `money_can_move` is false; nothing earns until it is true |
| Fastest legitimate first dollar | 12 | the standing objective |
| Fit for the five certified skills | 10 | CSV, spreadsheet, document, data-entry, report |
| Customer demand | 10 | the thing a website does not create |
| Direct-hire capability | 8 | the only route that closes without a platform |
| Implementation difficulty | 7 | engineering cost to first use |
| Account risk | 7 | a ban costs the channel permanently |
| Legitimate API availability | 5 | nice, not decisive |
| Average contract value | 3 | all channels serve similar small work |
| Platform fees | 2 | 0–20%, second-order at this scale |
| Operating cost | 2 | all low |
| Scalability | 1 | irrelevant before the first dollar |

### C.2 Scores

| channel | perm | pay | fast | fit | demand | hire | impl | risk | **total** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **A — Direct hire (owner's network)** | 10 | 6 | 9 | 9 | 6 | 10 | 9 | 10 | **8.4** |
| **C — Owner-operated service site** | 10 | 6 | 4 | 9 | 2 | 10 | 4 | 9 | **6.6** |
| **B1 — Freelancehunt (read-only)** | 6 | 5 | 5 | 7 | 7 | 2 | 8 | 7 | **5.7** |
| **B2 — PeoplePerHour** | 3 | 5 | 3 | 7 | 7 | 3 | 5 | 4 | **4.5** |
| **B3 — Contra** | 3 | 5 | 2 | 6 | 5 | 3 | 4 | 4 | **3.8** |
| **B4 — Upwork** | 2 | 5 | 2 | 8 | 9 | 3 | 3 | 3 | **3.8** |
| **B5 — Freelancer.com** | 0 | 5 | 1 | 7 | 8 | 2 | 2 | 1 | **2.8** |
| **B6 — Fiverr / Guru** | 1 | 5 | 1 | 6 | 7 | 2 | 2 | 2 | **2.7** |

Score notes. *Permission* is near-measured: 10 where no third party's
terms apply at all (A and C), 6 for Freelancehunt (reading permitted,
submission forbidden), 0–3 where forbidden or unresolved. *Payment* is
6 everywhere for A and C and 5 on platforms, because the rail is
unbuilt in both cases — platforms at least have escrow, but the owner
still cannot receive. *Demand* for C is a 2 and that is the whole
argument of section C.5.

### C.3 Required lists

**Top five overall channels**
1. Direct hire from the owner's existing network (8.4)
2. Owner-operated service site (6.6)
3. Freelancehunt, read-only discovery (5.7)
4. PeoplePerHour, pending a terms reading (4.5)
5. Contra and Upwork, tied and both unresolved (3.8)

**Top three for autonomous discovery** — only **one** qualifies.
1. **Freelancehunt**, official API, permission recorded 2026-10-15.

PeoplePerHour and Contra have affirmative *crawler* permission and
unread contracts, which is not the same thing; per the brief I report
fewer rather than inventing qualifying candidates. If the owner reads
and clears PPH's terms, it becomes the second.

**Top three for direct-hire acquisition**
1. Direct hire from the owner's network — no third-party permission
   involved
2. Owner-operated service site — same, plus automated intake
3. Freelancehunt invitations — clients may invite; `SUBMIT_BID` is
   forbidden but an invitation is initiated by the client. **The
   permission status of responding to an invitation is UNRESOLVED** and
   must not be assumed from the reading permission.

**Top three for lowest operating cost**
1. Direct hire — zero marginal cost
2. Freelancehunt read-only — one API call per poll, no fee to read
3. Owner-operated site — hosting and Stripe fees only, no platform cut

**Top three for fastest legitimate first dollar** — only **two**
qualify.
1. **Direct hire.** Needs no new capability, no platform permission and
   no website. Needs the payment rail.
2. **Owner-operated site.** Same payment rail, plus build and demand.

No marketplace qualifies, because none permits DeskPilot to close.

### C.4 What may be automated per channel, and what stays blocked

| channel | DeskPilot may automate | blocked |
|---|---|---|
| **A — Direct hire** | intake of a brief the client sent, clarifying questions, quoting within pricing rules, execution, verification, delivery, invoicing | sending the first approach to a stranger; accepting a binding contract without the Gate; confirming payment without a `rail_event` |
| **B1 — Freelancehunt** | polling `/v2/projects`, qualification, skill matching, deduplication, drafting a proposal for the owner to send **by hand** | `SUBMIT_BID`, `SUBMIT_APPLICATION` (**forbidden, cl. 3.1.11**); messaging (unresolved); accepting contracts (unresolved) |
| **B2 — PeoplePerHour** | nothing yet | everything, pending a terms reading |
| **B3 — Contra** | nothing useful — robots.txt permits the pages that do not contain the work | the Opportunities board is behind a paid tier: `DISCOVER_AUTHENTICATED` + `SPEND_MONEY` |
| **B4 — Upwork** | nothing | everything, unresolved |
| **B5/B6 — Freelancer, Fiverr, Guru** | nothing | discovery forbidden or machine reads refused |
| **C — Owner site** | the entire chain: intake, quoting, checkout, assignment, verification, delivery, payment confirmation | nothing by contract; everything by the Action Gate's own rules |

### C.5 Channel C, and the demand problem

Channel C has the best automation-permission profile available: it is
the owner's own site, so no third party's terms apply, and every step
the brief lists — five service categories, fixed-price packages, hourly
options, uploads, quoting, checkout, intake, assignment, verification,
delivery, payment confirmation — is permissible.

**It also has no customers.** The brief's warning is the correct one and
I will not soften it: building a site does not create demand. The
honest position is that I have **not measured** acquisition cost or
conversion for this niche, and inventing figures would be worse than
saying so.

What can be said structurally:

- C and A need **the same payment rail**. So A is strictly faster: it
  reuses a relationship that already exists and requires no site.
- C's demand must be bought (ads), earned (SEO, months) or borrowed
  (the owner's network) — and the third is just Channel A with extra
  steps.
- Therefore C is the right **destination** and the wrong **starting
  point**.

The evidence that would settle it is cheap and should precede the
build: a small paid-traffic test against a single landing page for one
service, measuring enquiries per unit spend, before any intake or
checkout code exists. That is a marketing experiment, not an
engineering project, and it is listed in section O as an owner
decision.

---

## D. Direct-hire strategy

The mechanics DeskPilot already supports, and the gaps.

**Fixed-price work.** The quoting path exists (`solvent/quoting.py`,
pricing controls, the Governor). A client brief arrives, is
quarantined as untrusted content, requirements are extracted as
unconfirmed drafts, the owner or a structured client confirmation
promotes them, and only confirmed requirements may gate a commitment.
Nothing new is needed.

**Hourly work.** `HOURLY_MINIMUM` exists as a permission concept in
`sourceaccess.py`. Hourly is harder to verify honestly than fixed
price — the deliverable is time, which no deterministic checker can
recompute — so fixed-price packages suit the verification architecture
far better. **Recommendation: lead with fixed price.**

**Published-price packages.** The strongest fit. A fixed scope at a
fixed price against the five certified skills is exactly what the
generated-battery evidence covers, and it removes the estimation risk
that hourly introduces.

**Customer invitations.** Permitted in principle on platforms, but the
permission to *respond* to one is unresolved everywhere and must not be
inferred from a reading permission.

**Repeat-client contracts.** The highest-value and least-examined
route. A satisfied client is the only acquisition channel with no
third-party permission question, no CAC and a known conversion rate.
`clientrelations.py` exists; whether it supports a repeat-engagement
flow was not audited here and is listed in section Q.

**Automated intake where permitted.** On the owner's own site,
everything. Through a platform, nothing beyond reading.

---

## E. Browser repository comparison — updated

Re-checked today. **Three of four audited commits have not moved**;
`browser-use` (`ccd22fef`), `playwright-mcp` (`b8b4183e`) and
`UI-TARS-desktop` (`3ac2cb8d`) are unchanged since the previous audit.
The Playwright monorepo moved from `ac22e99d` to `7676475b`.

**Every finding in the previous report remains valid**, and the one
that mattered most was re-verified at the *newer* commit (section H).

What the re-examination adds, per repository:

**Playwright MCP.** Three capabilities the previous audit missed, all
material:
- `browser_snapshot` accepts `target` and `depth`, so extraction can be
  scoped — though depth had no measurable effect on a flat listing page
  (section F).
- Both `browser_snapshot` and `browser_find` accept `filename`, writing
  to disk instead of the response. This is the finding that changes the
  architecture.
- Tools declare `type: 'readOnly' | 'action'`. Useful for Gate design —
  but **not a security boundary**: `browser_run_code_unsafe` is merely
  `action`, the same class as `browser_navigate`.

**browser-use.** The investigation the brief asked for confirms the
previous reading and sharpens it. Fifteen watchdogs
(`crash`, `downloads`, `popups`, `permissions`, `storage_state`,
`aboutblank`, `dom`, `security`, `captcha`, `har_recording`,
`recording`, `screenshot`, `local_browser`, `default_action`) are the
most complete long-running reliability machinery of the three, and that
remains its strongest claim. Its autonomous navigation and multi-step
execution are precisely what DeskPilot must **not** adopt, because they
are a second decision-maker. Its context management is viewport-scoped
and plateaus at 26 interactive elements regardless of page length —
measured — which is a correctness limit for enumeration, not merely a
cost one. Browser state management is genuinely strong; the telemetry
default (task text, URLs visited, final result, to PostHog) remains
disqualifying without an explicit opt-out.

**UI-TARS.** Visual interaction, coordinate actions and screen
understanding are real and work on software with no DOM — the one thing
neither browser tool can do. Its operator abstraction
(`packages/ui-tars/operators/`) is a clean interface worth copying. Its
recovery from unexpected UI states is model-driven, which means
non-deterministic and unauditable. On this host it remains disqualified
for the reason measured last time: `screen.grab()` photographs the whole
desktop including MT5, `nut-js` drives the real mouse and keyboard, and
Ctrl+V paste clobbers the clipboard.

**These features are not equally reliable or appropriate.** The ranking
for DeskPilot is unchanged: Playwright MCP 8.5, browser-use 5.1,
UI-TARS 3.3.

---

## F. Playwright MCP improvement opportunities, ranked

Measured on a 120-listing synthetic page. Ground truth: 120 listings,
120 detail URLs.

| strategy | response tokens | listings recovered | URLs recovered | time |
|---|---:|---:|---:|---:|
| full snapshot in response | 11,586 | 120/120 | 120/120 | 0.064 s |
| snapshot `target=main` | 11,144 | 120/120 | 120/120 | 0.095 s |
| snapshot `depth=6` / `depth=10` | 11,586 | 120/120 | 120/120 | 0.06–0.09 s |
| `browser_find` regex `Project \d+` | 11,541 | — | 119/120 | 0.056 s |
| **snapshot → `filename`** | **42** | **120/120** | **120/120** | **0.048 s** |

Ranked by benefit against risk and cost:

1. **Extract to file, not to context.** Benefit: 276× token reduction
   (11,586 → 42) at full fidelity. Risk: files accumulate on disk —
   the run also auto-wrote `page-<timestamp>.yml` into `--output-dir`,
   so retention needs a policy. Cost: trivial. **This is the single
   highest-value change and it needs no new code in the server.**
2. **Pagination-aware extraction with deduplication.** Benefit:
   correctness, not cost — the measured risk on listing boards is
   missing jobs, not spending tokens. Cost: low. Not yet measured
   against a paginated fixture (section Q).
3. **Scoped snapshots (`target`).** Benefit: 4% here, likely more on a
   page with heavy chrome. Low cost, low risk.
4. **`browser_find` for sparse lookups only.** Measured: a broad regex
   matching every card returned 11,541 tokens — as expensive as the
   whole snapshot, because context is attached per match. Useful to
   locate one element; useless to enumerate many. **A finding that
   contradicts the tool's own description of being "cheaper".**
5. **Depth limiting — no measurable benefit** on a flat listing page.
   Reported honestly rather than recommended.
6. **Screenshot fallback.** Fixed 1,229 tokens, but unparseable by
   deterministic code. Appropriate only as evidence for a human, never
   as the extraction path.

**The rule the brief asked for is the one the measurements support:**
do not optimise tokens at the expense of missed jobs. The
file-extraction route needs no such trade — it is both the cheapest and
the most complete.

---

## G. Complementary capabilities worth adopting

For each: the source, the weakness, whether DeskPilot already has it,
dependencies, complexity, performance, security, and how to test it.

| # | capability | from | DeskPilot has it? | new deps | complexity | test |
|---|---|---|---|---|---|---|
| 1 | Structured navigation | PW-MCP | no | none (subprocess) | M | fixture pages |
| 2 | Reliable extraction | PW-MCP | no | none | S | ground-truth fixtures |
| 3 | Multi-page workflows | PW-MCP | partial (orchestrator) | none | M | paginated fixture |
| 4 | Form understanding | PW-MCP | no | none | M | controlled form env |
| 5 | Navigation recovery | browser-use | no | none (reimplement) | M | fault injection |
| 6 | Session management | PW-MCP | no | none | S | restart test |
| 7 | Browser isolation | PW-MCP `--isolated` | n/a | none | S | profile inspection |
| 8 | Download handling | browser-use watchdog | no | none | M | fixture download |
| 9 | Upload handling | PW-MCP `browser_file_upload` | no | none | M | controlled form |
| 10 | Error detection | both | **yes** — supervision | none | S | existing suite |
| 11 | Timeout handling | PW-MCP config | partial | none | S | slow fixture |
| 12 | Long-running operation | browser-use watchdogs | partial | none | L | soak test |
| 13 | Resource efficiency | measured here | **yes** — VPS guard | none | S | memory gate |
| 14 | Context reduction | PW-MCP `filename` | no | none | **S** | token measurement |
| 15 | Deterministic execution | PW-MCP refs | **yes** in principle | none | S | replay test |
| 16 | Permission enforcement | facade (section H) | **yes** — Action Gate | none | M | unsafe-call test |
| 17 | Audit logging | — | **yes** — audit log | none | S | existing suite |
| 18 | Restart recovery | browser-use crash watchdog | **yes** for jobs | none | M | kill test |
| 19 | Site adapters | DeskPilot `marketplaces.py` | **yes** | none | M | per-adapter battery |
| 20 | Action Gate compatibility | PW-MCP named tools | **yes** — the Gate | none | M | gate enforcement test |

**Eight of twenty already exist.** The genuinely new ones are 1, 2, 14
and the recovery ideas (5, 8, 12). Everything else is DeskPilot's
existing machinery pointed at a new source — which is the right
outcome, and the reason not to import an agent framework.

**From browser-use, adopt ideas not code**: the watchdog pattern —
independent single-purpose observers on a long-running session — maps
onto `supervision.py`. Reproducing four of them (crash, timeout,
unexpected-popup, download-failure) captures most of the value without
importing 105 packages.

**From UI-TARS, adopt one idea and defer the rest**: the operator
abstraction, as the shape to use *if* desktop control is ever needed on
a machine that is not the MT5 host. Nothing else transfers.

---

## H. Security design — the unsafe tool

### H.1 Verification against a pinned upstream

**The finding holds, and upstream has not fixed it.** Verified today at
`7676475b`, a *newer* commit than the one previously audited:

- `browser_run_code_unsafe` still exists
- still `capability: 'core'`
- still described in its own source as *"executes arbitrary JavaScript
  in the Playwright server process and is RCE-equivalent"*
- `filteredTools` still admits anything whose capability
  `startsWith('core')`, so the capability config cannot remove it

### H.2 What it can actually reach — measured

I called it directly and had it make an outbound request to a loopback
server I controlled:

- **It executed.** The request arrived.
- **It reached a host the browser never visited** and that no allowlist
  mentioned.
- **`--allowed-origins https://example.invalid` did not stop it.** The
  request still arrived. The origin allowlist routes the *browser
  context*; the tool's `fetch` runs in the *server process*. Two
  layers, one control.

Also measured: the server **does** reject unknown tool names
(`browser_definitely_not_a_tool` → "not found"), so its registry is
enforced — but the unsafe tool is in the registry.

### H.3 Another reachable interface, found

**WebMCP is enabled by default.** It collects and exposes tools that a
*page* registers. The source labels them "page-provided, untrusted"
(`backend/webmcp.ts:133`), which is honest, but it means the tool
surface is not static: it changes with page content, and the page is
attacker-influenced. `--no-webmcp` disables it and **must be set
explicitly**.

### H.4 The enforcement boundary — demonstrated

The brief's rule: a client-side wrapper is insufficient if another
reachable interface can invoke unsafe tools. So the first question is
whether another interface exists. Measured:

| mode | listening sockets |
|---|---|
| stdio (no `--port`) | **none** |
| `--port 0` | one ephemeral TCP port |

In stdio mode the child's **stdin is the only interface**, and the
parent owns it. A facade that owns that pipe is therefore a real
boundary, not merely a convention — provided `--port` is never used.

Demonstrated with a stdlib-only facade modelling DeskPilot's
controller:

```
1. permitted call (browser_navigate): ok
2. browser_run_code_unsafe: refused by the facade
2. browser_evaluate:        refused by the facade
2. browser_click:           refused by the facade
3. tool names actually written to the pipe: ['browser_navigate']
4. listening sockets on the server process: none
```

### H.5 Verdict: NOT BLOCKED, conditionally

Secure isolation **can** be demonstrated, so integration is not blocked
— but only under all of the following, and the conditions are the
deliverable:

1. **stdio only.** `--port` never passed; absence of a listening socket
   asserted at start and verifiable from `/proc` (or the Windows
   equivalent) as a runtime invariant.
2. **Static name allowlist in the controller**, which is the sole
   writer to stdin. Allowlist by name, never by the server's declared
   `type`, which is a model hint.
3. **`--no-webmcp`**, so a page cannot extend the tool surface.
4. **`--isolated`** and a dedicated browser profile.
5. **Separate OS identity** with no write access to DeskPilot's files
   or database, because of H.2: if the tool is ever invoked, the
   browser-level controls will not contain it.
6. **Host-level outbound firewall** restricted to the permitted hosts —
   the only control that actually binds the server process's own
   `fetch`.
7. **Read-only tool set to begin**: navigate, snapshot, find.

Residual risk, stated rather than hidden: the server will always offer
the unsafe tool, so the boundary is DeskPilot's to hold. Conditions 5
and 6 exist precisely so that a failure of conditions 1–3 is contained
rather than fatal. The strongest available improvement would be an
upstream tool-level allowlist; there is none today.

---

## I. Action Gate compatibility

| | A: library | B: browser service | C: restricted process + narrow IPC | D: custom controller |
|---|---|---|---|---|
| Security | poor — in-process | good | **good** | best in theory |
| Dependency exposure | **breaks zero-dep** | breaks it | **no Python import** | depends on Playwright anyway |
| Performance | best | network hop | **one pipe** | best |
| Complexity | low | high | **moderate** | **very high** |
| Maintainability | poor | moderate | **good** | poor — reimplements CDP |
| Auditability | poor | moderate | **good — every call is a line** | good |
| Windows | fine | container needed | **fine** | fine |
| MT5 coexistence | shared process | separate host possible | **separate process, killable** | same |

**Option C is the recommendation.** It is the only one that preserves
the zero-third-party-dependency principle in substance: `solvent`
imports nothing new, because the controller is `subprocess` and `json`
from the standard library. Node and Playwright become *installed
programs*, in the same category as Chromium — which the container
already has — rather than imports.

**This does not weaken the Action Gate, and the distinction is exact.**
The Gate's authority is unchanged: the browser controller is a tool the
Gate may authorise, like any other consequential action. What it does
introduce is a new authority that should be named honestly for the
owner's decision:

> **The authority requested:** permission for DeskPilot to execute an
> external Node.js program with network access, under a restricted OS
> identity, whose tool surface is constrained by a DeskPilot-side
> allowlist. It is not permission for `solvent` to import third-party
> code, and not a change to what the Gate must approve.

Option D (a minimal custom Playwright controller) was considered and
rejected: it still depends on Playwright, and reimplementing the
accessibility-snapshot pipeline would mean owning a parser that
Microsoft maintains with 20,391 lines of tests.

---

## J. Resource analysis — Windows VPS and MT5

Measured on this host with synthetic pages and the preinstalled
Chromium.

**Is one browser worker enough? Yes — measured.**

| workers | pages/sec | resident memory, working |
|---:|---:|---:|
| 1 | 6.13 | 908 MB |
| 2 | 6.96 (+14%) | 1,758 MB (+94%) |
| 3 | 7.38 (+20%) | 2,604 MB (+187%) |

**Tripling the workers bought 20% throughput for 2.9× the memory.** On a
VPS shared with MT5 that trade is clearly bad. One worker.

Honest caveat: these pages were served from loopback with no network
latency, so parallelism had little to hide. Against real sites the
throughput gain would be larger — but the memory cost is linear
regardless, and memory is what threatens MT5.

Other measured figures: a single session idles at ~127 MB and peaks at
~908 MB with pages open; browser-use's equivalent was ~1,188 MB.

Recommendations:
- **One headless worker, started per discovery window and torn down.**
  Not a resident service.
- **Gate browser start on free memory** and refuse rather than swap.
  Swapping degrades MT5 before it degrades the agent, and MT5 is the
  thing with money in it.
- **Below-normal process priority** for the browser tree.
- **Bound the output directory.** The run auto-wrote a timestamped
  `.yml` per snapshot in addition to the named file; unbounded, that
  fills a disk.
- **No shared mouse or keyboard control, ever.** Headless means there is
  no cursor to share. **UI-TARS is not recommended**, on the measured
  grounds that it captures the whole screen and drives the real input
  devices.

---

## K. Browser intelligence — feeding the existing components

**DeskPilot already has what this needs. No new memory database is
warranted**, which the brief asked me to check before recommending one.

`solvent/memory.py` holds operational learning with a verification-tier
gate at the write, and `solvent/types.py` defines the tiers. They map
onto the brief's categories exactly:

| brief's category | existing tier | may feed learning? |
|---|---|---|
| Verified facts | `T3_EXTERNAL_FACT` | yes, including economic |
| Publicly observed data (browser) | `T1_DETERMINISTIC` at best | yes, non-economic only |
| Inferred estimates | `T1` | yes, non-economic only |
| Historical outcomes | depends on origin | per tier |
| Uncertain information | `T0_SELF_REPORT` | **never** |

The rule already enforced: `may_feed_economic_learning` is true **only**
for `T3_EXTERNAL_FACT`. So a browser observation of an advertised price
may inform Finder's prioritisation and job classification, and **may
never** update pricing or margin models — only a verified payment can.
That is the correct answer and it required no new mechanism.

**Untrusted input is also already solved.** `solvent/content.py` names
web pages explicitly: everything external enters through
`quarantine()`, renders as `<untrusted:…>` when interpolated, and
requirements derived from it are born `MODEL_EXTRACTED_UNCONFIRMED`.
Only confirmed requirements may gate a commitment. **Browser output
must enter through that same function**, and then it structurally
cannot become an instruction source with authority.

Where observations would genuinely help: Finder accuracy and
opportunity prioritisation (T1, permitted), marketplace knowledge such
as posting volumes and category mix (T1), and conversion measurement —
but only once outcomes are T3.

---

## L. Testing plan

No live site. Fixtures only. Nothing submits, messages or pays.

**Functional**
1. Job discovery against a synthetic board — found vs ground truth.
2. Extraction fidelity — every field, ground truth written first.
3. Skill matching against the five certified skills.
4. Pricing-data extraction, recorded as T1 and never economic.
5. Duplicate detection across overlapping pages.
6. Client-requirement interpretation → unconfirmed drafts only.

**Reliability** (fault injection against a local mock)
7. Browser crash mid-extraction → restart, no partial record kept.
8. Navigation timeout → bounded retry, then refusal.
9. Session expiry → re-auth refused without the Gate.
10. Changed layout / broken selector → refuse, do not guess.
11. Unexpected popup and partial load.
12. Network failure and download failure.
13. Pagination error → detect a gap rather than silently truncate.
14. Restart recovery → resume without duplicating.

**Security — adversarial**
15. Prompt injection in a job description.
16. Malicious page instructions aimed at the controller.
17. Hidden form fields; unexpected submission buttons.
18. Redirects off the allowlist → blocked at the request layer.
19. Unauthorised download attempts.
20. Fake payment instructions in page text → must not reach the ledger.
21. Session manipulation.
22. **Attempts to trigger unsafe code execution** — assert
    `browser_run_code_unsafe` and `browser_evaluate` are refused by the
    facade and never written to the pipe (demonstrated in H.4).
23. **WebMCP tool-registration attempt** — a page registering a tool
    must not extend the surface when `--no-webmcp` is set.

**Resource and gate**
24. Memory gate refuses browser start under pressure.
25. Action Gate enforcement: no consequential call without approval.
26. Listening-socket invariant: zero, asserted continuously.

Pass criteria: every refusal is explicit and reasoned; no listing is
silently missed; nothing from a page ever becomes a confirmed
requirement without the owner.

---

## M. Implementation options

| | Minimal | Intermediate | Advanced |
|---|---|---|---|
| Scope | navigate + snapshot-to-file, read-only | + find, pagination, dedup, recovery | + authorised form interaction |
| Tools allowed | 3 | 5 | 8, write tools Gate-approved |
| Effort | ~1 capability's worth | ~2 | ~4 |
| Risk | lowest | low | moderate |
| Value | Freelancehunt + PPH discovery feeding Finder | reliable multi-page enumeration | direct-hire site workflows |

**Recommended: Minimal first**, because it delivers the only permitted
use (reading) and the measurements show it delivers it at 42 tokens
with full fidelity. Intermediate once a paginated fixture suite exists.
Advanced only after the owner resolves the permission questions in O.

---

## N. Recommended architecture

```
  DeskPilot Finder
        ↓
  existing decision engine        (unchanged — no second decision-maker)
        ↓
  existing Action Gate            (unchanged — authority stays here)
        ↓
  restricted browser controller   (stdlib: subprocess + json)
        · static tool-name allowlist, sole writer to stdin
        · asserts: no listening socket, --no-webmcp, --isolated
        · every call logged to the audit log before dispatch
        ↓  stdio pipe, no port
  playwright-mcp, separate OS identity
        · restricted filesystem, host firewall to permitted hosts only
        · one worker, started per window, torn down after
        ↓
  headless Chromium, memory-gated against MT5
        ↓
  permitted site  →  snapshot written to file
        ↓
  stdlib parser  →  content.quarantine()  →  unconfirmed drafts
```

The browser does `OBSERVE → EXTRACT → INTERACT WHEN AUTHORIZED →
RETURN EVIDENCE` and nothing else. Evidence supported: the extraction
path costs 42 response tokens at 120/120 fidelity (F); the boundary is
demonstrable (H.4); one worker suffices (J); the untrusted-input and
learning rules already exist (K).

---

## O. Owner decisions

1. **May DeskPilot execute an external Node.js program?** Named exactly
   in section I. This is the gating decision.
2. **Read Upwork's API terms while signed in.** The only route to
   resolving the largest unresolved platform. Machine access is 403.
3. **Read PeoplePerHour's terms of service.** Its robots.txt permits
   listing pages and publishes job sitemaps; only the contract is
   missing. Best ratio of effort to new permission.
4. **Record a determination for Freelancehunt and generate the API
   token** — the adapter exists and is idle.
5. **Stand up one payment rail.** The binding constraint on all
   channels. Stripe-style KYC needs a legal entity, identity documents,
   a bank account and a site describing the product; reported timelines
   are days, and it is an owner action, not engineering.
6. **Is responding to a platform invitation permitted?** Unresolved
   everywhere; must not be inferred from a reading permission.
7. **Run a paid-traffic demand test before building Channel C.**
8. **Confirm one browser worker, headless, no desktop control.**
9. **Approve the read-only tool allowlist** (navigate, snapshot, find)
   and that write tools stay Gate-approved individually.

## P. First-dollar path

**Fastest credible route, unchanged by this audit and now better
evidenced: a fixed-price direct-hire assignment from the owner's
existing network, in one of the five certified skills, paid through one
working rail.**

It requires no platform permission, no browser capability, no website
and no new code. Its only blocker is decision 5.

Realistic sequence: stand up the rail (days, owner-gated) → take one
fixed-price CSV or data-entry job from a known contact → deliver
through the existing verified pipeline → confirm payment via
`rail_event`. Discovery automation changes nothing about this path;
it is worth building for the *second* dollar and beyond.

## Q. Remaining uncertainties

1. **Upwork's actual terms** — UNRESOLVED, machine-unreadable.
2. **Freelancehunt clause 3.1.11 as of today** — re-verification
   blocked by 403; the 2026-10-15 reading stands.
3. **PeoplePerHour and Contra terms** — unread.
4. **Whether responding to an invitation is permitted** — unresolved.
5. **Channel C demand** — not measured; must not be assumed.
6. **Extraction correctness on real listing pages** — all figures here
   are from synthetic fixtures. A saved-page corpus is the missing
   evidence, and it can be built without touching a live site.
7. **Pagination and deduplication** — designed, not measured.
8. **Windows behaviour** — everything here ran on Linux.
9. **Whether `clientrelations.py` supports repeat engagements** — not
   audited; relevant because repeat clients are the best channel.
10. **Transitive licence and CVE review** of the Playwright tree — not
    done.

---

*No production file was modified. No dependency was added to DeskPilot.
No marketplace automation was activated, no bid submitted, no contract
accepted, no message sent, no funds moved, no subscription introduced,
no service deployed, and MT5 was not touched. The Action Gate was not
changed, and no second decision engine was proposed.*
