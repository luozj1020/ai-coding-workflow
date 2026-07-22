import json
import subprocess
import sys
from pathlib import Path

from tests._unittest_compat import load_function_tests, raises


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
REPOSITORY_HASH = "sha256:" + "a" * 64

from acceptance_graph import (  # noqa: E402
    AcceptanceGraphError, build_delta_packet, build_graph, hash_document,
    validate_graph, validate_receipt,
)
from evidence_store import build_object, store_object  # noqa: E402
from workflow_state import state_id_for  # noqa: E402


def metadata(kind, path="src/main.py", tool="pytest"):
    return {
        "schema_version": 1,
        "kind": kind,
        "repository": {"commit": "abc123", "path": path},
        "selector": {"symbol": None, "start_line": None, "end_line": None},
        "producer": {"tool": tool, "version": "1"},
        "dependency_hashes": {
            "file_hash": "sha256:file", "symbol_hash": None,
            "build_configuration_hash": None, "validation_command_hash": None,
            "worktree_state_hash": "sha256:worktree",
        },
    }


def put(store, kind, payload, path="src/main.py", tool="pytest"):
    obj = build_object(metadata(kind, path, tool), json.dumps(payload).encode(), "json")
    store_object(store, obj)
    return obj["object_id"]


def make_state(acceptance, decisions=None, phase="review"):
    state = {
        "schema_version": 1, "state_id": "", "parent_state_id": None,
        "revision": 0, "task_id": "phase6-test", "phase": phase,
        "repository_state_hash": REPOSITORY_HASH,
        "goal": {"id": "G-1", "statement": "prove acceptance", "acceptance_ids": sorted(acceptance)},
        "constraints": [], "accepted_decisions": decisions or [],
        "rejected_hypotheses": [], "open_questions": [],
        "evidence_refs": sorted({ref for item in acceptance.values() for ref in item["evidence_refs"]}),
        "acceptance_status": acceptance,
        "next_action": {"owner": "codex", "operation": "review", "allowed_paths": []},
    }
    state["state_id"] = state_id_for(state)
    return state


def accepted_receipt(graph, accepted=(), conditional=(), rejected=()):
    receipt = {
        "schema_version": 1, "review_id": "", "bound_state_id": graph["state_id"],
        "bound_graph_id": graph["graph_id"], "reviewer": "codex",
        "accepted": list(accepted), "conditional": list(conditional),
        "rejected": list(rejected), "frozen_decisions_confirmed": [],
        "new_questions": [],
    }
    receipt["review_id"] = hash_document(receipt, "review_id")
    return receipt


def test_satisfied_acceptance_requires_immutable_supporting_evidence(tmp_path):
    result_ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [result_ref]}})
    graph = build_graph(state, tmp_path / "objects")
    assert graph["state_id"] == state["state_id"]
    assert graph["acceptance_items"][0]["graph_status"] == "supported"
    assert graph["acceptance_items"][0]["result_refs"] == [result_ref]
    assert validate_graph(graph) == []


def test_satisfied_without_evidence_is_explicitly_unsupported(tmp_path):
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": []}})
    item = build_graph(state, tmp_path / "objects")["acceptance_items"][0]
    assert item["graph_status"] == "unsupported"
    assert "satisfied-without-immutable-evidence" in item["unverified_claims"]


def test_lexical_candidate_alone_cannot_support_acceptance(tmp_path):
    ref = put(
        tmp_path / "objects", "callers",
        {"analysis_method": "bounded-lexical-candidate", "status": "passed", "semantic_guarantee": False},
        tool="context-broker",
    )
    state = make_state({"AC-1": {"description": "callers", "status": "satisfied", "evidence_refs": [ref]}})
    item = build_graph(state, tmp_path / "objects")["acceptance_items"][0]
    assert item["graph_status"] == "unsupported"
    assert "bounded-lexical-candidate-cannot-satisfy-acceptance" in item["unverified_claims"]


def test_contradictory_evidence_fails_closed(tmp_path):
    passed = put(tmp_path / "objects", "test-result", {"status": "passed"})
    failed = put(tmp_path / "objects", "test-result", {"status": "failed"}, path="tests/test_main.py")
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [passed, failed]}})
    item = build_graph(state, tmp_path / "objects")["acceptance_items"][0]
    assert item["graph_status"] == "contradictory"
    assert item["contradictory_refs"] == [failed]


