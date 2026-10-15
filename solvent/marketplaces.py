"""Adapters for the six marketplaces, for the ones that have a route.

One adapter so far, and it does not run yet. Both facts are the point.

Of the six platforms in the catalogue, five have no machine route a
reader is permitted to take: two disallow their listing pages in
robots.txt, one answers an automated request with 403 twice on
different days, one has no public API, and the one whose robots.txt
permits a read has a public API that lists freelancers to clients --
the opposite of what DeskPilot needs.

Freelancer is different, and only in a narrow way. Its robots.txt
disallows page paths and says nothing about ``/api/``; the documented
JSON endpoint answers an unauthenticated request for active projects
and returns its own rate limits in the response headers, which is a
platform describing machine access rather than refusing it. That
makes a *route* verified. It does not make it permitted: robots.txt
governs crawlers and the API terms of service are a separate document
that nobody has read. Discovery therefore still refuses this source
until the owner records a determination, and the adapter below cannot
reach the network on its own in any case -- ``WorkSource`` gets a
fetch through the Action Gate or it gets nothing.

What is finished here is the part that is hard to get right later:
turning that platform's actual response into opportunities without
lying about any of it. Two places where that is easy to get wrong,
both handled explicitly below -- the budget is a range rather than a
price, and it is denominated in the client's currency rather than the
owner's.
"""

from __future__ import annotations

from . import sourceaccess as access
from .audit import new_id
from .content import quarantine
from .discovery import Opportunity, WorkSource
from .types import PrivacyClass

#: The documented endpoint for open projects. Read-only, and the only
#: one this adapter knows: a source that could be pointed anywhere is
#: a source whose allowlist entry means nothing.
FREELANCER_HOST = "www.freelancer.com"
FREELANCER_PATH = "/api/projects/0.1/projects/active/"

#: The owner's currency. A budget in anything else is not converted:
#: converting needs an exchange rate, DeskPilot has no source for one,
#: and a quote built on an invented rate is an invented quote.
OWNER_CURRENCY = "USD"


