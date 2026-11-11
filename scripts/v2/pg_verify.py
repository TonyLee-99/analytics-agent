"""A1: the Postgres copy equals the source build for the window, per department; boundaries hold.

    uv run python scripts/v2/pg_verify.py   # -> results/v2/pg_verify.json, exit 1 on any mismatch
"""

import json
import sys

import duckdb
import psycopg

from analytics_agent.copilot.departments import DEPT_CODES
from analytics_agent.copilot.settings import (
    PG_ADMIN_DSN,
    SOURCE_WAREHOUSE,
    V2_RESULTS,
    WINDOW_END,
    WINDOW_START,
)

src = duckdb.connect(str(SOURCE_WAREHOUSE), read_only=True)
s_rows = src.execute(f"""SELECT d.department, count(*) FROM main.fct_service_request f
    LEFT JOIN main.dim_department d ON d.department_key = f.owner_department_key
    WHERE f.created_at >= TIMESTAMP '{WINDOW_START}' AND f.created_at < TIMESTAMP '{WINDOW_END}' GROUP BY 1""").fetchall()
source = {DEPT_CODES[n or "Unknown"]: c for n, c in s_rows}
with psycopg.connect(PG_ADMIN_DSN) as c:
    pg = dict(c.execute("SELECT dept_code, count(*) FROM wh.service_request GROUP BY 1").fetchall())
    lo, hi = c.execute("SELECT min(created_at), max(created_at) FROM wh.service_request").fetchone()
mism = {k: (source.get(k), pg.get(k)) for k in set(source) | set(pg) if source.get(k) != pg.get(k)}
ok = not mism and str(lo) >= WINDOW_START and str(hi) < WINDOW_END
out = {"ok": ok, "rows_source": sum(source.values()), "rows_postgres": sum(pg.values()), "departments": len(pg),
       "mismatches": mism, "min_created_at": str(lo), "max_created_at": str(hi), "window": [WINDOW_START, WINDOW_END]}
V2_RESULTS.mkdir(parents=True, exist_ok=True)
(V2_RESULTS / "pg_verify.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out))
sys.exit(0 if ok else 1)
