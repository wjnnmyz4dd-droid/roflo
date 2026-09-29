"""Real discovery adapters for sources that genuinely permit machine access.

Every adapter here was written against documentation read at a recorded date
and, where the endpoint is reachable without credentials, against a live
response whose shape was inspected rather than assumed. None of them invents an
endpoint, a parameter or a field name.

They are :class:`~solvent.discovery.WorkSource` implementations and get no
special powers for being real: they cannot open a socket themselves. Discovery
supplies the fetch, which goes through the Action Gate, so an adapter that
wanted to reach the network without permission has nowhere to do it.

An adapter's whole job is: say what to request, and turn the answer into
normalised candidates. It does not decide whether work is worth taking, whether
DeskPilot can do it, what it is worth, or whether anything may be sent back.

**Dates matter here.** A determination that automated access is permitted is
true of a moment. Each adapter carries ``verified_on`` and the registry stores
it, so a stale determination can be re-checked rather than trusted forever.
"""

from __future__ import annotations

import json

from . import sourceaccess as access
from .audit import new_id
from .content import quarantine
from .discovery import Opportunity, WorkSource
from .types import Cents, PrivacyClass

#: The date the access terms below were read from their authoritative source.
#: Not a guess, and not "whenever this file was edited": it is the day someone
#: actually looked, and it is what a staleness check compares against.
VERIFIED_ON = "2026-09-29"


def _cents(value) -> Cents:
    """A money field, or zero when the source did not state one.

    Zero means *not stated*, and is never presented as a budget of nothing:
    the opportunity row keeps the source's own fields, and a missing budget is
    the Governor's problem to refuse, not this module's to invent.
    """
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


