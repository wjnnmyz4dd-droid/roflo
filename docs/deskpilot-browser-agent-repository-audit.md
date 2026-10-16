# Browser and computer-use agent repositories — an audit for DeskPilot

**This is an audit. Nothing was integrated, no dependency was added to
DeskPilot, and no production file was modified.** Every repository was
cloned into a scratch directory outside the working tree; every
benchmark ran against a loopback HTTP server serving a synthetic page.

Date: 2026-10-16. Branch: `claude/solvent-architecture-audit-8ot9et`.
Measurements: `docs/evidence/browser-agent-measurements.json`.

**How to read the scores.** Every score below separates two things that
are routinely conflated. *Measured* means I ran it or read it in the
source at the commit named, and the number is reproducible. *Judgment*
means I weighed a measured fact against DeskPilot's stated constraints.
The weights in section 21 are mine, not measurements, and are stated so
the owner can disagree with the weighting without re-doing the work.

---

## 1. Executive summary

Three repositories were audited in depth at pinned commits:

| repository | commit | licence (from the file) |
|---|---|---|
| `github.com/browser-use/browser-use` | `ccd22fef47a1a36e0c4596fef5db73a5117b9d06` | MIT |
| `github.com/microsoft/playwright-mcp` | `b8b4183e099f136cbec0388a6088d4aa2f6b9685` | Apache-2.0 |
| `github.com/bytedance/UI-TARS-desktop` | `3ac2cb8d946d78d5ff92ad17b318eaafa18c178c` | Apache-2.0 |

Because `playwright-mcp` at that commit is a 51-line shim, its real
implementation was audited in the Playwright monorepo:
`github.com/microsoft/playwright` at `ac22e99d98291194ddb5a14b53c05deac514e604`.

**The headline conclusion is that one repository is sufficient for what
DeskPilot actually needs, and it is not the most popular one.**
`playwright-mcp` scores 8.5/10 against DeskPilot's constraints,
`browser-use` 5.1, `UI-TARS-desktop` 3.3. The reason is not quality —
all three are well-engineered — but shape. DeskPilot already has a
decision engine, an Action Gate and a verification layer. It does not
need an agent; it needs a *pair of hands* that takes named, auditable
instructions. `playwright-mcp` is exactly that. `browser-use` is an
agent, and most of what it adds is machinery DeskPilot already owns.

Four findings did not come from the documentation and would change a
decision made from blog comparisons alone:

1. **browser-use sends the task text, every URL visited, and the final
   result to a third-party analytics endpoint by default.** Opt-out,
   not opt-in. Measured in source.
2. **playwright-mcp's default tool surface contains an RCE-equivalent
   tool that its capability config cannot remove.** Measured by running
   the server and listing its tools.
3. **The popular claim that accessibility snapshots are cheaper than
   screenshots is only true on simple pages.** Measured: the crossover
   is around 15 list items, and at 120 items the snapshot costs 9.7× a
   screenshot.
4. **browser-use and playwright-mcp disagree about what a page *is*.**
   browser-use serialises only the viewport and plateaus at 26
   interactive elements; playwright-mcp serialises the whole page. For
   enumerating job listings that difference decides the architecture.

**Nothing here should be installed yet.** Adopting any of them breaks
`ZeroThirdPartyDependencies`, whose stated reason is that every
dependency is a potential path around the Action Gate. That is an owner
decision (section 23).

---

## 2. Repository selection methodology

Candidates were gathered from current comparison surveys, then filtered
against DeskPilot's actual constraints rather than against popularity.
The constraints are not preferences; each is enforced somewhere in the
codebase or by an agreement already read:

- `solvent` imports only the standard library
  (`tests_solvent/test_architecture.py::ZeroThirdPartyDependencies`).
- The machine is a Windows VPS shared with the owner's MT5 terminal,
  and the owner's keyboard, screen and MT5 session must not be touched.
- Consequential actions pass a human Action Gate, so an action must be
  *nameable* before it can be approved.
- Automated proposal submission is forbidden (Freelancehunt clause
  3.1.11), so the target use is discovery and direct-hire support, not
  bidding.
- The verification architecture requires that a check never share the
  freedom of the thing it checks.

Seven repositories were examined; three were taken to depth. Licences
for all seven were read from the files in the clones, not from package
metadata or from any article.

| repository | licence (verified) | taken to depth | why / why not |
|---|---|---|---|
| browser-use/browser-use | MIT | **yes** | the reference autonomous browser agent; Python, matching DeskPilot |
| microsoft/playwright-mcp | Apache-2.0 | **yes** | the deterministic, accessibility-tree approach; closest in philosophy |
| bytedance/UI-TARS-desktop | Apache-2.0 | **yes** | the only credible open desktop-control agent; needed for that category |
| Skyvern-AI/skyvern | **AGPL-3.0** | no | licence (section 4) |
| browserbase/stagehand | MIT | no | overlaps playwright-mcp; TypeScript-only; cloud-oriented |
| OpenAdaptAI/OpenAdapt | MIT | no | record-and-replay, not navigation |
| steel-dev/steel-browser | Apache-2.0 | no | a browser *host*, complementary rather than comparable |

