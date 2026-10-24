# MiroFish, re-evaluated against the repaired system

**Verdict: NOT SUITABLE. No MiroFish integration is claimed, and its
runtime did not participate in any measurement.**

The earlier evaluation (`deskpilot-connector-implementation-certification.md`,
Stage 3) was made against a DeskPilot with two open seams. Both are now
closed — `solvent/dealmaking` joins a qualified opportunity to the
negotiation engine, and `solvent/contracting` joins an agreed price to
an admitted order. The question this document answers is whether that
changes the MiroFish answer.

It does not, and the reason is worth stating plainly: **none of the
blockers were about DeskPilot's seams.** Four are properties of
MiroFish, one is a property of DeskPilot's own egress guard, and none
of the five is affected by what the connectors do.

## Re-verified at the current head

Repository cloned and read at commit `7657031ac01184afe2cb220f5ee3545573b5e843`
(2 October 2026, "chore: update Star History (#832)"). Not run, and no
native result is claimed.

| Check | Finding at this head | Evidence |
|---|---|---|
| Purpose | A multi-agent engine that constructs a "parallel digital world" from seed material and predicts **collective social outcomes** — public-opinion dynamics, trending events, a novel's lost ending. Not a client negotiating a price for a CSV cleanup | `README.md:29–41` |
| Licence | **AGPL-3.0**, in both `LICENSE` and `backend/pyproject.toml` | `LICENSE:1`; `backend/pyproject.toml:6` |
| Python | `requires-python = ">=3.11,<3.13"` | `backend/pyproject.toml:5` |
| Mandatory remote services | An OpenAI-format LLM endpoint **and** Zep Cloud. Three separate services raise on a missing key — `ValueError("ZEP_API_KEY 未配置")` | `zep_tools.py:433`, `graph_builder.py:70`, `zep_graph_memory_updater.py:261` |
| Offline path | **None.** Zero matches for `offline`, `mock_llm`, `OFFLINE` or `dry_run` anywhere under `backend/app` | grep, whole subtree |
| Determinism | **No seeding anywhere** under `backend/app` — zero matches for `random.seed`, `np.random.seed`, `manual_seed` or `set_seed`. Sampling temperatures 0.3–0.5 | grep; `zep_tools.py:1126,1609,1668,1726`, `report_agent.py:1221,1346,1552,1874` |

One correction to the earlier report's framing. The Python pin is not a
blocker *here*: this container runs 3.11.17, which satisfies it. It is a
blocker for DeskPilot, whose installer serves 3.13 and whose CI runs
3.11 and 3.13 — a component that cannot be installed on half the
supported interpreters cannot be part of the product. Stated that way
rather than as "excludes 3.13 and 3.14", which was true but read as
though nothing could run it.

## The decisive blocker is DeskPilot's, and it is local

`solvent/egress.py` installs a PEP 578 audit hook denying
`socket.connect`, `socket.getaddrinfo`, `socket.gethostbyname`,
`socket.sendto` and `socket.bind` process-wide — and, in the same set,
`subprocess.Popen`, `os.system`, `os.exec` and `os.posix_spawn`,
"because a child process would escape an in-process hook entirely"
(`egress.py:40–48`). It cannot be removed:

> **It cannot be removed.** `sys.addaudithook` has no removal API, and
> adding a second hook cannot mask the first. — `egress.py:12`

> Install the guard. Idempotent, and irreversible for this interpreter.
> — `egress.py:105`

MiroFish's mandatory services are remote. A process that has loaded
DeskPilot's production components cannot reach them, and the guard
cannot be lifted for the duration. Nor is "run it in a subprocess" a
way round: spawning one is in the same guarded set, for exactly that
reason. This is not a configuration to change — it is the control that
makes "no live outreach" a property of the interpreter rather than a
promise.

## Why it would not have helped anyway

Even with the network, a 90-day business simulation needs two things
MiroFish does not offer:

* **Reproducible runs.** No seeding and non-zero temperature means two
  runs of the same scenario give different answers. A figure that
  cannot be reproduced cannot be audited, and the assignment's 30/60/90
  comparison is meaningless without it.
* **An independent grader.** The value of a simulation is that
  something other than the system under test decides whether it did
  well. A model-driven engine shares no boundary with DeskPilot's
  business logic, but it also offers no deterministic verdict to grade
  against.

## What is used instead

The three-way split in `simulation/__init__.py`: the customer model
decides how fictional clients behave, DeskPilot decides what the
business does about it, and `simulation.audit` grades — sharing no code
with either. `simulation/calendar.py` supplies deterministic time, and
a guard walks its parse tree to prove it imports no `time`, no `random`
and no `uuid`.

That is a smaller claim than "integrated a multi-agent prediction
engine", and it is one that can be checked.
