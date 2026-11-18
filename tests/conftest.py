"""All integration reads/writes cross a real localhost HTTP boundary."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
# pytest creates owner-only directories, while getpass may report the same
# username for both the sandbox and the normal Windows account. Never reuse
# their shared pytest-of-{user} bucket across invocations.
(ROOT / "tmp").mkdir(exist_ok=True)
os.environ["PYTEST_DEBUG_TEMPROOT"] = tempfile.mkdtemp(prefix="pytest-run-", dir=ROOT / "tmp")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def fixture_url(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("backend") / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--port", str(port)],
            cwd=str(ROOT), env={**os.environ, "QUIET": "1", "PYTHONUTF8": "1"},
            stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(base_url=url, trust_env=False, timeout=0.3) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding="utf-8"))
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("fixture did not become healthy")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def backend(fixture_url):
    with httpx.Client(base_url=fixture_url, trust_env=False, timeout=3) as client:
        client.post("/__admin/reset", json={}).raise_for_status()
        yield client

