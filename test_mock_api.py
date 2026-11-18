import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import server as mock


class BackendContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mock.reset()
        cls.instance = ThreadingHTTPServer(("127.0.0.1", 0), mock.API)
        cls.base = f"http://127.0.0.1:{cls.instance.server_port}"
        cls.thread = threading.Thread(target=cls.instance.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.instance.shutdown()
        cls.instance.server_close()

    def setUp(self):
        self.call("POST", "/__admin/reset", {})

    def call(self, method, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(self.base+path, method=method, data=data, headers={"Content-Type":"application/json", **(headers or {})})
        try:
            res = urlopen(req, timeout=3)
        except HTTPError as exc:
            res = exc
        return res.status, json.loads(res.read().decode())

    def test_document_isolation(self):
        status, result = self.call("GET", "/v1/tenants/harbor/documents")
        self.assertEqual(status, 200)
        self.assertNotIn("N-POL-REFUND-2026", [x["id"] for x in result["documents"]])

    def test_lost_ack_is_committed_once(self):
        path = "/v1/tenants/northstar/tickets/NS-104/comments"
        payload = {"body":"Proposed response", "expected_version":1, "idempotency_key":"case-100", "approval_reference":"user-approved-1"}
        status, _ = self.call("POST", path, payload, {"X-Demo-Failure":"after"})
        self.assertEqual(status, 503)
        status, event = self.call("GET", "/v1/tenants/northstar/actions/case-100")
        self.assertEqual(status, 200)
        self.assertEqual(event["status"], "committed")
        status, _ = self.call("POST", path, payload)
        self.assertEqual(status, 200)
        status, ticket = self.call("GET", "/v1/tenants/northstar/tickets/NS-104")
        self.assertEqual(len(ticket["comments"]), 1)

    def test_version_conflict(self):
        path = "/v1/tenants/northstar/tickets/NS-104/comments"
        payload = {"body":"Text", "expected_version":0, "idempotency_key":"conflict-1", "approval_reference":"approved"}
        status, response = self.call("POST", path, payload)
        self.assertEqual((status, response["error"]), (409, "version_conflict"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
