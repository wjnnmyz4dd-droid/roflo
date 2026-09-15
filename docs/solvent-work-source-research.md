# Solvent — Work Source Research

**Research date: 15 September 2026.** Terms change; re-verify before acting.

Last revision of this document asserted nothing because no research had been
done. This revision reports what was actually found, separates what was verified
from what was not, and records where verification was refused.

## What could and could not be verified

| Attempt | Result |
| --- | --- |
| Upwork automation help page (primary) | **HTTP 403** to an automated fetch |
| Upwork user agreement (primary) | **HTTP 403** to an automated fetch |
| Algora API docs (primary) | **HTTP 503** |
| Algora source repository | **Fetched successfully** |
| Secondary reporting on all of the above | Fetched successfully |

**The 403s were not worked around.** A platform returning 403 to an automated
client is expressing a preference, and circumventing it would be the first thing
this architecture forbids. Where only secondary sources were available, findings
are labelled as such and remain an owner-verification item.

---

## Finding 1 — General freelance marketplaces: **DO NOT USE** for autonomous operation

Secondary sources are consistent and mutually corroborating:

- Automated proposal submission at scale is prohibited and treated as spam.
- **There is no public API for submitting proposals.** The public API is limited
  to basic functions such as retrieving profiles.
- The user agreement is reported to name "robots, scripts, and automated
  interaction with the site through your account" explicitly.
- 2026 guidance is reported to require freelancers to **personally review and
  customise all client communications**.
- Bulk scraping of job feeds or profiles is prohibited even where technically
  possible.

**Assessment.** A personal-performance requirement is not a technical obstacle to
route around — it is a statement that the contracted human must do the work.
Solvent is an AI operator, so it structurally cannot satisfy that clause, and no
implementation makes it acceptable. **Recommended: DO NOT USE for autonomous
operation.** A human freelancer using Solvent as a private back-office tool is a
different question, and is the owner's to answer, not Solvent's to assume.

