# DeskPilot — Upwork official integration review

Date: 2026-10-18 · Platform: Linux x86_64 · Branch
`claude/solvent-architecture-audit-8ot9et`

What Upwork officially supports, established from Upwork's own
infrastructure where that was possible and marked **UNVERIFIED** where it
was not. No capability is claimed because a third-party article
describes it.

**Headline: `UNVERIFIED` dominates this review, and not because the
research was thin.** Every Upwork-hosted documentation page returns HTTP
403 to this container. Two primary sources were reachable and are used
below; nothing else was, and I did not work around the block.

---

## 1. What was reachable, and what was not

| Source | Result | Used as evidence |
|---|---|---|
| `https://mcp.upwork.com/.well-known/oauth-protected-resource` | **200** | Yes — primary |
| `https://mcp.upwork.com/.well-known/oauth-authorization-server` | **200** | Yes — primary |
| `https://mcp.upwork.com/mcp` | **401** | Yes — the endpoint exists and requires a bearer token |
| `https://www.upwork.com/robots.txt` | **200** | Yes — primary |
| `https://www.upwork.com/llms.txt` | **200** | Yes — primary |
| `https://www.upwork.com/ai/mcp` | **403** | No |
| `https://www.upwork.com/developer/documentation/graphql/api/docs/index.html` | **403** | No |
| `https://www.upwork.com/developer` | **403** | No |
| `https://www.upwork.com/ai-reference` | **403** | No |
| `https://www.upwork.com/legal` | **403** | No |
| `https://www.upwork.com/i/how-it-works/freelancer` | **403** | No |
| `https://developers.upwork.com/`, `https://developer.upwork.com/api-changelog.html` | 301 → the 403 page | No |

The two `.well-known` documents are the OAuth discovery metadata the MCP
specification requires a server to publish unauthenticated. Reading them
is the documented way a client discovers how to authenticate; it is not
a bypass, and no credential was presented, requested or guessed at any
point.

The 403s are bot protection rather than a `robots.txt` rule — the
developer documentation path is not disallowed in `robots.txt`, it is
simply refused to this client. **The brief says not to scrape restricted
pages, so the investigation stopped there rather than changing headers
until something answered.** Everything below that depends on those pages
is marked UNVERIFIED, which is the honest state and not a gap that more
effort from this container would close.

## 2. The eighteen questions

| # | Question | Status | Basis |
|---|---|---|---|
| 1 | Does Upwork provide an official MCP server? | **VERIFIED — yes** | Upwork-operated host `mcp.upwork.com` publishes MCP OAuth metadata naming the resource `"Upwork MCP"` at `https://mcp.upwork.com/mcp` |
| 2 | Is it publicly available to freelancers? | **UNVERIFIED** | The metadata does not distinguish freelancer from client accounts, and the page that would say so is 403 |
| 3 | Can DeskPilot authenticate? | **PARTIAL — the mechanism is verified, the account is not** | OAuth 2.0 authorization code with PKCE (`S256`), refresh tokens and client credentials; `token_endpoint_auth_methods_supported` includes `"none"`, so a public client is supported. No account exists and none was created |
| 4 | Is job discovery supported? | **UNVERIFIED** | No tool list is obtainable without authenticating, which was not done |
| 5 | Is proposal drafting supported? | **UNVERIFIED** | As above |
| 6 | Is proposal submission supported? | **UNVERIFIED** | As above |
| 7 | Does submission require confirmation inside an Upwork-controlled interface? | **UNVERIFIED — and assumed yes by design** | §4 below |
| 8 | Is automated marketplace browsing permitted? | **VERIFIED — no, for job search** | `robots.txt` disallows `/*/jobs/search*`, `/search/profiles/*`, `/jobs/rss`, `/jobs/atom`, `/rss/`, `/atom/`, `/freelancers/public/api/`. `llms.txt` says to avoid "Dynamic search result pages, login-gated pages, or session-based URLs" |
| 9 | Is automated proposal preparation permitted? | **UNVERIFIED** | Not addressed by either reachable source |
| 10 | Can external SMS approval satisfy Upwork's confirmation requirements? | **VERIFIED — no, as a matter of architecture** | §4 below |
| 11 | Are competing proposal counts available? | **UNVERIFIED** | Not in either reachable source |
| 12 | Are competing bid amounts available? | **UNVERIFIED** | Not in either reachable source |
| 13 | Is client hiring history available? | **UNVERIFIED** | Not in either reachable source |
| 14 | Are current Connects requirements available? | **UNVERIFIED** | Not in either reachable source |
| 15 | Can applicable freelancer service fees be obtained before submission? | **UNVERIFIED** | `llms.txt` links client pricing only; the freelancer fee page is 403 |
| 16 | Do API rate limits apply? | **UNVERIFIED** | Figures circulate in third-party write-ups; the official page is 403, so none are adopted |
| 17 | Is special developer approval required? | **PARTIAL** | The OAuth metadata's `registration_endpoint` is the ordinary signup page and public clients are supported, which is *consistent* with no separate approval — but the absence of an approval step in OAuth metadata is not a statement of policy |
| 18 | Do commercial-use restrictions apply? | **UNVERIFIED** | The Legal Center is 403. Assume restrictions exist until read |

