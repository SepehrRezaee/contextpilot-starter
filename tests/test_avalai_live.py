"""Opt-in provider and full localhost application checks using synthetic fixtures."""
import hashlib
import json
import os
import sys

import httpx
import pytest

from assistant.errors import AssistantError
from assistant.eval_runner import EVALUATION_DATE, ROOT, LocalProcess, backend_ticket, free_port, trace_by_id
from assistant.model_adapter import AvalAIAdapter

pytestmark = pytest.mark.skipif(os.environ.get("CONTEXTPILOT_LIVE_TESTS") != "1",
                                reason="set CONTEXTPILOT_LIVE_TESTS=1 for paid AvalAI checks")
CTX = {"tenant_id": "northstar", "user_id": "reviewer-01", "session_id": "live-1"}


@pytest.fixture(autouse=True)
def require_credential():
    if not os.environ.get("AVALAI_API_KEY", "").strip():
        pytest.fail("Set AVALAI_API_KEY in the process environment before running live checks.", pytrace=False)


def assert_credential_absent(text, source):
    if os.environ["AVALAI_API_KEY"].strip() in text:
        pytest.fail(f"Credential disclosure in {source}.", pytrace=False)


@pytest.mark.anyio
async def test_live_avalai_connectivity():
    adapter = AvalAIAdapter(os.environ["AVALAI_API_KEY"].strip())
    metadata = {}
    failure = None
    try:
        content = await adapter._complete(
            [{"role": "user", "content": "Reply with one short greeting."}], max_tokens=48, metadata=metadata)
    except AssistantError as error:
        failure = error.code
    if failure:
        pytest.fail(f"AvalAI smoke failed with {failure}. Request status {metadata.get('request_status')}.",
                    pytrace=False)
    assert metadata["request_status"] == 200
    assert_credential_absent(content, "provider output")
    print(json.dumps({"smoke": "ok", "model": adapter.model, **metadata}))


def test_live_contextpilot_workflow(tmp_path, monkeypatch):
    fixture_port = free_port()
    fixture = LocalProcess([sys.executable, str(ROOT / "server.py"), "--port", str(fixture_port)],
                           fixture_port, tmp_path / "backend.log")
    # The credential belongs to the assistant process only.
    with monkeypatch.context() as env:
        env.delenv("AVALAI_API_KEY")
        fixture.start()
    try:
        run_workflow(fixture, tmp_path, monkeypatch)
    finally:
        fixture.stop()
        for name in ("backend.log", "api.log", "trace.jsonl"):
            log = tmp_path / name
            if log.exists():
                assert_credential_absent(log.read_text(encoding="utf-8"), name)


