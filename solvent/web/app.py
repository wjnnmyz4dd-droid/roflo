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
import urllib.parse
from dataclasses import dataclass, field

from . import auth as auth_module
from . import intents as intents_module
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


class ControlCentre:
    """The whole surface. Given a projection and an intent writer, nothing else."""

    def __init__(self, readmodel, *, sessions=None, intent_writer=None,
                 password_hash: str = "", secure_cookies: bool = True) -> None:
        self.read = readmodel
        self.sessions = sessions or auth_module.Sessions()
        self.intents = intent_writer or intents_module.IntentWriter()
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
        body, title = handler(request)
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
            "/sources": self.sources,
            "/skills": self.skills,
            "/skill": self.skill_detail,
            "/capabilities": self.capabilities,
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
                    f'<div class="login">{note}<h1>Solvent</h1>'
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
            "Solvent runtime executes it under its own authorities, and a "
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
            out.append(("bad", "HALT is engaged. Solvent is taking no new work "
                               "and performing no external effects."))
        if not posture["simulation_only"]:
            out.append(("warn", "Real external effects are ENABLED. Solvent can "
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
        if posture["halted"]:
            attention.append(("HALT is engaged", "/security"))

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
                ("Simulation", "ON" if posture["simulation_only"] else "OFF",
                 "no real external effects" if posture["simulation_only"]
                 else "real effects enabled"),
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

        return (f"<h1>Command centre</h1>"
                f'<p class="sub">What needs you, then what is happening.</p>'
                f"<h2>Needs your attention</h2>{needs}"
                f"<h2>Business</h2>{cards}{simulated}"
                f"<h2>Jobs by state</h2>"
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
    def sources(self, request: Request):
        rows = self.read.work_sources()
        return ("<h1>Work sources</h1>"
                '<p class="sub">Where work may come from. Registering a source '
                "is not permission to act on it.</p>"
                + table(["name", "kind", "readiness", "compliance", "fixture",
                         "healthy"],
                        [[esc(r.get("name", "")), esc(r.get("kind", "")),
                          esc(r.get("readiness", "")),
                          _compliance_pill(r.get("compliance", "")),
                          esc("yes" if r.get("is_fixture") else "no"),
                          pill("yes" if r.get("healthy") else "no",
                               "ok" if r.get("healthy") else "bad")]
                         for r in rows], empty="No sources registered.")
                + detail("What the four stages mean",
                         "<p>A source is <code>CONFIGURED</code> when it exists, "
                         "<code>VERIFIED</code> when somebody has recorded what "
                         "its terms allow, <code>AUTHORIZED</code> when the owner "
                         "has approved it, and <code>ACTIVE</code> when it is "
                         "being polled. Adding one grants nothing: an unverified "
                         "source fails closed.</p>")
                + "<h2>Opportunities seen</h2>"
                + table(["source", "title", "status"],
                        [[esc(o.get("source", "")),
                          esc(o.get("title", ""))[:60],
                          esc(o.get("status", ""))]
                         for o in self.read.opportunities()],
                        empty="None yet."),
                "Work sources")

    # ------------------------------------------------------------ skills lab
    def skills(self, request: Request):
        projects = self.read.skill_projects()
        awaiting = [p for p in projects if p.get("stage") == "AWAITING_OWNER"]
        return ("<h1>Skills Lab</h1>"
                '<p class="sub">What Solvent wants to be able to do, what it is '
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

    # ---------------------------------------------------------- capabilities
    def capabilities(self, request: Request):
        rows = self.read.capabilities()
        certs = self.read.verifier_certifications()
        return ("<h1>Capabilities</h1>"
                '<p class="sub">What Solvent is allowed to sell, and on what '
                "evidence. Derived from the registry, never a second list.</p>"
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

    # ------------------------------------------------------ customer service
    def service(self, request: Request):
        relay = self.read.relay_queue()
        return ("<h1>Customer service</h1>"
                '<p class="sub">Solvent drafts; a person sends. Nothing on this '
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
        pending_intents = self.intents.pending()
        return ("<h1>Approvals</h1>"
                '<p class="sub">Decisions that are genuinely yours. Routine '
                "technical decisions are not here — they are governed already."
                "</p>"
                "<h2>Certified skills awaiting your decision</h2>"
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
                + table(["intent", "verb", "class", "why"],
                        [[esc(i.get("id", "")), esc(i.get("verb", "")),
                          pill(str(i.get("intent_class", "")),
                               "warn" if i.get("intent_class") == "CONSEQUENTIAL"
                               else "info"),
                          esc(i.get("why", ""))[:60]] for i in pending_intents],
                        empty="None queued.")
                + detail("Why some actions cannot be completed here",
                         "<p>A consequential action needs an approval signed with "
                         "the owner key. This control centre does not hold that "
                         "key — deliberately, so that taking over the website "
                         "does not take over the business. Those actions are "
                         "queued here and completed by the runtime with an owner "
                         "approval.</p>"),
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
        approved = setup["approved_models"] or {}
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

    # ------------------------------------------------------------ owner setup
    def setup(self, request: Request):
        setup = self.read.owner_setup()
        steps = [
            ("Contracting structure", bool(setup["structure"]),
             setup["structure"] or "not recorded"),
            ("Legal name", setup["has_legal_name"],
             "supplied" if setup["has_legal_name"] else "needed to name the "
             "contracting party"),
            ("Contact email", setup["has_email"],
             "supplied" if setup["has_email"] else "needed so a client can "
             "reach you"),
            ("Postal address", setup["has_address"],
             "supplied" if setup["has_address"] else "NOT REQUIRED YET — only to "
             "issue an invoice"),
            ("Tax reference", setup["tax_reference_provisioned"],
             "recorded as present (never the number)"
             if setup["tax_reference_provisioned"] else
             "NOT REQUIRED YET — only to connect a payment rail"),
            ("Payment rail", setup["rail_status"] not in ("", "SELECTED"),
             f"{setup['payment_rail'] or 'none'} — {setup['rail_status']}"),
            ("Model clearance", bool(setup["approved_models"]),
             "recorded" if setup["approved_models"] else "no artifact cleared"),
        ]
        return ("<h1>Owner setup</h1>"
                '<p class="sub">Only what is genuinely required, and what is not '
                "required yet.</p>"
                + table(["step", "state", "detail"],
                        [[esc(name),
                          pill("DONE", "ok") if done else pill("ACTION", "warn"),
                          esc(note)] for name, done, note in steps])
                + detail("Where secrets go",
                         "<p>The owner key and any payment secret live in "
                         "<code>/etc/solvent/solvent.env</code> on the server, "
                         "readable only by the service. They are never entered "
                         "here, never displayed here, and never sent to a "
                         "browser. This page reports whether a value is present, "
                         "never what it is.</p>"),
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


def _compliance_pill(value: str) -> str:
    return pill(value or "—", {"PERMITTED": "ok", "SUSPENDED": "bad",
                               "UNKNOWN": "warn"}.get(value, "neutral"))


def _change_pill(value: str) -> str:
    return pill(value or "—", {"UPGRADE": "warn", "NEW": "info",
                               "UPDATE": "neutral"}.get(value, "neutral"))


def _stage_pill(value: str) -> str:
    return pill(value or "—", {"PROMOTED": "ok", "AWAITING_OWNER": "warn",
                               "CERTIFIED": "info", "ABANDONED": "neutral"
                               }.get(value, "neutral"))


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


def _plain_event(event: str) -> str:
    return _PLAIN.get(event, event.replace(".", " ").replace("_", " "))
