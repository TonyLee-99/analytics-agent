"""A learned, cost-sensitive router/abstention policy from the cached samples (zero LLM calls).

Question: given the five cached Haiku samples for a BIRD question, should we (1) answer with Haiku's vote,
or (2) escalate to Sonnet? A fixed agreement threshold is one policy; here a model predicts
P(Haiku vote is wrong) from signals available at answer time, and the escalation threshold is chosen to
minimise an explicit expected cost:

    cost(question) = $Haiku_SC5 + [escalated] * $Sonnet + [final answer wrong] * W

W is the business cost of a wrong number reaching a user (stated, varied: $0.01, $0.10, $1). Prices are
the explicit price sheet in eval/stats.py with no prompt caching (like-for-like).
Protocol: nested cross-validation on the frozen sub200 -- outer 5 folds x 20 repeats for reporting, the
threshold chosen inside each training fold. Baselines: never / always escalate, the pre-registered
threshold (agreement < 0.8), unanimity (< 1.0), and the oracle (escalate iff Haiku is wrong).

    uv run python -m analytics_agent.eval.learned_router   -> results/learned_router.json
"""

from __future__ import annotations

import json
import math

import numpy as np

from analytics_agent.confidence import fit_logreg, predict_logreg
from analytics_agent.config import RESULTS
from analytics_agent.eval.datasets import load_bird, subset_ids
from analytics_agent.eval.report import load_stem
from analytics_agent.eval.stats import PRICE

FEATURES = ["agreement", "n_result_clusters", "sample0_in_majority", "sample0_corrected", "sample0_problem",
            "retrieval_top_score", "log_rows", "n_joins", "n_selects", "log_sql_chars", "has_hint"]


def _usd(u: dict, model: str) -> float:
    return (u.get("input_tokens", 0) * PRICE[model]["in"] + u.get("output_tokens", 0) * PRICE[model]["out"]) / 1e6


def build() -> tuple[np.ndarray, dict]:
    sub = [f"bird_{i:03d}" for i in subset_ids("sub200")]
    tasks = {t.id: t for t in load_bird(subset_ids("sub200"))}
    e = load_stem("bird__e_sc5__haiku__sub200")
    so = load_stem("bird__d_selfcorrect__sonnet__sub200")
    X, h_ok, s_ok, h_cost, s_cost = [], [], [], [], []
    for i in sub:
        r, sg = e[i], e[i]["signals"]
        sql = (r["pred_sql"] or "").upper()
        X.append([float(sg.get("sc_agreement") or 0), float(len(sg.get("sc_votes") or [])),
                  float(bool(sg.get("main_in_majority"))), float(sg.get("corrections") or 0),
                  float(bool(sg.get("main_problems"))), float(sg.get("retrieval_top_score") or 0),
                  math.log1p(sg.get("n_rows") or 0), float(sql.count(" JOIN ")), float(sql.count("SELECT")),
                  math.log1p(len(sql)), float(bool(tasks[i].evidence))])
        h_ok.append(r["ex"])
        s_ok.append(so[i]["ex"])
        h_cost.append(_usd(r["usage"], "claude-haiku-5-5"))
        s_cost.append(_usd(so[i]["usage"], "claude-sonnet-5-5"))
    return np.array(X), {"h_ok": np.array(h_ok), "s_ok": np.array(s_ok), "h_cost": np.array(h_cost),
                         "s_cost": np.array(s_cost), "ids": sub}


def evaluate(esc: np.ndarray, d: dict, idx: np.ndarray, W: float) -> dict:
    ok = np.where(esc, d["s_ok"][idx], d["h_ok"][idx])
    usd = d["h_cost"][idx] + esc * d["s_cost"][idx]
    return {"ex": float(ok.mean()), "usd": float(usd.mean()), "esc": float(esc.mean()),
            "expected_cost": float((usd + (1 - ok) * W).mean())}


