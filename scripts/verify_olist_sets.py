"""Re-execute every SQL in the Olist evaluation sets and print a summary. Exit 1 on any failure.

Checks
- golden: gold_sql executes, is non-empty, and still matches the stored n_rows / answer_preview;
  every definition variant executes and still differs from gold.
- ambiguous: every interpretation executes; at least two interpretations differ.
- controls: gold_sql executes and is non-empty.
Run: uv run python scripts/verify_olist_sets.py
"""

from __future__ import annotations

import collections
import datetime as dt
import decimal
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "olist.duckdb"
SETS = ROOT / "eval_sets"


def load(name: str) -> list[dict]:
    with open(SETS / f"{name}.jsonl") as f:
        return [json.loads(line) for line in f]


def jsonable(v):
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


def preview(rows, n=3):
    return [[jsonable(v) for v in r] for r in rows[:n]]


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    problems: list[str] = []
    stats = collections.OrderedDict()

    golden = load("olist_golden")
    n_var = 0
    for r in golden:
        try:
            rows = con.execute(r["gold_sql"]).fetchall()
        except Exception as e:  # noqa: BLE001
            problems.append(f"{r['id']}: gold error {e}")
            continue
        if not rows:
            problems.append(f"{r['id']}: gold returned 0 rows")
        if len(rows) != r["n_rows"] or preview(rows) != r["answer_preview"]:
            problems.append(f"{r['id']}: result drifted from stored preview")
        for v in r.get("variants", []):
            n_var += 1
            try:
                vrows = con.execute(v["sql"]).fetchall()
            except Exception as e:  # noqa: BLE001
                problems.append(f"{r['id']}/{v['label']}: variant error {e}")
                continue
            if vrows == rows:
                problems.append(f"{r['id']}/{v['label']}: variant equals gold")
    stats["golden questions"] = len(golden)
    stats["golden metric questions"] = sum(1 for r in golden if r["uses_metric"])
    stats["golden definition variants"] = n_var
    stats["paraphrase groups"] = len({r["paraphrase_group"] for r in golden if r["paraphrase_group"]})

    amb = load("olist_ambiguous")
    n_int = 0
    for r in amb:
        results = []
        for it in r["interpretations"]:
            n_int += 1
            try:
                results.append(con.execute(it["sql"]).fetchall())
            except Exception as e:  # noqa: BLE001
                problems.append(f"{r['id']}/{it['label']}: interpretation error {e}")
        if len(r["interpretations"]) < 2 or len({repr(x) for x in results}) < 2:
            problems.append(f"{r['id']}: interpretations do not differ")
    stats["ambiguous questions"] = len(amb)
    stats["ambiguous interpretations"] = n_int

    ctl = load("olist_clear_controls")
    for r in ctl:
        try:
            if not con.execute(r["gold_sql"]).fetchall():
                problems.append(f"{r['id']}: control returned 0 rows")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{r['id']}: control error {e}")
    stats["clear controls"] = len(ctl)

    print(f"{'check':32s} value")
    for k, v in stats.items():
        print(f"{k:32s} {v}")
    print("by category:", dict(collections.Counter(r["category"] for r in golden)))
    print("by difficulty:", dict(collections.Counter(r["difficulty"] for r in golden)))
    if problems:
        print(f"\nFAILED ({len(problems)}):")
        for p in problems:
            print("  -", p)
        return 1
    print("\nall SQL executed OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
