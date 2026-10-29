"""Shared service layer for the HTTP API, the chat UI and the MCP server."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from analytics_agent.agent import Agent, AgentConfig, answer_confidence
from analytics_agent.catalog import get_catalog
from analytics_agent.config import TRACES
from analytics_agent.llm import make_client

MAX_ROWS_RETURNED = 200


@lru_cache(maxsize=8)
def get_agent(db_id: str) -> Agent:
    model = os.environ.get("AA_MODEL", "haiku")
    if db_id == "olist":  # business warehouse: governed metrics first, ask when ambiguous
        cfg = AgentConfig(name="api_olist", schema_mode="full", max_corrections=2, semantic_mode="semantic",
                          allow_clarify=True, summarize=model != "mock")
    else:
        cfg = AgentConfig(name="api", schema_mode="retrieved", top_k=6, max_corrections=2, summarize=model != "mock")
    return Agent(make_client(model), cfg, trace_path=TRACES / "serving.jsonl")


def ask(question: str, db_id: str = "olist") -> dict[str, Any]:
    ans = get_agent(db_id).ask(question, db_id)
    conf = answer_confidence(ans.signals) if ans.status == "answered" else None
    return {
        "status": ans.status,
        "question": question,
        "db_id": db_id,
        "sql": ans.sql,
        "columns": ans.columns,
        "rows": [list(map(_jsonable, r)) for r in ans.rows[:MAX_ROWS_RETURNED]],
        "row_count": len(ans.rows),
        "summary": ans.summary,
        "clarification": ans.clarification,
        "confidence": conf,
        "needs_review": conf is not None and conf < 0.6,
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
