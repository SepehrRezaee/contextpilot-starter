"""Real fixture side effects, not mocked assistant text, prove consent/recovery."""
import asyncio
import hashlib

import httpx
import pytest


def context(user="reviewer-01", tenant="northstar", session="review"):
    from assistant.schemas import Context
    return Context(tenant_id=tenant, user_id=user, session_id=session)


def service(backend, tmp_path, *, demo_failures=(), reader=None, publisher=None):
    from assistant.actions import Actions
    from assistant.backend_client import CommentPublisher
    from assistant.config import Settings
    from assistant.storage import Store
    settings = Settings(backend_url=str(backend.base_url).rstrip("/"), database=tmp_path / "actions.sqlite3", read_attempts=1)
    return Actions(settings, Store(settings.database), reader=reader,
                   publisher=publisher or CommentPublisher(settings, demo_failures=demo_failures))


async def propose(actions, body="Please verify prior credits before confirming a refund."):
    from assistant.tracing import Trace
    ctx = context()
    trace = Trace(None, ctx, "draft")
    ticket = await actions.mcp.get_ticket(ctx, "NS-104", trace)
    return actions.propose(ctx, ticket, body, trace)


def comments(backend):
    return backend.get("/v1/tenants/northstar/tickets/NS-104").json()["comments"]


@pytest.mark.anyio
async def test_draft_is_immutable_persistent_and_never_writes(backend, tmp_path):
    from assistant.errors import AssistantError
    from assistant.tracing import Trace
    actions = service(backend, tmp_path)
    draft = await propose(actions)
    assert draft["status"] == "PROPOSED"
    assert draft["body_hash"] == hashlib.sha256(draft["body"].encode()).hexdigest()
    assert comments(backend) == []
    assert service(backend, tmp_path).inspect(context(), draft["action_id"]) == draft
    with pytest.raises(AssistantError, match="action_not_found"):
        actions.inspect(context(user="other"), draft["action_id"])
    with pytest.raises(AssistantError, match="action_not_found"):
        await actions.approve(context(tenant="harbor"), draft["action_id"], draft["body_hash"], Trace(None, context(tenant="harbor"), "approve"))
    with pytest.raises(AssistantError, match="body_hash_mismatch"):
        await actions.approve(context(), draft["action_id"], "0" * 64, Trace(None, context(), "approve"))
    assert comments(backend) == []


@pytest.mark.anyio
async def test_503_after_commit_reconciles_original_key_and_repeat_is_noop(backend, tmp_path):
    from assistant.tracing import Trace
    actions = service(backend, tmp_path, demo_failures=("after",))
    draft = await propose(actions)
    trace = Trace(None, context(), "approve")
    committed = await actions.approve(context(), draft["action_id"], draft["body_hash"], trace)
    assert committed["status"] == "COMMITTED"
    assert committed["idempotency_key"] == draft["idempotency_key"]
    assert committed["attempts"] == 1
    assert committed["receipt"]["version"] == 2
    assert len(comments(backend)) == 1
    assert comments(backend)[0]["body"] == draft["body"]
    assert comments(backend)[0]["approval_reference"].startswith("approval-")
    events = trace.finish("ok")["events"]
    assert any(e["stage"] == "action.reconcile" and e["outcome"] == "committed" for e in events)
    restarted = service(backend, tmp_path)
    again = await restarted.approve(context(), draft["action_id"], draft["body_hash"], Trace(None, context(), "approve"))
    assert again["status"] == "COMMITTED"
    await restarted.reconcile(context(), draft["action_id"], Trace(None, context(), "reconcile"))
    assert len(comments(backend)) == 1


@pytest.mark.anyio
async def test_503_before_commit_retries_only_same_key_and_body(backend, tmp_path):
    from assistant.tracing import Trace
    actions = service(backend, tmp_path, demo_failures=("before",))
    draft = await propose(actions)
    trace = Trace(None, context(), "approve")
    committed = await actions.approve(context(), draft["action_id"], draft["body_hash"], trace)
    assert committed["status"] == "COMMITTED"
    assert committed["attempts"] == 2
    assert committed["idempotency_key"] == draft["idempotency_key"]
    assert len(comments(backend)) == 1
    assert comments(backend)[0]["body"] == draft["body"]
    publish_events = [e for e in trace.events if e["stage"] == "http.publish"]
    assert [e["status"] for e in publish_events] == [503, 201]
    assert {e["idempotency_key"] for e in publish_events} == {draft["idempotency_key"]}


@pytest.mark.anyio
async def test_concurrent_approvals_have_one_durable_consent_and_one_comment(backend, tmp_path):
    from assistant.tracing import Trace
    actions = service(backend, tmp_path)
    draft = await propose(actions)
    other_instance = service(backend, tmp_path)
    await asyncio.gather(*[
        instance.approve(context(), draft["action_id"], draft["body_hash"], Trace(None, context(), "approve"))
        for instance in (actions, other_instance)
    ])
    assert len(comments(backend)) == 1
    with actions.store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 1
    assert actions.inspect(context(), draft["action_id"])["status"] == "COMMITTED"


@pytest.mark.anyio
async def test_stale_proposal_stops_at_preflight_without_silent_rebase(backend, tmp_path):
    from assistant.tracing import Trace
    actions = service(backend, tmp_path)
    draft = await propose(actions)
    backend.post("/v1/tenants/northstar/tickets/NS-104/comments", json={
        "body": "Another reviewed update", "expected_version": 1,
        "idempotency_key": "external-update", "approval_reference": "external-human"}).raise_for_status()
    trace = Trace(None, context(), "approve")
    stale = await actions.approve(context(), draft["action_id"], draft["body_hash"], trace)
    assert stale["status"] == "CONFLICT"
    assert stale["attempts"] == 0
    assert len(comments(backend)) == 1
    assert not any(e["stage"] == "http.publish" for e in trace.events)


