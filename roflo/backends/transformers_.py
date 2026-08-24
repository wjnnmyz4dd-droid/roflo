"""In-process Hugging Face transformers backend.

Loads a checkpoint directly, so there is no server between you and the weights.
``tokenize(..., add_special_tokens=False)`` is deliberate: the template layer
already emitted whatever BOS the format calls for, and letting the tokenizer add
a second one changes what the model actually conditions on.

Requires the ``local`` extra: ``pip install 'roflo[local]'``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ..config import BackendConfig
from ..types import BackendError, Chunk, ModelInfo, SamplingParams
from .base import Backend


class TransformersBackend(Backend):
    kind = "transformers"

    def __init__(self, config: BackendConfig) -> None:
        super().__init__(config)
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise BackendError(
                "the transformers backend needs the 'local' extra: "
                "pip install 'roflo[local]'"
            ) from exc

        if not self.model:
            raise BackendError("backend.model must name a checkpoint or local path")

        self._torch = torch
        opts = dict(config.options)
        device_map = opts.pop("device_map", "auto")
        dtype = opts.pop("dtype", "auto")
        self._tokenizer = AutoTokenizer.from_pretrained(
            opts.pop("tokenizer", self.model), trust_remote_code=opts.pop("trust_remote_code", False)
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model, device_map=device_map, dtype=dtype, **opts
        )
        self._model.eval()

    def _generate_kwargs(self, params: SamplingParams) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "max_new_tokens": params.max_tokens,
            "do_sample": params.temperature > 0,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "top_k": params.top_k,
            "repetition_penalty": params.repeat_penalty,
        }
        if params.min_p:
            kwargs["min_p"] = params.min_p
        if not kwargs["do_sample"]:
            # HF warns and ignores these when sampling is off; drop them.
            for key in ("temperature", "top_p", "top_k"):
                kwargs.pop(key, None)
        kwargs.update(params.extra)
        return kwargs

    async def stream(self, prompt: str, params: SamplingParams) -> AsyncIterator[Chunk]:
        from transformers import TextIteratorStreamer

        if params.seed is not None:
            self._torch.manual_seed(params.seed)

        inputs = self._tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
        streamer = TextIteratorStreamer(
            self._tokenizer, skip_prompt=True, skip_special_tokens=True
        )

        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(
            None,
            lambda: self._model.generate(
                **inputs,
                streamer=streamer,
                pad_token_id=self._tokenizer.eos_token_id,
                **self._generate_kwargs(params),
            ),
        )

        text = ""
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def pump() -> None:
            for piece in streamer:
                loop.call_soon_threadsafe(queue.put_nowait, piece)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, pump)

        while (piece := await queue.get()) is not None:
            text += piece
            yield Chunk(text=piece)
            if hit := next((s for s in params.stop if s in text), None):
                yield Chunk(finish_reason="stop")
                del hit
                return

        await task
        yield Chunk(finish_reason="stop")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self.model, backend=self.kind, detail={"local": True})]
