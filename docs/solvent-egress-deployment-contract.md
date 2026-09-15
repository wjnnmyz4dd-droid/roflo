# Solvent — Egress Deployment Contract

The Action Gate is a decision layer. This document is the containment layer.
**Neither is sufficient alone**, and Solvent does not claim the Gate as an
enforced control on a host that does not satisfy this contract.

## The problem

A dependency can open its own socket. An SDK, a document parser resolving a remote
entity, a webhook client, a transitive package three levels down — none of them
consult application logic before calling `connect()`. A Gate such a library can
bypass is worse than no Gate, because it produces confidence that is not earned.

## Layer 1 — in-process (implemented, `solvent/egress.py`)

A CPython audit hook denies `socket.connect`, `socket.getaddrinfo`,
`socket.bind`, `subprocess.Popen` and `os.system` for the whole interpreter.

- **Non-removable.** `sys.addaudithook` has no removal API, and adding a second
  hook does not displace the first.
- **Below application code.** `socket.connect` raises its audit event from
  CPython's C implementation, so `httpx`, `requests`, `urllib` and everything else
  built on the socket module are caught by a single rule.
- **Spawning is blocked** because a child process would escape an in-process hook
  entirely.
- The only way through is a window the Action Gate opens around one approved call
  and closes in a `finally`.

**What this layer does not stop:** a `ctypes` call straight to libc, and
same-thread code piggybacking on an open window. Both are stated in the module
docstring rather than left for someone to discover.

## Layer 2 — operating system (this contract)

```
┌─ WORKER NAMESPACE ─────────────────┐   ┌─ GATE NAMESPACE ──────────────────┐
│ Orchestrator · Governor · Capability│   │ Action Gate process               │
│ execution · document parsing        │   │                                   │
│                                     │   │ • holds ALL outbound credentials  │
│ • NO default route                  │   │ • egress allowlist from Policy    │
│ • NO DNS resolver                   │   │ • rate limits and size caps       │
│ • NO credentials in env or on disk  │   │ • every call written to Audit     │
│                                     │   │                                   │
│   only channel out:                 │   │                                   │
│   AF_UNIX socket ───────────────────┼──►│  (the only inbound path)          │
└─────────────────────────────────────┘   └───────────┬───────────────────────┘
                                                      │ allowlisted egress only
  ┌─ ROFLO NAMESPACE(S) ──────────┐                   ▼
  │ one process per (model,       │◄──────  providers · client rail ·
  │ privacy class); reachable ONLY │         payment rail · public web
  │ from the Gate namespace        │
  └────────────────────────────────┘
```

**M1 — Unroutable worker namespace.** Workers run with no default route and no
resolver. A malicious dependency calling `connect()` fails at the **kernel** with
`ENETUNREACH`; it is not blocked by a policy it might evade, because there is no
path.

**M2 — Credential isolation.** Outbound credentials exist only inside the Gate
namespace. Even if M1 were defeated, a worker holds nothing to authenticate with.

**M3 — Allowlisted egress.** The Gate egresses only to destinations on a
Policy-held allowlist (host, port, protocol, and a maximum privacy class per
destination). The allowlist is **data in Policy**, so adding a destination is an
owner act that is versioned and audited.

## roflo containment

roflo accepts an OpenAI-style `model` field, echoes it back in every response, and
**never routes on it**: the backend is fixed at construction. `/v1/models` even
enumerates the provider's real model list, so the endpoint advertises a menu it
cannot honour.

Therefore:

> **Run one roflo process per (model, privacy class), each in its own namespace,
> reachable only from the Gate, selected by endpoint URL. Never route by the
> `model` field, and never trust the `model` value in a response for cost
> attribution.**

This needs no change to roflo and is strictly safer than fixing the field: it
makes the privacy boundary a process and network boundary rather than a string in
a JSON body that a prompt-injected component could set.

## Verification

```bash
python3 tools/verify_egress.py
```

Measures rather than asserts. Expected output on a conforming host:

```
1. unconfined process        : EGRESS_SUCCEEDED
2. isolated worker namespace : EGRESS_BLOCKED:OSError:[Errno 101] Network is unreachable
3. in-process guard          : {'socket.connect': 'DENIED_BY_GUARD', 'dns': 'DENIED_BY_GUARD', ...}
RESULT: OS-LEVEL ISOLATION ENFORCED.
```

If line 2 does not report `EGRESS_BLOCKED`, **the Action Gate must not be
described as an enforced control on that host**, and `egress.simulation_only` must
stay `true`.

## Bypass tests

`tests_solvent/test_egress.py` runs the in-process attempts (G-1…G-6) as ordinary
tests: raw socket, DNS, a library making its own request, `subprocess`,
`os.system`, and `bind`. All must fail to reach the network. The OS-level checks
live in the standalone tool because the suite correctly forbids spawning the
processes they need.

## Status

| Layer | Status |
| --- | --- |
| In-process guard | **IMPLEMENTED AND TESTED** (6 bypass attempts, all denied) |
| OS isolation | **AVAILABLE AND VERIFIED on this host**; deployment is OD-4 |
| Credential isolation | **DESIGNED**; no real credentials exist yet |
| Allowlist + privacy caps | **IMPLEMENTED AND TESTED** |
| Default posture | **FAIL-CLOSED** — empty allowlist, `simulation_only = true` |
