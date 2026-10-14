# DeskPilot: zero history, marketplace access, and a probation path

Four kinds of statement appear below and they are never mixed.

| Label | Means |
|---|---|
| **OBSERVED** | A request was made and this is what came back, with the date |
| **DOCUMENTED** | The platform's own published statement of what it permits |
| **SIMULATED** | A deterministic run against real code and generated work |
| **ASSUMED** | Nobody has established it; it is stated so it can be argued with |

**A platform is not given a zero-job count because it could not be
read.** Guru refuses automated requests; that is recorded as *unknown
volume, refused access*, not as "no work". The only platforms with
measured volume are the two that published a way to measure them.

---

## A. Marketplace registry reconciliation

The discrepancy is real and the explanation is short: **the AI-agent
marketplaces were never in the registry.**

`docs/solvent-work-source-research.md` (15 September 2026) examined
them as "Finding 3 — Agent-native work marketplaces: promising,
**unverified**", naming OpenTask, Circle's agent services marketplace
and the x402 payment protocol. Its own assessment was that this is
"structurally the correct long-term home for Solvent", followed
immediately by: *"none of it was verified from primary sources, the
economics are unknown... **Recommended: DEFER pending owner
verification**."*

It was deferred, and deferral meant it never became a catalogue entry.

What the catalogue did contain was thirteen sources: the six
traditional freelance marketplaces, four government and grant
sources (`sam_gov`, `grants_gov`, `state_local_procurement`,
`rfp_board`), and three direct ones. Commit `4ccc381` on 13 October
cut it to nine. **The six freelance marketplaces were already there
and were not the new arrivals** — what that commit removed was the
government and grants category.

So there was never a switch from agent marketplaces to freelance
marketplaces. There was a researched category that was deferred and
stayed deferred, and a separate narrowing of a list it was not part
of. This audit is the first time an agent-native platform has been
measured, and section B reports what was found.

---

## B. Marketplace eligibility

### Can a brand-new account take part?

| Platform | New accounts | Evidence |
|---|---|---|
| **Freelancer** | Yes, with a cost question | **DOCUMENTED** 2026-10-14 |
| **OpenTask** | Yes, free, agents explicitly welcome | **OBSERVED** 2026-10-14 |
| **Upwork** | Unknown — help pages refuse machines | **OBSERVED** HTTP 403 |
| **Fiverr** | Unknown | not established |
| **PeoplePerHour** | Unknown | not established |
| **Guru** | Unknown — refuses machines | **OBSERVED** HTTP 403 |
| **Contra** | Yes for profiles, wrong direction | **DOCUMENTED** |

**Freelancer.** The membership page's lowest listed tier is **Basic at
$4.99/month for 50 bids per month** ($4.50 if billed annually). No free
tier is listed on that page. The support article on free-member bid
allowances returns *"the article you were trying to view is no longer
available."* So: **the cost of bidding as a new account is DOCUMENTED
at $4.99/month, and whether a free allowance exists is UNKNOWN** — the
owner can see it the moment they sign in, and this audit could not.

**OpenTask.** The front page states account creation, posting and
responding are all free, and that it is a place to "find people and AI
agents to build, research, design, and deliver". The platform fee is
**4.5%, charged to the task owner** rather than to the worker. Payment
requires a supported wallet. This is the only platform examined where
an AI agent is a first-class participant rather than something the
terms are silent about.

**Upwork.** `api.upwork.com/graphql` returns HTTP 401 without a
credential — real endpoint, registration required. Its help pages
return **HTTP 403 to an automated fetch**, so the Connects economics
could not be read and are **UNKNOWN**. They are widely reported; this
audit does not repeat a figure it could not verify.

---

## C. Actual job-feed observations

### Freelancer — measured

**OBSERVED.** Eleven samples of the public, unauthenticated,
documented endpoint `GET /api/projects/0.1/projects/active/`, four
minutes apart, **20:34Z to 21:16Z on 2026-10-14 — a 33.1-minute
window**. HTTP 200 every time, no credential. The platform returns its
own limits in a header (`200, 200;window=60, 1000;window=3600`); 11
requests were made against an hourly allowance of 1,000.

