"""Screening a job listing for the ways one turns out not to be a job.

This module produces **signals**. It decides nothing. It cannot reject an
opportunity, block a job, refuse a payment or change a rule, and it is not a
second Risk authority: Policy, the Financial Governor and the Action Gate keep
every consequential decision they already had. What this adds is evidence for
them to decide *with*, and a reason string the owner can read.

**Why signals and not verdicts.** A listing that asks for an up-front fee is
usually a fraud and occasionally a legitimate bonded-contract requirement. A
screening stage that rejected it outright would be wrong some of the time and
unappealable all of the time. One that reports "this asks for money before work
begins, and here is the sentence that says so" is right every time, and leaves
the decision where the authority for it already lives.

**Everything here reads attacker-authored text.** A job description is written
by whoever posted it, and some of them are written specifically to be read by
a system like this one. So the text is never executed, never followed, never
treated as configuration, and never allowed to carry an instruction. It is
matched against patterns and quoted back. :class:`~solvent.content.
UntrustedContent` is the structural guarantee; this is the part that notices
what the text is *trying to do*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- severities

#: Worth telling the owner. Not, on its own, a reason to walk away.
NOTE = "NOTE"
#: Materially raises the chance this is not real work.
CONCERN = "CONCERN"
#: Characteristic of a specific, known fraud, or an attempt to subvert
#: DeskPilot itself. Never auto-rejects; always surfaces.
SEVERE = "SEVERE"

_ORDER = {NOTE: 0, CONCERN: 1, SEVERE: 2}


@dataclass(frozen=True, slots=True)
class Signal:
    """One thing noticed, with the evidence that caused it."""

    code: str
    severity: str
    #: Owner-readable, and specific enough to act on.
    explanation: str
    #: The matched text, quoted. Never re-interpreted, never executed.
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "explanation": self.explanation, "evidence": self.evidence}


@dataclass
class Screening:
    signals: list = field(default_factory=list)

    @property
    def worst(self) -> str:
        return max((s.severity for s in self.signals),
                   key=lambda s: _ORDER[s], default=NOTE)

    @property
    def clean(self) -> bool:
        return not self.signals

    @property
    def severe(self) -> list:
        return [s for s in self.signals if s.severity == SEVERE]

    def codes(self) -> list:
        return sorted({s.code for s in self.signals})

    def to_dict(self) -> dict:
        return {"worst": self.worst if self.signals else "",
                "signals": [s.to_dict() for s in self.signals]}


# ------------------------------------------------------------------ patterns
#
# Each entry is (code, severity, explanation, regex). The explanation is what
# the owner reads, so it says what was seen and why it matters -- not a label.

_PATTERNS: tuple[tuple[str, str, str, str], ...] = (

    # --- money that flows the wrong way ----------------------------------
    ("ADVANCE_FEE", SEVERE,
     "the listing asks for money before the work starts. Legitimate clients "
     "pay for work; they do not charge for the chance to do it",
     r"\b(pay|send|transfer|deposit|wire|remit)\b[^.]{0,60}\b(fees?|deposits?|"
     r"upfront|up[- ]front|in advance|registration|activation|training|"
     r"onboarding|starter|membership)\b"),
    ("PURCHASE_REQUIRED", CONCERN,
     "the worker is expected to buy something before being paid",
     r"\b(buy|purchase|order)\b[^.]{0,50}\b(equipment|software|licen[cs]es?|"
     r"kits?|materials|gift ?cards?)\b"),
    ("FAKE_CHECK", SEVERE,
     "this is the overpayment fraud: a cheque or transfer arrives for too "
     "much, and the difference is wired back before the original bounces",
     r"\b(cash|deposit|clear)\b[^.]{0,60}\b(che(ck|que)|money order)\b"
     r"|\b(send|wire|return|forward)\b[^.]{0,40}\b(remaining|difference|"
     r"excess|overpay\w*|balance)\b"),
    ("MONEY_TRANSFER", SEVERE,
     "the task is moving other people's money, which is how a worker is used "
     "to launder it",
     r"\b(receive|accept|process|forward|transfer)\b[^.]{0,40}\b(payments?|"
     r"funds?|transfers?)\b[^.]{0,40}\b(on (my|our) behalf|to (another|a "
     r"third)|and (then )?(send|forward|wire))\b"
     r"|\bmoney mule\b|\bpayment processor? agent\b"),
    ("CRYPTO_PAYMENT", CONCERN,
     "payment is to be made in cryptocurrency to a named wallet, which is "
     "irreversible and unattributable",
     r"\b(bitcoin|btc|ethereum|eth|usdt|crypto(currency)?)\b[^.]{0,40}"
     r"\b(wallet|address|send|pay)\b|\bwallet address\b"),

    # --- off-platform pressure -------------------------------------------
    ("OFF_PLATFORM", CONCERN,
     "the listing pushes contact or payment off the platform, which removes "
     "the protections and the record the platform provides",
     r"\b(contact|message|reach|email|text|whatsapp|telegram|skype)\b"
     r"[^.]{0,40}\b(directly|off[- ]?(platform|site)|outside)\b"
     r"|\b(pay|payment)\b[^.]{0,30}\boutside (of )?(the )?(platform|site)\b"
     r"|\bavoid(ing)? (the )?(platform|site) fees?\b"),
    ("PLATFORM_RULE_BREAK", CONCERN,
     "the listing asks for something the platform's own rules forbid",
     r"\b(don'?t|do not|no need to) (tell|inform|report|mention)\b"
     r"[^.]{0,30}\b(platform|upwork|fiverr|site|support)\b"
     r"|\bagainst (the )?(platform|site) (rules|terms|policy)\b"),

    # --- identity and credentials ----------------------------------------
    ("CREDENTIAL_REQUEST", SEVERE,
     "the listing asks for credentials. No legitimate job requires the "
     "keys to the systems doing the work",
     r"\b(send|share|provide|give|need|require|paste|enter)\b[^.]{0,40}"
     r"\b(passwords?|api[ _-]?keys?|secrets?|access tokens?|private keys?|credentials?|"
     r"login details|2fa|two[- ]factor|seed phrase)\b"),
    ("IDENTITY_DOCUMENTS", CONCERN,
     "the listing asks for identity documents up front, which is how "
     "identity theft is sourced",
     r"\b(send|upload|provide|scan)\b[^.]{0,40}\b(passport|driver'?s "
     r"licen[cs]e|social security|ssn|national insurance|birth certificate|"
     r"bank statement)\b"),
    ("BANK_DETAILS", CONCERN,
     "the listing asks for banking details before any agreement exists",
     r"\b(send|provide|share|need)\b[^.]{0,40}\b(bank account|routing|"
     r"sort code|iban|account number|card number|cvv)\b"),

    # --- attacks on DeskPilot itself (§13, §44) --------------------------
    ("PROMPT_INJECTION", SEVERE,
     "the listing contains text addressed to an automated system, telling it "
     "to disregard its own rules. It is treated as evidence about the sender, "
     "never as an instruction",
     r"\b(ignore|disregard|forget|override|bypass)\b[^.]{0,40}"
     r"\b(previous|prior|earlier|above|your|all)\b[^.]{0,30}"
     r"\b(instructions?|rules?|prompts?|polic\w+|constraints?|guidelines?|systems?)\b"
     r"|\byou are now\b|\bnew instructions?:\b|\bsystem prompt\b"
     r"|\bact as (if|though)\b[^.]{0,30}\bno (restrictions?|rules?)\b"),
    ("EXECUTION_REQUEST", SEVERE,
     "the listing asks for a command to be run or a file to be executed, "
     "which is an attempt to use the worker as a way onto their machine",
     r"\b(run|execute|launch)\b[^.]{0,30}\b(this |the )?(scripts?|commands?|"
     r"shell|bash|powershell|\.exe|\.sh|binary|installers?)\b"
     r"|\bcurl\b[^.]{0,40}\|\s*(bash|sh)\b"
     r"|\b(download|fetch)\b[^.]{0,40}\b(and (run|execute|install))\b"),
    ("SECURITY_DISABLE", SEVERE,
     "the listing asks for a security control to be turned off",
     r"\b(disable|turn off|switch off|deactivate|uninstall)\b[^.]{0,40}"
     r"\b(security|antivirus|firewalls?|defender|protections?|sandbox|"
     r"safety|monitoring)\b"),
    ("POLICY_CHANGE", SEVERE,
     "the listing tries to change how approvals or payments work, which is "
     "the owner's decision and not a client's",
     r"\b(change|update|modify|set)\b[^.]{0,40}\b(owner|approval|payment)\b"
     r"[^.]{0,30}\b(polic\w+|details?|settings?|address|account)\b"
     r"|\b(no|without) (owner )?(approval|authorisation|authorization) "
     r"(is )?(needed|required)\b"),
    ("DATA_EXFILTRATION", SEVERE,
     "the listing asks for another client's material, which DeskPilot will "
     "not provide under any commercial terms",
     # Two orders, because both are natural English and only one was
     # matched before: "send me another client's files" puts the object
     # last, "upload the files from your other clients" puts it first.
     r"\b(send|upload|share|provide)\b[^.]{0,40}\b(other|another|previous|"
     r"past|existing)\b[^.]{0,20}\b(client|customer)s?\b"
     r"[^.]{0,30}\b(files?|data|work|documents?|details?)\b"
     r"|\b(send|upload|share|provide)\b[^.]{0,30}"
     r"\b(files?|data|work|documents?|details?)\b[^.]{0,30}"
     r"\b(other|another|previous|past|existing)\b[^.]{0,20}"
     r"\b(client|customer)s?\b"),

    # --- the work itself --------------------------------------------------
    ("ILLEGAL_WORK", SEVERE,
     "the work described is illegal",
     r"\b(hack|crack|ddos|phish\w*|keylog\w*|ransomware|botnet|carding)\b"
     r"|\b(fake|forge|counterfeit)\b[^.]{0,30}\b(id|identity|document|"
     r"passport|licen[cs]e|review|certificate|diploma)\b"
     r"|\bbypass\b[^.]{0,30}\b(captcha|authentication|drm|paywall|2fa)\b"
     r"|\bwrite (fake|false) reviews?\b"),
    ("ACADEMIC_FRAUD", CONCERN,
     "the work is to be submitted as someone else's own",
     r"\b(write|complete|take|sit)\b[^.]{0,30}\b(my|his|her|their)\b"
     r"[^.]{0,20}\b(exam|essay|thesis|dissertation|assignment|coursework)\b"),
    ("UNREALISTIC_PAY", CONCERN,
     "the pay described is far above the market for the work, which is the "
     "commonest hook in a recruitment fraud",
     r"\$\s?\d{3,}\s*(/|per )?\s*(hour|hr)\b"
     r"|\b(no experience|no skills?)\b[^.]{0,40}\b(\$\s?\d{3,}|high pay)\b"
     r"|\beasy money\b|\bguaranteed (income|earnings)\b"),
    ("URGENCY_PRESSURE", NOTE,
     "the listing pressures an immediate decision, which is used to stop "
     "someone checking",
     r"\b(urgent|immediately|right away|asap|today only|limited (time|slots?)|"
     r"act (now|fast)|first come)\b"),
    ("VAGUE_SCOPE", NOTE,
     "the listing does not say what the work actually is",
     r"\b(various tasks?|simple tasks?|easy (work|tasks?)|data entry)\b"
     r"[^.]{0,40}\b(from home|online|flexible)\b"),
    ("SUSPICIOUS_ATTACHMENT", CONCERN,
     "an attachment is of a type that executes rather than describes",
     r"\.(exe|scr|bat|cmd|com|pif|vbs|js|jar|ps1|msi|apk|dmg)\b"
     r"|\.(zip|rar|7z)\b[^.]{0,30}\bpassword\b"),
)

_COMPILED = tuple(
    (code, severity, explanation, re.compile(pattern, re.I | re.S))
    for code, severity, explanation, pattern in _PATTERNS)


def _quote(match: re.Match, text: str, width: int = 90) -> str:
    """The matched sentence, trimmed. Evidence the owner can check."""
    start = max(0, match.start() - 25)
    end = min(len(text), match.end() + 25)
    snippet = " ".join(text[start:end].split())
    return (("…" if start else "") + snippet[:width] +
            ("…" if end < len(text) else ""))


def screen_text(text: str) -> Screening:
    """Screen one piece of untrusted text.

    Takes ``str`` rather than :class:`~solvent.content.UntrustedContent` so it
    can also be pointed at a title, an attachment name or a client message
    without any of them being promoted to trusted on the way in.
    """
    screening = Screening()
    if not text:
        return screening
    seen = set()
    for code, severity, explanation, pattern in _COMPILED:
        match = pattern.search(text)
        if match and code not in seen:
            seen.add(code)
            screening.signals.append(
                Signal(code=code, severity=severity, explanation=explanation,
                       evidence=_quote(match, text)))
    return screening


def screen(opportunity) -> Screening:
    """Screen a whole opportunity: its title, body, attachments and terms.

    Structured fields are screened too. A platform's own budget field cannot
    contain an instruction, but a *title* can, and a title is the one part of a
    listing that gets rendered everywhere.
    """
    screening = screen_text(opportunity.title or "")
    body = screen_text(opportunity.description or "")

    known = {s.code for s in screening.signals}
    for signal in body.signals:
        if signal.code not in known:
            known.add(signal.code)
            screening.signals.append(signal)

    for attachment in getattr(opportunity, "attachments", ()) or ():
        for signal in screen_text(str(attachment)).signals:
            if signal.code not in known:
                known.add(signal.code)
                screening.signals.append(signal)

    # Money that has to leave before any arrives is a fact about the offer,
    # not a guess about its wording -- so it is reported from the field even
    # when nothing in the prose mentions it.
    upfront = getattr(opportunity, "upfront_cost_cents", 0) or 0
    if upfront > 0 and "ADVANCE_FEE" not in known:
        known.add("ADVANCE_FEE")
        screening.signals.append(Signal(
            code="ADVANCE_FEE", severity=SEVERE,
            explanation="the listing requires money to be spent before the "
                        "work can start. Legitimate clients pay for work; "
                        "they do not charge for the chance to do it",
            evidence=f"upfront_cost_cents={upfront}"))

    # Indicators the source itself attached, carried through so a platform's
    # own fraud flag is not silently dropped on the way in.
    for indicator in getattr(opportunity, "risk_indicators", ()) or ():
        code = f"SOURCE_FLAG:{indicator}"
        if code not in known:
            known.add(code)
            screening.signals.append(Signal(
                code=code, severity=CONCERN,
                explanation="the source itself flagged this listing",
                evidence=str(indicator)))
    return screening
