"""The AI backend, said honestly, and a model choice that fits the machine.

Two defects this module exists to prevent, both of which the previous
experience had.

**"Configured" is not "working".** ``roflo``'s ``BackendConfig.kind`` defaults
to the string ``"ollama"``. Nothing about that default implies Ollama is
installed, running, holding the model, or capable of answering. An installer
that read the configuration and reported a working AI backend would be
reporting the value of a dataclass field. So :func:`assess` climbs a ladder and
stops at the first rung that fails, and the only rung that means usable is
``READY``: the code supports the backend, the owner configured it, the
executable is on disk, the service answers, the model is present, and an
inference actually returned something.

**A cleared licence is not a licence for every size.** This is the trap in the
model catalogue and it is already documented in
``docs/solvent-model-rights-evidence.md``: Qwen2.5 is *not* uniformly
Apache-2.0. The 3B is released under ``qwen-research``, which says "FOR
NON-COMMERCIAL PURPOSES ONLY", and the 72B carries a conditional licence. The
3B is also exactly the model a naive installer would pick when it found a small
VPS -- small, same family, obviously the right size -- and picking it would put
a business that sells its output in breach. :data:`CATALOGUE` therefore carries
the licence per size, and :func:`recommend` refuses a non-commercial model
however well it fits, saying why.

Sizing leaves room for other software. The owner's MetaTrader terminal is on
the same machine and has money attached to it; an installer that fills the RAM
with a language model to look impressive has broken the thing that matters.
"""

from __future__ import annotations

from dataclasses import dataclass

from .host import Host
from .report import Level, Outcome, Row

GB = 1024 ** 3

#: RAM the operating system needs left alone.
OS_RESERVE = 2 * GB
#: Additional RAM left alone when MetaTrader is present. A trading terminal
#: with charts open is not a background task.
MT5_RESERVE = 2 * GB
#: What DeskPilot itself needs beyond the model.
APP_RESERVE = 1 * GB


@dataclass(frozen=True, slots=True)
class ModelOption:
    """One model the owner could run, with its rights status attached.

    ``commercial`` is not advice, it is the conclusion already reached and
    evidenced in the model-rights document. ``verified_digest`` is non-empty
    only where this repository holds a digest that was actually checked against
    the publisher's licence and the packaged blob; the installer will not
    invent one, so a model without it cannot be cleared unattended.
    """

    tag: str
    display: str
    licence: str
    commercial: bool
    #: Approximate resident size of the default quantisation.
    ram_bytes: int
    disk_bytes: int
    verified_digest: str = ""
    note: str = ""

    @property
    def can_autoclear(self) -> bool:
        """May the installer run ``setup model`` for this without asking?"""
        return self.commercial and bool(self.verified_digest)


#: The digest verified in ``docs/solvent-model-rights-evidence.md`` and used as
#: the default by ``solvent setup model``. The only one this repository proves.
QWEN14B_DIGEST = ("sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b"
                  "5339f370f9a54")

CATALOGUE: tuple[ModelOption, ...] = (
    ModelOption("qwen2.5:0.5b-instruct", "Qwen2.5 0.5B Instruct",
                "apache-2.0", True, int(0.6 * GB), int(0.4 * GB),
                note="very small; suitable only for the simplest extraction"),
    ModelOption("qwen2.5:3b-instruct", "Qwen2.5 3B Instruct",
                "qwen-research", False, int(2.5 * GB), int(1.9 * GB),
                note="NON-COMMERCIAL licence; cannot be used by a business "
                     "that sells its output"),
    ModelOption("qwen2.5:7b-instruct", "Qwen2.5 7B Instruct",
                "apache-2.0", True, int(5.5 * GB), int(4.7 * GB)),
    ModelOption("qwen2.5:14b-instruct", "Qwen2.5 14B Instruct",
                "apache-2.0", True, int(10 * GB), int(9 * GB),
                verified_digest=QWEN14B_DIGEST,
                note="the artifact DeskPilot's model-rights evidence covers"),
    ModelOption("qwen2.5:32b-instruct", "Qwen2.5 32B Instruct",
                "apache-2.0", True, int(21 * GB), int(20 * GB)),
    ModelOption("qwen2.5:72b-instruct", "Qwen2.5 72B Instruct",
                "qwen", True, int(48 * GB), int(47 * GB),
                note="conditional licence: above 100 million monthly active "
                     "users a separate licence must be requested"),
)


