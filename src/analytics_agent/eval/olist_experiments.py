"""Olist business-set experiments.

semantic : controlled 3-arm experiment on the 50-question golden set
           raw      - agent writes SQL from schema + table/column docs
           glossary - same, plus the governed metric definitions pasted into the prompt as text
           semantic - agent must pick governed metrics/dimensions (JSON); a compiler writes the SQL;
                      raw SQL allowed only as a fallback for questions the layer cannot express
           Glossary vs semantic separates "knowing the definition" from "not re-implementing it".
ambiguity: clarification behaviour on 20 curated ambiguous questions + 20 clear controls.

    uv run python -m analytics_agent.eval.olist_experiments semantic [--model haiku]
    uv run python -m analytics_agent.eval.olist_experiments ambiguity [--model haiku]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from analytics_agent.agent import Agent, result_signature
from analytics_agent.config import RESULTS, TRACES
from analytics_agent.eval import run as R
from analytics_agent.eval.configs import get_config
from analytics_agent.eval.datasets import load_jsonl, load_olist_golden
from analytics_agent.eval.metrics import business_ex
from analytics_agent.executor import execute_all
from analytics_agent.llm import make_client

ARMS = ["olist_raw", "olist_glossary", "olist_semantic"]


def _variant_rows(task) -> list[tuple[str, list]]:
    out = []
    for v in task.meta.get("variants") or []:
        rows, err = execute_all(v["sql"], "duckdb", task.db_path)
        if rows is not None:
            out.append((v["label"], rows))
    return out


def semantic(model: str) -> dict:
    tasks = {t.id: t for t in load_olist_golden()}
    variants = {tid: _variant_rows(t) for tid, t in tasks.items() if t.meta.get("uses_metric")}
    report: dict = {"model": model, "arms": {}}
    per_q: dict = defaultdict(dict)
    for arm in ARMS:
        R.main(["--suite", "olist", "--config", arm, "--model", model, "--workers", "4"])
        recs = [json.loads(x) for x in open(R.run_path("olist", arm, model, None))]
        metric_recs = [r for r in recs if tasks[r["id"]].meta.get("uses_metric")]
        other = [r for r in recs if not tasks[r["id"]].meta.get("uses_metric")]
        drift, drift_labels = 0, defaultdict(int)
        for r in metric_recs:
            label = "governed" if r["ex"] else "other_wrong"
            if not r["ex"] and r["pred_sql"]:
                rows, _ = execute_all(r["pred_sql"], "duckdb", tasks[r["id"]].db_path)
                for vlabel, vrows in variants.get(r["id"], []):
                    if rows is not None and business_ex(rows, vrows):
                        label = f"variant: {vlabel}"
                        drift += 1
                        drift_labels[vlabel] += 1
                        break
            per_q[r["id"]][arm] = {"ex": r["ex"], "definition": label,
                                   "used_semantic": r["signals"].get("used_semantic")}
        # paraphrase consistency: do all members of a group return the same result?
        groups = defaultdict(list)
        for r in recs:
            g = tasks[r["id"]].meta.get("paraphrase_group")
            if g:
                rows, _ = execute_all(r["pred_sql"], "duckdb", tasks[r["id"]].db_path) if r["pred_sql"] else (None, "")
                groups[g].append(None if rows is None else result_signature(
                    [tuple(round(v, 4) if isinstance(v, float) else v for v in row) for row in rows]))
        consistent = sum(1 for g in groups.values() if None not in g and len(set(g)) == 1)
        n_m = len(metric_recs)
        report["arms"][arm] = {
            "ex_all": round(100 * sum(r["ex"] for r in recs) / len(recs), 1),
            "ex_metric_questions": round(100 * sum(r["ex"] for r in metric_recs) / n_m, 1),
            "ex_non_metric_questions": round(100 * sum(r["ex"] for r in other) / max(1, len(other)), 1),
            "n_metric": n_m, "n_non_metric": len(other),
            "definition_drift_rate": round(drift / n_m, 3),
            "definition_drift_by_variant": dict(drift_labels),
            "paraphrase_groups_consistent": f"{consistent}/{len(groups)}",
            "answered_via_compiler": sum(1 for r in recs if r["signals"].get("used_semantic")),
            "cost_per_q": round(sum(r["usage"].get("cost_usd", 0) for r in recs) / len(recs), 5),
            "p95_latency_s": R.summarize_run(recs)["latency_s"]["p95"],
        }
    report["per_question"] = per_q
    out = RESULTS / f"semantic_experiment__{model}.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["arms"], indent=2))
    return report


def ambiguity(model: str) -> dict:
    amb = load_jsonl("olist_ambiguous.jsonl")
    ctl = load_jsonl("olist_clear_controls.jsonl")
    llm = make_client(model)
    report: dict = {"model": model, "arms": {}}
    for arm in ("olist_raw", "olist_semantic"):
        cfg = get_config(arm, allow_clarify=True, name=f"{arm}_clarify")
        agent = Agent(llm, cfg, trace_path=TRACES / f"ambiguity__{arm}__{model}.jsonl")

        def one(item, kind, agent=agent):
            ans = agent.ask(item["question"], "olist", task_id=item["id"])
            rec = {"id": item["id"], "kind": kind, "status": ans.status, "clarification": ans.clarification,
                   "sql": ans.sql}
            if kind == "ambiguous":
                rec["resolved_by_governed_layer"] = item.get("resolved_by_governed_layer", False)
                rec["matched_interpretation"] = None
                if ans.status == "answered":
                    rows, _ = execute_all(ans.sql, "duckdb", agent_db())
                    for it in item["interpretations"]:
                        irows, _ = execute_all(it["sql"], "duckdb", agent_db())
                        if rows is not None and irows is not None and business_ex(rows, irows):
                            rec["matched_interpretation"] = it["label"]
                            break
            else:
                rows, _ = execute_all(ans.sql, "duckdb", agent_db()) if ans.sql else (None, "")
                grows, _ = execute_all(item["gold_sql"], "duckdb", agent_db())
                rec["ex"] = business_ex(rows, grows) if ans.status == "answered" else 0
            return rec

        with ThreadPoolExecutor(max_workers=4) as pool:
            recs = list(pool.map(lambda it: one(it, "ambiguous"), amb)) + list(pool.map(lambda it: one(it, "control"), ctl))
        a = [r for r in recs if r["kind"] == "ambiguous"]
        c = [r for r in recs if r["kind"] == "control"]
        a_nr = [r for r in a if not r["resolved_by_governed_layer"]]
        report["arms"][arm] = {
            "clarify_rate_ambiguous": round(sum(r["status"] == "clarify" for r in a) / len(a), 3),
            "clarify_rate_ambiguous_excluding_governed_resolved": round(
                sum(r["status"] == "clarify" for r in a_nr) / max(1, len(a_nr)), 3),
            "false_clarify_rate_controls": round(sum(r["status"] == "clarify" for r in c) / len(c), 3),
            "controls_ex": round(sum(r.get("ex", 0) for r in c) / len(c), 3),
            "ambiguous_answered_without_asking": sum(r["status"] == "answered" for r in a),
            "detail": recs,
        }
        print(arm, {k: v for k, v in report["arms"][arm].items() if k != "detail"})
    out = RESULTS / f"ambiguity__{model}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    return report


def agent_db() -> str:
    from analytics_agent.config import OLIST_DB

    return str(OLIST_DB)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["semantic", "ambiguity"])
    ap.add_argument("--model", default="haiku")
    args = ap.parse_args()
    semantic(args.model) if args.what == "semantic" else ambiguity(args.model)


if __name__ == "__main__":
    main()
