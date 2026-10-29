"""Collect every number quoted in README.md from results/*.json into results/headline.json.

tests/test_readme_numbers.py fails if any of these values is missing from the README, so a re-run
that changes a result cannot leave a stale number behind.
Run: uv run python scripts/build_headline.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import RESULTS  # noqa: E402


def j(name):
    return json.loads((RESULTS / name).read_text())


st, sem, abl, rob = j("stats.json"), j("semantic_experiment__haiku.json"), j("ablations.json"), j("robustness.json")
lr, rt, amb, nf = j("learned_router.json"), j("redteam.json"), j("ambiguity__haiku.json"), j("noise_floor.json")
pairs = {(p["a"], p["b"], p["questions"]): p for p in st["semantic"]["pairs"]}
sig_pairs = [x for x in rob["label_noise"]["pairs"] if x["independent"]["significance_kept"] is not None]
ra = st["semantic"]["raw_arm_agreement"]
full = {s["label"][:3]: s for s in abl["full500"]}
sub = {s["label"]: s for s in abl["sub200"]}
ops = {o["answer_if_agreement_at_least"]: o for o in st["abstention"]["operating_points"]}
none = st["cost"]["scenarios"]["none"]
r08 = next(r for r in none["routed"] if r["threshold"] == 0.8)
r10 = next(r for r in none["routed"] if r["threshold"] == 1.0)
W1 = lr["by_wrong_answer_cost"]["W=$1.0"]

H = {
    # Olist semantic layer
    "raw_metric_correct": f"{pairs[('raw', 'glossary', 'metric')]['correct_a']}/30",
    "glossary_metric_correct": f"{pairs[('raw', 'glossary', 'metric')]['correct_b']}/30",
    "semantic_metric_correct": f"{pairs[('glossary', 'semantic', 'metric')]['correct_b']}/30",
    "raw_to_glossary_p": f"p={pairs[('raw', 'glossary', 'metric')]['p_value']:.3f}",
    "glossary_to_semantic_p": f"p={pairs[('glossary', 'semantic', 'metric')]['p_value']:.1f}",
    "raw_drift": f"{ra['definition_drift']['n']}/30",
    "drift_pass_gate": f"{ra['definition_drift']['pass_gate_at_0.8']} of {ra['definition_drift']['n']}",
    "raw_ex_all": f"{sem['arms']['olist_raw']['ex_all']:.1f}%",
    "glossary_ex_all": f"{sem['arms']['olist_glossary']['ex_all']:.1f}%",
    "semantic_ex_all": f"{sem['arms']['olist_semantic']['ex_all']:.1f}%",
    "hybrid_ex_all": f"{sem['arms']['olist_hybrid']['ex_all']:.1f}%",
    # abstention / routing
    "sc_ex_all": f"{st['abstention']['ex_all']:.1f}%",
    "unanimous_coverage": f"{ops[1.0]['coverage']:.1f}%",
    "unanimous_ex": f"{ops[1.0]['ex_answered']:.1f}%",
    "unanimous_gain_ci": f"+{ops[1.0]['gain_ci95_pp'][0]:.1f} to +{ops[1.0]['gain_ci95_pp'][1]:.1f}",
    "ge08_coverage": f"{ops[0.8]['coverage']:.1f}%",
    "ge08_ex": f"{ops[0.8]['ex_answered']:.1f}%",
    "auroc": f"{st['abstention']['auroc']:.2f}",
    "auroc_ci": f"{st['abstention']['auroc_ci95'][0]:.2f}–{st['abstention']['auroc_ci95'][1]:.2f}",
    "oracle_any5": f"{st['abstention']['oracle_any_of_5_correct']:.1f}%",
    "routed08_ex": f"{r08['ex']:.1f}%", "routed08_share": f"{100 * r08['share_of_sonnet_cost']:.0f}%",
    "routed10_ex": f"{r10['ex']:.1f}%", "routed10_share": f"{100 * r10['share_of_sonnet_cost']:.0f}%",
    "oracle_router": f"{st['cost']['oracle_router_ex']:.1f}%",
    "learned_W1_ex": f"{W1['learned']['ex']:.1f}%",
    "learned_auroc": f"{lr['oof_auroc_learned_mean']:.2f}",
    "sonnet_sub": f"{sub['sonnet (d) full pipeline']['ex']:.1f}%",
    "clarify_raw": f"{100 * amb['arms']['olist_raw']['clarify_rate_ambiguous']:.0f}%",
    "clarify_sem": f"{100 * amb['arms']['olist_semantic']['clarify_rate_ambiguous']:.0f}%",
    # BIRD
    "a_full": f"{full['(a)']['ex']:.1f}%", "d_full": f"{full['(d)']['ex']:.1f}%",
    "a_excl_dev20": f"{st['dev20']['a_zeroshot_official']['excl_dev20_n480']:.1f}",
    "d_excl_dev20": f"{st['dev20']['d_selfcorrect']['excl_dev20_n480']:.1f}",
    "noise_hw_200": f"±{st['noise']['half_width_95_pp_of_a_run_vs_run_delta']['200']:.1f}",
    "noise_hw_500": f"±{st['noise']['half_width_95_pp_of_a_run_vs_run_delta']['500']:.1f}",
    "flip_rate": f"{100 * st['noise']['flip_rate']:.1f}%",
    "subset_a_sub": f"{st['subset']['a_zeroshot_official']['sub200']:.1f}",
    "subset_a_rest": f"{st['subset']['a_zeroshot_official']['other300']:.1f}",
    "fewshot_far_n": str(st["fewshot"]["<0.5"]["n"]),
    "selfcorr_fixed": f"{st['vote']['selfcorrection_within_run']['fixed']} of {st['vote']['selfcorrection_within_run']['fired']}",
    "vote_gain": f"+{st['vote']['vote_gain_pp']:.1f}",
    # robustness
    "label_noise_sig_kept_min": f"{100 * min(x['independent']['significance_kept'] for x in sig_pairs):.0f}%",
    # safety
    "attacks": f"{rt['sql_layer']['blocked']}/{rt['sql_layer']['attacks']}",
    "v1_reid": f"{rt['confidentiality_layer']['v1_md5_replica']['reidentified']:,}",
    "gold_fp": f"0/{rt['sql_layer']['gold_queries_checked']}",
}
(RESULTS / "headline.json").write_text(json.dumps(H, indent=1, ensure_ascii=False))
for k, v in H.items():
    print(f"{k:24s} {v}")
