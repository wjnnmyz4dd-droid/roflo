# Solvent — Always-On Deployment

**17 September 2026.** How Solvent runs 24/7 on a host that stays awake, and what
it does when the owner's laptop does not.

---

## 1. The physical constraint, stated plainly

**A computer that is asleep does not execute code.** No amount of software design
changes that. If Solvent must work while the owner's laptop is closed, Solvent
must be running on a **different machine that stays awake**.

So the laptop stops being the runtime and becomes what it should have been:

```
   OWNER DEVICE                          ALWAYS-ON HOST
   (may sleep, close, travel)            (stays awake)
        │                                      │
        │  inspect, approve, halt              ├── Solvent runtime (this doc)
        │  ───────────────────────────────►    ├── Policy · Governor · Gate
        │                                      ├── Ledger · Audit · Orchestrator
        │  the laptop is never in the          ├── Capability · Memory
        │  execution path                      └── approved model runtime
```

A VPS is **one** possible always-on host. So is a mini PC on a shelf, or a home
server. What is architecturally mandatory is *a machine that stays awake* — not
any particular vendor. Nothing in Solvent names a provider, so moving hosts is a
copy and a `systemctl enable`, not a rewrite.

## 2. The smallest thing that works

**One host. One Python process. SQLite. systemd.** That is the whole deployment.

| Considered | Verdict |
| --- | --- |
| Kubernetes | No. One process for one owner. It would be more moving parts than business logic |
| Docker | No. systemd already provides supervision, restart limits, resource caps and sandboxing. A container would add a build step and a second thing to patch |
| PostgreSQL | No. One writer, low write volume, and SQLite in WAL mode is durable. See §7 |
| Redis / a queue | No. There is one process; a queue between it and itself is ceremony |
| A second host | No. Failover for a business with zero revenue is cost without benefit. Revisit when downtime costs money |

Solvent has **zero third-party dependencies**, so there is nothing to resolve at
install time and no supply chain to audit. The venv exists to pin an interpreter,
not to install anything.

**The division of responsibility that keeps this honest:**

| Concern | Owner |
| --- | --- |
| Is the process alive? | **systemd** |
| Should this job advance? | **Job Orchestrator** |
| May this money be spent? | **Financial Governor** |
| May this leave the machine? | **Action Gate** |
| Is Solvent able to do anything right now? | `solvent health` (reads all of the above) |

`solvent/runtime.py` owns none of them. It starts the process safely, ticks tasks
on a schedule, and stops cleanly. If it ever grows an opinion about whether a job
is worth taking, that opinion belongs in the Governor — and a test asserts it has
not: the runtime never calls `amend`, `set_operating_mode`, `authorize_spend`,
`record_delivery` or `perform=`.

## 3. Two modes, one Solvent

| | LOCAL | ALWAYS_ON |
| --- | --- | --- |
| Where | the owner's machine | the always-on host |
| Storage | in-memory is fine | **in-memory is fatal — it refuses to start** |
| Lifetime | ends when the lid closes | survives reboots |
| Business logic | **identical** | **identical** |

There is one Solvent with two deployment modes, not two architectures. The only
behavioural difference is that `ALWAYS_ON` treats an ephemeral database as a
fatal startup error, because a 24/7 service whose ledger dies on restart is not a
24/7 service.

## 4. Startup: verify, then run

`solvent run` checks eight things before it does anything. **Fatal means do not
start**; everything else is a warning, because refusing to boot over a missing
Stripe secret would leave the owner unable to inspect the very gap they need to
fix.

| Check | Fatal? |
| --- | --- |
| Audit hash chain intact | **Yes** — evidence you cannot trust is worse than none |
| Durable storage | **Yes in ALWAYS_ON** |
| Database readable | **Yes** |
| Policy present | **Yes** |
| Operating mode | No — reports HALT, never clears it |
| Egress posture | No — reports fail-closed or live |
| Owner signing key | No — warns; approvals fail closed without it |
| Unsettled external actions | No — warns and routes to `solvent recover` |

## 5. Crash recovery: the part that actually matters

This is the defect a 24/7 deployment exists to survive, and it was real:

> The Action Gate recorded an external action **after** `perform()` returned. Kill
> the process mid-`perform()` — the moment the invoice is in flight — and the
> durable record contained **zero rows**. On restart Solvent had no idea the
> action was ever attempted. It would have done it again.

Reproduced by killing a real process mid-effect, then fixed:

```
    write intent  ──►  perform()  ──►  write outcome
         │                 │                 │
    committed          the effect        committed
    BEFORE the         may land here     AFTER
    effect can happen
```

A crash between the two leaves an **intent with no outcome**. That is not
"failed" and not "succeeded" — it is **unknown**, and it is recorded as unknown.
`ActionGate.unsettled()` lists them.

On restart, `reconcile()` puts every job owning an unsettled action into
**BLOCKED on OWNER**. It does not retry, and it does not assume success. The
owner is the only party who can go and look at whether the client got the email.

**Restarting a process is not repeating a business action.** A supervisor that
conflates the two sends the invoice twice.

