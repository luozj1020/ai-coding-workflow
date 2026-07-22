import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REPOSITORY_HASH = "sha256:" + "a" * 64
EVIDENCE_1 = "sha256:" + "1" * 64
EVIDENCE_2 = "sha256:" + "2" * 64


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def task_data():
    return {
        "schema_version": 1,
        "id": "T-STATE-1",
        "mode": "builder",
        "goal": "Preserve control users during graph rewrite",
        "profiles": ["base"],
        "scope": {"write_paths": ["src/optimizer.cc"], "forbidden_paths": ["prod/"]},
        "acceptance": [
            {"id": "AC-1", "description": "Control users are preserved", "validation_id": "V-1"},
            {"id": "AC-2", "description": "Focused tests pass", "validation_id": "V-1"},
        ],
        "risk": {},
        "handoff": {"must_do": ["Preserve control edges"], "must_not_do": ["Touch prod/"]},
        "validation": [{"id": "V-1", "command": ["pytest", "-q"]}],
        "stop_conditions": ["Unexpected public API change"],
    }


def initialize(tmp_path, repository_hash=REPOSITORY_HASH):
    tmp_path.mkdir(parents=True, exist_ok=True)
    task = tmp_path / "task.json"
    task.write_text(json.dumps(task_data()), encoding="utf-8")
    run_dir = tmp_path / "run"
    result = run_script(
        "init-workflow-state.py", "--task", task, "--run-dir", run_dir,
        "--repository-state-hash", repository_hash,
    )
    assert result.returncode == 0, result.stderr
    return run_dir


def load_state(run_dir):
    return json.loads((run_dir / "WORKFLOW_STATE.json").read_text(encoding="utf-8"))


def write_delta(tmp_path, state_id, events):
    path = tmp_path / "delta.json"
    path.write_text(json.dumps({"schema_version": 1, "base_state_id": state_id, "events": events}), encoding="utf-8")
    return path


def test_same_input_produces_deterministic_state_hash(tmp_path):
    first = initialize(tmp_path / "first")
    second = initialize(tmp_path / "second")
    assert load_state(first)["state_id"] == load_state(second)["state_id"]


def test_initializer_creates_replayable_state_and_event(tmp_path):
    run_dir = initialize(tmp_path)
    state = load_state(run_dir)
    result = run_script(
        "validate-workflow-state.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl",
    )
    assert result.returncode == 0, result.stderr
    assert state["revision"] == 0
    assert state["constraints"][0]["frozen"] is True
    assert set(state["acceptance_status"]) == {"AC-1", "AC-2"}


def test_apply_delta_is_event_traced_and_replayable(tmp_path):
    run_dir = initialize(tmp_path)
    before = load_state(run_dir)
    delta = write_delta(tmp_path, before["state_id"], [
        {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_1}},
        {"event_type": "decision-accepted", "payload": {"id": "D-1", "statement": "Preserve control inputs first", "evidence_refs": [EVIDENCE_1]}},
        {"event_type": "decision-frozen", "payload": {"id": "D-1"}},
        {"event_type": "acceptance-updated", "payload": {"id": "AC-1", "status": "satisfied", "evidence_refs": [EVIDENCE_1]}},
    ])
    result = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", delta,
        "--actor", "claude-builder",
    )
    assert result.returncode == 0, result.stderr
    after = load_state(run_dir)
    assert after["revision"] == 4
    assert after["parent_state_id"] != before["parent_state_id"]
    assert after["accepted_decisions"][0]["status"] == "frozen"
    assert after["acceptance_status"]["AC-1"]["status"] == "satisfied"
    validation = run_script(
        "validate-workflow-state.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl",
    )
    assert validation.returncode == 0, validation.stderr
    assert len((run_dir / "WORKFLOW_EVENTS.jsonl").read_text(encoding="utf-8").splitlines()) == 5


