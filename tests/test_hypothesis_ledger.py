import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REPOSITORY_HASH = "sha256:" + "a" * 64
UPDATED_REPOSITORY_HASH = "sha256:" + "b" * 64
EVIDENCE_1 = "sha256:" + "1" * 64
EVIDENCE_2 = "sha256:" + "2" * 64
EVIDENCE_3 = "sha256:" + "3" * 64
MISSING_EVIDENCE = "sha256:" + "f" * 64


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def task_data():
    return {
        "schema_version": 1,
        "id": "T-HYPOTHESIS-1",
        "mode": "builder",
        "goal": "Find the graph rewrite regression",
        "profiles": ["base"],
        "scope": {"write_paths": ["src/optimizer.cc"]},
        "acceptance": [{"id": "AC-1", "description": "Root cause is evidence-backed", "validation_id": "V-1"}],
        "risk": {},
        "handoff": {"must_do": ["Preserve control edges"]},
        "validation": [{"id": "V-1", "command": ["pytest", "-q"]}],
        "stop_conditions": ["No repository evidence"],
    }


def prepare_state(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps(task_data()), encoding="utf-8")
    run_dir = tmp_path / "run"
    initialized = run_script(
        "init-workflow-state.py", "--task", task, "--run-dir", run_dir,
        "--repository-state-hash", REPOSITORY_HASH,
    )
    assert initialized.returncode == 0, initialized.stderr
    state = json.loads((run_dir / "WORKFLOW_STATE.json").read_text(encoding="utf-8"))
    delta = tmp_path / "evidence-delta.json"
    delta.write_text(json.dumps({
        "schema_version": 1,
        "base_state_id": state["state_id"],
        "events": [
            {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_1}},
            {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_2}},
            {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_3}},
        ],
    }), encoding="utf-8")
    applied = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", delta,
        "--actor", "planner",
    )
    assert applied.returncode == 0, applied.stderr
    return run_dir


def reject_value(hypothesis_id="H-1", statement="Failure is caused by node ordering", scope_refs=None):
    return {
        "schema_version": 1,
        "id": hypothesis_id,
        "statement": statement,
        "reason": "The failing test reproduces with stable node order",
        "evidence_refs": [EVIDENCE_1],
        "reopen_when": "Remote graph order differs from local order",
        "producer": "claude-builder",
        "repository_state_hash": REPOSITORY_HASH,
        "scope_refs": scope_refs if scope_refs is not None else ["AC-1", "GraphOptimizer"],
    }


