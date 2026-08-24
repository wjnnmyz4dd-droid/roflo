"""Deterministic backend used by the test suite and by ``roflo check``.

It echoes the prompt it was handed, which makes it the simplest way to see
exactly what the template layer produced before pointing at real weights.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..types import Chunk, ModelInfo, SamplingParams
from .base import Backend


class EchoBackend(Backend):
    kind = "echo"

    async def stream(self, prompt: str, params: SamplingParams) -> AsyncIterator[Chunk]:
        budget = max(params.max_tokens, 1)
        for word in prompt.split(" ")[:budget]:
            yield Chunk(text=word + " ")
        yield Chunk(finish_reason="stop")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self.model or "echo", backend=self.kind)]
