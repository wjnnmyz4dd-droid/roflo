# Solvent — Production Model Commercial-Rights Evidence (OD-3)

**Verified 15 September 2026. Re-verified 17 September 2026.** One question:
*may the exact model Solvent intends to use produce paid client work?*

**Re-verification result (17 Sep):** every digest below re-fetched **unchanged**,
the packaged licence still hashes byte-identical to the publisher's, the upstream
repo commit is still `cf98f3b3…`, and the family licence spread is unchanged. The
answer holds. The re-run did, however, expose an enforcement defect — see §9.

This record exists because the previous answer — "the Ollama tag packages
Apache-2.0 weights" — was a claim, not evidence. What follows is the chain.

---

## 1. The artifact under verification

| Field | Value | Where it comes from |
| --- | --- | --- |
| Backend | `ollama` | `roflo.toml` `[backend] kind` |
| Configured tag | **`qwen2.5:14b-instruct`** | `roflo.toml` `[backend] model` |
| Model family | `qwen2` | Ollama config blob |
| Reported size | `14.8B` | Ollama config blob |
| Format | `gguf` | Ollama config blob |
| **Quantisation** | **`Q4_K_M`** | Ollama config blob `file_type` |
| Model layer digest | `sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54` | Ollama registry manifest |
| Model layer size | 8,988,110,688 bytes (8.99 GB) | Ollama registry manifest |
| Config blob digest | `sha256:db59b814cab753a51167db007fa6b6e0095f678ff24c9f7284753b75b34c6df3` | Ollama registry manifest |
| Licence layer digest | `sha256:832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e` | Ollama registry manifest |
| Upstream checkpoint | `Qwen/Qwen2.5-14B-Instruct` | Hugging Face |
| Upstream repo commit | `cf98f3b3bbb457ad9e2bb7baf9a0125b6b88caa8` | HF model API |
| Publisher | **Alibaba Cloud** (Qwen team) | LICENSE copyright line |

**The deployed artifact is a derivative.** `qwen2.5:14b-instruct` is a **Q4_K_M
quantisation in GGUF**, not the upstream `safetensors`. 8.99 GB for 14.8B
parameters is ≈4.9 bits per parameter, consistent with Q4_K_M and inconsistent
with the ~29.5 GB fp16 original. Apache-2.0 §4 permits derivative works, so this
does not change the rights — but it does mean the thing Solvent runs is not the
thing on the Hugging Face model page, and the record must say so.

## 2. Provenance chain, each link resolved

```
roflo.toml  [backend] model = "qwen2.5:14b-instruct"
   ↓  Ollama registry manifest  (HTTP 200)
   ↓  https://registry.ollama.ai/v2/library/qwen2.5/manifests/14b-instruct
layers: model  sha256:2049f5674b1e…   (8.99 GB, Q4_K_M GGUF)
        license sha256:832dd9e00a68…  (11,343 bytes)
   ↓  blob fetched and hashed
license blob content hash == 832dd9e00a68…      ← content address verified
   ↓  compared byte-for-byte against upstream
https://huggingface.co/Qwen/Qwen2.5-14B-Instruct/raw/main/LICENSE
  sha256 == 832dd9e00a68…                        ← IDENTICAL
   ↓
Apache License 2.0 — "Copyright 2024 Alibaba Cloud"
```

**This is the link that was missing before.** The licence text Ollama ships
inside the tag is not merely *described* as Apache-2.0 — it is the **same 11,343
bytes** as the upstream publisher's `LICENSE` file, proven by a matching
SHA-256. The two were compared with `cmp`, not by reading two web pages.

## 3. Sibling tags — what `14b-instruct` actually is

| Tag | Model layer digest | Size |
| --- | --- | --- |
| `14b` | `2049f5674b1e…` | 8,988,110,688 |
| **`14b-instruct`** | **`2049f5674b1e…`** | **8,988,110,688** |
| `14b-instruct-q4_K_M` | `2049f5674b1e…` | 8,988,110,688 |
| `14b-instruct-fp16` | `c599ec1e5773…` | 29,547,716,448 |
| `latest` | `2bada8a74506…` | 4,683,073,952 |

