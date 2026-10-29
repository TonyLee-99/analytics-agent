"""Regression tests for the three holes found in the external review (R1):
1. zip tokens were invertible (unsalted md5 of a 5-digit value),
2. a CTE named like a system table hid the real system table from the allowlist,
3. the row cap bounded rows but not bytes (memory DoS)."""

import importlib.util
import sys

import pytest

from analytics_agent.executor import execute
from analytics_agent.guardrails import validate_sql
from tests.conftest import HAS_OLIST


def _build_module():
    from analytics_agent.config import ROOT

    spec = importlib.util.spec_from_file_location("build_olist", ROOT / "scripts" / "build_olist.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_olist"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_zip_token_is_keyed_and_not_a_plain_hash():
    import hashlib

    b = _build_module()
    t1, t2 = b.zip_token(b"k1" * 16, "01037"), b.zip_token(b"k2" * 16, "01037")
    assert t1 != t2 and t1.startswith("zip_") and len(t1) == 20
    for h in (hashlib.md5, hashlib.sha1, hashlib.sha256):
        assert h(b"01037").hexdigest()[:16] not in t1


@pytest.mark.skipif(not HAS_OLIST, reason="warehouse not built")
def test_zip_dictionary_attack_reidentifies_nobody():
    from analytics_agent.eval.redteam import confidentiality_layer

    rep = confidentiality_layer()
    assert rep["current_reidentified_max"] == 0
    assert rep["v1_md5_replica"]["reidentified"] == rep["v1_md5_replica"]["customers"]  # the attack is real


@pytest.mark.parametrize("sql", [
    "WITH sqlite_master AS (SELECT 1 AS x) SELECT type, name, sql FROM main.sqlite_master",
    "WITH tables AS (SELECT 1) SELECT table_name FROM information_schema.tables",
    "WITH pg_settings AS (SELECT 1) SELECT name FROM pg_catalog.pg_settings",
    "WITH customers AS (SELECT 1) SELECT * FROM raw.customers",
    "WITH x AS (SELECT * FROM x) SELECT * FROM x",  # non-recursive self-reference = the base table in DuckDB
    "WITH a AS (SELECT * FROM b), b AS (SELECT 1) SELECT * FROM a",  # forward reference is not a CTE
])
def test_cte_shadowing_cannot_hide_tables(sql):
    assert not validate_sql(sql, "duckdb", allowed_tables=["orders", "customers"], row_limit=1000).ok


@pytest.mark.parametrize("sql", [
    "WITH RECURSIVE n(i) AS (SELECT 0 UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT * FROM n",
    "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT * FROM b",
    "WITH x AS (SELECT 1 AS v) SELECT * FROM (SELECT * FROM x) AS s JOIN orders ON 1 = 1",
])
def test_legitimate_ctes_still_pass(sql):
    v = validate_sql(sql, "duckdb", allowed_tables=["orders", "customers"], row_limit=1000)
    assert v.ok, v.errors


@pytest.mark.parametrize("sql,dialect", [
    ("SELECT zeroblob(500000) FROM atom", "sqlite"),
    ("SELECT printf('%.*c', 900000000, 'x')", "sqlite"),
    ("SELECT repeat('x', 500000) FROM orders", "duckdb"),
    ("SELECT repeat('x', order_id) FROM orders", "duckdb"),
    ("SELECT lpad('a', 900000000, 'b')", "duckdb"),
])
def test_memory_bombs_rejected_by_validator(sql, dialect):
    assert not validate_sql(sql, dialect, allowed_tables=["orders", "atom"], row_limit=1000).ok


def test_memory_bombs_bounded_without_validator(toy_sqlite, toy_duck):
    r = execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 1000) "
                "SELECT zeroblob(500000) FROM n", "sqlite", toy_sqlite)
    assert not r.ok and "exceeds" in r.error
    r = execute("SELECT length(printf('%.*c', 900000000, 'x'))", "sqlite", toy_sqlite)
    assert r.ok and r.rows == [(None,)]  # SQLITE_LIMIT_LENGTH: the engine refuses to build the string
    r = execute("SELECT repeat('x', 500000) FROM range(1000)", "duckdb", toy_duck)
    assert not r.ok and "exceeds" in r.error


def test_scorer_path_is_not_size_capped(toy_duck):
    from analytics_agent.executor import execute_all

    rows, err = execute_all("SELECT repeat('x', 30000) FROM range(1000)", "duckdb", toy_duck)
    assert rows is not None and len(rows) == 1000, err


def test_negative_limit_is_clamped():
    v = validate_sql("SELECT * FROM orders LIMIT -1", "sqlite", ["orders"], row_limit=1000)
    assert v.ok and v.sql.rstrip().endswith("LIMIT 1000")
