# When Solvent has a bad day

Plain English. No commands until the last section.

---

## 1. What an incident is

An **incident** is a failure Solvent recorded about itself.

Not an error message printed to a screen and lost. A durable record with an
identity, a history, and a place on the website. If Solvent fails at three in
the morning and you look on Tuesday, the incident is still there, and it says
what it was doing when it failed.

You will find them under **Incidents**.

---

## 2. What Solvent remembers

For every failure worth recording:

| It records | So that |
|---|---|
| Where it started — which component, which operation | You know where to look |
| What it was doing — the job, the client, the trigger | You know what was affected |
| The last safe checkpoint it reached | A restart can resume from a known place |
| Whether any external action became **uncertain** | See section 6. This is the one that matters most |
| What it did about it, and whether that worked | You know if it is still happening |
| The root cause, once established | Not a guess — see section 4 |
| The fix, and **how the fix was proven** | See section 5 |
| How it will be prevented next time | The same failure should not be new twice |
| How many times this has happened | See section 3 |

It deliberately does **not** record secrets. Your signing key, payment
credentials, passwords and phone number are removed as the record is written,
not when it is displayed — because a record that already contains a secret has
already been copied into backups.

---

## 3. "Has this happened before?"

Every failure gets a **fingerprint**: a short identity for that *kind* of
failure.

The fingerprint deliberately ignores the parts that change every time — the job
id, the file path, the exact number of seconds something took. Two failures that
are really the same problem get the same fingerprint, so the website can tell
you "this is the fourth time" instead of showing you four unrelated-looking
problems.

The incident page answers it directly:

- **NEW FAILURE CLASS** — nothing like this has been recorded before.
- **SEEN BEFORE** — with the earlier incidents listed, and what was learned.
- **SEEN BEFORE — and a verified fix was supposed to prevent it.** This is a
  **regression**, and it is worse news than a new failure: something that was
  fixed and proven has come back.

---

## 4. What Solvent does automatically, and what it will not

**It will**, without asking:

- record the failure
- retry something that has no effect on anyone
- resume from the last safe checkpoint
- stop calling a dependency that keeps failing (see section 7)
- rebuild a projection it can recompute
- tell you

**It will never**, whatever the failure:

- weaken the firewall
- change a rule, a spending limit or a permission
- promote a skill, or change which AI model is cleared
- send money, or retry an action that may already have sent money
- erase or edit the audit record
- turn off verification
- decide it is allowed to do something it was not allowed to do before

That last line is the whole design. **Repeated failure is not permission to
bypass a control.** A system that gets more powerful the more it breaks is a
system that breaks its way out of its own governance.

---

## 5. Why "it restarted fine" is not a fix

An incident is only **VERIFIED** when the evidence is named — a test that
reproduces the original failure and now passes, for instance.

Restarting successfully is not that. A process that came back up has
demonstrated that it can start, which is the one thing nobody doubted.

So the statuses run:

- **DETECTED** — it happened.
- **CONTAINED / RECOVERING / RECOVERED** — Solvent did something about it.
- **ROOT_CAUSE_PENDING** — nobody knows why yet.
- **FIX_PENDING** — the cause is known, the fix is not written.
- **FIXED** — a fix is recorded. *Still not proven.*
- **VERIFIED** — the fix has evidence behind it. Only now is the lesson kept
  and offered the next time something like this happens.
- **RECURRED** — it came back.
- **OWNER_ACTION_REQUIRED** — it needs you.

An unverified fix is exactly the one that should **not** be offered as advice to
the next person hitting the same failure, which is why the lesson is written at
VERIFIED and not before.

---

## 6. "Uncertain external action" — read this one

Some actions leave the building. Sending a client their file. Charging a card.

If Solvent crashes *after* starting one of those and *before* recording that it
finished, then nobody knows whether it happened. The client may or may not have
the file. The card may or may not have been charged.

Solvent does not guess, and **it does not retry**. Retrying a payment that may
already have gone through is how a client gets charged twice.

Instead it records the action as **uncertain**, stops, and asks you. The
incident page has a line for it: *"Did any external action become uncertain?"*

If that line says anything other than "nothing recorded as uncertain", go and
check the other system — the payment provider, the sent folder — before doing
anything else.

This is the honest position. Solvent does not claim that every action happens
exactly once, because it cannot: the moment between "I did it" and "I wrote down
that I did it" cannot be made to not exist. What it promises is **at most
once**, with the uncertainty surfaced rather than papered over.

---

## 7. Circuit breakers

If a dependency fails repeatedly — the AI model provider, the payment webhook,
a work source — Solvent stops calling it for a while. That is a **circuit
breaker**, and you will find it under **Circuit breakers**.

- **CLOSED** — normal. Calls go through.
- **OPEN** — contained. Solvent has stopped calling it, and will try again after
  a cooldown.
- **HALF-OPEN** — the cooldown has passed and exactly one test call is allowed.

A breaker **contains** a failure. It does not open a door. It cannot send the
work somewhere else, to a different provider or a different model, because that
would be a permission decision and containment is not allowed to make one. If
the only cleared model is down, Solvent waits — it does not quietly use an
uncleared one.

---

## 8. Canary recovery

When a breaker's cooldown passes, Solvent does not throw the full workload back
at something that has been broken for ten minutes. It tries **one** safe
operation first — the canary.

- Canary passes → the breaker closes and normal work resumes.
- Canary fails → the breaker opens again and the cooldown restarts.

This is why recovery looks slow. It is slow on purpose: a service that is
flapping between working and not working will produce a retry storm if you let
it, and a retry storm is its own incident.

---

## 9. What you will actually be asked to do

Most incidents need nothing from you. The ones that do will say so, in the
**Owner action** line on the incident page, and — if you have set up
notifications — by text.

The three that always need you:

1. **The audit chain does not verify.** The record of what happened can no
   longer be trusted. Stop work and investigate.
2. **An external action is uncertain.** Go and look at the other system.
3. **A regression.** Something that was fixed and proven has come back, which
   means the fix was wrong or something undid it.

---

## 10. The commands

```
solvent health                    # is it working right now?
solvent doctor                    # measured enforcement, not intentions
```

Everything else is on the website, under **Incidents**, **Diagnostics** and
**Circuit breakers**.