def choose_threshold(p: np.ndarray, d: dict, idx: np.ndarray, W: float) -> float:
    grid = np.unique(np.concatenate([[0.0, 1.01], p]))
    costs = [evaluate(p >= t, d, idx, W)["expected_cost"] for t in grid]
    return float(grid[int(np.argmin(costs))])


def run(Ws=(0.01, 0.10, 1.0), repeats: int = 20, folds: int = 5) -> dict:
    X, d = build()
    y_wrong = 1 - d["h_ok"]
    n = len(y_wrong)
    agree = X[:, 0]
    out = {"n": n, "features": FEATURES, "by_wrong_answer_cost": {}}
    for W in Ws:
        rows = {k: [] for k in ("learned", "agreement_best_threshold_in_fold")}
        for rep in range(repeats):
            perm = np.random.default_rng(rep).permutation(n)
            for f in range(folds):
                test = perm[f::folds]
                train = np.setdiff1d(perm, test)
                m = fit_logreg(X[train], y_wrong[train], l2=5.0)
                p_tr, p_te = predict_logreg(m, X[train]), predict_logreg(m, X[test])
                t = choose_threshold(p_tr, d, train, W)
                rows["learned"].append(evaluate(p_te >= t, d, test, W) | {"n": len(test)})
                # same nested protocol, but the only feature is (1 - agreement)
                ta = choose_threshold(1 - agree[train], d, train, W)
                rows["agreement_best_threshold_in_fold"].append(
                    evaluate((1 - agree[test]) >= ta, d, test, W) | {"n": len(test)})
        res = {}
        for k, lst in rows.items():
            w = np.array([r["n"] for r in lst], dtype=float)
            res[k] = {m: round(float(np.average([r[m] for r in lst], weights=w)), 5) for m in ("ex", "usd", "esc", "expected_cost")}
        all_idx = np.arange(n)
        fixed = {"never_escalate": np.zeros(n, bool), "always_escalate": np.ones(n, bool),
                 "preregistered_agreement_lt_0.8": agree < 0.8, "unanimity_agreement_lt_1.0": agree < 1.0,
                 "oracle": d["h_ok"] == 0}
        for k, esc in fixed.items():
            res[k] = {m: round(v, 5) for m, v in evaluate(esc, d, all_idx, W).items()}
        for k in res:
            res[k]["ex"] = round(100 * res[k]["ex"], 2)
            res[k]["esc"] = round(100 * res[k]["esc"], 1)
        out["by_wrong_answer_cost"][f"W=${W}"] = res
    # does the learned score rank errors better than agreement alone? (out-of-fold AUROC, 20 repeats)
    from analytics_agent.confidence import auroc

    aucs = []
    for rep in range(repeats):
        perm = np.random.default_rng(100 + rep).permutation(n)
        p = np.zeros(n)
        for f in range(folds):
            test = perm[f::folds]
            train = np.setdiff1d(perm, test)
            p[test] = predict_logreg(fit_logreg(X[train], y_wrong[train], l2=5.0), X[test])
        aucs.append(auroc(-p, d["h_ok"].astype(float)))
    out["oof_auroc_learned_mean"] = round(float(np.mean(aucs)), 4)
    out["oof_auroc_learned_range"] = [round(float(min(aucs)), 4), round(float(max(aucs)), 4)]
    out["auroc_agreement_only"] = round(auroc(agree, d["h_ok"].astype(float)), 4)
    (RESULTS / "learned_router.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    r = run()
    for W, res in r["by_wrong_answer_cost"].items():
        print(W)
        for k, v in res.items():
            print(f"   {k:36s} EX {v['ex']:6.2f}  esc {v['esc']:5.1f}%  $/q {v['usd']:.5f}  E[cost] {v['expected_cost']:.5f}")
    print("OOF AUROC learned", r["oof_auroc_learned_mean"], r["oof_auroc_learned_range"], "agreement", r["auroc_agreement_only"])
