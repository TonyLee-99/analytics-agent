"""Orchestrate benchmark runs in order, retrying incomplete runs (rate limits) until done.

    uv run python scripts/run_evals.py --plan full    # everything in the README tables
    uv run python scripts/run_evals.py --plan small   # 20-question smoke eval (make eval-small)
    uv run python scripts/run_evals.py --plan qwen    # local model runs (slow, ~1 min/question)
All LLM responses are cached, so re-running a finished plan costs nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.eval import run as R  # noqa: E402

PLANS = {
    "small": [("bird", "d_selfcorrect", "haiku", "dev20", 4, None)],
    "full": [
        ("bird", "a_zeroshot_official", "haiku", None, 4, None),
        ("bird", "b_retrieval", "haiku", None, 4, None),
        ("bird", "c_fewshot", "haiku", None, 4, None),
        ("bird", "d_selfcorrect", "haiku", None, 4, None),
    ],  # then run --plan sub (Sonnet, a2, self-consistency on the frozen subset)
    "sub": [  # budget-limited runs on the frozen subset (can run in parallel with "full")
        ("bird", "d_selfcorrect", "sonnet", "sub200", 4, None),
        ("bird", "a2_ourprompt_full", "haiku", "sub200", 4, None),
        ("bird", "e_sc5", "haiku", "sub200", 1, None),
    ],
    "qwen": [
        ("bird", "d_selfcorrect", "qwen", "sub200", 1, None),
        ("bird", "a_zeroshot_official", "qwen", "sub200", 1, None),
    ],
}


def expected(suite: str, subset: str | None) -> int:
    return len(R.load_tasks(suite, subset))


def done(suite, config, model, subset) -> int:
    p = R.run_path(suite, config, model, subset)
    return sum(1 for _ in open(p)) if p.exists() else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="small", choices=sorted(PLANS))
    ap.add_argument("--max-rounds", type=int, default=6)
    args = ap.parse_args()
    for suite, config, model, subset, workers, n in PLANS[args.plan]:
        need = expected(suite, subset)
        for rnd in range(args.max_rounds):
            have = done(suite, config, model, subset)
            if have >= need:
                break
            argv = ["--suite", suite, "--config", config, "--model", model, "--workers", str(workers)]
            if subset:
                argv += ["--subset", subset]
            if n:
                argv += ["--n-samples", str(n)]
            if model == "qwen":
                argv += ["--shuffle"]
            try:
                R.main(argv)
            except Exception as e:  # noqa: BLE001
                print(f"run error: {e}")
                if "budget" in str(e).lower():
                    return
            if done(suite, config, model, subset) == have:  # no progress: probably rate limited
                wait = 600 * (rnd + 1)
                print(f"no progress on {config}/{model}; sleeping {wait}s")
                time.sleep(wait)
        print(json.dumps({"finished": [suite, config, model, subset], "rows": done(suite, config, model, subset)}))


if __name__ == "__main__":
    main()
