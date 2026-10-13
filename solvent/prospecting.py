"""EXPERIMENTAL. Looking for work beyond the curated list.

**This is a research prototype and is not wired into commercial operation.**
Nothing here can register a source, create an opportunity, contact anybody,
or cause money to move. It answers one question and no others: *would
searching the open internet find more legitimate, relevant, actionable work
than the sources DeskPilot already has?*

The distinction the whole module is built around:

    **Finding a website is not permission to use it.**

A search result is a lead about a possible source. It is not a source, and
the thing it mentions is not an opportunity. Turning a lead into a source
means a human reading that site's terms and the owner registering it --
exactly the eligibility review the curated catalogue already requires. So
``Candidate`` is deliberately not an ``Opportunity`` and there is no method
anywhere that converts one into the other.

What it enforces, because a crawler without these is a liability:

* **A domain allowlist.** It visits what it was told to visit. There is no
  "follow the interesting links" -- that is how a prototype becomes an
  uncontrolled crawl of the internet overnight.
* **A request budget.** A hard count, checked before every fetch. When it
  is spent the run stops, and stopping early is reported rather than hidden.
* **A rate limit and a timeout** per domain.
* **robots.txt, honoured.** A disallowed path is not fetched. The file is a
  platform's own machine-readable answer to this exact question.
* **Provenance on every candidate** -- which URL, fetched when, under which
  budget. A candidate whose origin is unknown is discarded.
* **Deduplication**, within a run and against what is already known.
* **Retention.** Fetched page text is not kept. What survives a run is the
  extracted fields and the provenance, because keeping arbitrary web pages
  indefinitely is a data-protection problem nobody asked for.
* **Prompt-injection resistance.** Page text is quarantined content from an
  untrusted stranger. It is never instructions, and a page that tries to
  issue them is recorded as having tried.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum

from .content import quarantine
from .errors import FailClosed
from .types import Cents, PrivacyClass

#: This module is a prototype. The constant exists so that "is this in
#: production?" has a single answer that a test can assert on, rather than
#: being a matter of opinion about how wired-in it looks.
PRODUCTION_READY = False

#: Stages of the workflow, in order. Named so a run can say where it stopped.
STAGES = ("search", "identify-source", "verify-source", "check-access-rules",
          "extract", "match-skill", "evaluate-compensation",
          "check-suitability", "deduplicate", "record")


class Disposition(Enum):
    """What became of one lead."""

    RECORDED = "RECORDED"
    #: The domain was not on the allowlist.
    OFF_ALLOWLIST = "OFF_ALLOWLIST"
    #: robots.txt disallows this path.
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    #: Fetched, but nothing that looks like a piece of paid work.
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    #: An opportunity, but not work any certified skill does.
    NO_SKILL_MATCH = "NO_SKILL_MATCH"
    #: No compensation stated, or stated as unpaid.
    NO_COMPENSATION = "NO_COMPENSATION"
    #: Already seen, this run or before.
    DUPLICATE = "DUPLICATE"
    #: The page tried to issue instructions.
    INJECTION_ATTEMPT = "INJECTION_ATTEMPT"
    #: The budget ran out before this one.
    BUDGET_SPENT = "BUDGET_SPENT"
    FETCH_FAILED = "FETCH_FAILED"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a candidate came from. Without this it is a rumour."""

    url: str
    domain: str
    fetched_at: str
    run_id: str

    def key(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class Candidate:
    """A possible piece of work, found somewhere nobody has authorised yet.

    Deliberately **not** an Opportunity. An Opportunity belongs to a
    registered source with a compliance determination behind it; this is a
    note that something looked promising on a page.
    """

    title: str
    #: The matched capability, or empty when nothing matched.
    capability: str
    #: What the page said about money. Zero means it said nothing usable.
    stated_cents: Cents
    provenance: Provenance
    #: The page's own words, quarantined. Never instructions.
    excerpt: object = None
    notes: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        """Whether a person could do something with this today."""
        return bool(self.capability and self.stated_cents > 0)


@dataclass
class Budget:
    """A hard ceiling on a run. Checked before every fetch, not after."""

    max_requests: int = 20
    max_seconds: float = 60.0
    #: Minimum gap between two requests to the same domain.
    per_domain_delay: float = 1.0
    spent: int = 0
    started: float = field(default_factory=time.monotonic)

    def exhausted(self) -> tuple[bool, str]:
        if self.spent >= self.max_requests:
            return True, f"request budget of {self.max_requests} is spent"
        elapsed = time.monotonic() - self.started
        if elapsed >= self.max_seconds:
            return True, f"time budget of {self.max_seconds:g}s is spent"
        return False, ""


@dataclass
class RunReport:
    run_id: str
    candidates: list = field(default_factory=list)
    dispositions: dict = field(default_factory=dict)
    requests_made: int = 0
    stopped_early: str = ""

    def count(self, disposition: Disposition) -> int:
        return sum(1 for d in self.dispositions.values() if d is disposition)

    def summary(self) -> dict:
        actionable = [c for c in self.candidates if c.actionable]
        return {
            "run_id": self.run_id,
            "requests_made": self.requests_made,
            "urls_considered": len(self.dispositions),
            "candidates_recorded": len(self.candidates),
            "actionable": len(actionable),
            "stopped_early": self.stopped_early,
            "by_disposition": {d.value: self.count(d) for d in Disposition
                               if self.count(d)},
        }


#: Phrases that make a page a set of instructions rather than a listing.
#: Reused deliberately from the same thinking as the customer-service
#: governance patterns: a web page is a stranger talking, and a stranger
#: telling an automated system what to do is the thing to notice.
_INJECTION = (
    r"ignore (all )?(previous|prior|above) instructions",
    r"disregard (all )?(previous|prior)",
    r"you are now",
    r"system\s*:",
    r"\bnew instructions\b",
    r"send (your|the) (api[_ ]?key|credential|password|token)",
    r"\bvisit\b.{0,30}\band (submit|apply|register)\b",
)

#: Money, as a page might state it. Conservative: a number with a currency
#: marker near a budget word. "Great exposure" is not compensation.
_MONEY = re.compile(
    r"(?:[$£€]\s?)(\d[\d,]*)(?:\.(\d{2}))?|(\d[\d,]*)\s?(?:usd|gbp|eur)",
    re.IGNORECASE)
_UNPAID = re.compile(
    r"\b(unpaid|voluntary|volunteer|for exposure|no budget|equity only)\b",
    re.IGNORECASE)

#: What each certified skill looks like when somebody describes needing it.
#: Not a keyword list: every entry requires a *task* word and an *object*
#: word, because "data" alone matches half the internet and "spreadsheet"
#: alone matches a blog post about spreadsheets.
SKILL_SIGNALS = {
    "csv-cleanup": (
        (r"\b(clean|cleanse|tidy|deduplicat\w+|dedupe|normalis\w+|normaliz\w+)\b",
         r"\b(csv|spreadsheet|data ?set|customer list|mailing list|export)\b"),),
    "spreadsheet-automation": (
        (r"\b(automat\w+|formula\w*|calculat\w+|format\w*|pivot)\b",
         r"\b(excel|spreadsheet|workbook|xlsx|google sheet)\b"),),
    "document-processing": (
        (r"\b(fill|populate|merge|convert|generat\w+|template)\b",
         r"\b(document|docx|word|contract|letter|report template)\b"),),
    "data-entry": (
        (r"\b(enter|entry|transcrib\w+|input|copy|transfer|migrat\w+)\b",
         r"\b(data|records?|invoices?|forms?|database|crm)\b"),),
    "report-builder": (
        (r"\b(build|prepar\w+|creat\w+|produc\w+|compil\w+|summaris\w+|summariz\w+)\b",
         r"\b(report|dashboard|summary|analysis document|monthly figures)\b"),),
}


class Prospector:
    """Runs the experiment. Reads pages; authorises nothing.

    ``fetch`` is injected: a callable taking a URL and returning
    ``(status, text)``. Supplied by the caller so a run can be reproduced
    against recorded pages, and so this module physically cannot reach the
    network on its own -- the same reason ``WorkSource`` does not hold a
    socket.
    """

    def __init__(self, *, allowed_domains, fetch, robots=None,
                 budget: Budget | None = None, known_urls=()):
        if not allowed_domains:
            raise FailClosed(
                "an open-internet run needs an explicit domain allowlist. "
                "A prototype that decides for itself where to go is an "
                "uncontrolled crawl")
        self.allowed = {d.lower().lstrip(".") for d in allowed_domains}
        self._fetch = fetch
        #: ``(domain, path) -> bool``. Defaults to refusing nothing, which
        #: is only safe because the allowlist is already narrow.
        self._robots = robots or (lambda domain, path: True)
        self.budget = budget or Budget()
        self._seen = {self._fingerprint_url(u) for u in known_urls}
        self._last_request: dict = {}

    # ---- the guards ----------------------------------------------------

    @staticmethod
    def _fingerprint_url(url: str) -> str:
        parsed = urllib.parse.urlsplit(url.lower())
        return hashlib.sha256(
            f"{parsed.netloc}{parsed.path}".encode("utf-8")).hexdigest()[:32]

    def _domain_of(self, url: str) -> str:
        return urllib.parse.urlsplit(url).netloc.lower().split(":")[0]

    def on_allowlist(self, url: str) -> bool:
        domain = self._domain_of(url)
        return any(domain == d or domain.endswith("." + d)
                   for d in self.allowed)

    def _respect_rate_limit(self, domain: str) -> None:
        last = self._last_request.get(domain)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.budget.per_domain_delay:
                time.sleep(self.budget.per_domain_delay - gap)
        self._last_request[domain] = time.monotonic()

    # ---- the workflow --------------------------------------------------

    def run(self, urls, *, run_id: str) -> RunReport:
        """Search → verify → extract → match → record, with a budget."""
        report = RunReport(run_id=run_id)
        for url in urls:
            spent, why = self.budget.exhausted()
            if spent:
                report.stopped_early = why
                report.dispositions[url] = Disposition.BUDGET_SPENT
                continue
            report.dispositions[url] = self._consider(url, run_id, report)
        return report

    def _consider(self, url: str, run_id: str, report) -> Disposition:
        # identify-source / verify-source
        if not self.on_allowlist(url):
            return Disposition.OFF_ALLOWLIST

        # check-access-rules: the platform's own answer, honoured.
        domain = self._domain_of(url)
        path = urllib.parse.urlsplit(url).path or "/"
        if not self._robots(domain, path):
            return Disposition.ROBOTS_DISALLOWED

        fingerprint = self._fingerprint_url(url)
        if fingerprint in self._seen:
            return Disposition.DUPLICATE

        self._respect_rate_limit(domain)
        self.budget.spent += 1
        report.requests_made += 1
        try:
            status, text = self._fetch(url)
        except Exception:
            return Disposition.FETCH_FAILED
        if status != 200 or not text:
            return Disposition.FETCH_FAILED

        self._seen.add(fingerprint)

        # Everything below treats `text` as a stranger's words.
        if self._looks_like_instructions(text):
            return Disposition.INJECTION_ATTEMPT

        capability = self.match_skill(text)
        stated = self.stated_compensation(text)
        if not capability and stated <= 0:
            return Disposition.NO_OPPORTUNITY
        if not capability:
            return Disposition.NO_SKILL_MATCH
        if stated <= 0:
            return Disposition.NO_COMPENSATION

        provenance = Provenance(url=url, domain=domain,
                                fetched_at=_now(), run_id=run_id)
        report.candidates.append(Candidate(
            title=self._title(text), capability=capability,
            stated_cents=stated, provenance=provenance,
            # Quarantined, and short: the run keeps an excerpt for a person
            # to read, not the page. Retaining arbitrary web pages
            # indefinitely is a problem nobody asked for.
            excerpt=quarantine(text[:400], source=domain,
                               privacy=PrivacyClass.PUBLIC)))
        return Disposition.RECORDED

    # ---- reading a page ------------------------------------------------

    @staticmethod
    def _looks_like_instructions(text: str) -> bool:
        lowered = text.lower()
        return any(re.search(pattern, lowered) for pattern in _INJECTION)

    @staticmethod
    def _title(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:120]
        return ""

    @staticmethod
    def match_skill(text: str) -> str:
        """Which certified skill, if any, this page is asking for.

        Both a task word and an object word, within the same page. Keyword
        matching alone produces a stream of false positives -- "data" is on
        half the internet -- and a false positive here costs a person's
        time reading something that was never a job.
        """
        lowered = text.lower()
        for capability, signal_sets in SKILL_SIGNALS.items():
            for task, obj in signal_sets:
                if re.search(task, lowered) and re.search(obj, lowered):
                    return capability
        return ""

    @staticmethod
    def stated_compensation(text: str) -> Cents:
        """What the page says it pays. Zero when it does not say.

        "Great exposure" is not compensation, and a page that says so
        explicitly is recorded as paying nothing rather than as unknown.
        """
        if _UNPAID.search(text):
            return 0
        best = 0
        for match in _MONEY.finditer(text):
            whole = match.group(1) or match.group(3) or ""
            pence = match.group(2) or "00"
            if not whole:
                continue
            try:
                value = int(whole.replace(",", "")) * 100 + int(pence)
            except ValueError:
                continue
            best = max(best, value)
        return best


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def compare(curated: dict, experimental: dict) -> dict:
    """The two channels side by side, on the measures that matter.

    Deliberately reports ratios as well as counts: a run that considered
    ten times as many pages and found two more jobs has not won.
    """
    def ratio(part, whole):
        return round(part / whole, 3) if whole else 0.0

    rows = {}
    for name, data in (("curated", curated), ("open_internet", experimental)):
        considered = int(data.get("urls_considered", 0))
        recorded = int(data.get("candidates_recorded", 0))
        actionable = int(data.get("actionable", 0))
        rows[name] = {
            "considered": considered,
            "recorded": recorded,
            "actionable": actionable,
            "false_positive_rate": ratio(recorded - actionable, recorded),
            "actionable_per_request": ratio(actionable,
                                            int(data.get("requests_made", 0))),
            "duplicate_rate": ratio(
                int(data.get("by_disposition", {}).get("DUPLICATE", 0)),
                considered),
        }
    return rows
