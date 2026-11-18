"""Read-only SDK protocol client over one persistent stdio session; no HTTP fallback."""
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from .config import Settings
from .errors import AssistantError
from .lifecycle import LoopResource
from .schemas import Context, Ticket, validate_ticket_scope
from .tracing import Trace

TOOL = "get_ticket"


def normalize_ticket(result: CallToolResult, tenant: str, ticket_id: str) -> Ticket:
    """Validate tool output as data: schema, then identity against the trusted request."""
    if result.isError:
        text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
        if "HTTP Error 404" in text:
            raise AssistantError("ticket_not_found", 404)
        raise AssistantError("mcp_tool_error")
    try:
        payload = result.structuredContent
        if payload is None:
            blocks = [c.text for c in result.content if isinstance(c, TextContent)]
            if len(blocks) != 1:
                raise ValueError("expected one JSON result")
            payload = json.loads(blocks[0])
        ticket = Ticket.model_validate(payload)
        if ticket.tenant_id != tenant or ticket.id != ticket_id:
            raise ValueError("identity mismatch")
        return ticket
    except (ValueError, TypeError, ValidationError) as exc:
        raise AssistantError("malformed_mcp_result") from exc


class TicketMCP:
    """Spawns the MCP server once and multiplexes tool calls over that session.

    Starting a Python MCP server costs about a second, so paying it per ticket read
    dominated every request. The session is reopened transparently when it breaks.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._session = LoopResource(self._open, open_timeout=settings.mcp_timeout)

    @asynccontextmanager
    async def _open(self):
        params = StdioServerParameters(
            command=self.settings.python, args=[str(self.settings.mcp_server)],
            cwd=str(self.settings.mcp_server.parent),
            env={"CONTEXTPILOT_API_URL": self.settings.backend_url, "PYTHONUTF8": "1"},
        )
        timeout = timedelta(seconds=self.settings.mcp_timeout)
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
                    await session.initialize()
                    yield session

    async def aclose(self) -> None:
        await self._session.aclose()

    async def _call(self, context: Context, ticket_id: str, trace: Trace) -> CallToolResult:
        # tenant_id is always the trusted request context, never model or document text.
        arguments = {"tenant_id": context.tenant_id, "ticket_id": ticket_id}
        for may_reconnect in (True, False):
            session, opened = await self._session.get()
            if opened:
                trace.event("mcp.initialize", transport="stdio")
            trace.event("mcp.tools/call", transport="stdio", tool=TOOL, ticket_id=ticket_id,
                        session="new" if opened else "reused")
            try:
                with anyio.fail_after(self.settings.mcp_timeout):
                    return await session.call_tool(TOOL, arguments=arguments)
            except Exception:
                await self._session.discard(session)
                if opened or not may_reconnect:
                    raise
                # A reused session can have died while idle; a read is safe to repeat once.
                trace.event("mcp.reconnect", reason="reused_session_failed")
        raise AssertionError("unreachable")

    async def get_ticket(self, context: Context, ticket_id: str, trace: Trace) -> Ticket:
        validate_ticket_scope(context.tenant_id, ticket_id)
        if not self.settings.mcp_server.is_file():
            raise AssistantError("mcp_unavailable")
        for attempt in range(1, self.settings.read_attempts + 1):
            trace.event("mcp.attempt", attempt=attempt)
            try:
                result = await self._call(context, ticket_id, trace)
            except Exception as exc:
                trace.event("mcp.error", code="mcp_unavailable", attempt=attempt)
                if attempt == self.settings.read_attempts:
                    raise AssistantError("mcp_unavailable") from exc
                await anyio.sleep(0.05)
                continue
            ticket = normalize_ticket(result, context.tenant_id, ticket_id)
            trace.event("mcp.validated", ticket_id=ticket.id, version=ticket.version)
            return ticket
        raise AssistantError("mcp_unavailable")
