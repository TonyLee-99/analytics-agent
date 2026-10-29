"""Minimal structured tracing: one trace per question, one span per agent step.

Spans carry timing plus LLM usage (tokens, $ cost, cache hit) so a trace answers "where did the
time and money go?". Traces are appended as JSON lines; `python -m analytics_agent.trace_report`
summarises them. The schema is deliberately OpenTelemetry-shaped (trace_id/span name/attributes), so
swapping in an OTel exporter is a one-file change."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_lock = threading.Lock()


@dataclass
class Span:
    name: str
    start: float
    end: float = 0.0
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return round(self.end - self.start, 4)


@dataclass
class Trace:
    question: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    spans: list[Span] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    start: float = field(default_factory=time.time)

    @contextmanager
    def span(self, name: str, **attrs: Any):
        s = Span(name, time.time(), attrs=dict(attrs))
        try:
            yield s
        finally:
            s.end = time.time()
            self.spans.append(s)

    def llm_totals(self) -> dict[str, float]:
        tot = {"llm_calls": 0, "llm_calls_uncached": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
               "llm_latency_s": 0.0}
        for s in self.spans:
            u = s.attrs.get("llm")
            if u:
                tot["llm_calls"] += 1
                tot["llm_calls_uncached"] += 0 if u.get("cached") else 1
                tot["input_tokens"] += u.get("input_tokens", 0)
                tot["output_tokens"] += u.get("output_tokens", 0)
                tot["cost_usd"] += u.get("cost_usd", 0.0)
                tot["llm_latency_s"] += u.get("latency_s", 0.0)
        tot["cost_usd"] = round(tot["cost_usd"], 6)
        tot["llm_latency_s"] = round(tot["llm_latency_s"], 3)
        return tot

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id, "question": self.question, "start": self.start, **self.attrs,
            "totals": self.llm_totals(),
            "spans": [{"name": s.name, "start": round(s.start, 4), "duration_s": s.duration_s, **s.attrs}
                      for s in self.spans],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(path, "a") as f:
            f.write(json.dumps(self.to_dict(), default=str) + "\n")
