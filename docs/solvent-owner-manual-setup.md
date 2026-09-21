# Solvent — owner setup, step by step

**Who this is for:** you, the owner. It assumes you have never used Linux, do
not know what an environment variable is, and have never configured a webhook.
None of that is needed. Where a step needs a term explained, it is explained.

**What this gets you:** a first *supervised* trial — one client you found
yourself, one CSV job, done and checked by Solvent, and handed back to you to
send. Solvent sends nothing and charges nobody. You stay in the loop for every
outward action.

**Three rules that never change.**

1. **You never paste a secret into a chat window** — not into Claude, not into
   ChatGPT, not into a support ticket, not into a GitHub issue. Nothing in this
   guide asks you to.
2. **You never put a secret in the project folder.** Secrets go in one file,
   `/etc/solvent/solvent.env`, which is outside the project and is excluded from
   backups on purpose.
3. **If a step feels risky, it isn't your job to judge that.** Solvent's own
   checks decide whether work is correct or safe. Your job is the things only
   you can decide: who you are, your money, your credentials, your permission.

---

## What you need, and what each thing is

| # | What | Is it secret? | Do you need it for the first trial? |
|---|---|---|---|
| 1 | **Owner key** — a long random password Solvent uses to confirm approvals really came from you | **SECRET** | **Yes** |
| 2 | **Your legal name** — the name you contract under | Public business information | **Yes** |
| 3 | **Your contact email** — where a client reaches you | Public business information | **Yes** |
| 4 | **Your postal address** | Private configuration | No — only when you issue an invoice |
| 5 | **Tax reference** | **HIGHLY SENSITIVE** | No. And Solvent never stores the number — see below |
| 6 | **Stripe keys** | **SECRET** | No — only when you want Solvent to *verify* payments itself |

Steps 1–3 are the whole list for a first trial. Steps 4–6 are later.

### Why Solvent needs each one

- **Owner key.** Anything that can approve spending must be able to prove the
  approval came from you. Without the key Solvent refuses every consequential
  approval — that is the safe direction, not a fault.
- **Legal name and email.** When a client asks "who am I contracting with?",
  Solvent must answer truthfully. It will never invent a name. Until you give
  it one it says, honestly, that it acts for an individual it cannot name.
- **Address.** Only appears on an invoice. Not collected before then.
- **Tax reference.** Solvent records only **that you have one** — never the
  number itself. Nothing in Solvent ever prints it, and the record it keeps
  cannot be erased later, so keeping the number would be a permanent copy of a
  sensitive identifier for no benefit. Give the number to Stripe directly, in
  Stripe, where it is actually needed.

---

## Where each value is stored, and who can see it

| Value | Stored where | Survives restart | In the audit log | In logs | In backups | Workers can read it | Can appear to a client |
|---|---|---|---|---|---|---|---|
| Owner key | `/etc/solvent/solvent.env` only | Yes (the file) | **No** | **No** | **No** — excluded on purpose | **No** | **No** |
| Stripe webhook secret | `/etc/solvent/solvent.env` only | Yes (the file) | **No** | **No** | **No** | **No** | **No** |
| Legal name | Solvent's database | Yes | Yes | Possibly | **Yes** | Yes | **Yes** — that is its purpose |
| Email | Solvent's database | Yes | Yes | Possibly | **Yes** | Yes | Yes |
| Address | Solvent's database | Yes | Yes | Possibly | **Yes** | Yes | On an invoice |
| Tax reference | **Nowhere.** Only the word `PROVISIONED` | n/a | Presence only | No | Presence only | No | **No** |

The two secrets are in one file and nowhere else. Your business identity is in
the database, which **is** backed up — that is correct, because it is business
information you would not want to lose, but it is worth knowing.

---

## Step 1 — The owner key

**What this is.** A long random password. You generate it, store it, and never
look at it again. You do not memorise it and you do not type it anywhere except
the one file below.

**Where you do this.** On the computer that runs Solvent. Open a terminal — on
most Linux servers you reach it with `ssh`; if someone set the server up for
you, this is the black window with a text prompt.

### 1a. Generate it

Type this exactly and press Enter:

```
python3 -c "import secrets; print(secrets.token_hex(32))"
```

You will see a long line of letters and numbers. **That is your owner key.**

> Do not send that line to anyone. Do not paste it into a chat. It will be on
> your screen — that is fine; nobody else is looking at your terminal.

### 1b. Put it in the secrets file

Open the secrets file in a simple text editor:

```
sudo nano /etc/solvent/solvent.env
```

(`sudo` means "as administrator". `nano` is a basic editor: arrow keys to move,
no mouse.)

You will see a line that reads:

