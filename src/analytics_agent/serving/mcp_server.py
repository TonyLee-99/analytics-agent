"""MCP server so other agents (Claude Code, IDE assistants, internal agent platforms) can query the
warehouse through the same guarded agent.

    uv run python -m analytics_agent.serving.mcp_server          # stdio transport
Tools: ask_warehouse(question, db_id), list_tables(db_id), describe_table(table, db_id)
v2 tools (Chicago 311 copilot; identity fixed by AA_MCP_USER_EMAIL, never a tool argument):
       ask_copilot(question), search_documents(query, k), list_metrics()
"""

from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer

from analytics_agent.serving import service

server = MCPServer(
    name="analytics-warehouse",
    instructions="Ask business questions about the Olist marketplace warehouse in plain English. "
                 "All SQL is read-only, row-capped and PII-masked. If the answer has status 'clarify', "
                 "ask the user the clarification question instead of guessing.",
)


@server.tool()
def ask_warehouse(question: str, db_id: str = "olist", n_samples: int = 5) -> str:
    """Answer a natural-language data question. Returns JSON with status, route (answered | needs_review |
    escalated | clarify | failed), sql, columns, rows (max 200), summary, confidence (agreement of
    n_samples SQLs on their results) and, when the question is ambiguous, a clarification question."""
    return json.dumps(service.ask(question, db_id, n_samples), default=str)


@server.tool()
def list_tables(db_id: str = "olist") -> str:
    """List tables, their descriptions, columns and which columns are PII-masked."""
    return json.dumps(service.list_tables(db_id))


@server.tool()
def describe_table(table: str, db_id: str = "olist") -> str:
    """Return the CREATE TABLE-style schema of one table with column descriptions and sample values."""
    return service.describe_table(table, db_id)


def _mcp_identity() -> str:
    email = os.environ.get("AA_MCP_USER_EMAIL", "")
    if not email:
        raise PermissionError("AA_MCP_USER_EMAIL is not set: this MCP server has no identity to act for")
    return email


@server.tool()
def ask_copilot(question: str) -> str:
    """Ask the Chicago 311 copilot (documents + governed warehouse metrics), as the configured user.
    Returns JSON with status (answered | clarify | denied | error), text with [S#] citations, citations
    (doc_id, url, character span), notices (e.g. departments you cannot see), sql, rows, trace_id."""
    from analytics_agent.copilot import serving as v2

    try:
        return json.dumps(v2.ask_as_email(_mcp_identity(), question), default=str)
    except Exception as e:  # noqa: BLE001 - identity errors become a refusal, not a crash
        return json.dumps({"status": "denied", "error": str(e)})


@server.tool()
def search_documents(query: str, k: int = 5) -> str:
    """Search City documents the configured user may read (ACL-filtered, rechecked). JSON list of chunks."""
    from analytics_agent.copilot import serving as v2

    try:
        return json.dumps(v2.search_as_email(_mcp_identity(), query, k))
    except Exception as e:  # noqa: BLE001
        return json.dumps({"status": "denied", "error": str(e)})


@server.tool()
def list_metrics() -> str:
    """List the governed 311 metrics, including the two families with competing definitions."""
    from analytics_agent.copilot import serving as v2

    return json.dumps(v2.list_metrics())


if __name__ == "__main__":
    server.run()
