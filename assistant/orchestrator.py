"""Single bounded workflow. Model output cannot select tools, scope or approval."""
import re

from .actions import Actions
from .backend_client import BackendReader, CommentPublisher, HttpPool
from .config import Settings
from .memory import ALL_KEYS, extract_preferences, parse_forget
from .mcp_client import TicketMCP
from .model_adapter import DeterministicAdapter, ModelAdapter
from .retrieval import assess, select_documents
from .schemas import TENANTS, Context, RespondRequest, validate_ticket_scope
from .storage import Store
from .tracing import Trace


class Orchestrator:
    def __init__(self, settings: Settings, *, adapter: ModelAdapter | None = None, demo_failures: tuple[str, ...] = ()):
        self.settings = settings
        self.store = Store(settings.database)
        self._http = HttpPool(settings)
        self.mcp = TicketMCP(settings)
        self.reader = BackendReader(settings, http=self._http)
        self.adapter = adapter or DeterministicAdapter()
        self.actions = Actions(settings, self.store, mcp=self.mcp, reader=self.reader,
                               publisher=CommentPublisher(settings, demo_failures=demo_failures, http=self._http))

    async def aclose(self) -> None:
        """Stop the MCP server process, close pooled HTTP connections and the database."""
        await self.mcp.aclose()
        self._http.close()
        self.store.close()

    async def respond(self, request: RespondRequest, trace: Trace) -> dict:
        context = Context(**{key: getattr(request, key) for key in Context.model_fields})
        message = request.message
        lower = message.lower()
        ticket_ids = list(dict.fromkeys(re.findall(r"\b[A-Z]{2,5}-[1-9][0-9]{0,6}\b", message.upper())))
        # Naming any other registered tenant, or a ticket outside this tenant's namespace,
        # is declined before any tool access; prose never selects the tenant.
        foreign = any(re.search(rf"\b{re.escape(other)}\b", lower) for other in TENANTS if other != context.tenant_id)
        for ticket_id in ticket_ids:
            try:
                validate_ticket_scope(context.tenant_id, ticket_id)
            except Exception:
                foreign = True
        result = {"answer": "", "citations": [], "proposed_action": None, "trace_id": trace.trace_id,
                  "intent": "clarification", "preferences": [], "evidence": None}

        def done(intent, answer):
            trace.intent = intent
            trace.event("intent", name=intent)
            result.update(intent=intent, answer=answer)
            return result

        if foreign:
            return done("scope_decline", "I can only access tickets, policies and preferences in your current tenant. Use an authorized application context for another tenant.")
        forget_keys = parse_forget(message)
        if forget_keys is not None:
            count = self.store.forget(context, forget_keys)
            trace.event("memory.forget", keys=list(forget_keys), deleted=count)
            if set(forget_keys) == set(ALL_KEYS):
                return done("forget", "Your writing preferences have been forgotten.")
            return done("forget", "Forgotten writing preferences: " + ", ".join(forget_keys) + ".")
        updates = extract_preferences(message)
        if updates:
            self.store.upsert_preferences(context, updates)
            result["preferences"] = self.store.preferences(context)
            trace.event("memory.upsert", keys=sorted(updates))
            return done("preference_update", "Saved writing preferences: " + ", ".join(f"{k}={v}" for k, v in updates.items()) + ".")
        if "preferences" in lower and any(word in lower for word in ("remember", "recall", "stored")):
            result["preferences"] = self.store.preferences(context)
            trace.event("memory.read", keys=[p["key"] for p in result["preferences"]])
            answer = ("Stored writing preferences: " + ", ".join(f"{p['key']}={p['value']}" for p in result["preferences"]) + "."
                      if result["preferences"] else "No writing preferences are stored for you in this tenant.")
            return done("memory_recall", answer)
        draft = bool(re.search(r"\b(draft|propose)\b", lower))
        if not draft and re.search(r"\b(send|publish|approve)\b", lower):
            return done("approval_required", "Publishing requires a stored draft and a separate approval request for its action ID and exact body hash.")
        if len(ticket_ids) > 1:
            return done("clarification", "Please specify one ticket per request.")
        policy_query = bool(re.search(r"\b(policy|policies|refund|billing|cancellation)\b", lower))
        if not ticket_ids and (draft or not policy_query):
            return done("clarification", "Please specify a ticket ID to investigate or draft, a policy topic, or an explicit writing preference.")
        intent = "draft" if draft else ("investigate" if ticket_ids else "policy")
        trace.intent = intent
        trace.event("intent", name=intent)
        ticket = await self.mcp.get_ticket(context, ticket_ids[0], trace) if ticket_ids else None
        documents = await self.reader.documents(context.tenant_id, trace)
        today = self.settings.today()
        query = message + (" " + ticket.subject if ticket else "")
        retrieval = select_documents(documents, context.tenant_id, query, today)
        trace.selected_document_ids = [d.id for d in retrieval.documents]
        trace.event("retrieval.select", encountered_ids=retrieval.encountered_ids,
                    selected_ids=trace.selected_document_ids, ranking=retrieval.ranking, issues=retrieval.issues)
        evidence = assess(ticket, retrieval, today)
        preferences = self.store.preferences(context)
        style = {p["key"]: p["value"] for p in preferences}
        trace.event("memory.read", keys=sorted(style))
        answer = self.adapter.render(evidence, style, draft=draft)
        trace.event("model.render", mode="deterministic", assessment=evidence.assessment)
        result.update(answer=answer, citations=evidence.citations, intent=intent,
                      preferences=preferences, evidence=evidence.public())
        if draft:
            result["proposed_action"] = self.actions.propose(context, ticket, answer, trace)
        return result

