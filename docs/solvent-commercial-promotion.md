# Commercial promotion of the five-skill library

**Owner authorisation of 2026-10-12.** The owner explicitly authorised
commercial promotion of four capabilities, subject to verification of their
existing certification records, and asked that `csv-cleanup/1.0`'s existing
approval be preserved.

This document records what was verified, what was promoted, what was withheld,
and what promotion deliberately does not grant.

## 1. The authoritative registry, after the promotion

Read back in a separate process from the database the owner's own command
wrote, by querying `registered_capabilities` directly rather than by trusting
the command's own output:

| Capability | Decision | Registered | Checks covered | Withheld |
|---|---|---|---|---|
| `csv-cleanup/1.0` | APPROVED | proven | 11 of 11 | — |
| `report-builder/1.0` | APPROVED | proven | 8 of 8 | — |
| `spreadsheet-automation/1.0` | APPROVED | proven | **14 of 15** | `number_format` |
| `data-entry/1.0` | APPROVED | proven | 16 of 16 | — |
| `document-processing/1.0` | APPROVED | proven | 14 of 14 | — |

`may_deploy` returns true for all five, with the registry's own reason
("… is approved and proven"). That is the question delivery asks, so it is the
question this table answers.

## 2. The certification evidence each promotion rests on

Each verifier's certification is **re-run against the current implementation**
at promotion time. An edited verifier is different code and starts again, so a
promotion cannot be carried forward over a change to the thing being promoted.

| Capability | Verifier | Implementation fingerprint | Certified | Black-box |
|---|---|---|---|---|
| `csv-cleanup` | `solvent.csvverify.run` | `sha256:dd27d3bbf8f424e72dbfd11e418710cd` | 11 of 11 | 34 fixtures, 3 levels, 0 false completions |
| `report-builder` | `solvent.reportverify.run` | `sha256:9a60e1ca7d2f0b8c069747e54c9453dc` | 8 of 8 | 27 fixtures, 8 levels, 0 false completions |
| `spreadsheet-automation` | `solvent.sheetverify.run` | `sha256:9df643943149b753d86b21579d63fe29` | 14 of 15 | 17 fixtures, 7 levels, 0 false completions |
| `data-entry` | `solvent.entryverify.run` | `sha256:0f5a8fd713b1ceb6a668b17f25fbae2a` | 16 of 16 | 17 fixtures, 7 levels, 0 false completions |
| `document-processing` | `solvent.docverify.run` | `sha256:c6b99eca3befe952133aecc119b1d7c7` | 14 of 14 | 20 fixtures, 7 levels, 0 false completions |

The levels are the black-box matrix's: ordinary work at three complexity
levels, boundaries, hostile input, false-success traps, real client behaviour,
and capability gaps. "0 false completions" means no scenario produced a
deliverable that was accepted while being wrong — the one number that resets a
promotion to no.

A check is credited only when it was right in **both** directions over enough
distinct evidence: ten distinct correct artifacts accepted, every defect class
it was shown caught, five distinct instances of each class. A check nobody put
a defect in front of is not certified however many correct artifacts it
approved.

### What certification does not mean

It does not mean a verifier is correct. A defect class nobody modelled is not
in the battery. The honest reading is "met the stated evidence requirements".

## 3. The one check that is not certified, and is not claimed

`spreadsheet-automation.number_format` cannot be certified by any evidence. A
display format changes how a figure is shown and not what it is, so there is
nothing to recompute and nothing a wrong answer would look like.

It is recorded in `harness.UNCERTIFIABLE` with that reason, excluded from
`certifiable_scope`, and therefore **absent from the promotion's scope**. This
is enforced rather than noted:

```
may_authorize_delivery(verifier_ref="solvent.sheetverify.run",
                       capability_version="spreadsheet-automation/1.0",
                       check="number_format")
  → REFUSED: solvent.sheetverify.run is not certified to decide
    'number_format' for spreadsheet-automation/1.0; it is trusted only on:
    compute_column, consolidate_sheets, drop_empty_rows, formulas_agree,
    no_unauthorised_changes, opens_as_workbook, preserve_columns,
    rename_headers, require_columns, row_reconciliation, select_columns,
    sort_rows, total_row, trim_whitespace
```

while `compute_column`, on the same promoted capability, is ALLOWED. The
refusal is about the check, not the capability. A promotion that claimed
`number_format` is refused outright with nothing recorded, and a mutant
(`promotion-claims-checks-it-is-not-certified-for`) restores that defect to
prove the refusal is load-bearing.

**`spreadsheet-automation` is promoted on fourteen of fifteen checks.** A job
resting on cell formatting is refused at delivery exactly as it was before the
promotion.

## 4. How the promotion is recorded

`harness.promote_capability` is a separate function from
`provision_capability` on purpose. Provisioning records an APPROVED decision
only when there is no decision yet, deliberately: a capability the owner
cleared for development carries a LIMITED answer, and silently turning that
into APPROVED because somebody re-ran a provisioning command is exactly the
conflation the two words exist to prevent.

So LIMITED → APPROVED is a **new decision**, recorded as one. The LIMITED row
stays where it is, because `capability_proposals` is append-only and the
question "when did this become sellable, and on what evidence" has to stay
answerable:

