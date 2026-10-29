"""Shared service layer for the HTTP API, the chat UI and the MCP server."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from analytics_agent.agent import Agent, AgentConfig, HybridAgent
from analytics_agent.catalog import get_catalog
from analytics_agent.config import TRACES
from analytics_agent.llm import make_client

MAX_ROWS_RETURNED = 200


# Serving uses the confidence that was evaluated: agreement of N sampled SQLs on their *execution
# results* (results/abstention.json, BIRD sub200, Haiku, N=5):
#   all 5 agree      -> 69.5% of questions, EX 86.3%   => "answered"
#   4 of 5 or fewer  -> EX 57% or lower                 => "needs_review" (or escalate, if configured)
# With N=1 there is no evaluated confidence signal (single-run signals scored AUROC 0.52), so the API
# returns confidence=None and says so, instead of inventing a number.
DEFAULT_SAMPLES = int(os.environ.get("AA_SERVE_SAMPLES", "5"))
ANSWER_AT_AGREEMENT = 1.0


@lru_cache(maxsize=16)
def get_agent(db_id: str, n_samples: int = DEFAULT_SAMPLES, model: str | None = None) -> Agent | HybridAgent:
    model = model or os.environ.get("AA_MODEL", "haiku")
    llm = make_client(model)
    summarize = model != "mock"
    if db_id == "olist":  # business warehouse: governed metrics first (hybrid router), ask when ambiguous
        common = dict(schema_mode="full", max_corrections=2, allow_clarify=True, summarize=summarize,
                      n_samples=n_samples)
        sem = Agent(llm, AgentConfig(name="api_olist_semantic", semantic_mode="semantic", **common),
                    trace_path=TRACES / "serving.jsonl")
        raw = Agent(llm, AgentConfig(name="api_olist_glossary", semantic_mode="glossary", **common),
                    trace_path=TRACES / "serving.jsonl")
        return HybridAgent(sem, raw)
    cfg = AgentConfig(name="api", schema_mode="retrieved", top_k=6, max_corrections=2, summarize=summarize,
                      n_samples=n_samples)
    return Agent(llm, cfg, trace_path=TRACES / "serving.jsonl")


def ask(question: str, db_id: str = "olist", n_samples: int | None = None) -> dict[str, Any]:
    n = n_samples or DEFAULT_SAMPLES
    ans = get_agent(db_id, n).ask(question, db_id)
    confidence = ans.signals.get("sc_agreement") if (n > 1 and ans.status == "answered") else None
    route = ans.status if ans.status != "answered" else (
        "needs_review" if confidence is not None and confidence < ANSWER_AT_AGREEMENT else "answered")
    escalate_to = os.environ.get("AA_ESCALATE_MODEL")
    escalated = False
    if route == "needs_review" and escalate_to:  # Haiku-first routing (results/routing.json)
        strong = get_agent(db_id, 1, escalate_to).ask(question, db_id)
        if strong.status == "answered":
            ans, route, escalated = strong, "escalated", True
    return {
        "status": ans.status,
        "route": route,
        "question": question,
        "db_id": db_id,
        "sql": ans.sql,
        "columns": ans.columns,
        "rows": [list(map(_jsonable, r)) for r in ans.rows[:MAX_ROWS_RETURNED]],
        "row_count": len(ans.rows),
        "summary": ans.summary,
        "clarification": ans.clarification,
        "confidence": confidence,
        "confidence_method": f"self-consistency agreement over {n} samples" if n > 1 else
        "none (single sample: no evaluated confidence signal)",
        "needs_review": route == "needs_review" or (route == "answered" and confidence is None),
        "escalated": escalated,
        "error": ans.error,
        "trace_id": ans.trace.get("trace_id"),
        "usage": ans.signals.get("usage", {}),
        "used_governed_metrics": bool(ans.signals.get("used_semantic")),
    }


def list_tables(db_id: str = "olist") -> list[dict[str, Any]]:
    cat = get_catalog(db_id)
    return [{"table": t.name, "description": t.description, "columns": [c.name for c in t.columns],
             "pii_columns": [c.name for c in t.columns if c.pii]} for t in cat.tables]


def describe_table(table: str, db_id: str = "olist") -> str:
    cat = get_catalog(db_id)
    if cat.table(table) is None:
        raise ValueError(f"unknown table {table!r}; known: {cat.table_names}")
    return cat.render([table])


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float | str):
        return v
    return str(v)
