"""Reference MCP server exposing a real SDK tool over stdio.

Installation: pip install 'mcp>=1.13,<3'
Run: CONTEXTPILOT_API_URL=http://127.0.0.1:8765 python mcp_server.py

This is a development server using synthetic local data; not an authenticated production MCP server.
"""
import json
import os
from urllib.parse import quote
from urllib.request import urlopen
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("CONTEXTPILOT_API_URL", "http://127.0.0.1:8765").rstrip("/")
mcp = FastMCP("contextpilot-ticket-tools")


@mcp.tool()
def get_ticket(tenant_id: str, ticket_id: str) -> dict:
    """Read a synthetic support ticket for a specified tenant. Never writes data."""
    path = f"/v1/tenants/{quote(tenant_id,safe='')}/tickets/{quote(ticket_id,safe='')}"
    with urlopen(BASE + path, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    mcp.run(transport="stdio")
