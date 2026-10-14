# DeskPilot: bidding on measured speed, and getting paid

One sweep: verify the six marketplaces' real APIs, put a
completion-speed-aware bidder inside the existing Job Finder, add
cryptocurrency receipt support with a blank wallet field, model optional
conversion, report Stripe's state, and record the owner's commercial
numbers. No live bid, no live contract, no money moved, no wallet
created, simulation left on throughout.

---

## A. Real API verification

Every line below is a request made on 2026-10-14 and the response that
came back. Nothing here is from documentation alone.

| platform | probe | result |
|---|---|---|
| **Upwork** | `POST api.upwork.com/graphql` | **401** `{"message":"Authentication failed"}` — the endpoint is real and closed |
| | developer docs page | 403 to an automated fetch |
| **Fiverr** | `developers.fiverr.com` | connection failed; no developer site resolves |
| **Freelancer** | `GET /api/projects/0.1/projects/active/?limit=1` | **200, live project JSON, no credential** |
| **PeoplePerHour** | `robots.txt` | 200 (it was empty on 2026-10-13 — that was a transport failure, not an absent file) |
| **Guru** | `robots.txt` and `/api` | **403 to both**, from a different user agent than yesterday's |
| **Contra** | `api.contra.com/public-api/opportunities` | 404; `contra.com/public-api/` returns 401 |

### The finding that matters, and a correction to my own record

**Freelancer.com serves live job data over an unauthenticated API**, and
tells the caller its own limits in the response headers:

```
ratelimit-limit: 200, 200;window=60, 1000;window=3600
```

A platform publishing its rate limits to an anonymous caller is
describing machine access, not refusing it.

Yesterday I recorded Freelancer's public discovery as **FORBIDDEN**,
citing robots.txt. I re-read the file today and **the citation was
accurate** — `/projects/browse.php*`, `/jobs/?*`, `/work/*` and
`/find/*` really are disallowed, and still are. **The conclusion drawn
from it was too broad.** Those are web *page* paths. robots.txt does not
mention `/api/` at all, and the API is a different door.

The finding is now UNDETERMINED with a citation recording both facts and
naming what is still unread: Freelancer's API terms of service, which is
a separate document from robots.txt. Operationally nothing changes —
unread is not permitted, and both answers refuse — but the record now
says the true thing and points the owner at the one document to read.

Worth noting the contrast this exposes: **Upwork's robots.txt
*does* disallow `/api*`** and offers the GraphQL endpoint under
credentials instead. Two platforms, two opposite postures, and a single
"is scraping allowed?" question would have missed both.

### Upwork's published access conditions

Real and specific: a client ID and shared secret, an account under a
company with a verified ID, a stated description of intended use, and a
documented cap of 40,000 requests a day. Marketplace job search exists
in the schema. None of it is reachable without the owner registering an
application under their own identity.

---

## B. Bidder implementation

### What was implemented

`Finder.completion_estimate()` and `Finder.bid()`, plus two dataclasses
(`CompletionEstimate`, `Bid`) and `Finder.bid_floor_cents()`. A bid is
composed; it is never sent.

### Whether a separate agent was avoided

**Yes, and structurally enforced.** A test walks every class in
`solvent/` and fails on any name matching `Bidder` or `BidAgent`. A
second test asserts the Finder contains no `transport`, no HTTP client,
no `gate.request` and no `submit`.

The reason is not tidiness. Bidding needs three things that already have
owners — measured execution history for the time, `quoting` for cost and
margin, the owner's recorded terms for the floor. A bidding agent would
need its own copy of all three and would disagree with them the first
time one changed.

### Completion-time estimation method

The **median of measured wall-clock runs** for that capability, from
`execution_measurements` — work DeskPilot has actually performed.

- Median, not mean: one pathological run is not the new promise. Tested
  with an outlier of 9,000s among four runs near 12s; the estimate stays
  at 12s.
- **No history means no estimate and no bid.** There is no fallback
  constant. An unmeasured capability is not a fast one.
- Confidence is reported, not smoothed: ≥5 runs is `MEASURED`, fewer is
  `PROVISIONAL`. A client told "about four minutes" deserves to know
  whether that rests on one run or fifty.

### Bid-pricing method

1. Build an `ExecutionMeasurement` from the estimate.
2. Cost it through the existing `quoting.operating_cost` at the owner's
   recorded rates — compute, platform fee, payment fee.
3. Floor = the highest of: the owner's minimum project fee; the price
   needed to clear the owner's minimum margin (`cost / (1 − margin)`, in
   exact `Fraction` arithmetic); and the cost itself, which no price may
   sit below whatever the margin rule produces.
