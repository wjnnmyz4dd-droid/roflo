# Advisory reviews: what actually ran

The owner asked for K-LEAN and iFixAi to be run, with native execution
preferred, and said plainly: *do not falsely claim either repository
executed*. Here is what happened with each.

## iFixAi — EXECUTED NATIVELY, but not against DeskPilot

- Repository: `ifixai-ai/iFixAi`, cloned at `1559983`.
- Installed into a virtualenv from source. `ifixai, version 4.2.0`.
- Ran: `ifixai run --provider mock --suite all --eval-mode deterministic
  --no-telemetry`, with `DO_NOT_TRACK=1` and `IFIXAI_TELEMETRY=0`.
  Telemetry was disabled deliberately: it sends a persistent install
  id by default, and sending anything outward about this session is an
  external effect nobody asked for.
- Output preserved in `ifixai-mock-deterministic-summary.md` and
  `ifixai-run-console.txt`.

**The result characterises iFixAi's own mock provider, not DeskPilot.**
The scorecard reads `Provider: mock`, `Fixture: NimbusForge Deploy
Copilot` — a demo fixture bundled with the tool. The 60% / grade D is
about that mock. Reporting it as a DeskPilot finding would be a
fabrication.

Two reasons it could not be pointed at DeskPilot:

1. iFixAi audits a *live AI assistant endpoint* for behavioural
   failures — fabrication, deception, sabotage, oversight evasion. It
   is not a static analyser of a Python codebase.
2. DeskPilot has no model endpoint to point it at. The five certified
   skills are deterministic and call no model; that is a designed
   property, not an omission. The one place a model is used is
   advisory routing, which is not an agent endpoint.

Most inspections were skipped in any case: 40 of 60 errored with
"judge pipeline required", because scoring them needs a second LLM
acting as judge (it auto-selected AWS Bedrock) and no provider
credentials exist here.

**Findings about DeskPilot from iFixAi: none.** Not "none found" —
none sought, because the tool and the subject do not meet.

## K-LEAN — NOT EXECUTED

- Repository: `calinfaja/K-LEAN`, cloned at `79f3557`.
- The repository is **archived**. Its own README says so: "the
  committee has adjourned".
- `src/klean/reviews.py` hard-codes `LITELLM_URL =
  "http://localhost:4000"` and queries `/models` and
  `/chat/completions` against it. K-LEAN's function is to federate a
  review across OpenAI, Gemini, DeepSeek and Moonshot through a
  LiteLLM proxy and return their consensus.
- That needs API keys for those providers. There are none in this
  environment, and inventing them or using the owner's is not
  something this sweep will do.

**K-LEAN did not run. Nothing in this report is a K-LEAN finding.**

Any review content attributed to K-LEAN in this sweep would be
source-derived — i.e. me applying criteria I read in its source — and
is labelled as such wherever it appears. It is not a second opinion
from another system; it is the same opinion wearing a borrowed name,
which is worth saying out loud.
