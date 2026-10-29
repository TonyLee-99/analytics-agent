"""MCP server so other agents (Claude Code, IDE assistants, internal agent platforms) can query the
warehouse through the same guarded agent.

    uv run python -m analytics_agent.serving.mcp_server          # stdio transport
Tools: ask_warehouse(question, db_id), list_tables(db_id), describe_table(table, db_id)
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from analytics_agent.serving import service

server = MCPServer(
    name="analytics-warehouse",
    instructions="Ask business questions about the Olist marketplace warehouse in plain English. "
                 "All SQL is read-only, row-capped and PII-masked. If the answer has status 'clarify', "
                 "ask the user the clarification question instead of guessing.",
)


@server.tool()
def ask_warehouse(question: str, db_id: str = "olist") -> str:
    """Answer a natural-language data question. Returns JSON with status, sql, columns, rows (max 200),
    summary, confidence and, when the question is ambiguous, a clarification question."""
    return json.dumps(service.ask(question, db_id), default=str)


@server.tool()
def list_tables(db_id: str = "olist") -> str:
    """List tables, their descriptions, columns and which columns are PII-masked."""
    return json.dumps(service.list_tables(db_id))


@server.tool()
def describe_table(table: str, db_id: str = "olist") -> str:
    """Return the CREATE TABLE-style schema of one table with column descriptions and sample values."""
    return service.describe_table(table, db_id)


if __name__ == "__main__":
    server.run()
