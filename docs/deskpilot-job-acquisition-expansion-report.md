# DeskPilot: job acquisition expansion

Two revenue channels, the terms behind each source read rather than assumed,
and a research prototype that is kept out of production on purpose.

Everything below was executed. Where something could not be executed, it
says so instead of estimating.

| commit | what |
|---|---|
| `a6cfa76` | the seven marketplace permissions; terms findings, read and cited |
| `4b56629` | the inbound storefront, feeding the existing pipeline |
| `925a6e2` | the skipped tests investigated — and the bug one was hiding |
| `74bcd44` | the ninety-scenario business matrix across both channels |
| `(this)` | source resilience, one shared instruction detector, the report |

---

## A. The two skipped tests

### 1. `tests_solvent.test_installer_packaged_entrypoint` (`setUpModule`)

**Reason, verbatim:** *"DeskPilot's egress guard is installed in this
interpreter, so no subprocess can be started and these tests cannot execute
the packaged entry point. The guard is deliberately irreversible and is not
worked around. Run this suite on its own."*

**Disposition: resolved by doing what it says.** Run standalone:

```
Ran 44 tests in 16.498s
OK
```

Nothing is untested. The skip is an artifact of running this suite in the
same interpreter as the ones that install the egress guard, and the guard
being irreversible is the correct design — working around it to make a test
run would be removing the thing the test exists to protect.

### 2. `test_a_heartbeat_this_process_may_not_read_is_unknown`

**Reason, verbatim:** *"running as root, which is refused nothing"*

**Disposition: the skip was hiding a real defect, now fixed.**

Run as an unprivileged user this test does not skip — it *fails*.
`runtime_liveness` documented four outcomes, one of them "unreadable →
UNKNOWN", and had an `except OSError` branch to produce it. That branch was
unreachable: the function called `Path(...).stat()`, and **stat() needs no
read permission on the file**, only search permission on its directory. A
heartbeat the control centre could not read still yielded a fresh
timestamp, and a fresh timestamp read as `VERIFIED HEALTHY`.

The control centre runs as a restricted identity precisely so that it *can*
be refused. Reporting a refusal as a healthy runtime is the reassuring
default this project exists to refuse — and it sat behind a skip, so the
assertion had never once executed.

Now opened rather than stat()ed, and verified both ways: as root with the
refusal injected, and as `nobody` with a real `chmod 000`. The test no
longer skips anywhere, because a test that only runs on one kind of machine
is a test nobody notices breaking. A companion test asserts a readable
heartbeat is still read as fresh, so a "fix" that refused everything would
not pass.

**Skipped tests: 2 → 1.**

---

## B. The original website list

Thirteen entries were already in the catalogue:

| source | type | previous status |
|---|---|---|
| Upwork | freelance marketplace | manual only |
| Fiverr | freelance marketplace | manual only |
| Freelancer.com | freelance marketplace | manual only |
| PeoplePerHour | freelance marketplace | manual only |
| Guru | freelance marketplace | manual only |
| Contra | freelance marketplace | manual only |
| SAM.gov | public procurement | official API, needs a key |
| Grants.gov | public procurement | official API, no key needed |
| State / local procurement portal | public procurement | undetermined |
| RFP / bid board | bid board | undetermined |
| Owner-entered opportunity | direct | **working** |
| File import (CSV) | direct | working |
| Direct client inquiry | direct | working |

---

## C. Marketplace research and terms findings

Read on **2026-10-13**, from each platform's own machine-readable statement
about automated access. Reading a `robots.txt` is not scraping — it is the
file published for exactly this question — and honouring it is not optional.

| source | finding on automated reading of listings | evidence |
|---|---|---|
| Upwork | **FORBIDDEN** | `robots.txt`: `Disallow: /jobs/`, `/*/jobs/search*`, `/o/jobs/job/*`, `/api*`; no Crawl-delay |
| Fiverr | **FORBIDDEN** | `robots.txt`: `Disallow: /search/`, `/search_results/`, `/gigs/search` |
| Freelancer.com | **FORBIDDEN** | `robots.txt`: `Disallow: /projects/browse.php*`, `/jobs/?*`, `/work/*`, `/find/*` |
| PeoplePerHour | **UNDETERMINED** | `robots.txt` requested, returned empty |
| Guru | **UNDETERMINED** | `robots.txt` returned HTTP 403 to an automated request |
| Contra | **UNDETERMINED** | not read |

