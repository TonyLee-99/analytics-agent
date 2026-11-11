"""Conflict detection on paraphrases that are not in any eval set (written for this test)."""

import pytest

from analytics_agent.copilot.copilot import LAYER_PATH, definition_signals
from analytics_agent.semantic.compiler import load_layer

L = load_layer(LAYER_PATH)
FAM = {f: {n: m for n, m in L.metrics.items() if m.get("family") == f} for f in ("time_to_close", "sla_attainment")}
C, A = "median_days_to_close_completed", "median_days_to_close_all_closed"
R, ALL = "sla_attainment_resolved", "sla_attainment_all_requests"


@pytest.mark.parametrize("q,fam,want", [
    ("median days to close for tree trims, leaving out cancellations", "time_to_close", [C]),
    ("How long do graffiti requests take to resolve?", "time_to_close", [C]),
    ("median close time for potholes, just the completed ones", "time_to_close", [C]),
    ("median days to close where status is Completed", "time_to_close", [C]),
    ("median time to close counting canceled requests too", "time_to_close", [A]),
    ("median days to close for every closed request in 2025", "time_to_close", [A]),
    ("time to close, completed or canceled, for DWM", "time_to_close", [A]),
    ("SLA attainment among completed requests for CDOT", "sla_attainment", [R]),
    ("SLA attainment across all requests, open ones counted as misses", "sla_attainment", [ALL]),
    ("share of requests within benchmark, including open requests", "sla_attainment", [ALL]),
    # ambiguous: must ask
    ("How quickly does Streets and San close requests?", "time_to_close", []),
    ("median time to close last month", "time_to_close", []),
    ("what's our SLA attainment this year?", "sla_attainment", []),
    ("SLA attainment for water requests by month", "sla_attainment", []),
])
def test_paraphrases(q, fam, want):
    assert definition_signals(q.lower(), FAM[fam]) == want


def test_both_definitions_named_asks():
    q = "median days to close, excluding canceled, and also including canceled for comparison"
    assert sorted(definition_signals(q.lower(), FAM["time_to_close"])) == sorted([C, A])
