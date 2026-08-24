import json

import httpx
import pytest

from roflo.backends import available, load_backend
from roflo.backends.base import HTTPBackend
from roflo.config import BackendConfig
from roflo.types import BackendError, SamplingParams

PARAMS = SamplingParams(max_tokens=32, temperature=0.5, seed=7, stop=["<|im_end|>"])


def mount(backend, handler):
    """Swap in a mock transport, keeping the backend's own base_url/headers."""
    backend._client = httpx.AsyncClient(
        base_url=backend.base_url,
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json"},
    )
    return backend


def sse(*payloads):
    return "".join(f"data: {json.dumps(p)}\n\n" for p in payloads)


def test_registry_lists_all_backends():
    assert set(available()) == {"echo", "llamacpp", "ollama", "transformers", "vllm"}


def test_unknown_backend_names_the_alternatives():
    with pytest.raises(BackendError, match="ollama"):
        load_backend(BackendConfig(kind="banana"))


def test_backends_apply_their_default_url():
    assert load_backend(BackendConfig(kind="ollama")).base_url == "http://127.0.0.1:11434"
    assert load_backend(BackendConfig(kind="vllm", base_url="http://x:1/")).base_url == "http://x:1"


class TestOllama:
    def backend(self):
        return load_backend(BackendConfig(kind="ollama", model="m"))

    def test_payload_sets_raw_to_bypass_the_modelfile_template(self):
        payload = self.backend().payload("PROMPT", PARAMS)
        assert payload["raw"] is True
        assert payload["prompt"] == "PROMPT"
        assert payload["options"]["num_predict"] == 32
        assert payload["options"]["seed"] == 7
        assert payload["options"]["stop"] == ["<|im_end|>"]

    def test_config_options_override_derived_options(self):
        cfg = BackendConfig(kind="ollama", model="m", options={"num_ctx": 8192, "temperature": 1.9})
        payload = load_backend(cfg).payload("p", PARAMS)
        assert payload["options"]["num_ctx"] == 8192
        assert payload["options"]["temperature"] == 1.9

    async def test_stream_parses_ndjson(self):
        def handler(request):
            body = "".join(
                json.dumps(e) + "\n"
                for e in (
                    {"response": "he"},
                    {"response": "llo"},
                    {"response": "", "done": True, "done_reason": "stop"},
                )
            )
            return httpx.Response(200, text=body)

        backend = mount(self.backend(), handler)
        chunks = [c async for c in backend.stream("p", PARAMS)]
        assert "".join(c.text for c in chunks) == "hello"
        assert chunks[-1].finish_reason == "stop"

    async def test_http_error_becomes_backend_error(self):
        backend = mount(self.backend(), lambda r: httpx.Response(500, text="boom"))
        with pytest.raises(BackendError, match="500"):
            [c async for c in backend.stream("p", PARAMS)]


class TestLlamaCpp:
    def backend(self):
        return load_backend(BackendConfig(kind="llamacpp", model="m"))

    def test_payload_passes_prompt_verbatim(self):
        payload = self.backend().payload("<|im_start|>user\nhi", PARAMS)
        assert payload["prompt"] == "<|im_start|>user\nhi"
        assert payload["n_predict"] == 32
        assert payload["stop"] == ["<|im_end|>"]

    async def test_stream_parses_sse_and_length_stop(self):
        def handler(request):
            return httpx.Response(
                200,
                text=sse(
                    {"content": "a"},
                    {"content": "b"},
                    {"content": "", "stop": True, "stopped_limit": True},
                ),
            )

        backend = mount(self.backend(), handler)
        chunks = [c async for c in backend.stream("p", PARAMS)]
        assert "".join(c.text for c in chunks) == "ab"
        assert chunks[-1].finish_reason == "length"


class TestVLLM:
    def backend(self):
        return load_backend(BackendConfig(kind="vllm", model="org/m"))

    def test_payload_targets_the_completion_route_shape(self):
        payload = self.backend().payload("PROMPT", PARAMS)
        assert payload["prompt"] == "PROMPT"
        assert payload["model"] == "org/m"
        assert "messages" not in payload

    def test_neutral_penalties_are_omitted(self):
        payload = self.backend().payload("p", SamplingParams(repeat_penalty=1.0, min_p=0.0))
        assert "repetition_penalty" not in payload
        assert "min_p" not in payload

    async def test_stream_parses_openai_sse(self):
        def handler(request):
            return httpx.Response(
                200,
                text=sse(
                    {"choices": [{"text": "x", "finish_reason": None}]},
                    {"choices": [{"text": "y", "finish_reason": None}]},
                    {"choices": [{"text": "", "finish_reason": "stop"}]},
                )
                + "data: [DONE]\n\n",
            )

        backend = mount(self.backend(), handler)
        chunks = [c async for c in backend.stream("p", PARAMS)]
        assert "".join(c.text for c in chunks) == "xy"
        assert chunks[-1].finish_reason == "stop"


class TestSSEParsing:
    @pytest.mark.parametrize("line", [": comment", "event: ping", "data:", "data: [DONE]", "data: {bad"])
    def test_non_payload_lines_are_ignored(self, line):
        assert HTTPBackend.parse_sse(line) is None

    def test_payload_is_decoded(self):
        assert HTTPBackend.parse_sse('data: {"a": 1}') == {"a": 1}


class TestEcho:
    async def test_complete_drains_the_stream(self):
        backend = load_backend(BackendConfig(kind="echo"))
        assert (await backend.complete("a b c", SamplingParams())).strip() == "a b c"

    async def test_max_tokens_is_respected(self):
        backend = load_backend(BackendConfig(kind="echo"))
        out = await backend.complete("a b c d e", SamplingParams(max_tokens=2))
        assert out.strip() == "a b"
