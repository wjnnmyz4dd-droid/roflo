# DeskPilot and Solvent: which name goes where

**Read this before renaming anything.**

DeskPilot is the product. Solvent is the engine it runs on. Both names are
correct, they name different things, and the line between them is load-bearing.

The single source of truth is [`solvent/branding.py`](../solvent/branding.py):

```python
PRODUCT = "DeskPilot"   # what the owner sees
ENGINE  = "Solvent"     # what runs underneath
```

Owner-facing text imports `PRODUCT` rather than spelling the name inline, so
the product can be renamed again by editing one line. Internal identifiers stay
spelled `solvent` in the source.

## Why not just rename everything

Because several occurrences of "solvent" are **contracts with something outside
the source tree**. Renaming one does not change a label; it breaks a running
installation, and the breakage is silent in the cases that matter most.

| Name | What breaks if renamed |
|---|---|
| the `solvent` Python package | every import, and any pickled or dotted reference |
| the database file and its tables | the owner's data stops being found |
| migration identifiers | migrations re-run or are skipped |
| `SOLVENT_OWNER_KEY` | **no approvals can be issued** — the owner loses control of their own system |
| the `solvent_session` cookie | every signed-in browser is signed out |
| the `solvent_job_id` payment metadata key | incoming payments stop matching jobs, and money goes unattributed |
| `/var/lib/solvent`, `/etc/solvent/solvent.env` | the database, spools and secrets are not where the service looks |
| the `solvent` CLI | every deployment instruction and runbook stops working |
| `solvent.service` | the service does not start after a reboot |
| audit event names | the chain still verifies, but queries over history stop matching |
| historical audit rows | **evidence is falsified.** The log is append-only and hash-chained for exactly this reason |
| signed approval payload fields | existing signatures no longer verify, and past approvals become unprovable |

The last two deserve emphasis. The audit log is the record of what the system
did and what the owner authorised. Rewriting it to look tidier is not a
branding change; it is destroying evidence, and the hash chain is there to make
that detectable.

## The rule

- Is a **person reading it in the product** — a page, a heading, a CLI banner,
  a text message, a setup instruction? → **DeskPilot**, via `PRODUCT`.
- Is a **machine reading it** — an import, a path, a table, a cookie, an
  environment variable, a service unit, a signed field, an audit event name? →
  **Solvent**, spelled literally.
- Is it **technical evidence or a diagnostics page** where the engine identity
  is genuinely useful? → naming Solvent is correct and wanted. The Diagnostics
  page does exactly this.

## What is deliberately still Solvent in owner-visible places

These were considered and kept:

- **`solvent setup …`, `solvent halt`, `solvent resume`** and every other
  subcommand. The owner types these, but they are also what every existing
  deployment document says to type. A `deskpilot` alias was not added; see
  below.
- **The `--reason` defaults on `halt` and `resume`** ("owner halted Solvent").
  These become the text of audit records. They are evidence, and evidence about
  the engine is accurately about the engine.
- **Reason strings produced by engine modules** (`capability.py`, `feedback.py`,
  `policy.py`, `skillslab.py`, `clientrelations.py`, `readiness.py`). These
  explain why the engine refused something and land in the audit log.
- **Docstrings and comments throughout.** They describe the engine to a
  developer, which is what they are for.

## The CLI alias question

A `deskpilot` alias for the `solvent` command was considered and **not added**.
It would have to be a second console-script entry point resolving to the same
`main()`. That is cheap, but it creates two spellings in circulation, and every
runbook, systemd unit and support answer would then have to say which one it
means. One name for the command is less confusing than two. The command is
`solvent`; the product is DeskPilot; the naming document is this file.

If that decision is revisited, the alias **must** dispatch to the same
`solvent.cli.main` — no duplicated argument parsing, no second implementation.

## Tests that hold this line

[`tests_solvent/test_branding.py`](../tests_solvent/test_branding.py) asserts
the retained engine names as deliberately as the changed product ones,
including that no `Desk Pilot` / `Desk-Pilot` spelling creeps in, and that this
document's warning is still present in `branding.py`.
