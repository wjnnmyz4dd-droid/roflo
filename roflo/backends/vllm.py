"""vLLM backend.

Uses vLLM's OpenAI-compatible ``/v1/completions`` endpoint (not
``/v1/chat/completions``), so vLLM's ``--chat-template`` is not applied and the
prompt is passed through unchanged. Works against any server exposing that
route, including text-generation-inference and llama-server's OpenAI shim.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..types import Chunk, ModelInfo, SamplingParams
from .base import HTTPBackend


class VLLMBackend(HTTPBackend):
    kind = "vllm"
    default_base_url = "http://127.0.0.1:8000"
    health_path = "/health"

    def payload(self, prompt: str, params: SamplingParams) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "presence_penalty": params.presence_penalty,
            "frequency_penalty": params.frequency_penalty,
            "stream": True,
        }
        # top_k / min_p / repeat_penalty are vLLM extensions to the OpenAI body.
        if params.top_k:
            body["top_k"] = params.top_k
        if params.min_p:
            body["min_p"] = params.min_p
        if params.repeat_penalty != 1.0:
            body["repetition_penalty"] = params.repeat_penalty
        if params.seed is not None:
            body["seed"] = params.seed
        if params.stop:
            body["stop"] = params.stop
        body.update(self.config.options)
        body.update(params.extra)
        return body

    async def stream(self, prompt: str, params: SamplingParams) -> AsyncIterator[Chunk]:
        async for line in self._stream_lines("/v1/completions", self.payload(prompt, params)):
            event = self.parse_sse(line)
            if event is None:
                continue
            for choice in event.get("choices", []):
                if text := choice.get("text", ""):
                    yield Chunk(text=text)
                if reason := choice.get("finish_reason"):
                    yield Chunk(finish_reason=reason)
                    return

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self.client.get("/v1/models")
            response.raise_for_status()
        except Exception:
            return await super().list_models()
        return [
            ModelInfo(id=m["id"], backend=self.kind)
            for m in response.json().get("data", [])
        ]