def test_missing_or_stale_evidence_fails_graph_build(tmp_path):
    missing = "sha256:" + "0" * 64
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [missing]}})
    with raises(AcceptanceGraphError, match="unreadable"):
        build_graph(state, tmp_path / "objects")

    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    obj_path = next((tmp_path / "objects").glob("*/*.json"))
    sidecar = obj_path.with_suffix(".validity.json")
    sidecar.write_text(json.dumps({
        "schema_version": 1, "object_id": ref, "status": "stale",
        "checked_at": "2026-07-21T00:00:00Z", "current_context_hash": "sha256:" + "1" * 64,
        "reasons": [{"dependency": "file_hash", "status": "stale", "expected": "old", "actual": "new"}],
    }), encoding="utf-8")
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}})
    with raises(AcceptanceGraphError, match="is stale"):
        build_graph(state, tmp_path / "objects")


def test_new_diff_on_evidence_path_reopens_previously_supported_item(tmp_path):
    store = tmp_path / "objects"
    passed = put(store, "test-result", {"status": "passed"}, path="src/main.py")
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [passed]}})
    previous = build_graph(state, store)
    diff = put(store, "diff-hunk", {"status": "changed"}, path="src/main.py")
    graph = build_graph(state, store, previous=previous, new_diff_refs=[diff])
    assert graph["acceptance_items"][0]["graph_status"] == "reopened"
    assert graph["reopened_acceptance"] == ["AC-1"]


def test_newly_linked_diff_is_detected_and_reopens_without_cli_hint(tmp_path):
    store = tmp_path / "objects"
    passed = put(store, "test-result", {"status": "passed"}, path="src/main.py")
    first_state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [passed]}})
    previous = build_graph(first_state, store)
    diff = put(store, "diff-hunk", {"status": "changed"}, path="src/main.py")
    second_state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [passed, diff]}})
    graph = build_graph(second_state, store, previous=previous)
    assert graph["new_diff_refs"] == [diff]
    assert graph["reopened_acceptance"] == ["AC-1"]


def test_unrelated_diff_does_not_reopen_supported_item(tmp_path):
    store = tmp_path / "objects"
    passed = put(store, "test-result", {"status": "passed"}, path="src/main.py")
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [passed]}})
    previous = build_graph(state, store)
    graph = build_graph(state, store, previous=previous, changed_paths=["docs/readme.md"])
    assert graph["acceptance_items"][0]["graph_status"] == "supported"


def test_default_delta_omits_only_receipted_unchanged_supported_items(tmp_path):
    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}})
    graph = build_graph(state, tmp_path / "objects")
    assert [item["id"] for item in build_delta_packet(graph, previous=graph)["acceptance_items"]] == ["AC-1"]
    receipt = accepted_receipt(graph, accepted=["AC-1"])
    packet = build_delta_packet(graph, previous=graph, receipt=receipt)
    assert packet["acceptance_items"] == []
    assert packet["omitted_unchanged_accepted"] == ["AC-1"]


def test_revision_packet_contains_only_failed_evidence_subgraphs(tmp_path):
    passed = put(tmp_path / "objects", "test-result", {"status": "passed"})
    state = make_state({
        "AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [passed]},
        "AC-2": {"description": "needs work", "status": "failed", "evidence_refs": []},
    })
    graph = build_graph(state, tmp_path / "objects")
    packet = build_delta_packet(graph, mode="revision")
    assert [item["id"] for item in packet["acceptance_items"]] == ["AC-2"]