class GrantsGovSource(WorkSource):
    """Grants.gov Search2 — US federal funding opportunities.

    The only source here that needs no credential of any kind. The API guide
    states plainly: "Authentication and authorization are not required for the
    following APIs: Endpoint: search2." That makes it the one source which can
    demonstrate autonomous discovery on a machine the owner has not configured.

    **What it returns is funding, not contract work.** A grant is money applied
    for against a programme, not a client paying for a deliverable, and
    DeskPilot's mission is client work. It is registered because it is a real,
    working, unauthenticated feed and because the owner may legitimately want
    grant-funded work in scope -- but ``work_type`` says what it is so nobody
    has to infer it, and the owner decides whether to enable it.

    Verified against a live response on the date above: 517 matches for a
    one-word query, records under ``data.oppHits`` with the fields used below.
    """

    name = "grants_gov"
    display_name = "Grants.gov (US federal funding opportunities)"
    kind = "api"
    source_type = "PUBLIC_PROCUREMENT"
    host = "api.grants.gov"
    base_url = "https://api.grants.gov/v1/api/search2"
    is_fixture = False
    #: Platform schema fields, not prose. Title and dates are structured.
    structured_offer_terms = True
    access_method = access.PUBLIC_FEED
    auth_required = False
    manual_import = True
    work_type = "GRANT_FUNDING"
    verified_on = VERIFIED_ON
    rate_limit_note = ("no published rate limit; polled on a slow schedule "
                       "because a public service with no stated limit is one "
                       "to be gentle with, not one to hammer")
    terms_notes = ("grants.gov API guide, read 2026-09-29: search2 requires no "
                   "authentication and is described as unrestricted access "
                   "intended to democratise information access")

    def __init__(self, *, keyword: str = "", rows: int = 25,
                 statuses: str = "posted") -> None:
        self.keyword = keyword
        self.rows = max(1, min(int(rows), 100))
        self.statuses = statuses

    def request(self) -> object:
        """A POST body, exactly as the guide documents it."""
        return {
            "method": "POST",
            "url": self.base_url,
            "headers": {"Content-Type": "application/json"},
            "body": {"keyword": self.keyword, "rows": self.rows,
                     "oppStatuses": self.statuses},
        }

    def parse(self, payload: object) -> list[Opportunity]:
        """Turn the documented response into candidates.

        Fails closed on anything unexpected (§42): a response that is not the
        shape this adapter was written against is not reinterpreted, partially
        accepted or guessed at. Raising is what marks the source FAILED.
        """
        if payload is None:
            return []
        if isinstance(payload, (str, bytes)):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError(f"expected a JSON object, got {type(payload).__name__}")

        if payload.get("errorcode") not in (0, "0", None):
            raise ValueError(f"grants.gov reported errorcode "
                             f"{payload.get('errorcode')}: {payload.get('msg')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("response has no 'data' object")
        hits = data.get("oppHits")
        if hits is None:
            raise ValueError("response has no 'data.oppHits' array")
        if not isinstance(hits, list):
            raise ValueError("'data.oppHits' is not an array")

        out = []
        for hit in hits:
            if not isinstance(hit, dict):
                raise ValueError("an entry in oppHits is not an object")
            identifier = str(hit.get("id") or "").strip()
            number = str(hit.get("number") or "").strip()
            title = str(hit.get("title") or "").strip()
            if not identifier or not title:
                raise ValueError(
                    "an opportunity has no id or no title; a record that "
                    "cannot be identified cannot be deduplicated")
            agency = str(hit.get("agency") or "").strip()
            out.append(Opportunity(
                id=new_id("opp"), source=self.name,
                external_ref=number or identifier, title=title,
                # Grants.gov search results carry no award amount. Zero here
                # means "not stated", and the Governor refuses an opportunity
                # whose economics are unknown rather than assuming any figure.
                quoted_cents=0,
                needs=(),
                deadline=str(hit.get("closeDate") or ""),
                # §50. The agency is the posting body, recorded as given. No
                # client identity is inferred where none was supplied.
                client_ref=f"agency:{agency}" if agency else "",
                posting=quarantine(
                    json.dumps({k: hit.get(k) for k in
                                ("title", "agency", "agencyCode", "oppStatus",
                                 "docType", "openDate", "closeDate", "number")},
                               sort_keys=True),
                    source=self.name, privacy=PrivacyClass.PUBLIC),
                structured_terms=True,
                external_url=(f"https://www.grants.gov/search-results-detail/"
                              f"{identifier}" if identifier else ""),
                confidence=1.0))
        return out


class SamGovOpportunitiesSource(WorkSource):
    """SAM.gov Get Opportunities — US federal contract opportunities.

    This one is contract work: solicitations, presolicitations, sources-sought
    and award notices for federal procurement. It is the closest thing to
    DeskPilot's actual mission among the sources with a documented public API.

    **Needs a free API key, which is an owner action.** The GSA documentation
    states a key is obtained from the SAM.gov Account Details page, passed as
    ``api_key``, and that "Request per day are limited based on the federal or
    non-federal or general roles". No key is stored here, and the adapter is
    registered with ``auth_required`` so the registry shows it as needing
    configuration rather than as broken.

    **Visible is not eligible** (§12). Federal contracting attaches
    registration, eligibility and reporting obligations to the contractor.
    Seeing a solicitation says nothing about whether DeskPilot's owner may bid
    on it, and this adapter asserts nothing of the kind.
    """

    name = "sam_gov"
    display_name = "SAM.gov (US federal contract opportunities)"
    kind = "api"
    source_type = "PUBLIC_PROCUREMENT"
    host = "api.sam.gov"
    base_url = "https://api.sam.gov/opportunities/v2/search"
    is_fixture = False
    structured_offer_terms = True
    access_method = access.OFFICIAL_API
    auth_required = True
    manual_import = True
    work_type = "CONTRACT_WORK"
    verified_on = VERIFIED_ON
    rate_limit_note = ("per GSA documentation, limits depend on the account "
                       "role; a personal key is far more restricted than one "
                       "held against a registered entity")
    terms_notes = ("open.gsa.gov Get Opportunities Public API, read "
                   "2026-09-29: api_key required, pagination required, limit "
                   "max 1000 per page, postedFrom/postedTo required and at "
                   "most one year apart")

    #: Notice types, as the documentation defines them.
    SOLICITATION = "o"
    PRESOLICITATION = "p"
    COMBINED = "k"
    SOURCES_SOUGHT = "r"

    def __init__(self, *, posted_from: str = "", posted_to: str = "",
                 notice_type: str = "", naics: str = "", limit: int = 50
                 ) -> None:
        self.posted_from = posted_from
        self.posted_to = posted_to
        self.notice_type = notice_type
        self.naics = naics
        self.limit = max(1, min(int(limit), 1000))

    def request(self) -> object:
        """The documented query. The key is *not* here.

        Discovery supplies credentials through whatever the deployment
        configures; an adapter that carried a key would be an adapter that
        could leak one.
        """
        params = {"postedFrom": self.posted_from, "postedTo": self.posted_to,
                  "limit": self.limit}
        if self.notice_type:
            params["ptype"] = self.notice_type
        if self.naics:
            params["ncode"] = self.naics
        return {"method": "GET", "url": self.base_url, "params": params,
                "requires_credential": "SAM_GOV_API_KEY"}

    def parse(self, payload: object) -> list[Opportunity]:
        if payload is None:
            return []
        if isinstance(payload, (str, bytes)):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
        notices = payload.get("opportunitiesData")
        if notices is None:
            raise ValueError("response has no 'opportunitiesData' array")
        if not isinstance(notices, list):
            raise ValueError("'opportunitiesData' is not an array")

        out = []
        for notice in notices:
            if not isinstance(notice, dict):
                raise ValueError("an entry in opportunitiesData is not an object")
            notice_id = str(notice.get("noticeId") or "").strip()
            title = str(notice.get("title") or "").strip()
            if not notice_id or not title:
                raise ValueError(
                    "a notice has no noticeId or no title; a record that "
                    "cannot be identified cannot be deduplicated")
            office = str(notice.get("fullParentPathName") or "").strip()
            award = notice.get("award") or {}
            out.append(Opportunity(
                id=new_id("opp"), source=self.name,
                external_ref=notice_id, title=title,
                # Most solicitations state no value. An award notice may.
                quoted_cents=_cents((award or {}).get("amount")),
                needs=(),
                deadline=str(notice.get("responseDeadLine") or ""),
                client_ref=f"office:{office}" if office else "",
                posting=quarantine(
                    json.dumps({k: notice.get(k) for k in
                                ("title", "solicitationNumber", "type",
                                 "naicsCode", "classificationCode",
                                 "postedDate", "responseDeadLine",
                                 "fullParentPathName", "description")},
                               sort_keys=True, default=str),
                    source=self.name, privacy=PrivacyClass.PUBLIC),
                structured_terms=True,
                external_url=str(notice.get("uiLink") or ""),
                confidence=1.0))
        return out


#: Adapters that exist as real implementations, by source id.
ADAPTERS = {
    GrantsGovSource.name: GrantsGovSource,
    SamGovOpportunitiesSource.name: SamGovOpportunitiesSource,
}
