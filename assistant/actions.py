"""Durable human-consent boundary and conservative idempotent recovery.

State machine (status column):

    PROPOSED --approve(exact hash, fresh, current ticket version)--> SENDING
    PROPOSED --stale ticket version / expired draft---------------> CONFLICT
    SENDING  --verified backend receipt---------------------------> COMMITTED
    SENDING  --409 version_conflict-------------------------------> CONFLICT
    SENDING  --409 other / receipt mismatch-----------------------> INVARIANT_VIOLATION
    SENDING  --other 4xx------------------------------------------> FAILED
    SENDING  --ambiguous and unconfirmed by key-------------------> UNKNOWN
    UNKNOWN  --reconcile finds the key----------------------------> COMMITTED
    UNKNOWN  --approve again (same hash) and key still absent-----> SENDING (same key)

An ``approvals`` row exists exactly when a publish was authorized. The body, ticket
version and idempotency key never change after proposal, so every retry is the same
request and the backend's key de-duplication makes it safe.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from .backend_client import BackendReader, CommentPublisher, HttpPool
from .config import Settings
from .errors import AssistantError
from .mcp_client import TicketMCP
from .schemas import Context, Ticket, validate_ticket_scope
from .storage import Store, timestamp
from .tracing import Trace

# Consent is recorded and the outcome is not yet confirmed.
RESUMABLE = {"UNKNOWN", "APPROVED"}  # no worker owns them; APPROVED only exists in 0.1 databases
PENDING = RESUMABLE | {"SENDING"}
UNCONSENTED = {"PROPOSED", "CONFLICT"}  # the only states that may lack an approval record
IMMUTABLE = ("action_id", "tenant_id", "user_id", "ticket_id", "body", "body_hash",
             "expected_version", "idempotency_key", "created_at")
NEXT_STEP = {
    "PROPOSED": "Nothing has been sent. Review the exact body, then approve this action_id with its body_hash.",
    "SENDING": "A publish attempt is recorded. Call reconcile to confirm its outcome.",
    "COMMITTED": "Published once and confirmed by the backend receipt. Nothing further is required.",
    "UNKNOWN": "Outcome unconfirmed. Call reconcile, or approve again to retry with the same idempotency key.",
    "APPROVED": "Approved but never sent. Approve again to publish with the same idempotency key.",
    "CONFLICT": "The ticket changed or the draft expired. Request a new draft and review it again.",
    "FAILED": "The backend rejected this comment. Request a new draft.",
    "INVARIANT_VIOLATION": "The backend record does not match this proposal. Do not retry; investigate using the trace.",
}


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def binding(action: dict) -> str:
    return digest(json.dumps({key: action[key] for key in IMMUTABLE}, sort_keys=True, separators=(",", ":")))


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None


class Actions:
    def __init__(self, settings: Settings, store: Store, *, mcp=None, reader=None, publisher=None):
        self.settings, self.store = settings, store
        self._http = HttpPool(settings)
        self.mcp = mcp or TicketMCP(settings)
        self.reader = reader or BackendReader(settings, http=self._http)
        self._publisher = publisher or CommentPublisher(settings, http=self._http)
        store.script("""
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL, body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
                body_hash TEXT NOT NULL, expected_version INTEGER NOT NULL CHECK(expected_version>0),
                idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                binding_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN
                ('PROPOSED','APPROVED','SENDING','COMMITTED','CONFLICT','UNKNOWN','FAILED','INVARIANT_VIOLATION')),
                approval_reference TEXT, approved_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, receipt_json TEXT);
            CREATE TABLE IF NOT EXISTS approvals (
                action_id TEXT PRIMARY KEY REFERENCES actions(action_id),
                tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                body_hash TEXT NOT NULL, approval_reference TEXT NOT NULL UNIQUE, approved_at TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS proposal_immutable BEFORE UPDATE OF
                action_id,tenant_id,user_id,ticket_id,body,body_hash,expected_version,idempotency_key,created_at,binding_hash
                ON actions BEGIN SELECT RAISE(ABORT,'immutable proposal'); END;
            CREATE TRIGGER IF NOT EXISTS approval_immutable BEFORE UPDATE ON approvals
                BEGIN SELECT RAISE(ABORT,'immutable approval'); END;
            CREATE TRIGGER IF NOT EXISTS approval_reference_immutable BEFORE UPDATE OF approval_reference ON actions
                WHEN OLD.approval_reference IS NOT NULL
                BEGIN SELECT RAISE(ABORT,'immutable approval reference'); END;
        """)

    async def aclose(self) -> None:
        await self.mcp.aclose()
        self._http.close()

    # --- reads -------------------------------------------------------------------

    def _load(self, connection, context: Context, action_id: str) -> dict:
        """Owner-scoped read that re-verifies the proposal and its consent record."""
        row = connection.execute("SELECT * FROM actions WHERE action_id=? AND tenant_id=? AND user_id=?",
                                 (action_id, context.tenant_id, context.user_id)).fetchone()
        if row is None:
            raise AssistantError("action_not_found", 404)
        action = dict(row)
        validate_ticket_scope(context.tenant_id, action["ticket_id"])
        if not hmac.compare_digest(action["body_hash"], digest(action["body"])) or not hmac.compare_digest(action["binding_hash"], binding(action)):
            raise AssistantError("proposal_integrity_violation", 409)
        approval = connection.execute("SELECT * FROM approvals WHERE action_id=?", (action_id,)).fetchone()
        consented = action["approval_reference"] is not None
        if consented != (approval is not None) or (not consented and action["status"] not in UNCONSENTED):
            raise AssistantError("approval_integrity_violation", 409)
        if consented and any(approval[key] != action[key] for key in
                             ("tenant_id", "user_id", "body_hash", "approval_reference", "approved_at")):
            raise AssistantError("approval_integrity_violation", 409)
        return action

    @staticmethod
    def public(action: dict) -> dict:
        return {**{key: action[key] for key in IMMUTABLE},
                "status": action["status"], "approved_at": action["approved_at"],
                "attempts": action["attempts"], "error_code": action["error_code"],
                "receipt": json.loads(action["receipt_json"]) if action["receipt_json"] else None,
                "requires_approval": action["status"] == "PROPOSED",
                "next_step": NEXT_STEP[action["status"]]}

    def inspect(self, context: Context, action_id: str) -> dict:
        with self.store.connection() as connection:
            return self.public(self._load(connection, context, action_id))

    # --- propose -----------------------------------------------------------------

    def propose(self, context: Context, ticket: Ticket, body: str, trace: Trace) -> dict:
        validate_ticket_scope(context.tenant_id, ticket.id)
        if ticket.tenant_id != context.tenant_id:
            raise AssistantError("ticket_scope", 403)
        if not isinstance(body, str) or not body.strip() or len(body) > 2000:
            raise AssistantError("invalid_comment_body", 422)
        action = {"action_id": str(uuid.uuid4()), "tenant_id": context.tenant_id,
                  "user_id": context.user_id, "ticket_id": ticket.id, "body": body,
                  "body_hash": digest(body), "expected_version": ticket.version,
                  "idempotency_key": "cp-" + uuid.uuid4().hex, "created_at": timestamp()}
        action["binding_hash"] = binding(action)
        action["status"] = "PROPOSED"
        columns = list(action)
        with self.store.connection(immediate=True) as connection:
            connection.execute(f"INSERT INTO actions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(action.values()))
            stored = self._load(connection, context, action["action_id"])
        trace.event("action.transition", action_id=action["action_id"], previous=None, status="PROPOSED", body_hash=action["body_hash"])
        return self.public(stored)

    # --- approve and publish -------------------------------------------------------

    async def approve(self, context: Context, action_id: str, body_hash: str, trace: Trace) -> dict:
        """Record this owner's consent for exactly this body, then publish it once."""
        with self.store.connection() as connection:
            action = self._load(connection, context, action_id)
        if not hmac.compare_digest(action["body_hash"], body_hash):
            raise AssistantError("body_hash_mismatch", 409)
        if action["status"] in RESUMABLE:
            return await self._resume(context, action, trace)
        if action["status"] in ("COMMITTED", "SENDING"):
            trace.event("action.approval_repeat", action_id=action_id, status=action["status"])
            return self.public(action)
        if action["status"] != "PROPOSED":
            raise AssistantError("action_not_approvable", 409)
        # Read-only checks first: a failed tool or a stale draft must not consume consent.
        age = datetime.now(timezone.utc) - datetime.fromisoformat(action["created_at"])
        if age > timedelta(hours=self.settings.proposal_ttl_hours):
            return self.public(self._reject_stale(context, action_id, "proposal_expired", trace))
        ticket = await self.mcp.get_ticket(context, action["ticket_id"], trace)
        if ticket.version != action["expected_version"]:
            return self.public(self._reject_stale(context, action_id, "stale_ticket_version", trace))
        approval, approved_at = "approval-" + uuid.uuid4().hex, timestamp()
        with self.store.connection(immediate=True) as connection:
            action = self._load(connection, context, action_id)
            if action["status"] == "PROPOSED":
                # Consent and the intent to send become durable together, before any side effect.
                connection.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?)",
                                   (action_id, context.tenant_id, context.user_id, context.session_id, body_hash, approval, approved_at))
                connection.execute("UPDATE actions SET status='SENDING',approval_reference=?,approved_at=?,attempts=1 WHERE action_id=?",
                                   (approval, approved_at, action_id))
                claimed = self._load(connection, context, action_id)
            else:
                claimed = None  # a concurrent approval of the same draft won
        if claimed is None:
            trace.event("action.approval_repeat", action_id=action_id, status=action["status"])
            return self.public(action)
        trace.event("action.transition", action_id=action_id, previous="PROPOSED", status="SENDING",
                    approval_reference=approval, attempt=1)
        return self.public(await self._publish(context, claimed, trace))

    async def _resume(self, context: Context, action: dict, trace: Trace) -> dict:
        """Explicit re-approval of an unconfirmed action: look it up, then retry the same key."""
        outcome, resolved = await self._resolve(context, action, trace)
        if outcome != "not_found":
            return self.public(resolved)
        claimed = self._claim(context, action["action_id"], trace, allowed=RESUMABLE)
        if claimed is None:
            return self.inspect(context, action["action_id"])
        return self.public(await self._publish(context, claimed, trace))

    def _claim(self, context: Context, action_id: str, trace: Trace, *, allowed: set[str]) -> dict | None:
        """Atomically take the next publish attempt; None if the action moved on meanwhile."""
        with self.store.connection(immediate=True) as connection:
            action = self._load(connection, context, action_id)
            if action["status"] not in allowed:
                return None
            connection.execute("UPDATE actions SET status='SENDING',attempts=attempts+1,error_code=NULL WHERE action_id=?", (action_id,))
            claimed = self._load(connection, context, action_id)
        if action["status"] == "SENDING":
            trace.event("action.retry", action_id=action_id, idempotency_key=claimed["idempotency_key"], attempt=claimed["attempts"])
        else:
            trace.event("action.transition", action_id=action_id, previous=action["status"], status="SENDING", attempt=claimed["attempts"])
        return claimed

    async def _publish(self, context: Context, action: dict, trace: Trace) -> dict:
        """POST the stored body under its stable key; settle every unclear result by that key."""
        action_id = action["action_id"]
        for retries_left in range(self.settings.publish_attempts - 1, -1, -1):
            try:
                response = await self._publisher.publish(action, trace)
            except httpx.HTTPError:
                response = None
                trace.event("http.publish_error", code="ambiguous_transport_failure", idempotency_key=action["idempotency_key"])
            if response is not None and 200 <= response.status_code < 300:
                event = _json(response)
                if event is not None:
                    return self._record_receipt(context, action, event, trace)
            elif response is not None and response.status_code == 409:
                payload = _json(response)
                code = payload.get("error") if isinstance(payload, dict) else None
                status = "CONFLICT" if code == "version_conflict" else "INVARIANT_VIOLATION"
                return self._transition(context, action_id, status, trace, code=code or "invalid_conflict_response")
            elif response is not None and 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                return self._transition(context, action_id, "FAILED", trace, code="publish_rejected")
            # No decision and no retry after an unclear response until the SAME key is checked.
            outcome, resolved = await self._resolve(context, action, trace)
            if outcome != "not_found":
                return resolved
            if not retries_left:
                # A timed-out POST may still commit after this 404, so stay recoverable.
                return self._transition(context, action_id, "UNKNOWN", trace, code="publish_unconfirmed_after_retries")
            claimed = self._claim(context, action_id, trace, allowed={"SENDING"})
            if claimed is None:
                break
            action = claimed
        with self.store.connection() as connection:
            return self._load(connection, context, action_id)

    # --- settle ------------------------------------------------------------------

    def _reject_stale(self, context: Context, action_id: str, code: str, trace: Trace) -> dict:
        """A draft that can no longer be published as reviewed; no consent is recorded."""
        with self.store.connection(immediate=True) as connection:
            connection.execute("UPDATE actions SET status='CONFLICT',error_code=? WHERE action_id=? AND tenant_id=? AND user_id=? AND status='PROPOSED'",
                               (code, action_id, context.tenant_id, context.user_id))
            action = self._load(connection, context, action_id)
        trace.event("action.transition", action_id=action_id, previous="PROPOSED", status=action["status"], error_code=action["error_code"])
        return action

    def _transition(self, context: Context, action_id: str, status: str, trace: Trace, *, code=None, receipt=None) -> dict:
        with self.store.connection(immediate=True) as connection:
            action = self._load(connection, context, action_id)
            if action["status"] not in PENDING:
                return action
            previous = action["status"]
            connection.execute("UPDATE actions SET status=?,error_code=?,receipt_json=COALESCE(?,receipt_json) WHERE action_id=? AND status=?",
                               (status, code, json.dumps(receipt) if receipt else None, action_id, previous))
            action = self._load(connection, context, action_id)
        trace.event("action.transition", action_id=action_id, previous=previous, status=status, error_code=code)
        return action

    def _record_receipt(self, context: Context, action: dict, event, trace: Trace) -> dict:
        valid = (isinstance(event, dict) and event.get("status") == "committed"
                 and all(event.get(key) == action[key] for key in ("tenant_id", "ticket_id", "idempotency_key", "body"))
                 and type(event.get("version")) is int and event["version"] == action["expected_version"] + 1)
        if not valid:
            return self._transition(context, action["action_id"], "INVARIANT_VIOLATION", trace, code="receipt_mismatch")
        receipt = {key: event[key] for key in ("status", "tenant_id", "ticket_id", "idempotency_key", "version")}
        receipt["body_hash"] = action["body_hash"]
        return self._transition(context, action["action_id"], "COMMITTED", trace, receipt=receipt)

    async def _resolve(self, context: Context, action: dict, trace: Trace) -> tuple[str, dict]:
        """Ask the backend what happened to this idempotency key. Read-only."""
        try:
            event = await self.reader.action(context.tenant_id, action["idempotency_key"], trace)
        except AssistantError:
            trace.event("action.reconcile", action_id=action["action_id"], idempotency_key=action["idempotency_key"], outcome="unknown")
            return "unknown", self._transition(context, action["action_id"], "UNKNOWN", trace, code="reconciliation_unavailable")
        if event is None:
            trace.event("action.reconcile", action_id=action["action_id"], idempotency_key=action["idempotency_key"], outcome="not_found")
            return "not_found", action
        resolved = self._record_receipt(context, action, event, trace)
        outcome = "committed" if resolved["status"] == "COMMITTED" else "invariant_violation"
        trace.event("action.reconcile", action_id=action["action_id"], idempotency_key=action["idempotency_key"], outcome=outcome)
        return outcome, resolved

    async def reconcile(self, context: Context, action_id: str, trace: Trace) -> dict:
        """Owner-scoped, read-only confirmation; it never publishes."""
        with self.store.connection() as connection:
            action = self._load(connection, context, action_id)
        if action["status"] not in PENDING:
            return self.public(action)
        outcome, action = await self._resolve(context, action, trace)
        if outcome == "not_found":
            # A GET cannot prove an in-flight request will never commit; only an explicit
            # re-approval may send again, and it reuses the same key.
            action = self._transition(context, action_id, "UNKNOWN", trace, code="not_found_no_automatic_republish")
        return self.public(action)
