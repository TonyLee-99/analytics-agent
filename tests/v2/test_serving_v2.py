"""D2: HTTP and MCP entry points go through the same copilot and enforce identity."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from analytics_agent.copilot import serving as v2
from analytics_agent.serving import mcp_server
from analytics_agent.serving.api import app

from .helpers import small_copilot


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AA_PROXY_SECRET", "s3cret")
    v2.set_copilot(small_copilot(tmp_path))
    yield TestClient(app)
    v2.set_copilot(None)


def test_signed_identity_answers(client):
    e = "maria.chen@cityops.example"
    r = client.post("/v2/ask", json={"question": "how are potholes fixed?"},
                    headers={"X-Forwarded-Email": e, "X-Identity-Signature": v2.sign(e, "s3cret")})
    assert r.status_code == 200 and r.json()["user_id"] == "maria" and r.json()["citations"]


@pytest.mark.parametrize("headers", [
    {}, {"X-Forwarded-Email": "maria.chen@cityops.example"},
    {"X-Forwarded-Email": "maria.chen@cityops.example", "X-Identity-Signature": "forged"},
])
def test_unsigned_or_forged_identity_refused(client, headers):
    assert client.post("/v2/ask", json={"question": "how are potholes fixed?"}, headers=headers).status_code == 403


def test_signed_but_unmappable_identity_refused(client):
    e = "pat.lee@cityops.example"  # ambiguous in the directory
    r = client.post("/v2/ask", json={"question": "potholes?"},
                    headers={"X-Forwarded-Email": e, "X-Identity-Signature": v2.sign(e, "s3cret")})
    assert r.status_code == 403


def test_metrics_catalog(client):
    names = {m["name"] for m in client.get("/v2/metrics").json()}
    assert {"median_days_to_close_completed", "median_days_to_close_all_closed"} <= names


def test_mcp_tools_use_configured_identity(tmp_path, monkeypatch):
    v2.set_copilot(small_copilot(tmp_path))
    try:
        monkeypatch.delenv("AA_MCP_USER_EMAIL", raising=False)
        assert json.loads(mcp_server.ask_copilot("potholes?"))["status"] == "denied"
        monkeypatch.setenv("AA_MCP_USER_EMAIL", "dev.patel@cityops.example")
        docs = {d["doc_id"] for d in json.loads(mcp_server.search_documents("pothole crews target"))}
        assert "cdot-1" not in docs  # dev cannot read the CDOT-restricted doc
        assert json.loads(mcp_server.ask_copilot("potholes?"))["user_id"] == "dev"
        tools = asyncio.run(mcp_server.server.list_tools())
        assert {"ask_copilot", "search_documents", "list_metrics"} <= {t.name for t in tools}
    finally:
        v2.set_copilot(None)