---

## 3. The three selected repositories

### 3.1 browser-use

- URL: https://github.com/browser-use/browser-use
- Commit: `ccd22fef47a1a36e0c4596fef5db73a5117b9d06`, 2026-10-16
- Version `0.13.13`, Python `>=3.11,<4.0`
- **68,774 lines** of Python across the package (measured)
- **125 test files, 36,121 lines** (measured)

An autonomous agent: give it a task in English and a model key, and it
drives Chromium through CDP until it believes the task is done. The
largest subsystems are `browser/` (19,261 lines), `llm/` (10,036) and
`agent/` (7,759).

### 3.2 playwright-mcp

- URL: https://github.com/microsoft/playwright-mcp
- Commit: `b8b4183e099f136cbec0388a6088d4aa2f6b9685`, 2026-10-08
- Published as `@playwright/mcp` `0.0.83`, Node `>=18`

**A structural finding that affects how it should be evaluated:** at
this commit the repository is a shim. `index.js` is 19 lines and
`cli.js` is 32; `src/README.md` says the source now lives in the
Playwright monorepo. Every comparison that scores "playwright-mcp" by
reading this repository is scoring 51 lines of re-export.

The implementation audited is therefore
https://github.com/microsoft/playwright at
`ac22e99d98291194ddb5a14b53c05deac514e604`,
`packages/playwright-core/src/tools/`:

- `tools/mcp/` — 13 files, **2,627 lines** (protocol, config, relay)
- `tools/backend/` — 41 files, **6,120 lines** (the tools themselves)
- tests: `tests/mcp/`, **102 spec files, 20,391 lines** (measured)

Not an agent. An MCP server exposing discrete, schema'd browser tools —
**25 in the default surface** (measured by running it).

### 3.3 UI-TARS-desktop

- URL: https://github.com/bytedance/UI-TARS-desktop
- Commit: `3ac2cb8d946d78d5ff92ad17b318eaafa18c178c`, 2026-10-09 (`0.3.1`)
- Node `>=20`, pnpm monorepo, Electron
- **185,806 lines** of TS/TSX across 1,402 files (measured)
- **129 test files, 34,406 lines** (measured)
- Root `LICENSE` and `multimodal/tarko/llm-client/LICENSE` both Apache-2.0

A GUI agent that sees the screen and drives the real mouse and
keyboard, paired with a vision-language model (UI-TARS-1.5-7B
self-hosted, or a hosted vision model).

---

## 4. Alternatives rejected, and why

**Skyvern-AI/skyvern** (`d5e79eb`) — rejected on licence. Its `LICENSE`
is the GNU Affero GPL v3 (15 occurrences of "Affero"). AGPL-3.0 extends
copyleft to software users interact with over a network. For a
commercial service that is a live obligation, not a footnote, and the
alternative is a commercial licence nobody has authorised. Rejected
before capability was assessed, because no capability would change it.

**browserbase/stagehand** (`10418f9`, MIT) — rejected on redundancy, not
merit. Its `act`/`extract`/`observe` decomposition is good design and
is the same insight as playwright-mcp's discrete tools. It is
TypeScript-first and oriented toward Browserbase's hosted browsers,
which adds a vendor relationship DeskPilot does not need.

**OpenAdaptAI/OpenAdapt** (`e022d97`, MIT) — rejected on fit. It records
and replays desktop workflows. DeskPilot's problem is navigating sites
it has not seen, not replaying a recorded path.

**steel-dev/steel-browser** (`d8a4a7e`, Apache-2.0) — not a competitor.
It is a browser *host* with session management. If the owner ever wants
browser sessions isolated from the MT5 host, this is the category to
revisit (section 20).

---

## 5. Source-code findings

Each finding below names the file and what was done to establish it.

### 5.1 browser-use ships default-on telemetry carrying task content

`browser_use/telemetry/service.py` and `telemetry/views.py`.

`ProductTelemetry` initialises a PostHog client to
`https://eu.i.posthog.com` unless `ANONYMIZED_TELEMETRY` is falsy;
`browser_use/config.py:58` defaults it to `'true'`. The identifier is
`_machine_fingerprint()`, a SHA-256 of the MAC address
(`uuid.getnode()`) and hostname.

The payload is not just counters. `AgentTelemetryEvent` declares
`task: str`, `urls_visited: Sequence[str | None]`,
`final_result_response: str | None` and `error_message`, and
`BaseTelemetryEvent.properties` is `asdict(self)` minus the name — so
every declared field is transmitted. The client is constructed with
`disable_geoip=False` and `enable_exception_autocapture=True`.

Stated plainly: by default, the task description, every URL the agent
visited, and the final result leave the machine. It is honestly
documented and one environment variable turns it off. But a system
whose posture is "no unauthorised egress" would be adopting a component
that egresses by default.

