"""Collect every v2 number quoted in the README into results/v2/headline.json (a test diffs the README)."""

import json

from analytics_agent.copilot.settings import V2_RESULTS


def pct(x):
    return f"{round(100 * x)}%"


def load(n):
    p = V2_RESULTS / n
    return json.loads(p.read_text()) if p.exists() else None


ev, r1, rf, lag = load("eval.json"), load("redteam_round1.json"), load("redteam_roundfinal.json"), load("lag_window.json")
abl, cal, pgv = load("redteam_roundablation_norecheck_nodelivery.json"), load("judge_calibration.json"), load("pg_verify.json")
cp, do, v1 = ev["systems"]["copilot"], ev["systems"]["docs_only"], ev["systems"]["v1_sql"]
hold = rf["summary"]["by_author"].get("codex-blind-holdout", {})
h = {
    "rows": f"{pgv['rows_postgres']:,}",
    "rt_round1": f"{r1['summary']['leaked']}/{r1['summary']['scenarios']}",
    "rt_final": f"{rf['summary']['leaked']}/{rf['summary']['scenarios']}",
    "rt_holdout": f"{hold.get('leaked')}/{hold.get('n')}",
    "rt_ablation": f"{abl['summary']['leaked']}/{abl['summary']['scenarios']}",
    "lag_off": f"{lag['summary']['off_leaks_inside_window']}/{lag['summary']['inside_window_probes_per_mode']}",
    "n_eval": str(cp["all"]["n"]),
    "acc_all": pct(cp["all"]["accuracy"]), "acc_author": pct(cp["author"]["accuracy"]),
    "acc_codex": pct(cp["codex_blind"]["accuracy"]), "acc_holdout": pct(cp["codex_blind_holdout"]["accuracy"]),
    "acc_docs_only": pct(do["all"]["accuracy"]), "acc_v1": pct(v1["all"]["accuracy"]),
    "route": pct(cp["route_accuracy"]), "recall5": pct(cp["recall_at_5"]),
    "clarify": pct(cp["clarify_rate_on_conflicts"]),
    "false_clarify": pct(cp["false_clarify_on_explicit_definitions"]),
    "kappa": str(cal["cohen_kappa"]),
}
(V2_RESULTS / "headline.json").write_text(json.dumps(h, indent=1))
print(json.dumps(h))
