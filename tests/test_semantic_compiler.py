"""The governed-metric compiler must reproduce the hand-written gold SQL of the Olist golden set.

Each spec below was written by hand (what a perfect semantic-layer agent would emit) and is compared
against the independently hand-written gold_sql in eval_sets/olist_golden.jsonl.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pytest

from analytics_agent.semantic.compiler import SemanticError, compile_query

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "olist.duckdb"
GOLDEN = ROOT / "eval_sets" / "olist_golden.jsonl"

Y2017 = {"start": "2017-01-01", "end": "2018-01-01"}
Y2018 = {"start": "2018-01-01", "end": "2019-01-01"}

SPECS = {
    "olist_001": {"metrics": ["gmv"], "time_range": Y2017},
    "olist_002": {"metrics": ["gmv"], "time_range": Y2017},
    "olist_004": {"metrics": ["on_time_delivery_rate"], "time_range": Y2018},
    "olist_007": {"metrics": ["aov"], "time_range": Y2018},
    "olist_010": {"metrics": ["repeat_purchase_rate"]},
    "olist_013": {"metrics": ["gmv"], "dimensions": ["customer_state"], "time_range": Y2018,
                  "order_by": [{"field": "gmv", "desc": True}], "limit": 5},
    "olist_014": {"metrics": ["gmv"], "time_grain": "month", "time_range": Y2017},
    "olist_015": {"metrics": ["orders"], "time_range": Y2017},
    "olist_016": {"metrics": ["active_customers"], "time_range": Y2018},
    "olist_017": {"metrics": ["cancellation_rate"]},
    "olist_018": {"metrics": ["avg_delivery_days"], "time_range": Y2017},
    "olist_019": {"metrics": ["avg_delay_vs_promised_days"], "time_range": Y2018},
    "olist_020": {"metrics": ["nps_style_score"], "time_range": Y2018},
    "olist_021": {"metrics": ["freight_to_price_ratio"]},
    "olist_022": {"metrics": ["top10pct_seller_gmv_share"]},
    "olist_023": {"metrics": ["seller_gmv_hhi"]},
    "olist_024": {"metrics": ["on_time_delivery_rate"], "dimensions": ["customer_state"], "time_range": Y2018,
                  "order_by": [{"field": "on_time_delivery_rate", "desc": False}], "limit": 5},
    "olist_025": {"metrics": ["gmv"], "dimensions": ["product_category"], "time_range": Y2018,
                  "order_by": [{"field": "gmv", "desc": True}], "limit": 5},
    "olist_027": {"metrics": ["gmv"], "time_grain": "month", "time_range": Y2018,
                  "order_by": [{"field": "gmv", "desc": True}], "limit": 1},
    "olist_030": {"metrics": ["gmv", "orders"], "time_range": Y2018,
                  "filters": [{"field": "customer_state", "op": "=", "value": "SP"}]},
}

needs_db = pytest.mark.skipif(not DB.exists(), reason="data/olist.duckdb not built (run scripts/build_olist.py)")


def _golden() -> dict[str, dict]:
    with open(GOLDEN) as f:
        return {r["id"]: r for r in map(json.loads, f)}


def _norm(v):
    if isinstance(v, dt.datetime):
        return v.date() if v.time() == dt.time(0) else v
    return v


def _rows_equal(a: list[tuple], b: list[tuple]) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        if len(ra) != len(rb):
            return False
        for x, y in zip(ra, rb, strict=True):
            x, y = _norm(x), _norm(y)
            if isinstance(x, int | float) and isinstance(y, int | float) and not isinstance(x, bool):
                if not math.isclose(float(x), float(y), rel_tol=1e-9, abs_tol=1e-9):
                    return False
            elif x != y:
                return False
    return True


@needs_db
@pytest.mark.parametrize("qid", sorted(SPECS))
def test_compiler_matches_hand_written_gold(qid):
    import duckdb

    gold = _golden()[qid]
    con = duckdb.connect(str(DB), read_only=True)
    expected = con.execute(gold["gold_sql"]).fetchall()
    got = con.execute(compile_query(SPECS[qid])).fetchall()
    assert _rows_equal(got, expected), f"{qid}: compiler {got[:3]} != gold {expected[:3]}"


def test_specs_cover_at_least_12_golden_questions():
    golden = _golden()
    assert len(SPECS) >= 12
    for qid, spec in SPECS.items():
        assert set(spec["metrics"]) <= set(golden[qid]["uses_metric"]), qid


def test_unknown_metric_rejected():
    with pytest.raises(SemanticError):
        compile_query({"metrics": ["revenue_with_freight"]})


def test_disallowed_dimension_rejected():
    with pytest.raises(SemanticError):
        compile_query({"metrics": ["repeat_purchase_rate"], "dimensions": ["product_category"]})


def test_filter_on_non_dimension_rejected():
    # filters may only reference governed dimensions -> no SQL injection through field names
    with pytest.raises(SemanticError):
        compile_query({"metrics": ["gmv"], "filters": [{"field": "1=1; DROP TABLE orders; --", "op": "=", "value": 1}]})


def test_filter_values_are_escaped():
    sql = compile_query({"metrics": ["gmv"], "filters": [{"field": "customer_state", "op": "=", "value": "SP' OR '1'='1"}]})
    assert "'SP'' OR ''1''=''1'" in sql


def test_custom_sql_metric_refuses_dimensions():
    with pytest.raises(SemanticError):
        compile_query({"metrics": ["seller_gmv_hhi"], "dimensions": ["customer_state"]})
