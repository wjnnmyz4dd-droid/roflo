"""HTML rendering. Everything that reaches a page goes through :func:`esc`.

There is no template engine, and that is the point: a template language with an
autoescape setting has an off switch, and the off switch is what ships. Here the
only way to put a value on a page is to escape it, because ``esc`` is the only
function that produces text from data.

Client files, client messages and supplied documentation are all attacker-
controlled. So is a job title. Each is displayed, none is trusted.
"""

from __future__ import annotations

from html import escape

#: Progressive disclosure (§55): three levels, so the owner is not handed
#: fingerprints to read but is never prevented from reaching them.
PLAIN, OPERATIONAL, EVIDENCE = "plain", "operational", "evidence"


def esc(value) -> str:
    """The only route from data to markup. Quotes included, for attributes."""
    return escape("" if value is None else str(value), quote=True)


def money(cents) -> str:
    try:
        return f"${int(cents) / 100:,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def pill(text: str, tone: str = "neutral") -> str:
    """A status chip. Carries a word as well as a colour (§62).

    Colour alone fails for anybody who cannot distinguish the colours, and
    "green" is not a status anybody can act on.
    """
    return f'<span class="pill pill-{esc(tone)}">{esc(text)}</span>'


def table(headers: list[str], rows: list[list[str]], *,
          empty: str = "NO DATA YET") -> str:
    """A table, or an honest empty state. Cells must already be escaped.

    ``empty`` exists because a dashboard with invented rows is worse than one
    that says it has nothing: the owner would act on it.
    """
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                   for row in rows)
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def detail(summary: str, body: str) -> str:
    """One level down. Available, not in the way."""
    return (f"<details><summary>{esc(summary)}</summary>"
            f'<div class="detail">{body}</div></details>')


def link(href: str, text: str) -> str:
    return f'<a href="{esc(href)}">{esc(text)}</a>'


NAV = (
    ("/", "Command centre"),
    ("/jobs", "Jobs"),
    ("/clients", "Clients"),
    ("/sources", "Work sources"),
    ("/skills", "Skills Lab"),
    ("/capabilities", "Capabilities"),
    ("/service", "Customer service"),
    ("/approvals", "Approvals"),
    ("/money", "Money"),
    ("/model", "AI model"),
    ("/security", "Security"),
    ("/audit", "Audit"),
    ("/health", "System health"),
    ("/setup", "Owner setup"),
)

STYLE = """
:root{--bg:#fbfbfd;--fg:#16181d;--muted:#5b6170;--line:#e2e5ec;--card:#fff;
--ok:#0f6c3d;--okbg:#e6f4ec;--warn:#8a5300;--warnbg:#fdf1dc;--bad:#a1121f;
--badbg:#fdeaec;--info:#1f4e8c;--infobg:#e8f0fb;--accent:#2b4acb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#101216;--fg:#e8eaf0;--muted:#9aa2b4;--line:#282d38;--card:#171a20;
--ok:#6fdca0;--okbg:#10291d;--warn:#f0c070;--warnbg:#2c2210;--bad:#ff9aa4;
--badbg:#2e1317;--info:#8fb6f0;--infobg:#111d2e;--accent:#8fa4ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);
padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header .brand{font-weight:700;letter-spacing:.01em}
nav{display:flex;gap:2px;flex-wrap:wrap;padding:8px 16px;background:var(--card);
border-bottom:1px solid var(--line)}
nav a{padding:6px 10px;border-radius:7px;text-decoration:none;color:var(--muted);
font-size:13.5px;white-space:nowrap}
nav a:hover{background:var(--bg);color:var(--fg)}
nav a[aria-current=page]{background:var(--accent);color:#fff}
main{padding:16px;max-width:1180px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 16px}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;
padding:13px 15px}
.card .k{color:var(--muted);font-size:12.5px;text-transform:uppercase;
letter-spacing:.04em}
.card .v{font-size:22px;font-weight:650;margin-top:3px}
.card .n{color:var(--muted);font-size:12.5px;margin-top:3px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13.5px;
background:var(--card);border:1px solid var(--line);border-radius:10px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px;text-transform:uppercase;
letter-spacing:.03em}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;
font-weight:600;white-space:nowrap}
.pill-ok{background:var(--okbg);color:var(--ok)}
.pill-warn{background:var(--warnbg);color:var(--warn)}
.pill-bad{background:var(--badbg);color:var(--bad)}
.pill-info{background:var(--infobg);color:var(--info)}
.pill-neutral{background:var(--bg);color:var(--muted);border:1px solid var(--line)}
.empty{color:var(--muted);font-size:13.5px;padding:14px;background:var(--card);
border:1px dashed var(--line);border-radius:10px;margin:0}
details{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:8px 12px;margin:8px 0}
summary{cursor:pointer;color:var(--muted);font-size:13px}
.detail{padding-top:8px;font-size:13px;overflow-wrap:anywhere}
a{color:var(--accent)}
form.inline{display:inline}
button{font:inherit;padding:7px 13px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer}
button.danger{border-color:var(--bad);color:var(--bad)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.banner{padding:11px 14px;border-radius:10px;margin-bottom:14px;font-size:14px}
.banner-bad{background:var(--badbg);color:var(--bad)}
.banner-warn{background:var(--warnbg);color:var(--warn)}
.banner-ok{background:var(--okbg);color:var(--ok)}
.login{max-width:330px;margin:12vh auto;background:var(--card);
border:1px solid var(--line);border-radius:12px;padding:22px}
label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px}
input[type=password],input[type=text],textarea,select{width:100%;padding:8px 10px;
border:1px solid var(--line);border-radius:8px;background:var(--bg);
color:var(--fg);font:inherit}
code{font-size:12.5px;background:var(--bg);padding:1px 5px;border-radius:5px;
overflow-wrap:anywhere}
.muted{color:var(--muted)}
@media (max-width:640px){main{padding:12px}.card .v{font-size:19px}
th,td{padding:7px 8px}h1{font-size:18px}}
"""


def page(title: str, body: str, *, path: str = "/", authenticated: bool = True,
         banners: list[tuple[str, str]] | None = None,
         csrf: str = "") -> bytes:
    """One page. ``banners`` are ``(tone, text)`` and are escaped like anything."""
    nav = ""
    if authenticated:
        nav = "<nav>" + "".join(
            f'<a href="{esc(href)}"'
            + (' aria-current="page"' if href == path else "")
            + f">{esc(label)}</a>" for href, label in NAV) + "</nav>"
    logout = ""
    if authenticated and csrf:
        logout = (f'<form method="post" action="/logout" class="inline">'
                  f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
                  f"<button>Sign out</button></form>")
    alerts = "".join(
        f'<div class="banner banner-{esc(tone)}">{esc(text)}</div>'
        for tone, text in (banners or []))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{esc(title)} · Solvent</title>"
        f"<style>{STYLE}</style></head><body>"
        f'<header><span class="brand">Solvent</span>'
        f'<span class="muted">owner control centre</span>'
        f'<span style="margin-left:auto">{logout}</span></header>'
        f"{nav}<main>{alerts}{body}</main></body></html>"
    ).encode("utf-8")
