"""Record/replay of LLM responses ("cassettes") for the CI regression gate.

Replay mode never touches the network: a response is looked up by the same key the real client
uses (backend, model, system prompt, user prompt, sample). If a prompt changed, the key is missing
and the gate fails with CassetteMiss -- the author must re-record with the real model, and the new
recording must still clear the accuracy threshold. That is how a prompt change gets gated."""

from __future__ import annotations

import json
from pathlib import Path

from analytics_agent.llm.base import LLMError, LLMResponse
from analytics_agent.llm.cache import cache_key


class CassetteMiss(LLMError):
    pass


class CassetteLLM:
    backend = "claude_cli"  # keys are compatible with ClaudeCLIClient's cache keys

    def __init__(self, path: Path, model: str = "claude-haiku-5-5", record_with=None):
        self.path, self.model, self.inner = Path(path), model, record_with
        self.tape: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.tape[r["key"]] = r["response"]
        self.used: set[str] = set()

    def complete(self, system: str, prompt: str, *, sample: int = 0, tag: str = "") -> LLMResponse:
        key = cache_key(backend=self.backend, model=self.model, system=system, prompt=prompt, sample=sample)
        self.used.add(key)
        if key in self.tape:
            return LLMResponse(**{**self.tape[key], "cached": True})
        if self.inner is None:
            raise CassetteMiss(f"no recorded response for this prompt (key {key[:12]}); prompts changed -> "
                               "re-record with `make record-regression`")
        r = self.inner.complete(system, prompt, sample=sample, tag=tag)
        self.tape[key] = r.to_dict()
        return r

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            for k in sorted(self.used):
                f.write(json.dumps({"key": k, "response": self.tape[k]}) + "\n")