```
SOLVENT_OWNER_KEY=
```

Put your cursor at the end of that line and paste the long value after the `=`,
with **no spaces** and **no quotation marks**:

```
SOLVENT_OWNER_KEY=<GENERATED_OWNER_KEY>
```

Save and exit: press **Ctrl+O**, then **Enter**, then **Ctrl+X**.

### 1c. Set who can read the file

```
sudo chown root:solvent /etc/solvent/solvent.env
sudo chmod 0640 /etc/solvent/solvent.env
```

**What this means.** `0640` means: the administrator can read and change it, the
Solvent service can read it, nobody else can touch it.

> **Do not use `0600`.** That looks stricter, and it is — too strict. Solvent
> runs as its own user, and with `0600` it could not read its own key. Solvent
> would start and report every credential as missing.

### 1d. Restart Solvent so it picks up the new file

```
sudo systemctl restart solvent
```

### 1e. Check it worked

```
solvent setup check
```

**What you should see**, on the first line:

```
  OWNER_KEY                OK       present and valid (SOLVENT_OWNER_KEY)
```

**If you see `ACTION` instead**, the message says exactly what is wrong — for
example that the key is too short, or that it is still a placeholder. It never
shows you the value.

> **This command never prints your key**, or any other secret. It only reports
> whether one is present and usable. It is safe to screenshot.

**Rotating it later.** Generate a new one and repeat 1a–1d. Approvals issued
under the old key stop working, which is the point.

---

## Step 2 — Your contracting details

**What this is.** Telling Solvent the name you do business under and how a
client reaches you. Two values. No secrets.

Run this, putting your own details between the quotation marks:

```
solvent setup contracting \
  --legal-name "<YOUR_LEGAL_NAME>" \
  --email "<YOUR_CONTACT_EMAIL>"
```

**What you should see:**

```
contracting structure: INDIVIDUAL
  [OK ] CLIENT_AGREEMENT: complete
  [NEED] INVOICE: address
  [NEED] PAYMENT_RAIL: address, tax_reference
```

`CLIENT_AGREEMENT: complete` is the line that matters. The other two are
telling you what is *not yet* needed — an address when you first issue an
invoice, and a tax reference when you connect Stripe.

**Later, when you need those:**

```
solvent setup contracting --address "<YOUR_CONTRACTING_ADDRESS>"
solvent setup contracting --tax-reference-provisioned
```

> The tax-reference option deliberately **takes no value.** There is nowhere to
> type the number, because Solvent does not want it. It records only that you
> have one.

---

## Step 3 — Everything else is already prepared

These three were done for you. Each is one command, they take no secrets and no
personal information, and they are here so you can run them if you are setting
up a fresh machine.

```
solvent setup capability     # register the CSV service you approved
solvent setup work-source    # allow you to hand Solvent one job at a time
solvent setup model          # record the licence clearance for the AI model
```

**What you should see** — one confirmation line each, for example:

```
registered csv-cleanup/1.0 as proven, covering 11 certified check(s).
```

**What each one does.**

- **capability** — records that the CSV cleanup service is approved for sale. It
  re-runs the verification battery first, so if the evidence no longer supported
  the approval it would refuse rather than register.
- **work-source** — lets you hand Solvent a client you found yourself. It does
  **not** let Solvent browse job boards, bid, or contact anyone.
- **model** — records that the AI model Solvent runs is licensed for paid work.
  The licence research is already done and written up in
  `docs/solvent-model-rights-evidence.md`; this just applies the finding.

---

## Step 4 — Check everything at once

```
solvent setup check
```

**What a finished first-trial setup looks like:**

```
Owner configuration
======================================================================
  OWNER_KEY                OK       present and valid (SOLVENT_OWNER_KEY)
  CONTRACTING_IDENTITY     OK       INDIVIDUAL, complete for a client agreement
  STRIPE                   OK       ENGINEERING_READY; webhook secret not set
                                    (not required until a payment must be collected)
  MODEL                    OK       1 artifact(s) cleared
  WORK_SOURCE              OK       1 approved: owner_entered
  CSV_CAPABILITY           OK       csv-cleanup/1.0
  CLIENT_COMMUNICATION     OK       human relay — every reply is drafted and
                                    needs owner approval to send
  SIMULATION_ONLY          OK       ON — no real external effects
  HALT                     OK       not engaged
----------------------------------------------------------------------
  FIRST_TRIAL_READINESS    READY   (supervised, human-relayed, one hand-fed job)
  FULL_ACTIVATION          BLOCKED   (autonomous delivery and payment collection)
      - OD-2 activation: the chosen rail has no credentials or webhook secret
      - external execution is fail-closed (egress.simulation_only)
```

