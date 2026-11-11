"""Fixtures for v2 tests.

Postgres tests use a separate database (copilot_test) loaded with tests/v2/fixtures/sr_sample.csv.gz:
4,200 real 311 rows (300 per department, sampled deterministically from the window). In CI a Postgres
service container is required (AA_REQUIRE_PG=1 turns a missing database into a failure, not a skip).
"""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg
import pytest

from analytics_agent.copilot.settings import ROOT

ADMIN_BASE = os.environ.get("AA_PG_ADMIN_BASE", "host=localhost port=5433 user=postgres")
TEST_DB = "copilot_test"
ADMIN_DSN = f"{ADMIN_BASE} dbname={TEST_DB}"
GATEWAY_DSN = os.environ.get("AA_PG_TEST_GATEWAY_DSN", ADMIN_BASE.replace("user=postgres", "user=copilot_gateway")
                             .replace("password=postgres", "") + f" dbname={TEST_DB}")


def _pg_up() -> bool:
    try:
        with psycopg.connect(f"{ADMIN_BASE} dbname=postgres", connect_timeout=3):
            return True
    except psycopg.Error:
        return False


@pytest.fixture(scope="session")
def pg():
    if not _pg_up():
        if os.environ.get("AA_REQUIRE_PG"):
            pytest.fail("Postgres required (AA_REQUIRE_PG=1) but not reachable")
        pytest.skip("Postgres not running (make pg-start)")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "v2" / "pg_load.py"), "--fixture", "--db", TEST_DB,
                    "--admin", ADMIN_BASE], check=True, capture_output=True)
    from analytics_agent.copilot.identity import Directory
    from analytics_agent.copilot.warehouse import sync_roles

    sync_roles(Directory.from_config().state, ADMIN_DSN)
    return {"admin": ADMIN_DSN, "gateway": GATEWAY_DSN}
