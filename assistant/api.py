"""Local demo API. JSON context stands in for authenticated application claims."""
import inspect
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .errors import AssistantError
from .orchestrator import Orchestrator
from .schemas import ApproveRequest, Context, Identifier, RespondRequest, Tenant
from .tracing import Trace


class RespondResponse(BaseModel):
    answer: str
    citations: list[str]
    proposed_action: dict | None
    trace_id: str
    intent: str
    preferences: list[dict] = Field(default_factory=list)
    evidence: dict | None = None


def create_app(settings: Settings | None = None, *, demo_failures: tuple[str, ...] = ()) -> FastAPI:
    settings = settings or Settings.from_env()
    orchestrator = Orchestrator(settings, demo_failures=demo_failures)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await orchestrator.aclose()

    app = FastAPI(title="ContextPilot candidate", version="0.2.0", lifespan=lifespan,
                  description="Local demo: tenant/user/session fields are trusted application context. Separate hash-bound human approval is required to publish.")
    app.state.orchestrator = orchestrator

    async def execute(context: Context, intent: str, operation):
        trace = Trace(settings.trace_path, context, intent)
        try:
            result = operation(trace)
            if inspect.isawaitable(result):
                result = await result
        except AssistantError as exc:
            trace.event("error", code=exc.code)
            trace.finish("error")
            return JSONResponse(status_code=exc.status, content={"error": exc.code, "trace_id": trace.trace_id})
        except Exception:
            trace.event("error", code="internal_error")
            trace.finish("error")
            return JSONResponse(status_code=500, content={"error": "internal_error", "trace_id": trace.trace_id})
        trace.finish("ok")
        return {**result, "trace_id": trace.trace_id}

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo/log invalid request bodies, secrets, or spoofed identity fields.
        trace = Trace(settings.trace_path, None, "validation")
        trace.event("error", code="invalid_request")
        trace.finish("error")
        return JSONResponse(status_code=422, content={"error": "invalid_request", "trace_id": trace.trace_id})

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "contextpilot-assistant", "model_adapter": "deterministic"}

    def trusted_query(tenant_id: Annotated[Tenant, Query()],
                      actor_user_id: Annotated[Identifier, Query(alias="user_id")],
                      session_id: Annotated[Identifier, Query()]) -> Context:
        # An explicit Query alias prevents FastAPI from binding the actor to
        # the /users/{user_id} resource path instead of the caller's context.
        return Context(tenant_id=tenant_id, user_id=actor_user_id, session_id=session_id)

    @app.post("/assistant/respond", response_model=RespondResponse)
    async def respond(body: RespondRequest):
        context = Context(**{key: getattr(body, key) for key in Context.model_fields})
        return await execute(context, "respond", lambda trace: orchestrator.respond(body, trace))

    @app.post("/assistant/actions/{action_id}/approve")
    async def approve(action_id: UUID, body: ApproveRequest):
        context = Context(**{key: getattr(body, key) for key in Context.model_fields})

        async def operation(trace):
            return {"action": await orchestrator.actions.approve(context, str(action_id), body.body_hash, trace)}
        return await execute(context, "approve", operation)

    @app.get("/assistant/actions/{action_id}")
    async def inspect_action(action_id: UUID, context: Annotated[Context, Depends(trusted_query)]):
        return await execute(context, "action_inspect", lambda trace: {"action": orchestrator.actions.inspect(context, str(action_id))})

    @app.post("/assistant/actions/{action_id}/reconcile")
    async def reconcile(action_id: UUID, body: Context):
        async def operation(trace):
            return {"action": await orchestrator.actions.reconcile(body, str(action_id), trace)}
        return await execute(body, "reconcile", operation)

    def check_user(user_id, context):
        if user_id != context.user_id:
            raise AssistantError("user_not_found", 404)

    @app.get("/assistant/users/{user_id}/memory")
    async def memory(user_id: Identifier, context: Annotated[Context, Depends(trusted_query)]):
        def operation(trace):
            check_user(user_id, context)
            preferences = orchestrator.store.preferences(context)
            trace.event("memory.read", keys=[p["key"] for p in preferences])
            return {"preferences": preferences}
        return await execute(context, "memory_inspect", operation)

    @app.post("/assistant/users/{user_id}/memory/forget")
    async def forget(user_id: Identifier, body: Context):
        def operation(trace):
            check_user(user_id, body)
            count = orchestrator.store.forget(body)
            trace.event("memory.forget", deleted=count)
            return {"status": "forgotten", "deleted": count}
        return await execute(body, "forget", operation)

    return app

