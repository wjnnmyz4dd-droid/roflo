import json

import pytest
from fastapi.testclient import TestClient

from roflo.config import BackendConfig, Config, ServerConfig
from roflo.server import create_app
from roflo.types import BackendError, Chunk

from .test_engine import ScriptedBackend


def make_client(**kwargs):
    cfg = Config(backend=BackendConfig(kind="echo", model="m"), **kwargs)
    return TestClient(create_app(cfg))


def sse_events(response):
    """Decode the data frames of an SSE response body."""
    out = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            body = line[6:]
            if body != "[DONE]":
                out.append(json.loads(body))
    return out


class TestHealthAndModels:
    def test_health_reports_configuration(self):
        with make_client(template="raw") as client:
            body = client.get("/health").json()
            assert body["status"] == "ok"
            assert body["backend"] == "echo"
            assert body["template"] == "raw"

    def test_models_uses_openai_shape(self):
        with make_client() as client:
            body = client.get("/v1/models").json()
            assert body["object"] == "list"
            assert body["data"][0]["id"] == "m"


class TestChatCompletions:
    def test_non_streaming_response_shape(self):
        with make_client(template="raw") as client:
            r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "a b"}]})
            body = r.json()
            assert r.status_code == 200
            assert body["object"] == "chat.completion"
            assert body["choices"][0]["message"]["role"] == "assistant"
            assert body["choices"][0]["message"]["content"].strip() == "a b"
            assert body["usage"]["total_tokens"] > 0

    def test_streaming_emits_role_then_content_then_done(self):
        with make_client(template="raw") as client:
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "a b"}], "stream": True},
            )
            assert r.status_code == 200
            events = sse_events(r)
            assert events[0]["choices"][0]["delta"]["role"] == "assistant"
            text = "".join(e["choices"][0]["delta"].get("content", "") for e in events)
            assert text.strip() == "a b"
            assert events[-1]["choices"][0]["finish_reason"] == "stop"
            assert r.text.endswith("data: [DONE]\n\n")

    def test_openai_content_parts_are_accepted(self):
        with make_client(template="raw") as client:
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": [{"type": "text", "text": "hey"}]}]},
            )
            assert r.json()["choices"][0]["message"]["content"].strip() == "hey"

    def test_template_can_be_selected_per_request(self):
        """Inspect the prompt the backend was handed, not the echoed reply."""
        with make_client(template="raw") as client:
            backend = ScriptedBackend(["ok"])
            client.app.state.engine.backend = backend
            client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "template": "alpaca"},
            )
            assert backend.prompt.startswith("### Instruction:\nhi")

            client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            assert backend.prompt == "hi"

    def test_invalid_role_is_rejected(self):
        with make_client() as client:
            r = client.post("/v1/chat/completions", json={"messages": [{"role": "tool", "content": "x"}]})
            assert r.status_code == 422

    def test_invalid_sampling_value_is_rejected(self):
        with make_client() as client:
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "x"}], "top_p": 5},
            )
            assert r.status_code == 422


class TestSystemPrompt:
    """The echo backend replays its prompt, so the response reveals what was sent."""

    def test_nothing_is_prepended_when_unconfigured(self):
        with make_client(template="raw") as client:
            r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            assert r.json()["choices"][0]["message"]["content"].strip() == "hi"

    def test_configured_system_prompt_is_sent(self):
        with make_client(template="raw", system_prompt="SYS") as client:
            r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            assert "SYS" in r.json()["choices"][0]["message"]["content"]

    def test_empty_string_suppresses_the_configured_prompt(self):
        with make_client(template="raw", system_prompt="SYS") as client:
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "system_prompt": ""},
            )
            assert "SYS" not in r.json()["choices"][0]["message"]["content"]

    def test_request_can_override_the_configured_prompt(self):
        with make_client(template="raw", system_prompt="SYS") as client:
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "system_prompt": "OTHER"},
            )
            content = r.json()["choices"][0]["message"]["content"]
            assert "OTHER" in content and "SYS" not in content


class TestCompletions:
    def test_prompt_is_passed_through_untouched(self):
        with make_client() as client:
            r = client.post("/v1/completions", json={"prompt": "<|weird|> raw text"})
            body = r.json()
            assert body["object"] == "text_completion"
            assert body["choices"][0]["text"].strip() == "<|weird|> raw text"

    def test_streaming_completions(self):
        with make_client() as client:
            r = client.post("/v1/completions", json={"prompt": "a b", "stream": True})
            text = "".join(e["choices"][0]["text"] for e in sse_events(r))
            assert text.strip() == "a b"


class TestAuth:
    def test_key_is_not_required_when_unset(self):
        with make_client() as client:
            assert client.get("/v1/models").status_code == 200

    def test_missing_key_is_rejected(self):
        with make_client(server=ServerConfig(api_key="secret")) as client:
            assert client.get("/v1/models").status_code == 401

    def test_wrong_key_is_rejected(self):
        with make_client(server=ServerConfig(api_key="secret")) as client:
            r = client.get("/v1/models", headers={"Authorization": "Bearer nope"})
            assert r.status_code == 401

    def test_correct_key_is_accepted(self):
        with make_client(server=ServerConfig(api_key="secret")) as client:
            r = client.get("/v1/models", headers={"Authorization": "Bearer secret"})
            assert r.status_code == 200

    def test_health_stays_open_for_probes(self):
        with make_client(server=ServerConfig(api_key="secret")) as client:
            assert client.get("/health").status_code == 200


class TestBackendFailure:
    def test_backend_error_maps_to_502(self):
        class Broken(ScriptedBackend):
            async def stream(self, prompt, params):
                raise BackendError("server down")
                yield Chunk()  # pragma: no cover

        with make_client(template="raw") as client:
            client.app.state.engine.backend = Broken([])
            r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})
            assert r.status_code == 502
            assert "server down" in r.json()["error"]["message"]

    def test_backend_error_mid_stream_is_reported_in_band(self):
        class Broken(ScriptedBackend):
            async def stream(self, prompt, params):
                yield Chunk(text="partial")
                raise BackendError("died")

        with make_client(template="raw") as client:
            client.app.state.engine.backend = Broken([])
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "x"}], "stream": True},
            )
            events = sse_events(r)
            assert any("error" in e for e in events)