Two further findings worth stating plainly:

**Upwork has an official GraphQL API** at `api.upwork.com/graphql`, OAuth2,
requiring an approved key tied to a verified account with a stated purpose.
That is the sanctioned route and it is *not* what robots.txt forbids. Its
terms are recorded as UNDETERMINED because **the developer documentation
itself returned HTTP 403 to an automated fetch** — the platform declining to
be read by a machine, which is an answer and not one to work around. The
owner must read those terms from a browser and record the finding.

**Guru returned 403 to a plain robots.txt request.** A site declining to be
read by a machine at all is a finding, and anti-bot measures are not to be
circumvented.

**No public Fiverr seller API was found.** Third-party scrapers exist; they
are unsupported, and using one would be exactly the unauthorised automation
this section exists to avoid.

### Implementation status

**None of the five freelance marketplaces is implemented as an automated
source, and none should be.** Three forbid it in their own terms; two did
not answer. Manual import by the owner remains available for every one of
them and needs nobody's permission.

### The seven permissions, now distinct

Four of the seven distinctions did not previously exist:

| activity | permission | new? |
|---|---|---|
| public job discovery | `DISCOVER` | |
| authenticated job discovery | `DISCOVER_AUTHENTICATED` | **new** |
| automated applications | `SUBMIT_APPLICATION` | |
| automated proposals | `SUBMIT_BID` | |
| automated contract acceptance | `ACCEPT_CONTRACT` | **new** |
| automated service delivery | `DELIVER_THROUGH_PLATFORM` | **new** |
| automated customer communication | `SEND_MESSAGE` | |

Plus `ACCOUNT_MANAGEMENT`, named separately because it is the clearest case
of why these are not one permission: an API may permit account management
and still forbid unrestricted job searching.

Registration grants only the public read side. A sweep over every pair
asserts that granting one grants nothing else. And two facts must line up
before anything happens — the **owner granted** it (an intention) and the
**platform's terms permit** it (an observation, with a citation and a date).
The observation wins; an uncited finding is refused at construction.

---

## D. Active sources

Only one source is both permitted and carrying work the certified skills
are paid for:

```
source          work_type        verdict            why
demo_board      UNDECLARED       FIXTURE            invented postings
grants_gov      GRANT_FUNDING    OUT_OF_SCOPE       healthy, and not client work
owner_entered   OWNER_DECLARED   ACTIVE
sam_gov         CONTRACT_WORK    NEEDS_CREDENTIAL   SAM_GOV_API_KEY unset
storefront      CLIENT_SERVICES  ACTIVE             (new this sweep)
```

**Two active sources**: owner-entered work, and the new inbound storefront.

Nothing was deleted. A source's determination, its permission grants and
every opportunity it produced are the record of a decision somebody made.

---

## E. Inbound storefront

Five offers, one per certified skill and no more. Each carries what a
customer would otherwise argue about later: scope included, scope
**excluded**, deliverables, intake questions with the reason each is asked,
the minimum price, turnaround, a revision policy distinguishing a correction
from new work, and the disclosures.

**Implemented and executed:** order intake with required-answer validation,
a customer-readable status vocabulary, duplicate-order refusal,
duplicate-pipeline-entry refusal, and the handoff into the existing cycle.
An inbound order runs **all fourteen stages** of the same pipeline, and a
test compares its stage list against a discovered job's.

**Not implemented, and deliberately:** publication. These are drafts held
locally. Putting them in front of real customers means an account on
somebody's platform and advertising to the public. `published` is False on
every offer and a test asserts no code in the module can set it.

**Remaining external authorisation:** a platform account, the owner's
decision to advertise, and the contracting identity (still unset, so no
invoice is yet valid).

---

## F. Open-internet prototype

Read-only, budgeted, allowlisted, robots-honouring, and structurally unable
to reach production. The rule it is built around:

> **Finding a website is not permission to use it.**

