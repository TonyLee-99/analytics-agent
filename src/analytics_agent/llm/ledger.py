"""Append-only ledger of every *real* (uncached) LLM call, plus a hard call budget.

The budget is enforced mechanically: once the ledger holds AA_MAX_CLAUDE_CALLS Claude calls, further
Claude calls raise BudgetExceeded instead of spending money. `python -m analytics_agent.llm.ledger`
prints the totals used in the final report."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from analytics_agent.config import RESULTS

LEDGER = RESULTS / "llm_ledger.jsonl"
MAX_CLAUDE_CALLS = int(os.environ.get("AA_MAX_CLAUDE_CALLS", "4000"))
_lock = threading.Lock()


def _read(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Ledger:
    def __init__(self, path: Path = LEDGER):
        self.path = path
        self._claude_calls = sum(1 for r in _read(path) if r["backend"] == "claude_cli")

    def reserve(self, backend: str) -> None:
        with _lock:
            if backend == "claude_cli":
                if self._claude_calls >= MAX_CLAUDE_CALLS:
                    raise_budget(self._claude_calls)
                self._claude_calls += 1

    def record(self, backend: str, model: str, tag: str, resp: dict) -> None:
        row = {"ts": time.time(), "backend": backend, "model": model, "tag": tag, **resp}
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(row) + "\n")


def raise_budget(n: int) -> None:
    from analytics_agent.llm.base import BudgetExceeded

    raise BudgetExceeded(f"Claude call budget exhausted ({n} >= {MAX_CLAUDE_CALLS}); see {LEDGER}")


def summary(path: Path = LEDGER) -> dict:
    rows = _read(path)
    calls: Counter = Counter()
    cost: dict = defaultdict(float)
    by_tag: dict = defaultdict(lambda: [0, 0.0])
    for r in rows:
        k = f"{r['backend']}:{r['model']}"
        calls[k] += 1
        cost[k] += r.get("cost_usd", 0.0)
        tag = r.get("tag", "").split(":")[0]
        by_tag[f"{k} | {tag}"][0] += 1
        by_tag[f"{k} | {tag}"][1] += r.get("cost_usd", 0.0)
    claude = [r for r in rows if r["backend"] == "claude_cli"]
    return {
        "claude_calls_total": len(claude),
        "claude_cost_usd_total": round(sum(r.get("cost_usd", 0.0) for r in claude), 4),
        "calls_by_model": dict(calls),
        "cost_by_model": {k: round(v, 4) for k, v in cost.items()},
        "by_tag": {k: {"calls": v[0], "cost_usd": round(v[1], 4)} for k, v in sorted(by_tag.items())},
    }


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))
