# Letting Solvent reach you

What Solvent needs so it can tell you something when you are not looking at the
website, where each value goes, and how to check it works without ever showing
a secret.

**Nothing in this document asks you to type a private value into the website, or
into a chat, or into anything that keeps a copy.** Every value goes into one
file on the machine that runs Solvent.

---

## 1. What you will need

| You need | Where it goes | Is it secret? |
|---|---|---|
| Your mobile number | `SOLVENT_OWNER_PHONE` in the runtime's environment file | **Private.** Never in Git, never on a page in full |
| An SMS provider account | The provider's own dashboard | The account is yours |
| The SMS provider's API credential | `SOLVENT_SMS_CREDENTIAL` | **Secret.** Treat it like a password |
| A voice provider (optional) | usually the same provider | — |
| The voice credential | `SOLVENT_VOICE_CREDENTIAL` | **Secret** |

You do not need any of this for a first supervised job. Solvent will tell you
everything on the website. Notifications matter once you stop sitting in front
of it.

---

## 2. Where the values go

One file, on the machine that runs Solvent:

```
/etc/solvent/solvent.env
```

It should be owned by `root`, readable by the `solvent` group, and readable by
nobody else:

```
sudo chown root:solvent /etc/solvent/solvent.env
sudo chmod 0640 /etc/solvent/solvent.env
```

Add the lines you need. **Replace the placeholders with your own values** —
these are placeholders, not examples to copy:

```
SOLVENT_OWNER_PHONE=<YOUR_MOBILE_NUMBER_IN_E164>
SOLVENT_SMS_CREDENTIAL=<YOUR_SMS_PROVIDER_CREDENTIAL>
SOLVENT_VOICE_CREDENTIAL=<YOUR_VOICE_PROVIDER_CREDENTIAL>
```

E.164 means the international form: a plus sign, the country code, then the
number, with no spaces or brackets.

Then tell Solvent that a number is present. This records a **mask** and the
provider names, and never the number itself:

```
solvent setup notifications --sms-provider <PROVIDER_NAME> \
                            --voice-provider <PROVIDER_NAME>
```

---

## 3. What is stored where

This matters, so it is worth being precise.

| Value | Stored in the database? | On a page? | In the audit log? |
|---|---|---|---|
| Your full phone number | **No** | **No** | **No** |
| A mask of it (`***-***-1234`) | Yes | Yes | No |
| Provider names | Yes | Yes | Yes |
| Provider credentials | **No** | **No** | **No** |

The full number and the credentials exist only in that one file and in the
memory of the process that sends. The website reads the database and cannot see
them — which is deliberate, because the website is the part most likely to be
reached from outside.

The mask exists so you can confirm Solvent has the right number without the
number being in a place that gets backed up, copied and read.

---

## 4. Choosing a provider

Solvent does not care which company carries the text. It talks to an **adapter**,
and the adapter talks to the provider.

That is on purpose: wiring one company's API into the escalation rules would
make a price change into an architecture problem. Adding a provider means
writing one small adapter with one method; it means changing nothing about how
Solvent decides *when* to notify you.

**Nothing has been purchased, and no account has been created.** Choosing and
paying for a provider is yours to do.

---

## 5. What gets sent, and when

| Level | Where it goes |
|---|---|
| **INFO** | The website only. Routine things never reach your phone |
| **ACTION_REQUIRED** | Website + a text |
| **URGENT** | A text immediately. If you do not acknowledge it within the interval, a phone call |
| **CRITICAL** | A text and a phone call |

The interval is a business decision, not a constant in the code, so it lives in
policy and you can change it:

```
solvent setup notifications --escalate-after-minutes 15
```

Things that will call you: the audit chain failing to verify, an unsafe firewall
posture, repeated crashes, an external action left uncertain, a payment anomaly,
a database integrity failure.

Things that will not: a job finishing, a client saying thank you, a health check
passing.

---

## 6. What a message will never contain

A text is read off a lock screen, relayed through carriers, and backed up to
whatever your phone syncs with. So a message says **what happened and where to
look**, and nothing else.

It will never contain your signing key, a payment credential, a password, a tax
identifier, a client's data, an artifact's contents, or a figure.

It looks like this:

> Solvent: audit.chain_broken. The hash chain does not verify — open the control
> centre for details.

The details are on the website, behind your password.

---

## 7. Receiving a message approves nothing

This is worth saying plainly, because the convenient version of this feature is
dangerous.

**There is no reply-to-approve.** You cannot text back "YES" to release a
payment, promote a skill or clear a halt, and that is not an omission to be
filled in later. SMS is an unauthenticated channel: anyone who can spoof a
sender can write to it. A business that can be authorised by text message is a
business behind a sender-id check.

Consequential actions are approved at a terminal, by a process holding your
signing key:

```
solvent intents list
solvent intents approve <intent-id> --reason "..."
```

Acknowledging a notification records that you have *seen* it, and stops the
escalation. It authorises nothing.

---

## 8. Testing it without sending anything real

Until you deliberately configure a provider, Solvent uses one that **records
what would be sent and sends nothing**. That is also what the test suite uses,
deliberately: a test suite that can place a phone call will place one, during a
rerun nobody is watching, at three in the morning.

So you can check the whole path safely:

```
solvent setup check
```

Then open **Notifications** on the website. You should see:

- Owner phone: `***-***-1234` — the last four digits of your number
- SMS provider: whatever you named
- Escalate after: your interval

If the phone line is empty, the environment file is not being read by the
service. Check the file's permissions and that `EnvironmentFile` points at it.

When you do configure a real provider, most have a test or sandbox mode. Use it
first. A text you sent to yourself on purpose is the cheapest possible proof
that the path works.

---

## 9. Checking it without showing a secret

```
solvent doctor
```

Reports whether each value is **present**, never what it is. There is no command
that prints your number or a credential back to you, and that is intentional: a
command that can print a secret is a command that can put a secret in a
terminal's scrollback, a screenshot or a support ticket.

If you need to know what a value is, read the file you put it in.
