"""Smoke-test the MCP server over real stdio (subprocess), as another agent would use it.
Run: AA_MODEL=mock uv run python scripts/smoke_mcp.py      (offline)
     uv run python scripts/smoke_mcp.py --real              (one real Haiku question)"""

import asyncio
import json
import os
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main(real: bool) -> None:
    env = dict(os.environ)
    if not real:
        env["AA_MODEL"] = "mock"
    params = StdioServerParameters(command=sys.executable, args=["-m", "analytics_agent.serving.mcp_server"], env=env)
    async with Client(params) as client:
        tools = [t.name for t in (await client.list_tools()).tools]
        print("tools:", tools)
        tables = json.loads((await client.call_tool("list_tables", {})).content[0].text)
        print("list_tables:", [t["table"] for t in tables])
        q = "What was GMV in 2017?" if real else "How many orders are there?"
        ans = json.loads((await client.call_tool("ask_warehouse", {"question": q})).content[0].text)
        print("ask_warehouse:", {k: ans[k] for k in ("status", "sql", "rows", "summary", "confidence", "used_governed_metrics")})


if __name__ == "__main__":
    asyncio.run(main("--real" in sys.argv))
