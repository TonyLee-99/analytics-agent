"""Run-to-run noise floor: how much does BIRD EX move between two runs of the *same* prompt?

The CLI samples at the API default temperature, so a "run" is one draw. Self-consistency already
produced 4 extra independent draws per question (samples 1-4, generation only, no self-correction)
for the frozen 200-question subset. Replaying the SC agent from the cache (0 new calls) recovers
each draw's SQL, which we score separately. The spread of EX across draws is the noise floor that
any ablation difference must beat (Uber reports ignoring ~5% swings for the same reason).
Run: AA_MAX_CLAUDE_CALLS=0 uv run python scripts/noise_floor.py
(cache-only: a question whose prompt is not in the cache raises BudgetExceeded and is skipped)
"""

import itertools
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.agent import Agent  # noqa: E402
from analytics_agent.config import RESULTS  # noqa: E402
from analytics_agent.eval.configs import get_config  # noqa: E402
from analytics_agent.eval.datasets import load_bird, subset_ids  # noqa: E402
from analytics_agent.eval.metrics import mcnemar_exact, score  # noqa: E402
from analytics_agent.eval.run import bird_examples  # noqa: E402
from analytics_agent.llm import make_client  # noqa: E402

tasks = load_bird(subset_ids("sub200"))
agent = Agent(make_client("haiku"), get_config("e_sc5"), bird_examples())
rows, skipped = [], 0
for t in tasks:
    try:
        ans = agent.ask(t.question, t.db_id, t.evidence, task_id=t.id)
    except Exception:  # noqa: BLE001 - cache miss under a zero call budget
        skipped += 1
        continue
    rows.append([score(t, sql)[0] if sql else 0 for sql in ans.signals["sample_sqls"]])
M = np.array(rows, dtype=int)
uncached = 0
n = len(M)
draws = M[:, 1:]  # samples 1-4: same prompt, independent draws, no correction
ex = 100 * draws.mean(0)
pairs = [mcnemar_exact(list(draws[:, a]), list(draws[:, b])) for a, b in itertools.combinations(range(4), 2)]
diff = [abs(ex[a] - ex[b]) for a, b in itertools.combinations(range(4), 2)]
flip = [(p["only_a"] + p["only_b"]) / n for p in pairs]
out = {"n_questions": n, "skipped_not_in_cache": skipped, "uncached_calls_during_replay": uncached,
       "ex_per_draw": [round(x, 1) for x in ex], "ex_mean": round(float(ex.mean()), 2), "ex_std": round(float(ex.std(ddof=1)), 2),
       "max_abs_diff_between_two_draws_pp": round(max(diff), 1),
       "share_of_questions_flipping_between_two_draws": round(float(np.mean(flip)), 3),
       "min_pairwise_mcnemar_p": round(min(p["p_value"] for p in pairs), 4),
       "oracle_any_of_5_correct": round(100 * float(M.max(1).mean()), 1)}
(RESULTS / "noise_floor.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