4. The client's own budget is the ceiling — bidding above what they
   offered wins nothing.
5. Below the floor, **refuse**. Between floor and budget, bid the
   client's number: speed is already the advantage, and discounting on
   top buys nothing the owner asked for.

Worked example at the owner's terms, with real marketplace fees:

```
bid $600.00, delivery about 0.01h, margin 87%
  compute 22.000 CPU-seconds at $0.14/CPU-hour = $0.00
  platform fee 10.0% of $600.00 = $60.00
  payment fee 2.9% + $0.30 = $17.70
  floor: $77.70 of cost at the owner's 30% minimum margin → $111.00
  completion: median of 5 measured runs: 22.0s (MEASURED)
```

One documented conservatism: fees scale with price, so the cost used to
derive the floor is computed at the client's budget. That overstates the
floor slightly and never understates it.

### Authorization controls

The Bidder holds none. It reads and proposes. Everything consequential
stays where it was: `SUBMIT_BID` permission on the source, the owner's
signed approval, the Action Gate, and the seven submission gates in
`Applications.submit`. A `Bid` is an opinion about a number until all of
those agree.

It also refuses to compose one at all while the business is
unconfigured, the capability is uncertified or undeployable, the posting
names no price, or the capability has never been run.

### Test results

**23 tests, all passing.** Including: an unmeasured capability yields no
bid; a budget under the floor is refused; the floor is never below cost
even at a 1% margin and a 1-cent fee; a 95% platform cut makes every
price impossible and is refused; one run is `PROVISIONAL`, not
`MEASURED`; and the structural tests that no bidding agent exists and
the Finder cannot transmit.

---

## C. Cryptocurrency support

### Wallet placeholder

`CryptoReceipt` ships with `network=""`, `asset=""`, `address=""`. Blank
is the shipped state and a working one: everything refuses while it is
empty. A test asserts every string field defaults to `""`, so no
example, default or placeholder can be mistaken for a destination.

`solvent setup crypto --show` prints `(not set)` for each field.

### Supported networks

`ethereum`, `base`, `polygon`, `bitcoin`. Deliberately short: each is a
network whose address format this code can actually **check**. An
address it cannot check is one it must not accept.

### Supported assets

ETH/USDC/USDT (ethereum), ETH/USDC (base), POL/USDC/USDT (polygon), BTC
(bitcoin). Which the owner can actually receive depends on their own
wallet, which is theirs to know.

### Verified off-ramp options

**None verified, and none claimed.** Converting crypto to dollars means
sending an asset to an exchange or processor and receiving fiat — custody
and a transfer, two things this system holds no key for. No off-ramp
provider was contacted, tested or integrated, and naming one here would
be inventing a capability.

### Auto-conversion feasibility

Modelled as a *decision*, not an action. `ConversionPolicy` has three
states: `OFF` (shipped), `ADVISORY`, `AUTHORISED`. Even `AUTHORISED`
does not mean DeskPilot converts — it means the owner has a provider and
DeskPilot may record a conversion that provider performed. Authorising
without naming the provider is refused, because the refusal message is
the honest one: *Solvent does not do this.*

**Feasibility verdict: not feasible inside DeskPilot, and should not
be.** Auto-conversion requires custody. The architecture's value is that
it has none.

### Security controls

- **No key, ever.** An identifier scan over `payments.py` fails on
  `private_key`, `seed_phrase`, `mnemonic`, `sign`, `broadcast`,
  `send_transaction` and six more, and on importing `requests`,
  `socket`, `web3`, `urllib` or `subprocess`.
- **Real checksum validation.** BIP-173/350 bech32 is implemented, not
  approximated — a single mistyped character is caught. A length check
  would have accepted it and the money would be gone.
- **Unverifiable means refused.** A mixed-case EIP-55 address needs
  Keccak-256, which is not Python's `sha3_256` and not in the standard
  library. Rather than accept it unchecked, DeskPilot refuses and asks
  for the lower-case form. Refusing a good address costs a retype;
  accepting a bad one costs the payment.
- **Legacy base58 Bitcoin addresses are refused**, for the same reason.
- **Confirmations are mandatory** and per network (BTC 3, ETH 12, Base
  30, Polygon 60). Zero is never a configured receipt: an unconfirmed
  transaction can be replaced.
- **A payment to another address is not revenue**, however real and
  however correctly signed.
- **The watcher must be authenticated.** Confirmations arrive through
  the same spool Stripe uses, HMAC-signed with a shared secret; no
  secret means nothing verifies.
- **Testnet coins are not revenue** — `livemode` is carried through to
  the same `is_test_money` check Stripe events get.
