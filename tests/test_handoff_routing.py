import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from tests._unittest_compat import load_function_tests


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from handoff_routing import calibrate_estimates, estimate_paths, validate_estimate  # noqa: E402
from owner_lease import select_owner  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recorder = load("phase8_recorder", SCRIPTS / "record-handoff-event.py")
economics = load("phase8_economics", SCRIPTS / "workflow_economics.py")
router = load("phase8_router", SCRIPTS / "route-task.py")


def record(path, index, **overrides):
    values = {
        "sender": "codex", "receiver": "claude", "task_type": "builder",
        "dispatch_outcome": "success", "payload_bytes": 1000,
        "novel_payload_bytes": 800, "repeated_payload_bytes": 200,
        "task_card_bytes": 500, "review_packet_bytes": 100,
        "receiver_reads_before_first_action": 2,
        "receiver_searches_before_first_action": 1,
        "seconds_to_first_meaningful_action": 10,
        "known_facts_rediscovered": 2, "rejected_hypotheses_revisited": 1,
        "handoff_revision_count": 1, "context_objects_requested": 4,
        "context_cache_hits": 3,
    }
    values.update(overrides)
    recorder.record_handoff(path, run_id=f"run-{index}", task_id=f"task-{index}", detail=recorder.build_handoff_detail(**values))


def policy(**overrides):
    value = {
        "schema_version": 1, "task_type": "builder", "direct_cost_units": 100,
        "direct_active_seconds": 100, "direct_codex_work_units": 100,
        "serialization_cost_per_byte": 0.001,
        "reconstruction_cost_per_second": 1,
        "rediscovery_cost_per_item": 2, "revision_cost_per_item": 5,
        "codex_work_per_rediscovery": 2, "codex_work_per_revision": 5,
    }
    value.update(overrides)
    return value


def no_risks():
    return {key: "no" for key in router.HIGH}


def route_facts(**overrides):
    value = {
        "ownership_profile": "economy-first", "effective_risks": no_risks(),
        "target_files_count": 4, "predicted_diff_lines": 200, "exact_validation": True,
        "claude_role": "execution-builder", "task_role": "auxiliary",
        "durable_output_required": True, "codex_review_scope": "bounded",
        "delegation_value": True, "expected_delegated_cost_ratio": 0.8,
        "expected_active_elapsed_ratio": 1.2,
        "expected_codex_work_reduction_ratio": 0.5,
    }
    value.update(overrides)
    return value


def continuation_lease(model="claude"):
    owner = "claude-session-1" if model == "claude" else "codex-session-1"
    return select_owner({
        "schema_version": 1, "task_id": "T-8", "state_id": "sha256:" + "8" * 64,
        "operation": "continuation", "original_builder_id": owner,
        "current_builder_id": owner, "current_session_id": "session-1",
        "resume_status": "succeeded", "new_evidence_refs": [], "semantic_blockers": [],
        "explicit_owner_id": None, "switch_reason": None,
        "last_handoff_event_id": None, "lease_ttl_seconds": 1800,
    })


def test_estimator_is_unknown_canary_then_calibrated_from_observations(tmp_path):
    events = tmp_path / "events.jsonl"
    assert estimate_paths([events], task_type="builder")["status"] == "unknown"
    record(events, 1)
    one = estimate_paths([events], task_type="builder")
    assert one["status"] == "canary"
    record(events, 2)
    record(events, 3)
    three = estimate_paths([events], task_type="builder")
    assert three["status"] == "calibrated"
    assert three["components"]["serialization_bytes"] == 1000
    assert three["components"]["rediscovery_count"] == 3
    assert three["components"]["context_cache_hit_rate"] == 0.75
    assert validate_estimate(three) == []


def test_incomplete_observations_never_become_zero_or_calibrated(tmp_path):
    events = tmp_path / "events.jsonl"
    for index in range(3):
        record(events, index, seconds_to_first_meaningful_action="unknown")
    value = estimate_paths([events], task_type="builder")
    assert value["status"] == "unknown"
    assert value["components"]["reconstruction_seconds"] is None


def test_calibration_uses_explicit_cost_policy_and_observed_samples(tmp_path):
    events = tmp_path / "events.jsonl"
    for index in range(3):
        record(events, index)
    estimate = estimate_paths([events], task_type="builder")
    value = calibrate_estimates([estimate], policy())
    assert value["status"] == "calibrated"
    assert value["source"] == "observed-calibration"
    assert value["handoff_cost_units"] == 22
    assert value["penalty_cost_ratio"] == 0.22
    assert value["penalty_active_elapsed_ratio"] == 0.1
    assert value["penalty_codex_work_ratio"] == 0.11


