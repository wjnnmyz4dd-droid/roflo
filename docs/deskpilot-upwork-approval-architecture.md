# DeskPilot — Upwork owner approval architecture

Date: 2026-10-18 · Branch `claude/solvent-architecture-audit-8ot9et`
· Module `solvent/upworkbid.py` · Tests
`tests_solvent/test_upwork_bidding.py`

DeskPilot discovers an opportunity, prices it, asks the owner, and
stops. Approval makes a proposal *eligible* for submission. Nothing here
submits it, and that is a property of the code rather than a policy
written beside it.

---

## 1. The workflow

```
discover → price → rank → notify the owner → owner approves or rejects
                                                      ↓
                                      eligible for the owner to submit
                                                      ↓
                              the owner signs in to Upwork and submits
```

The last step is a person's, and DeskPilot has no code that could take
it: `solvent/upworkbid.py` contains no HTTP client, no socket, no
subprocess, and a test asserts it (`test_the_module_contains_no_
submission_path`). A path that does not exist cannot be re-enabled by
accident.

## 2. Two approvals that answer different questions

The brief asks whether an external SMS approval can satisfy Upwork's
confirmation requirement. It cannot, and the reason is worth stating
precisely, because "add an SMS step" sounds like it ought to be enough.

| | DeskPilot's owner approval | Upwork's confirmation |
|---|---|---|
| Question | may this business spend its money and capacity on this job at this price? | does this account holder authorise this action on this platform? |
| Authority | the owner's HMAC key, held by DeskPilot | Upwork's own session |
| Who can answer | the owner, through `OwnerChannel` | the account holder, in Upwork's interface |
| What it protects | the business's margin, capacity and risk limits | the Upwork account |

Neither substitutes for the other. Upwork's OAuth authorization endpoint
is on `www.upwork.com`, so even the *grant* that would let an agent act
is given by a person signing in there. DeskPilot keeps both, in that
order, and treats the second as a step it prepares for and never
performs.

## 3. What the approval is bound to

The brief requires an approval that is authenticated, job-specific,
bid-specific, time-limited, replay-resistant, auditable and idempotent,
and bound to the opportunity, the bid, the proposal version, the
Connects cost, the platform fees, an expiry and the owner's identity.

`OwnerChannel.SignedApproval` already signs: nonce, owner identity,
subject, action class, job id, max cents, expiry. Widening that field
list would change what every previously issued approval means — a
signature covering a different payload is a different scheme, and every
outstanding approval would have to be re-examined.

So the extra fields are bound **by content** instead. The
recommendation's material fields are hashed and the digest travels in
`subject`, which the signature already covers:

```
subject = "upwork proposal UPW-1 rev 1 at $200.00 #a3f1c2d4e5b60718"
                                                  └─ sha256 of the material fields
```

The material fields are the opportunity id, the version, the bid, the
floor, the Connects requirement, the Connects cost, the service fee, the
execution cost, the checker cost, the revision allowance and the net
contribution.

| Requirement | How it holds |
|---|---|
| Authenticated | HMAC-SHA256 under the owner key; an unkeyed channel issues nothing and says which variable to set |
| Job-specific | `job_id`, and the opportunity id inside the digest |
| Bid-specific | `max_cents`, and the bid inside the digest |
| Proposal version | inside the digest |
| Expected Connects cost | inside the digest |
| Expected platform fees | inside the digest |
| Time-limited | `expires_at`, checked by `verify` |
| Replay-resistant | single-use nonce; `consume` spends it |
| Auditable | `owner.approval_issued` in the append-only log, with the key fingerprint |
| Idempotent | a consumed nonce verifies false thereafter |
| Owner identity | signed, and `issue` refuses a non-owner |

## 4. Material change, by arithmetic rather than by memory

> If any material proposal detail changes, require fresh approval.

An instruction like that usually becomes a comment and then a bug. Here
it is the digest: change any material field and the subject changes, so
the approval the owner signed no longer describes what is in front of
us, and `approval_problem` says so by name.

What is deliberately *not* material: the summary. Rewording the pitch
does not change what the owner agreed to, and forcing a fresh approval
for a copy-edit trains people to approve without reading. There is a
test for each half — `test_a_changed_bid_invalidates_the_approval` and
`test_rewording_the_pitch_does_not_invalidate_the_approval` — because a
rule that fires on everything and a rule that fires on nothing are
equally useless and look the same from one side.

`test_the_digest_covers_every_material_field` walks all nine material
fields and requires each, alone, to move the digest.

## 5. SMS, and what it is for

`solvent/notify.py` delivers the recommendation and carries a link. It
is a **delivery channel, not an authentication factor**: possession of
an SMS is not treated as the owner's answer to anything. The approval is
minted by `OwnerChannel` under the owner key, through whatever the owner
actually touches.

A notification that fails to deliver is recorded as failed and leaves
the proposal unapproved — tested in the simulated acceptance, where a
refusing provider yields state `FAILED` and eligibility stays false.
Silence is never consent.

## 6. What the owner is shown

Every field §8 of the brief requires, each with its provenance attached
and none of them rounded off into a bare number. On today's evidence the
competition figures, Connects and fee are **UNAVAILABLE** and the market
rate is a **MODEL ESTIMATE**; see
`deskpilot-upwork-bid-economics.md` for why and what would change it.

The message ends:

> Approving makes this proposal eligible for submission; it does not
> submit it. Submission happens in Upwork's own interface, by you.

## 7. Failure modes, and what each does

| Case | Behaviour |
|---|---|
| No owner key configured | every approval refused, with the variable named |
| Approval requested by a non-owner | refused |
| Approval expired | `verify` refuses |
| Approval already consumed | `verify` refuses |
| Signature tampered | `verify` refuses |
| Wrong action class | refused: a proposal is `C3_FINANCIAL_COMMITMENT`, not a communication |
| Approval ceiling below the bid | refused, both figures named |
| Bid, draft, Connects or fee changed after approval | refused as a different proposal |
| Notification undelivered | recorded failed; proposal stays unapproved |
| Approved | eligible for the owner to submit; DeskPilot still does not submit |

## 8. Mutation coverage

| Mutant | What it breaks |
|---|---|
| `upwork-the-approval-is-not-bound-to-the-recommendation` | an approval for one bid authorises another |
| `upwork-an-approved-proposal-becomes-submittable` | DeskPilot reports itself allowed to submit |

Both killed. No historical coverage is claimed for this module: every
mutant here was run against the code as committed.

## 9. What this does not say

* It does not say DeskPilot may bid on Upwork. Submission is
  prohibited and the official route's capabilities are unverified; see
  `deskpilot-upwork-official-integration-review.md`.
* It does not say the approval satisfies any Upwork requirement. It
  satisfies DeskPilot's.
* It does not say an SMS is secure. It says an SMS is not relied on for
  security.
* No real proposal, Connect, customer message or submission occurred at
  any point.