```
report-builder   DECIDED  LIMITED   <the development grant>
report-builder   DECIDED  APPROVED  <this authorisation>
```

Four checks refuse, in this order, and any one of them stops the promotion
with nothing written:

1. **Identity.** Only a registered owner clears a capability for client work.
   Asked at the entry point so an unauthorised attempt leaves no PROPOSED row
   behind, *and* enforced independently inside the registry's `decide` — which
   is the actual control. A test asks the registry directly, so that claim does
   not rest on reading the caller.
2. **Existence.** The capability must be one this build has, with a verifier in
   the dispatch table.
3. **Scope.** The certification is re-run against the current implementation
   and must cover every check the promotion claims.
4. **Evidence floor.** `capability.promotion_verdict` must pass: enough
   fixtures, enough complexity levels, and zero false completions.

And afterwards `may_deploy` must actually return true. Reporting a successful
promotion while delivery would still refuse is a defect, and it raises rather
than printing a cheerful message.

### Audit journal

Every promotion writes three separately audited events under the owner's
identity — `capability.proposed`, `capability.decided`, `capability.registered`
— carrying the owner's reason. The journal is hash-chained, and the
`capability` authority is the **only** authority that writes during a
promotion. A test asserts that set is exactly `{"capability"}`: a second
authority appearing there would mean something took the promotion as licence to
act.

csv-cleanup's existing APPROVED row is not re-decided and not restamped. Its
original date and reason stand, which is what "preserve its status" has to mean
on an append-only table.

## 5. What promotion does not grant

Promotion clears a skill to be *delivered*. It is not permission to do anything
else, and each of these is a separate authority that this change does not
touch:

| Not granted | Still governed by |
|---|---|
| Accepting client jobs | the owner's job acceptance path |
| Sending proposals | the Action Gate |
| Contacting clients | the Action Gate and the egress allowlist |
| Spending money | the Financial Governor |
| Processing payments | the configured payment controls |
| Bypassing platform restrictions | the platform rules, unchanged |
| Unrestricted real-world execution | the runtime's execution authority |
| Disabling simulation mode | the owner's policy, unchanged |

Verified after a promotion run on a real database: `jobs`, `applications`,
`action_requests`, `ledger_entries`, `payments`, `payment_events`,
`submission_attempts`, `opportunities` and `governor_decisions` are all empty;
no `gate.*`, `governor.*`, `ledger.*`, `payment.*` or `submission.*` event
exists in the journal; and the egress, financial and governance policy values
are byte-identical before and after.

## 6. The owner's command

On the Windows VPS, in the DeskPilot installation:

```
deskpilot-cli setup capability --why "your reason, recorded with the decision"
```

or, equivalently, against the Python entry point:

```
python -m solvent.cli setup capability --db <path-to-deskpilot.db> --why "..."
```

`--only <capability>` promotes one instead of all of them. The command is
idempotent: running it twice does not append a second approval, and a restart
never needs it again.

### There is no signature step, and why

`policy.is_owner` checks **registration, not authentication**: it asks whether
the identity making a request is the registered owner identity, and it has no
way to verify that the person typing is that owner. This is recorded as
limitation **OD-5** in `docs/solvent-owner-decision-integration.md` and it is
unchanged here.

The practical consequence: the authentication for this promotion is that *you*
run the CLI as the owner on your own machine, over the terminal. There is no
request for you to sign and nothing to enter into the browser, because the web
surface deliberately cannot make this decision — it writes to a spool it cannot
read back, and the runtime validates and decides. Keeping the promotion on the
terminal path is the control, not an inconvenience.

Nothing in this repository promotes a capability on its own. The promotion
records exist as data; the decision is the command.

## 7. Verification

| | |
|---|---|
| Promotion suite | `tests_solvent/test_commercial_promotion.py`, 27 tests |
| Full regression after the promotion | **3388 pass, 2 skips, 0 failures** |
| Mutation battery | **178 of 178 killed**; 4 of them guard this promotion |
| Registry confirmation | read back in a separate process from the database the CLI wrote |

The four mutants remove one promotion control each — the evidence floor, the
scope check, the identity check, and the guard against appending a second
approval — and each is killed by a named test that newly fails and recovers on
revert.

The regression found one defect the promotion introduced, now fixed:
`setup capability` reported a partial refusal by naming only what was *not*
promoted, so a non-zero exit could be read as "nothing happened" while four
capabilities had just become sellable. It now names both sides, with a negative
control for the case where nothing is cleared.

The promotion suite covers: the evidence floor refusing a capability with a
false completion; a promotion wider than its certification refused with nothing
recorded; a capability this build does not have refused; a component and an
unregistered owner name both refused, by the registry as well as at the entry
point; LIMITED → APPROVED recorded without losing the LIMITED row; a second run
changing nothing; csv-cleanup neither re-decided nor restamped; `number_format`
still refused at delivery while `compute_column` is allowed; a promoted
capability delivering a real job end-to-end on the records' own authority; a
certified-but-unpromoted and a LIMITED capability each escalating instead of
delivering; and every restriction in section 5 asserted after the promotion ran.