**`FIRST_TRIAL_READINESS: READY` is the line you are looking for.**

`FULL_ACTIVATION: BLOCKED` is **expected and correct** at this stage. It means
Solvent still cannot send anything or take money by itself. For a supervised
trial it does not need to — you send the file and you take the payment.

---

## Running the first job

1. **You find the client and agree the work.** Solvent does not look for work.
2. **You put their CSV file on the server** and start the job. Solvent prices
   it, plans it, does it, and checks its own output against every requirement.
3. **If something in their file is ambiguous** — a date that could mean two
   different days — Solvent stops and writes a question for you to pass on. It
   does not guess.
4. **When the work is done**, run:

```
solvent relay
```

You will see something like:

```
job job_4c84cd2f…  [AWAITING_PAYMENT]
  client        client:hand-fed
  verification  PASSED — every requirement satisfied
  deliverable   /var/lib/solvent/.../cleaned.csv
                2114 bytes, digest sha256:9f2c…
  message to    CLIENT
      | Your cleaned file is attached. …
  READY_FOR_HUMAN_RELAY — send the file above, then record
  payment when it arrives. Solvent sends nothing itself.
```

5. **You send the file and the message yourself** — your own email, as you
   normally would.

> **You are not being asked to check whether the file is correct.**
> `verification  PASSED` means Solvent already re-read the delivered file from
> disk and confirmed every agreed requirement, independently of the code that
> produced it. If it had not passed, this view would say **DO NOT SEND** and the
> job would not be offered to you at all.

6. **You take payment however you normally take payment.** For a first trial
   Solvent does not need Stripe. The job will sit at `AWAITING_PAYMENT` until a
   verified payment arrives, which is simply the truth.

---

## Later — Stripe (only when you want Solvent to verify payments itself)

**You do not need this for the first trial.** Read this when you want Solvent to
know, by itself, that a client has paid.

### What you must understand first

Solvent **cannot receive a webhook directly.** It is deliberately built so that
it cannot open a network port at all — that is one of its containment controls,
not a missing feature. So you cannot point Stripe at Solvent.

Instead: something else on the server receives the webhook and writes it to a
folder, and Solvent reads that folder. The folder is:

```
/var/lib/solvent/payments-inbox
```

Setting up that receiver is a small technical task — it is the one step in this
guide worth asking a developer for. It is a few lines of code, and what it must
do is written down in `solvent/payments.py`.

### What you do in Stripe

1. In the Stripe dashboard, go to **Developers → Webhooks** and add an endpoint
   pointing at your receiver (not at Solvent).
2. Stripe shows you a **signing secret** starting `whsec_`. This is a **SECRET**.
3. Put it in the same secrets file, on the line that already exists:

```
SOLVENT_STRIPE_WEBHOOK_SECRET=<STRIPE_WEBHOOK_SECRET>
```

4. Restart: `sudo systemctl restart solvent`
5. Record that you have done it:

```
solvent setup stripe CONFIGURED --evidence "webhook secret installed on the server"
```

### The one detail that will otherwise waste your afternoon

When you create the payment in Stripe, you must attach the Solvent job id to it
as **metadata**, under exactly this key:

```
solvent_job_id
```

A payment without it is refused as unattributable — Solvent will not guess which
job a payment belongs to. The refusal is recorded, so you will see it.

### Checking it

```
solvent payments ingest
```

Each delivery is reported as applied or refused, with the reason. Your secret is
never printed. A delivery that fails verification is kept in a `rejected` folder
rather than deleted, so you can look at it.

Only after you have seen a real payment verified should you run:

```
solvent setup stripe LIVE_VERIFIED --evidence "observed a real payment arrive and verified"
```

---

## What must never leave your machine

**Never** put these in the project folder, a commit, a chat window, a screenshot
you share, a support ticket, or an email:

- the owner key
- the Stripe secret key or webhook signing secret
- your tax identifier
- any password, token or private key

**Safe to share:** anything `solvent setup check`, `solvent readiness` or
`solvent relay` prints. None of those commands print a secret value.

If you think a secret has been exposed: generate a new one and repeat the
install steps. For Stripe, roll the secret in the Stripe dashboard.

---

## What is still not possible after all of this

Deliberately, and until you separately decide otherwise:

- Solvent will not look for work, bid, or contact anyone.
- Solvent will not send a message or deliver a file by itself.
- Solvent will not charge anyone.
- Solvent will not sell anything except CSV cleanup, within the exact set of
  operations that has been tested.
- Solvent will not treat a client's claim to have paid as payment.

`SIMULATION_ONLY: ON` stays on until you turn it off in a separate, deliberate
step. Nothing in this guide turns it off.
