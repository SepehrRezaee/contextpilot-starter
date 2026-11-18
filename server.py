#!/usr/bin/env python3
"""Deterministic local backend for ContextPilot. Python 3.10+; no dependencies."""
import argparse
import copy
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

FIXTURES = json.loads((Path(__file__).parent / "fixtures.json").read_text(encoding="utf-8"))
LOCK = threading.RLock()
STATE = {}


def reset():
    with LOCK:
        STATE.clear()
        STATE["tickets"] = {(t["tenant_id"], t["id"]): copy.deepcopy(t) for t in FIXTURES["tickets"]}
        STATE["actions"] = {}


def respond(handler, status, payload):
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass


class API(BaseHTTPRequestHandler):
    server_version = "ContextPilotMock/1.0"

    def log_message(self, fmt, *args):
        if os.getenv("QUIET") != "1":
            super().log_message(fmt, *args)

    def _route(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        return parts, parse_qs(parsed.query)

    def _delay(self, query):
        value = int(query.get("delay_ms", ["0"])[0])
        if value:
            time.sleep(min(max(value, 0), 5000) / 1000)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 32_768:
                return None
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return None

    def do_GET(self):
        parts, query = self._route()
        try:
            self._delay(query)
        except ValueError:
            return respond(self, 400, {"error":"bad_delay"})
        if parts == ["health"]:
            return respond(self, 200, {"status":"ok", "service":"contextpilot-mock"})
        if parts == ["v1", "fixtures", "sessions"]:
            return respond(self, 200, {"sessions": FIXTURES["sample_sessions"]})
        if len(parts) < 4 or parts[:2] != ["v1", "tenants"]:
            return respond(self, 404, {"error":"not_found"})
        tenant = parts[2]
        known = {"northstar", "harbor"}
        if tenant not in known:
            return respond(self, 404, {"error":"unknown_tenant"})
        if parts[3:] == ["documents"]:
            docs = [d for d in FIXTURES["documents"] if d["tenant_id"] in ("global", tenant)]
            return respond(self, 200, {"tenant_id":tenant, "documents":docs})
        if len(parts) == 5 and parts[3] == "tickets":
            with LOCK:
                ticket = copy.deepcopy(STATE["tickets"].get((tenant, parts[4])))
            if ticket is None:
                return respond(self, 404, {"error":"ticket_not_found"})
            return respond(self, 200, ticket)
        if len(parts) == 5 and parts[3] == "actions":
            with LOCK:
                event = copy.deepcopy(STATE["actions"].get((tenant, parts[4])))
            if not event:
                return respond(self, 404, {"error":"action_not_found"})
            return respond(self, 200, event)
        return respond(self, 404, {"error":"not_found"})

    def do_POST(self):
        parts, query = self._route()
        body = self._body()
        if body is None:
            return respond(self, 400, {"error":"invalid_json"})
        if parts == ["__admin", "reset"]:
            reset()
            return respond(self, 200, {"status":"reset"})
        if len(parts) != 6 or parts[:2] != ["v1", "tenants"] or parts[3] != "tickets" or parts[5] != "comments":
            return respond(self, 404, {"error":"not_found"})
        tenant, ticket_id = parts[2], parts[4]
        if tenant not in ("northstar", "harbor"):
            return respond(self, 404, {"error":"unknown_tenant"})
        required = ("body", "expected_version", "idempotency_key", "approval_reference")
        if not isinstance(body, dict) or any(k not in body for k in required):
            return respond(self, 400, {"error":"missing_required_fields", "required":required})
        comment, key, approval = body["body"], body["idempotency_key"], body["approval_reference"]
        if not all(isinstance(v, str) and v.strip() for v in (comment, key, approval)) or len(comment) > 2000 or len(key) > 128:
            return respond(self, 400, {"error":"invalid_fields"})
        if self.headers.get("X-Demo-Failure") == "before":
            return respond(self, 503, {"error":"transient_before_commit"})
        with LOCK:
            if (tenant, key) in STATE["actions"]:
                event = copy.deepcopy(STATE["actions"][(tenant,key)])
                if event["ticket_id"] != ticket_id or event["body"] != comment:
                    return respond(self, 409, {"error":"idempotency_key_reused_for_different_action"})
                return respond(self, 200, event)
            ticket = STATE["tickets"].get((tenant,ticket_id))
            if ticket is None:
                return respond(self, 404, {"error":"ticket_not_found"})
            if not isinstance(body["expected_version"], int) or ticket["version"] != body["expected_version"]:
                return respond(self, 409, {"error":"version_conflict", "current_version":ticket["version"]})
            ticket["version"] += 1
            ticket["comments"].append({"body":comment, "approval_reference":approval})
            event = {"status":"committed", "tenant_id":tenant, "ticket_id":ticket_id,
                     "body":comment, "idempotency_key":key, "version":ticket["version"]}
            STATE["actions"][(tenant,key)] = copy.deepcopy(event)
        if self.headers.get("X-Demo-Failure") == "after":
            return respond(self, 503, {"error":"simulated_ack_lost", "hint":"operation may have committed"})
        return respond(self, 201, event)


def run(port):
    reset()
    server = ThreadingHTTPServer(("127.0.0.1", port), API)
    print(f"ContextPilot mock backend: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run(args.port)
