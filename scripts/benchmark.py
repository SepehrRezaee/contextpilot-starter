"""Black-box latency/throughput benchmark over the public assistant API.

It starts the supplied fixture backend and the assistant as real subprocesses and
only speaks HTTP, so the same script measures any checkout of the candidate code:

    python scripts/benchmark.py                       # this checkout
    python scripts/benchmark.py --root path/to/other  # another checkout (e.g. a baseline)

Nothing here imports the assistant package. Numbers are wall-clock, localhost only.
"""
import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
CONTEXT = {"tenant_id": "northstar", "user_id": "bench-user", "session_id": "bench"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(command, cwd, env, port, log_path):
    log = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=log, stderr=log,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 30
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=0.5, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.close()
                raise RuntimeError(f"{command[:3]} exited early:\n{Path(log_path).read_text(encoding='utf-8')[-2000:]}")
            try:
                if client.get("/health").status_code == 200:
                    return process, log
            except httpx.HTTPError:
                time.sleep(0.05)
    process.kill()
    log.close()
    raise RuntimeError("startup timed out")


def stop(process, log):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    log.close()


def process_tree(pid: int) -> list[dict]:
    """Working-set figures for a process and its descendants (Windows CIM, else /proc)."""
    if sys.platform == "win32":
        script = ("$all = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,PeakWorkingSetSize;"
                  f"$queue = @({pid}); $seen = @();"
                  "while ($queue.Count) { $p = $queue[0]; $queue = @($queue | Select-Object -Skip 1); $seen += $p;"
                  " $queue += @($all | Where-Object { $_.ParentProcessId -eq $p } | ForEach-Object { $_.ProcessId }) };"
                  "$all | Where-Object { $seen -contains $_.ProcessId } | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        rows = json.loads(out) if out else []
        rows = rows if isinstance(rows, list) else [rows]
        # WorkingSetSize is bytes; PeakWorkingSetSize is kilobytes in Win32_Process.
        return [{"pid": r["ProcessId"], "name": r["Name"], "working_set_mb": round(r["WorkingSetSize"] / 2**20, 1),
                 "peak_working_set_mb": round(r["PeakWorkingSetSize"] / 2**10, 1)} for r in rows]
    rows = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            status = dict(line.split(":", 1) for line in (entry / "status").read_text().splitlines() if ":" in line)
        except OSError:
            continue
        rows.append({"pid": int(entry.name), "ppid": int(status["PPid"]), "name": status["Name"].strip(),
                     "working_set_mb": round(int(status.get("VmRSS", "0 kB").split()[0]) / 1024, 1),
                     "peak_working_set_mb": round(int(status.get("VmHWM", "0 kB").split()[0]) / 1024, 1)})
    keep, changed = {pid}, True
    while changed:
        changed = False
        for row in rows:
            if row["ppid"] in keep and row["pid"] not in keep:
                keep.add(row["pid"])
                changed = True
    return [{k: v for k, v in row.items() if k != "ppid"} for row in rows if row["pid"] in keep]


def summarize(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    return {"n": len(ordered), "p50_ms": round(statistics.median(ordered), 2),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 2),
            "mean_ms": round(statistics.fmean(ordered), 2), "min_ms": round(ordered[0], 2), "max_ms": round(ordered[-1], 2)}


def timed(call):
    start_time = time.perf_counter()
    result = call()
    return (time.perf_counter() - start_time) * 1000, result


def run(root: Path, iterations: int, concurrency: int) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="contextpilot-bench-"))
    traces = workdir / "traces.jsonl"
    backend_port, api_port = free_port(), free_port()
    base_env = {**os.environ, "QUIET": "1", "PYTHONUTF8": "1"}
    backend, backend_log = start([sys.executable, str(REPO / "server.py"), "--port", str(backend_port)],
                                 REPO, base_env, backend_port, workdir / "backend.log")
    api_env = {**base_env, "CONTEXTPILOT_API_URL": f"http://127.0.0.1:{backend_port}",
               "CONTEXTPILOT_DB": str(workdir / "bench.sqlite3"), "CONTEXTPILOT_TRACE": str(traces),
               "CONTEXTPILOT_MCP_SERVER": str(REPO / "mcp_server.py")}
    try:
        boot, (api, api_log) = timed(lambda: start(
            [sys.executable, "-m", "uvicorn", "assistant.api:create_app", "--factory", "--host", "127.0.0.1",
             "--port", str(api_port), "--no-access-log", "--log-level", "error"], root, api_env, api_port, workdir / "api.log"))
    except BaseException:
        stop(backend, backend_log)
        raise
    package = subprocess.run([sys.executable, "-c", "import assistant; print(assistant.__file__)"], cwd=str(root),
                             capture_output=True, text=True, timeout=30).stdout.strip()
    report = {"root": str(root), "assistant_package": package, "python": sys.version.split()[0], "iterations": iterations,
              "concurrency": concurrency, "api_startup_ms": round(boot, 1), "workloads": {}}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=60, trust_env=False) as client, \
                httpx.Client(base_url=f"http://127.0.0.1:{backend_port}", timeout=10, trust_env=False) as fixture:

            def respond(message, **context):
                response = client.post("/assistant/respond", json={**CONTEXT, **context, "message": message})
                response.raise_for_status()
                return response.json()

            def sequential(name, call, count=iterations):
                first, _ = timed(call)
                report["workloads"][name] = {"first_ms": round(first, 2), **summarize([timed(call)[0] for _ in range(count)])}

            respond("I prefer concise customer replies without unnecessary introductions.")
            sequential("memory_recall (SQLite only)", lambda: respond("What writing preferences do you remember?"))
            sequential("policy_question (documents only)", lambda: respond("What is the current refund policy?"))
            sequential("ticket_investigation (MCP + documents)", lambda: respond("Summarize NS-104 and cite the current refund policy."))

            draft_ms, approve_ms = [], []
            for _ in range(max(3, iterations // 2)):
                elapsed, result = timed(lambda: respond("Draft a customer response for NS-104."))
                action = result["proposed_action"]
                draft_ms.append(elapsed)
                elapsed, approved = timed(lambda: client.post(
                    f"/assistant/actions/{action['action_id']}/approve", json={**CONTEXT, "body_hash": action["body_hash"]}))
                approved.raise_for_status()
                if approved.json()["action"]["status"] != "COMMITTED":
                    raise RuntimeError(f"approval did not commit: {approved.json()['action']['status']}")
                approve_ms.append(elapsed)
                fixture.post("/__admin/reset", json={}).raise_for_status()
            report["workloads"]["draft (MCP + documents + proposal)"] = summarize(draft_ms)
            report["workloads"]["approve_and_publish (preflight + POST)"] = summarize(approve_ms)

            def burst():
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    return list(pool.map(lambda _: timed(lambda: respond("Summarize NS-104 and cite the current refund policy."))[0],
                                         range(concurrency * 3)))
            wall, latencies = timed(burst)
            report["workloads"][f"concurrent_investigation (x{concurrency})"] = {
                **summarize(latencies), "wall_ms": round(wall, 1), "requests_per_second": round(len(latencies) / (wall / 1000), 2)}

        events = [event for line in traces.read_text(encoding="utf-8").splitlines() for event in json.loads(line)["events"]]
        calls = sum(event["stage"] == "mcp.tools/call" for event in events)
        report["mcp"] = {"tool_calls": calls, "sessions_started": sum(event["stage"] == "mcp.initialize" for event in events)}
        report["processes_at_idle"] = process_tree(api.pid)
        report["process_tree_working_set_mb"] = round(sum(p["working_set_mb"] for p in report["processes_at_idle"]), 1)
    finally:
        stop(api, api_log)
        stop(backend, backend_log)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO, help="checkout whose assistant package is launched")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.root.resolve(), args.iterations, args.concurrency)
    for name, stats in report["workloads"].items():
        print(f"{name:45s} " + "  ".join(f"{key}={value}" for key, value in stats.items()))
    print(f"mcp={report['mcp']}  startup_ms={report['api_startup_ms']}  tree_working_set_mb={report['process_tree_working_set_mb']}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
