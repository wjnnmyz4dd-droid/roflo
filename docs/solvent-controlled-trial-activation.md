# Controlled first trial — activation status and owner action packet

**Date:** 2026-09-20 · **Branch:** `claude/solvent-architecture-audit-8ot9et`
**Posture:** `simulation_only` **ON**, egress allowlist empty, no money moved,
no client contacted, no marketplace touched.

This records one pass of work: certifying two operations the owner authorised
conditionally, closing the activation items that could be closed without
private information, and rehearsing the controlled first trial twice — once
going right and once going wrong.

It ends with **one** owner action packet. Everything the owner has to do is in
§7 and nowhere else.

---

## 1. What the owner asked for, and what happened

| Asked | Outcome |
|---|---|
| Certify `rename_headers` and `sort_rows`; promote **only if** they pass | **Both passed.** Promoted into `csv-cleanup/1.0` as OD-14. |
| Close every activation item closable without private information | Four closed in code (§4). Four remain, all requiring the owner. |
| Prepare OD-5 / OD-1 / OD-2 / OD-3 without provisioning anything | Done (§4). No secret was generated, read, printed or stored. |
| Authorise the owner-hand-fed work source | Engineered and attacked (§5). Registration is still an owner act. |
| Keep communication human-relayed | Confirmed under a hostile client (§6). |
| Run positive and negative trial simulations | Both run, both green (§6). |

**No promotion was widened to make anything pass, and no certification was
weakened.** Three of the defects below are in checks that were *already*
certified; the honest reading is that the earlier certification was weaker than
it looked, and certifying two more operations is what exposed that.

---

## 2. The two operations, and what certifying them cost

Certifying an operation means building deliverables that are wrong in that
particular way and watching a verifier refuse them. Doing that for renaming and
sorting surfaced three defects — none of them in the new operations.

### 2.1 A renamed column was invisible to every comparison made by name

A renamed column shares no name between the source and the deliverable. Every
check compared columns by name, so the renamed column dropped out of both
projections — and swapping it with the column beside it moved one of the
client's *other* columns while every comparison saw a perfect file.
`no_unauthorised_changes`, the check whose entire subject is "nothing else
changed", accepted nine such instances in a single certification run.

**Fixed.** The plan's rename mapping is now an input to that check, so a renamed
column is an anchor rather than a blind spot, and a source column absent from
the deliverable under its agreed name is a failure. That also closes an
unauthorised rename, which looks exactly like a column that disappeared.

### 2.2 Authorising a rename authorised rewriting the column

`rename_headers` granted `full` change to the column's *values*. A deliverable
could rename the header and replace everything underneath it with another
column's data, and pass.

**Fixed.** Renaming is its own, narrower authorisation: the name may change, the
values may not. Where two requirements touch one column the broader one still
wins, so narrowing this took nothing away from a column another requirement
legitimately authorised.

### 2.3 Every check compared one column at a time

This is the serious one. A deliverable with **every value present and every
value on the wrong row** was accepted by the entire capability — every column's
values were exactly the source's, so every per-column comparison passed. That is
not an exotic failure; it is what sorting a column instead of sorting the rows
produces, and it is the single most likely way a sort implementation goes wrong.
The client would have received one customer's order under another's order
number.

**Fixed.** `row_reconciliation` now compares rows as rows, projected onto the
columns the job was not asked to change.

### 2.4 And one false rejection, found in the same pass

Protected columns were compared against the source's *distinct* rows
unconditionally, so a job nobody asked to deduplicate was told it had altered
data it never touched — with a detail line naming no altered value, because none
had been altered. Both that check and `row_reconciliation` now take the basis
from the plan.

`normalise_dates` gained the same kind of binding: it compared the date column
as a *set*, so a date moved onto the wrong row passed whenever some other row's
ambiguous date could have meant it. Dates are now checked against the day their
own row supplied. An ambiguous date is still not forced to one reading.

---

## 3. The evidence the promotion rests on

