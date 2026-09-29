# The job acquisition network

How DeskPilot finds work, and why finding it is safe to automate before
deciding what to take.

## One engine, not two

DeskPilot already had a business pipeline. This adds a **source layer** in front
of it, and nothing else:

```
JOB SOURCES → adapters → untrusted ingestion → normalisation → deduplication
→ risk screening → capability → conformance → price → profitability → risk
→ capacity → opportunity cost → policy → THE EXISTING JOB ORCHESTRATOR
```

Everything from "capability" rightwards already existed and is unchanged. The
new code finds candidates and describes them. It decides nothing about them.

The six P0 authorities are untouched: Policy Store, Audit Log, Ledger,
Financial Governor, Job Orchestrator, Action Gate. No second registry, no
second risk authority, no second incident store, no second notification engine.

## The separations that matter

Each of these is a permission, stored per source, and none implies another:

| | |
|---|---|
| `DISCOVER` `READ` `IMPORT` | the read side — granted when a source is registered |
| `PREPARE_APPLICATION` `PREPARE_BID` `PREPARE_MESSAGE` | drafting |
| `SUBMIT_APPLICATION` `SUBMIT_BID` | sending an offer |
| `SEND_MESSAGE` | talking to a person |
| `SPEND_MONEY` | application credits, membership fees, boosts |

**Registering a source grants the read side and nothing else.** A source that
could apply, bid, message or spend by being added is a source that can commit
the business by being added. Each consequential permission is granted
separately, by the owner, with a recorded reason. There is deliberately no
"grant everything".

**A granted permission is still not permission to act now.** `may()` says so in
its own reason string. The Action Gate decides each individual external effect,
every time, and simulation keeps it shut.

## Which platforms are connected

**None.** Twelve are catalogued; not one claims automated access.

Whether a platform permits automated access is a reading of *that platform's
current terms for a specific account*. It differs per platform, differs by
account type, changes over time, and cannot be determined from inside this
repository. A wrong guess gets the owner's account banned, which costs them the
channel permanently. So every commercial marketplace is recorded
`UNDETERMINED`, and `register_source` already refuses to mark a source
`PERMITTED` without a written determination.

`sourcecatalog.automated_sources()` returns an empty list. It is a function
rather than a constant so no page can be written to say otherwise.

| platform | category | automated discovery | manual entry |
|---|---|---|---|
| Upwork, Fiverr, Freelancer, PeoplePerHour, Guru, Contra | freelance marketplace | not established | yes |
| SAM.gov | public procurement | not established | yes |
| State/local procurement portal | public procurement | not established (one entry per portal) | yes |
| RFP / bid board | bid board | not established | yes |
| Owner-entered, file import, client inquiry | direct | n/a | yes |

**Manual ingestion works today, on all twelve.** The owner brings an
opportunity they found themselves and DeskPilot does the rest — qualification,
pricing, the Governor, execution, verification, delivery, payment. It is the
only acquisition path blocked by nothing external, it needs nobody's
permission, and it feeds the identical pipeline. The first dollar does not
require an API.

Nothing here bypasses CAPTCHA, evades anti-bot systems, rotates identities,
shares credentials or scrapes against a restriction. A source that would need
any of those is `UNSUPPORTED_AUTOMATION` and is never polled.

## Untrusted content

A job listing is written by whoever posted it, and some are written
specifically to be read by a system like this one. Listings are quarantined
structurally (`UntrustedContent`) and screened for what they are *trying to
do*: prompt injection, credential requests, execution requests, security
disabling, policy tampering, cross-client exfiltration, advance fees, fake
cheques, money-mule work, off-platform pressure, illegal work.

Screening produces **signals with quoted evidence**. It cannot reject an
opportunity, block a job or change a rule — a listing asking for an up-front
fee is usually fraud and occasionally a legitimate bond, and a stage that
rejected it outright would be wrong sometimes and unappealable always. A
flagged listing is still recorded, because a silently discarded one teaches
nobody anything.

## Where a source may point

A source URL naming loopback, a private or link-local network, a cloud metadata
service (`169.254.169.254`, `metadata.google.internal`, …), a non-`https`
scheme, or embedded credentials is refused **at registration** — before anyone
can be asked to put it on the egress allowlist. The Action Gate's allowlist
remains the real control; this is the layer that stops such a destination ever
being proposed for it.

## What is not built

- **No submission client.** `Applications.submit()` consults the source
  permission, then the Action Gate, and stops. Even with both open it does not
  send, because building that last step is a decision to start applying for
  real work. The boundary exists and is tested now, rather than being added
  later by whoever also wants it open.
- **No live adapter for any platform.** Fixtures and manual entry only.
- **No spending.** No adapter can purchase anything.

## Reading the code

| file | what it holds |
|---|---|
| `solvent/discovery.py` | the authority: registry, permissions, health, polling, dedup |
| `solvent/sourceaccess.py` | permission vocabulary, health states, URL safety |
| `solvent/sourcecatalog.py` | the twelve platforms and what is known about each |
| `solvent/opportunityrisk.py` | screening signals — no decisions |
| `solvent/application.py` | prepare, and the wall before submit |
