"""Claude via the headless Claude Code CLI (runs on the owner's subscription; no API key on this box).

Each call strips Claude Code's own system prompt, tools, MCP servers and settings so the request is
just (our system prompt, our user prompt). The CLI's JSON output carries usage + total_cost_usd, which
we log. In production you would swap this class for an Anthropic API client with the same interface.
"""

from __future__ import annotations

import json
import random
import subprocess
import time

from analytics_agent.config import ROOT
from analytics_agent.llm.base import LLMError, LLMResponse
from analytics_agent.llm.cache import DiskCache, ReplayMiss, cache_key, strict_replay
from analytics_agent.llm.ledger import Ledger

SANDBOX = ROOT / ".llm_sandbox"  # neutral empty cwd: no CLAUDE.md / project settings get picked up


class ClaudeCLIClient:
    backend = "claude_cli"

    def __init__(self, model: str = "claude-haiku-5-5", timeout_s: float = 240.0, max_retries: int = 4,
                 cache: DiskCache | None = None, ledger: Ledger | None = None):
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cache = cache or DiskCache()
        self.ledger = ledger or Ledger()

    def _cmd(self, system: str, prompt: str) -> list[str]:
        return [
            "claude", "-p", prompt,
            "--model", self.model,
            "--output-format", "json",
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            "--disable-slash-commands",
        ]

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        # Temperature is not exposed by the CLI; the API default (1.0) applies, so extra samples differ.
        key = cache_key(backend=self.backend, model=self.model, system=system, prompt=prompt, sample=sample)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse(**{**hit, "cached": True})
        if strict_replay():
            raise ReplayMiss(f"{self.model}: prompt not in cache (key {key[:12]}) under AA_STRICT_REPLAY")
        self.ledger.reserve(self.backend)
        SANDBOX.mkdir(exist_ok=True)
        last_err = ""
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            try:
                proc = subprocess.run(self._cmd(system, prompt), cwd=SANDBOX, capture_output=True, text=True,
                                      timeout=self.timeout_s)
                out = json.loads(proc.stdout) if proc.stdout.strip() else {}
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                out, last_err = {}, f"{type(e).__name__}: {e}"
            latency = time.time() - t0
            if out and not out.get("is_error") and out.get("subtype") == "success":
                u = out.get("usage", {})
                resp = LLMResponse(
                    text=out.get("result", ""),
                    model=self.model,
                    input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0),
                    cache_read_tokens=u.get("cache_read_input_tokens", 0),
                    cache_write_tokens=u.get("cache_creation_input_tokens", 0),
                    cost_usd=float(out.get("total_cost_usd", 0.0)),
                    latency_s=round(latency, 3),
                )
                d = resp.to_dict()
                self.ledger.record(self.backend, self.model, tag, {k: d[k] for k in (
                    "input_tokens", "output_tokens", "cost_usd", "latency_s")} | {"attempts": attempt + 1})
                self.cache.put(key, d)
                return resp
            if out:
                last_err = f"is_error={out.get('is_error')} status={out.get('api_error_status')} " \
                           f"result={str(out.get('result'))[:200]}"
            elif not last_err:
                last_err = f"exit={proc.returncode} stderr={proc.stderr[:300]}"
            # exponential backoff with jitter (rate limits / overload)
            time.sleep(min(120, (2 ** attempt) * 5) + random.random() * 3)
        raise LLMError(f"claude CLI failed after {self.max_retries + 1} attempts: {last_err}")