def test_concurrent_transitions_serialize_on_state_lock(tmp_path):
    run_dir = initialize(tmp_path)
    state = load_state(run_dir)
    delta = write_delta(tmp_path, state["state_id"], [
        {"event_type": "question-opened", "payload": {"id": "Q-LOCK", "question": "Serialized?"}},
    ])
    command = [
        sys.executable, str(SCRIPTS / "apply-workflow-delta.py"),
        "--state", str(run_dir / "WORKFLOW_STATE.json"),
        "--events", str(run_dir / "WORKFLOW_EVENTS.jsonl"),
        "--delta", str(delta), "--actor",
    ]
    first = subprocess.Popen(command + ["builder-a"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(command + ["builder-b"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first.communicate(timeout=15)
    second.communicate(timeout=15)
    assert sorted((first.returncode, second.returncode)) == [0, 1]
    validation = run_script(
        "validate-workflow-state.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl",
    )
    assert validation.returncode == 0, validation.stderr
    assert load_state(run_dir)["open_questions"][0]["id"] == "Q-LOCK"
    assert len((run_dir / "WORKFLOW_EVENTS.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_stale_delta_is_rejected_without_writes(tmp_path):
    run_dir = initialize(tmp_path)
    before_state = (run_dir / "WORKFLOW_STATE.json").read_bytes()
    before_events = (run_dir / "WORKFLOW_EVENTS.jsonl").read_bytes()
    delta = write_delta(tmp_path, "sha256:" + "0" * 64, [
        {"event_type": "question-opened", "payload": {"id": "Q-1", "question": "Which edge?"}},
    ])
    result = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", delta,
        "--actor", "builder",
    )
    assert result.returncode == 1
    assert "base_state_id" in result.stderr
    assert (run_dir / "WORKFLOW_STATE.json").read_bytes() == before_state
    assert (run_dir / "WORKFLOW_EVENTS.jsonl").read_bytes() == before_events


def test_frozen_decision_cannot_be_silently_overwritten(tmp_path):
    run_dir = initialize(tmp_path)
    state = load_state(run_dir)
    first_delta = write_delta(tmp_path, state["state_id"], [
        {"event_type": "decision-accepted", "payload": {"id": "D-1", "statement": "Original", "evidence_refs": []}},
        {"event_type": "decision-frozen", "payload": {"id": "D-1"}},
    ])
    first = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", first_delta, "--actor", "planner",
    )
    assert first.returncode == 0, first.stderr
    frozen = load_state(run_dir)
    second_delta = write_delta(tmp_path, frozen["state_id"], [
        {"event_type": "decision-accepted", "payload": {"id": "D-1", "statement": "Replacement", "evidence_refs": []}},
    ])
    before_events = (run_dir / "WORKFLOW_EVENTS.jsonl").read_bytes()
    second = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", second_delta, "--actor", "builder",
    )
    assert second.returncode == 1
    assert "frozen decision" in second.stderr
    assert load_state(run_dir) == frozen
    assert (run_dir / "WORKFLOW_EVENTS.jsonl").read_bytes() == before_events


def test_frozen_decision_requires_explicit_invalidation(tmp_path):
    run_dir = initialize(tmp_path)
    initial = load_state(run_dir)
    accept = write_delta(tmp_path, initial["state_id"], [
        {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_1}},
        {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_2}},
        {"event_type": "decision-accepted", "payload": {"id": "D-1", "statement": "Original", "evidence_refs": [EVIDENCE_1]}},
        {"event_type": "decision-frozen", "payload": {"id": "D-1"}},
    ])
    assert run_script("apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json", "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", accept, "--actor", "planner").returncode == 0
    frozen = load_state(run_dir)
    invalidate = write_delta(tmp_path, frozen["state_id"], [
        {"event_type": "decision-invalidated", "payload": {"id": "D-1", "reason": "Counterexample", "evidence_refs": [EVIDENCE_2]}},
        {"event_type": "decision-accepted", "payload": {"id": "D-1", "statement": "Replacement", "evidence_refs": [EVIDENCE_2]}},
    ])
    result = run_script("apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json", "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", invalidate, "--actor", "reviewer")
    assert result.returncode == 0, result.stderr
    assert load_state(run_dir)["accepted_decisions"][0]["statement"] == "Replacement"


def test_renderer_is_deterministic_and_markdown_compatible(tmp_path):
    run_dir = initialize(tmp_path)
    first = run_script("render-task-card-from-state.py", "--state", run_dir / "WORKFLOW_STATE.json")
    second = run_script("render-task-card-from-state.py", "--state", run_dir / "WORKFLOW_STATE.json")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stdout.startswith("<!-- workflow-state:")
    assert "# Task Card:" in first.stdout
    assert "## Goal" in first.stdout
    assert "## Scope" in first.stdout
    assert "## Acceptance Criteria" in first.stdout


def test_validator_rejects_tampered_state(tmp_path):
    run_dir = initialize(tmp_path)
    state_path = run_dir / "WORKFLOW_STATE.json"
    state = load_state(run_dir)
    state["goal"]["statement"] = "Tampered"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_script("validate-workflow-state.py", "--state", state_path, "--events", run_dir / "WORKFLOW_EVENTS.jsonl")
    assert result.returncode == 1
    assert "state_id does not match" in result.stderr


def test_illegal_phase_transition_is_rejected(tmp_path):
    run_dir = initialize(tmp_path)
    state = load_state(run_dir)
    delta = write_delta(tmp_path, state["state_id"], [
        {"event_type": "phase-changed", "payload": {"phase": "review"}},
    ])
    result = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", delta,
        "--actor", "planner",
    )
    assert result.returncode == 1
    assert "illegal phase transition" in result.stderr
    assert load_state(run_dir) == state


