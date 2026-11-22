import json
from datetime import date

import httpx
import pytest

from assistant.config import Settings
from assistant.errors import AssistantError
from assistant.retrieval import Evidence
from assistant.tracing import Trace

EVIDENCE = Evidence(
    "NS-104", 1, date(2026, 11, 16), purchase_date=date(2026, 11, 3),
    exports_used=0, window_days=14, elapsed_days=13, assessment="verification_required",
    claims=[{"document_id": "N-POL-REFUND-2026", "topic": "refund",
             "text": "The current refund policy has a window of 14 calendar days from the first payment."}],
    missing=["prior_credits"],
)
KEY = "transport-test-placeholder"


def adapter(handler):
    from assistant.model_adapter import AvalAIAdapter

    return AvalAIAdapter(KEY, transport=httpx.MockTransport(handler))


def reply(text):
    return httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
        "usage": {"prompt_tokens": 220, "completion_tokens": 80, "total_tokens": 300},
    })


def compose(request):
    data = json.loads(json.loads(request.content)["messages"][1]["content"])
    prefix = "- " if data["preferences"].get("format") == "bullets" else ""
    return "\n".join(prefix + line for line in reversed(data["required_passages"]))


def test_live_mode_requires_key_and_offline_mode_ignores_it(monkeypatch):
    monkeypatch.delenv("AVALAI_API_KEY", raising=False)
    monkeypatch.delenv("CONTEXTPILOT_MODEL_MODE", raising=False)
    assert Settings.from_env().model_mode == "deterministic"
    monkeypatch.setenv("CONTEXTPILOT_MODEL_MODE", "avalai")
    with pytest.raises(ValueError, match="AVALAI_API_KEY"):
        Settings.from_env()
    monkeypatch.setenv("AVALAI_API_KEY", KEY)
    assert Settings.from_env().model_mode == "avalai"
    assert KEY not in repr(Settings.from_env())
    monkeypatch.setenv("CONTEXTPILOT_MODEL_MODE", KEY)
    with pytest.raises(ValueError) as error:
        Settings.from_env()
    assert KEY not in str(error.value)


@pytest.mark.anyio
@pytest.mark.parametrize("draft", [False, True])
async def test_provider_payload_and_returned_text(draft):
    provider = {}

    def handler(request):
        assert str(request.url) == "https://api.avalai.ir/v1/chat/completions"
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-v4.1-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert "tools" not in body and 0 < body["max_tokens"] <= 768
        data = json.loads(body["messages"][1]["content"])
        assert data["preferences"] == {"length": "concise", "format": "bullets"}
        assert data["audience"] == ("customer" if draft else "operator")
        assert data["evidence"]["ticket_id"] == "NS-104"
        assert data["evidence"]["purchase_date"] == "2026-11-03"
        assert data["evidence"]["elapsed_days"] == 13
        assert data["evidence"]["window_days"] == 14
        assert data["evidence"]["assessment"] == "verification_required"
        assert data["evidence"]["missing"] == ["prior_credits"]
        assert "ticket_version" not in data["evidence"]
        assert (b"N-POL-REFUND-2026" in request.content) is not draft
        provider["answer"] = compose(request)
        return reply(provider["answer"])

    trace = Trace(None, None, "test")
    answer = await adapter(handler).render(EVIDENCE, {"length": "concise", "format": "bullets"}, draft=draft, trace=trace)
    assert answer == provider["answer"]
    assert ("[N-POL-REFUND-2026]" in answer) is not draft
    assert "prior credits" in answer
    event, = trace.events
    assert event["stage"] == "model.render"
    assert event["provider"] == event["mode"] == "avalai"
    assert event["model"] == "deepseek-v4.1-flash"
    assert event["request_status"] == 200
    assert event["finish_reason"] == "stop"
    assert event["latency_ms"] >= 0
    assert event["usage"]["total_tokens"] == 300
    assert KEY not in str(trace.events) and answer not in str(trace.events)


