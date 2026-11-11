"""Create the copilot database and load the bounded 311 window from the chicago-311-warehouse
DuckDB build (opened READ-ONLY; that repo is never modified).

    uv run python scripts/v2/pg_load.py            # full window (~3.9M rows, a few minutes)
    uv run python scripts/v2/pg_load.py --fixture  # load tests/v2/fixtures/sr_sample.csv.gz instead
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
import time

import duckdb
import psycopg

from analytics_agent.copilot.departments import DEPT_CODES
from analytics_agent.copilot.settings import ROOT, SOURCE_WAREHOUSE, WINDOW_END, WINDOW_START

SCHEMA = ROOT / "src" / "analytics_agent" / "copilot" / "schema.sql"
FIXTURE = ROOT / "tests" / "v2" / "fixtures" / "sr_sample.csv.gz"
FIXTURE_DIMS = ROOT / "tests" / "v2" / "fixtures" / "dims.json"
COLS = ["sr_number", "sr_type", "sr_short_code", "dept_code", "created_department", "origin", "status", "is_open",
        "is_canceled", "is_duplicate", "parent_sr_number", "created_at", "closed_at", "last_modified_at",
        "community_area", "ward"]

SOURCE_SQL = f"""
SELECT f.sr_number, rt.sr_type, rt.sr_short_code, d.department AS owner_department, cd.department AS created_department,
       o.origin, f.status, f.is_open, f.is_canceled, f.is_duplicate, f.parent_sr_number,
       f.created_at, f.closed_at, f.last_modified_at, ca.community_area, w.ward
FROM main.fct_service_request f
LEFT JOIN main.dim_request_type rt USING (request_type_key)
LEFT JOIN main.dim_department d ON d.department_key = f.owner_department_key
LEFT JOIN main.dim_department cd ON cd.department_key = f.created_department_key
LEFT JOIN main.dim_origin o USING (origin_key)
LEFT JOIN main.dim_community_area ca USING (community_area_key)
LEFT JOIN main.dim_ward w USING (ward_key)
WHERE f.created_at >= TIMESTAMP '{WINDOW_START}' AND f.created_at < TIMESTAMP '{WINDOW_END}'
"""


def ensure_db(admin_base: str, db: str) -> None:
    with psycopg.connect(admin_base + " dbname=postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
            c.execute(f'CREATE DATABASE "{db}"')


def load_dims(pg: psycopg.Connection, src: duckdb.DuckDBPyConnection | None) -> dict:
    import json

    if src is None:
        dims = json.loads(FIXTURE_DIMS.read_text())
    else:
        dims = {
            "community_area": src.execute("SELECT community_area, community_area_name, population_acs "
                                          "FROM main.dim_community_area WHERE community_area IS NOT NULL").fetchall(),
            "benchmark": src.execute("SELECT sr_type_upper, benchmark_days FROM reference.ref_resolution_benchmark")
            .fetchall(),
        }
    with pg.cursor() as cur:
        cur.executemany("INSERT INTO wh.department VALUES (%s, %s)", [(c, n) for n, c in DEPT_CODES.items()])
        cur.executemany("INSERT INTO wh.community_area VALUES (%s, %s, %s)", [tuple(r) for r in dims["community_area"]])
        cur.executemany("INSERT INTO wh.request_benchmark VALUES (%s, %s)", [tuple(r) for r in dims["benchmark"]])
    return dims


def rows_from_source(src: duckdb.DuckDBPyConnection):
    cur = src.execute(SOURCE_SQL)
    while True:
        batch = cur.fetchmany(50_000)
        if not batch:
            return
        for r in batch:
            (sr, sr_type, code, owner, created_dept, origin, status, is_open, is_canceled, is_dup, parent,
             created, closed, modified, ca, ward) = r
            yield (sr, sr_type, code, DEPT_CODES[owner or "Unknown"], created_dept, origin, status, is_open,
                   is_canceled, is_dup, parent, created, closed, modified, ca, ward)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--admin", default=os.environ.get("AA_PG_ADMIN_BASE", "host=localhost port=5433 user=postgres"))
    ap.add_argument("--db", default="copilot")
    args = ap.parse_args()
    ensure_db(args.admin, args.db)
    t0 = time.time()
    with psycopg.connect(args.admin + f" dbname={args.db}") as pg:
        pg.execute(SCHEMA.read_text())
        src = None if args.fixture else duckdb.connect(str(SOURCE_WAREHOUSE), read_only=True)
        load_dims(pg, src)
        n = 0
        with pg.cursor().copy(f"COPY wh.service_request ({', '.join(COLS)}) FROM STDIN") as cp:
            if src is None:
                with gzip.open(FIXTURE, "rt", newline="") as f:
                    for row in csv.DictReader(f):
                        cp.write_row([row[c] if row[c] != "" else None for c in COLS])
                        n += 1
            else:
                for row in rows_from_source(src):
                    cp.write_row(row)
                    n += 1
                    if n % 500_000 == 0:
                        print(f"  {n:,} rows ({time.time() - t0:.0f}s)", flush=True)
        for ddl in ("CREATE INDEX ON wh.service_request (dept_code, created_at)",
                    "CREATE INDEX ON wh.service_request (created_at)",
                    "CREATE INDEX ON wh.service_request (sr_type)"):
            pg.execute(ddl)
        pg.commit()
        pg.execute("ANALYZE")
    from analytics_agent.copilot.warehouse import setup_security

    setup_security(args.admin + f" dbname={args.db}")
    print(f"loaded {n:,} rows in {time.time() - t0:.0f}s; security policies applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
