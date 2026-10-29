"""Turns run files into the tables and plots used in the README.

    uv run python -m analytics_agent.eval.analysis ablations   -> results/ablations.md (+ .json)
    uv run python -m analytics_agent.eval.analysis abstention  -> results/abstention.json, docs/img/risk_coverage_*.png
    uv run python -m analytics_agent.eval.analysis routing     -> results/routing.json (runs the Router; cache hits)
    uv run python -m analytics_agent.eval.analysis frontier    -> docs/img/frontier_*.png, results/frontier.json
    uv run python -m analytics_agent.eval.analysis all
No new LLM calls are made unless a routed question was never answered by the strong model before.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from analytics_agent.agent import Agent
from analytics_agent.confidence import FEATURES, features, oof_logreg, risk_coverage, selective_report
from analytics_agent.config import RESULTS, ROOT, RUNS
from analytics_agent.eval.configs import get_config
from analytics_agent.eval.datasets import load_bird, subset_ids
from analytics_agent.eval.metrics import mcnemar_exact, percentile, score, wilson
from analytics_agent.eval.report import load_run
from analytics_agent.eval.run import bird_examples
from analytics_agent.llm import make_client
from analytics_agent.router import Router

IMG = ROOT / "docs" / "img"

CHAIN = [  # (label, file stem) for the full-500 Haiku ablation chain
    ("(a) zero-shot, official BIRD prompt, full schema", "bird__a_zeroshot_official__haiku"),
    ("(b) + our prompt, schema retrieval (BM25 top-6 + FK)", "bird__b_retrieval__haiku"),
    ("(c) + 3 retrieved verified examples", "bird__c_fewshot__haiku"),
    ("(d) + self-correction (<=2 rounds, value hints)", "bird__d_selfcorrect__haiku"),
]
SUB = [  # everything comparable on the frozen 200-question subset
    ("qwen2.5:7b local, (a) official prompt", "bird__a_zeroshot_official__qwen__sub200", "qwen"),
    ("qwen2.5:7b local, (d) full pipeline", "bird__d_selfcorrect__qwen__sub200", "qwen"),
    ("haiku (a) official prompt", "bird__a_zeroshot_official__haiku", "haiku"),
    ("haiku (a2) our prompt, full schema", "bird__a2_ourprompt_full__haiku__sub200", "haiku"),
    ("haiku (b) retrieval", "bird__b_retrieval__haiku", "haiku"),
    ("haiku (c) few-shot", "bird__c_fewshot__haiku", "haiku"),
    ("haiku (d) self-correction", "bird__d_selfcorrect__haiku", "haiku"),
    ("haiku (e) self-consistency x5 (vote)", "bird__e_sc5__haiku__sub200", "haiku"),
    ("sonnet (d) full pipeline", "bird__d_selfcorrect__sonnet__sub200", "sonnet"),
]


def _load(stem: str) -> dict[str, dict] | None:
    p = RUNS / f"{stem}.jsonl"
    return {r["id"]: r for r in load_run(p)} if p.exists() else None


def _stats(recs: list[dict]) -> dict:
    n = len(recs)
    k = sum(r["ex"] for r in recs)
    lo, hi = wilson(k, n)
    by = {}
    for d in ("simple", "moderate", "challenging"):
        xs = [r["ex"] for r in recs if r["difficulty"] == d]
        by[d] = (round(100 * sum(xs) / len(xs), 1), len(xs)) if xs else (None, 0)
    lat = [r["usage"].get("latency_s", 0) for r in recs]
    cost = [r["usage"].get("cost_usd", 0) for r in recs]
    return {
        "n": n, "ex": round(100 * k / n, 2), "ci95": [round(100 * lo, 1), round(100 * hi, 1)], "by_difficulty": by,
        "cost_per_q": round(float(np.mean(cost)), 5), "p50_s": round(percentile(lat, 0.5), 1),
        "p95_s": round(percentile(lat, 0.95), 1),
        "in_tok": round(float(np.mean([r["usage"].get("input_tokens", 0) for r in recs]))),
        "out_tok": round(float(np.mean([r["usage"].get("output_tokens", 0) for r in recs]))),
        "calls_per_q": round(float(np.mean([r["usage"].get("llm_calls", 0) for r in recs])), 2),
    }


def ablations() -> dict:
    out: dict = {"full500": [], "sub200": []}
    dev = {f"bird_{i:03d}" for i in subset_ids("dev20")}
    prev = None
    for label, stem in CHAIN:
        runs = _load(stem)
        if not runs or len(runs) < 500:
            out["full500"].append({"label": label, "missing": True, "n": len(runs or {})})
            continue
        recs = list(runs.values())
        st = _stats(recs)
        st["ex_excl_dev20"] = round(100 * np.mean([r["ex"] for r in recs if r["id"] not in dev]), 2)
        if prev is not None:
            ids = sorted(set(runs) & set(prev))
            st["vs_previous"] = mcnemar_exact([prev[i]["ex"] for i in ids], [runs[i]["ex"] for i in ids])
        st["label"] = label
        out["full500"].append(st)
        prev = runs
    sub = {f"bird_{i:03d}" for i in subset_ids("sub200")}
    base = None
    for label, stem, model in SUB:
        runs = _load(stem)
        if not runs:
            continue
        recs = [r for i, r in runs.items() if i in sub]
        if len(recs) < len(sub):
            out["sub200"].append({"label": label, "missing": True, "n": len(recs)})
            continue
        st = _stats(recs) | {"label": label, "model": model, "stem": stem}
        if "sc5" in stem:
            st["ex_sample0"] = round(100 * np.mean([r["ex_main"] for r in recs]), 2)
        if base is None and model == "haiku":
            base = {r["id"]: r for r in recs}
        if base is not None:
            ids = sorted(base)
            st["vs_haiku_a"] = mcnemar_exact([base[i]["ex"] for i in ids], [runs[i]["ex"] for i in ids])
        out["sub200"].append(st)
    # targeted paired comparisons on sub200 (isolate one change at a time)
    pairs = [("retrieval vs full schema (same prompt)", "bird__a2_ourprompt_full__haiku__sub200", "bird__b_retrieval__haiku"),
             ("our prompt vs official prompt (full schema)", "bird__a_zeroshot_official__haiku", "bird__a2_ourprompt_full__haiku__sub200"),
             ("few-shot vs none", "bird__b_retrieval__haiku", "bird__c_fewshot__haiku"),
             ("self-correction vs none", "bird__c_fewshot__haiku", "bird__d_selfcorrect__haiku"),
             ("self-consistency x5 vote vs single", "bird__d_selfcorrect__haiku", "bird__e_sc5__haiku__sub200"),
             ("sonnet vs haiku (both full pipeline)", "bird__d_selfcorrect__haiku", "bird__d_selfcorrect__sonnet__sub200"),
             ("haiku vs qwen2.5:7b (both full pipeline)", "bird__d_selfcorrect__qwen__sub200", "bird__d_selfcorrect__haiku")]
    out["pairs_sub200"] = []
    for label, a, b in pairs:
        ra, rb = _load(a), _load(b)
        if not ra or not rb:
            continue
        ids = sorted(sub & set(ra) & set(rb))
        if len(ids) < len(sub):
            continue
        xa, xb = [ra[i]["ex"] for i in ids], [rb[i]["ex"] for i in ids]
        out["pairs_sub200"].append({"comparison": label, "n": len(ids), "ex_a": round(100 * np.mean(xa), 1),
                                    "ex_b": round(100 * np.mean(xb), 1), **mcnemar_exact(xa, xb)})
    (RESULTS / "ablations.json").write_text(json.dumps(out, indent=2))
    lines = ["# BIRD Mini-Dev (2026 release, SQLite) results", "",
             "Generated by `uv run python -m analytics_agent.eval.analysis ablations` from `results/runs/*.jsonl`.",
             "EX = BIRD execution accuracy (set equality of result rows). 95% Wilson CI. p = exact McNemar vs the "
             "previous row on the same questions. Cost is the CLI's own `total_cost_usd`; latency is modeled "
             "end-to-end (sum of LLM call latencies on the critical path + DB time).", "",
             "## Full 500 questions, Claude Haiku 5.5", "",
             "| config | EX % | 95% CI | simple | moderate | challenging | Δ vs prev (p) | $/q | p50 s | p95 s | in tok | out tok | calls/q |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for st in out["full500"]:
        if st.get("missing"):
            lines.append(f"| {st['label']} | (incomplete: {st['n']}) |||||||||||")
            continue
        b = st["by_difficulty"]
        vs = st.get("vs_previous")
        vs_s = f"+{vs['only_b']}/-{vs['only_a']} (p={vs['p_value']})" if vs else "–"
        lines.append(f"| {st['label']} | **{st['ex']}** | {st['ci95'][0]}–{st['ci95'][1]} | {b['simple'][0]} (n={b['simple'][1]}) "
                     f"| {b['moderate'][0]} (n={b['moderate'][1]}) | {b['challenging'][0]} (n={b['challenging'][1]}) | {vs_s} "
                     f"| {st['cost_per_q']:.4f} | {st['p50_s']} | {st['p95_s']} | {st['in_tok']} | {st['out_tok']} | {st['calls_per_q']} |")
    lines += ["", "## Frozen 200-question stratified subset (all models)", "",
              "| system | EX % | 95% CI | Δ vs haiku (a) (p) | $/q | p50 s | p95 s | calls/q |", "|---|---|---|---|---|---|---|---|"]
    for st in out["sub200"]:
        if st.get("missing"):
            lines.append(f"| {st['label']} | (incomplete: {st['n']}) |||||||")
            continue
        vs = st.get("vs_haiku_a")
        vs_s = f"+{vs['only_b']}/-{vs['only_a']} (p={vs['p_value']})" if vs else "–"
        extra = f" (sample-0: {st['ex_sample0']})" if "ex_sample0" in st else ""
        lines.append(f"| {st['label']} | **{st['ex']}**{extra} | {st['ci95'][0]}–{st['ci95'][1]} | {vs_s} | {st['cost_per_q']:.4f} "
                     f"| {st['p50_s']} | {st['p95_s']} | {st['calls_per_q']} |")
    lines += ["", "## Paired comparisons on sub200 (one change at a time; exact McNemar)", "",
              "| comparison (A → B) | n | EX A | EX B | B-only right | A-only right | p |", "|---|---|---|---|---|---|---|"]
    for p_ in out["pairs_sub200"]:
        lines.append(f"| {p_['comparison']} | {p_['n']} | {p_['ex_a']} | {p_['ex_b']} | {p_['only_b']} | {p_['only_a']} | {p_['p_value']} |")
    (RESULTS / "ablations.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return out


def _plot_rc(curves: list[tuple[str, np.ndarray, np.ndarray]], title: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=130)
    colors = ["#2a6fdb", "#e07b39", "#3a9e5c", "#8e5bd8", "#777777"]
    for (label, cov, acc), c in zip(curves, colors, strict=False):
        ax.plot(100 * cov, 100 * acc, label=label, color=c, lw=2)
    ax.axvline(80, color="#bbbbbb", ls="--", lw=1)
    ax.set_xlabel("coverage: % of questions answered (most confident first)")
    ax.set_ylabel("execution accuracy of answered questions (%)")
    ax.set_title(title, fontsize=10)
    ax.set_xlim(0, 100)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, frameon=False, loc="lower left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def _selective(recs: list[dict], target: str = "ex") -> dict:
    y = np.array([r[target] for r in recs], dtype=float)
    X = np.array([features(r) for r in recs])
    agree = X[:, 0]
    rep = {"agreement": selective_report(agree, y)}
    lr = oof_logreg(X, y)
    rep["logreg_oof"] = selective_report(lr, y)
    single = np.array([(1.0 if (r["signals"].get("final_ok") and not r["signals"].get("main_problems")) else 0.0)
                       - 0.01 * r["signals"].get("corrections", 0) for r in recs])
    rep["single_sample_signals_only"] = selective_report(single, y)
    rep["features"] = FEATURES
    rep["_curves"] = {"agreement": agree, "logreg_oof": lr, "single": single, "y": y}
    return rep


def abstention() -> dict:
    out = {}
    for name, stem, title in [
        ("bird_sub200", "bird__e_sc5__haiku__sub200", "BIRD sub200, Haiku, 5 samples"),
        ("olist_golden", "olist__olist_semantic_n5__haiku", "Olist golden set, Haiku semantic arm, 5 samples"),
        ("olist_golden_raw", "olist__olist_raw_n5__haiku", "Olist golden set, Haiku raw-SQL arm, 5 samples"),
    ]:
        runs = _load(stem)
        if not runs:
            continue
        recs = list(runs.values())
        rep = _selective(recs)
        cur = rep.pop("_curves")
        curves = []
        for key, label in (("agreement", "self-consistency agreement"), ("logreg_oof", "calibrated (logreg, out-of-fold)"),
                           ("single", "single-sample signals only")):
            cov, acc = risk_coverage(cur[key], cur["y"])
            curves.append((label, cov, acc))
        _plot_rc(curves, f"Selective prediction: {title}", IMG / f"risk_coverage_{name}.png")
        out[name] = rep
        print(name, json.dumps({k: v for k, v in rep.items() if k != "features"}, indent=1))
    # Does self-consistency catch *definitional* errors? Compare agreement on correct answers, answers that
    # equal a known non-governed definition (drift), and other wrong answers (Olist raw arm).
    sem = RESULTS / "semantic_experiment__haiku.json"
    raw_sc = _load("olist__olist_raw_n5__haiku")
    if sem.exists() and raw_sc:
        per_q = json.loads(sem.read_text())["per_question"]
        groups: dict[str, list[float]] = {"correct": [], "definition_drift": [], "other_wrong": []}
        for qid, r in raw_sc.items():
            a = float(r["signals"].get("sc_agreement") or 0)
            if r["ex_main"]:
                groups["correct"].append(a)
            else:
                lab = per_q.get(qid, {}).get("olist_raw", {}).get("definition", "other_wrong")
                groups["definition_drift" if lab.startswith("variant") else "other_wrong"].append(a)
        out["olist_raw_agreement_by_outcome"] = {k: {"n": len(v), "mean_agreement": round(float(np.mean(v)), 3) if v else None,
                                                     "share_with_agreement_ge_0.8": round(float(np.mean([x >= 0.8 for x in v])), 3) if v else None}
                                                 for k, v in groups.items()}
        print("agreement by outcome (Olist raw):", out["olist_raw_agreement_by_outcome"])
    (RESULTS / "abstention.json").write_text(json.dumps(out, indent=2))
    return out


def routing(threshold: float = 0.8) -> dict:
    tasks = load_bird(subset_ids("sub200"))
    ex_store = bird_examples()
    cheap_sc = Agent(make_client("haiku"), get_config("e_sc5"), ex_store)
    cheap_1 = Agent(make_client("haiku"), get_config("d_selfcorrect"), ex_store)
    strong = Agent(make_client("sonnet"), get_config("d_selfcorrect"), ex_store)
    results = {}
    for name, router in [("haiku_sc5_then_sonnet", Router(cheap_sc, strong, threshold, "sc_agreement")),
                         ("haiku_single_then_sonnet", Router(cheap_1, strong, 0.5, "single"))]:
        def one(t, router=router):
            ra = router.ask(t.question, t.db_id, t.evidence, task_id=t.id)
            ex, _ = score(t, ra.answer.sql)
            return {"id": t.id, "ex": ex, "escalated": ra.escalated, "cost": ra.cost_usd, "latency": ra.latency_s}
        with ThreadPoolExecutor(max_workers=2) as pool:
            recs = list(pool.map(one, tasks))
        lat = [r["latency"] for r in recs]
        results[name] = {
            "threshold": threshold if "sc5" in name else "escalate on any problem/correction",
            "n": len(recs), "ex": round(100 * np.mean([r["ex"] for r in recs]), 2),
            "escalation_rate": round(float(np.mean([r["escalated"] for r in recs])), 3),
            "cost_per_q": round(float(np.mean([r["cost"] for r in recs])), 5),
            "p50_s": round(percentile(lat, 0.5), 1), "p95_s": round(percentile(lat, 0.95), 1),
        }
        print(name, results[name])
    # threshold sweep from the recorded runs (no calls): EX and cost as the escalation threshold moves
    sc = _load("bird__e_sc5__haiku__sub200")
    so = _load("bird__d_selfcorrect__sonnet__sub200")
    if sc and so:
        sweep = []
        for th in (0.2, 0.4, 0.6, 0.8, 1.0, 1.01):
            ex, cost = [], []
            for t in tasks:
                a, b = sc[t.id], so[t.id]
                esc = not (a["status"] == "answered" and (a["signals"].get("sc_agreement") or 0) >= th)
                ex.append(b["ex"] if esc else a["ex"])
                cost.append(a["usage"]["cost_usd"] + (b["usage"]["cost_usd"] if esc else 0))
            sweep.append({"threshold": th, "ex": round(100 * np.mean(ex), 2), "cost_per_q": round(float(np.mean(cost)), 5)})
        results["threshold_sweep"] = sweep
    (RESULTS / "routing.json").write_text(json.dumps(results, indent=2))
    return results


def frontier() -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    abl = json.loads((RESULTS / "ablations.json").read_text()) if (RESULTS / "ablations.json").exists() else ablations()
    pts = [(s["label"], s["ex"], s["cost_per_q"], s["p95_s"], s["model"]) for s in abl["sub200"] if not s.get("missing")]
    rt = json.loads((RESULTS / "routing.json").read_text()) if (RESULTS / "routing.json").exists() else {}
    for k, v in rt.items():
        if k != "threshold_sweep":
            pts.append((f"routed: {k}", v["ex"], v["cost_per_q"], v["p95_s"], "routed"))
    colors = {"qwen": "#3a9e5c", "haiku": "#2a6fdb", "sonnet": "#e07b39", "routed": "#8e5bd8"}
    for xkey, xlabel, fname in [(2, "mean $ per question (log scale; local qwen plotted at $0.00001)", "frontier_cost"),
                                (3, "p95 end-to-end latency per question (s)", "frontier_latency")]:
        fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=130)
        for label, ex, cost, p95, model in pts:
            x = max(cost, 1e-5) if xkey == 2 else p95
            ax.scatter(x, ex, color=colors[model], s=46, zorder=3)
            ax.annotate(label.replace("haiku ", "H ").replace("sonnet ", "S ").replace("qwen2.5:7b local, ", "Q "),
                        (x, ex), fontsize=6.5, xytext=(4, 3), textcoords="offset points")
        if xkey == 2:
            ax.set_xscale("log")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("BIRD EX on frozen sub200 (%)")
        ax.grid(alpha=0.25)
        for m, c in colors.items():
            ax.scatter([], [], color=c, label=m)
        ax.legend(fontsize=8, frameon=False, loc="lower right")
        ax.set_title("Cost-quality frontier (each point = one configuration, same 200 questions)", fontsize=10)
        fig.tight_layout()
        IMG.mkdir(parents=True, exist_ok=True)
        fig.savefig(IMG / f"{fname}.png")
        plt.close(fig)
    out = [{"label": p[0], "ex": p[1], "cost_per_q": p[2], "p95_s": p[3], "model": p[4]} for p in pts]
    (RESULTS / "frontier.json").write_text(json.dumps(out, indent=2))
    return {"points": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["ablations", "abstention", "routing", "frontier", "all"])
    args = ap.parse_args()
    if args.what in ("ablations", "all"):
        ablations()
    if args.what in ("abstention", "all"):
        abstention()
    if args.what in ("routing", "all"):
        routing()
    if args.what in ("frontier", "all"):
        frontier()


if __name__ == "__main__":
    main()