| Failure | What happens |
| --- | --- |
| Process killed | systemd restarts it; startup checks run; unsettled actions block their jobs |
| Unhandled exception in a task | The tick is recorded as failed; the loop continues. Three in a row is recorded as degraded |
| Host reboot | `WantedBy=multi-user.target` starts Solvent; identical recovery path |
| Crash mid-external-action | Intent survives; job blocks on the owner; **no retry** |
| Network down | Nothing is attempted that the Gate would not allow anyway; jobs keep their states |
| Model unavailable | A task fails, is recorded, and retried on its next interval |
| Payment rail unavailable | Stripe redelivers; the event is deduplicated by event id |

## 6. Duplicate protection — what is actually guaranteed

**Solvent does not claim exactly-once external execution, because it cannot have
it.** No system that makes a network call can. Between "the request left" and
"the response arrived" there is a window that no amount of local bookkeeping
closes.

What it does guarantee:

| Effect | Mechanism | Where |
| --- | --- | --- |
| Payment events | Deduplication by Stripe `event_id`, persisted | `payment_events` (append-only) |
| Owner approvals | Single-use; `used_at` is durable, so a restart cannot un-consume one | `owner_approvals` |
| External actions | Write-ahead intent; an unsettled one blocks rather than retries | `action_requests` (append-only) |
| Job state | One authoritative state machine with `resume_state` | `jobs` |
| Ledger entries | Append-only, enforced by SQLite triggers | `ledger_entries` |

The honest summary: **at-most-once for anything consequential, with uncertainty
surfaced to the owner rather than resolved by guessing.**

## 7. Database durability

SQLite stays, and the reasoning is written down rather than assumed:

- **One writer.** One process, one owner, a handful of writes per job.
- **WAL mode** — a killed writer does not leave the database locked against the
  next start.
- **`synchronous = FULL`** — a committed transaction is on disk *before* the
  commit returns. Every recovery claim in this document depends on that.
- **`busy_timeout = 10s`** — the scheduler and the payment ingress can collide;
  failing instantly on a lock that clears in milliseconds turns a non-event into
  an incident.
- **Append-only triggers** survive any client, including a raw `sqlite3` shell.

Both settings were verified in place on a live database, not assumed.

**When to revisit:** more than one writing process, a second host, or write
contention showing up as `busy_timeout` expiries. None of those is true today,
and migrating early would cost a database server for no benefit.

## 8. Backups

`deploy/backup.sh` uses SQLite's **online backup API** through Python — a
consistent snapshot of a live WAL database while the service keeps running. `cp`
can capture a torn database; this cannot. Python rather than the `sqlite3` CLI
because Solvent already requires Python 3.11 and the CLI is missing on minimal
hosts.

Every backup is **verified, not merely written**: `PRAGMA integrity_check` *and*
Solvent's own audit hash chain must both pass inside the copy. A backup nobody
has checked is a hope.

**Secrets are deliberately excluded.** `/etc/solvent/solvent.env` holds the owner
signing key and the Stripe webhook secret; a backup that quietly contained them
would turn every copy into somewhere they can leak from. Back that file up
separately and deliberately.

Default: daily, keep 14, both overridable. Restore is `gunzip` and point
`--db` at the result — tested end to end, including chain verification.

## 9. Logging

Everything goes to **journald**, which already does rotation and retention — so
Solvent never writes an unbounded log file of its own. Set size limits in
`journald.conf`, not in the unit.

The runtime's in-memory log is bounded to the last 200 lines on purpose: a
process that runs for months must not accumulate its own history. Durable
narrative belongs in the **audit log**, which is a different thing from
operational logging and is never rotated away.

**Secrets never reach either.** Verified by probe: with a real key set, the key
appears in neither the audit log, the runtime log, the startup report, nor the
database file. Approvals record a `key_id` digest, never the key.

## 10. Egress — the control that must be real

`solvent/egress.py` installs a CPython audit hook. It is genuine defence in depth
and it caught a real bypass attempt while these tests were being written — but
**it is not the production boundary**, because a `ctypes` call straight to libc
goes under Python entirely.

The production boundary is the kernel. The unit carries:

```ini
# IPAddressDeny=any
```

**commented out, on purpose.** Uncommenting it, plus one `IPAddressAllow` per
approved destination, is what seals the host — and that is an owner act on a real
host, not something to claim here. Until that line is live, this file is a
template, not enforcement. A commented line that says what it is beats an
uncommented one that was never tested. See
`docs/solvent-egress-deployment-contract.md`.

## 11. The kill switch survives everything

HALT lives in the Policy database, and `operating_mode` **defaults to HALT when
unset**. So:

- a service restart does not clear it
- a host reboot does not clear it
- starting the runtime does not clear it — tested across three consecutive restarts
- a corrupted or missing policy value fails *to* HALT, not to NORMAL

`solvent resume` is the only way back, and it is an owner action.

## 12. When the owner's laptop is offline

Nothing about Solvent changes. It keeps doing what Policy already authorised:
discovery on its interval, payment reconciliation, health checks. It continues
jobs already approved.