class FreelancerActiveProjects(WorkSource):
    """Open projects from Freelancer's documented read-only API.

    Discovery only. This class has no notion of bidding, messaging or
    accepting anything: those are separate permissions on the same
    platform, all of them UNDETERMINED, and a discovery adapter that
    grew a ``bid`` method would be deciding one of them by writing
    code.
    """

    kind = "api"
    host = FREELANCER_HOST
    is_fixture = False
    #: Title, budget, currency and the job taxonomy arrive as schema
    #: fields rather than as prose, so they are the platform's own
    #: structured statement of the offer. The description does not,
    #: and is quarantined below like any other attacker-authored text.
    structured_offer_terms = True
    work_type = access.CLIENT_SERVICES

    def __init__(self, name: str = "freelancer", *, limit: int = 50,
                 capability_for: dict | None = None) -> None:
        self.name = name
        self.limit = max(1, min(int(limit), 100))
        #: Platform job names mapped to DeskPilot capabilities, which is
        #: the owner's call and empty until they make it. Guessing that
        #: "Data Entry" on a job board means this system's `data-entry`
        #: skill would be deciding what a client wants from a taxonomy
        #: label, and a posting that maps to nothing simply arrives with
        #: no capability -- which the Finder already refuses to bid on.
        self.capability_for = {str(k).strip().lower(): str(v)
                               for k, v in (capability_for or {}).items()}

    def request(self) -> object | None:
        """What to ask the Gate for. Never performed here."""
        return {
            "method": "GET",
            "url": f"https://{FREELANCER_HOST}{FREELANCER_PATH}",
            "params": {"limit": self.limit, "job_details": "true"},
        }

    # ------------------------------------------------------------ parsing

    @staticmethod
    def _projects(payload: object) -> list:
        """The project list, or nothing, without guessing at a shape."""
        if not isinstance(payload, dict):
            return []
        if payload.get("status") != "success":
            return []
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        projects = result.get("projects")
        return [p for p in projects if isinstance(p, dict)] if isinstance(
            projects, list) else []

    def _quoted_cents(self, project: dict) -> tuple[int, str]:
        """What the client has actually committed to, and in what.

        Two refusals rather than two guesses.

        The budget is a *range*. Reading the maximum would treat the
        most a client said they might spend as money DeskPilot is going
        to be paid, which is the advertised-budget-as-revenue mistake
        the pricing rules forbid. The minimum is the only end of it the
        client has committed to.

        The budget is also in the client's currency. Converting needs
        an exchange rate and nothing here has a source for one, so a
        non-USD posting arrives with no price and says so instead of
        being quoted at a number somebody made up.
        """
        currency = project.get("currency")
        code = (currency.get("code") if isinstance(currency, dict) else "")
        code = str(code or "").upper()
        if code != OWNER_CURRENCY:
            return 0, (f"budget is in {code or 'an unstated currency'} and "
                       f"DeskPilot has no exchange rate, so it has no price "
                       "in the owner's currency")
        budget = project.get("budget")
        if not isinstance(budget, dict):
            return 0, "the posting states no budget"
        minimum = budget.get("minimum")
        try:
            cents = int(round(float(minimum) * 100))
        except (TypeError, ValueError):
            return 0, "the posting's budget is not a number"
        if cents <= 0:
            return 0, "the posting states no budget above zero"
        maximum = budget.get("maximum")
        note = f"the {code} {float(minimum):,.2f} floor of the client's range"
        if isinstance(maximum, (int, float)) and maximum:
            note += (f" (they named up to {float(maximum):,.2f}, which is "
                     "not money anyone has committed)")
        return cents, note

    def _needs(self, project: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Capabilities the owner has mapped, and the raw platform labels."""
        labels = []
        jobs = project.get("jobs")
        if isinstance(jobs, list):
            for job in jobs:
                if isinstance(job, dict) and job.get("name"):
                    labels.append(str(job["name"]))
        mapped = []
        for label in labels:
            capability = self.capability_for.get(label.strip().lower())
            if capability and capability not in mapped:
                mapped.append(capability)
        return tuple(mapped), tuple(labels)

    def parse(self, payload: object) -> list[Opportunity]:
        out: list[Opportunity] = []
        for project in self._projects(payload):
            if project.get("deleted") or project.get("nonpublic"):
                continue
            if str(project.get("status", "")).lower() != "active":
                continue
            cents, price_note = self._quoted_cents(project)
            needs, labels = self._needs(project)
            reference = str(project.get("id", "") or "")
            # Everything the client wrote, kept as what it is. The
            # preview description is prose on a public board: it is the
            # single most attacker-authored field on the platform and
            # it is quarantined like any other.
            body = str(project.get("preview_description", "") or "")
            # What the owner needs flagged, in the field for flagging
            # it. Not folded into the posting text: that text is
            # quarantined attacker-authored prose, and mixing DeskPilot's
            # own observations into it would make the two
            # indistinguishable to everything downstream.
            flags = [f"price: {price_note}"]
            if not needs:
                flags.append(
                    "no DeskPilot capability is mapped to "
                    + (f"platform categories {', '.join(labels)}"
                       if labels else "this posting, which states none")
                    + "; the owner decides what a platform label means")
            seo = str(project.get("seo_url", "") or "").lstrip("/")
            structure = str(project.get("type", "") or "").upper()
            out.append(Opportunity(
                id=new_id("opp"), source=self.name, external_ref=reference,
                title=str(project.get("title", "") or "")[:300],
                quoted_cents=cents, needs=needs,
                deadline="", client_ref="",
                posting=quarantine(body, source=self.name,
                                   privacy=PrivacyClass.PUBLIC),
                structured_terms=self.structured_offer_terms,
                owner_entered=False,
                payment_structure=structure,
                external_url=(f"https://{FREELANCER_HOST}/{seo}"
                              if seo else ""),
                risk_indicators=tuple(flags)))
        return out


#: Freelancehunt's official API 2.0. The projects endpoint answers
#: without a token; everything else on the host returns 401, which is
#: a platform publishing its board on purpose rather than failing to
#: defend it.
FREELANCEHUNT_HOST = "api.freelancehunt.com"
FREELANCEHUNT_PATH = "/v2/projects"

#: The one status that means a project is taking proposals. Read from
#: the platform's own status object rather than inferred from a date:
#: a project can be open past its stated expiry and closed before it.
FREELANCEHUNT_OPEN_STATUS = 11


class FreelancehuntProjects(WorkSource):
    """Open projects from Freelancehunt's official API.

    The first adapter in this file whose source actually permits what
    it does, and it is worth being precise about how narrow that is.
    Freelancehunt's User Agreement forbids, at clause 3.1.11, "tools
    for mass mailing of private messages or automatic proposal
    submission on projects". Reading is not in that sentence and the
    platform publishes an API that serves this endpoint to anybody.
    So this class reads, and like its Freelancer sibling it has no
    notion of bidding: on this platform that is not an undetermined
    permission to be established later, it is a prohibition with a
    clause number, and a ``bid`` method here would be code written
    against the agreement.

    Two things about this source that shape every opportunity it
    produces:

    **Budgets are in hryvnia.** It is a Ukrainian platform and most
    postings are denominated in UAH. DeskPilot has no exchange rate
    and will not invent one, so those arrive with no price and say
    so. This is not a defect in the adapter; it is the honest result,
    and the owner's remedy is a recorded rate, not a guess here.

    **The employer's record is published.** Completed projects,
    positive and negative reviews, a rating. That is the platform's
    own structured statement about a counterparty, and it is carried
    through as a flag rather than silently scored, because what
    counts as a reliable client is the owner's judgement.
    """

    kind = "api"
    host = FREELANCEHUNT_HOST
    is_fixture = False
    #: Name, budget, currency, skills, status and expiry all arrive as
    #: schema fields. The description does not, and is quarantined.
    structured_offer_terms = True
    work_type = access.CLIENT_SERVICES

    def __init__(self, name: str = "freelancehunt", *, limit: int = 50,
                 capability_for: dict | None = None,
                 rates: dict | None = None) -> None:
        self.name = name
        self.limit = max(1, min(int(limit), 100))
        #: Owner-recorded exchange rates, as cents of the owner's
        #: currency per one unit of theirs. Empty by default and
        #: never defaulted to a number: a rate DeskPilot chose would
        #: turn every hryvnia budget into an invented dollar price,
        #: and the whole posting would then be priced off it. The
        #: owner records one with a citation through
        #: ``solvent setup commercial --exchange-rate``; until they
        #: do, UAH postings arrive with no price and say why.
        self.rates = {}
        for code, cents in (rates or {}).items():
            try:
                value = float(cents)
            except (TypeError, ValueError):
                continue
            if value > 0:
                self.rates[str(code).strip().upper()] = value
        #: Platform skill names mapped to DeskPilot capabilities. The
        #: owner's call, empty until they make it. "BI & Data
        #: Analytics" is not this system's `csv-cleanup` because a
        #: taxonomy label is not a statement of what a client wants.
        self.capability_for = {str(k).strip().lower(): str(v)
                               for k, v in (capability_for or {}).items()}

    def request(self) -> object | None:
        """What to ask the Gate for. Never performed here."""
        return {
            "method": "GET",
            "url": f"https://{FREELANCEHUNT_HOST}{FREELANCEHUNT_PATH}",
            "headers": {"Accept": "application/json"},
        }

    # ------------------------------------------------------------ parsing

    @staticmethod
    def _projects(payload: object) -> list:
        """The project list, or nothing, without guessing at a shape."""
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [p for p in data
                if isinstance(p, dict) and p.get("type") == "project"
                and isinstance(p.get("attributes"), dict)]

    @staticmethod
    def _is_open(attributes: dict) -> bool:
        status = attributes.get("status")
        if not isinstance(status, dict):
            return False
        try:
            return int(status.get("id")) == FREELANCEHUNT_OPEN_STATUS
        except (TypeError, ValueError):
            return False

    def _quoted_cents(self, attributes: dict) -> tuple[int, str]:
        """What the client committed to, in the owner's currency or not.

        Freelancehunt states one amount rather than a range, so there
        is no most-they-might-spend to mistake for revenue here. The
        currency is the whole problem instead: it is usually UAH, and
        a UAH figure multiplied by a rate nobody recorded is an
        invented price.
        """
        budget = attributes.get("budget")
        if not isinstance(budget, dict):
            return 0, "the posting states no budget"
        code = str(budget.get("currency") or "").upper()
        try:
            amount = float(budget.get("amount"))
        except (TypeError, ValueError):
            return 0, "the posting's budget is not a number"
        if amount <= 0:
            return 0, "the posting states no budget above zero"
        if code == OWNER_CURRENCY:
            return int(round(amount * 100)), (
                f"the {code} {amount:,.2f} the client stated")
        named = code or "an unstated currency"
        rate = self.rates.get(code) if code else None
        if not rate:
            return 0, (f"budget is {amount:,.0f} {named} and DeskPilot has "
                       "no exchange rate, so it has no price in the "
                       "owner's currency")
        cents = int(round(amount * rate))
        return cents, (f"{amount:,.0f} {code} converted at the owner's "
                       f"recorded rate of {rate / 100:.4f} "
                       f"{OWNER_CURRENCY} per {code}; the rate is the "
                       "owner's recorded figure, not a live quote, and "
                       "the client committed to the hryvnia amount rather "
                       "than to this one")

    def _needs(self, attributes: dict) -> tuple[tuple[str, ...],
                                                tuple[str, ...]]:
        labels = []
        skills = attributes.get("skills")
        if isinstance(skills, list):
            for skill in skills:
                if isinstance(skill, dict) and skill.get("name"):
                    labels.append(str(skill["name"]))
        mapped = []
        for label in labels:
            capability = self.capability_for.get(label.strip().lower())
            if capability and capability not in mapped:
                mapped.append(capability)
        return tuple(mapped), tuple(labels)

    @staticmethod
    def _employer_note(attributes: dict) -> str:
        """The platform's own record of the counterparty, uninterpreted."""
        employer = attributes.get("employer")
        if not isinstance(employer, dict):
            return "the posting names no employer record"
        parts = []
        for key, label in (("completed_projects", "completed project(s)"),
                           ("positive_reviews", "positive review(s)"),
                           ("negative_reviews", "negative review(s)")):
            value = employer.get(key)
            if isinstance(value, (int, float)):
                parts.append(f"{int(value)} {label}")
        if not parts:
            return "the platform published no counterparty record"
        return ("the platform's own counterparty record: "
                + ", ".join(parts)
                + "; what counts as reliable is the owner's call")

    def parse(self, payload: object) -> list[Opportunity]:
        out: list[Opportunity] = []
        for project in self._projects(payload):
            attributes = project["attributes"]
            if not self._is_open(attributes):
                continue
            cents, price_note = self._quoted_cents(attributes)
            needs, labels = self._needs(attributes)
            flags = [f"price: {price_note}",
                     self._employer_note(attributes)]
            if not needs:
                flags.append(
                    "no DeskPilot capability is mapped to "
                    + (f"platform skills {', '.join(labels)}"
                       if labels else "this posting, which states none")
                    + "; the owner decides what a platform label means")
            if attributes.get("is_personal"):
                flags.append("addressed to a specific freelancer rather "
                             "than open to proposals")
            body = str(attributes.get("description", "") or "")
            links = project.get("links")
            url = ""
            if isinstance(links, dict):
                url = str(links.get("self") or "")
            out.append(Opportunity(
                id=new_id("opp"), source=self.name,
                external_ref=str(project.get("id", "") or ""),
                title=str(attributes.get("name", "") or "")[:300],
                quoted_cents=cents, needs=needs,
                # The platform's own stated expiry, carried as given.
                # It is when proposals close, which is not the same as
                # when the work is due -- so it is a deadline for
                # bidding and the Finder treats it as one.
                deadline=str(attributes.get("expired_at", "") or ""),
                client_ref="",
                posting=quarantine(body, source=self.name,
                                   privacy=PrivacyClass.PUBLIC),
                structured_terms=self.structured_offer_terms,
                owner_entered=False,
                payment_structure=str(
                    attributes.get("safe_type", "") or "").upper(),
                external_url=url,
                risk_indicators=tuple(flags)))
        return out