def reserve_bytes(mt5_present: bool) -> int:
    """RAM the recommender refuses to spend on a model."""
    return OS_RESERVE + APP_RESERVE + (MT5_RESERVE if mt5_present else 0)


@dataclass(frozen=True, slots=True)
class Recommendation:
    """What to run, or why nothing local will do.

    ``cloud_instead`` being set is a legitimate successful outcome, not a
    failure: a 4 GB VPS running a trading terminal should not host a 14B model,
    and saying so is more useful than degrading the machine to look capable.
    """

    model: ModelOption | None
    cloud_instead: bool
    reason: str
    rejected: tuple[tuple[str, str], ...] = ()

    @property
    def tag(self) -> str:
        return self.model.tag if self.model else ""


def recommend(total_ram: int, free_disk: int, *, mt5_present: bool,
              cpu_count: int = 1) -> Recommendation:
    """The largest commercially usable model this machine can host.

    Largest-that-fits rather than smallest-that-works, because quality is the
    product here; but "fits" is measured after the reserve, so the ceiling is
    what the machine can spare and not what it has.
    """
    budget = total_ram - reserve_bytes(mt5_present)
    rejected: list[tuple[str, str]] = []
    affordable: list[ModelOption] = []
    for option in CATALOGUE:
        if not option.commercial:
            rejected.append((option.tag,
                             f"licence {option.licence}: {option.note}"))
            continue
        if option.ram_bytes > budget:
            rejected.append((option.tag,
                             f"needs about {option.ram_bytes // GB} GB of RAM; "
                             f"only {max(budget, 0) // GB} GB can be spared"))
            continue
        if option.disk_bytes > free_disk:
            rejected.append((option.tag,
                             f"needs about {option.disk_bytes // GB} GB of disk; "
                             f"{free_disk // GB} GB free"))
            continue
        affordable.append(option)
    if not affordable:
        return Recommendation(
            None, True,
            ("No commercially licensed local model fits this machine once the "
             "operating system"
             + (", MetaTrader" if mt5_present else "")
             + " and DeskPilot are left room. Use an approved cloud backend "
               "instead of degrading this server."),
            tuple(rejected))
    best = max(affordable, key=lambda o: o.ram_bytes)
    reason = (f"{best.display} fits in the "
              f"{max(budget, 0) // GB} GB that can be spared")
    if cpu_count <= 2 and best.ram_bytes > 6 * GB:
        reason += (f"; with {cpu_count} CPU(s) it will answer slowly, which is "
                   f"workable for back-office jobs but not for anything "
                   f"interactive")
    return Recommendation(best, False, reason, tuple(rejected))


# ---------------------------------------------------------------------------
# Backend state
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BackendState:
    """How far up the ladder this backend actually is, and the evidence."""

    kind: str
    level: Level
    detail: str
    #: One line per rung, in order, for the Advanced Details view.
    evidence: tuple[tuple[str, bool, str], ...] = ()

    @property
    def usable(self) -> bool:
        return self.level is Level.READY


#: Backends ``roflo`` can drive. Supported means the code exists, nothing more.
SUPPORTED_KINDS = ("ollama", "llamacpp", "vllm", "transformers", "echo")

#: Backends reached over the network with a key rather than installed locally.
CLOUD_KINDS = ("vllm",)

OLLAMA_EXES = ("ollama.exe", "ollama")


