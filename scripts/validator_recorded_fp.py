"""How many of the agent's recorded SQL queries (all run files) would the current validator reject?
A guardrail change must not silently break queries the agent already answered correctly.
Run: uv run python scripts/validator_recorded_fp.py"""

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.catalog import get_catalog  # noqa: E402
from analytics_agent.config import RESULTS, RUNS  # noqa: E402
from analytics_agent.guardrails import validate_sql  # noqa: E402

n = rejected = rejected_correct = 0
examples = []
for f in sorted(RUNS.glob("*.jsonl.gz")) or sorted(RUNS.glob("*.jsonl")):
    opener = gzip.open if f.suffix == ".gz" else open
    for line in opener(f, "rt"):
        r = json.loads(line)
        sql = r.get("pred_sql") or ""
        if not sql:
            continue
        n += 1
        d = "duckdb" if r["db_id"] == "olist" else "sqlite"
        v = validate_sql(sql, d, get_catalog(r["db_id"]).table_names, 1000)
        if not v.ok:
            rejected += 1
            rejected_correct += r["ex"]
            examples.append({"run": f.name, "id": r["id"], "ex": r["ex"], "error": v.errors[0][:100]})
out = {"recorded_queries": n, "rejected_now": rejected, "rejected_now_that_were_correct": rejected_correct,
       "examples": examples[:30]}
(RESULTS / "validator_recorded_fp.json").write_text(json.dumps(out, indent=2))
print({k: v for k, v in out.items() if k != "examples"})
