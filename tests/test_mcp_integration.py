"""Catch HTTP shortcuts, identity substitution and unvalidated MCP results."""
import json
import time
from datetime import date

import pytest


def context():
    from assistant.schemas import Context
    return Context(tenant_id="northstar", user_id="reviewer-01", session_id="mcp-test")


@pytest.mark.anyio
async def test_real_sdk_stdio_invocation_from_unrelated_cwd(backend, tmp_path, monkeypatch):
    from assistant.config import Settings
    from assistant.mcp_client import TicketMCP
    from assistant.tracing import Trace
    monkeypatch.chdir(tmp_path)
    settings = Settings(backend_url=str(backend.base_url).rstrip("/"), read_attempts=1)
    trace = Trace(None, context(), "investigate")
    ticket = await TicketMCP(settings).get_ticket(context(), "NS-104", trace)
    assert ticket.id == "NS-104"
    assert ticket.tenant_id == "northstar"
    assert ticket.version == 1
    assert ticket.purchase_date == date(2026, 11, 3)
    events = trace.finish("ok")["events"]
    assert any(e["stage"] == "mcp.initialize" and e["transport"] == "stdio" for e in events)
    assert any(e["stage"] == "mcp.tools/call" and e["tool"] == "get_ticket" for e in events)


@pytest.mark.anyio
async def test_missing_ticket_and_foreign_ticket_fail_closed(backend):
    from assistant.config import Settings
    from assistant.errors import AssistantError
    from assistant.mcp_client import TicketMCP
    from assistant.tracing import Trace
    client = TicketMCP(Settings(backend_url=str(backend.base_url).rstrip("/"), read_attempts=1))
    with pytest.raises(AssistantError, match="ticket_not_found"):
        await client.get_ticket(context(), "NS-999", Trace(None, context(), "test"))
    foreign = Trace(None, context(), "test")
    with pytest.raises(AssistantError, match="ticket_scope"):
        await client.get_ticket(context(), "HB-204", foreign)
    assert not any(e["stage"].startswith("mcp.") for e in foreign.events)


@pytest.mark.anyio
async def test_mcp_startup_failure_is_truthful(tmp_path):
    from assistant.config import Settings
    from assistant.errors import AssistantError
    from assistant.mcp_client import TicketMCP
    from assistant.tracing import Trace
    client = TicketMCP(Settings(mcp_server=tmp_path / "missing.py", read_attempts=1))
    with pytest.raises(AssistantError, match="mcp_unavailable"):
        await client.get_ticket(context(), "NS-104", Trace(None, context(), "test"))


def test_mcp_normalization_rejects_malformed_error_and_wrong_identity():
    from mcp.types import CallToolResult, TextContent
    from assistant.errors import AssistantError
    from assistant.mcp_client import normalize_ticket
    valid = {"id": "NS-104", "tenant_id": "northstar", "version": 1,
             "status": "open", "subject": "Refund", "body": "Synthetic", "comments": []}
    ticket = normalize_ticket(CallToolResult(content=[TextContent(type="text", text=json.dumps(valid))]), "northstar", "NS-104")
    assert ticket.version == 1
    for result in [
        CallToolResult(content=[TextContent(type="text", text="not JSON")]),
        CallToolResult(content=[], structuredContent={**valid, "tenant_id": "harbor"}),
        CallToolResult(content=[], structuredContent={**valid, "version": True}),
        CallToolResult(content=[], isError=True),
    ]:
        with pytest.raises(AssistantError):
            normalize_ticket(result, "northstar", "NS-104")


@pytest.mark.anyio
async def test_mcp_timeout_is_bounded_and_closes_unresponsive_child(tmp_path):
    from assistant.config import Settings
    from assistant.errors import AssistantError
    from assistant.mcp_client import TicketMCP
    from assistant.tracing import Trace
    script = tmp_path / "unresponsive.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    client = TicketMCP(Settings(mcp_server=script, mcp_timeout=0.2, read_attempts=1))
    start = time.monotonic()
    with pytest.raises(AssistantError, match="mcp_unavailable"):
        await client.get_ticket(context(), "NS-104", Trace(None, context(), "test"))
    assert time.monotonic() - start < 5

