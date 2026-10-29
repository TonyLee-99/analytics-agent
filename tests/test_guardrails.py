import pytest

from analytics_agent.eval.redteam import ATTACKS, BENIGN_TRICKY
from analytics_agent.guardrails import validate_sql

OLIST_TABLES = ["orders", "customers", "order_items", "sellers", "geolocation"]
SQLITE_TABLES = ["atom", "bond", "molecule", "connected"]


@pytest.mark.parametrize("aid,dialect,sql,cat", ATTACKS, ids=[a[0] for a in ATTACKS])
def test_every_attack_is_blocked(aid, dialect, sql, cat):
    tables = OLIST_TABLES if dialect == "duckdb" else SQLITE_TABLES
    v = validate_sql(sql, dialect, allowed_tables=tables, row_limit=1000)
    assert not v.ok, f"{aid} was not blocked"
    assert v.errors


@pytest.mark.parametrize("bid,dialect,sql", BENIGN_TRICKY, ids=[b[0] for b in BENIGN_TRICKY])
def test_benign_lookalikes_are_allowed(bid, dialect, sql):
    tables = OLIST_TABLES if dialect == "duckdb" else SQLITE_TABLES
    v = validate_sql(sql, dialect, allowed_tables=tables + ["x"], row_limit=1000)
    assert v.ok, v.errors


def test_limit_is_injected_and_clamped():
    v = validate_sql("SELECT * FROM orders", "duckdb", OLIST_TABLES, row_limit=50)
    assert v.ok and "LIMIT 50" in v.sql and v.warnings
    v = validate_sql("SELECT * FROM orders LIMIT 10", "duckdb", OLIST_TABLES, row_limit=50)
    assert v.sql.strip().endswith("LIMIT 10")
    v = validate_sql("SELECT * FROM orders LIMIT 100000", "duckdb", OLIST_TABLES, row_limit=50)
    assert "LIMIT 50" in v.sql


def test_unknown_table_and_parse_error():
    assert "unknown table" in validate_sql("SELECT * FROM payroll", "duckdb", OLIST_TABLES).reason
    assert not validate_sql("SELEC * FRM orders", "duckdb", OLIST_TABLES).ok
    assert not validate_sql("", "duckdb", OLIST_TABLES).ok


def test_cte_names_are_not_treated_as_tables():
    v = validate_sql("WITH big AS (SELECT * FROM orders) SELECT count(*) FROM big", "duckdb", OLIST_TABLES)
    assert v.ok and v.tables == ["orders"]


def test_cartesian_join_is_flagged_not_blocked():
    v = validate_sql("SELECT count(*) FROM orders, customers", "duckdb", OLIST_TABLES)
    assert v.ok