| Measure | Value |
|---|---|
| Open projects on the platform | **5,131 → 5,153** across the window |
| Distinct projects seen | **141** |
| New projects arriving | **74.3 per hour** |
| Matching the five skills under the screen | **21 of 141 (15%)** |
| New matching projects arriving | **12.7 per hour** (~305/day) |
| Fixed-price / hourly | 120 / 21 |
| Currency | USD 93, INR 27, CAD 7, GBP 7, EUR 3, AUD 3, NZD 1 |
| Budget floor, USD postings | median **$30**, p90 $250, max $80,000 |
| Budget floor, USD matching postings | median **$250**, p90 $400 |
| **Existing bids already on a posting** | median **31**, p90 127, max 308 |

Two of these matter more than the rest.

**There is suitable work.** About 13 postings an hour carry labels
overlapping the five skills, and the ones that do are *better paid*
than the feed as a whole — a $250 median floor against $30. The cheap
end of this marketplace is not where DeskPilot's work is.

**It is heavily contested.** The median posting already has **31
bids** on it. A brand-new account with no reviews is bidder number 32.
Nothing in this audit estimates a win rate, and no figure here should
be read as one, but the number is the single most important thing
about this feed for someone with zero history.

The arrival measure is sound rather than lucky: at 74 per hour and a
four-minute gap, roughly five new postings appear between samples
against a 100-posting window, so nothing is being missed between them.

### OpenTask — measured, and the measurement is the finding

**OBSERVED 2026-10-14.** Its `robots.txt` reads `Allow: /` with
`Disallow: /api/`, `/bids/`, `/contracts/`, `/dashboard` — and it
publishes a task sitemap, which is a platform offering its listings to
crawlers in the same breath as it closes its API.

The published task sitemap contains **24 task URLs in total**, with
last-modified dates from **2026-08-15 to 2026-10-11**. `tasks/1.xml`
returns 404, so that is the whole of it. The profiles sitemap contains
**552 URLs**.

**Twenty-three profiles for every task the platform has ever listed,
and the newest task is three days old.** Structurally this is the
right kind of venue and it has almost no demand in it. That is the
difference between ranking on evidence and ranking on the article
title "12 platforms where AI agents actually earn money".

### The four that were not measured

Fiverr, PeoplePerHour, Guru and Contra have **no measured volume**,
and that is a statement about access, not about supply. Guru returned
HTTP 403 to automated requests on two separate days from two
different user agents; the correct response to a 403 is to stop, and
it was. **None of these is assigned a job count.**

---

## D. Probation simulation

`tools/probation_dryrun.py` puts the **141 real observed postings**
through the **production** adapter, Finder and probation logic.
Nothing was submitted; the run asserts it, and would raise if a
submittable bid appeared.

The useful result is not a number but how the number's *reason*
changes as each gap closes:

| Configuration | Recommended | Why the rest were rejected |
|---|---|---|
| As shipped (no probation) | **0** | 120 no skill mapped · 21 capability not on probation |
| Probation granted | **0** | 120 no skill mapped · **21 blocked on missing cited market rates** |
| Probation + ASSUMED rates, $150 ceiling | **0** | 120 unmapped · 11 above ceiling · 5 below floor · 5 no price |
| Probation + ASSUMED rates, $300 ceiling | **9** | 120 unmapped · 5 below floor · 5 no price · 2 above ceiling |

Four things worth reading off that.

**The largest single rejection reason is not a safeguard at all.** 120
of 141 are rejected because no DeskPilot skill is mapped to their
platform labels, and 120 of those genuinely are graphic design, PHP,
video editing and 3D modelling. That number is the market, not a bug.

**Probation is not the binding constraint.** Granting it moved the
blocker from "not on probation" to "no cited market rate" — a
commercial configuration gap the owner closes in minutes, and which
was invisible until probation was granted because it sat behind it.

**The probation value ceiling has to match the market.** At $150
nothing is recommended, because candidate postings have a $250 median.
At $300, nine are. The ceiling is a real decision with a measurable
answer, not a number to pick for comfort.

**The modelled margin is 87%, and that figure should be distrusted.**
All nine recommendations price at $250 against a $46.50 floor. The
floor is low because the execution timing behind it is a **benchmark**
— milliseconds on a file DeskPilot generated for itself. A real
client's file could take hours and the margin would collapse. That is
precisely the risk probation exists to bound, and precisely why the
value ceiling and the delivery window are not optional.

---

## E. Proposed probation architecture

### The deadlock

`Finder.bid` refuses any capability with no measured client runs.
Client runs only come from doing client work. Without a way out, every
skill stays blocked for ever and there is no first customer. This is
not a bug to be removed: the rule is what stops DeskPilot promising a
delivery time it has no basis for.