- **DeskPilot cannot price an asset.** A confirmation with no
  `amount_cents` establishes nothing; the watcher states the rate it
  observed.

**37 tests, all passing.**

### Remaining owner actions

1. Supply a receiving address from a wallet they control
   (`solvent setup crypto`).
2. Run or arrange a chain watcher and configure its shared secret.
3. Decide whether to convert at all, and if so name the provider — and
   understand that the provider converts, not DeskPilot.
4. Take their own tax advice on receiving crypto as business income.

---

## D. Stripe readiness

| component | state |
|---|---|
| Webhook signature verification | **configured in code** — v1-only, constant-time, replay-window enforced, empty secret refused outright |
| Raw-body handling | **configured** — the exact signed bytes, never re-serialised |
| Event→meaning mapping | **configured** — paid, refunded, disputed, payout paid/failed |
| Job attribution | **configured** — `solvent_job_id` metadata |
| Test-money exclusion | **configured** — `livemode=false` is never revenue |
| Delivery spool | **configured** — the process cannot listen on a port, by design |
| Rail selection in policy | **unconfigured** — `approved_rail` is empty |
| API key / webhook secret | **unconfigured**, and deliberately never stored in policy |
| Webhook endpoint registration | **unconfigured** — the owner's Stripe account |
| Live-verified status | **unconfigured** — the ladder's top rung, never reached |

The ladder is `SELECTED → ENGINEERING_READY → CONFIGURED →
LIVE_VERIFIED`. The first two are claims DeskPilot may make about its
own work; the last two are claims about the world, and only the owner
can make them.

---

## E. Commercial configuration

| term | value | state |
|---|---|---|
| Hourly rate | **$40/hour** | **already recorded** (`hourly_rate_cents: 4000`) |
| Minimum project fee | **$40** | derivable and now settable; the owner runs one command |
| VPS cost | **~$98/month** | → **14 cents/CPU-hour** (below) |
| Minimum margin | **30%** | settable; applied in the Finder's verdict and the bid floor |
| Cited market rates | — | **still open** |

### The compute rate, derived

```
$98.00 / month ÷ 730.5 hours (365.25/12 × 24) = $0.134155/hour
                                              = 13.4155 cents/hour
                                      rounded up = 14 cents/CPU-hour
```

Two honest caveats, both in the code:

- It is charged **per CPU-hour** but derived from a whole-machine cost.
  On a multi-core VPS that overstates the rate — the safe direction,
  since an understated cost produces a bid that loses money. If the
  owner states the core count it can be divided.
- It is an **amortisation, not a marginal cost.** The $98 is owed
  whether or not a job runs, so this only recovers it if the machine is
  busy. At low utilisation the true cost per job is higher than any
  per-CPU-hour figure can express — which is an argument for taking more
  work, not for charging less.

### Cited market rates — the one still open

Not closed, and deliberately not faked. I searched for the BLS OEWS
figure for SOC 43-9021 (Data Entry Keyers); the current BLS page is a
download index rather than the values, and the figures I found came from
third-party aggregators citing a May 2025 release. Recording a number
from a secondary source as though it were the primary one is exactly
what the citation requirement exists to prevent.

Separately, **OD-7 records that the owner approves the rate source
before anything is loaded from it**, so this was never mine to close.

### Remaining configuration blockers

**One.** Cited market rates. The other three close with a single command
(section H).

---

## F. Rigorous testing

### Totals

| | solvent | roflo | combined |
|---|---|---|---|
| **Total** | 3,923 | 94 | **4,017** |
| **Passed** | 3,922 | 94 | **4,016** |
| **Failed** | 0 | 0 | **0** |
| **Skipped** | 1 | 0 | **1** |

```
Ran 3921 tests in 526.169s
OK (skipped=1)
```

(The count above the block is two higher: a test added while fixing the
mutation survivor described below, after that run.)

The skip is `test_installer_packaged_entrypoint`, which cannot start a
subprocess once the irreversible egress guard loads.

### Mutation results

Sixty-nine mutants. Nine are new this sweep: four on the bidder, five
on crypto.

The first full run was **66/69 — one survivor and two not applied**,
and both outcomes were faults in my own work rather than in the code:

```
SURVIVED     the-floor-ignores-the-cost
NOT_APPLIED  unconfigured-terms-still-recommend-work  (old text found 2 times)
NOT_APPLIED  the-owners-margin-is-ignored             (old text found 2 times)
```