`14b`, `14b-instruct` and `14b-instruct-q4_K_M` are **three names for one
artifact**. `latest` is a **different, smaller model** — so a configuration that
degraded to bare `qwen2.5` would silently run something else. All five carry the
same licence layer.

## 4. Licence layers, kept separate

| Layer | Licence | Status |
| --- | --- | --- |
| **Model weights** | **Apache-2.0**, Copyright 2024 Alibaba Cloud | Verified byte-identical, upstream and packaged |
| **Model output** | Apache-2.0 imposes **no term on output** — it licenses the Work, and no clause claims or restricts what the Work generates | Verified by reading the licence text: no output, field-of-use, revenue or user-count clause exists in it |
| **Ollama packaging** | The quantisation is a derivative under Apache-2.0 §4; Ollama carries the licence in the manifest, which is what §4(a)/(d) asks of a redistributor | Verified — the licence layer is present in every tag checked |
| **roflo runtime** | **Unlicensed** — no `LICENSE` file, no `license` field in `pyproject.toml` | This is OD-6, and it is a **separate question**. A permissive runtime would not license the weights, and an unlicensed runtime does not taint them |

**The runtime licence and the weights licence are independent.** Neither inherits
from the other. Conflating them in either direction is the licence-laundering
error this section exists to prevent.

## 5. Apache-2.0 — obligations that actually apply here

| Obligation | Applies to Solvent? |
| --- | --- |
| Commercial use permitted (§2 grant: "reproduce, prepare Derivative Works of, publicly display, … sublicense, and distribute") | **Yes, permitted.** No commercial carve-out |
| Carry the licence with **redistribution** (§4(a)) | **Not triggered.** Solvent consumes output; it does not redistribute weights |
| State significant changes (§4(b)) | Not triggered — Solvent does not modify or redistribute the weights |
| Retain attribution/NOTICE (§4(c)/(d)) | Not triggered by consumption. **If the owner ever redistributes the weights or a fine-tune, it is** |
| Trademark (§6) | No licence to the "Qwen" or "Alibaba" marks. Solvent must not brand its service with them |
| Warranty / liability disclaimers (§7, §8) | **Weights come with no warranty.** Solvent's own verification tiers are the mitigation; a client's liability for bad output rests with Solvent, not Alibaba Cloud |
| Acceptable-use policy | **None exists.** The HF repo contains only `LICENSE`, `README.md` and weight/tokenizer files — no usage-policy or NOTICE file. Repo is not gated |
| Monthly-active-user threshold | **None.** No such clause in Apache-2.0 |
| Output restrictions | **None** |

## 6. Why the family name is not an answer

Within the **same** Qwen2.5 family, licences differ by size:

| Model | Licence | Commercial use |
| --- | --- | --- |
| Qwen2.5-0.5B-Instruct | `apache-2.0` | Yes |
| **Qwen2.5-3B-Instruct** | **`qwen-research`** | **No — "FOR NON-COMMERCIAL PURPOSES ONLY"; "Non-Commercial shall mean for research or evaluation purposes only"** |
| Qwen2.5-7B-Instruct | `apache-2.0` | Yes |
| **Qwen2.5-14B-Instruct** | **`apache-2.0`** | **Yes — the artifact in use** |
| Qwen2.5-32B-Instruct | `apache-2.0` | Yes |
| **Qwen2.5-72B-Instruct** | **`qwen`** | Conditional — "more than 100 million monthly active users, you shall request a license from us" |

Ollama's own library page states the same split: *"all models except the 3B and
72B are released under the Apache 2.0 license, while the 3B and 72B models are
under the Qwen license."*

Two of six sizes are not Apache-2.0. **"Qwen2.5 is Apache-2.0" is false.** A
clearance keyed on a family name would have cleared the research-only 3B for
paid client work — which is precisely the failure mode Solvent now refuses
structurally.

## 7. Evidence sources

| Source | Kind |
| --- | --- |
| `https://registry.ollama.ai/v2/library/qwen2.5/manifests/14b-instruct` | Primary — Ollama registry |
| `https://registry.ollama.ai/v2/library/qwen2.5/blobs/sha256:832dd9e0…` | Primary — the packaged licence itself |
| `https://huggingface.co/Qwen/Qwen2.5-14B-Instruct/raw/main/LICENSE` | Primary — publisher's licence file |
| `https://huggingface.co/api/models/Qwen/Qwen2.5-14B-Instruct` | Primary — publisher metadata (`license: apache-2.0`, not gated, file list) |
| `https://huggingface.co/Qwen/Qwen2.5-{0.5B,3B,7B,32B,72B}-Instruct` | Primary — sibling licences |
| `https://ollama.com/library/qwen2.5` | Primary — Ollama's licence statement |

