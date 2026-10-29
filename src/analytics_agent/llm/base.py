"""Backend-agnostic LLM interface. Every backend returns the same LLMResponse, so the agent never
knows (or cares) whether it is talking to the Claude CLI, a local Ollama model, an HTTP API, or a mock."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    cached: bool = False  # True when served from the on-disk response cache (no new call made)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


class LLMClient(Protocol):
    """Anything with .model and .complete(system, prompt, sample=0) -> LLMResponse."""

    model: str

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse: ...