### 5.2 browser-use's domain allowlist is real, with two gaps

`browser_use/browser/watchdogs/security_watchdog.py:176`.

Credit first: enforcement is not only a prompt. `_is_url_allowed`
parses with `urlparse` and compares `parsed.hostname` — not substring
matching, which is where this is usually got wrong — and the watchdog
blocks navigation events. There is *also* prompt-level reinforcement
(`beta/service.py:1367 _task_with_domain_constraints`), which is
belt-and-braces rather than the only control.

Two gaps:

- **Default is allow-all.** With neither `allowed_domains` nor
  `prohibited_domains` set, the function returns `True` (line ~213).
- **`data:` and `blob:` URLs bypass the allowlist entirely**, returned
  `True` before any domain check because "they don't have hostnames". A
  `data:` URL can carry arbitrary HTML and script.

### 5.3 browser-use is wired for an external CAPTCHA solver

`browser_use/browser/watchdogs/captcha_watchdog.py`.

The repository does **not** solve CAPTCHAs. It listens for
`BrowserUse.captchaSolverStarted/Finished` CDP events "from the browser
proxy" and exposes `wait_if_captcha_solving()` that the agent step loop
blocks on. The solver runs in browser-use's own proxy infrastructure.

Recorded precisely because the owner forbade CAPTCHA evasion: adopting
browser-use means adopting client-side support for a solving service,
inert unless that proxy is used, but present.

### 5.4 playwright-mcp exposes an RCE-equivalent tool that cannot be gated off

`packages/playwright-core/src/tools/backend/runCode.ts:38` and
`backend/tools.ts:83`.

The tool is `browser_run_code_unsafe`. Its own description reads:
*"Unsafe: executes arbitrary JavaScript in the Playwright server process
and is RCE-equivalent."* It builds a `vm` context seeded with `fetch`,
`Buffer` and `crypto`. Node's `vm` is not a security boundary.

The gating mechanism is `filteredTools`:

```ts
return browserTools.filter(tool => tool.capability.startsWith('core')
  || config.capabilities?.includes(tool.capability))
```

Anything whose capability starts with `core` is **unconditionally
enabled**. `runCode` and `evaluate` are both `capability: 'core'`, and
`config.d.ts` offers no tool-level allowlist. Confirmed empirically: a
default server returned **25 tools**, including `browser_evaluate` and
`browser_run_code_unsafe`.

Microsoft names the hazard honestly. The consequence for DeskPilot is
concrete: **tool filtering must happen on DeskPilot's side of the MCP
connection**, because the server cannot be configured to withhold it.

### 5.5 playwright-mcp's origin control is default-deny once enabled

`backend/context.ts:431`.

```ts
if (this.config.network?.allowedOrigins?.length) {
  disposables.push(await context.route('**', route => route.abort('blockedbyclient')));
  for (const origin of this.config.network.allowedOrigins) { ... route.continue() }
}
```

Every request is aborted, then specific origins are allowed back. This
is stronger than browser-use's model in two ways: it is default-deny
within the mechanism, and it operates on *all requests* — subresources,
XHR, beacons — not only top-level navigations.

The honest qualifier, from the CLI help: `--allowed-origins` "Default is
to allow all." The mechanism is default-deny; switching it on is opt-in.

`file:` is blocked by default — measured, not read: a `browser_navigate`
to a local file returned `Error: Access to "file:" protocol is blocked.`

No telemetry, analytics or phone-home code was found anywhere in
`tools/` (searched for posthog, segment, analytics, telemetry,
mixpanel, amplitude).

### 5.6 UI-TARS controls the real machine

`packages/ui-tars/operators/nut-js/src/index.ts`, via
`@computer-use/nut-js`:

- `screen.grab()` — captures the **entire screen**
- `mouse.move/click/doubleClick/drag`, `mouse.config.mouseSpeed = 3600`
- `keyboard.type/pressKey/releaseKey`, including `Ctrl+V` paste, which
  **overwrites the system clipboard**

There is no window scoping. On a VPS where MT5 is running, this agent
moves the owner's cursor, types into whatever has focus, replaces the
clipboard, and photographs a screen containing open positions and
account balances. Its model is a VLM — self-hosted (GPU) or hosted
(those screenshots leave the machine).

---

## 6. Capability comparison

| | browser-use | playwright-mcp | UI-TARS-desktop |
|---|---|---|---|
| shape | autonomous agent | tool server | autonomous GUI agent |
| surface | web | web | whole desktop |
| perception | DOM+AX, **viewport only** | AX snapshot, **whole page** | screenshots |
| action | CDP, LLM-chosen | 25 named tools | OS mouse/keyboard |
| model required to act | yes | **no** | yes (vision) |
| element addressing | index `[1793]` | ref `e18` + role + name | x/y coordinates |
| link URLs in default output | **no** (`href` not in `DEFAULT_INCLUDE_ATTRIBUTES`) | **yes** (`/url:`) | n/a |
| runtime deps declared | 37 | **2** | pnpm monorepo + Electron |
| packages installed | **105** | **3** | not installed |
| install size | **359 MB** | **19 MB** | not installed |
| telemetry by default | **yes** | none found | not assessed |
| long-running machinery | **15 watchdogs** | isolated contexts | Electron app |

