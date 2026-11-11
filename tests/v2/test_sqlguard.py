"""Postgres SQL guard: relation and function allowlists."""

import pytest

from analytics_agent.copilot.copilot import LAYER_PATH
from analytics_agent.copilot.sqlguard import check_sql
from analytics_agent.semantic.compiler import compile_query, load_layer

OK = [
    "SELECT dept_code, count(*) FROM wh.service_request GROUP BY 1",
    "WITH x AS (SELECT * FROM wh.service_request) SELECT count(*) FROM x",
    "SELECT d.department, percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (closed_at - created_at))) "
    "FROM wh.service_request r JOIN wh.department d USING (dept_code) GROUP BY 1",
    "SELECT date_trunc('month', created_at)::date, count(*) FILTER (WHERE is_open) FROM wh.service_request GROUP BY 1",
]
BAD = [
    ("SELECT set_config('app.live_depts', 'DWM', false)", "set_config"),
    ("SELECT current_setting('app.live_depts')", "current_setting"),
    ("SELECT * FROM service_request", "relation"),
    ("SELECT * FROM public.service_request", "relation"),
    ('SELECT * FROM "wh"."service_request_x"', "relation"),
    ("SELECT * FROM acl.role_departments", "relation"),
    ("SELECT * FROM pg_catalog.pg_stats", "system"),
    ("SELECT pg_sleep(10)", "pg_sleep"),
    ("SELECT query_to_xml('select 1', true, true, '')", "query_to_xml"),
    ("SELECT current_user", "current"),
    ("DELETE FROM wh.service_request", "read"),
    ("SELECT 1; SELECT 2", "statement"),
    ("SELECT * FROM dblink('x', 'select 1') AS t(a int)", "dblink"),
    ("COPY wh.service_request TO '/tmp/x'", ""),
]


@pytest.mark.parametrize("sql", OK)
def test_allowed(sql):
    g = check_sql(sql)
    assert g.ok, g.errors


@pytest.mark.parametrize("sql,why", BAD)
def test_rejected(sql, why):
    g = check_sql(sql)
    assert not g.ok
    assert why.lower() in " ".join(g.errors).lower()


def test_every_governed_metric_compiles_and_passes_guard():
    layer = load_layer(LAYER_PATH)
    for name, m in layer.metrics.items():
        q = {"metrics": [name], "dimensions": m.get("required_dimensions", ["dept_code"])[:1],
             "time_range": {"start": "2025-01-01", "end": "2026-01-01"}}
        g = check_sql(compile_query(q, layer))
        assert g.ok, (name, g.errors)
        assert g.relations and set(g.relations) <= {"wh.service_request", "wh.department", "wh.community_area",
                                                     "wh.request_benchmark"}
