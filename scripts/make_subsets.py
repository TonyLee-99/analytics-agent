"""Freeze the evaluation subsets once (seeded, stratified by db_id x difficulty).
- sub200: used for Sonnet, qwen, self-consistency and routing (budget-limited runs)
- dev20 : prompt-development slice, disjoint from sub200 (reported numbers also exclude it)
Run: uv run python scripts/make_subsets.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import BIRD_QUESTIONS, EVAL_SETS  # noqa: E402


def stratified(rows, n, rng):
    strata = defaultdict(list)
    for r in rows:
        strata[(r["db_id"], r["difficulty"])].append(r["question_id"])
    total = len(rows)
    picked = []
    for _key, ids in sorted(strata.items()):
        rng.shuffle(ids)
        picked += ids[: max(1, round(n * len(ids) / total))]
    rng.shuffle(picked)
    return sorted(picked[:n])


def main():
    rows = [json.loads(x) for x in open(BIRD_QUESTIONS)]
    rng = random.Random(20261029)
    sub = stratified(rows, 200, rng)
    rest = [r for r in rows if r["question_id"] not in set(sub)]
    dev = stratified(rest, 20, rng)
    for name, ids, desc in [("sub200", sub, "stratified 200 of BIRD Mini-Dev 2026 (seed 20261029)"),
                            ("dev20", dev, "prompt-development slice, disjoint from sub200")]:
        (EVAL_SETS / f"{name}.json").write_text(json.dumps({"description": desc, "question_ids": ids}, indent=1))
        print(name, len(ids))


if __name__ == "__main__":
    main()
