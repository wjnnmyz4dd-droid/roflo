"""Could two PDF readers check each other?

The reason this matters is the whole shape of this sweep. Four
worker-checker pairs in this system turned out to share a parser, and a
shared parser cannot catch its own misreading. A PDF capability whose
worker and checker both call pypdf would rebuild that fault on the
commonest job type in the market.

Two independent implementations only help if they agree on correct
input and diverge on corrupt input. Both halves are measured here;
either one failing sinks the design.
"""
import io
import json
import pathlib
import re
import sys

import pdfplumber
from pypdf import PdfReader

FIX, HOLD = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])


def plumber(data):
    with pdfplumber.open(io.BytesIO(data)) as doc:
        return "\n".join((p.extract_text() or "") for p in doc.pages)


def pypdf_text(data):
    return "\n".join((p.extract_text() or "")
                     for p in PdfReader(io.BytesIO(data)).pages)


def normalise(text):
    """Compare what a client would read, not how a library lays it out."""
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    return set(re.findall(r"[A-Za-z0-9][A-Za-z0-9.,/-]*", text))


report = {"agree_exactly": [], "agree_on_tokens": [], "disagree": []}
for path in sorted(FIX.glob("*.pdf")) + sorted(HOLD.glob("*.pdf")):
    data = path.read_bytes()
    a, b = normalise(plumber(data)), normalise(pypdf_text(data))
    if a == b:
        report["agree_exactly"].append(path.name)
    elif tokens(a) == tokens(b):
        report["agree_on_tokens"].append(path.name)
    else:
        only_a = sorted(tokens(a) - tokens(b))[:6]
        only_b = sorted(tokens(b) - tokens(a))[:6]
        report["disagree"].append(
            {"document": path.name,
             "tokens_only_pdfplumber": only_a,
             "tokens_only_pypdf": only_b,
             "token_overlap": round(
                 len(tokens(a) & tokens(b)) /
                 max(1, len(tokens(a) | tokens(b))), 3)})

# And the other half: on a damaged file, do they diverge rather than
# both returning confident nonsense?
damaged = (FIX / "statement-multipage.pdf").read_bytes()[:900]
outcomes = {}
for name, fn in (("pdfplumber", plumber), ("pypdf", pypdf_text)):
    try:
        outcomes[name] = f"text of {len(normalise(fn(damaged)))} chars"
    except Exception as exc:                              # noqa: BLE001
        outcomes[name] = f"raised {type(exc).__name__}"
report["on_a_truncated_file"] = outcomes

print(json.dumps(report, indent=2))
