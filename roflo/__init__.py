"""roflo -- self-hosted LLM inference with a pluggable backend."""

from .config import Config, load
from .prompt import TEMPLATES, render
from .types import BackendError, Chunk, Message, ModelInfo, SamplingParams

__version__ = "0.1.0"
__all__ = [
    "BackendError",
    "Chunk",
    "Config",
    "Message",
    "ModelInfo",
    "SamplingParams",
    "TEMPLATES",
    "load",
    "render",
]
