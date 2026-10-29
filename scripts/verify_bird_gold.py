"""A1: every BIRD Mini-Dev gold query runs on the downloaded DBs through the hardened executor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analytics_agent.eval.datasets import load_bird  # noqa: E402
from analytics_agent.executor import execute  # noqa: E402

errors = empty = 0
tasks = load_bird()
for t in tasks:
    r = execute(t.gold_sql, "sqlite", t.db_path, timeout_s=120, row_limit=10**7)
    errors += not r.ok
    empty += r.ok and r.n_rows == 0
print(f"BIRD gold SQL: {len(tasks)} queries, {errors} errors, {empty} empty")
sys.exit(1 if errors or empty else 0)