### The measurement that matters most

Both browser tools, same fixtures, same loopback server, no model in
the loop. Text tokens at 4 chars/token; image tokens by
width×height/750. Both indicative, both applied identically.

| list items | playwright-mcp snapshot | browser-use representation | screenshot |
|---:|---:|---:|---:|
| 1 | 154 tok | 49 tok | 1,229 tok |
| 5 | 541 | 239 | 1,229 |
| 20 | 2,014 | 632 | 1,229 |
| 60 | 5,984 | **632** | 1,229 |
| 120 | 11,940 | **638** | 1,229 |

Two things fall out, and neither matches the common summary.

**The "snapshots are cheaper than screenshots" claim inverts at about
15 list items.** At 120 items the snapshot costs 9.7× a screenshot. A
screenshot is O(1) in tokens; an accessibility snapshot is O(page).
Anyone adopting MCP on token-cost grounds should measure their own
pages.

**browser-use's representation plateaus because it only serialises what
is visible.** At 60 and 120 cards it reports the same 26 interactive
elements. It is not truncating arbitrarily — `serializer.py` gates on
`is_visible` — it is describing the viewport. To see the 94 listings
below the fold it must scroll, and each scroll is another model round
trip.

For DeskPilot's actual use — *enumerate every listing on a board* —
this is decisive. playwright-mcp returns the whole page, with roles,
names and `/url:` targets, in **one call, with no model**, in 0.023 s.
That output is parseable by ordinary deterministic code of the kind
`solvent` is already made of.

---

## 7. Unique advantages

**browser-use** — the deepest operational hardening of the three. Fifteen
watchdogs (crash, downloads, popups, permissions, storage-state,
about:blank, recording, DOM, security) represent years of encounters
with real sites. Nothing else audited has this. Its MIT licence is the
most permissive. Its Python matches DeskPilot's language, so its DOM
serializer is readable as a reference even if the agent is not adopted.

**playwright-mcp** — discrete, named, schema'd actions. This is the rare
case of a capability matching an existing architecture exactly:
`browser_click(ref=e18)` is a sentence the Action Gate can put in front
of the owner. A coordinate click is not. Backed by Playwright's own
engine, 20,391 lines of MCP tests, two dependencies, and an explicit
capability model. It is also the only one of the three that produces
useful output with **no model at all** — measured.

**UI-TARS-desktop** — it is the only one that can operate software that
has no web interface. If the work is ever "fill this in a desktop
application", nothing else audited can do it.

---

## 8. Limitations and defects

**browser-use**
- Telemetry carrying task text and URLs, default-on (5.1)
- Domain allowlist default-open; `data:`/`blob:` exempt (5.2)
- CAPTCHA-solver integration surface (5.3)
- 105 packages, 359 MB; bundles four LLM SDKs plus `reportlab`,
  `pypdf`, `cloudpickle`
- Viewport-only perception (section 6) — a correctness limit for
  enumeration, not merely a cost one
- `href` absent from default serialisation; configurable

**playwright-mcp**
- `browser_run_code_unsafe` and `browser_evaluate` in the ungateable
  `core` set (5.4)
- Snapshot cost scales with page size (section 6)
- Accessibility-tree blindness: a canvas app or unlabelled `div` soup
  gives the tree nothing. The `vision` capability exists for this and
  is opt-in — correct design, but it means the deterministic path has a
  hard floor
- The repository is a shim, so pinning `@playwright/mcp` pins a
  re-export whose behaviour is set by `playwright-core` (5.x, section 3.2)
- Version coupling is real: the pinned alpha `playwright-core` demanded
  `chromium-1247` while the host had `chromium-1194`, and the benchmark
  only ran after being pointed at the installed binary explicitly

**UI-TARS-desktop**
- Global input and full-screen capture, unscopable (5.6)
- Requires a VLM: GPU locally, or desktop screenshots to a third party
- 185,806 lines and an Electron app — the largest attack and
  maintenance surface of the three by an order of magnitude

---

## 9. Commercial licensing

Read from `LICENSE`/`COPYING` in each clone, not from metadata.

| repository | licence | commercial use | notes |
|---|---|---|---|
| browser-use | MIT | yes | attribution only |
| playwright-mcp | Apache-2.0 | yes | express patent grant; NOTICE duty |
| UI-TARS-desktop | Apache-2.0 | yes | root and `tarko/llm-client` both |
| skyvern | **AGPL-3.0** | **hazard** | network copyleft; excluded |
| stagehand | MIT | yes | not audited in depth |
| OpenAdapt | MIT | yes | not audited in depth |
| steel-browser | Apache-2.0 | yes | not audited in depth |

None of the three selected carries a licence obstacle. Apache-2.0's
patent grant is a mild positive over MIT for a commercial operator.