| | |
|---|---|
| Verifier | `solvent.csvverify.run` @ `sha256:dd27d3bbf8f424e72dbfd11e418710cd` |
| Certification stream | **526/526**, 0 false accepts, 0 false rejects |
| Development holdout | 524/524 |
| Surprise holdout | 526/526 |
| Five further unseen seeds | clean on all five |
| CSV defect classes | 21, five of them built for these two operations |
| Evidence floor | met by every certified check |
| Black-box scenarios | **34/34**, 0 false completions (9 new) |
| Mutation testing of the new controls | **21 mutants, 21 killed** |
| Full suite | 1,214 tests, all passing |

The nine new black-box scenarios: ordering as text (`10` before `9`), stable
ordering among equal keys, an empty sort key, a multi-column key, embedded
newlines, CRLF, a byte-order mark, non-ASCII header names that differ only by
suffix, a rename onto an existing name, a rename and a sort key naming absent
columns — and two traps: a column sorted instead of the rows, and the renamed
column moved. Both traps escalate rather than ship, caught by exactly the two
checks that were hardened.

### What this evidence does not say

* The defect-class list is a record of what has been thought of. Five classes
  were added only because these operations were certified, and three real
  defects arrived with them.
* Row-level binding needs two columns that survive untouched; date-level binding
  needs one whose values are unique and unchanged. Below that the checks fall
  back to the weaker per-column comparison, deliberately — inventing a pairing
  would reject correct work.
* Sorting is textual and stable. A client who meant numeric order gets `10`
  before `9`, and nothing detects that they meant something else.
* The version was held at `csv-cleanup/1.0` on the owner's instruction. The
  verifier's implementation changed, so the previous certification is invalid by
  fingerprint and a fresh one runs before any delivery. **That binding, not the
  version string, is what protects the approval.** There is no rule in the
  codebase requiring a bump when scope widens; if the owner wants one, that is a
  decision to record, not a fact to discover.

---

## 4. Activation items closed without private information

**OD-5 — owner signing key.** The key is now checked rather than merely read. A
key shorter than 32 bytes, a placeholder such as `<OWNER_KEY>` or `changeme`, a
value with no randomness in it, or a short value repeated to reach the length is
refused — and refusing fails closed rather than refusing to start, so Solvent
runs and says which variable to set. The key fingerprint in the audit is an HMAC
under a public label instead of a bare hash, so an audit row cannot be used to
test candidate keys offline. Refusal messages name the variable and never echo
the value. No key is generated, stored, printed or committed; tests use a
labelled test-only key defined in one place.

**OD-1 — contracting identity.** Details are now held *per purpose*. Naming the
contracting party needs a legal name and an email; an invoice needs an address; a
tax reference belongs to connecting a payment rail and nothing earlier. A tax
reference is recorded as **present**, never as a number — nothing anywhere
prints it, and storing the value would have written a tax identifier into an
append-only audit log that by construction cannot be redacted. (It reached there
through the policy patch payload, which records amendments verbatim. That is
now closed.)