### The way out, and what it costs

Probation lets one capability be **quoted** before it has history,
under bounds the owner sets per capability. It is implemented in
`Finder.probation` and `Policy.grant_probation`, and the central
restraint is this:

> **A probationary bid is a recommendation and is never submittable
> by DeskPilot.** It is priced, bounded, explained, and handed to the
> owner to send themselves.

Escaping the deadlock would not be worth it if the way out were
letting DeskPilot bid unsupervised on work it has never done.

Every condition must hold, and none defaults to yes:

- the owner has named **this capability**, with its own bounds
- it has not graduated — real client runs end probation automatically
- it has not used up the jobs the owner allowed
- a **value ceiling** is set, so a wrong estimate costs little
- a **minimum delivery window** is set, because how long the work
  takes is exactly what nobody knows

What probation does **not** relax: the capacity gate, the three-job
default and four-job ceiling, the pricing floors including the
$40/hour minimum, the verification tier, platform permission, and
owner approval. Seventeen adversarial tests pin each of those.

The estimate under probation comes from benchmark timings and is
labelled `PROVISIONAL`, with a basis that says in words that the input
was a file DeskPilot generated for itself. Benchmarks remain excluded
from the ordinary pricing history.

### A defect found while testing it

**Probation could not be revoked.** Policy patches deep-merge, so
amending `{"probation": {"capabilities": {}}}` — exactly what an owner
would write to clear a grant — merges an empty map into the existing
one and changes nothing. The owner would read "policy amended" and the
capability would still be on probation.

`Policy.revoke_probation` now zeroes the grant and marks it revoked
rather than deleting it, so the decision stays in the audit record,
and a test pins the silent no-op so the supported path stays the one
that works.

---

## F. Bidder readiness

**Bid recommendation and authorised automated submission are
different things, and only one of them exists.**

| | State |
|---|---|
| Compose a priced bid from measured history | **implemented** |
| Compose a priced bid under probation | **implemented**, recommendation only |
| Enforce the $40/hour floor on occupied hours | **implemented** |
| Rank competing offers by profit per second | **implemented** |
| Submit a bid to any marketplace | **does not exist** |

`Applications.submit` takes an injected transport. **No platform
transport has been written**, so every submission resolves to
`MANUAL_SUBMISSION_REQUIRED` — on Freelancer, on OpenTask, on
everything. Even a fully permitted, fully authorised bid is prepared
for the owner to send by hand.

This is not a gap probation creates, and probation does not narrow it:
a probationary bid is unsubmittable twice over, once because it is
probationary and once because there is nothing to submit it through.

---

## G. First-customer recommendation

### The ranking, on the ten criteria

Scored only on evidence gathered above. **M** = measured this pass,
**D** = documented by the platform, **U** = unknown.

| | Freelancer | OpenTask | Upwork | PeoplePerHour | Fiverr | Guru | Contra |
|---|---|---|---|---|---|---|---|
| 1. Legitimate access | **API open (M)** | listings allowed, API disallowed (D) | 401, registration (M) | pages only (D) | none found | **refuses machines (M)** | wrong direction (D) |
| 2. New-account eligibility | yes (D) | **yes, free (D)** | U | U | U | U | yes, wrong direction |
| 3. Suitable work available | **12.7/hr (M)** | **24 ever (M)** | U | U | U | U (refused) | none of this kind |
| 4. Bidding permission | UNDETERMINED | UNDETERMINED | UNDETERMINED | UNDETERMINED | U | U | U |
| 5. Automation permission | UNDETERMINED | UNDETERMINED | UNDETERMINED | UNDETERMINED | U | U | U |
| 6. Registration cost | **$4.99/mo (D)** | **free (D)** | U | U | U | U | free |
| 7. Platform fees | **10% or $5 min (D)** | **4.5% on the poster (D)** | U | U | U | U | — |
| 8. Payment feasibility | $50 min withdrawal (D) | wallet required (D) | U | U | U | U | — |
| 9. Skill compatibility | **15% of feed (M)** | unmeasured | U | U | U | U | none |
| 10. Integration effort | **adapter written** | none written | none written | none | none | none | none |

**No platform supports autonomous acquisition.** Bidding permission
and automation permission are UNDETERMINED on every one of them, and
no submission transport exists anywhere. That answer does not change
with the ranking; the ranking only decides where the owner should
spend their attention.

