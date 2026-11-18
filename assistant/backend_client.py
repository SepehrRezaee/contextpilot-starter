"""Separate read capabilities and approval-only publishing capability."""
import threading
from collections import deque
from functools import partial
from urllib.parse import quote

import anyio
import httpx

from .config import Settings
from .errors import AssistantError
from .schemas import TENANTS
from .tracing import Trace


def tenant_path(tenant: str) -> str:
    if tenant not in TENANTS:
        raise AssistantError("tenant_scope", 403)
    return f"/v1/tenants/{tenant}"


class HttpPool:
    """One pooled client for the backend, driven from worker threads.

    Measured on Windows with scripts/benchmark.py: building an
    ``httpx.AsyncClient`` per call cost ~17 ms, and even a pooled AsyncClient took
    ~10 ms per request and did not overlap concurrent requests on the proactor loop.
    A pooled sync client in a worker thread takes ~2 ms and scales with concurrency.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None

    def _get(self) -> httpx.Client:
        with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(base_url=self._settings.backend_url,
                                            timeout=self._settings.http_timeout, trust_env=False)
            return self._client

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return await anyio.to_thread.run_sync(partial(self._get().request, method, path, **kwargs))

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None


class BackendReader:
    def __init__(self, settings: Settings, *, http: HttpPool | None = None):
        self.settings = settings
        self._http = http or HttpPool(settings)

    def close(self) -> None:
        self._http.close()

    async def _get(self, path: str, trace: Trace, *, resource: str, missing_ok=False):
        for attempt in range(1, self.settings.read_attempts + 1):
            try:
                response = await self._http.request("GET", path)
                trace.event("http.read", resource=resource, status=response.status_code, attempt=attempt)
                if response.status_code == 404 and missing_ok:
                    return None
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise AssistantError("backend_read_rejected") from exc
                if not isinstance(exc, httpx.HTTPStatusError):
                    trace.event("http.read_error", resource=resource, attempt=attempt, kind=type(exc).__name__)
                if attempt == self.settings.read_attempts:
                    raise AssistantError("backend_unavailable") from exc
                await anyio.sleep(0.05)

    async def documents(self, tenant: str, trace: Trace) -> list[dict]:
        payload = await self._get(tenant_path(tenant) + "/documents", trace, resource="documents")
        if not isinstance(payload, dict) or payload.get("tenant_id") != tenant or not isinstance(payload.get("documents"), list):
            raise AssistantError("malformed_documents")
        return payload["documents"]

    async def action(self, tenant: str, key: str, trace: Trace) -> dict | None:
        return await self._get(tenant_path(tenant) + "/actions/" + quote(key, safe=""), trace,
                               resource="action_status", missing_ok=True)


class CommentPublisher:
    """Only Actions receives this capability. Failure controls are Python bootstrap only."""

    def __init__(self, settings: Settings, *, demo_failures: tuple[str, ...] = (), http: HttpPool | None = None):
        if any(f not in ("before", "after") for f in demo_failures):
            raise ValueError("invalid fixture failure mode")
        self.settings = settings
        self._demo_failures = deque(demo_failures)
        self._http = http or HttpPool(settings)

    def close(self) -> None:
        self._http.close()

    async def publish(self, action: dict, trace: Trace) -> httpx.Response:
        if not action.get("approval_reference") or action["status"] != "SENDING":
            raise AssistantError("approval_required", 409)
        headers = {}
        if self._demo_failures:
            headers["X-Demo-Failure"] = self._demo_failures.popleft()
        payload = {key: action[key] for key in ("body", "expected_version", "idempotency_key", "approval_reference")}
        path = tenant_path(action["tenant_id"]) + "/tickets/" + quote(action["ticket_id"], safe="") + "/comments"
        response = await self._http.request("POST", path, json=payload, headers=headers)
        trace.event("http.publish", status=response.status_code, idempotency_key=action["idempotency_key"])
        return response