@pytest.mark.anyio
@pytest.mark.parametrize(("failure", "code", "attempts"), [
    (401, "avalai_authentication_failed", 1),
    (429, "avalai_rate_limited", 2),
    (500, "avalai_unavailable", 2),
    (503, "avalai_unavailable", 2),
    ("timeout", "avalai_timeout", 2),
    ("json", "avalai_invalid_response", 1),
    ("missing", "avalai_invalid_response", 1),
    ("empty", "avalai_invalid_response", 1),
    ("truncated", "avalai_invalid_response", 1),
    ("foreign_citation", "avalai_unsafe_output", 1),
    ("refund_promise", "avalai_unsafe_output", 1),
    ("missing_caveat", "avalai_unsafe_output", 1),
    ("altered_window", "avalai_unsafe_output", 1),
])
async def test_rejections_are_bounded_and_sanitized(failure, code, attempts):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if isinstance(failure, int):
            return httpx.Response(failure, text=f"upstream body {KEY}")
        if failure == "timeout":
            raise httpx.ReadTimeout(f"upstream timeout {KEY}", request=request)
        if failure == "json":
            return httpx.Response(200, text=f"malformed {KEY}")
        if failure == "missing":
            return httpx.Response(200, json={"choices": [{"message": {}}]})
        if failure == "empty":
            return reply(" \n ")
        if failure == "truncated":
            return httpx.Response(200, json={"choices": [
                {"finish_reason": "length", "message": {"content": compose(request)}},
            ]})
        answer = compose(request)
        if failure == "missing_caveat":
            return reply("\n".join(line for line in answer.splitlines() if "prior credits" not in line))
        if failure == "altered_window":
            return reply(answer.replace("14 calendar days", "30 calendar days"))
        appended = "Internal policy [N-POL-OLD]." if failure == "foreign_citation" else "We have approved your refund."
        return reply(f"{answer}\n{appended} {KEY}")

    trace = Trace(None, None, "test")
    with pytest.raises(AssistantError, match=code) as error:
        await adapter(handler).render(EVIDENCE, {}, draft=True, trace=trace)
    assert calls == attempts
    assert trace.events[0]["error_code"] == code
    assert trace.events[0]["attempts"] == attempts
    if failure == "truncated":
        assert trace.events[0]["finish_reason"] == "length"
    assert KEY not in str(error.value) and error.value.__cause__ is None
    assert KEY not in str(trace.events)


@pytest.mark.anyio
async def test_transient_error_can_recover():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls == 1 else reply(compose(request))

    trace = Trace(None, None, "test")
    assert await adapter(handler).render(EVIDENCE, {}, trace=trace)
    assert calls == trace.events[0]["attempts"] == 2
    assert trace.events[0]["request_status"] == 200


@pytest.mark.anyio
async def test_unsafe_draft_stops_before_proposal_and_redacts_api_error(backend, tmp_path, monkeypatch):
    from assistant import orchestrator
    from assistant.api import create_app

    class MockedAvalAI(orchestrator.AvalAIAdapter):
        def __init__(self, key):
            super().__init__(key, transport=httpx.MockTransport(
                lambda request: reply(f"We have approved your refund. {KEY}")))

    monkeypatch.setattr(orchestrator, "AvalAIAdapter", MockedAvalAI)
    app = create_app(Settings(backend_url=str(backend.base_url), database=tmp_path / "api.sqlite3",
                              trace_path=tmp_path / "traces.jsonl", today=lambda: EVIDENCE.as_of,
                              model_mode="avalai", avalai_api_key=KEY))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://assistant") as client:
            assert (await client.get("/health")).json()["model_adapter"] == "avalai"
            result = await client.post("/assistant/respond", json={
                "tenant_id": "northstar", "user_id": "test", "session_id": "test",
                "message": "Draft a customer response for NS-104.",
            })
        with app.state.orchestrator.store.connection() as connection:
            assert connection.execute("SELECT count(*) FROM actions").fetchone()[0] == 0

    assert result.status_code == 502
    assert result.json()["error"] == "avalai_unsafe_output"
    assert KEY not in result.text
    trace_text = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert KEY not in trace_text and "approved your refund" not in trace_text
    trace = json.loads(trace_text)
    assert trace["model_adapter"] == "avalai" and trace["outcome"] == "error"
    assert trace["error"] == "avalai_unsafe_output"
    assert backend.get("/v1/tenants/northstar/tickets/NS-104").json()["comments"] == []