1. **Freelancer** — the only platform with both a verified machine
   route and measured suitable volume. Its drawbacks are documented
   rather than unknown: a $4.99/month bid allowance, a 10%-or-$5 fee,
   a $50 withdrawal minimum, and a median of 31 competing bids.
2. **OpenTask** — structurally the best fit and currently almost
   empty. Free, agents welcome, the fee falls on the poster. Worth the
   owner creating a free profile and watching; not worth engineering
   against 24 tasks.
3. **Upwork** — a real API behind a real registration, and everything
   about the economics unreadable by machine.
4. **PeoplePerHour** — listing pages readable, search disallowed, no
   API found.
5. **Fiverr** — seller-led, which is a different acquisition shape.
6. **Contra** — its API points the wrong way.
7. **Guru** — refuses machines, twice, on different days.

### Against owner-entered / manual import

| | Best marketplace route (Freelancer) | Manual import |
|---|---|---|
| Blocked by a platform | yes — terms unread | **no** |
| Blocked by missing code | yes — no transport | **no** |
| Needs a paid membership | $4.99/month | **no** |
| Platform fee | 10% or $5 | **none** |
| Competing bids | median 31 | **none** |
| Produces real client history | yes | **yes — identically** |
| Available today | no | **yes** |

**Manual import wins, and it is not close.** It is the only
acquisition path blocked by nothing external, it pays no platform fee,
it competes with nobody, and the execution measurement it produces is
exactly the same measurement a Freelancer job would produce. One
imported job graduates a skill out of probation just as well as one
won against thirty-one other bidders.

The marketplace question is worth answering — it is how this becomes a
business rather than a favour to people the owner knows — but it is
not the shortest route to the first real performance record. It is the
second step, and Freelancer is where that step goes.

---

## H. Remaining blockers

### Missing code
1. **No bid-submission transport for any platform.** Every submission
   is `MANUAL_SUBMISSION_REQUIRED`.
2. **No Upwork adapter.** Nothing to call their GraphQL API with.
3. **No OpenTask adapter.** Not worth writing at 24 tasks.

### Missing credentials
4. **No Upwork application key or secret.** Needs registration.
5. **No Stripe key or webhook secret.**
6. **No OpenTask wallet**, if that route is ever taken.

### Owner configuration
7. **Capacity budget unset** — `max_owed_seconds` is 0, so the gate
   refuses everything. `solvent setup capacity`.
8. **No cited market rates** — this is what blocked all 21 candidate
   postings in section D once probation was granted.
   `solvent setup commercial --market-rate`.
9. **No contracting identity** — no invoice can be issued.
10. **No probation grants** — `solvent setup probation`. The dry run
    suggests a ceiling near **$300**; at $150 nothing on this market
    is reachable.
11. **No platform-label mapping** — 120 of 141 postings arrive asking
    for nothing because no mapping exists. The screen used in section
    D is the auditor's, not a decision.

### Platform restrictions
12. **Freelancer API terms unread.** The single document between here
    and automated discovery.
13. **Bidding and automation UNDETERMINED everywhere.**
14. **Guru refuses machines**; Fiverr and PeoplePerHour publish no API.

### Missing real-world evidence
15. **No client job has ever been done** — so no skill has history and
    all five are blocked but for probation.
16. **No bid has been submitted**, so the win rate is unknown and
    every revenue figure remains conditional on it.
17. **No payment received**, through any rail.
18. **No evidence of what a real client file costs to process.** Every
    timing is a benchmark on generated input, and the 87% modelled
    margin in section D rests entirely on that.

---

## I. Test results

| Measure | Result |
|---|---|
| **Tests passed** | **4,406** |
| **Tests failed** | **0** |
| **Tests skipped** | **1** |
| Total | 4,407 — 4,313 in `tests_solvent`, 94 in `tests` |
| **Mutation, this sweep's first runs** | 9 probation mutants: **7 killed, 2 survived** |
| **Mutation, final** | **115/115 killed, 0 survived, 0 not applied** |
| **Unseen exam** | 6 questions, **6 passed** — fourth sitting |

### Mutation, honestly

Both probation survivors were **test defects, not false alarms**, and
both had the same shape — a test that did not route through the code
path it was named for:

- `zero-history-work-is-quoted-without-a-grant` survived because the
  test asked `probation()` directly. That proves the grant refuses; it
  does not prove the bidder consults it.
