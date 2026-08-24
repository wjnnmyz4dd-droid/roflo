"""Ollama backend.

Uses ``/api/generate`` with ``raw: true``. That flag tells Ollama to skip the
template baked into the model's Modelfile, so the prompt rendered by
:mod:`roflo.prompt` reaches the model verbatim -- no Modelfile SYSTEM line, no
template wrapper.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ..types import Chunk, ModelInfo, SamplingParams
from .base import HTTPBackend


class OllamaBackend(HTTPBackend):
    kind = "ollama"
    default_base_url = "http://127.0.0.1:11434"
    health_path = "/api/tags"

    def payload(self, prompt: str, params: SamplingParams) -> dict[str, Any]:
        options: dict[str, Any] = {
            "num_predict": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "top_k": params.top_k,
            "min_p": params.min_p,
            "repeat_penalty": params.repeat_penalty,
            "presence_penalty": params.presence_penalty,
            "frequency_penalty": params.frequency_penalty,
        }
        if params.seed is not None:
            options["seed"] = params.seed
        if params.stop:
            options["stop"] = params.stop
        options.update(self.config.options)
        options.update(params.extra)
        return {
            "model": self.model,
            "prompt": prompt,
            "raw": True,          # bypass the Modelfile template entirely
            "stream": True,
            "options": options,
        }

    async def stream(self, prompt: str, params: SamplingParams) -> AsyncIterator[Chunk]:
        async for line in self._stream_lines("/api/generate", self.payload(prompt, params)):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if text := event.get("response", ""):
                yield Chunk(text=text)
            if event.get("done"):
                yield Chunk(finish_reason=event.get("done_reason") or "stop")
                return

    async def list_models(self) -> list[ModelInfo]:
        data = await self._post_json_or_get("/api/tags")
        return [
            ModelInfo(
                id=m["name"],
                backend=self.kind,
                detail={"size": m.get("size"), "modified": m.get("modified_at")},
            )
            for m in data.get("models", [])
        ]

    async def _post_json_or_get(self, path: str) -> dict[str, Any]:
        response = await self.client.get(path)
        response.raise_for_status()
        return response.json()
