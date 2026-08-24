"""llama.cpp server backend (``llama-server``).

Uses the native ``/completion`` endpoint, which takes a prompt string as-is.
The OpenAI-compatible ``/v1/chat/completions`` route on the same server would
apply the GGUF's built-in chat template; this path does not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..types import Chunk, ModelInfo, SamplingParams
from .base import HTTPBackend


class LlamaCppBackend(HTTPBackend):
    kind = "llamacpp"
    default_base_url = "http://127.0.0.1:8000"
    health_path = "/health"

    def payload(self, prompt: str, params: SamplingParams) -> dict[str, Any]:
        body: dict[str, Any] = {
            "prompt": prompt,
            "n_predict": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "top_k": params.top_k,
            "min_p": params.min_p,
            "repeat_penalty": params.repeat_penalty,
            "presence_penalty": params.presence_penalty,
            "frequency_penalty": params.frequency_penalty,
            "stream": True,
            "cache_prompt": True,
        }
        if params.seed is not None:
            body["seed"] = params.seed
        if params.stop:
            body["stop"] = params.stop
        body.update(self.config.options)
        body.update(params.extra)
        return body

    async def stream(self, prompt: str, params: SamplingParams) -> AsyncIterator[Chunk]:
        async for line in self._stream_lines("/completion", self.payload(prompt, params)):
            event = self.parse_sse(line)
            if event is None:
                continue
            if text := event.get("content", ""):
                yield Chunk(text=text)
            if event.get("stop"):
                reason = "length" if event.get("stopped_limit") else "stop"
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
