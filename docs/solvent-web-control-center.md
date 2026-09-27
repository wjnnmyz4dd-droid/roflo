# The owner control centre

A website you sign into that shows what Solvent is doing and lets you act where
acting is genuinely yours to do. It is a **window on Solvent**, not a second
Solvent — and this document explains what that buys you, and what it costs.

Start it with `solvent web`. Set a password first with `solvent web-password`.

---

## 1. The one thing worth understanding

**The control centre cannot do most of what Solvent can do, and that is the
point.**

Constructing Solvent's authorities installs an audit hook that denies
`socket.bind` for the whole process, permanently. A web server must bind a port.
So the website physically cannot share a process with the Ledger, the Policy
Store, the Action Gate or the Owner Channel.

That forced separation is the security design. Assume somebody takes over the
website completely. They can:

- read your business data;
- queue *requests*.

They cannot send a message, charge anybody, promote a capability, rewrite policy,
clear HALT, edit the audit log, or read a secret. Not because the UI hides those
buttons — because there is no code path, the database is open read-only, and the
actions that matter need an approval signed with a key the website never holds.

**What you lose if the website is compromised:** confidentiality of your business
data, and a nuisance (somebody could halt your business). That is a real loss,
stated plainly rather than designed away.

---

## 2. What each page is for

| Page | The question it answers |
|---|---|
| **Command centre** | What needs me, and then what is happening? |
| **Jobs** | Every job, its state, price, blocker and next step. |
| **Clients** | One client's work, their messages, and nothing of anyone else's. |
| **Files** | Every deliverable Solvent produced — named, never served. |
| **Work sources** | Where work may come from, and at which of the four stages. |
| **Skills Lab** | What Solvent wants to do, is building, and has proven. |
| **Capabilities** | What it is allowed to sell, on what evidence. |
| **What it learned** | Every lesson, beside the grade of evidence it came from. |
| **Customer service** | What a client said, and what is drafted for you to send. |
| **Approvals** | Decisions that are genuinely yours. Nothing routine. |
| **Money** | Collected, outstanding, and simulated — kept strictly apart. |
| **AI model** | Which exact artifact is cleared, by digest. |
| **Security** | What is closed, and what a compromise would reach. |
| **Audit** | Every consequential action, in plain words. |
| **System health** | Is it working? |
| **Owner setup** | What you still need to provide, and what you do not. |

Every page reads from the authority that owns the fact. None of them recomputes
anything: a page that calculated profit differently from the Ledger would be a
second financial authority, and the first time they disagreed nobody would know
which was right.

---

## 3. Money is never overstated

Three figures, deliberately separate:

- **Collected** — a payment was cryptographically verified. This is revenue.
- **Outstanding** — invoiced and not yet collected.
- **Simulated** — fixture money from tests. Counted in **nothing** above, shown
  with a `SIMULATED` label wherever it appears.

Showing fixture revenue as revenue would be the most consequential lie this
surface could tell, because you would act on it.

---

## 4. You are not the quality control

Whether a deliverable is correct is decided by verification, independently of the
code that produced it, against the requirements that were agreed. The website
reports that verdict; it does not ask you to form one.

What is actually yours:

- **Authority** — may Solvent do this at all?
- **Money** — may it spend this?
- **Identity** — who does it contract as?
- **Scope** — may it start doing something new?

Anything the existing governance already decides is not put in front of you.
`Approvals` is the whole list, in one place.

---

## 5. How an action from the website actually happens

You press a button. The website writes an **intent** — a request — into a spool
directory. The Solvent runtime reads it and acts under its own authorities.

**Safe intents** are executed. Their worst outcome is Solvent doing *less*:
halting, acknowledging an alert, abandoning a proposed skill. Stopping is the
safe direction, so a stolen session that halts your business is a nuisance and
not a loss. Halting still asks for your password again, because a stolen session
is not you.

**Consequential intents wait for you.** Promoting a capability, clearing HALT,
retiring a version, moving the payment rail along: the website can ask, and it
cannot do. It does not hold the key that mints an owner approval, which is the
whole reason taking over the website does not take over the business.