Transitive licences were **not** audited. browser-use installs 105
packages; that tree needs its own review before adoption, and this
audit does not stand in for it (section 22).

---

## 10. Security analysis

Ranked against DeskPilot's posture, where the question is not "is this
secure software" but "what can it do that the Action Gate cannot see".

**playwright-mcp — strongest, with one sharp edge.** Default-deny
request interception once origins are configured; `file:` blocked by
default; capability model; no telemetry found; every action a named
tool call that can be logged, diffed and approved before dispatch. The
sharp edge is `browser_run_code_unsafe`, which the server will always
offer and which is RCE-equivalent by its own description. **Mitigable
entirely on DeskPilot's side** by allowlisting tool names at the client.

**browser-use — capable, with egress by default.** A genuine hostname-
parsing allowlist and fifteen watchdogs, undercut by default-on
telemetry carrying task text and visited URLs, an allowlist that is
open until configured, and `data:`/`blob:` exemption. All three are
configurable. None is the default.

**UI-TARS-desktop — incompatible with this host.** Not a criticism of
the project: a desktop agent is supposed to control the desktop. On
*this* machine the desktop contains MT5.

**Prompt injection** affects all three and none solves it. A job board
is attacker-influenced content. This is the reason an agent must not
hold its own authority — which is the architecture DeskPilot already
has, and an argument for the tool-server shape over the agent shape.

---

## 11. Windows / VPS compatibility

Not executed on Windows; this container is Linux. Assessed from
manifests and platform code, and flagged as such.

- **browser-use** — Windows supported; `pyproject.toml` carries
  platform markers (`pyobjc` only on Darwin, `screeninfo` elsewhere).
  `cryptography`, `pillow` and `lxml` need wheels, which exist. Risk:
  low. Confidence: good — markers are explicit.
- **playwright-mcp** — Node ≥18 and a Playwright browser download.
  Playwright is first-class on Windows. Risk: low. The one friction
  measured here was browser-version coupling (section 8), which on
  Windows means a browser install step per upgrade.
- **UI-TARS-desktop** — Electron plus a native `nut-js` addon; Windows
  is a supported target. Risk: moderate, native addon rebuilds.

**Unexecuted:** no Windows test was run. A Windows VPS smoke test is in
the plan at section 17.

---

## 12. MT5 resource implications

Measured on this host, same fixture, same browser binary:

| | processes | resident memory |
|---|---|---|
| playwright-mcp + headless Chromium | 8 | **740 MB** |
| browser-use + headless Chromium | 14 | **1,188 MB** (1,095 Chrome + 93 Python) |

