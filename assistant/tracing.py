import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schemas import Context

_WRITE_LOCK = threading.Lock()


class Trace:
    """Explicit redacted events: callers pass IDs/codes, never prompts or bodies."""

    def __init__(self, path: Path | None, context: Context | None, intent: str):
        self.path = path
        self.trace_id = str(uuid.uuid4())
        self.context = context
        self.intent = intent
        self.model_adapter = "deterministic"
        # perf_counter, not monotonic: on Windows monotonic() ticks every ~15.6 ms, which
        # reported 0.0 ms for whole requests and quantized every stage offset.
        self.started = time.perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.events: list[dict] = []
        self.selected_document_ids: list[str] = []
        self._finished: dict | None = None

    def event(self, stage: str, **fields):
        self.events.append({"stage": stage, "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 3), **fields})

    def _tools(self) -> list[str]:
        names = []
        for event in self.events:
            if event["stage"] == "mcp.tools/call":
                names.append("mcp:" + event["tool"])
            elif event["stage"] == "http.read":
                names.append("http:get_" + event["resource"])
            elif event["stage"] == "http.publish":
                names.append("http:post_comment")
        return list(dict.fromkeys(names))

    def finish(self, outcome: str) -> dict:
        if self._finished is not None:
            return self._finished
        transitions = [e for e in self.events if e["stage"] == "action.transition"]
        errors = [e["code"] for e in self.events if e["stage"] == "error"]
        result = {
            "trace_id": self.trace_id,
            **(self.context.model_dump() if self.context else {"tenant_id": None, "user_id": None, "session_id": None}),
            "started_at": self.started_at, "intent": self.intent,
            "model_adapter": self.model_adapter, "outcome": outcome,
            # Summary fields so one line answers "what did this run touch?" without reading events.
            "selected_document_ids": self.selected_document_ids,
            "tools": self._tools(),
            "action": ({"action_id": transitions[-1]["action_id"], "status": transitions[-1]["status"]}
                       if transitions else None),
            "error": errors[-1] if errors else None,
            "duration_ms": round((time.perf_counter() - self.started) * 1000, 3),
            "events": self.events,
        }
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=True) + "\n")
        self._finished = result
        return result
