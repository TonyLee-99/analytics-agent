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


def _simulate_d(M, n: int, shift: float, B: int, rng) -> list[int]:
    """Net loss D = lost - gained between two recordings of n questions. Questions are resampled from
    the measured 190 x 4 independent-draw matrix; `shift` additionally turns each correct answer of the
    candidate wrong with that probability (a regression of about shift x EX)."""
    import numpy as np

    out = []
    for _ in range(B):
        q = rng.integers(0, len(M), n)
        j, k = rng.choice(M.shape[1], 2, replace=False)
        a, b = M[q, j], M[q, k].copy()
        if shift:
            b = np.where((b == 1) & (rng.random(n) < shift), 0, b)
        out.append(int(((a == 1) & (b == 0)).sum() - ((a == 0) & (b == 1)).sum()))
    return out


def size_gate(alpha: float = 0.05, B: int = 20000) -> dict:
    """Paired gate: fail when the candidate recording loses at least k more questions than it gains
    against the baseline recording. k is the smallest value whose false-alarm rate (unchanged prompt,
    fresh sampling) is <= alpha, estimated from real redraw data (results/noise_floor_matrix.json)."""
    import numpy as np

    from analytics_agent.config import RESULTS

    M = np.array(json.loads((RESULTS / "noise_floor_matrix.json").read_text())["ex"])[:, 1:]
    rng = np.random.default_rng(0)
    base = {o["id"]: o["ex"] for o in run_gate("current")["outcomes"]}
    n = len(base)
    d0 = np.array(_simulate_d(M, n, 0.0, B, rng))
    k = next(k for k in range(1, n) if (d0 >= k).mean() <= alpha)
    power = {f"{round(100 * s * 0.73)}pp": round(float((np.array(_simulate_d(M, n, s, B // 4, rng)) >= k).mean()), 3)
             for s in (0.066, 0.13, 0.22)}
    deg = {o["id"]: o["ex"] for o in run_gate("degraded")["outcomes"]}
    lost = sum(1 for i in base if base[i] == 1 and deg[i] == 0)
    gained = sum(1 for i in base if base[i] == 0 and deg[i] == 1)
    gate = {
        "n": n, "rule": "fail if (lost - gained) >= k versus the baseline recording", "k": int(k),
        "false_alarm_rate": round(float((d0 >= k).mean()), 3),
        "power_by_true_regression": power,
        "old_absolute_threshold_false_fail_rate": round(float((d0 >= 3).mean()), 3),
        "baseline_ex": round(sum(base.values()) / n, 4), "baseline_outcomes": base,
        "observed_degraded": {"ex": round(sum(deg.values()) / n, 4), "lost": lost, "gained": gained,
                              "net": lost - gained, "flagged": lost - gained >= k},
        "method": "simulated from 190 BIRD questions x 4 independent Haiku draws of the same prompt",
        "degraded_change": "column descriptions/sample values and evidence hints removed from the prompt",
    }
    (REG / "gate.json").write_text(json.dumps(gate, indent=2))
    return gate


def gate_decision(outcomes: list[dict], gate: dict) -> dict:
    base = gate["baseline_outcomes"]
    cur = {o["id"]: o["ex"] for o in outcomes}
    lost = sum(1 for i in base if base[i] == 1 and cur.get(i, 0) == 0)
    gained = sum(1 for i in base if base[i] == 0 and cur.get(i, 0) == 1)
    return {"lost": lost, "gained": gained, "net": lost - gained, "fail": lost - gained >= gate["k"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["build", "record", "check", "size"])
    ap.add_argument("--variant", default="current", choices=sorted(VARIANTS))
    args = ap.parse_args()
    if args.what == "build":
        build_frozen_set()
        return
    if args.what == "size":
        g = size_gate()
        print(json.dumps({k: v for k, v in g.items() if k != "baseline_outcomes"}, indent=1))
        return
    res = run_gate(args.variant, record=args.what == "record")
    dec = gate_decision(res["outcomes"], json.loads((REG / "gate.json").read_text()))
    print(json.dumps({k: v for k, v in res.items() if k != "outcomes"}), dec, "FAIL" if dec["fail"] else "PASS")


if __name__ == "__main__":
    main()
