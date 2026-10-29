"""Error analysis: hand labels of 70 sampled failures of the zero-shot baseline (a), joined with the
outcome of the same questions under each later pipeline step, so we can see which change fixed which
error bucket. Writes results/error_analysis/summary.json and prints markdown tables.
Run: uv run python scripts/error_analysis.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import EVAL_SETS, RESULTS  # noqa: E402
from analytics_agent.eval.report import load_stem  # noqa: E402

labels = [json.loads(x) for x in open(EVAL_SETS / "bird_failure_labels.jsonl")]
STEPS = [("b", "bird__b_retrieval__haiku"), ("c", "bird__c_fewshot__haiku"), ("d", "bird__d_selfcorrect__haiku"),
         ("sonnet_d", "bird__d_selfcorrect__sonnet__sub200"), ("haiku_sc5", "bird__e_sc5__haiku__sub200")]
runs = {k: {i: r["ex"] for i, r in load_stem(stem).items()} for k, stem in STEPS}

n = len(labels)
prim = Counter(r["primary"] for r in labels)
gold = Counter(r["gold_issue"] for r in labels)
acc = sum(r["pred_acceptable"] for r in labels)
table = defaultdict(lambda: defaultdict(int))
for r in labels:
    b = r["primary"]
    table[b]["n"] += 1
    for k, _ in STEPS:
        if r["id"] in runs[k]:
            table[b][f"{k}_n"] += 1
            table[b][f"{k}_fixed"] += runs[k][r["id"]]
summary = {"n_labelled": n, "primary": dict(prim), "gold_issue": dict(gold), "pred_acceptable": acc,
           "by_bucket": {k: dict(v) for k, v in table.items()}}
out = RESULTS / "error_analysis" / "summary.json"
out.write_text(json.dumps(summary, indent=2))

print(f"labelled failures: {n}; gold issue: {dict(gold)}; baseline answer acceptable to a careful analyst: {acc}/{n}\n")
print("| bucket | n | fixed by (b) retrieval+our prompt | by (c) +few-shot | by (d) +self-correction | by Sonnet (d)* | by Haiku SC5* |")
print("|---|---|---|---|---|---|---|")
for b, v in sorted(table.items(), key=lambda kv: -kv[1]["n"]):
    cells = []
    for k, _ in STEPS:
        m = v.get(f"{k}_n", 0)
        cells.append(f"{v.get(f'{k}_fixed', 0)}/{m}" if m else "–")
    print(f"| {b} | {v['n']} | " + " | ".join(cells) + " |")
tot = [sum(v.get(f"{k}_fixed", 0) for v in table.values()) for k, _ in STEPS]
totn = [sum(v.get(f"{k}_n", 0) for v in table.values()) for k, _ in STEPS]
print(f"| **all** | {n} | " + " | ".join(f"{a}/{b}" for a, b in zip(tot, totn, strict=True)) + " |")
print("\n* Sonnet and SC5 were run on the frozen 200-question subset only, so only labelled failures inside it count.")