No blog, forum or licence-aggregator site was relied on.

## 8. Confidence, and what is still not proven

**Confidence: HIGH** that `qwen2.5:14b-instruct` as published by the Ollama
library is Apache-2.0 licensed and may be used for paid client work.

Two honest limits:

1. **A per-invocation CLI flag is invisible to any pre-flight check.** roflo's
   precedence is *flags > env > file*. Solvent now reads the two **persistent**
   layers (§9), but `roflo -m <tag>` overrides both at the moment of the call,
   after any readiness check has run. Nothing checkable before execution can see
   it. The mitigation is operational, not architectural: production invocations
   must not pass `-m`/`-b`.
2. **Solvent cannot verify what is running locally.** External execution is
   fail-closed, so Solvent cannot query a local Ollama and confirm the installed
   blob digest matches the approved one. Integrity at pull time rests on
   Ollama's own content-addressed verification. Solvent verifies the *approval*,
   not the *installation*.
3. **A tag is a pointer.** Ollama could repoint `14b-instruct` at different
   weights. That is why the approval record stores the **digest**, and why
   `ollama pull` re-verifying a changed digest is the owner's signal to re-clear.

Neither limit is a licence question, and neither is grounds to withhold
clearance — but both are why the clearance is recorded against a digest.

## 9. Enforcement

`PolicyStore.approve_model_artifact()` records the clearance and refuses an
incomplete one: model, tag, digest, licence id, licence source and verification
date are all required, and the digest must be a real `sha256:` content address —
a family name cannot be substituted for one. It is the owner path only, via
`amend()`, so no worker, no Learning, no Discovery and no client text can write
it; an architectural test asserts no authority module even calls it.

`readiness.model_artifact_check()` then requires **two facts to agree**: Policy
has cleared a specific artifact, *and* configuration names that same tag. A
clearance nothing runs is useless; a configured model nobody cleared is the
defect. Either mismatch blocks the first real job through
`assert_may_attempt_first_real_job()`.

### The defect the 17 Sep re-run found

The first implementation read **only `roflo.toml`**. But roflo's documented
precedence is *flags > env > file*, and `roflo/config.py::_env_overrides` honours
`ROFLO_MODEL` and `ROFLO_BACKEND`. So:

```
ROFLO_MODEL=qwen2.5:3b-instruct
  → roflo loads qwen2.5:3b-instruct        (research-only: NON-COMMERCIAL ONLY)
  → readiness reads roflo.toml             (still says 14b-instruct)
  → OD-3 reports CLEARED                   ← reproduced live, then fixed
```

Checking the lower-precedence layer and calling it "the configured model" was
the defect — the environment is what wins. `configured_model()` now applies the
same precedence the runtime does, and a test asserts the variable names still
match `roflo/config.py`, so a rename there fails the suite rather than silently
blinding the check.

Simulation is deliberately untouched by this — development keeps working; only
paid client work is gated.

## 10. The clearance record, ready for the owner to apply

```python
policy.approve_model_artifact(
    owner_identity="<the registered owner>",
    model="Qwen2.5-14B-Instruct",
    tag="qwen2.5:14b-instruct",
    digest="sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54",
    license_id="Apache-2.0",
    license_source="https://huggingface.co/Qwen/Qwen2.5-14B-Instruct/raw/main/LICENSE",
    verified_on="2026-09-15",
    restrictions="No trademark licence (§6): do not brand the service with "
                 "Qwen or Alibaba marks. Weights carry no warranty (§7-8). "
                 "Redistributing weights or a fine-tune would trigger §4 notice "
                 "duties; consuming output does not.",
    reason="OD-3: verified against publisher LICENSE and the packaged licence "
           "blob, byte-identical",
)
```

**This is not applied in the repository.** Writing it is an owner act, and the
owner identity is theirs to supply.
