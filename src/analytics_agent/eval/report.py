"""Summaries of run files: EX by difficulty with 95% Wilson intervals, latency p50/p95, tokens, $."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from analytics_agent.eval.metrics import percentile, wilson


def summarize_run(recs: list[dict]) -> dict:
    by = defaultdict(list)
    for r in recs:
        by[r.get("difficulty") or "all"].append(r["ex"])
        if r.get("difficulty"):
            by["total"].append(r["ex"])
    ex = {}
    for k, v in by.items():
        lo, hi = wilson(sum(v), len(v))
        ex[k] = {"n": len(v), "ex": round(100 * sum(v) / len(v), 2), "ci95": [round(100 * lo, 1), round(100 * hi, 1)]}
    lat = [r["usage"].get("latency_s", 0.0) for r in recs]
    cost = [r["usage"].get("cost_usd", 0.0) for r in recs]
    tin = [r["usage"].get("input_tokens", 0) for r in recs]
    tout = [r["usage"].get("output_tokens", 0) for r in recs]
    calls = [r["usage"].get("llm_calls", 0) for r in recs]
    status = defaultdict(int)
    for r in recs:
        status[r["status"]] += 1
    return {
        "n": len(recs),
        "ex_by_difficulty": dict(sorted(ex.items())),
        "ex_main_sample": round(100 * sum(r.get("ex_main", r["ex"]) for r in recs) / max(1, len(recs)), 2),
        "status": dict(status),
        "latency_s": {"p50": round(percentile(lat, 0.5), 2), "p95": round(percentile(lat, 0.95), 2),
                      "mean": round(sum(lat) / max(1, len(lat)), 2)},
        "cost_usd": {"per_question_mean": round(sum(cost) / max(1, len(cost)), 5), "total": round(sum(cost), 4)},
        "tokens": {"input_mean": round(sum(tin) / max(1, len(tin))), "output_mean": round(sum(tout) / max(1, len(tout)))},
        "llm_calls_per_question": round(sum(calls) / max(1, len(calls)), 3),
    }


def load_run(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in open(path) if line.strip()]