def assess(host: Host, *, kind: str, model_tag: str,
           base_url: str = "http://127.0.0.1:11434",
           probe_inference: bool = True) -> BackendState:
    """Climb the five rungs. Stop at the first failure and say which it was."""
    evidence: list[tuple[str, bool, str]] = []

    def done(level: Level, detail: str) -> BackendState:
        return BackendState(kind, level, detail, tuple(evidence))

    supported = kind in SUPPORTED_KINDS
    evidence.append(("supported by roflo", supported,
                     f"{kind!r} is "
                     f"{'a known backend' if supported else 'not a backend roflo has'}"))
    if not supported:
        return done(Level.ABSENT,
                    f"roflo has no backend called {kind!r}")

    configured = bool(model_tag)
    evidence.append(("configured by the owner", configured,
                     f"model tag {model_tag!r}" if configured
                     else "no model tag chosen"))
    if not configured:
        return done(Level.SUPPORTED,
                    "the backend exists in roflo but no model has been chosen. "
                    "This is not a working AI backend.")

    if kind == "echo":
        # The echo backend is real code and answers immediately, but it is a
        # test double. Calling it READY would make a machine with no model at
        # all look finished, which is the exact lie this module prevents.
        return done(Level.CONFIGURED,
                    "the echo backend is a test double, not a language model; "
                    "DeskPilot will not treat it as an AI backend")

    if kind in CLOUD_KINDS:
        reachable = _http_ok(host, base_url)
        evidence.append(("reachable", reachable, base_url))
        if not reachable:
            return done(Level.CONFIGURED,
                        f"configured for {base_url} but nothing answered there")
        return done(Level.READY, f"{kind} answering at {base_url}")

    exe = ""
    for candidate in OLLAMA_EXES:
        exe = host.which(candidate)
        if exe:
            break
    evidence.append(("installed on this computer", bool(exe),
                     exe or "no ollama executable on PATH"))
    if not exe:
        return done(Level.CONFIGURED,
                    "Ollama is configured but is not installed on this "
                    "computer. Nothing will answer until it is.")

    listed = host.run([exe, "list"], timeout=60.0)
    reachable = listed.ok
    evidence.append(("service reachable", reachable,
                     "ollama list answered" if reachable
                     else (listed.output.strip()[:200] or "ollama list failed")))
    if not reachable:
        return done(Level.INSTALLED,
                    "Ollama is installed but its service did not answer. "
                    "DeskPilot will not report an AI backend as ready.")

    has_model = _tag_present(listed.stdout, model_tag)
    evidence.append(("model present", has_model,
                     f"{model_tag} "
                     f"{'is downloaded' if has_model else 'is not downloaded'}"))
    if not has_model:
        return done(Level.REACHABLE,
                    f"Ollama is running but {model_tag} has not been "
                    f"downloaded yet.")

    if not probe_inference:
        return done(Level.REACHABLE,
                    f"{model_tag} is present; inference was not tested")

    answered = host.run([exe, "run", model_tag, "--", "say ok"], timeout=180.0)
    works = answered.ok and bool(answered.stdout.strip())
    evidence.append(("inference works", works,
                     "a prompt returned output" if works
                     else (answered.output.strip()[:200] or "no output")))
    if not works:
        return done(Level.REACHABLE,
                    f"{model_tag} is downloaded but did not answer a prompt.")
    return done(Level.READY, f"{model_tag} answered a test prompt through Ollama")


def _tag_present(listing: str, tag: str) -> bool:
    """Is ``tag`` in ``ollama list`` output? Tolerant of the ``:latest`` suffix."""
    wanted = tag.strip().lower()
    base = wanted.split(":", 1)[0]
    for line in listing.splitlines()[1:]:
        name = line.split()[0].lower() if line.split() else ""
        if not name:
            continue
        if name == wanted or name == f"{wanted}:latest":
            return True
        if wanted.endswith(":latest") and name == wanted[:-7]:
            return True
        if name == base and ":" not in wanted:
            return True
    return False


def _http_ok(host: Host, url: str) -> bool:
    """Is something answering at ``url``? Uses the host's own port check.

    Deliberately not a real HTTP request: the installer has no business making
    outbound calls during a system check, and "something is listening" is the
    question the next rung actually depends on.
    """
    try:
        _, _, rest = url.partition("://")
        hostport = rest.split("/", 1)[0]
        port = int(hostport.rsplit(":", 1)[1]) if ":" in hostport else 80
    except (ValueError, IndexError):
        return False
    return host.port_in_use(port)


def row(state: BackendState, recommendation: Recommendation | None = None) -> Row:
    """The owner-facing line. ``READY`` is the only PASS."""
    if state.level is Level.READY:
        return Row("AI backend", Outcome.PASS, state.detail, level=state.level,
                   facts={"kind": state.kind})
    outcome = Outcome.ACTION
    detail = state.detail
    if recommendation is not None and recommendation.cloud_instead:
        detail += " " + recommendation.reason
    return Row("AI backend", outcome, detail, level=state.level,
               facts={"kind": state.kind,
                      "evidence": [{"rung": r, "ok": o, "note": n}
                                   for r, o, n in state.evidence]})