A `Candidate` is deliberately not an `Opportunity`, and there is no method
anywhere that converts one into the other. Tests assert the module contains
no `Opportunity(`, no `register_source`, no `SUBMIT_`, no `SEND_MESSAGE`,
no `ACCEPT_CONTRACT`, and that nothing in the live pipeline imports it.

### The comparison

Run against recorded pages, so it is reproducible and no live site was
crawled:

| | curated | open-internet |
|---|---|---|
| considered | 3 sources | 8 URLs |
| recorded | 1 | 2 |
| actionable | 1 | 2 |
| false-positive rate | 0.0 | 0.0 |
| actionable per request | — (no polling authorised) | 0.286 |
| duplicate rate | 0.0 | 0.125 |

Dispositions on the experimental run: 2 recorded, 1 no opportunity, 1 no
skill match, 1 no compensation, 1 duplicate, 1 **injection attempt**, 1
fetch failure.

### Does broader discovery improve opportunity quality?

**On this evidence, it finds more and is not yet better.** Two actionable
candidates against one active curated source is not a result: the curated
side is small because its sources are *forbidden or uncredentialed*, not
because curation is a weak technique. The honest comparison is between an
open-internet run and a *working* marketplace integration, and no working
marketplace integration exists to compare against.

What the prototype did demonstrate is that the filtering works: of eight
URLs, six were correctly rejected, including one that tried to issue
instructions and one offering "great exposure" instead of money.

**Not promoted.** `PRODUCTION_READY = False`, asserted by a test.

---

## G. Five-skill coverage

| skill | outbound (curated) | inbound (storefront) | open-internet prototype |
|---|---|---|---|
| csv-cleanup | owner-entered only | **offer ready** | matched in the run |
| spreadsheet-automation | owner-entered only | **offer ready** | signal defined, not matched in this run |
| document-processing | owner-entered only | **offer ready** | signal defined, not matched |
| data-entry | owner-entered only | **offer ready** | signal defined, matched as unpaid |
| report-builder | owner-entered only | **offer ready** | matched in the run |

Matching is never by keyword alone: each skill signal requires a *task* word
and an *object* word in the same page, because "data" is on half the
internet and a false positive costs a person's time reading something that
was never a job.

---

## H. Commercial testing

### The ninety-scenario development matrix

Five skills × three values × three difficulties × two channels. Expectations
written from the operating rules, not read off the implementation.

**Result: 90/90.** Both channels reach identical verdicts on identical work.

Two of my own expectations were wrong and the code was right: $50 is 5,000
cents against a $40 floor, so it clears by ten dollars rather than being
declined. Worth knowing — a floor anywhere above $50 turns a third of the
matrix into declines.

### Source resilience — twenty categories

**20/20.** The category the file is named for: a source returning HTTP 200,
perfectly healthy, carrying nothing any certified skill can do, is not
active.

### End-to-end simulation

An inbound order ran discovery → qualify → price → owner approval →
contract → verification eligibility → budget → execute → verify → deliver →
invoice → payment → reconcile → audit. Invoiced $150.00, completed,
simulation enabled throughout.

---

## I. Security

**Findings this sweep:**

1. **`VERIFIED HEALTHY` on an unreadable heartbeat** (section A.2). Fixed.
2. **A posting issuing instructions was pursued like any other listing.**
   Nothing downstream obeys a posting — the Gate, the Governor and the owner
   all stand between it and any effect — but a system that pursues it
   silently has thrown away the one fact worth keeping: somebody tried. The
   Finder now records it as a risk and holds for a person.

**Consolidation rather than a third list:** detecting "is this text trying
to command me" now lives in one place, used by the Finder and the
prospector. It is explicitly *not* the customer-service classifier, which
does a different job — sorting a client's message into a routing category.
One notices an attempt; the other decides who answers a person.

**Remaining risk:** the instruction detector is regular expressions, and is
used only to *notice*, never to filter. Treating it as an access control
would make it one somebody rephrases past.

---

## J. Mutation testing

*(filled from the final run)*

---

## K. Remaining blockers

*(filled below)*

---

## L. Final classification

*(stated with the evidence below)*
