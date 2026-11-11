"""LLM backends for v2 behind v1's LLMClient interface.

- claude-cli (default): `claude -p` on the owner's subscription, via v1's ClaudeCLIClient
- anthropic: Messages API over HTTP, used only with AA_LLM_BACKEND=anthropic and ANTHROPIC_API_KEY set
- replay: strict cache only; any miss raises (what `make check` and CI use: zero model calls)

Every live response is cached under cache/v2_llm (key = hash of backend, model, system, prompt, sample,
pipeline version) and every live call is appended to results/v2/llm_ledger.jsonl with its cost.
"""

from __future__ import annotations

import os
import time

import httpx

from analytics_agent.copilot.settings import V2_CACHE, V2_RESULTS
from analytics_agent.llm.base import LLMError, LLMResponse
from analytics_agent.llm.cache import DiskCache, ReplayMiss, _base_key, versioned
from analytics_agent.llm.claude_cli import ClaudeCLIClient
from analytics_agent.llm.ledger import Ledger

V2_PIPELINE = "copilot-v2.1"
LLM_CACHE_DIR = V2_CACHE / "v2_llm"
LEDGER_PATH = V2_RESULTS / "llm_ledger.jsonl"
ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# list prices, USD per million tokens (input, output); used for the anthropic backend's cost field
PRICES = {"claude-haiku-5-5": (1.0, 5.0), "claude-sonnet-5-5": (3.0, 15.0)}


def v2_key(backend: str, model: str, system: str, prompt: str, sample: int) -> str:
    # the CLI and replay backends share keys, so a replay serves exactly what the CLI recorded
    b = "claude_cli" if backend in ("claude_cli", "replay") else backend
    return versioned(_base_key(backend=b, model=model, system=system, prompt=prompt, sample=sample), V2_PIPELINE)


class V2ClaudeCLI(ClaudeCLIClient):
    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        key = v2_key(self.backend, self.model, system, prompt, sample)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse(**{**hit, "cached": True})
        if replay_mode():
            raise ReplayMiss(f"{self.model}: prompt not in v2 cache (key {key[:12]}) in replay mode")
        resp = self._invoke(system, prompt, tag)  # v1's subprocess + retry logic, cached under a v2 key
        self.cache.put(key, resp.to_dict())
        return resp


class ReplayClient:
    backend = "replay"

    def __init__(self, model: str, cache: DiskCache):
        self.model = model
        self.cache = cache

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        key = v2_key(self.backend, self.model, system, prompt, sample)
        hit = self.cache.get(key)
        if hit is None:
            raise ReplayMiss(f"{self.model}: prompt not in v2 cache (key {key[:12]})")
        return LLMResponse(**{**hit, "cached": True})


class AnthropicClient:
    backend = "anthropic"

    def __init__(self, model: str, cache: DiskCache, ledger: Ledger):
        self.model, self.cache, self.ledger = model, cache, ledger
        self.key = os.environ["ANTHROPIC_API_KEY"]

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        key = v2_key(self.backend, self.model, system, prompt, sample)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse(**{**hit, "cached": True})
        t0 = time.time()
        for attempt in range(5):
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 2048, "system": system,
                      "messages": [{"role": "user", "content": prompt}]})
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code != 200:
                raise LLMError(f"anthropic HTTP {r.status_code}: {r.text[:200]}")
            d = r.json()
            u = d.get("usage", {})
            pin, pout = PRICES.get(self.model, (0.0, 0.0))
            resp = LLMResponse(text="".join(b.get("text", "") for b in d.get("content", [])), model=self.model,
                               input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                               cost_usd=(u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout) / 1e6,
                               latency_s=round(time.time() - t0, 3))
            self.cache.put(key, resp.to_dict())
            self.ledger.record(self.backend, self.model, tag, {"input_tokens": resp.input_tokens,
                               "output_tokens": resp.output_tokens, "cost_usd": resp.cost_usd,
                               "latency_s": resp.latency_s})
            return resp
        raise LLMError("anthropic: retries exhausted")


def replay_mode() -> bool:
    return os.environ.get("AA_LLM_BACKEND") == "replay" or os.environ.get("AA_STRICT_REPLAY", "") not in ("", "0")


def make_llm(model: str = "haiku"):
    model = ALIASES.get(model, model)
    cache = DiskCache(LLM_CACHE_DIR)
    if replay_mode():
        return ReplayClient(model, cache)
    ledger = Ledger(LEDGER_PATH)
    # opt-in only: a key in the environment is not enough (the owner's key is budgeted for other work)
    if os.environ.get("AA_LLM_BACKEND") == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient(model, cache, ledger)
    return V2ClaudeCLI(model, cache=cache, ledger=ledger, max_retries=3)