@pytest.mark.anyio
async def test_unknown_commit_and_restart_reconcile_without_republish(backend, tmp_path):
    from assistant.errors import AssistantError
    from assistant.tracing import Trace

    class UnavailableActionReader:
        async def action(self, tenant, key, trace):
            raise AssistantError("backend_unavailable")

    actions = service(backend, tmp_path, demo_failures=("after",), reader=UnavailableActionReader())
    draft = await propose(actions)
    unknown = await actions.approve(context(), draft["action_id"], draft["body_hash"], Trace(None, context(), "approve"))
    assert unknown["status"] == "UNKNOWN"
    assert unknown["attempts"] == 1
    restarted = service(backend, tmp_path)
    trace = Trace(None, context(), "reconcile")
    recovered = await restarted.reconcile(context(), draft["action_id"], trace)
    assert recovered["status"] == "COMMITTED"
    assert recovered["idempotency_key"] == draft["idempotency_key"]
    assert len(comments(backend)) == 1
    assert not any(e["stage"] == "http.publish" for e in trace.events)


@pytest.mark.anyio
async def test_restart_with_sending_intent_recovers_committed_receipt(backend, tmp_path):
    from assistant.backend_client import CommentPublisher
    from assistant.config import Settings
    from assistant.tracing import Trace

    class CrashAfterPublish(CommentPublisher):
        async def publish(self, action, trace):
            await super().publish(action, trace)
            raise RuntimeError("simulated process death before recording receipt")

    settings = Settings(backend_url=str(backend.base_url).rstrip("/"))
    actions = service(backend, tmp_path, publisher=CrashAfterPublish(settings))
    draft = await propose(actions)
    with pytest.raises(RuntimeError):
        await actions.approve(context(), draft["action_id"], draft["body_hash"], Trace(None, context(), "approve"))
    assert actions.inspect(context(), draft["action_id"])["status"] == "SENDING"
    recovered = await service(backend, tmp_path).reconcile(context(), draft["action_id"], Trace(None, context(), "reconcile"))
    assert recovered["status"] == "COMMITTED"
    assert len(comments(backend)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("different_body", [False, True])
async def test_backend_409_race_and_key_reuse_are_terminal(backend, tmp_path, different_body):
    from assistant.backend_client import CommentPublisher
    from assistant.config import Settings
    from assistant.tracing import Trace

    class RacingPublisher(CommentPublisher):
        async def publish(self, action, trace):
            async with httpx.AsyncClient(base_url=self.settings.backend_url, trust_env=False) as client:
                response = await client.post("/v1/tenants/northstar/tickets/NS-104/comments", json={
                    "body": "Racing write", "expected_version": 1,
                    "idempotency_key": action["idempotency_key"] if different_body else "race-other-key",
                    "approval_reference": "external-reviewed-write"})
                response.raise_for_status()
            return await super().publish(action, trace)

    settings = Settings(backend_url=str(backend.base_url).rstrip("/"))
    actions = service(backend, tmp_path, publisher=RacingPublisher(settings))
    draft = await propose(actions)
    result = await actions.approve(context(), draft["action_id"], draft["body_hash"], Trace(None, context(), "approve"))
    assert result["status"] == ("INVARIANT_VIOLATION" if different_body else "CONFLICT")
    assert result["attempts"] == 1
    assert len(comments(backend)) == 1


@pytest.mark.anyio
async def test_timed_out_writes_that_commit_later_remain_reconcilable(backend, tmp_path):
    from assistant.backend_client import CommentPublisher
    from assistant.config import Settings
    from assistant.tracing import Trace
    release = asyncio.Event()

    class InFlightPublisher(CommentPublisher):
        def __init__(self, settings):
            super().__init__(settings)
            self.pending = []

        async def publish(self, action, trace):
            async def late_commit():
                await release.wait()
                return await super(InFlightPublisher, self).publish(action, trace)
            self.pending.append(asyncio.create_task(late_commit()))
            raise httpx.ReadTimeout("client stopped waiting; upstream may still commit")

    publisher = InFlightPublisher(Settings(backend_url=str(backend.base_url).rstrip("/")))
    actions = service(backend, tmp_path, publisher=publisher)
    draft = await propose(actions)
    try:
        result = await actions.approve(context(), draft["action_id"], draft["body_hash"], Trace(None, context(), "approve"))
    finally:
        release.set()
        await asyncio.gather(*publisher.pending)
    assert result["status"] == "UNKNOWN"
    assert result["attempts"] == 2
    assert len(comments(backend)) == 1
    recovered = await service(backend, tmp_path).reconcile(context(), draft["action_id"], Trace(None, context(), "reconcile"))
    assert recovered["status"] == "COMMITTED"
    assert recovered["idempotency_key"] == draft["idempotency_key"]
    assert len(comments(backend)) == 1


@pytest.mark.anyio
async def test_comment_length_validation_before_any_publish(backend, tmp_path):
    from assistant.errors import AssistantError
    from assistant.tracing import Trace
    actions = service(backend, tmp_path)
    trace = Trace(None, context(), "draft")
    ticket = await actions.mcp.get_ticket(context(), "NS-104", trace)
    for body in ("", " ", "x" * 2001):
        with pytest.raises(AssistantError, match="invalid_comment_body"):
            actions.propose(context(), ticket, body, trace)
    assert len(actions.propose(context(), ticket, "x" * 2000, trace)["body"]) == 2000
    assert comments(backend) == []