def run_workflow(fixture, tmp_path, monkeypatch):
    port = free_port()
    traces = tmp_path / "trace.jsonl"
    for name, value in {
        "CONTEXTPILOT_API_URL": fixture.url,
        "CONTEXTPILOT_DB": str(tmp_path / "assistant.sqlite3"),
        "CONTEXTPILOT_TRACE": str(traces),
        "CONTEXTPILOT_TODAY": str(EVALUATION_DATE),
        "CONTEXTPILOT_MODEL_MODE": "avalai",
        "CONTEXTPILOT_MCP_SERVER": str(ROOT / "mcp_server.py"),
    }.items():
        monkeypatch.setenv(name, value)
    # Same production app, with the existing bootstrap-only synthetic failure injector.
    bootstrap = (
        "import sys, uvicorn; from assistant.api import create_app; "
        'uvicorn.run(create_app(demo_failures=("after",)), host="127.0.0.1", '
        'port=int(sys.argv[1]), log_level="error", access_log=False)'
    )
    api = LocalProcess([sys.executable, "-c", bootstrap, str(port)], port, tmp_path / "api.log")
    with (api, httpx.Client(base_url=api.url, timeout=45, trust_env=False) as client,
          httpx.Client(base_url=fixture.url, timeout=5, trust_env=False) as backend):
        assert client.get("/health").json()["model_adapter"] == "avalai"

        def respond(message, session="live-1"):
            response = client.post("/assistant/respond", json={**CTX, "session_id": session, "message": message})
            assert_credential_absent(response.text, "assistant response")
            result = response.json()
            if not response.is_success:
                trace = trace_by_id(traces, result["trace_id"])
                render = next((e for e in trace["events"] if e["stage"] == "model.render"), {})
                pytest.fail(f"ContextPilot returned HTTP {response.status_code} with {trace['error']}. "
                            f"Model metadata: {render}", pytrace=False)
            return result

        investigation = respond("Explain NS-104 and cite the current refund policy")
        facts = investigation["evidence"]
        assert facts["purchase_date"] == "2026-11-03"
        assert facts["as_of"] == str(EVALUATION_DATE)
        assert facts["window_days"] == 14 and facts["elapsed_days"] == 13
        assert facts["assessment"] == "verification_required" and "prior_credits" in facts["missing"]
        citations = {"N-POL-REFUND-2026", "N-POL-BILL-2026"}
        assert set(investigation["citations"]) == citations
        assert all(f"[{document_id}]" in investigation["answer"] for document_id in citations)
        assert "before any refund can be confirmed" in investigation["answer"]
        assert investigation["proposed_action"] is None
        investigation_trace = trace_by_id(traces, investigation["trace_id"])
        assert "mcp:get_ticket" in investigation_trace["tools"]
        render = assert_live_render(investigation_trace)
        print(json.dumps({"investigation": "ok", "trace_id": investigation["trace_id"],
                          "tools": investigation_trace["tools"], "citations": sorted(citations),
                          "example": next(line for line in investigation["answer"].splitlines()
                                          if "[N-POL-REFUND-2026]" in line),
                          "render": render}))

        assert respond("I prefer concise replies.")["intent"] == "preference_update"
        assert respond("Please use bullet points instead of short paragraphs from now on.")["intent"] == "preference_update"
        drafted = respond("Draft a customer response for NS-104.", session="live-2")
        assert_live_render(trace_by_id(traces, drafted["trace_id"]))
        preferences = {item["key"]: item for item in drafted["preferences"]}
        assert preferences["length"]["value"] == "concise"
        assert preferences["length"]["source_session"] == "live-1"
        assert preferences["format"]["value"] == "bullets"
        assert all(line.startswith("- ") for line in drafted["answer"].splitlines() if line)
        assert "Thank you" not in drafted["answer"] and "prior credits" in drafted["answer"]
        assert all(document_id not in drafted["answer"] for document_id in citations)
        action = drafted["proposed_action"]
        assert action["status"] == "PROPOSED" and action["requires_approval"]
        assert action["body"] == drafted["answer"]
        assert action["body_hash"] == hashlib.sha256(action["body"].encode("utf-8")).hexdigest()
        path = f"/assistant/actions/{action['action_id']}"
        assert client.get(path, params=CTX).json()["action"] == action
        assert backend_ticket(backend)["comments"] == []
        print("Live cross-session preferences and stored proposal verified.")

        foreign = respond("Explain HB-204")
        trace = trace_by_id(traces, foreign["trace_id"])
        assert foreign["intent"] == "scope_decline"
        assert foreign["evidence"] is None and foreign["citations"] == [] and foreign["proposed_action"] is None
        assert "HB-204" not in foreign["answer"]
        assert trace["tools"] == [] and not any(event["stage"].startswith("model.") for event in trace["events"])
        print("Tenant decline performed no tool or provider call.")

        wrong = client.post(path + "/approve", json={**CTX, "body_hash": "0" * 64})
        assert wrong.status_code == 409 and backend_ticket(backend)["comments"] == []
        payload = {**CTX, "body_hash": action["body_hash"]}
        approved = client.post(path + "/approve", json=payload)
        approved.raise_for_status()
        result = approved.json()
        assert result["action"]["status"] == "COMMITTED" and result["action"]["attempts"] == 1
        assert result["action"]["idempotency_key"] == action["idempotency_key"]
        trace = trace_by_id(traces, result["trace_id"])
        assert [e["status"] for e in trace["events"] if e["stage"] == "http.publish"] == [503]
        assert any(e["stage"] == "action.reconcile" and e["outcome"] == "committed" for e in trace["events"])
        repeat = client.post(path + "/approve", json=payload).json()
        assert repeat["action"]["status"] == "COMMITTED"
        assert "http:post_comment" not in trace_by_id(traces, repeat["trace_id"])["tools"]
        comments = backend_ticket(backend)["comments"]
        assert len(comments) == 1 and comments[0]["body"] == action["body"]
        print("Wrong hash rejected, valid approval reconciled a 503, and duplicate approval added no comment.")


def assert_live_render(trace):
    assert trace["model_adapter"] == "avalai" and trace["outcome"] == "ok"
    render, = [event for event in trace["events"] if event["stage"] == "model.render"]
    assert render["mode"] == render["provider"] == "avalai" and render["model"] == "deepseek-v4.1-flash"
    assert render["request_status"] == 200 and "error_code" not in render
    return render
