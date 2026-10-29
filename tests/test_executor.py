import duckdb

from analytics_agent.executor import execute
from tests.conftest import copy


def test_duckdb_connection_is_read_only(toy_duck, tmp_path):
    path = copy(toy_duck, tmp_path / "c.duckdb")
    for sql in ("DROP TABLE orders", "DELETE FROM orders", "INSERT INTO orders VALUES ('x','x','x',1)",
                "CREATE TABLE t AS SELECT 1", "COPY orders TO 'out.csv'", "SELECT * FROM read_csv('/etc/passwd')",
                "SET enable_external_access = true"):
        r = execute(sql, "duckdb", path)
        assert not r.ok, sql
    con = duckdb.connect(path, read_only=True)
    assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 3


def test_sqlite_connection_is_read_only_and_cannot_attach(toy_sqlite, tmp_path):
    path = copy(toy_sqlite, tmp_path / "c.sqlite")
    for sql in ("DROP TABLE atom", "DELETE FROM atom", "UPDATE atom SET element='x'",
                f"ATTACH DATABASE '{tmp_path}/evil.db' AS evil", "PRAGMA writable_schema = ON",
                f"VACUUM INTO '{tmp_path}/copy.db'", "SELECT load_extension('x')"):
        assert not execute(sql, "sqlite", path).ok, sql
    assert not (tmp_path / "evil.db").exists() and not (tmp_path / "copy.db").exists()
    assert execute("SELECT count(*) FROM atom", "sqlite", path).rows == [(3,)]


def test_row_limit_and_truncation_flag(toy_duck):
    r = execute("SELECT * FROM range(5000)", "duckdb", toy_duck, row_limit=100)
    assert r.ok and r.n_rows == 100 and r.truncated


def test_sqlite_timeout(toy_sqlite):
    heavy = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    r = execute(heavy, "sqlite", toy_sqlite, timeout_s=0.5)
    assert not r.ok and r.timed_out


def test_duckdb_timeout(toy_duck):
    r = execute("SELECT count(*) FROM range(1000000000) a, range(100) b WHERE a.range % 7 = b.range",
                "duckdb", toy_duck, timeout_s=0.5)
    assert not r.ok and r.timed_out