## 3. The primary evidence, quoted

`https://mcp.upwork.com/.well-known/oauth-protected-resource`:

```json
{"resource":"https://mcp.upwork.com/mcp",
 "authorization_servers":["https://mcp.upwork.com"],
 "bearer_methods_supported":["header"],
 "resource_name":"Upwork MCP"}
```

`https://mcp.upwork.com/.well-known/oauth-authorization-server`:

```json
{"issuer":"https://mcp.upwork.com",
 "authorization_endpoint":"https://www.upwork.com/ab/account-security/oauth2/authorize",
 "token_endpoint":"https://www.upwork.com/api/v3/oauth2/token",
 "revocation_endpoint":"https://www.upwork.com/api/v3/oauth2/token/revoke",
 "registration_endpoint":"https://www.upwork.com/register",
 "response_types_supported":["code"],
 "grant_types_supported":["authorization_code","refresh_token","client_credentials"],
 "token_endpoint_auth_methods_supported":["none","client_secret_basic","client_secret_post","private_key_jwt"],
 "code_challenge_methods_supported":["S256"],
 "client_id_metadata_document_supported":true}
```

`https://www.upwork.com/robots.txt`, the lines that bear on discovery:

```
Disallow: /*/jobs/search*
Disallow: /search/profiles/*
Disallow: /freelancers/public/api/
Disallow: /jobs/rss
Disallow: /jobs/atom
Disallow: /rss/
Disallow: /atom/
```

`https://www.upwork.com/llms.txt`, under **Model Guidance → Avoid**:

> Dynamic search result pages, login-gated pages, or session-based URLs

`llms.txt` is otherwise entirely about hiring. It describes no freelancer
automation surface, does not mention the MCP server, and links client
pricing but not freelancer fees.

## 4. Two conclusions that do not depend on the 403s

**The authorization endpoint is on `www.upwork.com`.** Whatever the tool
list turns out to be, the OAuth grant is given by a person signing in to
Upwork's own interface and approving the client there. DeskPilot cannot
mint that grant, and no amount of owner approval inside DeskPilot
substitutes for it.

**An SMS approval cannot satisfy an Upwork confirmation requirement, and
the question is slightly wrong.** The two approvals answer different
questions and neither can stand in for the other:

* DeskPilot's owner approval answers *"may this business spend its money
  and its capacity on this job at this price?"* — a question about
  DeskPilot's own controls, which Upwork has no view of.
* Upwork's confirmation answers *"does this account holder authorise
  this action on this platform?"* — a question about Upwork's account,
  which DeskPilot has no standing to answer.

So the design below keeps both, in that order, and treats the second as
a step DeskPilot prepares for and never performs.

## 5. What this means for DeskPilot

The honest reading is that **submission is out of scope until it can be
verified**, and the architecture is built that way rather than built
optimistically and fenced later:

* DeskPilot evaluates and recommends; the owner approves; the result is
  a proposal that is *eligible* for submission and nothing more.
* No submission path is implemented, so there is none to disable.
* No Connects are modelled as spendable, because the per-proposal cost
  cannot be read from an official source.
* Every figure DeskPilot shows the owner is labelled by provenance, and
  the labels for competition, Connects and fees are all
  **UNAVAILABLE** or **MODEL ESTIMATE** on today's evidence — never
  **VERIFIED PLATFORM DATA**.

`deskpilot-upwork-approval-architecture.md` describes the approval
workflow and `deskpilot-upwork-bid-economics.md` the figures and their
provenance.

## 6. What would change this review

Three things, in order of how much they unblock:

1. **An authenticated session against `mcp.upwork.com`.** One
   `tools/list` call answers questions 4, 5, 6, 11, 12, 13 and 14
   definitively. It needs an Upwork account and an owner who grants the
   OAuth scope in Upwork's interface — neither of which this engagement
   authorises.
2. **A reachable copy of the official documentation.** Questions 2, 15,
   16, 17 and 18 are answerable from pages that exist and simply will
   not serve this container. An operator who can open them, or a
   network path Upwork does not refuse, closes those.
3. **The Legal Center**, specifically, for question 18. Nothing should
   be built against this platform commercially until the terms have
   been read by someone who can read them.

## 7. What this review does not say

* It does not say the official MCP server lacks the capabilities the
  third-party write-ups describe. It says they were not verified, which
  is a different statement, and the brief asked for that distinction.
* It does not say automated browsing is forbidden in general — it says
  `robots.txt` disallows the job-search and profile-search paths, which
  is the part that matters for discovery.
* It does not record any attempt to authenticate, register a client,
  spend a Connect, or reach a login-gated page. None was made.
