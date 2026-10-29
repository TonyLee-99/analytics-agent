"""Benchmark loaders. Each item becomes a uniform `Task` the agent and evaluator understand."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

import sqlglot

from analytics_agent.config import BIRD_BASELINE_PROMPTS, BIRD_DB_ROOT, BIRD_QUESTIONS, EVAL_SETS, OLIST_DB
from analytics_agent.guardrails import referenced_tables


@dataclass
class Task:
    id: str
    db_id: str
    question: str
    gold_sql: str
    evidence: str = ""
    difficulty: str = ""
    dialect: str = "sqlite"
    db_path: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def gold_tables(self) -> list[str]:
        try:
            tree = sqlglot.parse_one(self.gold_sql, read=self.dialect)
        except Exception:  # noqa: BLE001
            return []
        return [t.split(".")[-1].lower() for t in referenced_tables(tree)]


def bird_db_path(db_id: str) -> str:
    return str(BIRD_DB_ROOT / db_id / f"{db_id}.sqlite")


@lru_cache(maxsize=1)
def _baseline_prompts() -> dict[int, str]:
    out = {}
    if not BIRD_BASELINE_PROMPTS.exists():  # fresh clone without `make data`: prompts only needed for live runs
        return out
    with open(BIRD_BASELINE_PROMPTS) as f:
        for line in f:
            r = json.loads(line)
            out[r["question_id"]] = r["prompt"]
    return out


def load_bird(ids: list[int] | None = None) -> list[Task]:
    tasks = []
    # the 2026 question file is vendored (CC BY-SA 4.0) so analyses run on a fresh clone without `make data`
    qpath = BIRD_QUESTIONS if BIRD_QUESTIONS.exists() else EVAL_SETS / "provenance" / "mini_dev_sqlite.jsonl"
    with open(qpath) as f:
        for line in f:
            r = json.loads(line)
            if ids is not None and r["question_id"] not in ids:
                continue
            tasks.append(Task(
                id=f"bird_{r['question_id']:03d}", db_id=r["db_id"], question=r["question"], gold_sql=r["SQL"],
                evidence=r.get("evidence", ""), difficulty=r["difficulty"], dialect="sqlite",
                db_path=bird_db_path(r["db_id"]),
                meta={"question_id": r["question_id"], "official_prompt": _baseline_prompts().get(r["question_id"])},
            ))
    return tasks


def load_olist_golden() -> list[Task]:
    tasks = []
    with open(EVAL_SETS / "olist_golden.jsonl") as f:
        for line in f:
            r = json.loads(line)
            tasks.append(Task(
                id=r["id"], db_id="olist", question=r["question"], gold_sql=r["gold_sql"], evidence="",
                difficulty=r.get("difficulty", ""), dialect="duckdb", db_path=str(OLIST_DB),
                meta={k: r.get(k) for k in ("category", "uses_metric", "paraphrase_group", "variants")},
            ))
    return tasks


def load_jsonl(name: str) -> list[dict]:
    with open(EVAL_SETS / name) as f:
        return [json.loads(line) for line in f if line.strip()]


def subset_ids(name: str) -> list[int]:
    """Frozen stratified subsets (written once by scripts/make_subsets.py)."""
    return json.loads((EVAL_SETS / f"{name}.json").read_text())["question_ids"]