**The survivor is the interesting one.** Deleting `if cost_cents >
floor:` — the clamp that stops any price sitting below what the work
costs — broke no test. My test set the margin to 1% and asserted the
floor was at least the cost. But for *any* margin strictly between 0
and 1, `cost / (1 - margin)` is already at or above cost, so the margin
branch satisfied that assertion on its own and the clamp underneath it
never executed. The test was passing for a reason that had nothing to
do with the control it was named after.

The clamp only bites when the margin is **zero**. Rewritten to set it
to zero with a one-cent floor, so removing the clamp drops the floor to
one cent and the test fails. A companion test pins the reasoning across
five margins, so if the margin rule ever starts producing a floor below
cost, that shows up as a failure rather than as a silently redundant
clamp.

The two `NOT_APPLIED` were non-unique anchors: `configured, gaps =
self.commercially_configured()` and `margin = self.minimum_margin_
fraction` each appear twice in `finder.py`. Widened to multi-line
anchors that match one site.

All three re-run individually and killed:

```
1/1 killed  (0 survived, 0 not applied or harness)   x3
```

**69/69, no survivors, nothing unapplied** — reported as a first run of
66 plus three resolutions, not folded into a single flattering number.
A `NOT_APPLIED` is never counted as a kill.

### Unseen exam results

The sealed exam runs on its committed seed (101318552, derived from the
frozen commit it was drawn against) as part of the suite: **60/60**.

### Adversarial findings

Four, from this sweep:

1. **My own prior finding was over-broad.** Freelancer's public
   discovery was recorded FORBIDDEN on a robots.txt citation that covers
   page paths and not the API. Corrected, with both facts recorded.
2. **A float made the bid floor a cent too high.** `70000 / (1 − 0.3)`
   is `100000.00000000001` in binary floating point, so the floor for a
   $700 cost at a 30% margin came out at $1,000.01. Now computed with
   exact `Fraction` arithmetic. One cent is not money, but a floor
   nobody can reproduce by hand is a floor nobody can check.
3. **A structural test that could not tell a promise from its
   violation.** My first "nothing here can spend" test grepped the
   source text and failed on the word *broadcast* inside a comment
   saying there is no broadcast. It now walks the AST and checks
   identifiers, plus a third test asserting the scan finds anything at
   all — so its absences mean something.
4. **A branding rule caught `X-Deskpilot-Signature`.** Trivial, and the
   kind of thing that ships: "Deskpilot" and "DeskPilot" are different
   products to anyone searching.

### Unresolved defects

**None in the code.** Every defect found this sweep is repaired and
each is pinned by a mutant that dies when the control is removed.

Three process faults are worth recording because they cost real time
and two of them repeated a mistake I had already diagnosed:

1. **A mutant was left applied when I killed the harness mid-run.**
   `finder-scores-work-it-declined` was live in `finder.py`, and an
   automated check was asking me to commit at that moment. Committing
   would have written a deliberately broken line into the branch, where
   it would have passed every test except the one it breaks. Caught by
   verifying all 69 anchors before committing, restored, and confirmed
   by re-running the suite.
2. **A guard that skipped and exited zero.** A restart used
   `grep -q ... && echo "ALREADY RUNNING" || (run)`. It found a stale
   process, skipped the run, and reported success — work not done,
   reported as done.
3. **A guard that matched itself.** Its replacement refused to start
   because the pattern `[p]ython3 tools/mutate_solvent.py` appears
   inside the guard's own shell command line. The `[p]` trick defeats
   the `grep` process, not the enclosing wrapper. I had diagnosed
   exactly this in a wait loop earlier in the session and then wrote
   the same shape again.

All three are the same mistake wearing different clothes: a check whose
success does not depend on the thing it is supposedly checking. That is
also precisely what the mutation survivor was. The standing rule that
comes out of it: **while the mutation harness runs it owns the working
tree**, and anything that looks uncommitted may be a sabotaged line.

---

## G. Autonomous readiness

> **Can DeskPilot autonomously discover a legitimate paying job, bid on
> it, complete it, independently verify it, submit it, and reconcile
> payment?**

**No — not end to end, today.**

### PARTIALLY IMPLEMENTED

Stage by stage, which is the only honest way to answer it:

