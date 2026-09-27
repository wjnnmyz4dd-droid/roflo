"""The HTTP layer. Binds a port, parses a request, and knows nothing else.

Kept separate from :mod:`solvent.web.app` so the whole surface can be tested
without a socket: every attack in the test suite drives :class:`ControlCentre`
directly, which is also how a real attacker reaches it — the browser is not the
threat model, the endpoint is.

Binds to loopback by default. A control centre reachable from the internet
because nobody passed an argument is not a decision anybody made; TLS and
exposure belong to a reverse proxy, which the deployment contract configures.
"""

from __future__ import annotations

import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import SECURITY_HEADERS, ControlCentre, Request
from .readmodel import ReadModel

#: Loopback, always, unless the operator says otherwise on the command line.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
#: Longest form body accepted. A control centre has no large uploads, so a big
#: body is either a mistake or an attempt to exhaust memory.
MAX_BODY = 64 * 1024


def _handler_for(centre: ControlCentre):
    class Handler(BaseHTTPRequestHandler):
        server_version = "solvent"
        sys_version = ""

        def _respond(self, response) -> None:
            self.send_response(response.status)
            for name, value in (response.headers or SECURITY_HEADERS):
                self.send_header(name, value)
            if response.set_session:
                flags = "HttpOnly; SameSite=Strict; Path=/"
                if centre.secure_cookies:
                    flags += "; Secure"
                self.send_header("Set-Cookie",
                                 f"solvent_session={response.set_session}; {flags}")
            if response.clear_session:
                self.send_header(
                    "Set-Cookie",
                    "solvent_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        def _request(self, method: str):
            parsed = urllib.parse.urlparse(self.path)
            query = {k: v[0] for k, v in
                     urllib.parse.parse_qs(parsed.query).items()}
            form = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return None
                raw = self.rfile.read(length).decode("utf-8", "replace")
                form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
            cookies = {}
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name:
                    cookies[name] = value
            return Request(method=method, path=parsed.path, query=query,
                           form=form, cookies=cookies,
                           source=self.client_address[0])

        def do_GET(self):
            request = self._request("GET")
            self._respond(centre.handle(request))

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            request = self._request("POST")
            if request is None:
                self._respond(type(centre.handle(Request("GET", "/login")))(
                    413, b"too large", SECURITY_HEADERS))
                return
            self._respond(centre.handle(request))

        def log_message(self, fmt, *args):
            # Request lines can contain anything a client sent. Log the method
            # and the path only, and never a form body: a body could carry the
            # owner's password.
            print(f"[web] {self.command} {urllib.parse.urlparse(self.path).path}")

    return Handler


def build(db_path: str, *, password_hash: str = "", spool: str = "",
          secure_cookies: bool = True) -> ControlCentre:
    from . import auth as auth_module
    from . import intents as intents_module

    return ControlCentre(
        ReadModel(db_path),
        password_hash=password_hash or auth_module.configured_hash(),
        intent_writer=intents_module.IntentWriter(
            spool or intents_module.INTENT_SPOOL),
        secure_cookies=secure_cookies)


def serve(db_path: str, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          password_hash: str = "", spool: str = "",
          secure_cookies: bool = True) -> None:
    centre = build(db_path, password_hash=password_hash, spool=spool,
                   secure_cookies=secure_cookies)
    if not centre.password_hash:
        print("[web] refusing to start: no SOLVENT_WEB_PASSWORD_HASH is set.")
        print("[web] generate one with: solvent web-password")
        raise SystemExit(2)
    server = ThreadingHTTPServer((host, port), _handler_for(centre))
    print(f"[web] control centre on http://{host}:{port} "
          f"(database {db_path}, read-only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        centre.read.close()
