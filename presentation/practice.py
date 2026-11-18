"""Rehearse the "failure + small change" part of the review on a throwaway copy.

    .\.venv\Scripts\python.exe presentation\practice.py list
    .\.venv\Scripts\python.exe presentation\practice.py start          # random drill
    .\.venv\Scripts\python.exe presentation\practice.py start F2       # a specific drill
    .\.venv\Scripts\python.exe presentation\practice.py check          # targeted tests in the copy
    .\.venv\Scripts\python.exe presentation\practice.py check --all    # whole suite in the copy
    .\.venv\Scripts\python.exe presentation\practice.py reveal         # where it was, and the fix

The copy lives in tmp/practice/project (ignored). The real project is never modified.
Open and edit files in the copy, then run "check".
"""
import json
import random
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRACTICE = ROOT / "tmp" / "practice"
PROJECT = PRACTICE / "project"
STATE = PRACTICE / "drill.json"
COPY = ["assistant", "tests", "server.py", "mcp_server.py", "fixtures.json", "eval_scenarios.json",
        "pyproject.toml", "test_mock_api.py", "README.md", "START_HERE.txt", "artifacts/fixture_manifest.json"]

# kind "fault": a bug was injected into assistant/ code; fixing it means finding and undoing it.
# kind "change": the requirement changed; a test already encodes it; implement it in assistant/.
DRILLS = {
    "F1": dict(kind="fault", title="Approval ignores the body hash",
               symptom="Security review: approving a draft with a WRONG body_hash still publishes the comment.",
               tests=["tests/test_actions.py::test_draft_is_immutable_persistent_and_never_writes",
                      "tests/test_security.py::test_message_never_approves_and_endpoint_checks_owner_and_exact_hash"],
               edits=[("assistant/actions.py", 'if not hmac.compare_digest(action["body_hash"], body_hash):',
                       'if not hmac.compare_digest(action["body_hash"], body_hash) and False:')]),
    "F2": dict(kind="fault", title="Retry uses a fresh idempotency key",
               symptom="Ops: after the backend returned 503, the same reply appears TWICE on the ticket.",
               tests=["tests/test_actions.py::test_503_after_commit_reconciles_original_key_and_repeat_is_noop",
                      "tests/test_actions.py::test_503_before_commit_retries_only_same_key_and_body"],
               edits=[("assistant/backend_client.py",
                       '        payload = {key: action[key] for key in ("body", "expected_version", "idempotency_key", "approval_reference")}\n',
                       '        payload = {key: action[key] for key in ("body", "expected_version", "idempotency_key", "approval_reference")}\n'
                       '        payload["idempotency_key"] = "cp-" + __import__("uuid").uuid4().hex\n')]),
    "F3": dict(kind="fault", title="Superseded documents are no longer filtered",
               symptom="NS-104 answers now say evidence is 'insufficient or conflicting' and cite nothing for refunds.",
               tests=["tests/test_retrieval.py"],
               edits=[("assistant/retrieval.py", 'if document.status != "active" or document.effective_at > today:',
                       'if document.effective_at > today:')]),
    "F4": dict(kind="fault", title="Preferences leak between users",
               symptom="Privacy bug: reviewer-02 sees the writing preferences that reviewer-01 saved (same tenant).",
               tests=["tests/test_memory.py"],
               edits=[("assistant/storage.py",
                       'FROM preferences WHERE tenant_id=? AND user_id=? ORDER BY key",\n                (context.tenant_id, context.user_id))',
                       'FROM preferences WHERE tenant_id=? ORDER BY key",\n                (context.tenant_id,))')]),
    "F5": dict(kind="fault", title="Off-by-one on the refund window",
               symptom="Support: exactly 14 days after payment the assistant says 'manual escalation', but the policy says within 14 days.",
               tests=["tests/test_retrieval.py::test_calendar_window_uses_injectable_clock"],
               edits=[("assistant/retrieval.py", "elif (result.elapsed_days > result.window_days or",
                       "elif (result.elapsed_days >= result.window_days or")]),
    "F6": dict(kind="fault", title="MCP result identity is not checked",
               symptom="Pen-test: when the MCP tool returns ANOTHER tenant's ticket, the assistant accepts it as data.",
               tests=["tests/test_mcp_integration.py::test_mcp_normalization_rejects_malformed_error_and_wrong_identity"],
               edits=[("assistant/mcp_client.py", "if ticket.tenant_id != tenant or ticket.id != ticket_id:",
                       "if ticket.id != ticket_id:")]),
    "F7": dict(kind="fault", title="No preflight version check before publishing",
               symptom="The ticket changed after the draft was made; approving it still records consent and POSTs (backend answers 409).",
               tests=["tests/test_actions.py::test_stale_proposal_stops_at_preflight_without_silent_rebase"],
               edits=[("assistant/actions.py", '        if ticket.version != action["expected_version"]:\n',
                       '        if ticket.version != action["expected_version"] and False:\n')]),
    "C1": dict(kind="change", title="Policy wording changed",
               symptom="New requirement: the backend policy now reads 'within 14 days of the first payment' (the word "
                       "'calendar' was removed). It must still be recognized as a 14-day window. tests/test_retrieval.py was updated.",
               tests=["tests/test_retrieval.py"],
               edits=[("tests/test_retrieval.py",
                       '"Refunds are available within 14 calendar days of the first payment, if no premium exports',
                       '"Refunds are available within 14 days of the first payment, if no premium exports')],
               solution="assistant/retrieval.py refund_terms(): make the word optional\n"
                        '    r" within (?P<days>\\d{1,3}) (?:calendar )?days of (?:the first payment|the original payment)"'),
    "C2": dict(kind="change", title="Forget must accept 'Wipe my preferences.'",
               symptom="New requirement: the chat message 'Wipe my preferences.' must forget preferences "
                       "(intent 'forget'). tests/test_security.py was updated.",
               tests=["tests/test_security.py::test_memory_api_is_owner_scoped_and_forget_clears_restarted_app"],
               edits=[("tests/test_security.py",
                       '        await client.post("/assistant/users/reviewer-01/memory/forget", json=CTX)\n',
                       '        r = await client.post("/assistant/respond", json={**CTX, "message": "Wipe my preferences."})\n'
                       '        assert r.json()["intent"] == "forget"\n')],
               solution="assistant/memory.py parse_forget(): add the verb to the explicit grammar\n"
                        '    r"(?:please |can you |could you )?(?:forget|delete|remove|clear|erase|wipe) "\n'
                        "(The orchestrator already routes every parse_forget match to Store.forget.)"),
    "C3": dict(kind="change", title="Allow three publish attempts",
               symptom="New requirement: operations wants up to 3 publish attempts (still bounded). "
                       "tests/test_contract_change.py was added.",
               tests=["tests/test_contract_change.py", "tests/test_actions.py::test_503_before_commit_retries_only_same_key_and_body"],
               create={"tests/test_contract_change.py":
                       "def test_three_publish_attempts_are_allowed_but_four_are_not():\n"
                       "    import pytest\n"
                       "    from assistant.config import Settings\n"
                       "    assert Settings(publish_attempts=3).publish_attempts == 3\n"
                       "    with pytest.raises(ValueError):\n"
                       "        Settings(publish_attempts=4)\n"},
               edits=[],
               solution="assistant/config.py __post_init__: publish_attempts not in (1, 2, 3)\n"
                        "(Actions._publish already loops over settings.publish_attempts; the key stays the same.)"),
}