def test_economics_applies_only_observed_calibration(tmp_path):
    events = tmp_path / "events.jsonl"
    for index in range(3):
        record(events, index)
    calibrated = calibrate_estimates([estimate_paths([events], task_type="builder")], policy())
    gate = economics.delegation_economy_gate(route_facts(handoff_tax=calibrated))
    assert gate["handoff_tax"]["applied"] is True
    assert abs(gate["expected_delegated_cost_ratio"] - 1.02) < 1e-12
    assert gate["status"] == "reject"
    model_guess = {**calibrated, "source": "spark-model-estimate", "penalty_cost_ratio": 0.0}
    gate = economics.delegation_economy_gate(route_facts(handoff_tax=model_guess))
    assert gate["handoff_tax"]["applied"] is False
    assert gate["handoff_tax"]["unverified_input_ignored"] is True
    assert gate["status"] == "canary"
    tampered = {**calibrated, "penalty_cost_ratio": 0.0}
    gate = economics.delegation_economy_gate(route_facts(handoff_tax=tampered))
    assert gate["handoff_tax"]["applied"] is False
    assert gate["handoff_tax"]["unverified_input_ignored"] is True


def test_router_prefers_same_model_continuation():
    decision = router.route(route_facts(
        owner_lease=continuation_lease(), workflow_state_id="sha256:" + "8" * 64,
    ))
    assert decision["execution"]["owner"] == "claude-builder"
    assert decision["execution"]["owner_source"] == "continuation-lease"
    assert decision["communication_routing"]["mode"] == "same-model-single-pass"


def test_router_rejects_unverified_or_state_mismatched_continuation():
    raw = router.route(route_facts(continuation_eligible=True, continuation_owner="codex-fast-path"))
    assert raw["communication_routing"]["continuation_selected"] is False
    mismatched = router.route(route_facts(
        owner_lease=continuation_lease("codex"), workflow_state_id="sha256:" + "9" * 64,
    ))
    assert mismatched["communication_routing"]["continuation_selected"] is False
    assert mismatched["communication_routing"]["continuation_reason"] == "owner-lease-state-mismatch"


def test_router_bypasses_uneconomic_cross_model_flow_but_explicit_owner_wins(tmp_path):
    events = tmp_path / "events.jsonl"
    for index in range(3):
        record(events, index, handoff_revision_count=4)
    calibrated = calibrate_estimates([estimate_paths([events], task_type="builder")], policy())
    decision = router.route(route_facts(ownership_profile="claude-first", handoff_tax=calibrated))
    assert decision["execution"]["owner"] == "codex-fast-path"
    assert decision["execution"]["owner_source"] == "handoff-tax-veto"
    assert decision["communication_routing"]["handoff_tax_veto"] is True
    explicit = router.route(route_facts(
        ownership_profile="claude-first", handoff_tax=calibrated,
        execution_owner="claude-builder",
    ))
    assert explicit["execution"]["owner"] == "claude-builder"
    assert explicit["execution"]["owner_source"] == "explicit-human-owner"


def test_spark_is_skipped_for_calibrated_route_and_never_authoritative(tmp_path):
    events = tmp_path / "events.jsonl"
    for index in range(3):
        record(events, index)
    calibrated = calibrate_estimates([estimate_paths([events], task_type="builder")], policy())
    decision = router.route(route_facts(handoff_tax=calibrated, spark_route_requested=True))
    assert decision["precard_estimator"] == {
        "spark_action": "skip", "reason_code": "communication-aware-deterministic-route",
        "decision_complete": True,
    }
    assert decision["communication_routing"]["spark_estimate_authoritative"] is False


def test_cli_roundtrip(tmp_path):
    events = tmp_path / "events.jsonl"
    for index in range(3):
        record(events, index)
    estimate_path = tmp_path / "estimate.json"
    policy_path = tmp_path / "policy.json"
    calibration_path = tmp_path / "calibration.json"
    policy_path.write_text(json.dumps(policy()), encoding="utf-8")
    first = subprocess.run([
        sys.executable, str(SCRIPTS / "estimate-handoff-tax.py"), str(events),
        "--task-type", "builder", "-o", str(estimate_path),
    ], cwd=ROOT, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([
        sys.executable, str(SCRIPTS / "calibrate-handoff-routing.py"),
        "--estimate", str(estimate_path), "--policy", str(policy_path),
        "-o", str(calibration_path),
    ], cwd=ROOT, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(calibration_path.read_text(encoding="utf-8"))["status"] == "calibrated"
    installer = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
    for name in ("handoff_routing.py", "estimate-handoff-tax.py", "calibrate-handoff-routing.py"):
        assert name in installer


def load_tests(loader, tests, pattern):
    return load_function_tests(globals())