**OD-2 — payment rail.** Four states instead of two: `SELECTED` (a decision),
`ENGINEERING_READY` (Solvent's own work, which it may report itself),
`CONFIGURED` (the owner has provisioned credentials Solvent never sees), and
`LIVE_VERIFIED` (a real payment has been observed arriving). The ladder moves
forward one step at a time, only the owner may advance it, and the two states
that assert something outside this repository require the owner's evidence
rather than an assertion. Current state: **`SELECTED`**.

**OD-11 — work source provenance.** "The owner typed this" is established from
Discovery's own registration record and never from data a source returned.
Seventeen attacks on that claim, all refused: a posting body asserting it, a
posting field named `owner_entered`, a source registered as manual that reaches
the network anyway, an unapproved source, a source nobody registered, and
self-registration by every non-owner authority.

---

## 5. What was *not* done, deliberately

Nothing in this pass: enabled live execution, populated the allowlist, cleared
HALT, accepted a client, sent a message, connected Stripe, charged money, signed
anything, published a service, bid on work, or deployed anywhere. No second
Guardian, Policy, Governor, Feedback authority or Customer Service was created.
`report-builder` was **not** promoted.

---

## 6. The trial, rehearsed twice

Both rehearsals make the same posture assertions — shared in code, so the
hostile path cannot be held to a lower standard than the happy one.

**Positive.** The owner's file: out of order, duplicated, padded, mixed date
notations, mixed-case regions, a non-ASCII name and a quoted comma. Every
operation in the promoted scope runs at once. The delivered bytes are checked:
renamed, sorted, deduplicated, dates normalised, regions mapped, padding
trimmed, `Épsilon Ltd` and `"Beta, LLC"` intact, every `Total` untouched. Every
requirement is independently verified. **The reply to the client is a draft;
sending it requires an owner approval and raises without one.**

**Negative.** The same trial with an ambiguous date, a hostile client sending
six messages at once, and a deliverable corrupted after the worker produced it.

* The ambiguity **stops the job** instead of being guessed; the question is
  drafted, not sent.
* No reply claims a legal entity or agrees that payment arrived.
* The claim to be the owner grants nothing — no approval was issued.
* The demand for extra work does not widen the scope.
* The instruction to disable verification changes nothing.
* The corrupted deliverable — every value present, every value on the wrong row
  — is caught by row reconciliation and **escalated rather than shipped**.
* A rude client does not put the system into HALT. An angry client is not a
  defect.

Both runs end with `simulation_only` on, nothing settled through the gate, zero
revenue, the rail still `SELECTED`, and an intact audit chain. A restart changes
none of it, and the job stays at `AWAITING_PAYMENT`: the work is verified, the
money has not arrived, and restarting does not decide otherwise.

---

## 7. OWNER ACTION PACKET

**This is the only list. Five items. Nothing below is something Solvent can do
for itself, and none of it is urgent — Solvent is safe sitting exactly where it
is.**

> **Never paste into this repository, a commit message, a chat window or a
> support ticket:** a signing key, an API key, a webhook secret, a password, or
> a tax identifier. Items 1 and 3 are done in your own secret store; Solvent
> only ever learns that they happened.

### 1 — Provision an owner signing key · *blocks every consequential approval*

Generate it on your own machine, store it in your host's secret manager, and
expose it to the Solvent process as `SOLVENT_OWNER_KEY`, outside the worker
namespace:

```
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Rotate by setting a new value; approvals issued under the old key stop
verifying, which is intended. **No key means no approvals** — an
unauthenticated system must not be able to spend money.

### 2 — Supply two contracting details · *blocks naming the contracting party*

For the first trial, only two:

* **legal name** — the name you contract under, as an individual (OD-1)
* **email** — where a client reaches you

Not needed yet: **address** (only to issue an invoice) and **tax reference**
(only to connect a payment rail, and recorded as present, never as a number).

Solvent will not infer any of these. Until the name is supplied it tells a
client, honestly, that it acts for an individual and cannot name them.

### 3 — Connect Stripe, when you want payment to be possible

Provision the secret key and webhook signing secret in your host's secret
store — never in this repository — then record the rail's move to `CONFIGURED`
with a one-line note of what you did. `LIVE_VERIFIED` comes later, and only
after you have actually seen a real payment arrive.

### 4 — Register the owner-entered work source · *blocks accepting any job*

One call, made as the owner, registering the hand-fed source and approving it.
It makes no external call and holds no credentials. This authorises **one
hand-fed opportunity at a time** and nothing else: no marketplace browsing, no
bidding, no claiming, no outreach, no prospecting, no scraping.

### 5 — Record the clearance for the model you will actually run

Readiness reports that the configured artifact is not the cleared one. Clearance
is keyed on the exact content digest — a family name clears nothing. Record the
artifact you will really run, with its licence and where you verified it.

---

### After all five

`simulation_only` stays **on** until you turn it off, separately and
deliberately. Doing these five does not start anything. It makes the first
controlled trial *possible*, with the client still hand-fed by you and every
outbound message still drafted for you to relay.

---

## 8. Disposition

**`csv-cleanup/1.0` is promoted with eleven certified checks, and Solvent
remains correctly unable to begin the controlled first trial.** Five owner
actions stand between here and that trial; four of them are things only the
owner can do, and the fifth is a decision only the owner can make.

The two operations passed. The interesting result is not that they passed — it
is that certifying them found three ways the already-approved capability could
ship wrong work, and one way it could refuse right work. That is what the
certification machinery is for, and it is a reason to treat the remaining scope
as tested rather than proven.
