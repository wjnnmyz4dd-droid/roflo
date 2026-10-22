"""Routing and page bodies. Reads through the projection, writes only intents.

Two rules hold for every route, and the tests attack both:

**Authorisation is enforced here, not in the markup.** A page the owner cannot
see is not merely unlinked — the handler refuses it. Hiding a button is not a
control, because an attacker sends the request directly.

**Nothing on a page is trusted.** Client messages, job titles, file names and
supplied documentation all pass through ``esc``.
"""

from __future__ import annotations

import json
import pathlib
import urllib.parse
from dataclasses import dataclass, field

from ..branding import ENGINE, PRODUCT
from . import auth as auth_module
from . import board as board_module
from . import intents as intents_module
from . import status
from ..types import JOBS_PER_AGENT_CEILING
from .render import (
    NAV, detail, esc, link, money, page, pill, table,
)


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    form: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True, slots=True)
class Response:
    status: int = 200
    body: bytes = b""
    headers: tuple = ()
    set_session: str = ""
    clear_session: bool = False


#: Sent on every response. A control surface has no reason to be framed, to sniff
#: content types, or to leak a referrer to anywhere.
SECURITY_HEADERS = (
    ("Content-Type", "text/html; charset=utf-8"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store, max-age=0"),
    # No inline script and no external anything. The pages need neither, so the
    # policy can be strict enough to make stored XSS unexploitable rather than
    # merely unlikely.
    ("Content-Security-Policy",
     "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
     "base-uri 'none'; frame-ancestors 'none'"),
)


def _board_banner(board) -> str:
    """One line at the top of the page, carrying the board's worst state.

    Worst rather than an average, because a page with one UNSAFE row and
    nineteen healthy ones is an unsafe page, and a summary that averaged them
    would be the most misleading sentence on the surface.
    """
    summary = board.summary
    attention = board.needing_attention()
    if summary == status.VERIFIED_HEALTHY:
        return ('<div class="banner banner-ok">Every checked fact is '
                "verified healthy.</div>")
    tone = status.TONE[summary]
    return (f'<div class="banner banner-{tone}">{pill(summary, tone)} '
            f"{esc(str(len(attention)))} of {esc(str(len(board.rows)))} "
            f"checked facts need reading.</div>")


def _board_table(board) -> str:
    """Worst first, and every row says what it rests on.

    The evidence column is the point of the table rather than a decoration:
    this surface spent its life reporting a halted business as NORMAL, and what
    would have given that away is a green row with nothing in the column next
    to it.
    """
    rows = []
    for name, entry in board.sorted_worst_first():
        rows.append([
            esc(name),
            pill(entry.state, entry.tone),
            esc(entry.detail),
            esc(entry.evidence or entry.action or "—"),
        ])
    return table(["fact", "state", "what is true", "evidence or next step"],
                 rows, empty="Nothing could be checked, which is itself "
                             "unknown rather than healthy.")


def _platform_pill(state: str) -> str:
    """The seven platform statuses, coloured by what they mean for the owner.

    ESTABLISHED reads green whichever kind it is. Manual import is not a
    lesser outcome than an API: it is the path that is never blocked by
    anybody, it needs nobody's permission, and it feeds the same pipeline.
    Colouring it amber would tell the owner to go and fix something that is
    working.
    """
    from ..sourcecatalog import (
        BLOCKED_MISSING_CREDENTIALS, BLOCKED_PLATFORM_RESTRICTION,
        ESTABLISHED_AUTOMATED, ESTABLISHED_MANUAL_IMPORT,
        ESTABLISHED_OWNER_AUTHORIZED, PENDING_OWNER_AUTHORIZATION, UNVERIFIED,
    )

    return pill(state, {
        ESTABLISHED_AUTOMATED: "ok",
        ESTABLISHED_OWNER_AUTHORIZED: "ok",
        ESTABLISHED_MANUAL_IMPORT: "ok",
        PENDING_OWNER_AUTHORIZATION: "info",
        BLOCKED_PLATFORM_RESTRICTION: "neutral",
        BLOCKED_MISSING_CREDENTIALS: "warn",
        UNVERIFIED: "warn",
    }.get(state, "neutral"))


def _connection_cards(read) -> str:
    """The Business Connections section.

    The state comes from `solvent.connections`, which reads the policy
    and asserts nothing: DeskPilot cannot know that a mailbox works or
    that a Stripe account is attached, because both are claims about
    things outside it. A card that said CONNECTED on DeskPilot's own
    say-so would be the most confidently wrong thing on the page.
    """
    from .. import connections as conn

    class _Reader:
        """Adapts the read-only model to the shape the cards expect."""

        def get(self, *path, default=None):
            return read.policy_value(*path, default=default)

    summary = conn.summary(_Reader())
    blocks = []
    for card in summary["cards"]:
        tone = {"CONNECTED": "ok", "ERROR": "bad"}.get(card["state"], "warn")
        button = ""
        if card["action_verb"]:
            button = (f'<form method="post" action="/intent">'
                      f'<input type="hidden" name="verb" '
                      f'value="{esc(card["action_verb"])}">'
                      f'<button type="submit">{esc(card["action_label"])}'
                      f'</button></form>')
        elsewhere = ("" if not card["owner_must_act_elsewhere"] else
                     '<div class="n">This step happens with the provider. '
                     'DeskPilot never asks for a password.</div>')
        blocks.append(
            f'<div class="card"><div class="k">{esc(card["title"])}</div>'
            f'<div class="v">{pill(card["state"], tone)}</div>'
            f'<div class="n">{esc(card["detail"])}</div>'
            f'{elsewhere}{button}</div>')
    note = f'<p class="sub">{esc(summary["note"])}</p>'
    return "".join(blocks) + note


def _pipeline_section(read) -> str:
    """Business performance, as the Chief of Staff last recorded it.

    Rendered from the recorded snapshot and nothing else. The page has
    a read-only database connection and no Solvent, so it could not
    construct a ChiefOfStaff even if it wanted to -- which is the
    right shape: the authority computes, the page shows.

    Every money figure carries the simulation label when simulation is
    on, and the label comes from the snapshot rather than from the
    page re-reading the policy. A dashboard that worked out for itself
    whether the money was real would be one `if` away from showing
    fixture money under a heading that says Collected.
    """
    snapshot = read.chief_of_staff()
    if not snapshot["present"]:
        # Not "a Solvent": the engine name on an owner-facing page is a
        # stale reference, and `test_branding` caught this one.
        return ('<p class="sub">The Chief of Staff has not run yet. '
                'Nothing is wrong on an installation that has only just '
                'started; on one that has been up a while it means the '
                'tick is not running.</p>')
    figures = snapshot["figures"]
    label = pill("SIMULATED", "info") + " " if snapshot["simulated"] else ""
    age = snapshot["age_seconds"]
    when = ("age unknown" if age is None
            else f"recorded {int(age)}s ago")
    staleness = ("" if not snapshot["stale"] else
                 '<p class="sub">' + pill("STALE", "warn")
                 + f" This is the last snapshot, {esc(when)}. The tick "
                   "runs every fifteen minutes, so a snapshot this old "
                   "means it stopped.</p>")

    def card(title, value, note=""):
        return (f'<div class="card"><div class="k">{esc(title)}</div>'
                f'<div class="v">{value}</div>'
                f'<div class="n">{esc(note)}</div></div>')

    counted = [
        card("Prospects", esc(str(figures.get("prospects", 0))),
             f"{figures.get('with_a_verified_need', 0)} with a published "
             "need"),
        card("Contactable", esc(str(figures.get("contactable", 0))),
             f"{figures.get('suppressed_addresses', 0)} address(es) "
             "suppressed"),
        card("Negotiations", esc(str(figures.get("negotiations", 0))),
             f"{figures.get('negotiations_agreed', 0)} agreed, "
             f"{figures.get('negotiations_escalated', 0)} with you"),
        card("Engagements", esc(str(figures.get("engagements", 0))),
             "contracts at every stage"),
        card("Contracted", label + esc(money(figures.get(
            "contracted_cents", 0))), "agreed contract value"),
        card("Collected", label + esc(money(figures.get(
            "collected_cents", 0))), "verified payments only"),
        card("Outstanding", label + esc(money(figures.get(
            "outstanding_cents", 0))), "invoiced and unpaid"),
        card("Messages prepared", esc(str(figures.get(
            "messages_prepared", 0))), "prepared, not necessarily sent"),
    ]
    steps: dict[str, int] = {}
    for row in snapshot["next_steps"]:
        steps[str(row.get("step", "?"))] = steps.get(
            str(row.get("step", "?")), 0) + 1
    by_step = table(
        ["next step", "relationships"],
        [[esc(k), esc(str(v))] for k, v in sorted(steps.items())],
        empty="Nothing queued.")
    return "".join(counted) + staleness + by_step


def _intent_class(verb: str) -> str:
    """The class of a verb named in an audit row, without trusting the row.

    The verb arrives from the audit log, so it is a string and not a promise:
    a verb that has since left the closed list reads as UNKNOWN rather than
    being coloured as though it were safe.
    """
    try:
        return intents_module.classify(verb)
    except ValueError:
        return "UNKNOWN"


class ControlCentre:
    """The whole surface. Given a projection and an intent writer, nothing else."""

    def __init__(self, readmodel, *, sessions=None, intent_writer=None,
                 password_hash: str = "", secure_cookies: bool = True) -> None:
        self.read = readmodel
        self.sessions = sessions or auth_module.Sessions()
        self.intents = intent_writer or intents_module.WriteOnlySpool()
        self.password_hash = password_hash
        self.secure_cookies = secure_cookies

    # ------------------------------------------------------------- dispatch
    def handle(self, request: Request) -> Response:
        session_id = request.cookies.get("solvent_session", "")
        session = self.sessions.get(session_id)

        if request.path == "/login":
            return self._login(request)
        if request.path == "/logout":
            return self._logout(request, session_id, session)

        if session is None:
            # Every path other than login requires a session, including ones a
            # future page might add: the check is here, before routing.
            return self._redirect("/login")

        if request.method == "POST":
            return self._post(request, session_id, session)

        handler = self._routes().get(request.path)
        if handler is None:
            return Response(404, page("Not found",
                                      "<h1>Not found</h1>"
                                      '<p class="sub">No such page.</p>',
                                      path=request.path,
                                      csrf=session["csrf"]),
                            SECURITY_HEADERS)
        answer = handler(request)
        if isinstance(answer, str):
            # A handler that returns a bare path is a route kept for its URL
            # rather than its content. ``/wizard`` is the case: there used to
            # be two setup pages, and the one that remains is ``/setup``.
            return Response(303, b"",
                            (("Location", answer),) + SECURITY_HEADERS[1:])
        body, title = answer
        return Response(200, page(title, body, path=request.path,
                                  csrf=session["csrf"],
                                  banners=self._banners()),
                        SECURITY_HEADERS)

    def _routes(self) -> dict:
        return {
            "/": self.command_centre,
            "/jobs": self.jobs,
            "/job": self.job_detail,
            "/clients": self.clients,
            "/client": self.client_detail,
            "/files": self.files,
            "/incidents": self.incidents,
            "/incident": self.incident_detail,
            "/diagnostics": self.diagnostics,
            "/breakers": self.breakers,
            "/notifications": self.notifications,
            "/recommendations": self.recommendations,
            "/wizard": self.wizard,     # kept: redirects to /setup
            "/sources": self.sources,
            "/bidder": self.bidder,
            "/skills": self.skills,
            "/skill": self.skill_detail,
            "/capabilities": self.capabilities,
            "/learning": self.learning,
            "/service": self.service,
            "/approvals": self.approvals,
            "/money": self.money_page,
            "/model": self.model,
            "/security": self.security,
            "/audit": self.audit,
            "/health": self.health,
            "/setup": self.setup,
        }

    # ---------------------------------------------------------------- login
    def _login(self, request: Request) -> Response:
        if request.method != "POST":
            return Response(200, self._login_page(), SECURITY_HEADERS)
        locked, seconds = self.sessions.locked_out(request.source)
        if locked:
            return Response(429, self._login_page(
                f"Too many attempts. Try again in {seconds // 60 + 1} minute(s)."),
                SECURITY_HEADERS)
        if not self.password_hash:
            return Response(503, self._login_page(
                "No web password is configured. Set SOLVENT_WEB_PASSWORD_HASH."),
                SECURITY_HEADERS)
        password = request.form.get("password", "")
        if not auth_module.check_password(password, self.password_hash):
            self.sessions.record_failure(request.source)
            # One message for a wrong password and for a password that is right
            # but locked out elsewhere: distinguishing them is a free oracle.
            return Response(401, self._login_page("Sign-in failed."),
                            SECURITY_HEADERS)
        self.sessions.clear_failures(request.source)
        session_id, _ = self.sessions.create(source=request.source)
        return Response(303, b"", (("Location", "/"),) + SECURITY_HEADERS[1:],
                        set_session=session_id)

    def _login_page(self, message: str = "") -> bytes:
        note = (f'<div class="banner banner-bad">{esc(message)}</div>'
                if message else "")
        return page("Sign in",
                    f'<div class="login">{note}<h1>{PRODUCT}</h1>'
                    '<p class="sub">Owner control centre.</p>'
                    '<form method="post" action="/login">'
                    '<label for="p">Password</label>'
                    '<input id="p" name="password" type="password" '
                    'autocomplete="current-password" required>'
                    '<p><button class="primary" type="submit">Sign in</button></p>'
                    "</form></div>", authenticated=False)

    def _logout(self, request: Request, session_id: str, session) -> Response:
        # A GET must not end a session: a logout on an <img> tag is CSRF too,
        # and the annoying kind.
        if request.method != "POST" or session is None:
            return self._redirect("/login")
        if not self.sessions.valid_csrf(session_id, request.form.get("csrf", "")):
            return Response(403, page("Refused", self._refusal(
                "That sign-out did not carry this session's token."),
                authenticated=False), SECURITY_HEADERS)
        self.sessions.destroy(session_id)
        return Response(303, b"", (("Location", "/login"),) + SECURITY_HEADERS[1:],
                        clear_session=True)

    # ----------------------------------------------------------------- posts
    def _post(self, request: Request, session_id: str, session) -> Response:
        if not self.sessions.valid_csrf(session_id, request.form.get("csrf", "")):
            return Response(403, page("Refused", self._refusal(
                "That request did not carry this session's token, so it was "
                "not performed."), path=request.path, csrf=session["csrf"]),
                SECURITY_HEADERS)
        if request.path != "/intent":
            return Response(404, page("Not found", self._refusal(
                "Nothing accepts a form at that address."),
                path=request.path, csrf=session["csrf"]), SECURITY_HEADERS)

        verb = request.form.get("verb", "")
        try:
            intent_class = intents_module.classify(verb)
        except ValueError as exc:
            return Response(400, page("Refused", self._refusal(str(exc)),
                                      path="/", csrf=session["csrf"]),
                            SECURITY_HEADERS)
        if verb in auth_module.REAUTH_REQUIRED:
            supplied = request.form.get("password", "")
            if not (self.password_hash
                    and auth_module.check_password(supplied, self.password_hash)):
                return Response(403, page("Refused", self._refusal(
                    "That action needs the owner's password again, in this "
                    "session. A stolen session is not the owner."),
                    path="/", csrf=session["csrf"]), SECURITY_HEADERS)
            self.sessions.note_reauth(session_id)

        record = self.intents.write(
            verb=verb, requested_by="web:owner",
            why=request.form.get("why", "") or f"requested from the control centre",
            subject=request.form.get("subject", ""),
            params={k: v for k, v in request.form.items()
                    if k not in ("csrf", "verb", "why", "subject", "password")})
        note = (
            "Recorded and queued. This is a <strong>request</strong>: the "
            "runtime executes it under its own authorities, and a "
            "consequential one needs an owner approval the control centre "
            "cannot produce."
            if intent_class == intents_module.CONSEQUENTIAL else
            "Recorded and queued for the runtime.")
        return Response(200, page(
            "Requested",
            f"<h1>{esc(intents_module.describe(verb))}</h1>"
            f'<p class="sub">{note}</p>'
            + table(["field", "value"],
                    [[esc("intent"), esc(record["id"])],
                     [esc("class"), pill(intent_class,
                                         "warn" if intent_class ==
                                         intents_module.CONSEQUENTIAL else "info")],
                     [esc("needs owner approval"),
                      esc("yes" if record["requires_owner_approval"] else "no")]])
            + f"<p>{link('/', 'Back to the command centre')}</p>",
            path="/", csrf=session["csrf"]), SECURITY_HEADERS)

    def _refusal(self, text: str) -> str:
        return (f'<div class="banner banner-bad">{esc(text)}</div>'
                f"<p>{link('/', 'Back')}</p>")

    def _redirect(self, where: str) -> Response:
        return Response(303, b"", (("Location", where),) + SECURITY_HEADERS[1:])

    # --------------------------------------------------------------- banners
    def _banners(self) -> list[tuple[str, str]]:
        out = []
        posture = self.read.posture()
        if posture["halted"]:
            out.append(("bad", f"HALT is engaged. {PRODUCT} is taking no new work "
                               "and performing no external effects."))
        if not posture["simulation_only"]:
            out.append(("warn", f"Real external effects are ENABLED. {PRODUCT} can "
                                "send and charge."))
        intact, why = self.read.audit_chain_intact()
        if not intact:
            out.append(("bad", f"Audit chain problem: {why}"))
        return out

    # ------------------------------------------------------- command centre
    def command_centre(self, request: Request):
        counts = self.read.job_counts()
        cash = self.read.money()
        posture = self.read.posture()
        relay = self.read.relay_queue()
        proposals = [p for p in self.read.capability_proposals()
                     if p.get("kind") == "PROPOSED"]
        awaiting = self.read.skill_projects(stage="AWAITING_OWNER")
        blocked = counts.get("BLOCKED", 0)

        attention = []
        if relay:
            attention.append((f"{len(relay)} message(s) drafted and waiting for "
                              "you to pass on", "/service"))
        if awaiting:
            attention.append((f"{len(awaiting)} skill(s) certified and waiting "
                              "for your decision", "/skills"))
        if proposals:
            attention.append((f"{len(proposals)} capability proposal(s) "
                              "unanswered", "/capabilities"))
        if blocked:
            attention.append((f"{blocked} job(s) blocked", "/jobs?state=BLOCKED"))
        # Not read from the posture dict a second time: the board decides what
        # each posture fact means, and two places deciding that is how this
        # surface came to disagree with itself.
        board = board_module.build(
            self.read, heartbeat_path=self.read.policy_value(
                "runtime", "heartbeat_path", default="") or "")
        for name, entry in board.sorted_worst_first():
            if entry.state == status.VERIFIED_HEALTHY:
                continue
            attention.append((f"{name}: {entry.detail}", "/health"))

        cards = "".join(
            f'<div class="card"><div class="k">{esc(k)}</div>'
            f'<div class="v">{v}</div><div class="n">{esc(n)}</div></div>'
            for k, v, n in (
                ("Collected", money(cash["collected_cents"]),
                 "verified payments only"),
                ("Outstanding", money(cash["outstanding_cents"]),
                 "invoiced and not yet collected"),
                ("Active jobs", str(sum(
                    counts.get(s, 0) for s in ("ACCEPTED", "EXECUTING",
                                               "VERIFYING"))), "in progress"),
                ("Awaiting payment", str(counts.get("AWAITING_PAYMENT", 0)),
                 "work done, money not in"),
                ("Blocked", str(blocked), "needs an answer"),
            ))

        needs = (table(["what", "where"],
                       [[esc(text), link(href, "open")]
                        for text, href in attention],
                       empty="Nothing needs your attention.")
                 if attention else
                 '<p class="empty">Nothing needs your attention.</p>')

        simulated = ""
        if cash["simulated_cents"]:
            simulated = (
                '<p class="sub">'
                + pill("SIMULATED", "info")
                + f" {esc(money(cash['simulated_cents']))} of fixture money "
                  "exists in this database and is counted nowhere above.</p>")

        return (f"<h1>{PRODUCT} Command Centre</h1>"
                f'<p class="sub">What needs you, then what is happening.</p>'
                + _board_banner(board)
                + f"<h2>Needs your attention</h2>{needs}"
                + "<h2>System status</h2>" + _board_table(board)
                + f"<h2>Business</h2>{cards}{simulated}"
                + "<h2>Business performance</h2>"
                + _pipeline_section(self.read)
                + "<h2>Business connections</h2>"
                + _connection_cards(self.read)
                + f"<h2>Jobs by state</h2>"
                + table(["state", "jobs"],
                        [[esc(s), esc(n)] for s, n in sorted(counts.items())],
                        empty="No jobs yet."),
                "Command centre")

    # ------------------------------------------------------------------ jobs
    def jobs(self, request: Request):
        state = request.query.get("state", "")
        rows = self.read.jobs(state=state)
        return ("<h1>Jobs</h1>"
                f'<p class="sub">{esc(str(len(rows)))} job(s)'
                + (f" in state {esc(state)}" if state else "") + ".</p>"
                + table(["job", "client", "state", "title", ""],
                        [[esc(r.get("id", ""))[:18], esc(r.get("client_id", "")),
                          _state_pill(r.get("state", "")),
                          esc(r.get("title", ""))[:60],
                          link(f"/job?id={urllib.parse.quote(r.get('id',''))}",
                               "open")]
                         for r in rows], empty="No jobs yet."),
                "Jobs")

    def job_detail(self, request: Request):
        job_id = request.query.get("id", "")
        job = self.read.job(job_id)
        if not job:
            return (self._refusal("No such job."), "Job")
        requirements = self.read.requirements(job_id)
        artifacts = self.read.artifacts(job_id)
        verification = self.read.verification(job_id)
        conversation = self.read.conversation(job_id)

        timeline = table(
            ["when", "what", "authority", "result"],
            [[esc(e.get("ts", ""))[:19], esc(_plain_event(e.get("event", ""))),
              esc(e.get("authority", "")), esc(e.get("result", ""))[:70]]
             for e in self.read.job_audit(job_id)],
            empty="Nothing recorded yet.")

        return (f"<h1>Job {esc(job_id)[:18]}</h1>"
                f'<p class="sub">{esc(job.get("title",""))} — client '
                f'{esc(job.get("client_id",""))} — '
                f'{_state_pill(job.get("state",""))}</p>'
                "<h2>What was agreed</h2>"
                + table(["requirement", "check", "source", "status"],
                        [[esc(r.get("text", ""))[:70],
                          esc(r.get("check_name", "")),
                          esc(r.get("source", "")),
                          esc(r.get("status", ""))] for r in requirements],
                        empty="No requirements committed.")
                + "<h2>Verification</h2>"
                + table(["requirement", "verdict", "verifier", "detail"],
                        [[esc(v.get("requirement_id", "")),
                          _verdict_pill(v.get("verdict", "")),
                          esc(v.get("verifier", ""))[:40],
                          esc(v.get("detail", ""))[:70]] for v in verification],
                        empty="Not verified yet.")
                + "<h2>Deliverables</h2>"
                + table(["file", "bytes", "digest"],
                        [[esc(a.get("path", "").rsplit("/", 1)[-1]),
                          esc(a.get("size", "")),
                          f"<code>{esc(str(a.get('digest',''))[:23])}…</code>"]
                         for a in artifacts], empty="Nothing produced yet.")
                + "<h2>Client conversation</h2>"
                + table(["when", "phase", "message"],
                        [[esc(c.get("ts", ""))[:19], esc(c.get("phase", "")),
                          esc(c.get("body", ""))[:110]] for c in conversation],
                        empty="No messages.")
                + "<h2>Timeline</h2>" + timeline,
                "Job")

    # --------------------------------------------------------------- clients
    def clients(self, request: Request):
        return ("<h1>Clients</h1>"
                + table(["client", "jobs", "last seen", ""],
                        [[esc(c["client_id"]), esc(c["jobs"]),
                          esc(str(c.get("last_seen", ""))[:19]),
                          link("/client?id="
                               + urllib.parse.quote(c["client_id"]), "open")]
                         for c in self.read.clients()],
                        empty="No clients yet."),
                "Clients")

    def client_detail(self, request: Request):
        client_id = request.query.get("id", "")
        if not client_id:
            return (self._refusal("No client named."), "Client")
        jobs = self.read.client_jobs(client_id)
        if not jobs:
            return (self._refusal("No such client, or no jobs for them."),
                    "Client")
        messages = []
        for job in jobs:
            for row in self.read.conversation(job.get("id", "")):
                messages.append(row)
        return (f"<h1>{esc(client_id)}</h1>"
                f'<p class="sub">{esc(str(len(jobs)))} job(s). This page shows '
                "only this client's work.</p>"
                + table(["job", "state", "title", ""],
                        [[esc(j.get("id", ""))[:18],
                          _state_pill(j.get("state", "")),
                          esc(j.get("title", ""))[:60],
                          link(f"/job?id={urllib.parse.quote(j.get('id',''))}",
                               "open")] for j in jobs])
                + "<h2>Conversation</h2>"
                + table(["when", "phase", "message"],
                        [[esc(m.get("ts", ""))[:19], esc(m.get("phase", "")),
                          esc(m.get("body", ""))[:110]] for m in messages],
                        empty="No messages."),
                "Client")

    # ---------------------------------------------------------- work sources
    def files(self, request: Request):
        """Every file Solvent has produced — listed, and deliberately not served.

        The page names each deliverable, says which client it belongs to, and
        reports whether verification passed on that exact digest. It does not
        hand the file over, and there is no route here that does.

        That is the point rather than an omission. A deliverable is the client's
        confidential work product, and this process is the one most likely to be
        reached from outside: it listens on a socket, and the rest of Solvent
        does not. A control centre that could stream client files would turn a
        stolen session into a data breach, which is a larger loss than anything
        else on this website could cause. The owner reads a path here and opens
        it on the machine, which is an action Solvent is not part of.
        """
        job = request.query.get("job", "")
        client = request.query.get("client", "")
        rows = self.read.deliverables(job_id=job, client_id=client)
        body = []
        for row in rows:
            digest = row.get("digest", "")
            counts = self.read.deliverable_verification(digest)
            if counts["total"] == 0:
                verdict = pill("not verified", "warn")
            elif counts["failed"]:
                verdict = pill(f"{counts['failed']} failing", "bad")
            else:
                verdict = pill(f"{counts['passed']} passing", "ok")
            body.append([
                esc(row.get("role", "")),
                esc(row.get("media_type", "")),
                esc(f"{row.get('size', 0):,} bytes"),
                # Enough digest to compare against the one on the job page,
                # and not the whole thing, which is noise at this width.
                f'<code>{esc(digest[:16])}</code>' if digest else "—",
                verdict,
                esc(row.get("client_id", "")),
                link(f"/job?id={urllib.parse.quote(row.get('job_id', ''))}",
                     esc(row.get("job_id", ""))[:14]),
                esc(row.get("ts", ""))[:19],
            ])
        scope = ""
        if job:
            scope = f" for job {esc(job)}"
        elif client:
            scope = f" for {esc(client)}"
        return ("<h1>Files and deliverables</h1>"
                f'<p class="sub">{esc(str(len(rows)))} file(s){scope}. '
                f"This page lists what {PRODUCT} produced and does not serve it: "
                "open a file on the machine, not through the website.</p>"
                + table(["role", "type", "size", "digest", "verification",
                         "client", "job", "produced"], body,
                        empty="No files have been produced yet.")
                + '<p class="sub">Verification is counted against each file\'s '
                  "own digest, so a corrected file does not inherit the passes "
                  "of the one it replaced.</p>",
                "Files")

    # ------------------------------------------------------------ resilience

    def incidents(self, request: Request):
        """§42. What has failed, whether it has failed before, and where it got to."""
        status = request.query.get("status", "")
        component = request.query.get("component", "")
        fingerprint = request.query.get("fingerprint", "")
        rows = self.read.incidents(status=status, component=component,
                                   fingerprint=fingerprint)
        summary = self.read.incident_components()
        body = [[
            _incident_pill(r.get("status", "")),
            _severity_pill(r.get("severity", "")),
            esc(r.get("component", "")),
            esc(r.get("operation", ""))[:28],
            (f'<code>{esc(r.get("fingerprint", "")[:10])}</code>'
             if r.get("fingerprint") else "—"),
            (esc(str(r.get("recurrence_count", 1)))
             + ("×" if (r.get("recurrence_count") or 1) > 1 else "")),
            esc(r.get("last_seen", ""))[:19],
            link(f"/incident?id={urllib.parse.quote(r.get('id', ''))}", "open"),
        ] for r in rows]
        head = ("<h1>Incidents</h1>"
                f'<p class="sub">{esc(str(len(rows)))} incident(s)'
                + (f" filtered by {esc(status or component or fingerprint)}"
                   if (status or component or fingerprint) else "")
                + f". An incident is a failure {PRODUCT} recorded, not a message "
                  "it printed.</p>")
        if summary:
            head += table(
                ["component", "incidents", "still open"],
                [[link(f"/incidents?component={urllib.parse.quote(r['component'])}",
                       esc(r["component"])),
                  esc(str(r["n"])),
                  (pill(str(r["open"]), "warn") if r["open"] else pill("0", "ok"))]
                 for r in summary], empty="")
        return (head + table(
            ["status", "severity", "component", "operation", "fingerprint",
             "seen", "last", ""], body,
            empty="Nothing has failed yet. An empty list here is the state of a "
                  "system that has not yet had a bad day, not a system that is "
                  "not watching."),
                "Incidents")

    def incident_detail(self, request: Request):
        """§43. The twelve questions an owner actually asks, answered in order."""
        incident_id = request.query.get("id", "")
        row = self.read.incident(incident_id)
        # ``one`` returns ``{}`` for no row, not ``None``. Checking ``is None``
        # meant an unknown id rendered a complete, empty incident page — down
        # to "this is a new failure class" — which is inventing an incident.
        if not row:
            return "<h1>Incident</h1><p class=\"sub\">No such incident.</p>", "Incident"
        fingerprint = row.get("fingerprint", "")
        related = self.read.related_incidents(fingerprint, exclude=incident_id)
        lesson = self.read.incident_lesson(fingerprint)
        frames = self.read.flight_frames(row.get("trace_id", "")) \
            if row.get("trace_id") else []

        answers = [
            ("What happened?",
             f"{esc(row.get('error_class', ''))} in "
             f"{esc(row.get('component', ''))}"),
            ("Where did it start?",
             f"{esc(row.get('component', ''))} / {esc(row.get('operation', ''))}"),
            (f"What was {PRODUCT} doing?", esc(row.get("trigger", "")) or "—"),
            ("What were the symptoms?", esc(row.get("symptoms", "")) or "—"),
            ("What was affected?",
             ", ".join(filter(None, [esc(row.get("job_id", "")),
                                     esc(row.get("client_id", ""))])) or "nothing recorded"),
            ("Did any external action become uncertain?",
             esc(row.get("external_uncertainty", ""))
             or "nothing recorded as uncertain"),
            ("Last safe checkpoint", esc(row.get("checkpoint", "")) or "none recorded"),
            (f"What did {PRODUCT} do?", esc(row.get("recovery_action", "")) or "—"),
            ("Did it recover?", esc(row.get("recovery_result", "")) or "—"),
            ("What caused it?",
             esc(row.get("root_cause", "")) or "not established yet"),
            ("What fix was applied?", esc(row.get("fix", "")) or "none yet"),
            ("How was the fix verified?",
             esc(row.get("verification_ref", ""))
             or "not verified — a fix is not proven by the symptom stopping"),
            ("How will it be prevented?",
             esc(row.get("prevention", "")) or "nothing recorded yet"),
            ("Has this happened before?",
             (f"yes — {len(related)} earlier incident(s) with this fingerprint"
              if related else "no; this is a new failure class")),
        ]
        out = [f"<h1>Incident {esc(incident_id)[:20]}</h1>",
               f'<p class="sub">{_incident_pill(row.get("status", ""))} '
               f'{_severity_pill(row.get("severity", ""))} · first seen '
               f'{esc(row.get("first_seen", ""))[:19]} · seen '
               f'{esc(str(row.get("recurrence_count", 1)))} time(s)</p>',
               table(["question", "answer"],
                     [[esc(q), a] for q, a in answers], empty="")]
        if row.get("owner_action"):
            out.append('<div class="banner banner-warn">Owner action: '
                       + esc(row["owner_action"]) + "</div>")
        if lesson:
            out.append("<h2>What was learned last time</h2>")
            out.append(table(
                ["", ""],
                [["Root cause", esc(lesson.get("root_cause", ""))],
                 ["Fix", esc(lesson.get("fix", ""))],
                 ["Prevention", esc(lesson.get("prevention", ""))],
                 ["Verified by", esc(lesson.get("verification_ref", ""))]],
                empty=""))
        out.append("<h2>Timeline</h2>")
        out.append(table(["when", "stage", "detail", "by"],
                         [[esc(e.get("ts", ""))[:19], esc(e.get("stage", "")),
                           esc(e.get("detail", ""))[:120], esc(e.get("actor", ""))]
                          for e in self.read.incident_timeline(incident_id)],
                         empty="No timeline recorded."))
        if related:
            out.append("<h2>Earlier incidents of the same kind</h2>")
            out.append(table(["when", "status", ""],
                             [[esc(r.get("ts", ""))[:19],
                               _incident_pill(r.get("status", "")),
                               link(f"/incident?id={urllib.parse.quote(r['id'])}",
                                    "open")] for r in related], empty=""))
        out.append(f"<h2>What {PRODUCT} was doing</h2>")
        out.append('<p class="sub">The flight recorder, sanitised. Secrets are '
                   "removed as frames are written, not as they are shown.</p>")
        out.append(table(["when", "component", "operation", "state", "checkpoint"],
                         [[esc(f.get("ts", ""))[:19], esc(f.get("component", "")),
                           esc(f.get("operation", "")), esc(f.get("state", "")),
                           esc(f.get("checkpoint", ""))] for f in frames],
                         empty="No flight frames were recorded for this incident."))
        return "".join(out), "Incident"

    def breakers(self, request: Request):
        """§47. What is contained, why, and when it will be probed again."""
        rows = self.read.breakers()
        body = [[
            esc(r.get("component", "")),
            _breaker_pill(r.get("state", "")),
            esc(str(r.get("failure_count", 0))),
            esc(r.get("why", ""))[:60] or "—",
            esc(r.get("opened_at", ""))[:19] or "—",
            esc(r.get("canary_result", "")) or "—",
        ] for r in rows]
        return ("<h1>Circuit breakers</h1>"
                '<p class="sub">A breaker contains a failing dependency. It does '
                "not grant anything: nothing here can route around a block, "
                "because choosing a different provider is a permission question "
                "and containment cannot answer one.</p>"
                + table(["component", "state", "failures", "why", "opened",
                         "canary"], body,
                        empty="No dependency has failed enough to be contained."),
                "Circuit breakers")

    def diagnostics(self, request: Request):
        """§44. Plain English first, and no green light DeskPilot has not earned.

        This page **renders** the diagnostics authority's recorded report. It
        does not decide what any of it means. It used to: this process cannot
        construct a :class:`~solvent.harness.Solvent` (the egress hook denies
        ``socket.bind``, so a process that serves a port can never hold the
        authorities), so the page carried its own copy of all eighteen checks.
        Two copies of one judgement is two answers to one question, and they
        drift. The runtime records the report; this renders it.

        The cost of that is honesty about time: a report is evidence about when
        it ran. A stale or missing one is shown as stale or missing, never as a
        current verdict, because the moment an owner most needs this page is the
        moment the runtime writing it has stopped.
        """
        report = self.read.latest_diagnostics()
        findings = report["findings"]

        if not report["present"]:
            banner = ('<div class="banner banner-warn">No diagnostics have been '
                      "recorded yet. The checks run in the "
                      f"{esc(ENGINE.lower())} runtime, not in this website, so "
                      "this page has nothing to show until the runtime has "
                      "run once.</div>")
        elif report["stale"]:
            banner = ('<div class="banner banner-bad">These checks are stale — '
                      f"last run {esc(_ago(report['age_seconds']))} ago. They "
                      "describe the system as it was then, not as it is now. "
                      f"Check that the {esc(ENGINE.lower())} runtime is "
                      "running.</div>")
        elif report["worst"] in ("UNSAFE", "FAILED"):
            banner = ('<div class="banner banner-bad">Something is wrong: '
                      f"{esc(report['worst'])}. The rows below say what.</div>")
        elif report["worst"] in ("UNKNOWN", "OWNER_ACTION_REQUIRED"):
            banner = ('<div class="banner banner-warn">Some things could not be '
                      "confirmed, or are waiting on you. Unknown is not the "
                      "same as fine.</div>")
        else:
            banner = ""

        when = (f"Last run {esc(report['ran_at'])} "
                f"({esc(_ago(report['age_seconds']))} ago)."
                if report["present"] else "Never run.")

        return (banner + "<h1>Diagnostics</h1>"
                f'<p class="sub">Every check {PRODUCT} can make about itself. '
                "A check that could not run reads UNKNOWN rather than passing, "
                "and a control nobody recorded reads UNSAFE rather than safe. "
                f"{when}</p>"
                # §7. The engine identity is useful exactly here, on a
                # technical page, and nowhere the owner is simply working.
                f'<p class="sub muted">Engine: {ENGINE}. Internal names —'
                f" the <code>{ENGINE.lower()}</code> command, "
                f"<code>{ENGINE.lower()}.service</code>, database tables and "
                f"audit records — stay {ENGINE}. {PRODUCT} is the product "
                f"they run.</p>"
                + table(["check", "state", "what it means", "what to do"],
                        [[esc(f["name"]), _diag_pill(f["state"]),
                          esc(f["detail"]), esc(f["owner_action"]) or "—"]
                         for f in findings],
                        empty="No checks have been recorded.")
                + self._host_metrics(),
                "Diagnostics")

    def _host_metrics(self) -> str:
        """§8. Numbers, with no opinion attached.

        What this process can measure from where it stands: it reads a file and
        holds no authorities, so it reports the database it can see and says
        nothing about a runtime it cannot.
        """
        import shutil

        rows = []
        try:
            path = pathlib.Path(self.read.path)
            total = 0
            for suffix in ("", "-wal", "-shm"):
                candidate = pathlib.Path(str(path) + suffix)
                if candidate.exists():
                    total += candidate.stat().st_size
            rows.append(["Database size", f"{total:,} bytes (including the "
                                          "write-ahead log)"])
            usage = shutil.disk_usage(path.parent)
            rows.append(["Disk", f"{round(100 * usage.used / usage.total, 1)}% "
                                 f"used, {usage.free / (1024 ** 3):.2f} GB free"])
        except OSError as exc:
            rows.append(["Storage", f"could not be measured: {exc}"])
        rows.append(["Audit events", f"{len(self.read.audit(limit=10 ** 9)):,}"])
        rows.append(["Jobs", f"{sum(self.read.job_counts().values()):,}"])
        return ("<h2>Host and storage</h2>"
                '<p class="sub">Measured from this process, which reads the '
                "database file and holds no authorities. Runtime uptime and "
                "memory belong to the service process and are reported by "
                "`solvent health`.</p>"
                + table(["measure", "value"],
                        [[esc(a), esc(b)] for a, b in rows], empty=""))

    def notifications(self, request: Request):
        """§45. What was sent, what got through, and what is still unanswered."""
        settings = self.read.notification_settings()
        rows = self.read.notifications()
        unresolved = self.read.unresolved_notifications()
        configured = [
            ("Owner phone", esc(settings["owner_phone_mask"])
             or f"not configured — {PRODUCT} cannot reach you away from here"),
            ("SMS provider", esc(settings["sms_provider"]) or "not configured"),
            ("Voice provider", esc(settings["voice_provider"]) or "not configured"),
            ("Escalate after",
             f"{esc(str(settings['escalate_after_seconds']))} seconds without "
             "an acknowledgement"),
        ]
        banner = ""
        if unresolved:
            banner = ('<div class="banner banner-warn">'
                      f"{len(unresolved)} notification(s) unresolved.</div>")
        return (banner + "<h1>Notifications</h1>"
                f'<p class="sub">{PRODUCT} tells you what happened and where to '
                "look. It never sends a key, client data or figures, because a "
                "text is read off a lock screen. Receiving one approves "
                "nothing.</p>"
                + table(["setting", "value"],
                        [[esc(name), value] for name, value in configured],
                        empty="")
                + "<h2>Recent</h2>"
                + table(["when", "severity", "event", "state", "channels",
                         "detail"],
                        [[esc(r.get("ts", ""))[:19],
                          _severity_pill(r.get("severity", "")),
                          esc(r.get("event", "")),
                          _delivery_pill(r.get("state", "")),
                          esc(r.get("channels", "")) or "—",
                          esc(r.get("detail", ""))[:60] or "—"]
                         for r in rows],
                        empty="Nothing has been sent."),
                "Notifications")

    def recommendations(self, request: Request):
        """§7. What Solvent suggests, and what it has no power to do about it.

        A recommendation carries zero authority. Each row says which of three
        things it is, because the difference is the only thing that matters:
        something already handled under existing policy, something waiting on
        the owner, or a suggestion nobody has acted on.
        """
        rows = []

        for proposal in self.read.capability_proposals():
            if proposal.get("kind") != "PROPOSED":
                continue
            rows.append(("Skill", f"{proposal.get('name', '')} was proposed",
                         esc(proposal.get("why", ""))[:80],
                         "OWNER DECISION REQUIRED"))
        for project in self.read.skill_projects():
            if project.get("stage") == "AWAITING_OWNER":
                rows.append(("Skill", f"{project.get('skill', '')} is certified "
                                      "and waiting", "the Lab finished it",
                             "OWNER DECISION REQUIRED"))
        for breaker in self.read.breakers():
            if breaker.get("state") != "CLOSED":
                rows.append(("Resilience",
                             f"{breaker.get('component', '')} is contained",
                             "it failed repeatedly and was contained "
                             "automatically",
                             "AUTOMATICALLY HANDLED"))
        for incident in self.read.incidents(open_only=True):
            if incident.get("status") == "OWNER_ACTION_REQUIRED":
                rows.append(("Resilience",
                             f"incident in {incident.get('component', '')}",
                             esc(incident.get("owner_action", ""))[:80],
                             "OWNER DECISION REQUIRED"))
            elif (incident.get("recurrence_count") or 1) >= 3:
                rows.append(("Resilience",
                             f"{incident.get('component', '')} keeps failing",
                             f"seen {incident.get('recurrence_count')} times; "
                             "a root cause is worth establishing",
                             "RECOMMENDATION"))
        for gap in self.read.capability_gaps():
            # §55/§56. Evidence, and an explicit statement that it is not
            # permission to do anything about itself. Budget is advertised,
            # not earned, and is labelled so nobody reads it as profit.
            rows.append((
                "Capability",
                f"{gap['need']} was asked for {gap['occurrences']} time(s)",
                f"across {len(gap['sources'])} source(s); "
                f"{money(gap['advertised_cents'])} advertised, not earned. "
                "Whether to build it is yours and the Skills Lab's to decide",
                "RECOMMENDATION"))

        posture = self.read.policy_value("network", "default_deny", default=None)
        if not posture:
            rows.append(("Security", "record the host firewall posture",
                         "unrecorded reads as unsafe, and it is the only "
                         "honest reading", "OWNER DECISION REQUIRED"))
        if not self.read.notification_settings()["owner_phone_mask"]:
            rows.append(("Alerting", "configure owner notifications",
                         f"without a number {PRODUCT} cannot reach you away from "
                         "this website", "RECOMMENDATION"))
        for row in self.read.relay_queue():
            rows.append(("Clients", f"a reply is drafted for {row.get('client_id','')}",
                         "it needs a person to send it", "OWNER DECISION REQUIRED"))

        return ("<h1>Recommendations</h1>"
                '<p class="sub">Suggestions, and nothing more. A recommendation '
                "has no authority: it cannot promote a skill, change a rule, "
                "spend anything or send anything. Each row says whether it is "
                "already handled, waiting on you, or just a suggestion.</p>"
                + table(["area", "what", "why", "standing"],
                        [[esc(area), esc(what), esc(why), _standing_pill(standing)]
                         for area, what, why, standing in rows],
                        empty=f"Nothing to suggest. {PRODUCT} is not inventing work "
                              "to look busy."),
                "Recommendations")

    def wizard(self, request: Request):
        """Kept as a redirect to ``/setup``, which is now the only setup page.

        There were two. This one was an ordered checklist of what blocks what;
        ``/setup`` was a flat list of what had been recorded. They covered the
        same subject matter from one projection and presented it twice, which
        is the duplication an audit found -- and the two had already drifted:
        this page read two policy paths that nothing writes, so steps the owner
        had completed showed as outstanding.

        The URL stays because it is in the navigation, in the installer's
        finish screen and in the owner guide. A redirect keeps those working
        and leaves one page to maintain.
        """
        return "/setup"

    def sources(self, request: Request):
        """§9. Where work may come from, and what may be done about it.

        The permission columns are the point of this page. A source can be
        registered, healthy, connected and still unable to apply for anything,
        because registering a source grants the read side only. Showing the
        granted powers next to the health makes that visible rather than
        something the owner has to infer from nothing going wrong.
        """
        from ..sourceaccess import CONSEQUENTIAL, DISCOVER, PERMISSIONS
        from ..sourcecatalog import CATALOGUE, operational_status

        rows = self.read.work_sources()
        # Derived from what this deployment has actually registered, not from
        # the catalogue alone: the same platform is UNVERIFIED on a machine
        # that has not read its terms and ESTABLISHED on one that has.
        registered = {r.get("name", ""): r for r in rows}

        def status_for(entry):
            row = registered.get(entry.source_id)
            if row is None:
                return operational_status(entry)
            try:
                held = tuple(json.loads(row.get("permissions") or "[]"))
            except ValueError:
                held = ()
            return operational_status(
                entry, registered=True, permissions=held,
                credential_present=bool(row.get("credential_configured"))
                if row.get("auth_required") else None)

        def _rate_limit_for(entry, registered_rows):
            row = registered_rows.get(entry.source_id)
            seconds = float((row or {}).get("min_poll_interval_seconds") or 0)
            if seconds <= 0:
                return "—" if not row else "none recorded"
            return f"no more than once every {seconds / 60:.0f} min"

        def granted(row):
            try:
                return json.loads(row.get("permissions") or "[]")
            except ValueError:
                return []

        def permission_cell(row):
            held = granted(row)
            consequential = [p for p in held if p in CONSEQUENTIAL]
            if not held:
                return pill("none", "neutral")
            label = ", ".join(p.replace("_", " ").lower() for p in held)
            return (pill(f"{len(held)} granted", "warn" if consequential else "ok")
                    + f'<div class="muted">{esc(label)}</div>')

        return ("<h1>Work sources</h1>"
                '<p class="sub">Where work may come from. Registering a source '
                "is not permission to act on it: a new source may be looked at, "
                "and may not apply, bid, message or spend until you say so, one "
                "power at a time.</p>"
                + table(["source", "type", "enabled", "compliance", "health",
                         "credential", "last success", "found", "may"],
                        [[esc(r.get("display_name") or r.get("name", "")),
                          esc(r.get("source_type", "") or r.get("kind", "")),
                          pill("yes", "ok") if r.get("enabled", 1)
                          else pill("no", "neutral"),
                          _compliance_pill(r.get("compliance", "")),
                          _health_pill(r.get("health", "")),
                          pill("configured", "ok")
                          if r.get("credential_configured")
                          else pill("not configured", "neutral"),
                          esc((r.get("last_success") or "never")[:19]),
                          esc(r.get("opportunities_found", 0)),
                          permission_cell(r)]
                         for r in rows], empty="No sources registered.")
                + _source_failures(rows)
                + detail("What a source may do",
                         "<p>Ten separate permissions, because the differences "
                         "between them are the ones that cost money: "
                         "<code>DISCOVER</code>, <code>READ</code> and "
                         "<code>IMPORT</code> are the read side and are granted "
                         "when a source is registered. "
                         "<code>PREPARE_APPLICATION</code>, "
                         "<code>SUBMIT_APPLICATION</code>, "
                         "<code>PREPARE_BID</code>, <code>SUBMIT_BID</code>, "
                         "<code>PREPARE_MESSAGE</code>, "
                         "<code>SEND_MESSAGE</code> and "
                         "<code>SPEND_MONEY</code> are each granted on their "
                         "own. Holding one never implies another, and a granted "
                         "permission is still not permission to act now — the "
                         "Action Gate decides each individual effect.</p>")
                + "<h2>Platforms known to DeskPilot</h2>"
                + f'<p class="sub">Listed is not connected. For every '
                  f'commercial marketplace below, whether automated access is '
                  f'permitted has <strong>not been established</strong> — that '
                  f'is a reading of a specific platform\'s current terms for '
                  f'your account, it changes, and guessing wrong gets the '
                  f'account banned. Every one of them supports you entering an '
                  f'opportunity by hand today, which needs nobody\'s '
                  f'permission and feeds exactly the same pipeline.</p>'
                + table(["platform", "category", "status", "rate limit",
                         "why it is in this state"],
                        [[esc(e.display_name),
                          esc(e.source_type.replace("_", " ").title()),
                          _platform_pill(status_for(e)[0]),
                          esc(_rate_limit_for(e, registered)),
                          esc(status_for(e)[1])]
                         for e in CATALOGUE],
                        empty="None.")
                + "<h2>Opportunities seen</h2>"
                + table(["source", "title", "budget", "risk", "status"],
                        [[esc(o.get("source", "")),
                          esc(o.get("title", ""))[:60],
                          esc(money(o.get("quoted_cents", 0) or 0)),
                          _risk_pill(o.get("risk_worst", "")),
                          esc(o.get("status", ""))]
                         for o in self.read.opportunities()],
                        empty="None yet."),
                "Work sources")

    def _submission_approvals(self) -> str:
        """§38. What clicking approve would actually authorise.

        Written so an owner who does not read code can still make an authority
        decision: what is being sent, to whom, for how much, against which
        capability, by when, and the digest that the authorisation binds to.
        The technical question -- is this package correct -- is already settled,
        which is why "verification: passed" is stated rather than asked.
        """
        waiting = self.read.applications_awaiting_approval()
        manual = self.read.applications_needing_manual_submission()
        unresolved = self.read.submissions_needing_reconciliation()
        parts = []

        if waiting:
            parts.append(
                "<h2>Applications awaiting your authorisation</h2>"
                '<p class="sub">Approving one authorises DeskPilot to transmit '
                "<em>that exact application</em> to that one source. It does "
                "not authorise any other application, any other source, or a "
                "changed price — if the application changes, the "
                "authorisation stops applying.</p>"
                + table(["opportunity", "source", "capability", "price",
                         "deadline", "verification", "authorises sending"],
                        [[esc(a.get("opportunity_id", ""))[:22],
                          esc(a.get("source", "")),
                          esc(a.get("capability_version", "")),
                          esc(money(a.get("price_cents", 0) or 0)),
                          esc(a.get("deadline", "") or "—"),
                          pill("passed", "ok") if int(a.get("verified") or 0)
                          else pill("not passed", "bad"),
                          f'<code>{esc(a.get("digest", "")[:26])}…</code>']
                         for a in waiting], empty="None."))

        if manual:
            parts.append(
                "<h2>Prepared packages you must submit yourself</h2>"
                '<p class="sub">These sources do not accept a '
                "machine-submitted application, so DeskPilot has prepared and "
                "verified the package and a person sends it. Nothing here has "
                "been transmitted.</p>"
                + table(["opportunity", "source", "why it is manual", "price"],
                        [[esc(a.get("opportunity_id", ""))[:22],
                          esc(a.get("source", "")),
                          _submission_mode_pill(a.get("submission_mode", "")),
                          esc(money(a.get("price_cents", 0) or 0))]
                         for a in manual], empty="None."))

        if unresolved:
            parts.append(
                '<div class="banner banner-bad">One or more submissions left '
                "and no answer came back, so whether they arrived is not "
                "known. These must not be sent again until somebody checks the "
                "source.</div>"
                "<h2>Submissions needing reconciliation</h2>"
                + table(["application", "source reference", "when", "detail"],
                        [[esc(a.get("application_id", ""))[:22],
                          esc(a.get("remote_ref", "") or "—"),
                          esc((a.get("ts", "") or "")[:19]),
                          esc(a.get("detail", ""))[:80]]
                         for a in unresolved], empty=""))
        return "".join(parts)

    # ------------------------------------------------------------ skills lab
    def skills(self, request: Request):
        projects = self.read.skill_projects()
        awaiting = [p for p in projects if p.get("stage") == "AWAITING_OWNER"]
        return ("<h1>Skills Lab</h1>"
                f'<p class="sub">What {PRODUCT} wants to be able to do, what it is '
                "building, and what it has proven.</p>"
                + ("<h2>Waiting for your decision</h2>"
                   + table(["skill", "change", "version", "why", ""],
                           [[esc(p.get("skill", "")),
                             _change_pill(p.get("change_type", "")),
                             esc(p.get("target_version", "")),
                             esc(p.get("need", ""))[:60],
                             link("/skill?id="
                                  + urllib.parse.quote(p.get("id", "")), "review")]
                            for p in awaiting]) if awaiting else "")
                + self._skill_inventory()
                + "<h2>All projects</h2>"
                + table(["skill", "stage", "change", "overlap verdict", "need", ""],
                        [[esc(p.get("skill", "")),
                          _stage_pill(p.get("stage", "")),
                          _change_pill(p.get("change_type", "")),
                          esc(p.get("overlap_verdict", "")),
                          esc(p.get("need", ""))[:50],
                          link("/skill?id=" + urllib.parse.quote(p.get("id", "")),
                               "open")] for p in projects],
                        empty="No skill projects yet.")
                + "<h2>Versions</h2>"
                + table(["skill", "version", "change", "lifecycle", "code"],
                        [[esc(v.get("skill", "")), esc(v.get("version", "")),
                          _change_pill(v.get("change_type", "")),
                          _lifecycle_pill(v.get("lifecycle", "")),
                          f"<code>{esc(str(v.get('fingerprint',''))[:19])}…</code>"]
                         for v in self.read.skill_versions()],
                        empty="No versions recorded."),
                "Skills Lab")

    def skill_detail(self, request: Request):
        project_id = request.query.get("id", "")
        project = self.read.skill_project(project_id)
        if not project:
            return (self._refusal("No such skill project."), "Skill")
        evidence = self.read.skill_evidence(project_id)
        return (f"<h1>{esc(project.get('skill',''))}</h1>"
                f'<p class="sub">{_stage_pill(project.get("stage",""))} '
                f'{_change_pill(project.get("change_type",""))} '
                f'{esc(project.get("target_version",""))}</p>'
                "<h2>Why it was proposed</h2>"
                f'<p>{esc(project.get("need",""))}</p>'
                f'<p class="muted">Observed on '
                f'{esc(project.get("observed_on","") or "no job recorded")}, '
                f'{esc(project.get("occurrences",1))} time(s).</p>'
                "<h2>Overlap check</h2>"
                f"<p>{pill(project.get('overlap_verdict','') or 'not run', 'info')} "
                f"{esc(project.get('overlap_why',''))}</p>"
                "<h2>Evidence</h2>"
                + table(["claim", "source", "trust", "status", "checked by"],
                        [[esc(e.get("claim", ""))[:70],
                          esc(e.get("source", ""))[:50],
                          esc(e.get("trust", "")),
                          _evidence_pill(e.get("status", "")),
                          esc(e.get("checked_by", ""))[:30]] for e in evidence],
                        empty="No evidence recorded, so nothing may be built.")
                + "<h2>Timeline</h2>"
                + table(["when", "stage", "outcome", "detail"],
                        [[esc(e.get("ts", ""))[:19], esc(e.get("stage", "")),
                          esc(e.get("outcome", "")),
                          esc(e.get("detail", ""))[:80]]
                         for e in self.read.skill_timeline(project_id)],
                        empty="Nothing yet."),
                "Skill")

    def _skill_inventory(self) -> str:
        """§28. What DeskPilot can actually do, from the authoritative record.

        Assembled from two places, both of them sources of truth rather than
        copies: the capability registry says what is proven and deliverable,
        and the Lab's version rows say what was certified and at which
        artifact. Nothing here is hard-coded -- a skill that reaches 1.1
        appears as 1.1 because the registry says so, not because anybody
        edited this page.
        """
        registered = {c.get("name"): c for c in self.read.capabilities()}
        versions = self.read.skill_versions()
        names = sorted(set(registered) | {v.get("skill") for v in versions
                                          if v.get("skill")})
        if not names:
            return ('<h2>Skills</h2><p class="empty">No skill has been '
                    "registered or certified yet. Nothing can be delivered "
                    "until one is.</p>")

        rows = []
        for name in names:
            capability = registered.get(name) or {}
            mine = [v for v in versions if v.get("skill") == name]
            latest = mine[-1] if mine else {}
            proven = bool(capability.get("proven"))
            rows.append([
                esc(name),
                esc(capability.get("version") or latest.get("version") or "—"),
                pill("proven", "ok") if proven
                else pill("not proven", "warn"),
                _lifecycle_pill(latest.get("lifecycle", "")),
                esc((latest.get("fingerprint") or "—")[:24]),
                esc(capability.get("evidence_ref") or
                    latest.get("evidence_ref") or "—")[:40],
                esc(len(mine)),
            ])
        return ("<h2>Skills</h2>"
                f'<p class="sub">Read from the capability registry and the '
                f"Lab's version record. This table is not written anywhere — "
                f"a new version appears here because the registry says so.</p>"
                + table(["skill", "version", "registry", "lifecycle",
                         "artifact", "evidence", "versions"], rows,
                        empty="None."))

    # ---------------------------------------------------------- capabilities
    def capabilities(self, request: Request):
        rows = self.read.capabilities()
        certs = self.read.verifier_certifications()
        return ("<h1>Capabilities</h1>"
                f'<p class="sub">What {PRODUCT} is allowed to sell, and on what '
                "evidence. Derived from the registry, never a second list.</p>"
                + self._library_section()
                + "<h2>Registered for client work</h2>"
                + table(["capability", "version", "proven", "scope", "evidence"],
                        [[esc(r.get("name", "")), esc(r.get("version", "")),
                          pill("proven" if r.get("proven") else "not promoted",
                               "ok" if r.get("proven") else "neutral"),
                          esc(str(r.get("covers", "")))[:70],
                          esc(str(r.get("why", "")))[:50]] for r in rows],
                        empty="Nothing registered.")
                + "<h2>Verifier certifications</h2>"
                + table(["verifier", "version", "state", "checks"],
                        [[esc(str(c.get("verifier_ref", "")))[:40],
                          esc(c.get("capability_version", "")),
                          pill(str(c.get("state", "")),
                               "ok" if c.get("state") == "CERTIFIED" else "bad"),
                          esc(str(c.get("certified_checks", "")))[:60]]
                         for c in certs], empty="None yet.")
                + "<h2>Proposals</h2>"
                + table(["name", "kind", "decision", "why"],
                        [[esc(p.get("name", "")), esc(p.get("kind", "")),
                          esc(p.get("decision", "")),
                          esc(p.get("why", ""))[:60]]
                         for p in self.read.capability_proposals()],
                        empty="None."),
                "Capabilities")

    def _library_section(self) -> str:
        """Every capability this build has, and where each one stands.

        This section exists because the page above it answers a narrower
        question than its heading suggests. "Registered for client work" has
        one row, correctly -- one capability is owner-promoted. An owner
        reading a page headed Capabilities saw a library of one, and four
        skills that are built, certified and cleared for development read as
        four skills that do not exist.

        Two columns say the two different things, because collapsing them is
        the whole problem: *certified* is what the evidence shows, *stance* is
        what the owner decided. A capability can be certified on every check
        it lists and still not be sellable, and that is not a defect to
        explain away -- it is the control, and the page should make it
        obvious rather than leave it to be inferred from an absence.
        """
        rows = self.read.library()
        if not rows:
            return ('<h2>The skill library</h2>'
                    + self._refusal(
                        "No capability is registered in this build, which "
                        "would mean the dispatch table is empty. That is a "
                        "defect rather than an empty library."))
        body = []
        for row in rows:
            earned, listed = row["checks_certified"], row["checks_listed"]
            evidence = pill(f"{row['state']} on {earned} of {listed}",
                            "ok" if row["state"] == "CERTIFIED" and earned
                            else "warn" if earned else "bad")
            stance = pill(row["stance"],
                          "ok" if row["stance"] == "PROMOTED"
                          else "warn" if row["stance"] == "LIMITED" else "neutral")
            body.append([esc(row["name"]), esc(row["version"]), evidence,
                         stance, esc(row["note"])[:70]])
        promoted = sum(1 for r in rows if r["deliverable"])
        return ('<h2>The skill library</h2>'
                f'<p class="sub">{len(rows)} capability(ies) in this build; '
                f'{promoted} cleared for client work. Certified is what the '
                f'evidence shows. Cleared is what you decided, and they are '
                f'different sentences.</p>'
                + table(["capability", "version", "evidence", "your decision",
                         "what that means"], body))

    # ------------------------------------------------------ customer service
    def learning(self, request: Request):
        """What Solvent has concluded, shown next to what it concluded it from.

        The evidence tier is the column that matters, and it is why this page
        is not just a list of lessons. Business Memory refuses a lesson drawn
        from a self-report, and refuses an economic lesson drawn from anything
        short of an external fact, because "the job went well" asserted by the
        component that ran it is not a finding. Showing the tier next to each
        lesson is what lets the owner see that rule holding rather than take it
        on faith — a page of confident conclusions with no provenance is how a
        system talks itself into believing its own output.

        Read-only, like everything here. A lesson is never edited from the
        website: Memory is where learning is written, and giving a second
        writer to the record of what worked would let the component whose
        autonomy depends on looking successful edit the evidence of its
        success.
        """
        kind = request.query.get("kind", "")
        rows = self.read.lessons(kind=kind)
        summary = self.read.lesson_kinds()
        body = []
        for row in rows:
            payload = row.get("payload", "")
            body.append([
                esc(row.get("kind", "")),
                esc(row.get("subject", ""))[:40],
                _tier_pill(row.get("tier", "")),
                f'<code>{esc(payload[:120])}</code>' if payload else "—",
                esc(row.get("evidence_ref", ""))[:18] or "—",
                esc(row.get("ts", ""))[:19],
            ])
        head = (f"<h1>What {PRODUCT} has learned</h1>"
                f'<p class="sub">{esc(str(len(rows)))} lesson(s)'
                + (f" of kind {esc(kind)}" if kind else "") + ". Each one is "
                "shown with the grade of evidence it was drawn from; Memory "
                "refuses a lesson that has none.</p>")
        if summary:
            head += table(
                ["kind", "lessons", "weakest evidence"],
                [[link(f"/learning?kind={urllib.parse.quote(r['kind'])}",
                       esc(r["kind"])),
                  esc(str(r["n"])), _tier_pill(r.get("weakest", ""))]
                 for r in summary], empty="")
        return (head + table(
            ["kind", "subject", "evidence", "what was recorded", "from", "when"],
            body, empty=f"Nothing has been learned yet. {PRODUCT} records a "
                        "lesson only from verified evidence, so an empty list "
                        "here is what a system that has not yet worked looks "
                        "like."),
                "What it learned")

    def service(self, request: Request):
        relay = self.read.relay_queue()
        return ("<h1>Customer service</h1>"
                f'<p class="sub">{PRODUCT} drafts; a person sends. Nothing on this '
                "page has been sent.</p>"
                "<h2>Waiting for you to pass on</h2>"
                + table(["job", "routed to", "stance", "message"],
                        [[link("/job?id=" + urllib.parse.quote(r.get("job_id", "")),
                               esc(str(r.get("job_id", ""))[:14])),
                          esc(r.get("routed_to", "")),
                          esc(r.get("stance", "")),
                          esc(r.get("body", ""))[:120]] for r in relay],
                        empty="Nothing waiting.")
                + "<h2>Feedback and complaints</h2>"
                + table(["job", "kind", "classification", "state", "body"],
                        [[esc(str(f.get("job_id", ""))[:14]),
                          esc(f.get("kind", "")),
                          esc(f.get("classification", "")),
                          esc(f.get("state", "")),
                          esc(f.get("body", ""))[:90]]
                         for f in self.read.feedback()], empty="None.")
                + detail("How a complaint is judged",
                         "<p>A client's feeling is a signal, not a verdict. "
                         "Whether the work was correct is decided by "
                         "verification against the agreed requirements, "
                         "independently of how the client feels about it — an "
                         "angry client with a correct file has not found a "
                         "defect, and a happy client with a wrong one has.</p>"),
                "Customer service")

    # --------------------------------------------------------------- approvals
    def approvals(self, request: Request):
        awaiting_skills = self.read.skill_projects(stage="AWAITING_OWNER")
        proposals = [p for p in self.read.capability_proposals()
                     if p.get("kind") == "PROPOSED"]
        pending_intents = self.read.queued_owner_intents()
        return ("<h1>Approvals</h1>"
                '<p class="sub">Decisions that are genuinely yours. Routine '
                "technical decisions are not here — they are governed already."
                "</p>"
                + self._submission_approvals()
                + "<h2>Certified skills awaiting your decision</h2>"
                + table(["skill", "change", "version", ""],
                        [[esc(p.get("skill", "")),
                          _change_pill(p.get("change_type", "")),
                          esc(p.get("target_version", "")),
                          link("/skill?id=" + urllib.parse.quote(p.get("id", "")),
                               "review")] for p in awaiting_skills],
                        empty="None.")
                + "<h2>Capability proposals</h2>"
                + table(["name", "why"],
                        [[esc(p.get("name", "")), esc(p.get("why", ""))[:80]]
                         for p in proposals], empty="None.")
                + "<h2>Requests queued for the runtime</h2>"
                + table(["intent", "verb", "class", "asked", "why"],
                        [[esc(i.get("id", "")), esc(i.get("verb", "")),
                          pill(_intent_class(i.get("verb", "")),
                               "warn" if _intent_class(i.get("verb", ""))
                               == intents_module.CONSEQUENTIAL else "info"),
                          esc(str(i.get("ts", ""))[:19]),
                          esc(i.get("why", ""))[:60]] for i in pending_intents],
                        empty="None queued.")
                + detail("Why some actions cannot be completed here",
                         "<p>A consequential action needs an approval signed with "
                         "the owner key. This control centre does not hold that "
                         "key — deliberately, so that taking over the website "
                         "does not take over the business. Those actions are "
                         "queued here and completed by the runtime with an owner "
                         "approval.</p>"
                         "<p>This list is the runtime\u2019s own record, not a "
                         "listing of the queue directory \u2014 this page cannot "
                         "read that directory, so that taking over the website "
                         "does not disclose what you have been asking for. A "
                         "request you have just submitted therefore appears here "
                         "once the runtime picks it up, which is within a minute "
                         "rather than immediately.</p>"),
                "Approvals")

    # ------------------------------------------------------------------ money
    def money_page(self, request: Request):
        cash = self.read.money()
        cards = "".join(
            f'<div class="card"><div class="k">{esc(k)}</div>'
            f'<div class="v">{esc(money(v))}</div>'
            f'<div class="n">{esc(n)}</div></div>'
            for k, v, n in (
                ("Collected", cash["collected_cents"], "verified only"),
                ("Invoiced", cash["invoiced_cents"], "asked for"),
                ("Outstanding", cash["outstanding_cents"], "not yet in"),
                ("Recorded costs", cash["recorded_costs_cents"], "ledger"),
            ))
        simulated = (
            f'<div class="banner banner-warn">{pill("SIMULATED","info")} '
            f'{esc(money(cash["simulated_cents"]))} of fixture money is in this '
            "database. It is excluded from every figure above.</div>"
            if cash["simulated_cents"] else "")
        return ("<h1>Money</h1>"
                '<p class="sub">Collected means a payment was verified. Nothing '
                "else counts.</p>" + simulated + cards
                + "<h2>Payments</h2>"
                + table(["job", "state", "invoiced", "collected", "verified by"],
                        [[esc(str(p.get("job_id", ""))[:14]),
                          _payment_pill(p.get("state", "")),
                          esc(money(p.get("amount_cents", 0))),
                          esc(money(p.get("collected_cents", 0))),
                          esc(str(p.get("verification_method", "")))[:40]]
                         for p in self.read.payments()], empty="No payments.")
                + "<h2>Rail events</h2>"
                + table(["event", "type", "job", "applied", "outcome"],
                        [[esc(str(e.get("event_id", ""))[:18]),
                          esc(e.get("event_type", "")),
                          esc(str(e.get("job_id", ""))[:14]),
                          esc("yes" if e.get("applied") else "no"),
                          esc(str(e.get("outcome", "")))[:60]]
                         for e in self.read.payment_events()],
                        empty="No rail events."),
                "Money")

    # ------------------------------------------------------------------ model
    def model(self, request: Request):
        setup = self.read.owner_setup()
        approved = setup["production_model"] or {}
        return ("<h1>AI model</h1>"
                '<p class="sub">Clearance is keyed on the exact artifact. A '
                "family name clears nothing.</p>"
                + table(["field", "value"],
                        [[esc(k), f"<code>{esc(str(v))[:80]}</code>"]
                         for k, v in sorted(approved.items())
                         if k not in ("api_key", "token")],
                        empty="No artifact cleared for commercial use.")
                + detail("Why the digest matters",
                         "<p>Two models can share a tag and differ in licence. "
                         "Clearance names a content digest, so configuring a "
                         "different artifact under the same name is not covered "
                         "by it and fails closed.</p>"),
                "AI model")

    # --------------------------------------------------------------- security
    def security(self, request: Request):
        posture = self.read.posture()
        intact, chain_note = self.read.audit_chain_intact()
        gate_rows = self.read.gate_requests(limit=50)
        refused = [g for g in gate_rows if "REFUS" in str(g.get("decision", "")).upper()]
        rows = [
            ["Simulation only",
             pill("ON", "ok") if posture["simulation_only"] else pill("OFF", "bad"),
             "no real external effects" if posture["simulation_only"]
             else "real effects are enabled"],
            ["HALT",
             pill("ENGAGED", "bad") if posture["halted"] else pill("clear", "ok"),
             "no new work while engaged"],
            ["Egress allowlist",
             pill(f"{len(posture['allowlist'])} destination(s)",
                  "neutral" if not posture["allowlist"] else "info"),
             "empty means nothing may be contacted"],
            ["Audit chain", pill("intact" if intact else "BROKEN",
                                 "ok" if intact else "bad"), esc(chain_note)],
            ["Active sessions", pill(str(self.sessions.count()), "info"),
             "this control centre, in memory only"],
            ["Owner key in this process", pill("never read", "ok"),
             "the control centre cannot mint an approval"],
            ["Database access", pill("read-only", "ok"),
             "this process opens SQLite mode=ro"],
        ]
        return ("<h1>Security</h1>"
                '<p class="sub">What is closed, and what this surface could do '
                "if somebody took it over.</p>"
                + table(["control", "state", "what it means"],
                        [[esc(r[0]), r[1], r[2] if r[1] else esc(r[2])]
                         for r in rows])
                + "<h2>Refused external actions</h2>"
                + table(["when", "action", "destination", "reason"],
                        [[esc(str(g.get("ts", ""))[:19]),
                          esc(g.get("action_class", "")),
                          esc(g.get("destination", ""))[:40],
                          esc(str(g.get("reason", "")))[:60]] for g in refused],
                        empty="None refused recently.")
                + detail("If this website were compromised",
                         "<p>An attacker with this process could read business "
                         "data and queue requests. They could not send a message, "
                         "charge anybody, promote a capability, rewrite policy, "
                         "clear HALT, edit the audit log or read a secret: none "
                         "of those is reachable from here, and the consequential "
                         "ones need an owner approval signed with a key this "
                         "process never holds.</p>"),
                "Security")

    # ------------------------------------------------------------------ audit
    def audit(self, request: Request):
        authority = request.query.get("authority", "")
        rows = self.read.audit(authority=authority)
        intact, note = self.read.audit_chain_intact()
        return ("<h1>Audit</h1>"
                f'<p class="sub">{pill("intact" if intact else "BROKEN", "ok" if intact else "bad")} '
                f"{esc(note)}. This view cannot change it.</p>"
                + table(["#", "when", "what", "authority", "who", "result"],
                        [[esc(e.get("seq", "")), esc(str(e.get("ts", ""))[:19]),
                          esc(_plain_event(e.get("event", ""))),
                          esc(e.get("authority", "")),
                          esc(e.get("initiator", "")),
                          esc(str(e.get("result", "")))[:60]] for e in rows],
                        empty="Nothing recorded."),
                "Audit")

    # ----------------------------------------------------------------- health
    def health(self, request: Request):
        counts = self.read.job_counts()
        intact, note = self.read.audit_chain_intact()
        return ("<h1>System health</h1>"
                + table(["check", "state", "detail"],
                        [["Database reachable", pill("yes", "ok"),
                          esc(self.read.path)],
                         ["Audit chain", pill("intact" if intact else "BROKEN",
                                              "ok" if intact else "bad"),
                          esc(note)],
                         ["Jobs recorded", pill(str(sum(counts.values())), "info"),
                          esc("across " + str(len(counts)) + " state(s)")],
                         ["Unsettled external actions",
                          pill(str(len([g for g in self.read.gate_requests()
                                        if g.get("state") == "ATTEMPTING"])),
                               "info"),
                          esc("actions of unknown outcome")]]),
                "System health")

    def _work_preferences_form(self, request: Request) -> str:
        """The one part of setup this page may change on its own.

        These are qualification filters: they decide which opportunities are
        worth looking at, and nothing else reads them. The Financial Governor
        decides affordability and profitability from its own numbers, so
        widening a filter here cannot make Solvent accept work it could not
        afford -- which is what makes this a SAFE request rather than one
        needing an owner approval.

        Everything else on this page is either an authority change or carries
        personal data, and goes through the approval system or the terminal.
        One form, not nine, because nine forms that mostly say "this one needs
        a terminal" would be worse than a page that says it once.
        """
        session = self.sessions.get(
            request.cookies.get("solvent_session", ""))
        if not session:
            return ""
        prefs = self.read.work_preferences()
        fields = []
        for name, spec in sorted(prefs.items()):
            label = name.replace("_", " ")
            value = "" if spec["value"] is None else str(spec["value"])
            fields.append(
                f'<label for="{esc(name)}">{esc(label)} '
                f'<span class="muted">({spec["minimum"]}&ndash;'
                f'{spec["maximum"]})</span></label>'
                f'<input type="text" inputmode="numeric" id="{esc(name)}" '
                f'name="{esc(name)}" value="{esc(value)}" '
                f'placeholder="not set">')
        return (
            "<h2>Work preferences</h2>"
            '<p class="sub">Which opportunities are worth looking at. These '
            "take effect on their own, within a minute: nothing here can "
            "authorise spending, contact anybody or change what DeskPilot is "
            "allowed to do.</p>"
            '<form method="post" action="/intent">'
            f'<input type="hidden" name="csrf" value="{esc(session["csrf"])}">'
            '<input type="hidden" name="verb" value="set_work_preferences">'
            + "".join(fields)
            + '<label for="why">why</label>'
            '<input type="text" id="why" name="why" '
            'placeholder="for the record" value="changed from the control '
            'centre">'
            '<p><button class="primary">Save work preferences</button></p>'
            "</form>")

    # ---------------------------------------------------------------- bidder
    def bidder(self, request: Request):
        """What DeskPilot will bid, how much it may take on, and why not.

        One page for the three questions an operator actually has
        about bidding: can it bid at all, how much work is it holding,
        and what would it charge. Every number is read through the
        same policy document the authorities read, so the page cannot
        show a limit the gate is not using.

        The form tightens only. Lowering a limit can only make
        DeskPilot take on less, which is safe enough to apply without
        a signature; raising one is a promise to do more and goes
        through an owner approval like every other consequential
        change. The page says which is which rather than offering a
        field that silently fails.
        """
        budget = self.read.capacity_budget()
        flight = self.read.capacity_in_flight()
        eligible = self.read.bid_eligibility()
        terms = self.read.commercial_terms()
        ceiling = JOBS_PER_AGENT_CEILING

        halted = bool(budget["halted"]["value"])
        owed = flight["owed_seconds"]
        allowed = float(budget["max_owed_seconds"]["value"] or 0)
        unconfigured = [name for name in ("max_owed_seconds",
                                          "max_concurrent_jobs")
                        if not budget[name]["value"]]

        state = ("not taking work" if halted or unconfigured
                 else "taking work")
        banner = (
            '<p class="sub">'
            + ("Bidding is halted by the owner. " if halted else "")
            + ("No capacity budget is set, so DeskPilot will take nothing "
               "on: a gate with no number is not a gate. "
               if unconfigured else "")
            + f"{owed:.0f}s of work is owed against "
            + (f"{allowed:.0f}s allowed" if allowed else "no limit set")
            + f", across {flight['jobs']} job(s)."
            + "</p>")

        limits = []
        for name, spec in budget.items():
            if name == "halted":
                continue
            value = spec["value"]
            shown = "not set" if value in (None, "") else str(value)
            note = ""
            if name in ("max_concurrent_jobs", "max_execution_workers",
                        "max_verification_workers"):
                note = (f"never above {ceiling}: this work agent holds "
                        f"three jobs by default and four at the most")
            limits.append([esc(name.replace("_", " ")),
                           pill(shown, "info" if value else "warn"),
                           esc(f"{spec['minimum']}–{spec['maximum']}"),
                           esc(note)])

        held = [[esc(kind), pill(str(v["held"]), "info"),
                 esc(f"{v['seconds']:.0f}s")]
                for kind, v in sorted(flight["kinds"].items())] or [
            ["—", pill("0", "ok"), "nothing owed"]]

        skills = []
        for skill, row in sorted(eligible.items()):
            if row["confident"]:
                mark, tone = "can bid", "ok"
            elif row["eligible"]:
                mark, tone = "provisional", "warn"
            else:
                mark, tone = "blocked", "bad"
            skills.append([
                esc(skill), pill(mark, tone), str(row["client_runs"]),
                str(row["benchmark_runs"]),
                esc("" if row["eligible"] else
                    "no client job has been done, so there is no measured "
                    "time to promise")])

        money_rows = [
            [esc(key.replace("_", " ")),
             esc("not set" if value in (None, "", 0) else str(value))]
            for key, value in sorted(terms.items())]

        return ("<h1>Bidder</h1>"
                + banner
                + "<h2>What it may take on</h2>"
                + table(["limit", "value", "allowed range", "note"], limits)
                + "<h2>What it is holding now</h2>"
                + table(["obligation", "held", "seconds"], held)
                + "<h2>What it may bid on</h2>"
                + '<p class="sub">A skill becomes biddable by having been '
                  "done for a client, not by being benchmarked. Benchmark "
                  "runs are real executions on files DeskPilot generated "
                  "for itself, and a delivery time quoted from one of "
                  "those would be a promise about a file nobody has "
                  "seen.</p>"
                + table(["skill", "bidding", "client runs", "benchmark runs",
                         "why not"], skills)
                + "<h2>What it charges</h2>"
                + table(["term", "value"], money_rows)
                + self._tighten_capacity_form(request, budget),
                "Bidder")

    def _tighten_capacity_form(self, request: Request, budget: dict) -> str:
        session = self.sessions.get(
            request.cookies.get("solvent_session", ""))
        if not session:
            return ""
        fields = []
        for name, spec in sorted(budget.items()):
            if name == "halted":
                continue
            value = "" if spec["value"] is None else str(spec["value"])
            fields.append(
                f'<label for="{esc(name)}">{esc(name.replace("_", " "))} '
                f'<span class="muted">({spec["minimum"]}&ndash;'
                f'{spec["maximum"]})</span></label>'
                f'<input type="text" inputmode="decimal" id="{esc(name)}" '
                f'name="{esc(name)}" value="{esc(value)}" '
                f'placeholder="not set">')
        return (
            "<h2>Tighten the budget</h2>"
            '<p class="sub">These apply on their own, within a minute, '
            "<strong>only if they lower a limit</strong>. Raising one means "
            "DeskPilot promising more work than it promised a minute ago, "
            "so it needs an owner approval and is refused here by name "
            "rather than quietly clamped.</p>"
            '<form method="post" action="/intent">'
            f'<input type="hidden" name="csrf" value="{esc(session["csrf"])}">'
            '<input type="hidden" name="verb" value="tighten_capacity">'
            + "".join(fields)
            + '<label for="why">why</label>'
            '<input type="text" id="why" name="why" '
            'value="tightened from the control centre">'
            '<p><button class="primary">Lower these limits</button></p>'
            "</form>"
            '<form method="post" action="/intent">'
            f'<input type="hidden" name="csrf" value="{esc(session["csrf"])}">'
            '<input type="hidden" name="verb" value="tighten_capacity">'
            '<input type="hidden" name="halted" value="true">'
            '<input type="hidden" name="why" value="owner stopped bidding">'
            '<p><button>Stop taking new work</button></p>'
            "</form>")

    # ------------------------------------------------------------ owner setup
    def setup(self, request: Request):
        """The one Owner Setup page. ``/wizard`` redirects here.

        There were two pages: an ordered checklist of what blocks what, and a
        flat list of what had been recorded. Same subject, same projection,
        presented twice -- and they had already drifted apart, because the
        checklist read two policy paths nothing writes and so reported
        outstanding work the owner had finished.

        One page, in the order things depend on each other, with the five
        states the rest of the control centre uses. Every row says what is
        true, what it rests on, and what to do about it -- including, where
        this process genuinely cannot establish something, that it cannot.

        No secret is displayed and none is asked for here. Values the owner
        provides either go in through a request the runtime executes, or -- for
        the three that carry legal identity or turn simulation off -- at a
        terminal, named on the row.
        """
        setup = self.read.owner_setup()
        settings = self.read.notification_settings()
        proven = [c for c in self.read.capabilities() if c.get("proven")]
        sources = self.read.work_sources()
        firewall = self.read.policy_value("network", "default_deny",
                                          default=None)
        rail_status = setup["rail_status"]

        board = status.Board()

        # 1. The owner key. Evidence or UNKNOWN; never an accusation.
        if setup["owner_key_evidence"] == "PROVEN":
            board.add("1. Owner signing key", status.healthy(
                "an approval has been issued, which nothing can do without "
                "the key",
                evidence="a signed owner approval exists in this database"))
        else:
            board.add("1. Owner signing key", status.unknown(
                "the key lives in the runtime's environment, which this "
                "process cannot read",
                action="Set SOLVENT_OWNER_KEY in the runtime's environment "
                       "file. This turns to verified once an approval has "
                       "been issued with it."))

        # 2. Who the owner contracts as.
        if setup["structure"]:
            missing = [name for name, present in (
                ("legal name", setup["has_legal_name"]),
                ("contact email", setup["has_email"])) if not present]
            if missing:
                board.add("2. Who you contract as", status.owner_action(
                    f"recorded as {setup['structure']}, without "
                    + " or ".join(missing),
                    action="Run `solvent setup contracting` at the terminal.",
                    evidence="governance.contracting in the policy document"))
            else:
                board.add("2. Who you contract as", status.healthy(
                    f"{setup['structure']}, with a legal name and a contact "
                    f"address recorded",
                    evidence="governance.contracting in the policy document"))
        else:
            board.add("2. Who you contract as", status.owner_action(
                "nothing is recorded, and nothing here will invent it",
                action="Run `solvent setup contracting` at the terminal. It "
                       "carries your legal name and address, which is why it "
                       "is not a form on this page."))

        # 3. A cleared model artifact.
        cleared = setup["cleared_models"]
        if cleared:
            board.add("3. A cleared AI model", status.healthy(
                f"{len(cleared)} artifact(s) cleared by digest"
                + (f"; this deployment runs "
                   f"{setup['production_model'].get('tag', 'none')}"
                   if setup["production_model"] else
                   ", and none is marked as the one this deployment runs"),
                evidence="governance.approved_models in the policy document"))
        else:
            board.add("3. A cleared AI model", status.owner_action(
                "no model artifact is cleared, so no model may be used for "
                "paid work",
                action="Open AI model, or run `solvent setup model`."))

        # 4. Something it is allowed to sell.
        if proven:
            board.add("4. A proven capability", status.healthy(
                ", ".join(sorted(c.get("name", "") for c in proven))
                + " cleared for client work",
                evidence="the capability registry, with verifier evidence"))
        else:
            board.add("4. A proven capability", status.blocked(
                "nothing is registered as proven, so work would be done and "
                "then refused at the delivery gate",
                action="Open Capabilities, or run `solvent setup capability`."))

        # 5. The host firewall. Unrecorded reads as unsafe on purpose.
        if firewall:
            board.add("5. Host firewall", status.healthy(
                "default-deny, recorded by the owner",
                evidence="network.default_deny in the policy document"))
        elif firewall is None:
            board.add("5. Host firewall", status.unknown(
                "nobody has recorded what this host's firewall does, and a "
                "script in a repository is not a firewall on a host",
                action="Run deploy/firewall.sh --apply, then `solvent setup "
                       "firewall --default-deny`."))
        else:
            board.add("5. Host firewall", status.unsafe(
                "recorded as NOT default-deny",
                evidence="network.default_deny is false",
                action="Close inbound access, then record it again."))

        # 6. How the owner signs in here.
        board.add("6. Control-centre password", status.healthy(
            "set, and stored as a hash",
            evidence="you are reading this page, which needed it"))

        # 7. Notifications. Optional, and said to be optional.
        if settings["owner_phone_mask"]:
            board.add("7. Owner notifications", status.healthy(
                f"texts go to {settings['owner_phone_mask']}"
                + (f" via {settings['sms_provider']}"
                   if settings["sms_provider"] else
                   ", once an SMS provider is configured"),
                evidence="notifications in the policy document; the number "
                         "itself is not stored"))
        else:
            board.add("7. Owner notifications", status.owner_action(
                "no number is recorded, so nothing can reach you when you are "
                "not looking at this page",
                action="Run `solvent setup notifications`. Your number stays "
                       "in the runtime's environment; only a mask is stored."))

        # 8. A payment rail. Not needed to do the work, only to be paid.
        if rail_status == "LIVE_VERIFIED":
            board.add("8. Payment rail", status.healthy(
                f"{setup['payment_rail']} is live and verified",
                evidence="payment.operational_status in the policy document"))
        else:
            board.add("8. Payment rail", status.owner_action(
                f"{setup['payment_rail'] or 'no rail'} "
                f"({rail_status or 'nothing recorded'}) — not needed until "
                f"you intend to be paid",
                action="Open Money, or run `solvent setup stripe`."))

        # 9. Where work comes from. Deliberately last.
        if sources:
            board.add("9. Work sources", status.healthy(
                f"{len(sources)} source(s) registered",
                evidence="the work-source registry"))
        else:
            board.add("9. Work sources", status.owner_action(
                "none registered — deliberately last, and not needed while "
                "work is hand-fed",
                action="Open Work sources once a supervised job has worked."))

        blocking = [row for row in board.rows[:5]
                    if row[1].state != status.VERIFIED_HEALTHY]
        unknowns = [row for row in board.rows[:5]
                    if row[1].state == status.UNKNOWN]
        if not blocking:
            banner = ('<div class="banner banner-ok">Everything a first '
                      "supervised job needs is verified.</div>")
        else:
            unseen = (f" {len(unknowns)} of them cannot be checked from here."
                      if unknowns else "")
            banner = ('<div class="banner banner-warn">'
                      f"{len(blocking)} of the first five steps are not "
                      f"verified.{unseen}</div>")

        return (banner + "<h1>Owner setup</h1>"
                '<p class="sub">In the order things depend on each other. The '
                "first five are what a first supervised job needs; the rest "
                "are for later and say so. Nothing here asks you to type a "
                "secret into this website.</p>"
                + _board_table(board)
                + self._work_preferences_form(request)
                + detail("Why some values are typed at a terminal",
                         "<p>Three changes are not completed from this page "
                         "even with a valid approval. Your contracting "
                         "identity carries your legal name and address; "
                         "authorising a work source needs an adapter, which "
                         "is code rather than a row in a form; and leaving "
                         "simulation is the single change that turns a "
                         "rehearsal into a business that can spend money and "
                         "contact strangers. Each row above names the command "
                         "for the one it needs.</p>")
                + detail("Where secrets go",
                         "<p>The owner key and any payment secret live in the "
                         "runtime's environment file on the server, readable "
                         "only by the runtime's own account. They are never "
                         "entered here, never displayed here, and never sent "
                         "to a browser. This page reports whether a value is "
                         "present, never what it is.</p>"),
                "Owner setup")


# ------------------------------------------------------------------- helpers
def _state_pill(state: str) -> str:
    tone = {"COMPLETE": "ok", "AWAITING_PAYMENT": "info", "BLOCKED": "warn",
            "FAILED": "bad", "REJECTED": "neutral"}.get(state, "neutral")
    return pill(state or "—", tone)


def _verdict_pill(verdict: str) -> str:
    return pill(verdict or "—", {"PASS": "ok", "FAIL": "bad",
                                 "UNVERIFIABLE": "warn"}.get(verdict, "neutral"))


def _payment_pill(state: str) -> str:
    return pill(state or "—", {"PAID": "ok", "PARTIALLY_PAID": "warn",
                               "REFUNDED": "bad"}.get(state, "neutral"))


def _health_pill(value: str) -> str:
    """A source's operational state. Only HEALTHY reads green: "cannot
    authenticate" and "rate limited" are different problems with different
    answers, and neither of them is fine."""
    return pill(value or "—", {"HEALTHY": "ok", "DEGRADED": "warn",
                               "AUTH_REQUIRED": "warn", "RATE_LIMITED": "warn",
                               "UNSUPPORTED": "neutral", "DISABLED": "neutral",
                               "FAILED": "bad"}.get(value, "neutral"))


def _risk_pill(value: str) -> str:
    """Screening severity. Blank means nothing was noticed, which is not the
    same as a clean bill of health and so is shown as a dash, not a tick."""
    return pill(value or "—", {"SEVERE": "bad", "CONCERN": "warn",
                               "NOTE": "info"}.get(value, "neutral"))


def _source_failures(rows) -> str:
    """Recent source trouble, named. A source failing quietly is a source that
    stops producing work without anybody noticing why."""
    failing = [r for r in rows
               if (r.get("health") or "HEALTHY") != "HEALTHY"
               or int(r.get("consecutive_failures") or 0)]
    if not failing:
        return ""
    return ("<h2>Sources needing attention</h2>"
            + table(["source", "state", "consecutive failures", "last failure",
                     "reason"],
                    [[esc(r.get("display_name") or r.get("name", "")),
                      _health_pill(r.get("health", "")),
                      esc(r.get("consecutive_failures", 0)),
                      esc((r.get("last_failure") or "—")[:19]),
                      esc(r.get("failure_reason", "") or "—")]
                     for r in failing], empty=""))


def _submission_mode_pill(value: str) -> str:
    """Why a source is manual. PROHIBITED reads red: somebody has said no, and
    that is different from nobody having built it yet."""
    return pill(value.replace("_", " ").lower() if value else "—",
                {"AUTOMATED_SUBMISSION_SUPPORTED": "ok",
                 "AUTOMATED_SUBMISSION_SUPPORTED_WITH_AUTH": "ok",
                 "PROHIBITED": "bad",
                 "APPLICATION_PREPARATION_ONLY": "warn",
                 "MANUAL_SUBMISSION_ONLY": "info"}.get(value, "neutral"))


def _compliance_pill(value: str) -> str:
    return pill(value or "—", {"PERMITTED": "ok", "SUSPENDED": "bad",
                               "UNKNOWN": "warn"}.get(value, "neutral"))


def _change_pill(value: str) -> str:
    return pill(value or "—", {"UPGRADE": "warn", "NEW": "info",
                               "UPDATE": "neutral"}.get(value, "neutral"))


def _stage_pill(value: str) -> str:
    """Lifecycle stage. VALIDATION_FAILED reads red because a candidate that
    did not pass is not a neutral state of affairs, and VALIDATION_REQUESTED
    reads amber because "waiting on somebody else" must not look like done."""
    return pill(value.replace("_", " ").lower() if value else "—",
                {"PROMOTED": "ok", "AWAITING_OWNER": "warn",
                 "CERTIFIED": "info", "ABANDONED": "neutral",
                 "VALIDATION_REQUESTED": "warn",
                 "VALIDATION_FAILED": "bad"}.get(value, "neutral"))


def _lifecycle_pill(value: str) -> str:
    return pill(value or "—", {"ACTIVE": "ok", "REVOKED": "bad",
                               "RETIRED": "neutral", "DEPRECATED": "warn",
                               "SUPERSEDED": "neutral"}.get(value, "neutral"))


def _evidence_pill(value: str) -> str:
    return pill(value or "—", {"VERIFIED": "ok", "REFUTED": "bad",
                               "CONTRADICTED": "bad",
                               "UNVERIFIED": "warn"}.get(value, "neutral"))


#: Audit event names in words the owner can read. Unknown events show their own
#: name rather than a guess.
_PLAIN = {
    "job.intake": "job received", "job.qualified": "job evaluated",
    "job.accepted": "job accepted", "job.delivered": "deliverable handed over",
    "payment.event": "payment event", "payment.opened": "invoice recorded",
    "policy.amended": "policy changed",
    "capability.registered": "capability promoted",
    "skillslab.need_recorded": "skill need recorded",
    "skillslab.overlap_checked": "checked against existing skills",
    "skillslab.offered_to_owner": "skill offered for your decision",
    "owner.approval_issued": "owner approval issued",
}


def _tier_pill(tier: str) -> str:
    """Stronger evidence reads greener. T0 is not evidence and says so."""
    if not tier:
        return pill("none", "bad")
    if tier.startswith("T0"):
        return pill(tier, "bad")
    if tier.startswith("T1"):
        return pill(tier, "warn")
    return pill(tier, "ok")


#: Diagnostic severity order, mirroring solvent.diagnostics. UNKNOWN sits
#: above HEALTHY deliberately: it is not good news.
_DIAG_RANK = {"HEALTHY": 0, "DEGRADED": 1, "WARNING": 2, "UNKNOWN": 3,
              "OWNER_ACTION_REQUIRED": 4, "FAILED": 5, "UNSAFE": 6}


def _ago(seconds) -> str:
    """Human elapsed time. Used to say how old a diagnostics report is."""
    if seconds is None:
        return "never"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _diag_pill(state: str) -> str:
    tone = {"HEALTHY": "ok", "DEGRADED": "warn", "WARNING": "warn",
            "UNKNOWN": "warn", "OWNER_ACTION_REQUIRED": "info",
            "FAILED": "bad", "UNSAFE": "bad"}.get(state, "neutral")
    return pill(state.replace("_", " ").lower(), tone)


def _incident_pill(status: str) -> str:
    tone = {"VERIFIED": "ok", "FIXED": "ok", "RECOVERED": "ok",
            "DETECTED": "warn", "CONTAINED": "warn", "RECOVERING": "warn",
            "ROOT_CAUSE_PENDING": "warn", "FIX_PENDING": "warn",
            "RECURRED": "bad", "OWNER_ACTION_REQUIRED": "bad"}.get(status,
                                                                   "neutral")
    return pill(status.replace("_", " ").lower(), tone)


def _severity_pill(severity: str) -> str:
    tone = {"INFO": "neutral", "DEGRADED": "warn", "SERIOUS": "bad",
            "CRITICAL": "bad", "ACTION_REQUIRED": "info",
            "URGENT": "bad"}.get(severity, "neutral")
    return pill(severity.replace("_", " ").lower(), tone)


def _breaker_pill(state: str) -> str:
    return pill(state.replace("_", "-").lower(),
                {"CLOSED": "ok", "HALF_OPEN": "warn", "OPEN": "bad"}.get(
                    state, "neutral"))


def _delivery_pill(state: str) -> str:
    """DELIVERED and SENT read differently on purpose: most providers confirm
    only that they accepted the request."""
    tone = {"DELIVERED": "ok", "ACKNOWLEDGED": "ok", "SENT": "info",
            "QUEUED": "neutral", "ESCALATED": "warn", "EXPIRED": "neutral",
            "FAILED": "bad"}.get(state, "neutral")
    return pill(state.lower(), tone)


def _standing_pill(standing: str) -> str:
    tone = {"AUTOMATICALLY HANDLED": "ok", "OWNER DECISION REQUIRED": "warn",
            "RECOMMENDATION": "info"}.get(standing, "neutral")
    return pill(standing.lower(), tone)


def _step_pill(state: str) -> str:
    tone = {"DONE": "ok", "ACTION REQUIRED": "bad", "OPTIONAL": "info",
            "NOT REQUIRED YET": "neutral", "BLOCKED": "bad",
            # Not green and not red. A fact this page cannot establish is worth
            # saying plainly; colouring it either way would be a claim.
            "CANNOT TELL FROM HERE": "warn"}.get(state, "neutral")
    return pill(state.lower(), tone)


def _plain_event(event: str) -> str:
    return _PLAIN.get(event, event.replace(".", " ").replace("_", " "))
