"""Backend registry.

Backends are resolved lazily so that importing :mod:`roflo` never pulls in
torch or transformers unless the local backend is actually selected.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import BackendConfig
from ..types import BackendError
from .base import Backend, HTTPBackend

__all__ = ["Backend", "HTTPBackend", "BACKENDS", "load_backend", "available"]


def _ollama() -> type[Backend]:
    from .ollama import OllamaBackend

    return OllamaBackend


def _llamacpp() -> type[Backend]:
    from .llamacpp import LlamaCppBackend

    return LlamaCppBackend


def _vllm() -> type[Backend]:
    from .vllm import VLLMBackend

    return VLLMBackend


def _transformers() -> type[Backend]:
    from .transformers_ import TransformersBackend

    return TransformersBackend


def _echo() -> type[Backend]:
    from .echo import EchoBackend

    return EchoBackend


BACKENDS: dict[str, Callable[[], type[Backend]]] = {
    "ollama": _ollama,
    "llamacpp": _llamacpp,
    "vllm": _vllm,
    "transformers": _transformers,
    "echo": _echo,
}


def available() -> list[str]:
    return sorted(BACKENDS)


def load_backend(config: BackendConfig) -> Backend:
    """Instantiate the backend named by ``config.kind``."""
    try:
        resolve = BACKENDS[config.kind]
    except KeyError:
        raise BackendError(
            f"unknown backend {config.kind!r}; available: {', '.join(available())}"
        ) from None
    return resolve()(config)
