import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def task_data():
    return {
        "schema_version": 1,
        "id": "T-HANDOFF-1",
        "mode": "builder",
        "goal": "Preserve control users during graph rewrite",
        "profiles": ["base"],
        "scope": {"write_paths": ["src/optimizer.cc"], "forbidden_paths": ["prod/"]},
        "acceptance": [
            {"id": "AC-1", "description": "Control users are preserved", "validation_id": "V-1"},
            {"id": "AC-2", "description": "Focused tests pass", "validation_id": "V-1"},
        ],
        "risk": {},
        "handoff": {"must_do": ["Preserve control edges"], "must_not_do": ["Do not redesign the graph API"]},
        "validation": [{"id": "V-1", "command": ["pytest", "-q"]}],
        "stop_conditions": ["Unexpected public API change"],
    }


def prepare_states(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps(task_data()), encoding="utf-8")
    run_dir = tmp_path / "run"
    initialized = run_script(
        "init-workflow-state.py", "--task", task, "--run-dir", run_dir,
        "--repository-state-hash", "sha256:base-repository",
    )
    assert initialized.returncode == 0, initialized.stderr
    base = tmp_path / "BASE_STATE.json"
    shutil.copyfile(run_dir / "WORKFLOW_STATE.json", base)
    base_data = json.loads(base.read_text(encoding="utf-8"))
    delta_input = tmp_path / "state-delta.json"
    delta_input.write_text(json.dumps({
        "schema_version": 1,
        "base_state_id": base_data["state_id"],
        "events": [
            {"event_type": "constraint-added", "payload": {"id": "C-EXTRA", "statement": "Keep the patch narrow", "source": "reviewer"}},
            {"event_type": "decision-accepted", "payload": {"id": "D-1", "statement": "Repair control-edge rewrite", "evidence_refs": ["E-1"]}},
            {"event_type": "decision-frozen", "payload": {"id": "D-1"}},
            {"event_type": "hypothesis-rejected", "payload": {"id": "H-1", "statement": "Redesign all graph edges", "reason": "Out of scope", "evidence_refs": ["E-1"]}},
            {"event_type": "question-opened", "payload": {"id": "Q-1", "question": "Which regression test covers the edge?"}},
            {"event_type": "evidence-added", "payload": {"ref": "E-1"}},
            {"event_type": "acceptance-updated", "payload": {"id": "AC-1", "status": "satisfied", "evidence_refs": ["E-1"]}},
            {"event_type": "next-action-updated", "payload": {"owner": "execution-builder", "operation": "Repair control-edge rewrite", "allowed_paths": ["src/optimizer.cc"]}},
            {"event_type": "phase-changed", "payload": {"phase": "implementation"}},
        ],
    }), encoding="utf-8")
    applied = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", delta_input,
        "--actor", "planner",
    )
    assert applied.returncode == 0, applied.stderr
    target = run_dir / "WORKFLOW_STATE.json"
    handoff_delta = tmp_path / "HANDOFF_DELTA.json"
    built = run_script(
        "build-handoff-delta.py", "--base", base, "--target", target,
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--output", handoff_delta,
    )
    assert built.returncode == 0, built.stderr
    return base, target, handoff_delta


def valid_ack(target):
    state = json.loads(Path(target).read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "state_id": state["state_id"],
        "receiver": "claude-session-28",
        "repair_attempt": 0,
        "understood_goal_id": state["goal"]["id"],
        "accepted_constraints": sorted(item["id"] for item in state["constraints"] if item["frozen"]),
        "accepted_decisions": sorted(item["id"] for item in state["accepted_decisions"]),
        "open_questions": sorted(item["id"] for item in state["open_questions"]),
        "planned_first_action": {
            "operation": "Inspect focused optimizer tests",
            "target": "GraphOptimizerTest",
            "write_paths": ["src/optimizer.cc"],
        },
        "additional_context_requested": [],
        "contradictions": [],
    }


def write_ack(tmp_path, value, name="HANDOFF_ACK.json"):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def validate_command(base, target, delta, ack, *extra):
    base_id = json.loads(Path(base).read_text(encoding="utf-8"))["state_id"]
    return run_script(
        "validate-handoff-ack.py", "--base-state", base, "--state", target,
        "--delta", delta, "--events", Path(target).parent / "WORKFLOW_EVENTS.jsonl",
        "--ack", ack, "--receiver-state-id", base_id, *extra,
    )


