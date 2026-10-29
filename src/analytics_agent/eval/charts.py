"""README charts, drawn only from results/*.json and the recorded run files (zero LLM calls).

    uv run python -m analytics_agent.eval.charts
      docs/img/hero.png             definition errors vs SQL errors: who catches what
      docs/img/abstention.png       EX at the achievable agreement thresholds, with bootstrap CIs
      docs/img/frontier_cost.png    EX vs $/question on sub200 (no-caching price sheet), Pareto line
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from analytics_agent.config import RESULTS, ROOT  # noqa: E402
from analytics_agent.eval.datasets import load_olist_golden  # noqa: E402
from analytics_agent.eval.report import load_stem  # noqa: E402

IMG = ROOT / "docs" / "img"
INK, MUTED, GRID = "#1d2430", "#6b7480", "#e3e6ea"
C_OK, C_DRIFT, C_OTHER, C_ACCENT = "#3a7d5c", "#c8553d", "#b9bec6", "#2a6fdb"


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8.5)
    ax.yaxis.label.set_color(INK)
    ax.xaxis.label.set_color(INK)


def hero() -> None:
    sem = json.loads((RESULTS / "semantic_experiment__haiku.json").read_text())["per_question"]
    st = json.loads((RESULTS / "stats.json").read_text())
    metric = [t.id for t in load_olist_golden() if t.meta.get("uses_metric")]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.2, 4.0), dpi=150, gridspec_kw={"width_ratios": [1.05, 1]})
    # left: what happens to the 30 metric questions in each arm
    arms = [("olist_raw", "raw SQL"), ("olist_glossary", "definitions\nin prompt"), ("olist_semantic", "governed\nmetrics (compiler)")]
    ok = [sum(sem[q][a]["definition"] == "governed" for q in metric) for a, _ in arms]
    drift = [sum(sem[q][a]["definition"].startswith("variant") for q in metric) for a, _ in arms]
    other = [len(metric) - o - d for o, d in zip(ok, drift, strict=True)]
    x = np.arange(len(arms))
    a1.bar(x, ok, color=C_OK, width=0.62, label="correct")
    a1.bar(x, drift, bottom=ok, color=C_DRIFT, width=0.62, label="wrong metric definition")
    a1.bar(x, other, bottom=np.array(ok) + np.array(drift), color=C_OTHER, width=0.62, label="other error")
    for i in range(len(arms)):
        a1.text(x[i], ok[i] / 2, f"{ok[i]}", ha="center", va="center", color="white", fontsize=9, fontweight="bold")
        if drift[i]:
            a1.text(x[i], ok[i] + drift[i] / 2, f"{drift[i]}", ha="center", va="center", color="white", fontsize=9,
                    fontweight="bold")
    a1.set_xticks(x, [n for _, n in arms], fontsize=8.5)
    a1.set_ylabel("metric questions (of 30)")
    a1.set_ylim(0, 31)
    a1.set_title("Olist: where the raw-SQL errors come from", fontsize=10, color=INK, loc="left")
    a1.legend(fontsize=7.5, frameon=False, loc="upper left", bbox_to_anchor=(0.0, -0.13), ncol=3)
    _style(a1)
    # right: share of answers that a ">= 4 of 5 samples agree" confidence gate lets through
    e = load_stem("bird__e_sc5__haiku__sub200")
    bird_ok = [r["signals"]["sc_agreement"] >= 0.8 for r in e.values() if r["ex"]]
    bird_bad = [r["signals"]["sc_agreement"] >= 0.8 for r in e.values() if not r["ex"]]
    ra = st["semantic"]["raw_arm_agreement"]
    groups = [("BIRD\ncorrect", np.mean(bird_ok), len(bird_ok), C_OK),
              ("BIRD\nwrong SQL", np.mean(bird_bad), len(bird_bad), C_OTHER),
              ("Olist\ncorrect", ra["correct"]["pass_gate_at_0.8"] / ra["correct"]["n"], ra["correct"]["n"], C_OK),
              ("Olist wrong\ndefinition", ra["definition_drift"]["pass_gate_at_0.8"] / ra["definition_drift"]["n"],
               ra["definition_drift"]["n"], C_DRIFT)]
    xs = np.arange(len(groups))
    a2.bar(xs, [100 * g[1] for g in groups], color=[g[3] for g in groups], width=0.62)
    for i, g in enumerate(groups):
        a2.text(xs[i], 100 * g[1] + 2, f"{100 * g[1]:.0f}%\n(n={g[2]})", ha="center", fontsize=8, color=INK)
    a2.set_xticks(xs, [g[0] for g in groups], fontsize=8.5)
    a2.set_ylim(0, 112)
    a2.set_ylabel("% passing the confidence gate")
    a2.set_title("Confidence (>= 4 of 5 samples agree) lets them through", fontsize=10, color=INK, loc="left")
    _style(a2)
    fig.text(0.01, 0.01, "Haiku 5.5. Left: 30 Olist metric questions per arm. Right: BIRD sub200 (n=200) and the Olist "
             "raw-SQL arm, 5 samples each, agreement on execution results.", fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(IMG / "hero.png")
    plt.close(fig)


def abstention() -> None:
    st = json.loads((RESULTS / "stats.json").read_text())["abstention"]
    ops = st["operating_points"]
    fig, ax = plt.subplots(figsize=(6.0, 3.6), dpi=150)
    pts = [(100.0, st["ex_all"], None)] + [(o["coverage"], o["ex_answered"], o) for o in ops]
    pts.sort()
    for c, ex, o in pts:
        ax.scatter(c, ex, color=C_ACCENT, s=36, zorder=3)
        if o:
            lo, hi = st["ex_all"] + o["gain_ci95_pp"][0], st["ex_all"] + o["gain_ci95_pp"][1]
            ax.vlines(c, lo, hi, color=C_ACCENT, alpha=0.35, lw=3)
            ax.annotate(f"answer if agreement >= {o['answer_if_agreement_at_least']:.1f}\n{c:.1f}% answered, EX {ex:.1f}",
                        (c, ex), xytext=(10, 4), textcoords="offset points", ha="left", fontsize=7.5, color=INK)
    ax.annotate(f"answer everything\nEX {st['ex_all']:.1f}", (100, st["ex_all"]), xytext=(-8, -24),
                textcoords="offset points", ha="right", fontsize=7.5, color=INK)
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=C_ACCENT, lw=1, alpha=0.6)
    ax.set_xlim(60, 102)
    ax.set_ylim(70, 95)
    ax.set_xlabel("% of questions answered")
    ax.set_ylabel("EX of answered questions (%)")
    ax.set_title("BIRD sub200, Haiku x5: abstaining at real thresholds (bars: 95% bootstrap CI)", fontsize=9, color=INK,
                 loc="left")
    ax.grid(color=GRID, lw=0.6)
    _style(ax)
    fig.tight_layout()
    fig.savefig(IMG / "abstention.png")
    plt.close(fig)


def frontier() -> None:
    st = json.loads((RESULTS / "stats.json").read_text())
    abl = json.loads((RESULTS / "ablations.json").read_text())["sub200"]
    from analytics_agent.eval.stats import PRICE

    def nocache(stem, model):
        r = load_stem(stem)
        ids = [f"bird_{i:03d}" for i in json.loads((ROOT / "eval_sets" / "sub200.json").read_text())["question_ids"]]
        u = [r[i]["usage"] for i in ids]
        return float(np.mean([(x.get("input_tokens", 0) * PRICE[model]["in"] + x.get("output_tokens", 0) * PRICE[model]["out"]) / 1e6
                              for x in u]))
    pts = []
    for s in abl:
        if s.get("missing") or s["model"] == "qwen":
            continue
        model = "claude-sonnet-5-5" if s["model"] == "sonnet" else "claude-haiku-5-5"
        label = s["label"].replace("haiku ", "Haiku ").replace("sonnet ", "Sonnet ")
        pts.append((label, s["ex"], nocache(s["stem"], model)))
    for r in st["cost"]["scenarios"]["none"]["routed"]:
        pts.append((f"routed: Haiku x5, escalate if agreement < {r['threshold']:.1f}", r["ex"], r["cost_per_q"]))
    fig, ax = plt.subplots(figsize=(7.6, 4.4), dpi=150)
    pts.sort(key=lambda p: p[2])
    pareto, best = [], -1
    for p in pts:
        if p[1] > best:
            pareto.append(p)
            best = p[1]
    ax.plot([p[2] for p in pareto], [p[1] for p in pareto], color=MUTED, lw=1, ls="--", zorder=1, label="Pareto frontier")
    for label, ex, cost in pts:
        col = "#e07b39" if "Sonnet" in label else ("#8e5bd8" if "routed" in label else C_ACCENT)
        ax.scatter(cost, ex, color=col, s=40, zorder=3)
        ax.annotate(label, (cost, ex), xytext=(6, -3), textcoords="offset points", fontsize=7, color=INK)
    ax.set_xscale("log")
    ax.set_xlim(9e-4, 6e-2)
    ax.set_xlabel("$ per question (explicit price sheet, no prompt caching; log scale)")
    ax.set_ylabel("BIRD EX on sub200 (%)")
    ax.set_title("Cost vs accuracy on the same 200 questions (run-vs-run noise: +/-5.1 pp)", fontsize=9.5, color=INK,
                 loc="left")
    ax.grid(color=GRID, lw=0.6)
    ax.legend(fontsize=7.5, frameon=False, loc="lower right")
    _style(ax)
    fig.tight_layout()
    fig.savefig(IMG / "frontier_cost.png")
    plt.close(fig)


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    for old in ("frontier_latency.png", "risk_coverage_bird_sub200.png", "risk_coverage_olist_golden_raw.png"):
        (IMG / old).unlink(missing_ok=True)
    hero()
    abstention()
    frontier()
    print("wrote", sorted(p.name for p in IMG.glob("*.png")))


if __name__ == "__main__":
    main()