You answer them at the terminal:

```
solvent intents list                              # what is waiting, and what each needs
solvent intents run                               # carry out the safe ones
solvent intents approve int_1234 --reason "..."   # approve exactly one
solvent intents reject  int_1234 --reason "..."   # refuse one, on the record
```

Three things are **not** completed by approving a file, even with a valid
approval, and `solvent intents list` tells you the command to use instead:

| Request | Why not | Type instead |
|---|---|---|
| Change who you contract as | Your legal name and address are personal data, and this would route them through a file the web process can read back | `solvent setup contracting` |
| Authorise a work source | An adapter that fetches from a platform is code, not a row in a form | `solvent setup work-source` |
| Leave simulation | This is the single change that turns a rehearsal into a business that spends money and contacts strangers. Approving a file is too easy a gesture for it | `solvent setup capability` |

**One approval authorises one intent.** The approval is bound by subject to the
single request you named, and the binding is inside the signature, so it cannot
be repointed. If three things are queued and you approve one, the other two are
still queued afterwards — not carried out, and not thrown away either.

Anything left waiting is announced in the Audit log the first time the runtime
sees it, so a request you never made is visible before you ever open the list.

---

## 6. Exposure — the part most likely to go wrong

The control centre binds **loopback** (`127.0.0.1`) by default. It is not
reachable from anywhere else until you deliberately make it so.

To reach it from outside, put a reverse proxy in front that terminates TLS, and
open only the proxy's port. Check your configuration before trusting it:

```
solvent web-exposure --host 127.0.0.1 --port 8765
```

It reports every way the binding could be wrong and what to do about each. An
**unrecorded** firewall posture is reported as **unsafe** — not because it
probably is, but because the host nobody checked is the host somebody forgot.

Set the firewall up and record it:

```
sudo ./deploy/firewall.sh              # dry run: shows the rules
sudo ./deploy/firewall.sh --apply
solvent setup firewall --default-deny --proxy-port 443 --tls-terminated
```

The firewall is default-deny inbound **and** outbound, on all three chains. It
does not open the control centre's port, because the site is reached through the
proxy.

### Running it as a service

`deploy/solvent-web.service` runs the control centre as its **own user**, which
is what makes "read-only" real rather than a promise in the code: the database is
mounted read-only for that service, the only writable path is the intent spool,
and outbound network is denied outright.

Its environment file (`/etc/solvent/solvent-web.env`) is deliberately a
*different file* from the runtime's. It carries the web password hash and
**neither the owner key nor any payment secret**. If you find yourself wanting to
add either, the thing you are trying to do belongs in the runtime.

---

## 7. Secrets

- The website **never displays a secret.** It reports `present` or `not
  configured`, never a value.
- There is no read-back path. Nothing in the surface can retrieve a stored
  secret, because nothing in the surface can read where they live.
- The password you set is stored only as a PBKDF2 hash, printed once by
  `solvent web-password` for you to paste into the environment file. The command
  prompts and does not echo, so the password is never in your shell history.
- Sessions live in memory only. Restarting the service signs you out, and there
  is no session store to steal.

---

## 8. If your browser or session is stolen

The blast radius, concretely:

| The attacker can | The attacker cannot |
|---|---|
| Read every business figure, job, client and message | Send anything to a client |
| Halt your business (a nuisance; needs your password) | Clear the halt |
| Queue a consequential request | Have it executed, or approve one |
| See which secrets are configured | Read any of them |
| See capability scope and evidence | Promote a capability |
| Read the audit log | Change one line of it |

Sessions expire after 30 minutes idle and 12 hours absolutely. Sign out to end
one immediately. If you think a session was stolen, restart the web service —
that invalidates every session, because they were only ever in memory.

---

## 9. What this does not do

- It does not accept file uploads. Client files reach Solvent through the
  runtime, not through the browser.
- It does not send anything to anybody. Every client message is a draft you
  relay.
- It does not run Solvent. If the website is down, the business is unaffected:
  jobs keep running, verification keeps holding, no limit disappears.
