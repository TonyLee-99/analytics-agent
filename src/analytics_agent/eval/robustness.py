"""Do the ablation conclusions survive the choice of metric and BIRD label noise? (zero LLM calls)

1. Metric sensitivity: re-execute every recorded prediction and rescore it four ways --
   official set equality, multiset (duplicates count), ordered list, and float tolerance (1e-6).
2. Label noise: the audit of 70 baseline failures found 7 acceptable answers
   (posterior Beta(8, 64) for the acceptable share of failures). We redraw, many times, which failures
   are "actually acceptable" and recompute every paired comparison. Two models:
   - independent: each (system, question) failure is acceptable independently,
   - shared: acceptability is a property of the question (one draw per question, used by every system).
   A conclusion is robust if the sign of the delta, and p < 0.05 where it held, survive in >= 95% of draws.
   Limitation: only failures were audited, so false-positive EX (wrong SQL matching gold by chance)
   is not modelled.

    uv run python -m analytics_agent.eval.robustness   -> results/robustness.json
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter

import numpy as np

from analytics_agent.config import DATA, RESULTS
from analytics_agent.eval.datasets import load_bird, subset_ids
from analytics_agent.eval.metrics import gold_rows, mcnemar_exact
from analytics_agent.eval.report import load_stem
from analytics_agent.executor import execute_all

EXEC_CACHE = DATA / "exec_cache"
FULL = ["a_zeroshot_official", "b_retrieval", "c_fewshot", "d_selfcorrect"]
PAIRS_FULL = [("a_zeroshot_official", "b_retrieval"), ("b_retrieval", "c_fewshot"), ("c_fewshot", "d_selfcorrect"),
              ("a_zeroshot_official", "d_selfcorrect")]
SUB = {"haiku_a2": "bird__a2_ourprompt_full__haiku__sub200", "haiku_b": "bird__b_retrieval__haiku",
       "haiku_d": "bird__d_selfcorrect__haiku", "haiku_sc5": "bird__e_sc5__haiku__sub200",
       "sonnet_d": "bird__d_selfcorrect__sonnet__sub200"}
PAIRS_SUB = [("haiku_a2", "haiku_b"), ("haiku_d", "haiku_sc5"), ("haiku_d", "sonnet_d")]


def _rows(task, sql: str):
    EXEC_CACHE.mkdir(parents=True, exist_ok=True)
    p = EXEC_CACHE / (hashlib.sha256(f"{task.db_id}|{sql}".encode()).hexdigest() + ".pkl")
    if p.exists():
        return pickle.loads(p.read_bytes())
    rows, _ = execute_all(sql, task.dialect, task.db_path, timeout_s=30) if sql else (None, "")
    p.write_bytes(pickle.dumps(rows))
    return rows


def _scores(pred, gold) -> dict[str, int]:
    if pred is None:
        return {"set": 0, "multiset": 0, "ordered": 0, "float_tol": 0}
    r6 = lambda rows: {tuple(round(v, 6) if isinstance(v, float) else v for v in r) for r in rows}  # noqa: E731
    return {"set": int(set(pred) == set(gold)), "multiset": int(Counter(pred) == Counter(gold)),
            "ordered": int(list(pred) == list(gold)), "float_tol": int(r6(pred) == r6(gold))}


def metric_sensitivity() -> dict:
    tasks = {t.id: t for t in load_bird()}
    runs = {s: load_stem(f"bird__{s}__haiku") for s in FULL} | {k: load_stem(v) for k, v in SUB.items()}
    scored: dict[str, dict[str, dict[str, int]]] = {}
    for name, recs in runs.items():
        scored[name] = {}
        for tid, r in recs.items():
            t = tasks[tid]
            scored[name][tid] = _scores(_rows(t, r["pred_sql"]), gold_rows(t.id, t.gold_sql, t.dialect, t.db_path))
        mism = sum(scored[name][i]["set"] != recs[i]["ex"] for i in recs)
        assert mism == 0, f"{name}: {mism} rescoring mismatches against the recorded EX"
    sub = {f"bird_{i:03d}" for i in subset_ids("sub200")}
    out = {"ex": {}, "pairs": []}
    for name in scored:
        ids = sorted(scored[name]) if name in FULL else sorted(sub)
        out["ex"][name] = {m: round(100 * np.mean([scored[name][i][m] for i in ids]), 2)
                           for m in ("set", "multiset", "ordered", "float_tol")}
    for a, b in PAIRS_FULL + PAIRS_SUB:
        ids = sorted(scored[a]) if (a in FULL and b in FULL) else sorted(sub)
        row = {"a": a, "b": b, "n": len(ids)}
        for m in ("set", "multiset", "ordered", "float_tol"):
            xa, xb = [scored[a][i][m] for i in ids], [scored[b][i][m] for i in ids]
            t = mcnemar_exact(xa, xb)
            row[m] = {"delta_pp": round(100 * (np.mean(xb) - np.mean(xa)), 2), "p": t["p_value"]}
        out["pairs"].append(row)
    return out, scored


def label_noise(scored: dict, draws: int = 2000) -> dict:
    rng = np.random.default_rng(7)
    sub = {f"bird_{i:03d}" for i in subset_ids("sub200")}
    labels = {json.loads(x)["id"]: json.loads(x) for x in open(DATA.parent / "eval_sets" / "bird_failure_labels.jsonl")}
    k_acc = sum(1 for v in labels.values() if v["pred_acceptable"])
    a_post, b_post = 1 + k_acc, 1 + len(labels) - k_acc
    out = {"audit": {"labelled_failures": len(labels), "acceptable": k_acc, "posterior": f"Beta({a_post},{b_post})",
                     "labels_by": "executing SQL per case; bird_093 re-checked"},
           "pairs": []}
    for a, b in PAIRS_FULL + PAIRS_SUB:
        ids = sorted(scored[a]) if (a in FULL and b in FULL) else sorted(sub)
        xa = np.array([scored[a][i]["set"] for i in ids])
        xb = np.array([scored[b][i]["set"] for i in ids])
        base = mcnemar_exact(list(xa), list(xb))
        base_delta = 100 * (xb.mean() - xa.mean())
        res = {"a": a, "b": b, "n": len(ids), "delta_pp": round(base_delta, 2), "p": base["p_value"]}
        for model in ("independent", "shared"):
            deltas, keep_sign, keep_sig = [], 0, 0
            for _ in range(draws):
                p = rng.beta(a_post, b_post)
                if model == "independent":
                    ua, ub = rng.random(len(ids)) < p, rng.random(len(ids)) < p
                else:
                    ua = ub = rng.random(len(ids)) < p
                ca, cb = np.maximum(xa, ua & (xa == 0)), np.maximum(xb, ub & (xb == 0))
                d = 100 * (cb.mean() - ca.mean())
                deltas.append(d)
                keep_sign += np.sign(d) == np.sign(base_delta)
                if base["p_value"] < 0.05:
                    keep_sig += mcnemar_exact(list(ca.astype(int)), list(cb.astype(int)))["p_value"] < 0.05
            res[model] = {"corrected_delta_pp_mean": round(float(np.mean(deltas)), 2),
                          "corrected_delta_ci95": [round(float(np.percentile(deltas, 2.5)), 2),
                                                   round(float(np.percentile(deltas, 97.5)), 2)],
                          "sign_kept": round(keep_sign / draws, 3),
                          "significance_kept": round(keep_sig / draws, 3) if base["p_value"] < 0.05 else None}
        out["pairs"].append(res)
    return out


def main() -> None:
    ms, scored = metric_sensitivity()
    ln = label_noise(scored)
    out = {"metric_sensitivity": ms, "label_noise": ln}
    (RESULTS / "robustness.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(ms["ex"], indent=1))
    for r in ms["pairs"]:
        print(r["a"], "->", r["b"], {m: (r[m]["delta_pp"], r[m]["p"]) for m in ("set", "multiset", "ordered", "float_tol")})
    for r in ln["pairs"]:
        print(r["a"], "->", r["b"], r["delta_pp"], r["p"], r["independent"], r["shared"])


if __name__ == "__main__":
    main()