- `probation-ignores-its-value-ceiling` survived because the test
  checked only that probationary bids are unsubmittable — true whether
  or not the ceiling applies.

Both now bid through `Finder.bid` and both mutants die. That is the
ninth and tenth time this pattern has appeared across these sweeps: a
check whose success does not depend on the thing it is checking.

Two further anchors were found stale or ambiguous and re-anchored.
`NOT_APPLIED` is not a kill.

### Unseen exam

Sat a fourth time, because the seal broke a third time: `finder.py` is
one of the four modules it covers and probation changed it. Re-frozen
over the finished code, fresh paper from the new seed, six of six.
Probation changes what may be *quoted* and nothing about what may be
*owed*, so the capacity behaviour those questions examine should be
unchanged — and it is, which is a result rather than an assumption.
All four sittings are kept.

### Defects found and fixed this sweep

1. **Probation could not be revoked** (section E). A silent no-op on
   the one control that lets DeskPilot quote unmeasured work.
2. **The platform fee had no floor.** Freelancer publishes "10% or
   $5.00 USD, whichever is greater". DeskPilot modelled fees as a
   fraction only, costing a $40 job at $4 against a real $5 —
   understating cost, overstating margin, in the direction that
   accepts work it should refuse.

### Unresolved

None known in this sweep's work. Two standing limitations:

- The AST scanner used by structural tests matches identifiers and
  whole string literals exactly; it would not catch a forbidden name
  inside a longer string.
- Every performance figure is a benchmark on generated input, on this
  Linux container, not the Windows VPS.

### Advisory reviews

**K-LEAN** (`calinfaja/k-lean@79f3557`, archived upstream). Its broker
path needs credentials this session does not have. Its local secret
scanner ran over the changed tree: `solvent/` 76 files and `tools/` 6
files, **no potential secrets found**.

**iFixAi** (`ifixai-ai/ifixai@d86ba61`). Its inspections are LLM
prompt probes needing a model and fixtures; probation is deterministic
policy and calls no model, so most do not apply. Two shapes did, and
both earned their place:

- **B16, silent failure** — the lens that found the revocation no-op.
  Second sweep running that one, second real defect from it.
- **B08, privilege escalation** — adapted into a test where a posting
  claims in its own body that probation is waived and asks for an
  automatic bid. It changes nothing: the grant is read from policy and
  a posting's prose is not an input to it.

Neither reviewer altered any authority, grading or control.

---

## The direct answer

### Which marketplace should DeskPilot attempt first?

**Freelancer** — but not before the owner has done one job by hand.

It is the only platform with a verified machine route and measured
suitable work: about 13 matching postings an hour, with a $250 median
budget floor against a $30 feed-wide median. Everything that makes it
hard is documented rather than unknown: $4.99/month to bid, 10% or $5
per job, $50 before you can withdraw, and a median of 31 bids already
on each posting.

**OpenTask** is the better-designed venue and currently has 24 tasks
in its entire published history against 552 profiles. Create a free
profile; do not build against it yet.

### What can DeskPilot actually do today?

Everything except find the work and send the bid.

It can qualify an opportunity, price it against the owner's floors
with the platform's real fee structure, enforce a $40/hour minimum on
the hours a job occupies, refuse to promise a deadline it cannot
support, rank competing offers by profit per second, admit at most
three jobs at once, execute all five skills, verify them
independently, deliver, invoice, and reconcile a payment — all
demonstrated end to end in simulation.

It cannot legitimately poll any marketplace, and it cannot submit a
bid to any of them.

### What exactly prevents the first real bid?

Four things, in order:

1. **One unread document.** Freelancer's API terms of service. Until
   the owner reads them and records a determination, DeskPilot may not
   poll the one feed it could otherwise read.
2. **No submission transport.** Even a permitted, priced, authorised
   bid resolves to `MANUAL_SUBMISSION_REQUIRED`. This is missing code,
   not a missing permission.
3. **No cited market rate.** The dry run's sharpest finding: with
   probation granted, every one of the 21 candidate postings was
   refused for this and nothing else.
4. **No client history, which probation now bounds rather than
   removes.** Even fully granted, probation produces a recommendation
   the owner must send themselves.

None of those blocks the first *job*. Manual import is blocked by
nothing: the owner brings one job, DeskPilot does all of it, and the
measurement that produces is the real history that graduates a skill
out of probation. That is the shortest legitimate route to a first
performance record, and it does not run through a marketplace at all.
