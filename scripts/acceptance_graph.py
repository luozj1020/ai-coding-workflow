#!/usr/bin/env python3
"""Strict Acceptance Graph, delta-review, and Review Receipt primitives."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from evidence_store import EvidenceStoreError, content_bytes, load_object
from workflow_state import (
    WorkflowStateError, canonical_json, is_safe_repo_path_pattern, validate_state,
)


HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GRAPH_STATUSES = {"supported", "unsupported", "contradictory", "reopened"}
PASS_VALUES = {"pass", "passed", "supported", "satisfied", "success", "succeeded"}
FAIL_VALUES = {"fail", "failed", "error", "errored", "rejected", "contradictory"}
MAX_GRAPH_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024


class AcceptanceGraphError(WorkflowStateError):
    """A graph, review packet, or receipt is unsafe or invalid."""


def hash_document(value: Dict[str, Any], identity_field: str) -> str:
    material = deepcopy(value)
    material.pop(identity_field, None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def load_bounded_json(path: Path, maximum: int, label: str) -> Any:
    try:
        if path.is_symlink():
            raise AcceptanceGraphError(f"{label} must not be a symlink")
        if path.stat().st_size > maximum:
            raise AcceptanceGraphError(f"{label} exceeds {maximum} byte limit")
        return json.loads(path.read_text(encoding="utf-8"))
    except AcceptanceGraphError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceGraphError(f"cannot read {label}: {exc}") from exc


def _object_payload(obj: Dict[str, Any]) -> Any:
    if obj["content"]["encoding"] == "json":
        return obj["content"]["value"]
    try:
        return json.loads(content_bytes(obj["content"]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def assess_evidence(obj: Dict[str, Any]) -> str:
    """Return supporting, contradictory, lexical-candidate, or neutral."""
    if obj["kind"] in {"compiler-error", "runtime-error"}:
        return "contradictory"
    payload = _object_payload(obj)
    if not isinstance(payload, dict):
        return "neutral"
    method = str(payload.get("analysis_method", "")).lower()
    if "lexical" in method or payload.get("evidence_quality") == "bounded-lexical-candidate":
        return "lexical-candidate"
    value = str(payload.get("status", payload.get("outcome", ""))).lower()
    if value in FAIL_VALUES:
        return "contradictory"
    if value in PASS_VALUES and (
        obj["kind"] in {"test-result", "acceptance-record"}
        or payload.get("semantic_guarantee") is True
    ):
        return "supporting"
    return "neutral"


def _paths_overlap(left: str, right: str) -> bool:
    left = left.replace("\\", "/").rstrip("/")
    right = right.replace("\\", "/").rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _load_refs(store: Path, refs: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    objects = {}
    for ref in sorted(set(refs)):
        if not HASH_RE.fullmatch(ref):
            raise AcceptanceGraphError(f"Acceptance evidence ref is not an immutable object ID: {ref}")
        try:
            objects[ref] = load_object(store, ref)
        except EvidenceStoreError as exc:
            raise AcceptanceGraphError(str(exc)) from exc
    return objects


def build_graph(
    state: Dict[str, Any], store: Path, *, previous: Optional[Dict[str, Any]] = None,
    new_diff_refs: Iterable[str] = (), changed_paths: Iterable[str] = (),
) -> Dict[str, Any]:
    errors = validate_state(state)
    if errors:
        raise AcceptanceGraphError("invalid Workflow State: " + "; ".join(errors))
    if previous is not None:
        errors = validate_graph(previous)
        if errors:
            raise AcceptanceGraphError("invalid previous Acceptance Graph: " + "; ".join(errors))
        if previous["task_id"] != state["task_id"]:
            raise AcceptanceGraphError("previous Acceptance Graph belongs to a different task")

    prior_evidence_refs = set()
    if previous:
        prior_evidence_refs = {
            ref for item in previous["acceptance_items"]
            for field in ("implementation_refs", "test_refs", "result_refs", "other_evidence_refs")
            for ref in item[field]
        }
    current_acceptance_refs = {
        ref for item in state["acceptance_status"].values() for ref in item["evidence_refs"]
    }
    newly_linked = _load_refs(store, current_acceptance_refs - prior_evidence_refs)
    automatic_diff_refs = {
        ref for ref, obj in newly_linked.items() if obj["kind"] == "diff-hunk"
    }
    diff_objects = _load_refs(store, set(new_diff_refs) | automatic_diff_refs)
    for object_id, obj in diff_objects.items():
        if obj["kind"] != "diff-hunk":
            raise AcceptanceGraphError(f"new diff ref is not a diff-hunk: {object_id}")
    impact_paths = set(changed_paths)
    impact_paths.update(
        obj["repository"]["path"] for obj in diff_objects.values()
        if obj["repository"]["path"]
    )
    if any(not is_safe_repo_path_pattern(path) for path in impact_paths):
        raise AcceptanceGraphError("changed paths must be repository-relative and traversal-free")

    previous_items = {
        item["id"]: item for item in (previous or {}).get("acceptance_items", [])
    }
    decisions = state["accepted_decisions"]
    items = []
    reopened = []
    for acceptance_id in sorted(state["acceptance_status"]):
        source = state["acceptance_status"][acceptance_id]
        if not set(source["evidence_refs"]).issubset(set(state["evidence_refs"])):
            raise AcceptanceGraphError(
                f"Acceptance {acceptance_id} references evidence absent from Workflow State evidence_refs"
            )
        objects = _load_refs(store, source["evidence_refs"])
        assessments = {ref: assess_evidence(obj) for ref, obj in objects.items()}
        contradictory = sorted(ref for ref, value in assessments.items() if value == "contradictory")
        supporting = sorted(ref for ref, value in assessments.items() if value == "supporting")
        lexical = sorted(ref for ref, value in assessments.items() if value == "lexical-candidate")
        paths = sorted({
            obj["repository"]["path"] for obj in objects.values()
            if obj["repository"]["path"]
        })
        implementation = sorted(ref for ref, obj in objects.items() if obj["kind"] == "diff-hunk")
        tests = sorted(ref for ref, obj in objects.items() if obj["kind"] == "test-definition")
        results = sorted(ref for ref, obj in objects.items() if obj["kind"] in {"test-result", "compiler-error", "runtime-error"})
        classified = set(implementation + tests + results)
        other = sorted(set(objects) - classified)
        decision_refs = sorted(
            decision["id"] for decision in decisions
            if set(decision["evidence_refs"]) & set(objects)
        )
        claims: List[str] = []
        if source["status"] == "satisfied" and not objects:
            claims.append("satisfied-without-immutable-evidence")
        if source["status"] == "satisfied" and not supporting:
            claims.append("no-deterministic-or-semantic-support")
        if lexical and not supporting:
            claims.append("bounded-lexical-candidate-cannot-satisfy-acceptance")
        if contradictory:
            status = "contradictory"
            claims.append("contradictory-evidence-present")
        elif source["status"] == "satisfied" and supporting:
            status = "supported"
        else:
            status = "unsupported"

        prior = previous_items.get(acceptance_id)
        prior_paths = prior["evidence_paths"] if prior else []
        if prior and prior["graph_status"] == "supported" and any(
            _paths_overlap(path, changed) for path in prior_paths for changed in impact_paths
        ):
            status = "reopened"
            claims.append("accepted-evidence-path-changed")
            reopened.append(acceptance_id)
        items.append({
            "id": acceptance_id,
            "description": source["description"],
            "state_status": source["status"],
            "graph_status": status,
            "decision_refs": decision_refs,
            "implementation_refs": implementation,
            "test_refs": tests,
            "result_refs": results,
            "other_evidence_refs": other,
            "contradictory_refs": contradictory,
            "evidence_paths": paths,
            "unverified_claims": sorted(set(claims)),
        })
    graph = {
        "schema_version": 1,
        "graph_id": "",
        "state_id": state["state_id"],
        "repository_state_hash": state["repository_state_hash"],
        "task_id": state["task_id"],
        "acceptance_items": items,
        "decisions": [{
            "id": decision["id"], "status": decision["status"],
            "evidence_refs": sorted(decision["evidence_refs"]),
            "decision_hash": "sha256:" + hashlib.sha256(
                canonical_json(decision).encode("utf-8")
            ).hexdigest(),
        } for decision in sorted(decisions, key=lambda item: item["id"])],
        "frozen_decisions": sorted(
            decision["id"] for decision in decisions if decision["status"] == "frozen"
        ),
        "new_diff_refs": sorted(diff_objects),
        "changed_paths": sorted(impact_paths),
        "reopened_acceptance": sorted(reopened),
    }
    graph["graph_id"] = hash_document(graph, "graph_id")
    return graph


ITEM_FIELDS = {
    "id", "description", "state_status", "graph_status", "decision_refs",
    "implementation_refs", "test_refs", "result_refs", "other_evidence_refs",
    "contradictory_refs", "evidence_paths", "unverified_claims",
}
GRAPH_FIELDS = {
    "schema_version", "graph_id", "state_id", "repository_state_hash", "task_id",
    "acceptance_items", "decisions", "frozen_decisions", "new_diff_refs", "changed_paths",
    "reopened_acceptance",
}


def _valid_strings(value: Any, *, hashes: bool = False) -> bool:
    return isinstance(value, list) and len(value) == len(set(value)) and all(
        isinstance(item, str) and item and (not hashes or HASH_RE.fullmatch(item)) for item in value
    )


def validate_graph(graph: Any) -> List[str]:
    if not isinstance(graph, dict) or set(graph) != GRAPH_FIELDS:
        return ["Acceptance Graph fields do not match schema"]
    errors = []
    if graph.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("graph_id", "state_id"):
        if not isinstance(graph.get(field), str) or not HASH_RE.fullmatch(graph[field]):
            errors.append(f"{field} must be a sha256: digest")
    for field in ("repository_state_hash", "task_id"):
        if not isinstance(graph.get(field), str) or not graph[field]:
            errors.append(f"{field} must be non-empty")
    decisions = graph.get("decisions")
    if not isinstance(decisions, list):
        errors.append("decisions must be an array")
        decisions = []
    decision_ids = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or set(decision) != {"id", "status", "evidence_refs", "decision_hash"}:
            errors.append(f"decisions[{index}] fields do not match schema")
            continue
        decision_ids.append(decision.get("id"))
        if decision.get("status") not in {"accepted", "frozen"}:
            errors.append(f"decisions[{index}].status is invalid")
        if not _valid_strings(decision.get("evidence_refs")):
            errors.append(f"decisions[{index}].evidence_refs is invalid")
        if not isinstance(decision.get("decision_hash"), str) or not HASH_RE.fullmatch(decision["decision_hash"]):
            errors.append(f"decisions[{index}].decision_hash is invalid")
    if len(decision_ids) != len(set(decision_ids)) or any(not isinstance(item, str) or not item for item in decision_ids):
        errors.append("decision IDs must be unique non-empty strings")
    items = graph.get("acceptance_items")
    if not isinstance(items, list):
        errors.append("acceptance_items must be an array")
        items = []
    ids = []
    hash_lists = {"implementation_refs", "test_refs", "result_refs", "other_evidence_refs", "contradictory_refs"}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            errors.append(f"acceptance_items[{index}] fields do not match schema")
            continue
        ids.append(item.get("id"))
        if not isinstance(item.get("description"), str) or not item["description"]:
            errors.append(f"acceptance_items[{index}].description is invalid")
        if item.get("graph_status") not in GRAPH_STATUSES:
            errors.append(f"acceptance_items[{index}].graph_status is invalid")
        if item.get("state_status") not in {"pending", "satisfied", "failed", "blocked", "unknown"}:
            errors.append(f"acceptance_items[{index}].state_status is invalid")
        for field in ITEM_FIELDS - {"id", "description", "state_status", "graph_status"}:
            if not _valid_strings(item.get(field), hashes=field in hash_lists):
                errors.append(f"acceptance_items[{index}].{field} is invalid")
        all_refs = sum((item[field] for field in hash_lists - {"contradictory_refs"}), [])
        if item.get("graph_status") == "supported" and not all_refs:
            errors.append(f"supported Acceptance {item.get('id')} has no Evidence Object ref")
        if not set(item.get("contradictory_refs", [])).issubset(set(all_refs)):
            errors.append(f"acceptance_items[{index}].contradictory_refs are not in evidence refs")
    if len(ids) != len(set(ids)) or any(not isinstance(item, str) or not item for item in ids):
        errors.append("Acceptance IDs must be unique non-empty strings")
    for field in ("frozen_decisions", "changed_paths", "reopened_acceptance"):
        if not _valid_strings(graph.get(field)):
            errors.append(f"{field} is invalid")
    if not _valid_strings(graph.get("new_diff_refs"), hashes=True):
        errors.append("new_diff_refs is invalid")
    if set(graph.get("frozen_decisions", [])) != {
        decision["id"] for decision in decisions if decision.get("status") == "frozen"
    }:
        errors.append("frozen_decisions does not match decision records")
    reopened = sorted(item["id"] for item in items if item.get("graph_status") == "reopened")
    if graph.get("reopened_acceptance") != reopened:
        errors.append("reopened_acceptance does not match reopened graph items")
    if isinstance(graph.get("graph_id"), str) and graph["graph_id"] != hash_document(graph, "graph_id"):
        errors.append("graph_id does not match canonical graph content")
    return errors


def _item_fingerprint(item: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(item).encode("utf-8")).hexdigest()


def build_delta_packet(
    graph: Dict[str, Any], *, previous: Optional[Dict[str, Any]] = None,
    receipt: Optional[Dict[str, Any]] = None, mode: str = "review",
) -> Dict[str, Any]:
    errors = validate_graph(graph)
    if errors:
        raise AcceptanceGraphError("invalid Acceptance Graph: " + "; ".join(errors))
    if mode not in {"review", "revision"}:
        raise AcceptanceGraphError("mode must be review or revision")
    previous_items = {}
    if previous is not None:
        errors = validate_graph(previous)
        if errors:
            raise AcceptanceGraphError("invalid previous Acceptance Graph: " + "; ".join(errors))
        if previous["task_id"] != graph["task_id"]:
            raise AcceptanceGraphError("previous Acceptance Graph belongs to a different task")
        previous_items = {item["id"]: item for item in previous["acceptance_items"]}
    previously_accepted = set()
    retry_ids = set()
    if receipt is not None:
        if previous is None:
            raise AcceptanceGraphError("a prior Review Receipt requires --previous-graph")
        errors = validate_receipt(receipt, previous)
        if errors:
            raise AcceptanceGraphError("invalid prior Review Receipt: " + "; ".join(errors))
        previously_accepted.update(receipt["accepted"])
        retry_ids.update(receipt["conditional"])
        retry_ids.update(receipt["rejected"])

    selected = []
    omitted = []
    for item in graph["acceptance_items"]:
        prior = previous_items.get(item["id"])
        changed = prior is None or _item_fingerprint(prior) != _item_fingerprint(item)
        failing = item["graph_status"] != "supported"
        include = failing or changed or item["id"] not in previously_accepted or item["id"] in retry_ids
        if mode == "revision":
            include = failing or item["id"] in retry_ids
        if include:
            selected.append(deepcopy(item))
        else:
            omitted.append(item["id"])
    previous_diff = set(previous["new_diff_refs"]) if previous else set()
    previous_tests = set()
    if previous:
        previous_tests = {ref for item in previous["acceptance_items"] for ref in item["test_refs"] + item["result_refs"]}
    current_tests = {ref for item in graph["acceptance_items"] for ref in item["test_refs"] + item["result_refs"]}
    packet = {
        "schema_version": 1,
        "packet_id": "",
        "mode": mode,
        "state_id": graph["state_id"],
        "graph_id": graph["graph_id"],
        "acceptance_items": selected,
        "unsupported_acceptance": sorted(item["id"] for item in selected if item["graph_status"] == "unsupported"),
        "contradictory_evidence": sorted({ref for item in selected for ref in item["contradictory_refs"]}),
        "reopened_acceptance": sorted(item["id"] for item in selected if item["graph_status"] == "reopened"),
        "changed_decisions": sorted(_changed_decision_ids(graph, previous)),
        "new_diff_refs": sorted(set(graph["new_diff_refs"]) - previous_diff),
        "new_test_refs": sorted(current_tests - previous_tests),
        "omitted_unchanged_accepted": sorted(omitted),
    }
    packet["packet_id"] = hash_document(packet, "packet_id")
    return packet


def _changed_decision_ids(graph: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> set[str]:
    current = {item["id"]: item["decision_hash"] for item in graph["decisions"]}
    prior = {item["id"]: item["decision_hash"] for item in (previous or {}).get("decisions", [])}
    return {decision_id for decision_id in set(current) | set(prior) if current.get(decision_id) != prior.get(decision_id)}


PACKET_FIELDS = {
    "schema_version", "packet_id", "mode", "state_id", "graph_id",
    "acceptance_items", "unsupported_acceptance", "contradictory_evidence",
    "reopened_acceptance", "changed_decisions", "new_diff_refs", "new_test_refs",
    "omitted_unchanged_accepted",
}


def validate_delta_packet(packet: Any, graph: Dict[str, Any]) -> List[str]:
    if not isinstance(packet, dict) or set(packet) != PACKET_FIELDS:
        return ["delta review packet fields do not match schema"]
    errors = []
    if packet.get("schema_version") != 1 or packet.get("mode") not in {"review", "revision"}:
        errors.append("delta review packet version or mode is invalid")
    if packet.get("state_id") != graph["state_id"] or packet.get("graph_id") != graph["graph_id"]:
        errors.append("review packet does not bind the same state and graph")
    graph_items = {item["id"]: item for item in graph["acceptance_items"]}
    packet_items = packet.get("acceptance_items")
    if not isinstance(packet_items, list):
        errors.append("acceptance_items must be an array")
        packet_items = []
    for item in packet_items:
        if not isinstance(item, dict) or item.get("id") not in graph_items or item != graph_items.get(item.get("id")):
            errors.append("review packet contains an altered or unknown Acceptance subgraph")
            break
    packet_ids = [item.get("id") for item in packet_items if isinstance(item, dict)]
    if len(packet_ids) != len(set(packet_ids)):
        errors.append("review packet contains duplicate Acceptance subgraphs")
    for field in PACKET_FIELDS - {"schema_version", "packet_id", "mode", "state_id", "graph_id", "acceptance_items"}:
        if not _valid_strings(packet.get(field), hashes=field in {"contradictory_evidence", "new_diff_refs", "new_test_refs"}):
            errors.append(f"{field} is invalid")
    expected_unsupported = sorted(item["id"] for item in packet_items if isinstance(item, dict) and item.get("graph_status") == "unsupported")
    expected_contradictory = sorted({ref for item in packet_items if isinstance(item, dict) for ref in item.get("contradictory_refs", [])})
    expected_reopened = sorted(item["id"] for item in packet_items if isinstance(item, dict) and item.get("graph_status") == "reopened")
    if packet.get("unsupported_acceptance") != expected_unsupported:
        errors.append("unsupported_acceptance does not match packet subgraphs")
    if packet.get("contradictory_evidence") != expected_contradictory:
        errors.append("contradictory_evidence does not match packet subgraphs")
    if packet.get("reopened_acceptance") != expected_reopened:
        errors.append("reopened_acceptance does not match packet subgraphs")
    if set(packet.get("new_diff_refs", [])) - set(graph["new_diff_refs"]):
        errors.append("new_diff_refs contains refs absent from the graph")
    graph_test_refs = {ref for item in graph["acceptance_items"] for ref in item["test_refs"] + item["result_refs"]}
    if set(packet.get("new_test_refs", [])) - graph_test_refs:
        errors.append("new_test_refs contains refs absent from the graph")
    if set(packet.get("omitted_unchanged_accepted", [])) & set(packet_ids):
        errors.append("omitted and included Acceptance IDs must be disjoint")
    packet_id = packet.get("packet_id")
    if not isinstance(packet_id, str) or not HASH_RE.fullmatch(packet_id):
        errors.append("packet_id must be a sha256: digest")
    elif packet_id != hash_document(packet, "packet_id"):
        errors.append("packet_id does not match canonical packet content")
    return errors


RECEIPT_FIELDS = {
    "schema_version", "review_id", "bound_state_id", "bound_graph_id", "reviewer",
    "accepted", "conditional", "rejected", "frozen_decisions_confirmed", "new_questions",
}


def validate_receipt(receipt: Any, graph: Dict[str, Any], packet: Optional[Dict[str, Any]] = None) -> List[str]:
    errors = validate_graph(graph)
    if errors:
        return ["bound Acceptance Graph is invalid: " + "; ".join(errors)]
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        return ["Review Receipt fields do not match schema"]
    errors = []
    if receipt.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if receipt.get("bound_state_id") != graph["state_id"]:
        errors.append("Review Receipt does not bind the exact Workflow State hash")
    if receipt.get("bound_graph_id") != graph["graph_id"]:
        errors.append("Review Receipt does not bind the exact Acceptance Graph hash")
    if not isinstance(receipt.get("reviewer"), str) or not receipt["reviewer"]:
        errors.append("reviewer must be non-empty")
    for field in ("accepted", "conditional", "rejected", "frozen_decisions_confirmed", "new_questions"):
        if not _valid_strings(receipt.get(field)):
            errors.append(f"{field} must be a unique array of non-empty strings")
    classifications = [set(receipt.get(field, [])) for field in ("accepted", "conditional", "rejected")]
    if any(classifications[i] & classifications[j] for i in range(3) for j in range(i + 1, 3)):
        errors.append("accepted, conditional, and rejected must be disjoint")
    items = {item["id"]: item for item in graph["acceptance_items"]}
    classified = set().union(*classifications)
    if not classified.issubset(items):
        errors.append("Review Receipt references unknown Acceptance IDs")
    for acceptance_id in receipt.get("accepted", []):
        if acceptance_id in items and items[acceptance_id]["graph_status"] != "supported":
            errors.append(f"cannot accept non-supported Acceptance {acceptance_id}")
    if set(receipt.get("frozen_decisions_confirmed", [])) - set(graph["frozen_decisions"]):
        errors.append("Receipt confirms unknown or non-frozen decisions")
    if packet is not None:
        errors.extend(validate_delta_packet(packet, graph))
        packet_ids = {item.get("id") for item in packet.get("acceptance_items", [])}
        if classified != packet_ids:
            errors.append("Receipt must classify every and only Acceptance in the review packet")
    review_id = receipt.get("review_id")
    if not isinstance(review_id, str) or not HASH_RE.fullmatch(review_id):
        errors.append("review_id must be a sha256: digest")
    elif review_id != hash_document(receipt, "review_id"):
        errors.append("review_id does not match canonical receipt content")
    return errors
