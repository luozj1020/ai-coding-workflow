#!/usr/bin/env python3
"""Deterministic Phase 2 handoff delta and short-ACK primitives."""
from __future__ import annotations

import fnmatch
import hashlib
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Dict, List

from workflow_state import (
    STATE_ID_RE, WorkflowStateError, canonical_json, is_safe_repo_path_pattern,
    validate_event, validate_state,
)


SCHEMA_VERSION = 1
MAX_ACK_BYTES = 8192
MAX_REPAIR_ATTEMPTS = 1
HANDOFF_DELTA_FILE = "HANDOFF_DELTA.json"
HANDOFF_ACK_FILE = "HANDOFF_ACK.json"
HANDOFF_ACK_REPAIR_FILE = "HANDOFF_ACK_REPAIR.json"


class HandoffProtocolError(WorkflowStateError):
    """A delta, ACK, or repair transition is invalid."""


def _by_id(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {item["id"]: item for item in items}


def delta_id_for(delta: Dict[str, Any]) -> str:
    material = deepcopy(delta)
    material.pop("delta_id", None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def validate_ancestry(base: Dict[str, Any], target: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    if not events:
        raise HandoffProtocolError("workflow event log is empty")
    for index, event in enumerate(events, 1):
        errors = validate_event(event)
        if errors:
            raise HandoffProtocolError(f"invalid workflow event {index}: " + "; ".join(errors))
    base_index = next(
        (index for index, event in enumerate(events) if event.get("new_state_id") == base["state_id"]),
        None,
    )
    if base_index is None:
        raise HandoffProtocolError("base state is absent from workflow event log")
    current_id = base["state_id"]
    traversed = 0
    if current_id == target["state_id"]:
        raise HandoffProtocolError("target state must be newer than base state")
    for event in events[base_index + 1:]:
        if event.get("base_state_id") != current_id:
            raise HandoffProtocolError("workflow event ancestry is discontinuous")
        current_id = event["new_state_id"]
        traversed += 1
        if current_id == target["state_id"]:
            if target["revision"] - base["revision"] != traversed:
                raise HandoffProtocolError("state revisions do not match workflow event ancestry")
            return
    raise HandoffProtocolError("target state is not a descendant of base state")


def build_delta(
    base: Dict[str, Any], target: Dict[str, Any], events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    base_errors = validate_state(base)
    target_errors = validate_state(target)
    if base_errors or target_errors:
        raise HandoffProtocolError("invalid state input: " + "; ".join(base_errors + target_errors))
    if base["task_id"] != target["task_id"]:
        raise HandoffProtocolError("base and target task_id differ")
    if target["revision"] <= base["revision"]:
        raise HandoffProtocolError("target revision must be newer than base revision")
    validate_ancestry(base, target, events)

    base_constraints = _by_id(base["constraints"])
    target_constraints = _by_id(target["constraints"])
    removed_constraints = sorted(set(base_constraints) - set(target_constraints))
    changed_constraints = sorted(
        item_id for item_id in set(base_constraints) & set(target_constraints)
        if base_constraints[item_id] != target_constraints[item_id]
    )
    if removed_constraints or changed_constraints:
        raise HandoffProtocolError("frozen constraints cannot be removed or changed")

    base_decisions = _by_id(base["accepted_decisions"])
    target_decisions = _by_id(target["accepted_decisions"])
    added_decisions = [
        deepcopy(target_decisions[item_id])
        for item_id in sorted(set(target_decisions) - set(base_decisions))
    ]
    updated_decisions = [
        deepcopy(target_decisions[item_id])
        for item_id in sorted(set(base_decisions) & set(target_decisions))
        if base_decisions[item_id] != target_decisions[item_id]
    ]
    invalidated_decisions = sorted(set(base_decisions) - set(target_decisions))

    base_rejected = _by_id(base["rejected_hypotheses"])
    target_rejected = _by_id(target["rejected_hypotheses"])
    reopened_hypotheses = sorted(set(base_rejected) - set(target_rejected))
    changed_rejected = sorted(
        item_id for item_id in set(base_rejected) & set(target_rejected)
        if base_rejected[item_id] != target_rejected[item_id]
    )
    if changed_rejected:
        raise HandoffProtocolError("rejected hypotheses cannot be silently changed")

    base_questions = _by_id(base["open_questions"])
    target_questions = _by_id(target["open_questions"])
    changed_questions = sorted(
        item_id for item_id in set(base_questions) & set(target_questions)
        if base_questions[item_id] != target_questions[item_id]
    )
    if changed_questions:
        raise HandoffProtocolError("open questions cannot be silently rewritten")

    acceptance_changes = {
        item_id: deepcopy(target["acceptance_status"][item_id])
        for item_id in sorted(target["acceptance_status"])
        if base["acceptance_status"].get(item_id) != target["acceptance_status"][item_id]
    }
    removed_acceptance = sorted(set(base["acceptance_status"]) - set(target["acceptance_status"]))
    if removed_acceptance:
        raise HandoffProtocolError("acceptance criteria cannot be removed")

    delta = {
        "schema_version": SCHEMA_VERSION,
        "delta_id": "",
        "task_id": target["task_id"],
        "base_state_id": base["state_id"],
        "new_state_id": target["state_id"],
        "target_revision": target["revision"],
        "goal": deepcopy(target["goal"]) if base["goal"] != target["goal"] else None,
        "phase": target["phase"] if base["phase"] != target["phase"] else None,
        "repository_state_hash": (
            target["repository_state_hash"]
            if base["repository_state_hash"] != target["repository_state_hash"] else None
        ),
        "added_constraints": [
            deepcopy(target_constraints[item_id])
            for item_id in sorted(set(target_constraints) - set(base_constraints))
        ],
        "added_decisions": added_decisions,
        "updated_decisions": updated_decisions,
        "invalidated_decisions": invalidated_decisions,
        "added_evidence": sorted(set(target["evidence_refs"]) - set(base["evidence_refs"])),
        "removed_evidence": sorted(set(base["evidence_refs"]) - set(target["evidence_refs"])),
        "rejected_hypotheses": [
            deepcopy(target_rejected[item_id])
            for item_id in sorted(set(target_rejected) - set(base_rejected))
        ],
        "reopened_hypotheses": reopened_hypotheses,
        "resolved_questions": sorted(set(base_questions) - set(target_questions)),
        "new_open_questions": [
            deepcopy(target_questions[item_id])
            for item_id in sorted(set(target_questions) - set(base_questions))
        ],
        "changed_acceptance": acceptance_changes,
        "next_action": deepcopy(target["next_action"]),
    }
    delta["delta_id"] = delta_id_for(delta)
    return delta


def validate_delta(delta: Any) -> List[str]:
    if not isinstance(delta, dict):
        return ["delta must be an object"]
    required = {
        "schema_version", "delta_id", "task_id", "base_state_id", "new_state_id",
        "target_revision", "goal", "phase", "repository_state_hash",
        "added_constraints", "added_decisions", "updated_decisions",
        "invalidated_decisions", "added_evidence", "removed_evidence",
        "rejected_hypotheses", "reopened_hypotheses", "resolved_questions", "new_open_questions",
        "changed_acceptance", "next_action",
    }
    errors = []
    if set(delta) != required:
        missing = sorted(required - set(delta))
        unknown = sorted(set(delta) - required)
        if missing:
            errors.append("missing delta fields: " + ", ".join(missing))
        if unknown:
            errors.append("unknown delta fields: " + ", ".join(unknown))
        return errors
    if delta.get("schema_version") != SCHEMA_VERSION:
        errors.append("delta schema_version must be 1")
    for field in ("delta_id", "base_state_id", "new_state_id"):
        value = delta.get(field)
        if not isinstance(value, str) or not STATE_ID_RE.fullmatch(value):
            errors.append(f"{field} must be a sha256: digest")
    if isinstance(delta.get("delta_id"), str) and delta["delta_id"] != delta_id_for(delta):
        errors.append("delta_id does not match canonical delta content")
    if not isinstance(delta.get("task_id"), str) or not delta["task_id"]:
        errors.append("task_id must be a non-empty string")
    if not isinstance(delta.get("target_revision"), int) or isinstance(delta["target_revision"], bool) or delta["target_revision"] < 1:
        errors.append("target_revision must be a positive integer")
    for field in (
        "added_constraints", "added_decisions", "updated_decisions",
        "invalidated_decisions", "added_evidence", "removed_evidence",
        "rejected_hypotheses", "reopened_hypotheses", "resolved_questions", "new_open_questions",
    ):
        if not isinstance(delta.get(field), list):
            errors.append(f"{field} must be an array")
    if not isinstance(delta.get("changed_acceptance"), dict):
        errors.append("changed_acceptance must be an object")
    if not isinstance(delta.get("next_action"), dict):
        errors.append("next_action must be an object")
    return errors


def ack_size(ack: Dict[str, Any]) -> int:
    return len(canonical_json(ack).encode("utf-8"))


def validate_ack_shape(ack: Any, *, max_bytes: int = MAX_ACK_BYTES) -> List[str]:
    if not isinstance(ack, dict):
        return ["ACK must be an object"]
    required = {
        "schema_version", "state_id", "receiver", "repair_attempt",
        "understood_goal_id", "accepted_constraints", "accepted_decisions",
        "open_questions", "planned_first_action", "additional_context_requested",
        "contradictions",
    }
    errors = []
    if set(ack) != required:
        missing = sorted(required - set(ack))
        unknown = sorted(set(ack) - required)
        if missing:
            errors.append("missing ACK fields: " + ", ".join(missing))
        if unknown:
            errors.append("unknown ACK fields: " + ", ".join(unknown))
        return errors
    if ack_size(ack) > max_bytes:
        errors.append(f"ACK exceeds {max_bytes} byte limit")
    if ack.get("schema_version") != SCHEMA_VERSION:
        errors.append("ACK schema_version must be 1")
    if not isinstance(ack.get("state_id"), str) or not STATE_ID_RE.fullmatch(ack["state_id"]):
        errors.append("state_id must be a sha256: digest")
    for field in ("receiver", "understood_goal_id"):
        value = ack.get(field)
        if not isinstance(value, str) or not value or len(value) > 256:
            errors.append(f"{field} must be a non-empty string of at most 256 characters")
    attempt = ack.get("repair_attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or not 0 <= attempt <= MAX_REPAIR_ATTEMPTS:
        errors.append(f"repair_attempt must be between 0 and {MAX_REPAIR_ATTEMPTS}")
    limits = {
        "accepted_constraints": 128,
        "accepted_decisions": 128,
        "open_questions": 64,
        "additional_context_requested": 16,
        "contradictions": 8,
    }
    for field, maximum in limits.items():
        value = ack.get(field)
        if not isinstance(value, list):
            errors.append(f"{field} must be an array")
            continue
        if len(value) > maximum:
            errors.append(f"{field} exceeds {maximum} item limit")
        if not all(isinstance(item, str) and 0 < len(item) <= 512 for item in value):
            errors.append(f"{field} items must be non-empty strings of at most 512 characters")
        if len(value) != len(set(value)):
            errors.append(f"{field} must not contain duplicates")
    action = ack.get("planned_first_action")
    if not isinstance(action, dict):
        errors.append("planned_first_action must be an object")
    else:
        if set(action) - {"operation", "target", "write_paths"} or not {"operation", "target"}.issubset(action):
            errors.append("planned_first_action must contain operation and target, with optional write_paths")
        for field in ("operation", "target"):
            value = action.get(field)
            if not isinstance(value, str) or not value or len(value) > 256:
                errors.append(f"planned_first_action.{field} must be 1-256 characters")
        paths = action.get("write_paths", [])
        if not isinstance(paths, list) or len(paths) > 32 or not all(
            isinstance(path, str) and 0 < len(path) <= 256 for path in paths
        ):
            errors.append("planned_first_action.write_paths must contain at most 32 short paths")
    return errors


def _path_allowed(path: str, allowed_paths: List[str]) -> bool:
    if not is_safe_repo_path_pattern(path):
        return False
    normalized = str(PurePosixPath(path.replace("\\", "/")))
    if normalized.startswith("../") or normalized.startswith("/"):
        return False
    for allowed in allowed_paths:
        if not is_safe_repo_path_pattern(allowed):
            continue
        candidate = allowed.replace("\\", "/")
        if fnmatch.fnmatchcase(normalized, candidate):
            return True
        if candidate.endswith("/") and normalized.startswith(candidate):
            return True
        if normalized == candidate:
            return True
    return False


def _looks_like_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return "/" in normalized or normalized.startswith(".") or bool(PurePosixPath(normalized).suffix)


def evaluate_ack(
    ack: Dict[str, Any], base_state: Dict[str, Any], state: Dict[str, Any], delta: Dict[str, Any],
    events: List[Dict[str, Any]], receiver_state_id: str, *, max_bytes: int = MAX_ACK_BYTES,
) -> Dict[str, Any]:
    errors = (
        validate_state(base_state) + validate_state(state) + validate_delta(delta)
        + validate_ack_shape(ack, max_bytes=max_bytes)
    )
    if errors:
        return {"status": "invalid", "execute_allowed": False, "errors": errors}
    binding_errors = []
    if receiver_state_id != base_state["state_id"]:
        binding_errors.append("receiver state ID does not match supplied base state")
    if receiver_state_id != delta["base_state_id"]:
        binding_errors.append("receiver state does not match delta base_state_id")
    if delta["new_state_id"] != state["state_id"]:
        binding_errors.append("delta new_state_id does not match target state")
    if delta["task_id"] != state["task_id"]:
        binding_errors.append("delta task_id does not match target state")
    if ack["state_id"] != state["state_id"]:
        binding_errors.append("ACK state_id does not match target state")
    try:
        expected_delta = build_delta(base_state, state, events)
    except HandoffProtocolError as exc:
        binding_errors.append(f"cannot derive expected delta: {exc}")
    else:
        if expected_delta != delta:
            binding_errors.append("delta content does not match base and target states")
    if binding_errors:
        return {"status": "state-mismatch", "execute_allowed": False, "errors": binding_errors}

    expected_constraints = {
        item["id"]: item for item in state["constraints"] if item["frozen"]
    }
    expected_decisions = {item["id"]: item for item in state["accepted_decisions"]}
    expected_questions = {item["id"]: item for item in state["open_questions"]}
    accepted_constraints = set(ack["accepted_constraints"])
    accepted_decisions = set(ack["accepted_decisions"])
    acknowledged_questions = set(ack["open_questions"])
    unexpected = {
        "constraints": sorted(accepted_constraints - set(expected_constraints)),
        "decisions": sorted(accepted_decisions - set(expected_decisions)),
        "open_questions": sorted(acknowledged_questions - set(expected_questions)),
    }
    if any(unexpected.values()):
        return {
            "status": "invalid", "execute_allowed": False,
            "errors": ["ACK contains IDs absent from target state"], "unexpected": unexpected,
        }
    missing = {
        "constraints": sorted(set(expected_constraints) - accepted_constraints),
        "decisions": sorted(set(expected_decisions) - accepted_decisions),
        "open_questions": sorted(set(expected_questions) - acknowledged_questions),
    }
    goal_mismatch = ack["understood_goal_id"] != state["goal"]["id"]
    action_paths = list(ack["planned_first_action"].get("write_paths", []))
    target = ack["planned_first_action"]["target"]
    if _looks_like_path(target) and target not in action_paths:
        action_paths.append(target)
    forbidden_paths = [
        path for path in action_paths
        if not _path_allowed(path, state["next_action"]["allowed_paths"])
    ]
    issues = []
    if goal_mismatch:
        issues.append("goal-id-mismatch")
    if any(missing.values()):
        issues.append("missing-state-acknowledgements")
    if forbidden_paths:
        issues.append("planned-write-outside-allowed-paths")
    if ack["contradictions"]:
        issues.append("contradictions-reported")
    if ack["additional_context_requested"]:
        issues.append("additional-context-requested")
    if issues:
        can_repair = ack["repair_attempt"] < MAX_REPAIR_ATTEMPTS
        return {
            "status": "repair-required" if can_repair else "blocked",
            "execute_allowed": False,
            "repair_allowed": can_repair,
            "issues": issues,
            "missing": missing,
            "forbidden_paths": forbidden_paths,
            "contradictions": ack["contradictions"],
            "additional_context_requested": ack["additional_context_requested"],
        }
    return {
        "status": "accepted", "execute_allowed": True, "repair_allowed": False,
        "state_id": state["state_id"], "receiver": ack["receiver"],
    }


def build_repair_packet(result: Dict[str, Any], ack: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("status") != "repair-required":
        raise HandoffProtocolError("repair packet requires repair-required result")
    constraints = _by_id(state["constraints"])
    decisions = _by_id(state["accepted_decisions"])
    questions = _by_id(state["open_questions"])
    missing = result["missing"]
    return {
        "schema_version": SCHEMA_VERSION,
        "state_id": state["state_id"],
        "receiver": ack["receiver"],
        "repair_attempt": ack["repair_attempt"] + 1,
        "max_repair_attempts": MAX_REPAIR_ATTEMPTS,
        "missing_constraints": [deepcopy(constraints[item_id]) for item_id in missing["constraints"]],
        "missing_decisions": [deepcopy(decisions[item_id]) for item_id in missing["decisions"]],
        "missing_open_questions": [deepcopy(questions[item_id]) for item_id in missing["open_questions"]],
        "expected_goal": deepcopy(state["goal"]) if "goal-id-mismatch" in result["issues"] else None,
        "forbidden_paths": result["forbidden_paths"],
        "allowed_paths": (
            list(state["next_action"]["allowed_paths"])
            if result["forbidden_paths"] else []
        ),
        "contradictions": result["contradictions"],
        "additional_context_requested": result["additional_context_requested"],
        "instruction": "Return one corrected short ACK for this exact state; do not restate the task.",
    }


def merge_acks(base: Dict[str, Any], repair: Dict[str, Any], *, max_bytes: int = MAX_ACK_BYTES) -> Dict[str, Any]:
    errors = validate_ack_shape(base, max_bytes=max_bytes) + validate_ack_shape(repair, max_bytes=max_bytes)
    if errors:
        raise HandoffProtocolError("invalid ACK input: " + "; ".join(errors))
    if base["state_id"] != repair["state_id"] or base["receiver"] != repair["receiver"]:
        raise HandoffProtocolError("base and repair ACK must bind the same state and receiver")
    if base["repair_attempt"] != 0 or repair["repair_attempt"] != 1:
        raise HandoffProtocolError("ACK merge requires repair_attempt 0 followed by 1")
    merged = deepcopy(repair)
    for field in ("accepted_constraints", "accepted_decisions", "open_questions"):
        merged[field] = sorted(set(base[field]) | set(repair[field]))
    # The follow-up ACK explicitly reports what remains unresolved after the
    # resend; carrying resolved requests/contradictions forward would make the
    # single bounded repair impossible to close.
    merged["additional_context_requested"] = list(repair["additional_context_requested"])
    merged["contradictions"] = list(repair["contradictions"])
    shape_errors = validate_ack_shape(merged, max_bytes=max_bytes)
    if shape_errors:
        raise HandoffProtocolError("merged ACK is invalid: " + "; ".join(shape_errors))
    return merged