| stage | state |
|---|---|
| **Discover** | implemented, not authorised. One route (Freelancer's API) is technically open and unread; the rest are closed or forbidden |
| **Bid** | **implemented and tested**, on measured completion time and real cost. Composes; cannot send |
| **Complete** | **implemented and proven** — five certified skills, measured, deterministic |
| **Independently verify** | **implemented** — T1 deterministic, T2 by owner review or an independently authored library. T3 unproducible |
| **Submit** | implemented behind seven gates; no platform permits it |
| **Reconcile payment** | **implemented** for Stripe and crypto; neither configured; no payment ever processed |

### Each remaining restriction

1. **No authorised discovery route.** Freelancer's API answers
   unauthenticated and publishes its limits — the most promising finding
   in this sweep — but its API terms are unread, and unread is not
   permitted. Upwork's API requires a registered application under a
   verified company identity. Guru refuses machine reads with a 403, two
   days running. Fiverr has no developer site. PeoplePerHour forbids
   query-string search. Contra's permission covers pages without jobs.
2. **No credentials anywhere.** Nothing is configured for any platform.
3. **No contracting identity**, so no invoice can be issued.
4. **No payment rail configured.** Stripe's verification is built and
   keyless; crypto's address field is blank by design.
5. **One commercial blocker open** — cited market rates.
6. **T3 unproducible**, so work above $5,000 is refused at
   qualification.
7. **Nothing has been live-verified.** Every figure in this repository
   is from simulation or measurement of DeskPilot's own execution. No
   real opportunity, no real bid, no real payment.

It is not BLOCKED: the pipeline runs end to end under simulation, and
the manual-import route needs nobody's permission. It is not IMPLEMENTED
BUT NOT LIVE VERIFIED either, because *discovery* and *submission* are
not merely unverified — they have no permitted route at all. Bidding,
execution, verification and reconciliation genuinely are implemented and
unverified; discovery and submission are blocked upstream of that.

---

## H. Owner activation checklist

Only what is actually required. Nothing below asks for information
DeskPilot does not use.

### 1. Marketplace registration

Pick one. They are not equivalent:

- **Freelancer.com** — read the **API terms of service** and decide
  whether automated reading of active projects is permitted for your
  account. The endpoint already answers without a credential; the only
  missing thing is the permission, and it is a document, not a key.
- **Upwork** — register an application at the developer site under a
  company account with a verified ID; you will need to state your
  intended use. Yields a client ID and shared secret.
- Guru, Fiverr, PeoplePerHour, Contra: nothing to register that reaches
  job data.

### 2. API credentials

Only after step 1, and only for the platform chosen. DeskPilot stores
the *presence* of a credential, never its value — the value goes in the
environment.

- Freelancer: none needed for the public endpoint.
- Upwork: `UPWORK_CLIENT_ID`, `UPWORK_CLIENT_SECRET`.

### 3. Commercial policy

One command closes three of the four blockers:

```
solvent setup commercial \
    --minimum-fee-cents 4000 \
    --minimum-margin-fraction 0.30 \
    --compute-cents-per-cpu-hour 14
```

The fourth needs a rate you can cite:

```
solvent setup commercial \
    --market-rate csv-cleanup=<cents>=<citation>
```

Use a primary source — the BLS OEWS table for the occupation, or your
own historical actuals. Per OD-7, approve the source in Policy first.

*(If your VPS has more than one vCPU, say so and the 14 can be divided.)*

### 4. Stripe

```
solvent setup contracting --legal-name "..." --email "..." --address "..."
solvent setup stripe SELECTED
solvent setup stripe ENGINEERING_READY
```

Then, in your Stripe account: create the account, obtain the webhook
signing secret, register the endpoint, and run a receiver that writes
deliveries into the spool. Set `SOLVENT_STRIPE_SECRET` in the
environment. Advance to `CONFIGURED` only once that is true, and to
`LIVE_VERIFIED` only after a real payment has been verified end to end.

**Legal name, postal address and email are genuinely required** — they
appear on the invoice. A tax reference is recorded as *present only*;
DeskPilot never stores the number.

### 5. Cryptocurrency wallet

```
solvent setup crypto --network <ethereum|base|polygon|bitcoin> \
                     --asset <USDC|ETH|BTC|...> \
                     --address <your own receiving address>
```

Use a wallet **you** control. Give the lower-case form of an EVM address.
DeskPilot stores the address and nothing else — it holds no key and
cannot spend from it. Then run a chain watcher and set its shared secret
so confirmations can be authenticated.

### 6. Auto-conversion

Optional, and off unless you want it. If you already have an exchange or
processor that converts for you, record it so the books reflect what it
did. **DeskPilot will not convert anything** — authorising this permits
*recording*, not moving.

### 7. Production activation

In order, and not before the above:

1. `solvent readiness` — every OWNER_CONFIG row should read READY.
2. Provision the owner signing key (OD-5), the last gate on real
   external execution.
3. Take **one** job through manual import and let the pipeline run it
   end to end.
4. Only then consider enabling continuous operation, with
   `max_jobs_per_run` and `max_committed_cents` set deliberately.

Step 3 is the one that matters. It needs no platform's permission, and
it is the only step that turns any of this into revenue.
