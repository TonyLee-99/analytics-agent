"""One command to run a benchmark config and score it.

    uv run python -m analytics_agent.eval.run --suite bird --config d_selfcorrect --model haiku [--subset sub200]

Writes results/runs/<suite>__<config>__<model>[__<subset>].jsonl (one line per question, resumable)
and prints EX by difficulty plus latency/token/cost stats. Already-finished questions are skipped,
and every LLM response is cached on disk, so re-running is free.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from analytics_agent.agent import Agent
from analytics_agent.config import EVAL_SETS, RUNS, TRACES
from analytics_agent.eval.configs import get_config
from analytics_agent.eval.datasets import Task, load_bird, load_olist_golden, subset_ids
from analytics_agent.eval.metrics import score
from analytics_agent.eval.report import summarize_run
from analytics_agent.llm import make_client
from analytics_agent.retrieval import Example, ExampleStore

_lock = threading.Lock()


def bird_examples() -> ExampleStore:
    exs = []
    with open(EVAL_SETS / "bird_fewshot_pool.jsonl") as f:
        for line in f:
            r = json.loads(line)
            exs.append(Example(r["question"], r["SQL"], r["db_id"], r.get("evidence", ""), f"bird_dev_{r['question_id']}"))
    return ExampleStore(exs)


def olist_examples() -> ExampleStore:
    p = EVAL_SETS / "olist_examples.jsonl"
    exs = []
    if p.exists():
        for line in open(p):
            r = json.loads(line)
            exs.append(Example(r["question"], r["sql"], "olist", "", r["id"]))
    return ExampleStore(exs)


def run_path(suite: str, config: str, model: str, subset: str | None) -> Path:
    name = f"{suite}__{config}__{model.replace(':', '-')}" + (f"__{subset}" if subset else "")
    return RUNS / f"{name}.jsonl"


def load_tasks(suite: str, subset: str | None, limit: int | None = None) -> list[Task]:
    if suite == "bird":
        tasks = load_bird(subset_ids(subset) if subset else None)
    elif suite == "olist":
        tasks = load_olist_golden()
    else:
        raise ValueError(suite)
    return tasks[:limit] if limit else tasks


def run_one(agent: Agent, task: Task) -> dict:
    t0 = time.time()
    ans = agent.ask(task.question, task.db_id, task.evidence, official_prompt=task.meta.get("official_prompt"),
                    task_id=task.id)
    ex, pred_err = score(task, ans.sql) if ans.status != "clarify" else (0, "clarify")
    main_sql = getattr(ans, "sql_main", ans.sql)
    ex_main = ex if main_sql == ans.sql else (score(task, main_sql)[0] if main_sql else 0)
    return {
        "id": task.id, "db_id": task.db_id, "difficulty": task.difficulty, "question": task.question,
        "evidence": task.evidence, "gold_sql": task.gold_sql, "pred_sql": ans.sql, "main_sql": main_sql,
        "status": ans.status, "ex": ex, "ex_main": ex_main, "pred_error": pred_err, "clarification": ans.clarification,
        "signals": {k: v for k, v in ans.signals.items() if k != "usage"}, "usage": ans.signals.get("usage", {}),
        "meta": {k: v for k, v in task.meta.items() if k not in ("official_prompt", "variants")},
        "wall_s": round(time.time() - t0, 2),
    }


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="bird", choices=["bird", "olist"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--subset", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--n-samples", type=int, default=None)
    ap.add_argument("--shuffle", action="store_true", help="random task order (fixed seed) so a partial run is a random subsample")
    args = ap.parse_args(argv)

    overrides = {}
    if args.n_samples:
        overrides["n_samples"] = args.n_samples
    cfg = get_config(args.config, **overrides)
    llm = make_client(args.model)
    examples = bird_examples() if args.suite == "bird" else olist_examples()
    out = run_path(args.suite, cfg.name + (f"_n{args.n_samples}" if args.n_samples else ""), args.model, args.subset)
    trace_path = TRACES / (out.stem + ".jsonl")
    agent = Agent(llm, cfg, examples, trace_path=trace_path)
    tasks = load_tasks(args.suite, args.subset, args.limit)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["id"] for line in open(out) if line.strip()}
    todo = [t for t in tasks if t.id not in done]
    if args.shuffle:
        import random

        random.Random(7).shuffle(todo)
    print(f"[{out.name}] {len(tasks)} tasks, {len(done)} done, {len(todo)} to run")
    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_one, agent, t): t for t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            t = futs[f]
            try:
                rec = f.result()
            except Exception as e:  # noqa: BLE001 - keep the run going; record the failure
                failures += 1
                print(f"  !! {t.id}: {type(e).__name__}: {str(e)[:200]}")
                if "budget" in str(e).lower():
                    break
                continue
            with _lock, open(out, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} done")
    ids = {t.id for t in tasks}
    recs = [json.loads(line) for line in open(out) if line.strip()]
    recs = [r for r in recs if r["id"] in ids]
    summary = summarize_run(recs)
    print(json.dumps(summary, indent=2))
    if failures:
        print(f"{failures} tasks raised errors (not written; re-run to retry)")
    return summary


if __name__ == "__main__":
    main()
