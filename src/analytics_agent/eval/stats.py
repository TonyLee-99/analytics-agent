"""Statistics that every headline number is read against (zero LLM calls; recorded runs only).

    uv run python -m analytics_agent.eval.stats      -> results/stats.json

Sections
- dev20:       headline EX with and without the 20 prompt-development questions
- noise:       per-question flip rate between two independent draws -> 95% half-width of a run-vs-run delta
- subset:      is the frozen sub200 subset representative? (baseline on sub200 vs the other 300)
- abstention:  EX at the *achievable* agreement thresholds with bootstrap CIs; AUROC CI; tie-break range
- fewshot:     few-shot gain split by template similarity of the retrieved example to the gold SQL
- vote:        self-consistency vote vs its own sample 0; fresh-draw count between runs (c) and (d)
- cost:        $/question recomputed from tokens x an explicit price sheet (fit exactly to the CLI's own
               cost reports), under "as recorded" and "no prompt caching"; routed vs Sonnet; oracle router
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict

import numpy as np

from analytics_agent.confidence import auroc
from analytics_agent.config import RESULTS
from analytics_agent.eval.datasets import load_bird, subset_ids
from analytics_agent.eval.metrics import mcnemar_exact, wilson
from analytics_agent.eval.report import load_stem
from analytics_agent.eval.run import bird_examples

# $ per million tokens, recovered by least squares from 3,184 Haiku and 212 Sonnet CLI cost reports
# (cost = a*uncached_in + b*cache_write + c*cache_read + d*out; max abs residual < 1e-16).
# Sonnet never had a cache read, so its read price is assumed to be 0.1x input, as for Haiku.
PRICE = {"claude-haiku-5-5": {"in": 0.10, "write": 0.20, "read": 0.01, "out": 0.50},
         "claude-sonnet-5-5": {"in": 2.00, "write": 4.00, "read": 0.20, "out": 10.00}}

RNG = np.random.default_rng(20261029)


def ids(name: str) -> set[str]:
    return {f"bird_{i:03d}" for i in subset_ids(name)}


def pct(x) -> float:
    return round(100 * float(np.mean(x)), 2)


def boot_ci(f, n: int, b: int = 4000) -> list[float]:
    vals = [f(RNG.integers(0, n, n)) for _ in range(b)]
    return [round(float(np.percentile(vals, 2.5)), 3), round(float(np.percentile(vals, 97.5)), 3)]


def dev20_section() -> dict:
    dev = ids("dev20")
    out = {}
    for s in ("a_zeroshot_official", "b_retrieval", "c_fewshot", "d_selfcorrect"):
        r = load_stem(f"bird__{s}__haiku")
        out[s] = {"all500": pct([x["ex"] for x in r.values()]),
                  "excl_dev20_n480": pct([x["ex"] for i, x in r.items() if i not in dev])}
    a, d = load_stem("bird__a_zeroshot_official__haiku"), load_stem("bird__d_selfcorrect__haiku")
    keep = sorted(i for i in a if i not in dev)
    out["a_to_d_excl_dev20"] = mcnemar_exact([a[i]["ex"] for i in keep], [d[i]["ex"] for i in keep])
    out["note"] = "dev20 is disjoint from sub200, so every sub200 number is unaffected"
    return out


def noise_section() -> dict:
    nf = json.loads((RESULTS / "noise_floor.json").read_text())
    f = nf["share_of_questions_flipping_between_two_draws"]
    hw = {str(n): round(100 * 1.96 * math.sqrt(f / n), 1) for n in (30, 50, 190, 200, 500)}
    return {"flip_rate": f, "n_flip_estimate": nf["n_questions"],
            "half_width_95_pp_of_a_run_vs_run_delta": hw,
            "explanation": "two draws of the same system disagree on a share f of questions; the EX difference "
                           "between them has SD ~ sqrt(f/n), so |delta| below 1.96*sqrt(f/n) is indistinguishable "
                           "from redrawing. Draws had no self-correction: this is generation noise.",
            "draw_ex_range": [min(nf["ex_per_draw"]), max(nf["ex_per_draw"])]}


def subset_section() -> dict:
    sub = ids("sub200")
    out = {}
    for s in ("a_zeroshot_official", "d_selfcorrect"):
        r = load_stem(f"bird__{s}__haiku")
        a = [x["ex"] for i, x in r.items() if i in sub]
        b = [x["ex"] for i, x in r.items() if i not in sub]
        se = math.sqrt(np.mean(a) * (1 - np.mean(a)) / len(a) + np.mean(b) * (1 - np.mean(b)) / len(b))
        out[s] = {"sub200": pct(a), "other300": pct(b), "z": round((np.mean(a) - np.mean(b)) / se, 2)}
    return out


def abstention_section() -> dict:
    e = load_stem("bird__e_sc5__haiku__sub200")
    recs = list(e.values())
    agree = np.array([r["signals"]["sc_agreement"] or 0.0 for r in recs])
    y = np.array([r["ex"] for r in recs], dtype=float)
    n = len(y)
    levels = sorted(set(agree.tolist()), reverse=True)
    table = []
    for lv in levels:
        m = agree == lv
        table.append({"agreement": lv, "n": int(m.sum()), "ex": pct(y[m])})
    ops = []
    for th in (1.0, 0.8, 0.6):
        m = agree >= th

        def gain(idx, th=th):
            a, yy = agree[idx], y[idx]
            mm = a >= th
            return 100 * (yy[mm].mean() - yy.mean()) if mm.any() else 0.0
        ops.append({"answer_if_agreement_at_least": th, "coverage": pct(m), "ex_answered": pct(y[m]),
                    "gain_vs_answer_all_pp": round(100 * (y[m].mean() - y.mean()), 2),
                    "gain_ci95_pp": boot_ci(gain, n)})
    # EX at exactly 80% coverage depends on how ties at agreement 0.8 are broken
    k = int(math.ceil(0.8 * n))
    ex80 = []
    for seed in range(2000):
        rng = np.random.default_rng(seed)
        order = np.lexsort((rng.random(n), -agree))
        ex80.append(100 * y[order[:k]].mean())
    au = auroc(agree, y)
    au_ci = boot_ci(lambda idx: auroc(agree[idx], y[idx]), n)
    pos, neg = agree[y == 1], agree[y == 0]
    ties = float((pos[:, None] == neg[None, :]).mean())
    return {"n": n, "ex_all": pct(y), "by_agreement_level": table, "operating_points": ops,
            "ex_at_exactly_80pct_coverage_over_tie_seeds": {"min": round(min(ex80), 1), "mean": round(float(np.mean(ex80)), 2),
                                                            "max": round(max(ex80), 1)},
            "auroc": round(au, 4), "auroc_ci95": au_ci, "share_of_pos_neg_pairs_tied": round(ties, 3),
            "oracle_any_of_5_correct": json.loads((RESULTS / "noise_floor.json").read_text())["oracle_any_of_5_correct"]}


def _toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", s.lower()))


def fewshot_section() -> dict:
    tasks = {t.id: t for t in load_bird()}
    store = bird_examples()
    b, c = load_stem("bird__b_retrieval__haiku"), load_stem("bird__c_fewshot__haiku")
    buckets = defaultdict(list)
    for tid, t in tasks.items():
        exs = store.search(t.question + " " + t.evidence, t.db_id, 3)
        g = _toks(t.gold_sql)
        sim = max((len(g & _toks(e.sql)) / max(1, len(g | _toks(e.sql))) for e in exs), default=0.0)
        key = "<0.5" if sim < 0.5 else ("0.5-0.6" if sim < 0.6 else "0.6-0.7")
        buckets[key].append(tid)
    out = {}
    for key in ("<0.5", "0.5-0.6", "0.6-0.7"):
        ii = buckets[key]
        xb, xc = [b[i]["ex"] for i in ii], [c[i]["ex"] for i in ii]
        out[key] = {"n": len(ii), "ex_without_examples": pct(xb), "ex_with_examples": pct(xc), **mcnemar_exact(xb, xc)}
    out["definition"] = "max token-Jaccard between any of the 3 retrieved example SQLs and the test question's gold SQL"
    return out


def vote_section() -> dict:
    e = load_stem("bird__e_sc5__haiku__sub200")
    c, d = load_stem("bird__c_fewshot__haiku"), load_stem("bird__d_selfcorrect__haiku")
    fresh = [i for i in d if d[i]["signals"]["corrections"] == 0 and d[i]["pred_sql"] != c[i]["pred_sql"]]
    return {"vote_ex": pct([r["ex"] for r in e.values()]), "own_sample0_ex": pct([r["ex_main"] for r in e.values()]),
            "vote_gain_pp": round(pct([r["ex"] for r in e.values()]) - pct([r["ex_main"] for r in e.values()]), 2),
            "vote_vs_sample0": mcnemar_exact([r["ex_main"] for r in e.values()], [r["ex"] for r in e.values()]),
            "d_first_drafts_regenerated_not_replayed": len(fresh),
            "selfcorrection_within_run": {
                "fired": sum(1 for r in d.values() if r["signals"]["corrections"]),
                "fixed": sum(r["ex"] for r in d.values() if r["signals"]["corrections"]),
                "gain_ci95_pp": [round(100 * x, 1) for x in wilson(
                    sum(r["ex"] for r in d.values() if r["signals"]["corrections"]), len(d))]}}


def _price(model: str, u: dict, cache: str) -> float:
    p = PRICE[model]
    tin, rd, wr, out = u.get("input_tokens", 0), u.get("cache_read_tokens", 0), u.get("cache_write_tokens", 0), \
        u.get("output_tokens", 0)
    if cache == "none":
        return (tin * p["in"] + out * p["out"]) / 1e6
    return ((tin - rd - wr) * p["in"] + wr * p["write"] + rd * p["read"] + out * p["out"]) / 1e6


def _per_call_usage(stem: str) -> dict[str, list[dict]]:
    """Per-question list of LLM-call usages from the trace file (falls back to the run's totals)."""
    from analytics_agent.config import TRACES

    out: dict[str, list[dict]] = {}
    p = TRACES / f"{stem}.jsonl"
    if p.exists():
        for line in open(p):
            t = json.loads(line)
            out[t["task_id"]] = [s["llm"] for s in t["spans"] if s.get("llm")]
    return out


def cost_section() -> dict:
    """Routing economics under an explicit price sheet. Run-level token totals are priced directly; the
    'no caching' scenario bills every input token at the base input price."""
    sc = load_stem("bird__e_sc5__haiku__sub200")
    so = load_stem("bird__d_selfcorrect__sonnet__sub200")
    hd = load_stem("bird__d_selfcorrect__haiku")
    sub = ids("sub200")

    def q_cost(rec, model, cache):
        u = rec["usage"]
        tin, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
        if cache == "none":
            return (tin * PRICE[model]["in"] + out * PRICE[model]["out"]) / 1e6
        return u.get("cost_usd", 0.0)

    res = {"price_sheet_per_mtok": PRICE, "scenarios": {}}
    for cache in ("recorded", "none"):
        rows = []
        for th in (0.8, 1.0):
            ex, cost = [], []
            for i in sorted(sub):
                a, b = sc[i], so[i]
                esc = not (a["status"] == "answered" and (a["signals"].get("sc_agreement") or 0) >= th)
                ex.append(b["ex"] if esc else a["ex"])
                cost.append(q_cost(a, "claude-haiku-5-5", cache) + (q_cost(b, "claude-sonnet-5-5", cache) if esc else 0))
            rows.append({"threshold": th, "ex": pct(ex), "cost_per_q": round(float(np.mean(cost)), 5)})
        sonnet = float(np.mean([q_cost(so[i], "claude-sonnet-5-5", cache) for i in sub]))
        haiku1 = float(np.mean([q_cost(hd[i], "claude-haiku-5-5", cache) for i in sub]))
        for r in rows:
            r["share_of_sonnet_cost"] = round(r["cost_per_q"] / sonnet, 3)
        res["scenarios"][cache] = {"sonnet_only_cost_per_q": round(sonnet, 5), "haiku_single_cost_per_q": round(haiku1, 5),
                                   "routed": rows}
    oracle = [so[i]["ex"] if not sc[i]["ex"] else 1 for i in sub]
    res["oracle_router_ex"] = pct(oracle)
    res["oracle_escalation_rate"] = round(float(np.mean([not sc[i]["ex"] for i in sub])), 3)
    res["note"] = ("token counts and recorded costs come from the CLI usage fields; Sonnet calls ran in one burst "
                   "and never got prompt-cache reads, Haiku calls got 21.8% reads, so 'none' is the like-for-like view")
    return res


def semantic_section() -> dict:
    """Paired tests between the Olist arms, and how confident the raw arm was when it used a wrong definition."""
    from analytics_agent.eval.datasets import load_olist_golden

    metric = {t.id for t in load_olist_golden() if t.meta.get("uses_metric")}
    arms = {a: load_stem(f"olist__olist_{a}__haiku") for a in ("raw", "glossary", "semantic", "hybrid")}
    ids_all = sorted(arms["raw"])
    out = {"pairs": []}
    for a, b, scope in [("raw", "glossary", "metric"), ("glossary", "semantic", "metric"), ("raw", "semantic", "metric"),
                        ("glossary", "hybrid", "all"), ("semantic", "hybrid", "all"), ("raw", "glossary", "all")]:
        ii = [i for i in ids_all if scope == "all" or i in metric]
        t = mcnemar_exact([arms[a][i]["ex"] for i in ii], [arms[b][i]["ex"] for i in ii])
        out["pairs"].append({"a": a, "b": b, "questions": scope, "n": len(ii),
                             "correct_a": sum(arms[a][i]["ex"] for i in ii), "correct_b": sum(arms[b][i]["ex"] for i in ii),
                             **t})
    sem = json.loads((RESULTS / "semantic_experiment__haiku.json").read_text())["per_question"]
    sc = load_stem("olist__olist_raw_n5__haiku")
    assert all(sc[i]["main_sql"] == arms["raw"][i]["pred_sql"] for i in sc), "SC sample 0 must equal the raw-arm answer"
    groups: dict[str, list[float]] = {"correct": [], "definition_drift": [], "other_wrong": []}
    for i, r in sc.items():
        a = float(r["signals"]["sc_agreement"] or 0)
        if r["ex_main"]:
            groups["correct"].append(a)
        elif sem.get(i, {}).get("olist_raw", {}).get("definition", "").startswith("variant"):
            groups["definition_drift"].append(a)
        else:
            groups["other_wrong"].append(a)
    out["raw_arm_agreement"] = {k: {"n": len(v), "agreement_values": sorted(v),
                                    "pass_gate_at_0.8": sum(x >= 0.8 for x in v),
                                    "pass_gate_unanimous": sum(x >= 1.0 for x in v)} for k, v in groups.items()}
    return out


def main() -> dict:
    out = {"dev20": dev20_section(), "noise": noise_section(), "subset": subset_section(),
           "abstention": abstention_section(), "fewshot": fewshot_section(), "vote": vote_section(),
           "cost": cost_section(), "semantic": semantic_section()}
    (RESULTS / "stats.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1)[:6000])
    return out


if __name__ == "__main__":
    main()
