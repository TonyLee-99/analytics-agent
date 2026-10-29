"""Deterministic fake LLM for tests and CI (no network, no cost)."""

from __future__ import annotations

from collections.abc import Callable

from analytics_agent.llm.base import LLMResponse


class MockLLM:
    backend = "mock"

    def __init__(self, responder: Callable[[str, str, int], str] | str = "```sql\nSELECT 1\n```",
                 model: str = "mock"):
        self.model = model
        self._responder = responder
        self.calls: list[tuple[str, str, int]] = []

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        self.calls.append((system, prompt, sample))
        text = self._responder(system, prompt, sample) if callable(self._responder) else self._responder
        return LLMResponse(text=text, model=self.model, input_tokens=len(prompt) // 4,
                           output_tokens=len(text) // 4, latency_s=0.0)
