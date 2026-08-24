"""OpenAI-compatible HTTP server.

Exposes ``/v1/chat/completions``, ``/v1/completions`` and ``/v1/models`` so any
OpenAI client library can point at this process by setting ``base_url``. The
wire format is borrowed; the behaviour behind it is whatever your weights do.

``server.api_key``, when set, gates access to this endpoint. It is an access
control on the port -- so that binding beyond localhost does not hand anyone on
the network a free inference endpoint -- not a content control.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Config
from .engine import Engine, estimate_tokens
from .types import BackendError, Message, SamplingParams, Usage


class ChatMessageModel(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Any


class CommonParams(BaseModel):
    model: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    repeat_penalty: float | None = Field(default=None, gt=0.0)
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    stop: list[str] | str | None = None
    stream: bool = False
    # roflo extensions
    template: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def sampling(self, base: SamplingParams) -> SamplingParams:
        stop = [self.stop] if isinstance(self.stop, str) else self.stop
        return base.merged(
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            min_p=self.min_p,
            repeat_penalty=self.repeat_penalty,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
            seed=self.seed,
            stop=stop,
            extra=self.extra,
        )


class ChatRequest(CommonParams):
    messages: list[ChatMessageModel]
    #: Omit to use the configured system prompt; send "" to suppress it.
    system_prompt: str | None = None


class CompletionRequest(CommonParams):
    prompt: str


def _usage_dict(usage: Usage) -> dict[str, int]:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = Engine(config)
        try:
            yield
        finally:
            await app.state.engine.aclose()

    app = FastAPI(title="roflo", version="0.1.0", lifespan=lifespan)
    app.state.config = config

    if config.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.server.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def require_key(request: Request) -> None:
        expected = config.server.api_key
        if not expected:
            return
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if token != expected:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    def engine(request: Request) -> Engine:
        return request.app.state.engine

    @app.exception_handler(BackendError)
    async def _backend_error(request: Request, exc: BackendError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(exc), "type": "backend_error"}},
        )

    @app.get("/health")
    async def health(eng: Engine = Depends(engine)) -> dict[str, Any]:
        ok = await eng.health()
        return {
            "status": "ok" if ok else "backend_unreachable",
            "backend": config.backend.kind,
            "model": eng.model_name,
            "template": config.template,
        }

    @app.get("/v1/models", dependencies=[Depends(require_key)])
    async def list_models(eng: Engine = Depends(engine)) -> dict[str, Any]:
        models = await eng.list_models()
        now = int(time.time())
        return {
            "object": "list",
            "data": [
                {"id": m.id, "object": "model", "created": now, "owned_by": m.backend}
                for m in models
            ],
        }

    @app.post("/v1/chat/completions", dependencies=[Depends(require_key)])
    async def chat_completions(body: ChatRequest, eng: Engine = Depends(engine)):
        messages = [Message.parse(m.model_dump()) for m in body.messages]
        params = body.sampling(config.sampling)
        # Absent -> configured system prompt; "" -> explicitly none.
        system = config.system_prompt if body.system_prompt is None else (body.system_prompt or None)
        created = int(time.time())
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        model_name = body.model or eng.model_name

        if not body.stream:
            text, finish, usage = await eng.chat(
                messages, params=params, template=body.template, system_prompt=system
            )
            return {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": finish,
                    }
                ],
                "usage": _usage_dict(usage),
            }

        async def events() -> AsyncIterator[str]:
            def frame(delta: dict[str, Any], finish: str | None) -> str:
                payload = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(payload)}\n\n"

            yield frame({"role": "assistant", "content": ""}, None)
            try:
                async for chunk in eng.stream_chat(
                    messages, params=params, template=body.template, system_prompt=system
                ):
                    if chunk.text:
                        yield frame({"content": chunk.text}, None)
                    if chunk.finish_reason:
                        yield frame({}, chunk.finish_reason)
            except BackendError as exc:
                yield f"data: {json.dumps({'error': {'message': str(exc)}})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/completions", dependencies=[Depends(require_key)])
    async def completions(body: CompletionRequest, eng: Engine = Depends(engine)):
        """Raw completion: the prompt reaches the model exactly as sent."""
        params = body.sampling(config.sampling)
        created = int(time.time())
        cid = f"cmpl-{uuid.uuid4().hex[:24]}"
        model_name = body.model or eng.model_name

        if not body.stream:
            parts, finish = [], "stop"
            async for chunk in eng.stream_text(body.prompt, params):
                parts.append(chunk.text)
                if chunk.finish_reason:
                    finish = chunk.finish_reason
            text = "".join(parts)
            return {
                "id": cid,
                "object": "text_completion",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "text": text, "finish_reason": finish}],
                "usage": _usage_dict(
                    Usage(estimate_tokens(body.prompt), estimate_tokens(text))
                ),
            }

        async def events() -> AsyncIterator[str]:
            try:
                async for chunk in eng.stream_text(body.prompt, params):
                    payload = {
                        "id": cid,
                        "object": "text_completion",
                        "created": created,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "text": chunk.text,
                                "finish_reason": chunk.finish_reason,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
            except BackendError as exc:
                yield f"data: {json.dumps({'error': {'message': str(exc)}})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return app