The honest reading: **the browser is the cost, not the framework.** The
difference between 740 MB and 1,188 MB is real but secondary; either
way a browser session is roughly 0.7–1.2 GB resident alongside MT5. The
framework overhead (93 MB of Python, or Node's equivalent) is the small
part.

Consequences:

- A browser must not run continuously. It should be started for a
  bounded discovery window and torn down — which suits DeskPilot's
  scheduled-task model and is natural for a tool server, less natural
  for a long-lived agent loop.
- The existing VPS resource protection should gate browser start on
  free memory and refuse rather than swap. Swapping would degrade MT5
  before it degrades the agent, and MT5 is the thing with money in it.
- **UI-TARS is disqualified on a stronger ground than memory**: it
  shares the mouse, keyboard, clipboard and screen with MT5. No
  resource budget makes that acceptable.

---

## 13. Human approval compatibility

This is where the three separate most sharply, and it is mostly
architecture rather than measurement.

An Action Gate can only approve what can be *described*. Compare the
three descriptions of one action:

- playwright-mcp: `browser_click(ref="e18")` where the snapshot records
  `button "Place a bid" [ref=e18]` — the owner is approving "click the
  Place a bid button on project 4411"
- browser-use: `click_element_by_index(1793)` — resolvable only against
  a DOM state the gate would have to reconstruct
- UI-TARS: `click(x=412, y=338)` — not reviewable in any useful sense

playwright-mcp also separates reads from writes naturally: `snapshot`,
`find` and `network_requests` change nothing, while `click`, `type` and
`fill_form` do. A gate can auto-approve the first set and hold the
second. That mapping is available because the tools are named, not
because anyone designed for approval.

A further point in playwright-mcp's favour: **it does not need a model
to perceive.** DeskPilot could call `browser_navigate` + `browser_snapshot`
and hand the result to ordinary deterministic code — no model involved
in discovery at all. That keeps the recurring architectural rule
intact: the component that checks the result does not share the freedom
of the component that produced it.

---

## 14. Marketplace automation constraints

Unchanged by this audit, and worth restating because a browser agent
makes the temptation concrete: **Freelancehunt clause 3.1.11 forbids
automatic proposal submission, and Freelancer §33 forbids automated API
access broadly.** A browser is not a loophole. Submitting a proposal
through a browser agent is automatic proposal submission.

What a browser capability may legitimately do:

- read public listing pages a human could read
- extract structured facts for the owner to review
- prepare a draft the owner sends themselves
- support a direct-hire conversation the client initiated

What it must not do, regardless of capability:

- submit a proposal or bid
- create or verify accounts
- act in a way designed to look human when it is not
- touch a CAPTCHA (which rules out wiring up browser-use's solver hooks)

Both browser candidates support a domain allowlist, so a deployment
could be pinned to permitted sites. **playwright-mcp's is enforced at
the request layer and is default-deny once set**, which is the stronger
guarantee if the owner ever wants "this agent can physically only reach
these three hosts".

---

## 15. Direct-hire acquisition suitability

Direct hire is the only compliant route to a first paying assignment,
so this is the use case that matters.

The work is: watch a small number of permitted pages, notice a
legitimate opportunity, extract it faithfully, and put it in front of
the owner. That is **read-mostly, extraction-heavy, and correctness-
critical** — a client brief transcribed wrongly is worse than one
missed.

playwright-mcp fits this almost exactly. A whole-page snapshot with
roles, accessible names and `/url:` targets is a structured document.
DeskPilot can parse it deterministically, apply the existing
verification discipline, and never call a model for discovery. The
`browser_find` and `browser_network_request` tools cover the cases
where the snapshot is not enough.

browser-use is a poorer fit for the same job *because* it is better at
something else. Its viewport-scoped perception means enumerating a long
board takes scroll-and-reason cycles, each one an LLM call and a chance
to miss a listing. Its strength — deciding what to do next — is the one
thing DeskPilot must not delegate.

UI-TARS is not applicable: these are websites.

---

## 16. Competitive bidding suitability where permitted

Recorded for completeness; **competitive bidding is frozen and nothing
in this audit unfreezes it.**

If a platform's terms ever permitted automated submission, the same
ranking would hold and for a sharper reason. A bid is a binding
commercial commitment. It must be composed deterministically from
agreed inputs, approved by the owner, and submitted as an auditable
action — `browser_fill_form` with named fields and recorded values,
then `browser_click` on a named button, each loggable and replayable.
An agent that decides a bid's content is precisely the thing the
commitment-control design forbids.

So: **if bidding were ever permitted, the tool-server shape would be
more suitable than the agent shape, not less** — the opposite of what
"more autonomous is better" would suggest.

---

## 17. Benchmark results, and the tests not run

### Executed (isolated, loopback only)

Everything in section 6 and section 12. Method: a synthetic job-listing
page at five densities, served from `127.0.0.1` by Python's
`http.server`; the real `@playwright/mcp` server driven over stdio by a
minimal JSON-RPC client written for the purpose; browser-use driven
through `BrowserSession` with no LLM configured. Both used the
container's preinstalled `chromium-1194`. Raw figures in
`docs/evidence/browser-agent-measurements.json`.

Also executed: dependency and install-size measurement (105 packages /
359 MB versus 3 / 19 MB), default tool enumeration from the running MCP
server (25 tools), and the `file:` protocol refusal.

### Not executed, and why

- **Real marketplace pages.** Deliberate. Pointing an agent at a live
  job board during an audit risks exactly the automated interaction the
  agreements forbid. The synthetic fixture is the honest substitute,
  and its limitation is that real pages are heavier and messier.
- **Task-completion success rates.** Would require live sites and model
  keys. Published figures are vendor-reported and were not adopted.
- **UI-TARS end to end.** No display and no GPU in this container.
- **Windows behaviour.** Linux container.
- **Transitive licence audit of browser-use's 105 packages.**

### Test plan, if the owner wants these closed

1. Windows VPS smoke test, MT5 running: start each candidate, navigate
   to one permitted page, snapshot, tear down. Record peak RSS, MT5
   tick latency before/during/after, and whether the cursor or
   clipboard moves. Pass = MT5 unaffected and no input device touched.
2. Extraction fidelity: 20 saved copies of real listing pages, offline.
   Ground truth written before the run. Measure listings found, fields
   correct, and hallucinated fields. This is the number that should
   decide adoption, and it can be measured without touching a live site.
3. Token and latency profile on those same saved pages, replacing the
   synthetic curve in section 6.
4. Prompt-injection battery: saved pages carrying hostile instructions
   in visible text, `alt` attributes and hidden elements. Pass = no
   out-of-allowlist request is attempted and nothing reaches the Action
   Gate that the brief did not ask for.
5. Transitive licence and CVE scan of whichever dependency tree the
   owner accepts.

---

## 18. DeskPilot integration feasibility

**Feasible, and smaller than it looks — but it is not free.**

The blocker is architectural, not technical:
`ZeroThirdPartyDependencies` asserts that `solvent` imports only the
standard library, *"Every dependency is a potential path around the
Action Gate."* Every option here breaks it. That is section 23's
question.

The shape that breaks it least: **do not import anything.** Run
`playwright-mcp` as a separate process and speak MCP to it over stdio
with a stdlib-only JSON-RPC client. The audit's own client is ~40 lines
of `subprocess` and `json` and needs nothing outside the standard
library — demonstrated, since it is what produced section 6.

That gives:

- no Python dependency added to `solvent`; Node and Playwright become
  *installed programs*, like Chromium already is, not imports
- a process boundary that the existing egress and resource guards can
  police
- tool-name allowlisting at the client, which is also the mitigation
  for `browser_run_code_unsafe` (5.4)
- a deterministic discovery path with no model

What would still need building, honestly: a capability registration and
certification for browsing work, generated-battery evidence as every
other capability has, Action Gate wiring for the write tools, and
resource gating on browser start. That is real work, comparable to one
of the five existing skills.

---

## 19. Improvement opportunities

Ideas worth taking from these projects, whatever is adopted:

1. **The watchdog pattern** (browser-use). Independent observers on a
   long-running session — crash, popup, download, permission — each
   with one job. DeskPilot's supervision module is the natural home.
2. **Capability-gated tool surfaces** (playwright-mcp). Grouping actions
   into named capabilities that can be switched off is a cleaner
   expression of what DeskPilot's certified-scope already does.
3. **Refs over coordinates** (playwright-mcp). Addressing a thing by
   role and accessible name rather than position is the same discipline
   as recomputing a check from the artifact rather than trusting a
   report.
4. **Naming the unsafe thing `unsafe`** (playwright-mcp). `browser_run_code_unsafe`
   is a better name than anything DeskPilot currently uses for its own
   sharpest operations.
5. **The honest-defaults inversion.** browser-use's telemetry and
   allow-all allowlist are both documented and both default the
   permissive way. A useful rule for DeskPilot: a default that leaks is
   a defect even when it is documented.

---

## 20. Potential hybrid architecture

**One repository is sufficient.** The honest answer to "would
complementary capabilities inspire a stronger architecture" is: take
*ideas* from the others, not code.

A defensible design, if the owner proceeds:

```
  solvent (stdlib only, unchanged)
    └── browsing capability (new, certified like the other five)
          │  stdlib JSON-RPC over stdio — no import
          ▼
  playwright-mcp process
    · tool-name allowlist applied client-side (no evaluate, no run_code)
    · --allowed-origins pinned to permitted hosts (default-deny)
    · started per discovery window, torn down after
          ▼
  Chromium, headless, memory-gated against MT5
```

Discovery is deterministic: navigate, snapshot, parse the snapshot with
ordinary code, verify with a checker that does not share the parser —
the rule the last sweep spent itself establishing. A model enters only
where judgment is genuinely required, and never holds authority.

Borrowed rather than installed: the watchdog pattern (§19.1) for
session health; browser-use's `DEFAULT_INCLUDE_ATTRIBUTES` as a
reference for what is worth extracting; UI-TARS's operator interface as
the design to copy *if* desktop control is ever needed on a machine
that is not the MT5 host.

Where a second component would genuinely earn its place: **a browser
host** such as steel-browser, if the owner wants browser sessions off
the MT5 machine entirely. That is an infrastructure decision, not an
agent decision, and it is the cleanest answer to section 12.

---

## 21. Weighted rankings

Weights express DeskPilot's constraints as I read them. **They are
judgment.** The inputs are measured. Reweight and the order can change —
that is the point of showing them.

| criterion | weight | why it carries this weight |
|---|---:|---|
| Approval-gate compatibility | 20 | an action that cannot be named cannot be approved |
| Security and auditability | 20 | the agent reads attacker-influenced pages |
| Licence suitability | 15 | commercial operation |
| Footprint vs zero-dependency architecture | 15 | each dependency is a path around the gate |
| MT5 / VPS coexistence | 15 | the trading terminal has the money in it |
| Determinism and testability | 15 | everything here must be certifiable |

| | browser-use | playwright-mcp | UI-TARS |
|---|---:|---:|---:|
| Approval-gate compatibility | 5 | **10** | 3 |
| Security and auditability | 4 | **7** | 3 |
| Licence suitability | **10** | 9 | 9 |
| Footprint | 3 | **9** | 2 |
| MT5 coexistence | 5 | **7** | 1 |
| Determinism | 4 | **9** | 2 |
| **weighted total** | **5.1** | **8.5** | **3.3** |

Score notes, measured versus judged:

- *Licence* — measured from the files. browser-use 10 (MIT), the other
  two 9 (Apache-2.0, marginally more obligation, offset by a patent
  grant). Judgment: the one-point gap.
- *Footprint* — measured: 105 packages/359 MB versus 3/19 MB. The
  scores are close to a direct reading of those numbers.
- *MT5 coexistence* — memory measured (1,188 MB vs 740 MB); UI-TARS's 1
  is judgment, and it is a hard judgment: sharing the input devices
  with MT5 violates a stated constraint outright.
- *Security* — the facts are measured (telemetry payload, allowlist
  defaults, `data:` exemption, `file:` block, default-deny routing,
  ungateable RCE tool). The weighting of "default-on egress" against
  "ungateable RCE tool, mitigable client-side" is judgment, and it is
  what puts playwright-mcp at 7 rather than 9.
- *Approval compatibility* and *determinism* — mostly judgment, resting
  on two measured facts: playwright-mcp produces usable output with no
  model, and its elements are addressed by role and name rather than
  index or coordinate.

### Category winners

- **Best overall repository — browser-use.** Not DeskPilot-specific.
  For a team that wants a working browser agent today with no
  controller of their own, it delivers the most capability per unit of
  integration work: 68,774 lines, 15 watchdogs, 37 integrations, MIT.
  *Measured:* size, dependency count, watchdog inventory, test volume.
  *Judgment:* that breadth-with-no-scaffolding is what "best overall"
  should mean.
- **Best browser-navigation repository — playwright-mcp**, narrowly, and
  the distinction is real rather than manufactured. It wins on
  reliability and completeness: whole-page perception in one call, 0.023 s,
  no model, 20,391 lines of tests. *browser-use wins this category
  instead if you need navigation without building a controller* — it
  decides where to go, and playwright-mcp never will.
- **Best desktop-control repository — UI-TARS-desktop, uncontested.** It
  is the only audited project that controls a desktop. Saying so is
  more useful than inventing a contest.
- **Best security foundation — playwright-mcp.** *Measured:* no
  telemetry found, `file:` blocked by default, default-deny request
  routing when origins are set, capability model, named auditable
  actions. The `browser_run_code_unsafe` finding is the one real mark
  against it and it is fixable by the client.
- **Best low-resource option — playwright-mcp.** *Measured:* 3 packages,
  19 MB installed, 740 MB resident with a browser open, versus 105
  packages, 359 MB, 1,188 MB.
- **Best long-running automation option — browser-use.** The one
  category where it clearly wins the architecture, not just the
  popularity. *Measured:* 15 purpose-built watchdogs covering crashes,
  popups, downloads, permissions and storage state — the failures that
  only appear after hours of running. playwright-mcp's isolated
  contexts are cleaner per call but it has no equivalent session-health
  machinery.
- **Best candidate for DeskPilot — playwright-mcp** (8.5 vs 5.1 vs 3.3).
- **Best complementary capabilities from the others** — browser-use's
  watchdog pattern and its DOM attribute selection, as *designs* to
  reimplement; UI-TARS's operator abstraction, as the shape to copy if
  desktop control is ever wanted on a machine without MT5 on it.

---

## 22. Recommended next steps

In order, and none of them is "install something".

1. **Owner decision on the zero-dependency invariant** (section 23).
   Everything else waits on it. Note the subprocess design in section
   18 narrows the question to "may DeskPilot shell out to an installed
   Node program", which is a smaller question than "may solvent import
   third-party code".
2. **Run test 2 from section 17** — extraction fidelity against saved
   copies of real listing pages, ground truth written first. It needs
   no live site, no model and no decision, and it is the number that
   should actually decide adoption. This audit measured cost and shape;
   it did not measure whether extraction is *correct* on real pages.
3. **Run test 1** — the Windows VPS smoke test with MT5 running.
4. **Prompt-injection battery** (test 4) before any write-capable tool
   is enabled.
5. Only then: a certified browsing capability built the way every other
   capability here was — generated battery, independent checker,
   recorded owner decision.

---

## 23. Unresolved questions requiring owner decisions

1. **Does DeskPilot accept a third-party runtime at all?**
   `ZeroThirdPartyDependencies` says no and gives a security reason.
   The subprocess design avoids a Python dependency but still requires
   Node and Playwright installed on the VPS. *This cannot be decided
   from evidence; it is a risk appetite.*
2. **Is a browser permitted on the MT5 host?** Measured cost is
   0.7–1.2 GB resident while open. Accept it with memory gating, or put
   browsing on a separate machine (section 20)?
3. **Which hosts may be reached?** The default-deny origin list is only
   as good as the list. The owner must name the permitted sites.
4. **Is read-only browsing auto-approved?** `snapshot`, `find` and
   `navigate` within the allowlist change nothing. Treating them as
   routine makes discovery useful; treating every call as gated makes
   it unusable.
5. **Who filters the tool surface?** The recommendation is a
   client-side allowlist excluding `browser_evaluate` and
   `browser_run_code_unsafe`, because the server cannot withhold them.
   Confirm that is acceptable rather than a reason to decline.
6. **Does the CAPTCHA integration surface in browser-use disqualify it
   entirely**, even unused and even if only its DOM serializer were
   ever read for reference?
7. **Is desktop control wanted at all?** If yes, it needs a machine
   that is not the MT5 host, and that is a hosting decision before it
   is a software one.

---

*No DeskPilot production file was modified in producing this audit. No
dependency was added to the project. No bidding was activated, no
application submitted, no contract accepted, no funds moved, no service
deployed, and MT5 was not touched. Repositories were cloned read-only
into a scratch directory; benchmarks ran against a loopback server.*
