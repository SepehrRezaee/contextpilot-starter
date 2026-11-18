import hashlib
import json
from pathlib import Path


def test_all_supplied_scenarios_run_against_real_processes_and_emit_evidence(tmp_path):
    from assistant.eval_runner import run_evaluation
    report = run_evaluation(output_dir=tmp_path / "artifacts")
    assert report["summary"] == {"passed": 8, "failed": 0, "skipped": 0, "total": 8}
    assert [case["id"] for case in report["scenarios"]] == [f"E{i:02d}" for i in range(1, 9)]
    assert all(case["status"] == "PASS" and case["duration_ms"] > 0 and case["evidence"] for case in report["scenarios"])
    assert all(case["failure_reason"] is None for case in report["scenarios"])
    persistence = report["scenarios"][1]["evidence"]
    assert persistence["process_before"] != persistence["process_after"]
    recovery = json.loads((tmp_path / "artifacts" / "trace_503_recovery.json").read_text())
    assert any(e["stage"] == "http.publish" and e["status"] == 503 for e in recovery["events"])
    assert any(e["stage"] == "action.reconcile" and e["outcome"] == "committed" for e in recovery["events"])
    investigation = json.loads((tmp_path / "artifacts" / "trace_investigation.json").read_text())
    assert "N-POL-REFUND-2026" in investigation["selected_document_ids"]
    assert any(e["stage"] == "mcp.tools/call" for e in investigation["events"])


def test_supplied_infrastructure_is_byte_for_byte_unchanged():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "artifacts" / "fixture_manifest.json").read_text(encoding="utf-8-sig"))
    assert len(manifest) == 7
    for item in manifest:
        assert hashlib.sha256((root / item["file"]).read_bytes()).hexdigest() == item["sha256"], item["file"]

