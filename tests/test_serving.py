"""Serving smoke tests with a mocked LLM (no network): HTTP API, UI page, MCP tools."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import HAS_OLIST


@pytest.fixture()
def mock_env(monkeypatch):
    monkeypatch.setenv("AA_MODEL", "mock")
    from analytics_agent.serving import service

    service.get_agent.cache_clear()
    yield
    service.get_agent.cache_clear()


def test_health_and_ui(mock_env):
    from analytics_agent.serving.api import app

    c = TestClient(app)
    assert c.get("/health").json() == {"ok": True}
    assert "Ask the warehouse" in c.get("/").text


def test_ask_endpoint_on_toy_db(mock_env, toy_duck):
    from analytics_agent.serving.api import app

    r = TestClient(app).post("/ask", json={"question": "How many orders?", "db_id": "toy"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered"
    assert body["rows"] == [[3]] and body["sql"].lower().startswith("select")
    assert body["trace_id"] and "llm_calls" in body["usage"]


def test_ask_validation_error(mock_env):
    from analytics_agent.serving.api import app

    assert TestClient(app).post("/ask", json={"question": ""}).status_code == 422


@pytest.mark.skipif(not HAS_OLIST, reason="olist warehouse not built")
def test_tables_endpoint_flags_pii(mock_env):
    from analytics_agent.serving.api import app

    tables = {t["table"]: t for t in TestClient(app).get("/tables").json()}
    assert "customer_zip_code_prefix" in tables["customers"]["pii_columns"]


def test_mcp_tools_in_process(mock_env, toy_duck):
    from mcp import Client

    from analytics_agent.serving.mcp_server import server

    async def go():
        async with Client(server) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            res = await client.call_tool("ask_warehouse", {"question": "How many orders?", "db_id": "toy"})
            listed = await client.call_tool("list_tables", {"db_id": "toy"})
            return tools, res, listed

    tools, res, listed = asyncio.run(go())
    assert {"ask_warehouse", "list_tables", "describe_table"} <= tools
    payload = json.loads(res.content[0].text)
    assert payload["status"] == "answered" and payload["rows"] == [[3]]
    assert {t["table"] for t in json.loads(listed.content[0].text)} == {"orders", "customers"}


def test_split_vote_is_routed_to_review(monkeypatch, toy_duck):
    """The API exposes the evaluated confidence: unanimous samples -> answered, a split vote -> needs_review."""
    from analytics_agent.llm import mock as mock_mod
    from analytics_agent.serving import service

    def responder(system, prompt, sample):
        return "```sql\nSELECT count(*) AS n FROM orders\n```" if sample < 3 else "```sql\nSELECT 7 AS n\n```"

    monkeypatch.setattr(service, "make_client", lambda name: mock_mod.MockLLM(responder))
    service.get_agent.cache_clear()
    split = service.ask("How many orders?", "toy", n_samples=5)
    assert split["confidence"] == 0.6 and split["route"] == "needs_review" and split["needs_review"]
    assert split["rows"] == [[3]]
    monkeypatch.setattr(service, "make_client", lambda name: mock_mod.MockLLM("```sql\nSELECT 3 AS n\n```"))
    service.get_agent.cache_clear()
    ok = service.ask("How many orders?", "toy", n_samples=5)
    assert ok["confidence"] == 1.0 and ok["route"] == "answered" and not ok["needs_review"]
    single = service.ask("How many orders?", "toy", n_samples=1)
    assert single["confidence"] is None and single["needs_review"]
    service.get_agent.cache_clear()
