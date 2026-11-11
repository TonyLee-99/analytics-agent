"""Execute each question's gold SQL as the owner, restricted to the asking user's departments under the
policy model (config/org.yaml), and freeze the expected rows: eval_sets/v2/gold_<set>.json.

    uv run python scripts/v2/build_gold.py questions_author questions_codex
"""

from __future__ import annotations

import hashlib
import json
import sys

from analytics_agent.copilot.identity import Directory
from analytics_agent.copilot.settings import V2_EVAL_SETS
from analytics_agent.copilot.warehouse import oracle_query


def jsonable(v):
    from decimal import Decimal

    if isinstance(v, Decimal | float):
        return round(float(v), 6)
    return v if v is None or isinstance(v, bool | int | str) else str(v)


def main(names: list[str]) -> int:
    st = Directory.from_config().state
    bad = 0
    for name in names:
        path = V2_EVAL_SETS / f"{name}.jsonl"
        items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        gold = {"set": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "items": {}}
        for q in items:
            if not q.get("gold_sql"):
                continue
            depts = st.data_departments(q["user"])
            r = oracle_query(q["gold_sql"], depts)
            rows = [[jsonable(v) for v in row] for row in r.rows]
            empty = r.ok and (not rows or all(v in (None, 0) for row in rows for v in row))
            if not r.ok or empty:
                bad += 1
                print(f"{q['id']}: {'ERROR ' + r.error if not r.ok else 'EMPTY/ZERO'} :: {q['question'][:80]}")
            gold["items"][q["id"]] = {"ok": r.ok, "error": r.error, "columns": r.columns, "rows": rows,
                                      "depts": sorted(depts)}
        (V2_EVAL_SETS / f"gold_{name}.json").write_text(json.dumps(gold, indent=1))
        print(f"{name}: {len(gold['items'])} gold results")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["questions_author"]))
