"""Configuration: TOML file, overridable by ROFLO_* environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .types import SamplingParams

DEFAULT_PATHS = (Path("roflo.toml"), Path.home() / ".config" / "roflo" / "roflo.toml")


@dataclass(slots=True)
class BackendConfig:
    kind: str = "ollama"
    model: str = ""
    base_url: str = ""
    timeout: float = 600.0
    api_key: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=list)
    api_key: str = ""


@dataclass(slots=True)
class Config:
    backend: BackendConfig = field(default_factory=BackendConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    sampling: SamplingParams = field(default_factory=SamplingParams)
    template: str = "chatml"
    # No default. Nothing is prepended to your conversation unless you put it here.
    system_prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce(dc_type: type, data: dict[str, Any], where: str) -> Any:
    valid = {f.name: f.type for f in fields(dc_type)}
    unknown = set(data) - set(valid)
    if unknown:
        raise ValueError(f"unknown key(s) in [{where}]: {', '.join(sorted(unknown))}")
    return dc_type(**data)


def _env_overrides(cfg: Config) -> Config:
    """Apply ROFLO_* overrides.

    ``ROFLO_BACKEND``/``ROFLO_MODEL``/``ROFLO_BASE_URL``/``ROFLO_API_KEY``,
    ``ROFLO_TEMPLATE``, ``ROFLO_SYSTEM_PROMPT``, ``ROFLO_HOST``, ``ROFLO_PORT``,
    ``ROFLO_SERVER_API_KEY``, and ``ROFLO_TEMPERATURE``/``ROFLO_MAX_TOKENS``.
    """
    env = os.environ
    if v := env.get("ROFLO_BACKEND"):
        cfg.backend.kind = v
    if v := env.get("ROFLO_MODEL"):
        cfg.backend.model = v
    if v := env.get("ROFLO_BASE_URL"):
        cfg.backend.base_url = v
    if v := env.get("ROFLO_API_KEY"):
        cfg.backend.api_key = v
    if v := env.get("ROFLO_TEMPLATE"):
        cfg.template = v
    if (v := env.get("ROFLO_SYSTEM_PROMPT")) is not None:
        cfg.system_prompt = v or None
    if v := env.get("ROFLO_HOST"):
        cfg.server.host = v
    if v := env.get("ROFLO_PORT"):
        cfg.server.port = int(v)
    if v := env.get("ROFLO_SERVER_API_KEY"):
        cfg.server.api_key = v
    if v := env.get("ROFLO_TEMPERATURE"):
        cfg.sampling.temperature = float(v)
    if v := env.get("ROFLO_MAX_TOKENS"):
        cfg.sampling.max_tokens = int(v)
    return cfg


def load(path: str | Path | None = None) -> Config:
    """Load config from ``path``, else the first default path that exists."""
    candidates = [Path(path)] if path else list(DEFAULT_PATHS)
    raw: dict[str, Any] = {}
    for candidate in candidates:
        if candidate.is_file():
            raw = tomllib.loads(candidate.read_text())
            break
    else:
        if path:
            raise FileNotFoundError(f"config file not found: {path}")

    unknown = set(raw) - {"backend", "server", "sampling", "template", "system_prompt"}
    if unknown:
        raise ValueError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")

    cfg = Config(
        backend=_coerce(BackendConfig, raw.get("backend", {}), "backend"),
        server=_coerce(ServerConfig, raw.get("server", {}), "server"),
        sampling=_coerce(SamplingParams, raw.get("sampling", {}), "sampling"),
        template=raw.get("template", "chatml"),
        system_prompt=raw.get("system_prompt") or None,
    )
    return _env_overrides(cfg)