def test_receipt_binds_exact_state_and_refuses_unsupported_acceptance(tmp_path):
    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}})
    graph = build_graph(state, tmp_path / "objects")
    receipt = accepted_receipt(graph, accepted=["AC-1"])
    assert validate_receipt(receipt, graph) == []
    receipt["bound_state_id"] = "sha256:" + "f" * 64
    receipt["review_id"] = hash_document(receipt, "review_id")
    assert any("exact Workflow State" in error for error in validate_receipt(receipt, graph))

    unsupported = build_graph(make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": []}}), tmp_path / "objects")
    bad = accepted_receipt(unsupported, accepted=["AC-1"])
    assert any("cannot accept non-supported" in error for error in validate_receipt(bad, unsupported))


def test_receipt_must_classify_exact_packet_scope(tmp_path):
    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    graph = build_graph(make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}}), tmp_path / "objects")
    packet = build_delta_packet(graph)
    empty = accepted_receipt(graph)
    assert any("every and only" in error for error in validate_receipt(empty, graph, packet))


def test_prior_receipt_must_bind_previous_graph_not_changed_graph(tmp_path):
    store = tmp_path / "objects"
    ref = put(store, "test-result", {"status": "passed"})
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}})
    previous = build_graph(state, store)
    receipt = accepted_receipt(previous, accepted=["AC-1"])
    current = build_graph(state, store, previous=previous, changed_paths=["docs/readme.md"])
    packet = build_delta_packet(current, previous=previous, receipt=receipt)
    assert packet["acceptance_items"] == []
    changed = build_graph(state, store, previous=previous, changed_paths=["src/main.py"])
    packet = build_delta_packet(changed, previous=previous, receipt=receipt)
    assert [item["id"] for item in packet["acceptance_items"]] == ["AC-1"]


def test_changed_decision_is_reported_even_when_id_is_stable(tmp_path):
    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    acceptance = {"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}}
    first_state = make_state(acceptance, [{"id": "D-1", "statement": "old", "status": "frozen", "evidence_refs": [ref]}])
    second_state = make_state(acceptance, [{"id": "D-1", "statement": "new", "status": "frozen", "evidence_refs": [ref]}])
    first = build_graph(first_state, tmp_path / "objects")
    second = build_graph(second_state, tmp_path / "objects", previous=first)
    assert build_delta_packet(second, previous=first)["changed_decisions"] == ["D-1"]


def test_tampered_packet_is_rejected_by_receipt_validation(tmp_path):
    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    graph = build_graph(make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}}), tmp_path / "objects")
    packet = build_delta_packet(graph)
    packet["unsupported_acceptance"] = ["AC-1"]
    packet["packet_id"] = hash_document(packet, "packet_id")
    receipt = accepted_receipt(graph, accepted=["AC-1"])
    assert any("does not match packet subgraphs" in error for error in validate_receipt(receipt, graph, packet))


def test_acceptance_ref_must_also_be_declared_in_state_evidence_refs(tmp_path):
    ref = put(tmp_path / "objects", "test-result", {"status": "passed"})
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}})
    state["evidence_refs"] = []
    state["state_id"] = state_id_for(state)
    with raises(AcceptanceGraphError, match="absent from state.evidence_refs"):
        build_graph(state, tmp_path / "objects")


def load_tests(loader, tests, pattern):
    return load_function_tests(globals())


def test_cli_round_trip_and_strict_schemas_are_installed(tmp_path):
    store = tmp_path / "objects"
    ref = put(store, "test-result", {"status": "passed"})
    state = make_state({"AC-1": {"description": "works", "status": "satisfied", "evidence_refs": [ref]}})
    state_path = tmp_path / "state.json"
    graph_path = tmp_path / "graph.json"
    packet_path = tmp_path / "packet.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build-acceptance-graph.py"), "--state", str(state_path), "--store", str(store), "-o", str(graph_path)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build-delta-review-packet.py"), "--graph", str(graph_path), "-o", str(packet_path)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    receipt = accepted_receipt(graph, accepted=["AC-1"])
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/validate-review-receipt.py"), "--receipt", str(receipt_path), "--graph", str(graph_path), "--packet", str(packet_path)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    installer = (ROOT / "scripts/install_workflow.py").read_text(encoding="utf-8")
    for name in ("acceptance_graph.py", "build-acceptance-graph.py", "build-delta-review-packet.py", "validate-review-receipt.py", "acceptance-graph.schema.json", "review-receipt.schema.json"):
        assert name in installer
    for name in ("acceptance-graph.schema.json", "review-receipt.schema.json"):
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
