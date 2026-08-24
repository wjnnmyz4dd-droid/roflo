"""Command line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from . import config as config_module
from .backends import available
from .engine import Engine
from .prompt import TEMPLATES
from .types import BackendError, Message


def build_config(args: argparse.Namespace) -> config_module.Config:
    """Load the config file, then apply command-line overrides on top."""
    cfg = config_module.load(args.config)
    if args.backend:
        cfg.backend.kind = args.backend
    if args.model:
        cfg.backend.model = args.model
    if args.base_url:
        cfg.backend.base_url = args.base_url
    if args.template:
        cfg.template = args.template
    if args.system is not None:
        cfg.system_prompt = args.system or None
    if args.temperature is not None:
        cfg.sampling.temperature = args.temperature
    if args.max_tokens is not None:
        cfg.sampling.max_tokens = args.max_tokens
    if args.seed is not None:
        cfg.sampling.seed = args.seed
    return cfg


async def _stream_to_stdout(engine: Engine, messages: list[Message]) -> str:
    parts: list[str] = []
    async for chunk in engine.stream_chat(messages):
        if chunk.text:
            parts.append(chunk.text)
            sys.stdout.write(chunk.text)
            sys.stdout.flush()
    sys.stdout.write("\n")
    return "".join(parts)


async def cmd_chat(args: argparse.Namespace) -> int:
    """Interactive REPL. History is kept in-process and sent every turn."""
    cfg = build_config(args)
    engine = Engine(cfg)
    history: list[Message] = []
    banner = f"roflo · {cfg.backend.kind}:{engine.model_name} · template={cfg.template}"
    print(banner)
    print("commands: /reset  /system <text>  /nosystem  /show  /exit\n")
    try:
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line in ("/exit", "/quit"):
                return 0
            if line == "/reset":
                history.clear()
                print("(history cleared)")
                continue
            if line == "/nosystem":
                cfg.system_prompt = None
                print("(system prompt cleared)")
                continue
            if line.startswith("/system "):
                cfg.system_prompt = line[len("/system "):].strip() or None
                print(f"(system prompt set: {cfg.system_prompt!r})")
                continue
            if line == "/show":
                print(engine.render(history + [Message("user", "<next>")]))
                continue

            history.append(Message("user", line))
            print("model> ", end="", flush=True)
            try:
                reply = await _stream_to_stdout(engine, history)
            except BackendError as exc:
                print(f"\n[error] {exc}", file=sys.stderr)
                history.pop()
                continue
            history.append(Message("assistant", reply.strip()))
    finally:
        await engine.aclose()


async def cmd_complete(args: argparse.Namespace) -> int:
    """Raw completion: the prompt is sent byte-for-byte, no template applied."""
    cfg = build_config(args)
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    engine = Engine(cfg)
    try:
        async for chunk in engine.stream_text(prompt, cfg.sampling):
            if chunk.text:
                sys.stdout.write(chunk.text)
                sys.stdout.flush()
        sys.stdout.write("\n")
    except BackendError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.aclose()
    return 0


async def cmd_render(args: argparse.Namespace) -> int:
    """Print the exact string that would be sent to the model, and stop.

    Useful for confirming that nothing is added on your behalf.
    """
    cfg = build_config(args)
    engine = Engine(cfg)
    messages = [Message("user", args.prompt if args.prompt is not None else sys.stdin.read())]
    rendered = engine.render(messages)
    sys.stdout.write(rendered if args.no_repr else repr(rendered) + "\n")
    await engine.aclose()
    return 0


async def cmd_check(args: argparse.Namespace) -> int:
    cfg = build_config(args)
    engine = Engine(cfg)
    try:
        ok = await engine.health()
        target = getattr(engine.backend, "base_url", "") or "in-process"
        print(f"backend  : {cfg.backend.kind} ({target})")
        print(f"reachable: {'yes' if ok else 'no'}")
        print(f"model    : {engine.model_name}")
        print(f"template : {cfg.template}")
        print(f"system   : {cfg.system_prompt!r}")
        if ok:
            models = await engine.list_models()
            print(f"available: {', '.join(m.id for m in models) or '(none reported)'}")
        return 0 if ok else 1
    except BackendError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.aclose()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    cfg = build_config(args)
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port
    app = create_app(cfg)
    print(f"roflo serving {cfg.backend.kind}:{cfg.backend.model} on "
          f"http://{cfg.server.host}:{cfg.server.port}/v1")
    if cfg.server.host not in ("127.0.0.1", "localhost") and not cfg.server.api_key:
        print("note: bound beyond localhost with no server.api_key set -- "
              "anyone who can reach this port can use the model.", file=sys.stderr)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level=args.log_level)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    print("backends:")
    for name in available():
        print(f"  {name}")
    print("\ntemplates:")
    for name, tmpl in TEMPLATES.items():
        print(f"  {name:<8} {tmpl.description}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roflo",
        description="Self-hosted LLM inference with a pluggable backend.",
    )
    parser.add_argument("-c", "--config", help="path to roflo.toml")
    parser.add_argument("-b", "--backend", choices=available(), help="override backend")
    parser.add_argument("-m", "--model", help="override model name or path")
    parser.add_argument("--base-url", help="override backend URL")
    parser.add_argument("-t", "--template", choices=sorted(TEMPLATES), help="chat template")
    parser.add_argument("-s", "--system", help="system prompt ('' for none)")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--seed", type=int)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("chat", help="interactive chat REPL").set_defaults(func=cmd_chat)

    complete = sub.add_parser("complete", help="raw completion (no template)")
    complete.add_argument("prompt", nargs="?", help="prompt text; omit to read stdin")
    complete.set_defaults(func=cmd_complete)

    render = sub.add_parser("render", help="show the rendered prompt without generating")
    render.add_argument("prompt", nargs="?")
    render.add_argument("--no-repr", action="store_true", help="print raw, not repr()")
    render.set_defaults(func=cmd_render)

    sub.add_parser("check", help="probe the configured backend").set_defaults(func=cmd_check)

    serve = sub.add_parser("serve", help="run the OpenAI-compatible server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    sub.add_parser("info", help="list backends and templates").set_defaults(func=cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func: Any = args.func
    try:
        if asyncio.iscoroutinefunction(func):
            return asyncio.run(func(args))
        return func(args)
    except KeyboardInterrupt:
        return 130
    except (BackendError, ValueError, FileNotFoundError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