def test_delta_is_deterministic_self_contained_and_state_bound(tmp_path):
    base, target, delta_path = prepare_states(tmp_path)
    second = tmp_path / "second-delta.json"
    result = run_script(
        "build-handoff-delta.py", "--base", base, "--target", target,
        "--events", Path(target).parent / "WORKFLOW_EVENTS.jsonl", "--output", second,
    )
    assert result.returncode == 0, result.stderr
    first_value = json.loads(delta_path.read_text(encoding="utf-8"))
    assert first_value == json.loads(second.read_text(encoding="utf-8"))
    assert first_value["base_state_id"] == json.loads(base.read_text(encoding="utf-8"))["state_id"]
    assert first_value["new_state_id"] == json.loads(target.read_text(encoding="utf-8"))["state_id"]
    assert first_value["added_constraints"][0]["id"] == "C-EXTRA"
    assert first_value["added_decisions"][0]["status"] == "frozen"
    assert first_value["rejected_hypotheses"][0]["id"] == "H-1"
    assert first_value["changed_acceptance"]["AC-1"]["status"] == "satisfied"


def test_valid_short_ack_is_accepted(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    ack = write_ack(tmp_path, valid_ack(target))
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "accepted"
    assert payload["execute_allowed"] is True


def test_receiver_base_mismatch_fails_without_repair(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    ack = write_ack(tmp_path, valid_ack(target))
    result = run_script(
        "validate-handoff-ack.py", "--base-state", base, "--state", target,
        "--delta", delta, "--events", Path(target).parent / "WORKFLOW_EVENTS.jsonl", "--ack", ack,
        "--receiver-state-id", "sha256:" + "0" * 64,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "state-mismatch"
    assert not (tmp_path / "HANDOFF_ACK_REPAIR.json").exists()


def test_tampered_delta_is_rejected_even_with_recomputed_hash(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = json.loads(delta.read_text(encoding="utf-8"))
    value["added_constraints"] = []
    sys.path.insert(0, str(SCRIPTS))
    from handoff_protocol import delta_id_for
    value["delta_id"] = delta_id_for(value)
    delta.write_text(json.dumps(value), encoding="utf-8")
    ack = write_ack(tmp_path, valid_ack(target))
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 1
    assert "delta content does not match" in result.stdout


def test_delta_requires_contiguous_event_ancestry(tmp_path):
    base, target, _ = prepare_states(tmp_path)
    events = Path(target).parent / "WORKFLOW_EVENTS.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    broken = tmp_path / "broken-events.jsonl"
    broken.write_text("\n".join([lines[0]] + lines[2:]) + "\n", encoding="utf-8")
    result = run_script(
        "build-handoff-delta.py", "--base", base, "--target", target,
        "--events", broken, "--output", tmp_path / "broken-delta.json",
    )
    assert result.returncode == 1
    assert "ancestry is discontinuous" in result.stderr


def test_missing_frozen_constraint_generates_bounded_resend(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["accepted_constraints"].remove("C-EXTRA")
    ack = write_ack(tmp_path, value)
    repair_path = tmp_path / "repair.json"
    result = validate_command(base, target, delta, ack, "--repair-output", repair_path)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "repair-required"
    repair = json.loads(repair_path.read_text(encoding="utf-8"))
    assert repair["repair_attempt"] == 1
    assert repair["max_repair_attempts"] == 1
    assert repair["missing_constraints"][0]["id"] == "C-EXTRA"


def test_goal_mismatch_resends_only_expected_goal(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["understood_goal_id"] = "G-WRONG"
    ack = write_ack(tmp_path, value)
    repair_path = tmp_path / "goal-repair.json"
    result = validate_command(base, target, delta, ack, "--repair-output", repair_path)
    assert result.returncode == 2
    repair = json.loads(repair_path.read_text(encoding="utf-8"))
    assert repair["expected_goal"]["id"] == "G-1"
    assert repair["missing_constraints"] == []


def test_contradiction_stops_then_blocks_after_one_repair(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["contradictions"] = ["Decision D-1 conflicts with repository evidence"]
    ack = write_ack(tmp_path, value)
    first = validate_command(base, target, delta, ack)
    assert first.returncode == 2
    value["repair_attempt"] = 1
    repaired = write_ack(tmp_path, value, "repair-ack.json")
    second = validate_command(base, target, delta, repaired)
    assert second.returncode == 3
    assert json.loads(second.stdout)["status"] == "blocked"


def test_write_outside_allowed_paths_requires_repair(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["planned_first_action"]["write_paths"] = ["prod/deploy.py"]
    ack = write_ack(tmp_path, value)
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["forbidden_paths"] == ["prod/deploy.py"]
    assert payload["execute_allowed"] is False


def test_path_like_action_target_is_checked_without_write_paths(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["planned_first_action"] = {
        "operation": "Modify deployment helper",
        "target": "prod/deploy.py",
    }
    ack = write_ack(tmp_path, value)
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 2
    assert json.loads(result.stdout)["forbidden_paths"] == ["prod/deploy.py"]


def test_windows_absolute_action_target_is_never_treated_as_repo_relative(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["planned_first_action"] = {
        "operation": "Modify external file",
        "target": "C:\\secrets\\token.py",
    }
    ack = write_ack(tmp_path, value)
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 2
    assert json.loads(result.stdout)["forbidden_paths"] == ["C:\\secrets\\token.py"]


def test_context_request_requires_bounded_response(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["additional_context_requested"] = ["CTX-Q-12"]
    ack = write_ack(tmp_path, value)
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 2
    assert "additional-context-requested" in json.loads(result.stdout)["issues"]


def test_ack_byte_limit_and_long_restatement_fail_closed(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    value = valid_ack(target)
    value["contradictions"] = ["x" * 513]
    ack = write_ack(tmp_path, value)
    long_result = validate_command(base, target, delta, ack)
    assert long_result.returncode == 1
    assert "at most 512" in long_result.stdout
    value = valid_ack(target)
    value["planned_first_action"]["operation"] = "x" * 257
    ack = write_ack(tmp_path, value, "oversized-action.json")
    size_result = validate_command(base, target, delta, ack, "--max-bytes", "256")
    assert size_result.returncode == 1
    assert "exceeds 256 byte limit" in size_result.stdout


def test_raw_ack_whitespace_cannot_bypass_byte_limit(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    ack = tmp_path / "whitespace-ack.json"
    compact = json.dumps(valid_ack(target))
    ack.write_text(compact[:-1] + (" " * 9000) + "}", encoding="utf-8")
    result = validate_command(base, target, delta, ack)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "ACK file exceeds 8192 byte limit" in payload["errors"]


def test_merge_requires_receiver_authored_repair_and_can_close_ack(tmp_path):
    base, target, delta = prepare_states(tmp_path)
    initial = valid_ack(target)
    initial["accepted_constraints"].remove("C-EXTRA")
    initial["additional_context_requested"] = ["CTX-Q-12"]
    initial["contradictions"] = ["Need the focused test location"]
    initial_path = write_ack(tmp_path, initial, "initial.json")
    repair = valid_ack(target)
    repair["repair_attempt"] = 1
    repair["accepted_constraints"] = ["C-EXTRA"]
    repair["accepted_decisions"] = []
    repair["open_questions"] = []
    repair_path = write_ack(tmp_path, repair, "receiver-repair.json")
    merged_path = tmp_path / "merged.json"
    merged = run_script(
        "merge-handoff-ack.py", "--base-ack", initial_path,
        "--repair-ack", repair_path, "--output", merged_path,
    )
    assert merged.returncode == 0, merged.stderr
    merged_value = json.loads(merged_path.read_text(encoding="utf-8"))
    assert merged_value["additional_context_requested"] == []
    assert merged_value["contradictions"] == []
    result = validate_command(base, target, delta, merged_path)
    assert result.returncode == 0, result.stdout


def test_merge_rejects_different_receiver_or_extra_round(tmp_path):
    _, target, _ = prepare_states(tmp_path)
    initial = valid_ack(target)
    repair = valid_ack(target)
    repair["repair_attempt"] = 1
    repair["receiver"] = "other-session"
    result = run_script(
        "merge-handoff-ack.py", "--base-ack", write_ack(tmp_path, initial, "initial.json"),
        "--repair-ack", write_ack(tmp_path, repair, "repair.json"),
        "--output", tmp_path / "merged.json",
    )
    assert result.returncode == 1
    assert "same state and receiver" in result.stderr


def test_schemas_are_strict_and_document_ack_byte_limit():
    delta_schema = json.loads((ROOT / "schemas" / "handoff-delta.schema.json").read_text(encoding="utf-8"))
    ack_schema = json.loads((ROOT / "schemas" / "handoff-ack.schema.json").read_text(encoding="utf-8"))
    assert delta_schema["additionalProperties"] is False
    assert ack_schema["additionalProperties"] is False
    assert "8192" in ack_schema["$comment"]
    assert ack_schema["properties"]["repair_attempt"]["maximum"] == 1
