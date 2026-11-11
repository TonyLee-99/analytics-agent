"""P1: row-level security under per-user roles, tested on a real Postgres with the SQL guard bypassed."""

from __future__ import annotations

import psycopg
import pytest

from analytics_agent.copilot.identity import ALL_DEPTS, Directory
from analytics_agent.copilot.warehouse import Warehouse, oracle_query, relations_ok, role_name, sync_roles

PROBES = [
    "SELECT dept_code, count(*) FROM wh.service_request GROUP BY 1 ORDER BY 1",
    "SELECT count(*) FROM wh.service_request",
    "SELECT sr_number FROM wh.service_request ORDER BY sr_number",
    "SELECT d.department, count(*) FROM wh.service_request r JOIN wh.department d USING (dept_code) GROUP BY 1 ORDER BY 1",
    "SELECT count(*) FROM (SELECT * FROM wh.service_request UNION ALL SELECT * FROM wh.service_request) u",
]


@pytest.fixture(scope="module")
def env(pg):
    d = Directory.from_config()
    return d, Warehouse(dsn=pg["gateway"]), pg["admin"]


def test_gateway_has_no_privilege_of_its_own(env):
    facts = relations_ok(env[2])
    assert facts["gateway"] == {"super": False, "inherit": False, "bypassrls": False, "createrole": False,
                                "createdb": False}
    assert facts["gateway_table_privileges"] == []
    assert all(inherit is False and set_ is True and admin is False
               for _, inherit, set_, admin in facts["gateway_memberships"])
    assert facts["rls"]["wh.service_request"] is True and facts["rls"]["acl.role_departments"] is True
    assert {p[1] for p in facts["policies"]} == {"dept_read", "own_mapping"}  # no extra permissive policy
    assert set(facts["user_relations"]) == set(facts["allowlist"]) | {"acl.role_departments"}


@pytest.mark.parametrize("user", ["maria", "dev", "rosa", "nina", "priya", "jules", "lena", "omar", "sam", "grace"])
def test_rows_equal_policy_model_exactly(env, user):
    d, wh, admin = env
    depts = d.state.data_departments(user)
    for sql in PROBES:
        got = wh.query(user, depts, sql)
        want = oracle_query(sql, depts, admin_dsn=admin)
        assert got.ok and want.ok, (got.error, want.error)
        assert sorted(map(tuple, got.rows)) == sorted(map(tuple, want.rows)), (user, sql)
    if depts:  # positive assertion: an entitled user actually sees rows
        assert wh.query(user, depts, PROBES[1]).rows[0][0] > 0


def test_unset_or_empty_live_setting_is_default_deny(env):
    d, wh, _ = env
    assert wh.query("maria", set(), PROBES[1]).rows == [(0,)]


def test_live_setting_cannot_widen_beyond_synced_mapping(env):
    d, wh, _ = env
    got = wh.query("maria", ALL_DEPTS, PROBES[0])  # forged: every department
    assert {r[0] for r in got.rows} == {"CDOT"}


def test_set_config_is_not_executable(env):
    _, wh, _ = env
    r = wh.query("maria", {"CDOT"}, "SELECT set_config('app.live_depts', 'DWM,CDOT', true)")
    assert not r.ok and "permission denied" in r.error


def test_multi_statement_rejected_by_server(env):
    _, wh, _ = env
    r = wh.query("maria", {"CDOT"}, "SET LOCAL app.live_depts = 'DWM'; SELECT count(*) FROM wh.service_request")
    assert not r.ok and "multiple commands" in r.error


@pytest.mark.parametrize("sql", ["SET ROLE cu_lena", "RESET ROLE", "SET row_security = off",
                                 "SELECT * FROM acl.role_departments WHERE role_name <> current_user",
                                 "SELECT count(*) FROM pg_stats WHERE tablename = 'service_request'"])
def test_session_tricks_do_not_expose_rows(env, sql):
    _, wh, _ = env
    r = wh.query("maria", {"CDOT"}, sql)
    assert not r.ok or not r.rows or r.rows in ([(0,)], [])  # either an error or nothing visible
    after = wh.query("maria", {"CDOT"}, PROBES[0])  # and the connection is clean afterwards
    assert {row[0] for row in after.rows} == {"CDOT"}


def test_error_mid_transaction_resets_role(env):
    _, wh, _ = env
    assert not wh.query("lena", ALL_DEPTS, "SELECT 1/0").ok
    r = wh.query("maria", {"CDOT"}, PROBES[0])
    assert {row[0] for row in r.rows} == {"CDOT"}


def test_revocation_inside_lag_is_closed_by_live_narrowing(env):
    """The role table still says maria -> CDOT (sync lag); her live entitlements are empty."""
    d, wh, _ = env
    d2 = Directory.from_config()
    d2.remove_member("dept-cdot", "maria")
    assert wh.query("maria", d2.state.data_departments("maria"), PROBES[1]).rows == [(0,)]


def test_synced_revocation_alone_also_denies(env, pg):
    d, wh, admin = env
    d2 = Directory.from_config()
    d2.remove_member("dept-cdot", "maria")
    try:
        sync_roles(d2.state, admin)
        # even if the caller forged live departments, the synced mapping is now empty
        assert wh.query("maria", {"CDOT"}, PROBES[1]).rows == [(0,)]
    finally:
        sync_roles(d.state, admin)


def test_role_names_are_strict():
    with pytest.raises(ValueError):
        role_name("x; DROP ROLE postgres")
    with pytest.raises(ValueError):
        Warehouse().query("maria", {"CDOT,DWM"}, "SELECT 1")


def test_gateway_cannot_read_tables_directly(pg):
    with psycopg.connect(pg["gateway"], autocommit=True) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT count(*) FROM wh.service_request")
