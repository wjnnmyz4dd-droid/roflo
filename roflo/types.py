"""Core data types shared by the prompt layer, the backends and the server."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Message":
        role = raw.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValueError(f"unsupported role: {role!r}")
        content = raw.get("content")
        if isinstance(content, list):
            # OpenAI content-part form: keep the text parts, ignore the rest.
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if not isinstance(content, str):
            raise ValueError("message content must be a string or a list of text parts")
        return cls(role=role, content=content)


@dataclass(slots=True)
class SamplingParams:
    """Sampling knobs, passed through to the backend without clamping.

    Defaults mirror the underlying runtimes rather than imposing a house style:
    nothing here is narrowed on the way to the model.
    """

    max_tokens: int = 512
    temperature: float = 0.8
    top_p: float = 0.95
    top_k: int = 40
    min_p: float = 0.0
    repeat_penalty: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    seed: int | None = None
    stop: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def merged(self, **overrides: Any) -> "SamplingParams":
        """Return a copy with the non-``None`` overrides applied."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        extra = {**self.extra, **clean.pop("extra", {})}
        stop = clean.pop("stop", None)
        params = replace(self, **clean)
        params.extra = extra
        if stop is not None:
            params.stop = list(dict.fromkeys([*self.stop, *stop]))
        return params


@dataclass(slots=True)
class Chunk:
    """One streamed step of a generation."""

    text: str = ""
    finish_reason: str | None = None


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class ModelInfo:
    id: str
    backend: str
    detail: dict[str, Any] = field(default_factory=dict)


class BackendError(RuntimeError):
    """Raised when a backend is unreachable or rejects a request."""