def test_validator_rejects_tampered_event_payload(tmp_path):
    run_dir = initialize(tmp_path)
    events_path = run_dir / "WORKFLOW_EVENTS.jsonl"
    event = json.loads(events_path.read_text(encoding="utf-8"))
    event["payload"]["task_source"] = "tampered.json"
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    result = run_script(
        "validate-workflow-state.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", events_path,
    )
    assert result.returncode == 1
    assert "event_id does not match" in result.stderr


def test_schemas_are_valid_json_and_strict_at_root():
    for name in ("workflow-state.schema.json", "workflow-event.schema.json"):
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
        assert schema["type"] == "object"


def test_initializer_rejects_traversal_and_windows_absolute_scope_paths(tmp_path):
    for index, unsafe in enumerate(("../secret.py", "C:\\secrets\\token.py")):
        task = task_data()
        task["scope"]["write_paths"] = [unsafe]
        task_path = tmp_path / f"task-{index}.json"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        result = run_script(
            "init-workflow-state.py", "--task", task_path,
            "--run-dir", tmp_path / f"run-{index}",
            "--repository-state-hash", REPOSITORY_HASH,
        )
        assert result.returncode == 1
        assert "repository-relative and traversal-free" in result.stderr


def test_initializer_rejects_non_hash_repository_binding(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps(task_data()), encoding="utf-8")
    result = run_script(
        "init-workflow-state.py", "--task", task, "--run-dir", tmp_path / "run",
        "--repository-state-hash", "repo-state",
    )
    assert result.returncode == 1
    assert "repository_state_hash must be a sha256" in result.stderr


def test_nested_evidence_must_be_registered_before_use(tmp_path):
    run_dir = initialize(tmp_path)
    state = load_state(run_dir)
    delta = write_delta(tmp_path, state["state_id"], [
        {"event_type": "decision-accepted", "payload": {
            "id": "D-UNREGISTERED", "statement": "Unsupported", "evidence_refs": [EVIDENCE_1],
        }},
    ])
    result = run_script(
        "apply-workflow-delta.py", "--state", run_dir / "WORKFLOW_STATE.json",
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--delta", delta,
        "--actor", "planner",
    )
    assert result.returncode == 1
    assert "not registered in state.evidence_refs" in result.stderr