When something needs the owner, it **stops and waits** in `BLOCKED / OWNER`.
It does not guess, does not proceed on a lower bar, and does not expire the
question. When the owner comes back, the state is exactly where they left it and
`solvent health` says what is waiting.

The one thing that genuinely requires the owner is a **signed approval**, and
that is correct: the signing key is the owner's, and consequential actions fail
closed without it.

## 13. Remote control — deliberately not built

The owner will eventually want to approve from a phone. That needs an
authenticated channel, and **there is no unauthenticated admin panel and no port
open to the internet**. Building one now would be the single largest attack
surface in the system, added before there is any revenue to protect.

For now, remote administration is **SSH to the host** and the existing CLI. That
is authenticated, encrypted, needs no new code, and has no listening service of
Solvent's own. A proper remote channel is a later phase with its own threat
model.

The only inbound path that will eventually be required is the **Stripe webhook**,
and its contract already exists: TLS, signature verification, `v1` only,
constant-time comparison, replay tolerance, event-id deduplication, and — because
a valid signature proves origin and nothing else — semantic validation *after*
authentication: job association, currency, amount ceiling, and positive value.

## 14. Minimum host

| | |
| --- | --- |
| OS | Any current Linux with systemd |
| CPU | 1 vCPU for Solvent itself |
| RAM | 512 MB for Solvent itself |
| Disk | 5 GB, plus the model |
| GPU | **Not required** |
| Network | Outbound only. **No inbound** until the Stripe webhook is needed |

Solvent is a bookkeeper: it waits, writes small rows and makes occasional calls.
The cost is the **model**, not Solvent:

| Model runtime | RAM | Disk | Practical? |
| --- | --- | --- | --- |
| `qwen2.5:14b-instruct` (Q4_K_M) local | ~12 GB | 9 GB | CPU inference works but is slow — minutes per response |
| A smaller cleared local model | ~4 GB | 3 GB | Comfortable on a modest host |
| Hosted API | 512 MB | — | Cheapest host, but a new dependency and a new credential |

**This is the real sizing decision, and it is the owner's.** A host that runs
Solvent is nearly free. A host that runs a 14B model locally is not. The three
options differ in cost, latency and privacy, not in architecture — Solvent's
business logic is identical under all of them.

Note `ROFLO_MODEL` in the environment file: it must name the artifact cleared
under OD-3. A different tag there is a silent downgrade, and `solvent health`
refuses it (see `docs/solvent-model-rights-evidence.md`).

## 15. Deploy

```bash
# On the always-on host, as a user with sudo:
git clone <repo> solvent && cd solvent
git checkout claude/solvent-architecture-audit-8ot9et

sudo ./deploy/install.sh          # user, directories, venv, unit, env template
sudo -e /etc/solvent/solvent.env  # fill in: it is empty on purpose

sudo systemctl enable --now solvent
```

Then, and these are different questions:

```bash
systemctl status solvent      # is the process alive?
solvent health                # is Solvent able to do anything?
journalctl -u solvent -f      # what is it doing?
```

`systemctl status` answers the first. Only `solvent health` answers the second —
a running process that is HALTED, RECOVERING or NOT_READY is alive and correctly
doing nothing.

Backups:

```bash
sudo crontab -e
0 3 * * *  /opt/solvent/deploy/backup.sh /var/backups/solvent
```

**One-command operations.** Process lifecycle is systemd's job and is not
duplicated:

| Intent | Command |
| --- | --- |
| start / stop / restart / status | `systemctl {start,stop,restart,status} solvent` |
| run in the foreground | `solvent run --always-on --db …` |
| health | `solvent health` |
| readiness for a first paid job | `solvent readiness` |
| halt / resume | `solvent halt` / `solvent resume` |
| reconcile after a crash | `solvent recover` |

## 16. Upgrades

Deliberate, never automatic. **Solvent does not self-update its governance code**
— there is no code path that fetches or executes anything.

```bash
./deploy/backup.sh            # 1. back up first
systemctl stop solvent        # 2. stop
git -C /opt/solvent-src pull  # 3. fetch deliberately, then review the diff
sudo ./deploy/install.sh      # 4. re-install (additive migrations run at startup)
solvent health                # 5. verify BEFORE starting
systemctl start solvent       # 6. start
```

Rollback is `git checkout <previous>`, re-install, restore the backup if a
migration ran. Migrations are **additive only** — no column is dropped, renamed
or retyped, and no row is rewritten, so an append-only table stays append-only
and an older database opens under a newer Solvent.

## 17. What this does not do

Stated so nobody mistakes the scope:

- **No production activation.** `simulation_only` is still on, the allowlist is
  still empty, no real client work can happen.
- **No host was provisioned, no account created, no money spent.**
- **`IPAddressDeny=any` is commented out** and must be turned on deliberately.
- **No remote control channel** beyond SSH.
- **No high availability.** One host. If it dies, Solvent is down until it comes
  back — and the ledger survives, because that is what backups are for.
