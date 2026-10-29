"""Frozen regression eval used as a CI gate for prompt/pipeline changes.

30 BIRD Mini-Dev questions on the two smallest databases (superhero, toxicology), whose SQLite files
are vendored in tests/regression/dbs (CC BY-SA 4.0, BIRD) so CI needs no downloads.

    uv run python -m analytics_agent.eval.regression record [--variant degraded]   # real LLM (cached)
    uv run python -m analytics_agent.eval.regression check  [--variant degraded]   # replay only
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import shutil
from pathlib import Path

from analytics_agent.agent import Agent
from analytics_agent.catalog import Column, DatabaseCatalog, Table, get_catalog, register_catalog
from analytics_agent.config import ROOT
from analytics_agent.eval.configs import get_config
from analytics_agent.eval.datasets import Task, load_jsonl
from analytics_agent.eval.metrics import bird_ex
from analytics_agent.executor import execute_all
from analytics_agent.llm.cassette import CassetteLLM
from analytics_agent.retrieval import Example, ExampleStore

REG = ROOT / "tests" / "regression"
DBS = ("superhero", "toxicology")
VARIANTS = {
    # the shipped pipeline (config d)
    "current": {},
    # a plausible "token-saving" prompt change: drop column docs/sample values and the evidence hints
    "degraded": {"describe_columns": False, "use_hints": False, "name": "d_degraded"},
}


def frozen_tasks() -> list[Task]:
    return [Task(**{k: v for k, v in r.items() if k != "gold_rows"}) for r in load_jsonl_path(REG / "frozen_set.jsonl")]


def load_jsonl_path(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def register_vendored() -> None:
    for db in DBS:
        raw = json.loads((REG / "catalogs" / f"{db}.json").read_text())
        tables = [Table(t["name"], [Column(**c) for c in t["columns"]], t["description"], [tuple(x) for x in t["fks"]])
                  for t in raw["tables"]]
        register_catalog(DatabaseCatalog(db, "sqlite", str(REG / "dbs" / f"{db}.sqlite"), tables))


def build_frozen_set(n: int = 30, seed: int = 11) -> None:
    """One-off: pick the questions, vendor the DBs + catalogs + gold results."""
    from analytics_agent.eval.datasets import load_bird

    (REG / "dbs").mkdir(parents=True, exist_ok=True)
    (REG / "catalogs").mkdir(parents=True, exist_ok=True)
    pool = [t for t in load_bird() if t.db_id in DBS]
    random.Random(seed).shuffle(pool)
    chosen = sorted(pool[:n], key=lambda t: t.id)
    for db in DBS:
        cat = get_catalog(db)
        shutil.copy(cat.path, REG / "dbs" / f"{db}.sqlite")
        (REG / "catalogs" / f"{db}.json").write_text(json.dumps(dataclasses.asdict(cat)))
    with open(REG / "frozen_set.jsonl", "w") as f:
        for t in chosen:
            d = dataclasses.asdict(t)
            d["db_path"] = ""
            d["meta"] = {"question_id": t.meta["question_id"]}
            f.write(json.dumps(d) + "\n")
    print(f"frozen set: {len(chosen)} questions")


def fewshot_store() -> ExampleStore:
    exs = [Example(r["question"], r["SQL"], r["db_id"], r.get("evidence", ""))
           for r in load_jsonl("bird_fewshot_pool.jsonl") if r["db_id"] in DBS]
    return ExampleStore(exs)


def run_gate(variant: str = "current", record: bool = False) -> dict:
    register_vendored()
    tape = REG / f"cassette_{variant}.jsonl"
    inner = None
    if record:
        from analytics_agent.llm import make_client

        inner = make_client("haiku")
    llm = CassetteLLM(tape, record_with=inner)
    cfg = get_config("d_selfcorrect", **VARIANTS[variant])
    agent = Agent(llm, cfg, fewshot_store())
    outcomes = []
    for t in frozen_tasks():
        path = str(REG / "dbs" / f"{t.db_id}.sqlite")
        ans = agent.ask(t.question, t.db_id, t.evidence, task_id=t.id)
        gold, _ = execute_all(t.gold_sql, "sqlite", path)
        pred, _ = execute_all(ans.sql, "sqlite", path) if ans.sql else (None, "")
        outcomes.append({"id": t.id, "ex": bird_ex(pred, gold)})
    if record:
        llm.save()
    ex = sum(o["ex"] for o in outcomes) / len(outcomes)
    return {"variant": variant, "n": len(outcomes), "ex": round(ex, 4), "outcomes": outcomes}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["build", "record", "check"])
    ap.add_argument("--variant", default="current", choices=sorted(VARIANTS))
    args = ap.parse_args()
    if args.what == "build":
        build_frozen_set()
        return
    res = run_gate(args.variant, record=args.what == "record")
    thr = json.loads((REG / "gate.json").read_text())["min_ex"] if (REG / "gate.json").exists() else None
    print(json.dumps({k: v for k, v in res.items() if k != "outcomes"}), "threshold", thr,
          "PASS" if thr is None or res["ex"] >= thr else "FAIL")


if __name__ == "__main__":
    main()
