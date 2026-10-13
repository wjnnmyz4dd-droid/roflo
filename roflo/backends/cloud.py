"""A hosted, OpenAI-compatible chat-completions endpoint.

Deliberately provider-neutral. ``roflo`` already speaks OpenAI-compatible
HTTP on both sides -- it *serves* that protocol, and :class:`HTTPBackend`
already sends ``Authorization: Bearer`` -- so a cloud backend is the same
protocol with a configurable ``base_url``. The owner points it at whichever
provider they hold credit with; nothing here picks a vendor for them, and no
provider's name is compiled in.

**It never carries a key.** The key arrives through configuration
(``ROFLO_API_KEY``) at the moment the backend is constructed and is never
written to the repository, the audit log or a report. Without one the
backend still constructs and still answers ``health()`` honestly -- it
reports that it is unconfigured rather than pretending to be ready, because
a route that reads "available" and fails on first use is worse than one that
says what it is.

The streaming format is OpenAI's server-sent events: ``data: {json}`` lines
terminated by ``data: [DONE]``. Usage, where the provider returns it, is
reported so the cost of a call is measured rather than estimated.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ..types import Chunk, ModelInfo, SamplingParams
from .base import BackendError, HTTPBackend


class CloudBackend(HTTPBackend):
    """Any hosted endpoint speaking OpenAI's chat-completions API."""

    kind = "cloud"
    #: No default. A cloud backend with a guessed address would silently talk
    #: to whatever happens to be at it.
    default_base_url = ""
    health_path = "/v1/models"

    @property
    def configured(self) -> bool:
        """Both halves are needed, and neither is inferable."""
        return bool(self.base_url and self.config.api_key)

    def unconfigured_reason(self) -> str:
        if not self.base_url:
            missing = "no base_url is set"
        elif not self.config.api_key:
            missing = "no api_key is set"
        else:
            return ""
        return (f"the cloud backend is not configured: {missing}. Set "
                "ROFLO_BASE_URL and ROFLO_API_KEY, or choose a local backend")

    def payload(self, prompt: str, params: SamplingParams) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "presence_penalty": params.presence_penalty,
            "frequency_penalty": params.frequency_penalty,
            "stream": True,
            # Ask for usage on the final chunk where the provider supports
            # it. A call whose cost is unknown cannot be charged to a job.
            "stream_options": {"include_usage": True},
        }
        if params.seed is not None:
            body["seed"] = params.seed
        if params.stop:
            body["stop"] = list(params.stop)
        body.update(self.config.options)
        body.update(params.extra)
        return body

    async def health(self) -> bool:
        """Unconfigured is not healthy, and is not an error either."""
        if not self.configured:
            return False
        return await super().health()

    async def list_models(self) -> list[ModelInfo]:
        if not self.configured:
            return []
        try:
            response = await self.client.get("/v1/models", timeout=10.0)
            rows = response.json().get("data", [])
        except Exception:  # noqa: BLE001 - a listing failure is not fatal
            return ([ModelInfo(id=self.model, backend=self.kind)]
                    if self.model else [])
        return [ModelInfo(id=row.get("id", ""), backend=self.kind)
                for row in rows if row.get("id")]

    async def stream(self, prompt: str,
                     params: SamplingParams) -> AsyncIterator[Chunk]:
        if not self.configured:
            raise BackendError(self.unconfigured_reason())

        async for line in self._stream_lines("/v1/chat/completions",
                                             self.payload(prompt, params)):
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            for choice in event.get("choices") or ():
                delta = choice.get("delta") or {}
                if text := delta.get("content"):
                    yield Chunk(text=text)
                if reason := choice.get("finish_reason"):
                    yield Chunk(finish_reason=reason)

            # The usage chunk arrives last and has no choices. Passing it on
            # is what lets a caller bill the real number of tokens instead
            # of a guess.
            if usage := event.get("usage"):
                yield Chunk(text="", usage=dict(usage))