def _force_remove(func, path, _):
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _load_state():
    if not STATE.exists():
        sys.exit("No drill in progress. Run: practice.py start")
    return json.loads(STATE.read_text(encoding="utf-8"))


def _line_of(path: Path, text: str) -> int:
    content = path.read_text(encoding="utf-8")
    return content[:content.index(text)].count("\n") + 1 if text in content else 0


def start(drill_id=None):
    drill_id = (drill_id or random.choice(list(DRILLS))).upper()
    if drill_id not in DRILLS:
        sys.exit(f"Unknown drill {drill_id}. Choose from: {', '.join(DRILLS)}")
    drill = DRILLS[drill_id]
    if PROJECT.exists():
        shutil.rmtree(PROJECT, onerror=_force_remove)
    for item in COPY:
        source, target = ROOT / item, PROJECT / item
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copyfile(source, target)
    for path, old, new in drill["edits"]:
        file = PROJECT / path
        text = file.read_text(encoding="utf-8")
        if text.count(old) != 1:
            sys.exit(f"Drill {drill_id} no longer matches {path}; the code changed. Pick another drill.")
        file.write_text(text.replace(old, new), encoding="utf-8")
    for path, content in drill.get("create", {}).items():
        (PROJECT / path).write_text(content, encoding="utf-8")
    STATE.write_text(json.dumps({"id": drill_id}), encoding="utf-8")
    print(f"Drill ready in: {PROJECT}")
    print(f"Type: {'injected bug - find and fix it' if drill['kind'] == 'fault' else 'changed requirement - implement it'}")
    print(f"\n  {drill['symptom']}\n")
    print("Work ONLY inside that folder. Suggested first step:")
    print(f'  cd "{PROJECT}"')
    print(f'  & "{sys.executable}" -m pytest -q -x tests --ignore=tests/test_scenarios.py')
    print("Then:  practice.py check   (or 'reveal' if you are stuck)")


def check(run_all=False):
    drill = DRILLS[_load_state()["id"]]
    tests = ["-q"] if run_all else ["-q", *drill["tests"]]
    print("Running:", "whole suite" if run_all else " ".join(drill["tests"]), flush=True)
    code = subprocess.call([sys.executable, "-m", "pytest", *tests], cwd=PROJECT)
    print("\nPASS - drill solved." if code == 0 else "\nStill failing. Read the first assertion, then the code it points to.")
    return code


def reveal():
    drill_id = _load_state()["id"]
    drill = DRILLS[drill_id]
    print(f"{drill_id}: {drill['title']}\n")
    if drill["kind"] == "fault":
        for path, old, new in drill["edits"]:
            print(f"{path}:{_line_of(ROOT / path, old)}")
            print("  injected:  " + new.strip().replace("\n", "\n             "))
            print("  original:  " + old.strip().replace("\n", "\n             "))
    else:
        print("Reference solution:\n" + drill["solution"])
    print("\nTests that prove it:", " ".join(drill["tests"]))


def main():
    args = sys.argv[1:] or ["list"]
    if args[0] == "list":
        for key, drill in DRILLS.items():
            print(f"{key}  [{drill['kind']:6}] {drill['title']}")
    elif args[0] == "start":
        start(args[1] if len(args) > 1 else None)
    elif args[0] == "check":
        sys.exit(check("--all" in args))
    elif args[0] == "reveal":
        reveal()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
