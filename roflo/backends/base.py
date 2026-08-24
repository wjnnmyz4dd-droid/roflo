"""Backend interface.

A backend takes a fully rendered prompt string and streams tokens back. It does
not see ``Message`` objects and never applies a chat template of its own -- that
work belongs to :mod:`roflo.prompt`, so the bytes the model conditions on are
exactly the bytes rendered there.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import BackendConfig
from ..types import BackendError, Chunk, ModelInfo


class Backend(ABC):
    """Streaming text-completion backend."""

    kind: str = "base"
    #: Endpoint path used by :meth:`health`, relative to ``base_url``.
    health_path: str = "/"
    default_base_url: str = ""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.model = config.model
        self.base_url = (config.base_url or self.default_base_url).rstrip("/")

    @abstractmethod
    def stream(self, prompt: str, params: Any) -> AsyncIterator[Chunk]:
        """Yield :class:`Chunk` objects as the model produces them."""

    async def complete(self, prompt: str, params: Any) -> str:
        """Collect a full completion by draining :meth:`stream`."""
        return "".join([chunk.text async for chunk in self.stream(prompt, params)])

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self.model, backend=self.kind)] if self.model else []

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class HTTPBackend(Backend):
    """Shared plumbing for backends that reach a server over HTTP."""

    def __init__(self, config: BackendConfig) -> None:
        super().__init__(config)
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(config.timeout, connect=10.0),
            headers=headers,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(self.health_path, timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise BackendError(
                f"{self.kind} returned {exc.response.status_code}: "
                f"{exc.response.text[:400]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"{self.kind} unreachable at {self.base_url}: {exc}") from exc
        return response.json()

    async def _stream_lines(
        self, path: str, payload: dict[str, Any]
    ) -> AsyncIterator[str]:
        """POST ``payload`` and yield non-empty response lines."""
        try:
            async with self._client.stream("POST", path, json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise BackendError(
                        f"{self.kind} returned {response.status_code}: {body[:400]}"
                    )
                async for line in response.aiter_lines():
                    if line := line.strip():
                        yield line
        except httpx.HTTPError as exc:
            raise BackendError(f"{self.kind} unreachable at {self.base_url}: {exc}") from exc

    @staticmethod
    def parse_sse(line: str) -> dict[str, Any] | None:
        """Decode one ``data:`` line of a Server-Sent Events stream.

        Returns ``None`` for comments, non-data fields and the ``[DONE]``
        sentinel.
        """
        if not line.startswith("data:"):
            return None
        body = line[5:].strip()
        if not body or body == "[DONE]":
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