def write_json(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def update(run_dir, ledger, input_path, operation="reject"):
    return run_script(
        "update-hypothesis-ledger.py", "--operation", operation,
        "--input", input_path, "--ledger", ledger,
        "--state", run_dir / "WORKFLOW_STATE.json",
        "--state-events", run_dir / "WORKFLOW_EVENTS.jsonl",
        "--actor", "codex-reviewer",
    )


def create_ledger(tmp_path, value=None):
    run_dir = prepare_state(tmp_path)
    ledger = run_dir / "REJECTED_HYPOTHESES.json"
    reject_input = write_json(tmp_path, "reject.json", value or reject_value())
    result = update(run_dir, ledger, reject_input)
    assert result.returncode == 0, result.stderr
    return run_dir, ledger


def proposal(statement="Failure is caused by node ordering", evidence_refs=None, scope_refs=None, related=None):
    return {
        "schema_version": 1,
        "statement": statement,
        "producer": "claude-revision",
        "evidence_refs": evidence_refs or [],
        "repository_state_hash": REPOSITORY_HASH,
        "scope_refs": scope_refs if scope_refs is not None else ["AC-1"],
        "related_hypothesis_ids": related or [],
    }


def check(tmp_path, ledger, value, *extra):
    proposal_path = write_json(tmp_path, "proposal.json", value)
    return run_script(
        "check-revisited-hypothesis.py", "--ledger", ledger,
        "--proposal", proposal_path, *extra,
    )


def test_reject_requires_evidence_present_in_state(tmp_path):
    run_dir = prepare_state(tmp_path)
    ledger = run_dir / "REJECTED_HYPOTHESES.json"
    value = reject_value()
    value["evidence_refs"] = [MISSING_EVIDENCE]
    result = update(run_dir, ledger, write_json(tmp_path, "reject.json", value))
    assert result.returncode == 1
    assert "absent from State IR" in result.stderr
    assert not ledger.exists()
    assert json.loads((run_dir / "WORKFLOW_STATE.json").read_text())["rejected_hypotheses"] == []


def test_reject_creates_hash_bound_ledger_and_synchronizes_state(tmp_path):
    run_dir, ledger_path = create_ledger(tmp_path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    state = json.loads((run_dir / "WORKFLOW_STATE.json").read_text(encoding="utf-8"))
    assert ledger["revision"] == 1
    assert ledger["items"][0]["status"] == "rejected"
    assert ledger["items"][0]["evidence_refs"] == [EVIDENCE_1]
    assert ledger["items"][0]["reopen_when"]
    assert state["rejected_hypotheses"][0]["id"] == "H-1"
    sys.path.insert(0, str(SCRIPTS))
    from hypothesis_ledger import ledger_id_for, validate_ledger
    assert validate_ledger(ledger) == []
    assert ledger["ledger_id"] == ledger_id_for(ledger)


def test_reject_retry_is_idempotent(tmp_path):
    run_dir, ledger = create_ledger(tmp_path)
    before_ledger = ledger.read_bytes()
    before_state = (run_dir / "WORKFLOW_STATE.json").read_bytes()
    result = update(run_dir, ledger, write_json(tmp_path, "retry.json", reject_value()))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "unchanged"
    assert ledger.read_bytes() == before_ledger
    assert (run_dir / "WORKFLOW_STATE.json").read_bytes() == before_state


def test_same_id_with_different_evidence_is_not_silently_idempotent(tmp_path):
    run_dir, ledger = create_ledger(tmp_path)
    value = reject_value()
    value["evidence_refs"] = [EVIDENCE_2]
    result = update(run_dir, ledger, write_json(tmp_path, "conflict.json", value))
    assert result.returncode == 1
    assert "conflicting hypothesis" in result.stderr or "different metadata" in result.stderr


def test_exact_repeat_without_new_evidence_is_recorded_each_time(tmp_path):
    _, ledger_path = create_ledger(tmp_path)
    first = check(tmp_path, ledger_path, proposal())
    second = check(tmp_path, ledger_path, proposal())
    assert first.returncode == second.returncode == 2
    assert json.loads(first.stdout)["status"] == "rejected-repeat"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(ledger["revisit_events"]) == 2
    assert ledger["revisit_events"][0]["event_id"] != ledger["revisit_events"][1]["event_id"]


def test_new_evidence_requires_explicit_reopen(tmp_path):
    _, ledger = create_ledger(tmp_path)
    result = check(tmp_path, ledger, proposal(evidence_refs=[EVIDENCE_2]))
    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "reopen-required"
    assert payload["new_evidence_present"] is True
    assert payload["execute_allowed"] is False


def test_repository_change_requires_reopen_even_without_new_evidence(tmp_path):
    _, ledger = create_ledger(tmp_path)
    value = proposal()
    value["repository_state_hash"] = UPDATED_REPOSITORY_HASH
    result = check(tmp_path, ledger, value)
    assert result.returncode == 3
    assert json.loads(result.stdout)["repository_changed"] is True


def test_explicit_related_id_catches_paraphrase_without_fuzzy_guess(tmp_path):
    _, ledger = create_ledger(tmp_path)
    result = check(
        tmp_path, ledger,
        proposal(statement="Maybe the deterministic scheduler is wrong", related=["H-1"]),
    )
    assert result.returncode == 2
    match = json.loads(result.stdout)["matched_hypotheses"][0]
    assert match["id"] == "H-1"
    assert match["match_type"] == "explicit"


def test_similar_statement_requires_review_but_is_not_declared_exact(tmp_path):
    _, ledger = create_ledger(tmp_path)
    result = check(tmp_path, ledger, proposal(statement="Failure is caused by node order"))
    assert result.returncode == 4
    payload = json.loads(result.stdout)
    assert payload["status"] == "possible-repeat"
    assert payload["matched_hypotheses"][0]["match_type"] == "similar"


def test_novel_hypothesis_does_not_mutate_ledger(tmp_path):
    _, ledger = create_ledger(tmp_path)
    before = ledger.read_bytes()
    result = check(tmp_path, ledger, proposal(statement="The parser drops control edges", scope_refs=["Parser"]))
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "novel"
    assert ledger.read_bytes() == before


def test_review_packet_contains_only_scope_relevant_history(tmp_path):
    run_dir, ledger = create_ledger(tmp_path)
    second_value = reject_value("H-2", "Failure is caused by cache eviction", ["CacheLayer"])
    second_input = write_json(tmp_path, "second-reject.json", second_value)
    assert update(run_dir, ledger, second_input).returncode == 0
    review = tmp_path / "review.json"
    result = check(
        tmp_path, ledger,
        proposal(statement="Investigate a new graph invariant", scope_refs=["GraphOptimizer"]),
        "--review-output", review, "--max-relevant", "1",
    )
    assert result.returncode == 0
    packet = json.loads(review.read_text(encoding="utf-8"))
    assert [item["id"] for item in packet["items"]] == ["H-1"]
    assert "H-2" not in review.read_text(encoding="utf-8")


def test_reopen_requires_new_evidence_and_condition_confirmation(tmp_path):
    run_dir, ledger = create_ledger(tmp_path)
    base = {
        "schema_version": 1,
        "hypothesis_id": "H-1",
        "producer": "codex-reviewer",
        "reason": "Remote ordering differs",
        "new_evidence_refs": [EVIDENCE_1],
        "condition_met": True,
        "repository_state_hash": REPOSITORY_HASH,
    }
    no_new = update(run_dir, ledger, write_json(tmp_path, "no-new.json", base), "reopen")
    assert no_new.returncode == 1
    assert "evidence not present" in no_new.stderr
    base["new_evidence_refs"] = [EVIDENCE_2]
    base["condition_met"] = False
    unmet = update(run_dir, ledger, write_json(tmp_path, "unmet.json", base), "reopen")
    assert unmet.returncode == 1
    assert "explicitly confirmed" in unmet.stderr


def test_reopen_updates_ledger_and_removes_state_rejection(tmp_path):
    run_dir, ledger_path = create_ledger(tmp_path)
    rejected_state = tmp_path / "REJECTED_STATE.json"
    shutil.copyfile(run_dir / "WORKFLOW_STATE.json", rejected_state)
    value = {
        "schema_version": 1,
        "hypothesis_id": "H-1",
        "producer": "codex-reviewer",
        "reason": "Remote graph order differs from local evidence",
        "new_evidence_refs": [EVIDENCE_2],
        "condition_met": True,
        "repository_state_hash": REPOSITORY_HASH,
    }
    result = update(run_dir, ledger_path, write_json(tmp_path, "reopen.json", value), "reopen")
    assert result.returncode == 0, result.stderr
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    state = json.loads((run_dir / "WORKFLOW_STATE.json").read_text(encoding="utf-8"))
    assert ledger["items"][0]["status"] == "reopened"
    assert ledger["items"][0]["reopened_evidence_refs"] == [EVIDENCE_2]
    assert state["rejected_hypotheses"] == []
    validation = run_script(
        "validate-workflow-state.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl",
    )
    assert validation.returncode == 0, validation.stderr
    handoff_delta = tmp_path / "reopen-handoff-delta.json"
    delta_result = run_script(
        "build-handoff-delta.py", "--base", rejected_state,
        "--target", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl",
        "--output", handoff_delta,
    )
    assert delta_result.returncode == 0, delta_result.stderr
    assert json.loads(handoff_delta.read_text(encoding="utf-8"))["reopened_hypotheses"] == ["H-1"]
    novel = check(tmp_path, ledger_path, proposal())
    assert novel.returncode == 0


def test_unknown_explicit_hypothesis_id_fails_closed(tmp_path):
    _, ledger = create_ledger(tmp_path)
    result = check(tmp_path, ledger, proposal(related=["H-UNKNOWN"]))
    assert result.returncode == 1
    assert "unknown hypotheses" in result.stderr


def test_tampered_ledger_is_rejected(tmp_path):
    _, ledger_path = create_ledger(tmp_path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["items"][0]["reason"] = "tampered"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    result = check(tmp_path, ledger_path, proposal())
    assert result.returncode == 1
    assert "ledger_id does not match" in result.stderr


def test_schema_is_strict_and_requires_evidence():
    schema = json.loads((ROOT / "schemas" / "rejected-hypothesis.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    item = schema["$defs"]["item"]
    assert item["additionalProperties"] is False
    assert item["properties"]["evidence_refs"]["minItems"] == 1
