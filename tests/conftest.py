"""Shared fixtures. The toy databases below are *unit-test fixtures only* (a few hand-made rows to
exercise code paths offline in CI); no reported number comes from them."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from analytics_agent.catalog import Column, DatabaseCatalog, Table, build_duckdb_catalog, register_catalog
from analytics_agent.config import BIRD_DB_ROOT, OLIST_DB

HAS_OLIST = OLIST_DB.exists()
HAS_BIRD = (BIRD_DB_ROOT / "toxicology" / "toxicology.sqlite").exists()


@pytest.fixture(scope="session")
def toy_duck(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("db") / "toy.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE orders (order_id VARCHAR, customer_id VARCHAR, order_status VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO orders VALUES ('o1','c1','delivered',10.0),('o2','c2','canceled',5.0),"
                "('o3','c1','delivered',7.5)")
    con.execute("CREATE TABLE customers (customer_id VARCHAR, customer_state VARCHAR)")
    con.execute("INSERT INTO customers VALUES ('c1','SP'),('c2','RJ')")
    con.close()
    register_catalog(build_duckdb_catalog(path, "toy"))
    return path


@pytest.fixture(scope="session")
def toy_sqlite(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("db") / "toy.sqlite")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE atom (atom_id TEXT, molecule_id TEXT, element TEXT)")
    con.executemany("INSERT INTO atom VALUES (?,?,?)", [("a1", "m1", "c"), ("a2", "m1", "o"), ("a3", "m2", "c")])
    con.commit()
    con.close()
    cat = DatabaseCatalog("toy_sqlite", "sqlite", path, [
        Table("atom", [Column("atom_id", "TEXT", pk=True), Column("molecule_id", "TEXT"), Column("element", "TEXT")])])
    register_catalog(cat)
    return path


def copy(src: str, dst: Path) -> str:
    dst.write_bytes(Path(src).read_bytes())
    return str(dst)
