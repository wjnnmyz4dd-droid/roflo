# roflo

Self-hosted LLM inference with a pluggable backend and no middleware in the
request path.

You bring the weights. `roflo` renders the prompt, streams the tokens back, and
stays out of the way. It runs open-weights models through Ollama, llama.cpp,
vLLM, or Hugging Face transformers, and puts an OpenAI-compatible API in front
of whichever one you pick, so existing client code works unchanged.

## What "no guardrails" means here

Concretely, and only these things:

- **No system prompt unless you write one.** `system_prompt` is unset by
  default. There is no house persona, no policy preamble, no "you are a helpful
  assistant" appended behind your back.
- **No template you didn't choose.** Every backend targets a *completion*
  endpoint, never a chat endpoint. Ollama is called with `raw: true`; llama.cpp
  gets `/completion`; vLLM gets `/v1/completions`. The bytes rendered by
  `roflo.prompt` are the bytes the model conditions on.
- **No filtering, in either direction.** There is no classifier pass, no
  keyword list, no prompt rewriting, no post-processing of what comes back.
  Read [`roflo/engine.py`](roflo/engine.py) — it's the whole request path, and
  there is nowhere for a filter to hide.
- **No clamped sampling.** `temperature`, `top_k`, `min_p`, `repeat_penalty`
  and friends pass through as given. `[backend.options]` forwards anything else
  your runtime accepts.
- **A base-model path.** `template = "raw"` sends pure text with no chat markers
  at all, for pretrained checkpoints that continue text rather than answer turns.

What it does *not* mean: this is plumbing, not a modification of any model. A
checkpoint's own behaviour comes from its training, and refusal behaviour tuned
into an instruct model is still there when you serve it through `roflo`. If you
want different behaviour, that's a question of which checkpoint you load — many
open-weights base and community-tuned models exist precisely because their
defaults differ. The one thing `roflo` guarantees is that nothing *in this
process* is added between you and the weights.

Because nothing intervenes, what the deployment produces is determined by the
model you load and the prompts you send. That makes the operator — you —
responsible for the output, which is the tradeoff you are choosing when you
self-host instead of using a hosted API. Worth knowing before you bind the
server to anything other than localhost.

## Install

```bash
git clone https://github.com/wjnnmyz4dd-droid/roflo && cd roflo
python -m venv .venv && source .venv/bin/activate
pip install -e .

pip install -e '.[local]'   # adds the in-process transformers backend
pip install -e '.[dev]'     # adds pytest
```

Requires Python 3.11+.

## Quickstart

```bash
# 1. Serve a model. Any of these works; ollama is the least setup.
ollama serve & ollama pull qwen2.5:14b-instruct

# 2. Point roflo at it and confirm the connection.
roflo -b ollama -m qwen2.5:14b-instruct check

# 3. Talk to it.
roflo -b ollama -m qwen2.5:14b-instruct chat
```

Then write a `roflo.toml` (a commented example ships in the repo) so you can
drop the flags.

### See exactly what gets sent

`render` prints the literal prompt string and generates nothing. This is the
fastest way to confirm the claims above rather than take them on trust:

```console
$ roflo -t chatml render "hello"
'<|im_start|>user\nhello<|im_end|>\n<|im_start|>assistant\n'

$ roflo -t raw render "The year is 2027 and"
'The year is 2027 and'
```

No system turn appears in either, because none was configured.

## Commands

| Command | Purpose |
| --- | --- |
| `roflo chat` | Interactive REPL. `/system <text>`, `/nosystem`, `/reset`, `/show`, `/exit` |
| `roflo complete "text"` | One-shot raw completion, no template. Reads stdin if no argument |
| `roflo render "text"` | Print the rendered prompt and exit |
| `roflo check` | Probe the backend, print the resolved configuration |
| `roflo serve` | Run the OpenAI-compatible server |
| `roflo info` | List backends and templates |

Global flags: `-c/--config`, `-b/--backend`, `-m/--model`, `--base-url`,
`-t/--template`, `-s/--system`, `--temperature`, `--max-tokens`, `--seed`.

## Backends

| Backend | Endpoint used | Notes |
| --- | --- | --- |
| `ollama` | `/api/generate` with `raw: true` | Bypasses the Modelfile template and its `SYSTEM` line |
| `llamacpp` | `/completion` | Native `llama-server` route; GGUF files |
| `vllm` | `/v1/completions` | Also fits TGI and other OpenAI-compatible servers |
| `transformers` | in-process | Loads a checkpoint directly; needs the `local` extra |
| `echo` | in-process | Replays the prompt. For tests and for inspecting rendering |

Adding one means subclassing `Backend` (or `HTTPBackend`), implementing
`stream()`, and registering it in `roflo/backends/__init__.py`. The registry
resolves lazily, so a new optional dependency never becomes an import-time cost
for everyone else.

## Templates

`chatml`, `llama3`, `mistral`, `gemma`, `alpaca`, `raw`.

Match the template to the checkpoint — a Llama 3 model fed ChatML markers
produces bad output for reasons that look like model problems but aren't.
`mistral` and `gemma` have no system role, so a system prompt is folded into
the first user turn rather than silently dropped.

## Configuration

`roflo.toml` in the working directory, or `~/.config/roflo/roflo.toml`, or
`-c path`. Precedence: **CLI flags > `ROFLO_*` env vars > file > defaults**.

Env vars: `ROFLO_BACKEND`, `ROFLO_MODEL`, `ROFLO_BASE_URL`, `ROFLO_API_KEY`,
`ROFLO_TEMPLATE`, `ROFLO_SYSTEM_PROMPT`, `ROFLO_HOST`, `ROFLO_PORT`,
`ROFLO_SERVER_API_KEY`, `ROFLO_TEMPERATURE`, `ROFLO_MAX_TOKENS`.

Unknown keys are rejected at load time rather than ignored, so a typo surfaces
as an error instead of a setting that silently does nothing.

## HTTP API

`roflo serve` exposes `POST /v1/chat/completions`, `POST /v1/completions`,
`GET /v1/models` and `GET /health`. Streaming and non-streaming both work.

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="unused")
response = client.chat.completions.create(
    model="local",
    messages=[{"role": "user", "content": "hello"}],
    temperature=0.9,
)
```

Two extensions to the OpenAI request body:

- `template` — override the chat template for one request.
- `system_prompt` — override the configured system prompt. Send `""` to
  suppress it entirely; omit the field to use whatever is configured.

`server.api_key` gates the port so that binding beyond localhost doesn't hand
everyone on the network a free inference endpoint. It controls *access*, not
content.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

94 tests, no network and no model weights required — the `echo` and scripted
backends stand in for real ones, and the HTTP backends are exercised against
mocked transports that assert on the exact request payloads.
