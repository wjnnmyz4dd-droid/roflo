# DeskPilot — Upwork bid economics

Date: 2026-10-18 · Branch `claude/solvent-architecture-audit-8ot9et`
· Module `solvent/upworkbid.py`

What a bid is worth, how the figure is arrived at, and — the part that
matters most on today's evidence — which figures DeskPilot cannot
obtain and therefore refuses to state.

---

## 1. The honest starting position

Five of the inputs the brief asks for cannot be read from an official
Upwork source from this container. Every Upwork documentation page
returns HTTP 403; see
`deskpilot-upwork-official-integration-review.md`.

| Input | Status today | Why |
|---|---|---|
| Competing proposal count | **UNAVAILABLE** | no verified route |
| Competing bid amounts | **UNAVAILABLE** | no verified route |
| Client hiring history | **UNAVAILABLE** | no verified route |
| Connects required / cost | **UNAVAILABLE** | no official per-Connect price |
| Freelancer service fee | **UNAVAILABLE** | `llms.txt` links client pricing only; the freelancer page is 403 |
| Market rate | **MODEL ESTIMATE** | from DeskPilot's own observed runs |
| Execution and checker cost | **HISTORICAL OBSERVATION** | measured runs of certified skills |
| Client budget | **VERIFIED PLATFORM DATA** when the posting states it | the posting's own number |

This is not a gap to be papered over with defaults. The brief says
plainly: *never present estimated competing bids as verified actual
bids*, *never fabricate a win probability*, *never assume every proposal
costs $2*, *never assume every contract has the same service fee*.

## 2. Provenance is part of the number

`Figure` cannot be constructed without a `Provenance`, and the
constructor refuses the two contradictions: an `UNAVAILABLE` figure
carrying a value, and a valued figure with no source. An `UNAVAILABLE`
figure must also say *why*, so the owner can tell "we could not get it"
from "we did not look".

```
UNAVAILABLE (no official source for competing bid amounts)
$3.00 [MODEL ESTIMATE] — modelled at 10%
0.25 [HISTORICAL OBSERVATION] — 3 won of 12 observed
```

Rendering never drops the label, so a figure cannot lose its provenance
on the way to the owner.

## 3. An unknown cost makes the answer unknown, not larger

The single most consequential rule here. If any modelled cost is
missing, `net_contribution` is `UNAVAILABLE` and says which costs are
missing — it does **not** sum the known ones and call that a total.
Treating an unknown cost as zero is precisely how an unprofitable job
gets recommended, and on today's evidence two of the five costs are
unknown, so this is the ordinary case rather than an edge case.

The same propagates: no contribution, no margin, no profit per Connect.

## 4. The bid

```
competition known   → undercut it (90% by default), then clamp
competition unknown → the client's stated budget, and say so
always              → never below the floor, never above the budget
```

The floor comes from `Finder.bid_floor_cents`, which is cost, the
owner's minimum fee and the owner's minimum margin, whichever binds
hardest. `recommended_bid` refuses outright to price anything without a
floor, because a price with no floor under it is how work gets taken at
a loss.

The brief's own example, $500 budget against roughly $300 of
competition: with a $150 floor the recommendation lands at or below
$200 and at or above the floor. There is a test asserting exactly that.

Bidding the client's budget by default is deliberately not the
behaviour. A posting budgeting $500 that everyone else is bidding $300
on is a posting where $500 does not win. But the fallback when nothing
is known *is* the budget, because inventing a number to undercut would
be a fabricated competitor.

## 5. The odds

`win_probability` returns a rate only from observed outcomes, and
refuses below ten:

| Observed outcomes | Result |
|---|---|
| < 10 | **UNAVAILABLE**, confidence INSUFFICIENT DATA |
| 10–29 | a rate, confidence LOW |
| 30–99 | a rate, confidence MEDIUM |
| ≥ 100 | a rate, confidence HIGH |

Confidence rises with the *sample*, never with the rate. A win rate is
`HISTORICAL_OBSERVATION` and can never be `VERIFIED_PLATFORM_DATA`;
there is a test for that, because "Upwork says we win 40%" would be a
sentence nobody could support.

At the outset there are no outcomes at all, so the ordinary answer for
a long while is no answer. A ranking that needs a win probability has
to cope with that rather than being handed a default.

## 6. Ranking

In the brief's priority, over what can actually be computed: expected
contribution, then profit per Connect, then the odds, then confidence.

**An opportunity whose contribution is unknown sorts last**, whatever
else is true about it — including below a priced opportunity that
*loses money*. Not because losing money is good, but because the owner
can see that it loses money, and nobody can see anything about the
other one. A ranking that floats unknowns to the top is a ranking that
spends Connects on them.

That rule survived a mutant only after the test was strengthened: the
original test passed with the rule deleted, because the sibling sort
keys already ordered the ordinary case. The loss-making-versus-
unpriceable case is where the rule and the other keys disagree, and it
is the one now bound to the mutant.

Expected profit per Connect is computed where both halves are known,
which today is never in production — the Connects requirement is
`UNAVAILABLE`.

## 7. The budget

`BidBudget` computes every figure §11 asks for from recorded events.
Nothing multiplies a count by a rate:

| Figure | Source |
|---|---|
| Connects purchased / consumed / remaining | summed `connects` on the respective events |
| Actual proposal cost | summed `proposal_cost` amounts, each as recorded |
| Proposals approved / submitted / rejected | counted events |
| Contracts won / lost | counted events |
| Revenue collected, platform fees, processing costs | summed events |
| Net contribution | revenue − fees − processing − proposal cost |
| Acquisition cost per won contract | all proposal cost over contracts won, **losses included** |

An event kind the module does not recognise is **refused**, not counted
as nothing: a mistyped kind would otherwise make a cost silently
disappear.

`affordable()` refuses a proposal whose cost is not recorded, rather
than letting a zero through. A planning figure of "$2 per proposal" is
usable for *planning* only; it is not a cost, and nothing in the code
treats it as one.

### Planning allocation, and what it is not

The brief's starting assumption is $150 a month, roughly $2 a proposal,
five skills, fifteen opportunities per skill. The code treats that as
an allocation, not a quota: `affordable()` answers only *is there
money*, and profitability answers *is it worth it* — separately, and
first. The brief's own instruction is not to spend merely to satisfy
quotas, and a budget that approves spending is a budget that creates
pressure to spend.

### Not yet persisted — an open item

`BidBudget` is a value object over a list of events. No table backs it.
Persisting it means adding a table to `solvent/store.py`, which is
under the capacity examination's seal, and resealing a sealed paper to
store figures for a channel that cannot yet be used is the wrong trade.
The right moment is when live operation is authorised. Recorded here as
the open item it is rather than left to be discovered.

## 8. What closes the gaps

| Gap | What closes it |
|---|---|
| Connects cost, service fee, competition, client history | one authenticated `tools/list` against `mcp.upwork.com`, then reading what it actually returns |
| Win probability | real outcomes — ten before any rate, a hundred before a confident one |
| Budget persistence | a table in `store.py` and a reseal of the capacity exam, when live operation is authorised |
| Whether any of this may be used commercially | Upwork's Legal Center, which returns 403 here |

## 9. What this does not say

* It does not say any figure here is an Upwork figure. One is — the
  client's stated budget — and it is labelled.
* It does not say DeskPilot would be profitable on Upwork. No contract,
  revenue or cost has passed through this.
* It does not say the planning assumptions are real prices.
* No real bid, Connect, customer message or submission occurred.
