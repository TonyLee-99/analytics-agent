"""Local open model through Ollama ("data never leaves the laptop" option). Cost is $0 marginal."""

from __future__ import annotations

import time

import httpx

from analytics_agent.llm.base import LLMError, LLMResponse
from analytics_agent.llm.cache import DiskCache, cache_key
from analytics_agent.llm.ledger import Ledger


class OllamaClient:
    backend = "ollama"

    def __init__(self, model: str = "qwen2.5:7b", host: str = "http://localhost:11434", num_ctx: int = 16384,
                 timeout_s: float = 600.0, cache: DiskCache | None = None, ledger: Ledger | None = None):
        self.model, self.host, self.num_ctx, self.timeout_s = model, host, num_ctx, timeout_s
        self.cache = cache or DiskCache()
        self.ledger = ledger or Ledger()

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        temperature = 0.0 if sample == 0 else 0.7
        key = cache_key(backend=self.backend, model=self.model, system=system, prompt=prompt, sample=sample,
                        num_ctx=self.num_ctx)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse(**{**hit, "cached": True})
        body = {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": temperature, "num_ctx": self.num_ctx, "seed": sample},
        }
        t0 = time.time()
        try:
            r = httpx.post(f"{self.host}/api/chat", json=body, timeout=self.timeout_s)
            r.raise_for_status()
            out = r.json()
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"ollama call failed: {e}") from e
        resp = LLMResponse(text=out["message"]["content"], model=self.model,
                           input_tokens=out.get("prompt_eval_count", 0), output_tokens=out.get("eval_count", 0),
                           latency_s=round(time.time() - t0, 3))
        d = resp.to_dict()
        self.ledger.record(self.backend, self.model, tag, {k: d[k] for k in (
            "input_tokens", "output_tokens", "cost_usd", "latency_s")})
        self.cache.put(key, d)
        return resp