Sources: [Upwork help — use bots and other automation properly](https://support.upwork.com/hc/en-us/articles/43342677368467-Use-bots-and-other-automation-properly) (403 to automated fetch; title only) · [UpHunt on auto-apply and ToS](https://uphunt.io/blog/upwork-auto-apply-api) · [GigRadar responsible-automation guide](https://gigradar.io/blog/responsible-automation-on-upwork) · [GigUp on 2026 AI-proposal rules](https://giguphq.com/blog/upwork-ai-proposals-banned-2026)

---

## Finding 2 — Open-source bounties: strong structural fit, **one serious new risk**

**Why the structure fits Solvent unusually well:** a bounty is paid on merge, so
completion is verified by someone other than the worker — which is exactly the
T3 external fact Solvent's verify-before-learn law requires, and exactly what an
invoice does not provide. Reported payouts settle via Stripe 1–3 business days
after merge, giving a real payment-verification signal. Upfront cost is zero.

**Verified directly:** the Algora repository is **AGPL-3.0**, and its README
documents **no public API** for listing bounties. Claims of an unauthenticated
`/api/orgs/{org}/bounties` endpoint come from third parties, not from the
project, and remain **unverified**.

**The risk that changes the recommendation.** A substantial and growing number of
open-source projects now restrict or ban AI-generated contributions:

- Oracle imposed an interim ban on AI-generated contributions to **OpenJDK**,
  covering content generated "in part or in full" by LLMs.
- **GCC** banned AI-generated contributions on 29 July 2026, including material
  derived from LLM output "even after human revision".
- **Zig, NetBSD, GIMP, Gentoo and qemu** reject LLM-generated pull requests.
- Of 120 projects surveyed with written policies: **72 allow with conditions, 37
  ban outright, 11 undecided.**
- **LLVM** permits AI assistance but requires disclosure and that the contributor
  can answer questions about the code under review.
- A recurring stated reason is that a contributor cannot honestly certify the
  Developer Certificate of Origin for generated code.

**Assessment.** Clearing the bounty *platform's* terms is not sufficient.
**Every target repository's AI policy must be screened before work begins**, and
where assistance is permitted, disclosed. A capability that silently submits
AI-written patches into projects that ban them would be both a terms violation
and a reputational failure that ends the channel. This is a per-repository gate,
and it belongs in Capability & Conformance, not in a source adapter.

Sources: [algora-io/algora (AGPL-3.0)](https://github.com/algora-io/algora) · [Oracle bans AI contributions to OpenJDK](https://www.opensourceforu.com/2026/08/oracle-bans-ai-generated-contributions-to-openjdk/) · [GCC draws a hard line](https://www.marcpope.com/blog/gcc-just-drew-a-hard-line-on-ai-generated-code-the-rest-of-open-source-should-be-watching) · [survey of 120 project AI policies](https://medium.com/@yadavrakshit60/i-read-the-ai-policies-of-120-open-source-projects-here-is-what-they-actually-say-9a5ea6897893) · [Algora agent earning guide](https://gigs.sh/p/algora)

---

## Finding 3 — Agent-native work marketplaces: promising, **unverified**

A category exists that is *designed* for the thing Solvent does: platforms where
agents discover work, deliver and get paid without a human driving a browser.
Reported examples include OpenTask (hosted MCP tools and published agent cards),
Circle's agent services marketplace (launched 11 May 2026, reported 32 services /
349 endpoints), and the x402 HTTP payment protocol for agent-to-agent payment.

**Assessment.** Structurally this is the correct long-term home for Solvent: an
explicit automation path rather than an absent prohibition. But none of it was
verified from primary sources, the economics are unknown, and a new marketplace
is exactly where a "fake high-paying job" is most likely to appear. **Recommended:
DEFER pending owner verification**, and treat the first engagement as a research
exercise rather than a revenue plan.

Sources: [OpenTask](https://opentask.ai/) · [platforms where agents earn](https://dev.to/kirothebot/the-agent-economy-is-real-12-platforms-where-ai-agents-actually-earn-money-may-2026-5bm2) · [agent-to-agent marketplace guide](https://dev.to/nikhilranka23/the-complete-guide-to-agent-to-agent-marketplaces-in-2026-5f9a)

---

## Recommendation

### BEST FIRST SOURCE — **direct / owner-sourced work, via the manual bridge**

**Implemented and working today** (`ManualSource`, `run_manual_opportunity`).

**Why.** It is the only acquisition path blocked by *nothing external*. No
platform terms apply, no API is needed, no identity question arises beyond OD-1,
and the client relationship is the owner's own. Solvent still does the entire
job: qualification, capability and conformance, jurisdiction-aware pricing, the
Governor, permission, execution, verification, delivery support, payment
verification, actual profit and learning. Owner-entered terms are
`OWNER_CONFIRMED`, so qualification proceeds without a human re-confirming what
the owner just typed.

- **Allowed automation:** everything after acquisition.
- **Solvent may:** qualify, price, plan, execute, verify, prepare delivery,
  track payment, compute actual profit, learn.
- **Solvent may not:** find the client, sign the contract, or receive the money —
  those are the owner's, and OD-1/OD-2 govern them.
- **Identity:** the owner's own, already established.
- **Payment path:** however the owner already gets paid (OD-2 records it).
- **Fees:** none beyond the owner's existing arrangements.
- **Integration:** none required.
- **Risks:** does not scale, and proves nothing about automated discovery.
- **Owner action:** OD-1, OD-2, OD-3, plus one real client.

### SECOND CHOICE — **open-source bounties, with per-repository AI-policy screening**

The first path worth *automating*, because completion is externally verified by
merge and payment has a real signal. Requires: verifying the platform's current
terms from primary sources; registering and proving a coding capability (none is
registered today); and a per-repository AI-policy screen with disclosure where
required.

### DEFER — agent-native marketplaces

Right shape, unverified substance. Revisit once one real job has completed.

### DO NOT USE — general freelance marketplaces for autonomous operation

Personal-performance requirements and explicit automation prohibitions.

---

## Standing rules

No anti-bot circumvention. No CAPTCHA bypass. No impersonation. No borrowed
credentials. No rate-limit evasion. No account creation without authorisation. No
proposal submitted to anyone until a source has a completed determination and the
owner has approved it.

A source reaches `PERMITTED` only via `Discovery.register_source`, which requires
a registered owner, the `PERMITTED_AUTOMATION` rung, and a recorded written
determination. None of the sources above has one.