def test_state_validator_enforces_nested_evidence_subset(tmp_path):
    run_dir = initialize(tmp_path)
    state_path = run_dir / "WORKFLOW_STATE.json"
    state = load_state(run_dir)
    state["accepted_decisions"].append({
        "id": "D-BYPASS", "statement": "Injected", "status": "accepted",
        "evidence_refs": [EVIDENCE_1],
    })
    sys.path.insert(0, str(SCRIPTS))
    from workflow_state import state_id_for
    state["state_id"] = state_id_for(state)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_script(
        "validate-workflow-state.py", "--state", state_path,
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl",
    )
    assert result.returncode == 1
    assert "absent from state.evidence_refs" in result.stderr


def test_recovery_previews_and_applies_event_ahead_state(tmp_path):
    run_dir = initialize(tmp_path)
    state_path = run_dir / "WORKFLOW_STATE.json"
    events_path = run_dir / "WORKFLOW_EVENTS.jsonl"
    old_state = state_path.read_bytes()
    state = load_state(run_dir)
    delta = write_delta(tmp_path, state["state_id"], [
        {"event_type": "evidence-added", "payload": {"ref": EVIDENCE_1}},
    ])
    applied = run_script(
        "apply-workflow-delta.py", "--state", state_path, "--events", events_path,
        "--delta", delta, "--actor", "planner",
    )
    assert applied.returncode == 0, applied.stderr
    recovered_state = state_path.read_bytes()
    state_path.write_bytes(old_state)

    preview = run_script(
        "recover-workflow-state.py", "--state", state_path, "--events", events_path,
        "--preview",
    )
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["classification"] == "event-ahead"
    assert state_path.read_bytes() == old_state

    recovery = run_script(
        "recover-workflow-state.py", "--state", state_path, "--events", events_path,
        "--apply",
    )
    assert recovery.returncode == 0, recovery.stderr
    assert state_path.read_bytes() == recovered_state
    receipt = json.loads((run_dir / "WORKFLOW_RECOVERY_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["before_state_id"] == state["state_id"]
    assert receipt["after_state_id"] == load_state(run_dir)["state_id"]
    assert receipt["receipt_id"].startswith("sha256:")
    validation = run_script(
        "validate-workflow-state.py", "--state", state_path, "--events", events_path,
    )
    assert validation.returncode == 0, validation.stderr


def test_recovery_fails_closed_when_state_is_ahead(tmp_path):
    run_dir = initialize(tmp_path)
    state_path = run_dir / "WORKFLOW_STATE.json"
    events_path = run_dir / "WORKFLOW_EVENTS.jsonl"
    state = load_state(run_dir)
    delta = write_delta(tmp_path, state["state_id"], [
        {"event_type": "question-opened", "payload": {"id": "Q-AHEAD", "question": "Ahead?"}},
    ])
    applied = run_script(
        "apply-workflow-delta.py", "--state", state_path, "--events", events_path,
        "--delta", delta, "--actor", "planner",
    )
    assert applied.returncode == 0, applied.stderr
    current_state = state_path.read_bytes()
    lines = events_path.read_text(encoding="utf-8").splitlines()
    events_path.write_text(lines[0] + "\n", encoding="utf-8")

    result = run_script(
        "recover-workflow-state.py", "--state", state_path, "--events", events_path,
        "--apply",
    )
    assert result.returncode == 1
    assert "state-ahead" in result.stderr
    assert state_path.read_bytes() == current_state
    assert not (run_dir / "WORKFLOW_RECOVERY_RECEIPT.json").exists()


def test_recovery_refuses_receipt_aliasing_state(tmp_path):
    run_dir = initialize(tmp_path)
    state_path = run_dir / "WORKFLOW_STATE.json"
    before = state_path.read_bytes()
    result = run_script(
        "recover-workflow-state.py", "--state", state_path,
        "--events", run_dir / "WORKFLOW_EVENTS.jsonl", "--apply",
        "--receipt", state_path,
    )
    assert result.returncode == 1
    assert "receipt path must be distinct" in result.stderr
    assert state_path.read_bytes() == before
