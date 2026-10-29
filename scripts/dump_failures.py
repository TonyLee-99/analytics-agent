"""Sample failures of a run for hand labelling (error analysis).
Writes results/error_analysis/<run>__sample.jsonl with question, hint, gold/pred SQL and result heads.
Run: uv run python scripts/dump_failures.py bird__a_zeroshot_official__haiku 70
"""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import RESULTS  # noqa: E402
from analytics_agent.eval.datasets import load_bird  # noqa: E402
from analytics_agent.eval.report import load_stem  # noqa: E402
from analytics_agent.executor import execute  # noqa: E402

stem, n = sys.argv[1], int(sys.argv[2])
tasks = {t.id: t for t in load_bird()}
recs = list(load_stem(stem).values())
fails = sorted([r for r in recs if not r["ex"]], key=lambda r: r["id"])
random.Random(2026).shuffle(fails)
out = RESULTS / "error_analysis" / f"{stem}__sample.jsonl"
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w") as f:
    for r in fails[:n]:
        t = tasks[r["id"]]
        g = execute(t.gold_sql, "sqlite", t.db_path, row_limit=8)
        p = execute(r["pred_sql"], "sqlite", t.db_path, row_limit=8) if r["pred_sql"] else None
        f.write(json.dumps({
            "id": r["id"], "db_id": t.db_id, "difficulty": t.difficulty, "question": t.question,
            "evidence": t.evidence, "gold_sql": t.gold_sql, "pred_sql": r["pred_sql"],
            "gold_head": [list(map(str, x)) for x in g.rows], "gold_truncated": g.truncated,
            "pred_head": [list(map(str, x)) for x in p.rows] if p and p.ok else None,
            "pred_error": (p.error if p and not p.ok else "") or ("no SQL" if not r["pred_sql"] else ""),
        }) + "\n")
print(f"{len(fails)} failures, sampled {min(n, len(fails))} -> {out}")
