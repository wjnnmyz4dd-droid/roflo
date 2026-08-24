"""The request path: messages -> rendered prompt -> backend -> tokens.

This module is the whole pipeline. It is worth reading top to bottom once,
because what it *doesn't* contain is the point of the project: there is no
filter step, no classifier pass, no prompt rewriting and no post-processing of
what the model returns. Text goes to the weights and comes back.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from .backends import load_backend
from .config import Config
from .prompt import get_template, render
from .types import Chunk, Message, ModelInfo, SamplingParams, Usage

_UNSET = object()


def estimate_tokens(text: str) -> int:
    """Rough token count for usage reporting.

    Backends that stream plain text do not report token counts, and we have no
    tokenizer in-process for the HTTP backends. Roughly four characters per
    token is the usual English approximation -- treat these numbers as
    indicative, not exact.
    """
    return max(1, round(len(text) / 4)) if text else 0


def _holdback(seen: str, stops: Sequence[str]) -> int:
    """Length of ``seen`` that is safe to emit now.

    Holds back a trailing run of characters that is a proper prefix of some
    stop string, since the next chunk may complete it.
    """
    safe = len(seen)
    for stop in stops:
        for size in range(min(len(stop) - 1, len(seen)), 0, -1):
            if seen.endswith(stop[:size]):
                safe = min(safe, len(seen) - size)
                break
    return safe


class Engine:
    """Holds the configured backend and renders prompts for it."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.backend = load_backend(config.backend)

    @property
    def model_name(self) -> str:
        return self.config.backend.model or self.backend.kind

    def render(
        self,
        messages: Sequence[Message],
        template: str | None = None,
        system_prompt: str | None | object = _UNSET,
        add_generation_prompt: bool = True,
    ) -> str:
        """Render messages using the configured template.

        Passing ``system_prompt=None`` explicitly suppresses the configured
        system prompt for this call; omitting it uses the configured value.
        """
        system = self.config.system_prompt if system_prompt is _UNSET else system_prompt
        return render(
            messages,
            template=template or self.config.template,
            system_prompt=system,  # type: ignore[arg-type]
            add_generation_prompt=add_generation_prompt,
        )

    def resolve_params(
        self, params: SamplingParams | None = None, template: str | None = None
    ) -> SamplingParams:
        """Merge request params over configured defaults and add template stops.

        Template stop strings are added so a chat-formatted model halts at the
        end of its turn instead of continuing into a hallucinated next one.
        Base ("raw") models have no such markers and get no added stops.
        """
        resolved = params or self.config.sampling
        tmpl = get_template(template or self.config.template)
        if tmpl.stop:
            resolved = resolved.merged(stop=list(tmpl.stop))
        return resolved

    async def stream_text(
        self, prompt: str, params: SamplingParams | None = None
    ) -> AsyncIterator[Chunk]:
        """Stream a completion for an already-rendered prompt."""
        async for chunk in self.backend.stream(prompt, params or self.config.sampling):
            yield chunk

    async def stream_chat(
        self,
        messages: Sequence[Message],
        params: SamplingParams | None = None,
        template: str | None = None,
        system_prompt: str | None | object = _UNSET,
    ) -> AsyncIterator[Chunk]:
        """Render ``messages`` and stream the model's continuation.

        Stop strings are trimmed from the output. They are template markers
        rather than model content, and a caller streaming to a terminal should
        never see a trailing ``<|im_end|>``. Because a marker can arrive split
        across two chunks, any tail that could still turn into one is held back
        until the next chunk resolves it.
        """
        prompt = self.render(messages, template=template, system_prompt=system_prompt)
        resolved = self.resolve_params(params, template)
        stops = resolved.stop

        seen = ""       # everything the backend has produced
        emitted = 0     # how much of `seen` we have already yielded

        async for chunk in self.backend.stream(prompt, resolved):
            seen += chunk.text

            cut = min((seen.find(s) for s in stops if s in seen), default=-1)
            if cut >= 0:
                if cut > emitted:
                    yield Chunk(text=seen[emitted:cut])
                yield Chunk(finish_reason="stop")
                return

            # On the final chunk there is no "next chunk" to complete a
            # partial marker, so nothing needs holding back.
            final = bool(chunk.finish_reason)
            safe = _holdback(seen, stops) if stops and not final else len(seen)
            if safe > emitted:
                yield Chunk(text=seen[emitted:safe])
                emitted = safe

            if final:
                yield Chunk(finish_reason=chunk.finish_reason)
                return

        if len(seen) > emitted:
            yield Chunk(text=seen[emitted:])

    async def chat(
        self,
        messages: Sequence[Message],
        params: SamplingParams | None = None,
        template: str | None = None,
        system_prompt: str | None | object = _UNSET,
    ) -> tuple[str, str, Usage]:
        """Non-streaming chat. Returns ``(text, finish_reason, usage)``."""
        prompt = self.render(messages, template=template, system_prompt=system_prompt)
        parts: list[str] = []
        finish = "stop"
        async for chunk in self.stream_chat(
            messages, params=params, template=template, system_prompt=system_prompt
        ):
            parts.append(chunk.text)
            if chunk.finish_reason:
                finish = chunk.finish_reason
        text = "".join(parts)
        usage = Usage(estimate_tokens(prompt), estimate_tokens(text))
        return text, finish, usage

    async def list_models(self) -> list[ModelInfo]:
        return await self.backend.list_models()

    async def health(self) -> bool:
        return await self.backend.health()

    async def aclose(self) -> None:
        await self.backend.aclose()
