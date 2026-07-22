#!/usr/bin/env python3
"""Deterministic Workflow State IR primitives.

This module is intentionally stdlib-only so the same state transition rules are
used by the initializer, delta applier, validator, renderer, and installed copy.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - Unix
    msvcrt = None

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


SCHEMA_VERSION = 1
STATE_FILE = "WORKFLOW_STATE.json"
EVENTS_FILE = "WORKFLOW_EVENTS.jsonl"
MAX_JSON_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_EVENT_LOG_BYTES = 64 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 4 * 1024 * 1024
STATE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VALID_PHASES = (
    "planning", "implementation", "verification", "review", "revision", "complete",
)
VALID_ACCEPTANCE_STATUS = ("pending", "satisfied", "failed", "blocked", "unknown")
VALID_DECISION_STATUS = ("accepted", "frozen")
PHASE_TRANSITIONS = {
    "planning": {"implementation", "complete"},
    "implementation": {"verification", "revision"},
    "verification": {"review", "revision"},
    "review": {"revision", "complete"},
    "revision": {"implementation", "verification"},
    "complete": set(),
}
VALID_EVENT_TYPES = (
    "state-initialized", "goal-updated", "constraint-added",
    "decision-accepted", "decision-frozen", "decision-invalidated",
    "hypothesis-rejected", "hypothesis-reopened", "question-opened", "question-resolved",
    "evidence-added", "acceptance-updated", "next-action-updated",
    "repository-state-updated", "phase-changed",
)


class WorkflowStateError(ValueError):
    """A state, event, or transition is invalid."""


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize one state read/transition/event-write critical section."""
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and lock.is_symlink():
        raise WorkflowStateError("state lock path must not be a symlink")
    with lock.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_id_for(state: Dict[str, Any]) -> str:
    material = deepcopy(state)
    material.pop("state_id", None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def event_id_for(event: Dict[str, Any]) -> str:
    material = deepcopy(event)
    material.pop("event_id", None)
    material.pop("timestamp", None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _nonempty(value: Any, path: str, errors: List[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")


def _unique_ids(items: Any, path: str, errors: List[str]) -> None:
    if not isinstance(items, list):
        errors.append(f"{path} must be an array")
        return
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{path}[{index}] must be an object")
            continue
        item_id = item.get("id")
        _nonempty(item_id, f"{path}[{index}].id", errors)
        if isinstance(item_id, str):
            if item_id in seen:
                errors.append(f"{path} contains duplicate id {item_id!r}")
            seen.add(item_id)


def _exact_fields(item: Dict[str, Any], expected: set[str], path: str, errors: List[str]) -> None:
    if set(item) != expected:
        errors.append(f"{path} must contain exactly {', '.join(sorted(expected))}")


def _string_list(value: Any, path: str, errors: List[str], *, require_item: bool = False) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{path} must be an array of non-empty strings")
    elif require_item and not value:
        errors.append(f"{path} must not be empty")
    elif len(value) != len(set(value)):
        errors.append(f"{path} must not contain duplicates")


def _evidence_ref_list(value: Any, path: str, errors: List[str], *, require_item: bool = False) -> None:
    before = len(errors)
    _string_list(value, path, errors, require_item=require_item)
    if len(errors) != before or not isinstance(value, list):
        return
    for index, ref in enumerate(value):
        if not STATE_ID_RE.fullmatch(ref):
            errors.append(f"{path}[{index}] must be a sha256: digest")


def _mutation_evidence_refs(
    state: Dict[str, Any], value: Any, *, require_item: bool = False,
) -> List[str]:
    errors: List[str] = []
    _evidence_ref_list(value, "evidence_refs", errors, require_item=require_item)
    if errors:
        raise WorkflowStateError("; ".join(errors))
    refs = list(value)
    missing = sorted(set(refs) - set(state.get("evidence_refs", [])))
    if missing:
        raise WorkflowStateError(
            "evidence_refs are not registered in state.evidence_refs: " + ", ".join(missing)
        )
    return refs


def is_safe_repo_path_pattern(value: Any) -> bool:
    """Return whether a path/glob is repository-relative on Unix and Windows."""
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return False
    return ".." not in normalized.split("/")


def validate_state(state: Any, *, verify_hash: bool = True) -> List[str]:
    errors: List[str] = []
    if not isinstance(state, dict):
        return ["state must be an object"]
    required = {
        "schema_version", "state_id", "parent_state_id", "revision", "task_id",
        "phase", "repository_state_hash", "goal", "constraints",
        "accepted_decisions", "rejected_hypotheses", "open_questions",
        "evidence_refs", "acceptance_status", "next_action",
    }
    unknown = sorted(set(state) - required)
    missing = sorted(required - set(state))
    if unknown:
        errors.append("unknown state fields: " + ", ".join(unknown))
    if missing:
        errors.append("missing state fields: " + ", ".join(missing))
        return errors
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    state_id = state.get("state_id")
    if not isinstance(state_id, str) or not STATE_ID_RE.fullmatch(state_id):
        errors.append("state_id must be a sha256: digest")
    parent_id = state.get("parent_state_id")
    if parent_id is not None and (not isinstance(parent_id, str) or not STATE_ID_RE.fullmatch(parent_id)):
        errors.append("parent_state_id must be null or a sha256: digest")
    revision = state.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        errors.append("revision must be a non-negative integer")
    _nonempty(state.get("task_id"), "task_id", errors)
    if state.get("phase") not in VALID_PHASES:
        errors.append("phase must be one of " + ", ".join(VALID_PHASES))
    repository_hash = state.get("repository_state_hash")
    if not isinstance(repository_hash, str) or not STATE_ID_RE.fullmatch(repository_hash):
        errors.append("repository_state_hash must be a sha256: digest")
    goal = state.get("goal")
    if not isinstance(goal, dict) or set(goal) != {"id", "statement", "acceptance_ids"}:
        errors.append("goal must contain exactly id, statement, and acceptance_ids")
    else:
        _nonempty(goal.get("id"), "goal.id", errors)
        _nonempty(goal.get("statement"), "goal.statement", errors)
        _string_list(goal.get("acceptance_ids"), "goal.acceptance_ids", errors)
    for name in ("constraints", "accepted_decisions", "rejected_hypotheses", "open_questions"):
        _unique_ids(state.get(name), name, errors)
    constraints = state.get("constraints")
    if isinstance(constraints, list):
        for index, item in enumerate(constraints):
            if not isinstance(item, dict):
                continue
            path = f"constraints[{index}]"
            _exact_fields(item, {"id", "statement", "source", "frozen"}, path, errors)
            _nonempty(item.get("statement"), path + ".statement", errors)
            _nonempty(item.get("source"), path + ".source", errors)
            if not isinstance(item.get("frozen"), bool):
                errors.append(path + ".frozen must be a boolean")
    decisions = state.get("accepted_decisions")
    if isinstance(decisions, list):
        for index, item in enumerate(decisions):
            if not isinstance(item, dict):
                continue
            path = f"accepted_decisions[{index}]"
            _exact_fields(item, {"id", "statement", "status", "evidence_refs"}, path, errors)
            _nonempty(item.get("statement"), path + ".statement", errors)
            if item.get("status") not in VALID_DECISION_STATUS:
                errors.append(path + ".status must be accepted or frozen")
            _evidence_ref_list(item.get("evidence_refs"), path + ".evidence_refs", errors)
    rejected = state.get("rejected_hypotheses")
    if isinstance(rejected, list):
        for index, item in enumerate(rejected):
            if not isinstance(item, dict):
                continue
            path = f"rejected_hypotheses[{index}]"
            _exact_fields(item, {"id", "statement", "reason", "evidence_refs"}, path, errors)
            _nonempty(item.get("statement"), path + ".statement", errors)
            _nonempty(item.get("reason"), path + ".reason", errors)
            _evidence_ref_list(item.get("evidence_refs"), path + ".evidence_refs", errors, require_item=True)
    questions = state.get("open_questions")
    if isinstance(questions, list):
        for index, item in enumerate(questions):
            if not isinstance(item, dict):
                continue
            path = f"open_questions[{index}]"
            _exact_fields(item, {"id", "question"}, path, errors)
            _nonempty(item.get("question"), path + ".question", errors)
    refs = state.get("evidence_refs")
    _evidence_ref_list(refs, "evidence_refs", errors)
    acceptance = state.get("acceptance_status")
    if not isinstance(acceptance, dict):
        errors.append("acceptance_status must be an object")
    else:
        for acceptance_id, value in acceptance.items():
            if not isinstance(acceptance_id, str) or not acceptance_id:
                errors.append("acceptance_status keys must be non-empty strings")
            if not isinstance(value, dict) or set(value) != {"description", "status", "evidence_refs"}:
                errors.append(f"acceptance_status.{acceptance_id} has invalid shape")
            elif value.get("status") not in VALID_ACCEPTANCE_STATUS:
                errors.append(f"acceptance_status.{acceptance_id}.status is invalid")
            else:
                _nonempty(value.get("description"), f"acceptance_status.{acceptance_id}.description", errors)
                _evidence_ref_list(value.get("evidence_refs"), f"acceptance_status.{acceptance_id}.evidence_refs", errors)
    if isinstance(refs, list):
        registered = set(refs)
        nested = []
        if isinstance(decisions, list):
            nested.extend(
                (f"accepted_decisions[{index}].evidence_refs", item.get("evidence_refs"))
                for index, item in enumerate(decisions) if isinstance(item, dict)
            )
        if isinstance(rejected, list):
            nested.extend(
                (f"rejected_hypotheses[{index}].evidence_refs", item.get("evidence_refs"))
                for index, item in enumerate(rejected) if isinstance(item, dict)
            )
        if isinstance(acceptance, dict):
            nested.extend(
                (f"acceptance_status.{item_id}.evidence_refs", item.get("evidence_refs"))
                for item_id, item in acceptance.items() if isinstance(item, dict)
            )
        for path, nested_refs in nested:
            if isinstance(nested_refs, list):
                missing_refs = sorted(set(nested_refs) - registered)
                if missing_refs:
                    errors.append(
                        f"{path} contains refs absent from state.evidence_refs: "
                        + ", ".join(missing_refs)
                    )
    action = state.get("next_action")
    if not isinstance(action, dict) or set(action) != {"owner", "operation", "allowed_paths"}:
        errors.append("next_action must contain exactly owner, operation, and allowed_paths")
    else:
        _nonempty(action.get("owner"), "next_action.owner", errors)
        _nonempty(action.get("operation"), "next_action.operation", errors)
        _string_list(action.get("allowed_paths"), "next_action.allowed_paths", errors)
        allowed_paths = action.get("allowed_paths")
        if isinstance(allowed_paths, list) and any(
            not is_safe_repo_path_pattern(path) for path in allowed_paths
        ):
            errors.append("next_action.allowed_paths must be repository-relative and traversal-free")
    if verify_hash and isinstance(state_id, str) and state_id != state_id_for(state):
        errors.append("state_id does not match canonical state content")
    return errors


def validate_event(event: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(event, dict):
        return ["event must be an object"]
    required = {
        "schema_version", "event_id", "timestamp", "actor", "event_type",
        "base_state_id", "new_state_id", "payload",
    }
    if set(event) != required:
        missing = sorted(required - set(event))
        unknown = sorted(set(event) - required)
        if missing:
            errors.append("missing event fields: " + ", ".join(missing))
        if unknown:
            errors.append("unknown event fields: " + ", ".join(unknown))
        return errors
    if event.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if event.get("event_type") not in VALID_EVENT_TYPES:
        errors.append("unsupported event_type")
    _nonempty(event.get("actor"), "actor", errors)
    _nonempty(event.get("timestamp"), "timestamp", errors)
    for name in ("event_id", "new_state_id"):
        value = event.get(name)
        if not isinstance(value, str) or not STATE_ID_RE.fullmatch(value):
            errors.append(f"{name} must be a sha256: digest")
    base = event.get("base_state_id")
    if base is not None and (not isinstance(base, str) or not STATE_ID_RE.fullmatch(base)):
        errors.append("base_state_id must be null or a sha256: digest")
    if not isinstance(event.get("payload"), dict):
        errors.append("payload must be an object")
    if isinstance(event.get("event_id"), str) and event["event_id"] != event_id_for(event):
        errors.append("event_id does not match canonical event content")
    return errors


def _require_payload(payload: Dict[str, Any], fields: Iterable[str]) -> None:
    missing = [field for field in fields if field not in payload]
    if missing:
        raise WorkflowStateError("payload missing: " + ", ".join(missing))


def _find(items: List[Dict[str, Any]], item_id: str) -> Tuple[int, Optional[Dict[str, Any]]]:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            return index, item
    return -1, None


def apply_mutation(state: Dict[str, Any], event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one validated semantic mutation without assigning hashes."""
    if event_type not in VALID_EVENT_TYPES or event_type == "state-initialized":
        raise WorkflowStateError(f"unsupported mutation event_type: {event_type}")
    if not isinstance(payload, dict):
        raise WorkflowStateError("payload must be an object")
    result = deepcopy(state)
    if event_type == "goal-updated":
        _require_payload(payload, ("statement",))
        _nonempty_or_raise(payload["statement"], "statement")
        result["goal"]["statement"] = payload["statement"]
    elif event_type == "constraint-added":
        _require_payload(payload, ("id", "statement", "source"))
        for field in ("id", "statement", "source"):
            _nonempty_or_raise(payload[field], field)
        item = {"id": payload["id"], "statement": payload["statement"], "source": payload["source"], "frozen": bool(payload.get("frozen", True))}
        if _find(result["constraints"], item["id"])[1] is not None:
            raise WorkflowStateError(f"constraint {item['id']} already exists")
        result["constraints"].append(item)
    elif event_type == "decision-accepted":
        _require_payload(payload, ("id", "statement"))
        _nonempty_or_raise(payload["id"], "id")
        _nonempty_or_raise(payload["statement"], "statement")
        refs = _mutation_evidence_refs(result, payload.get("evidence_refs", []))
        index, existing = _find(result["accepted_decisions"], payload["id"])
        if existing is not None:
            if existing.get("status") == "frozen" and existing.get("statement") != payload["statement"]:
                raise WorkflowStateError(f"frozen decision {payload['id']} cannot be overwritten")
            raise WorkflowStateError(f"decision {payload['id']} already exists")
        result["accepted_decisions"].append({"id": payload["id"], "statement": payload["statement"], "status": "accepted", "evidence_refs": list(refs)})
    elif event_type == "decision-frozen":
        _require_payload(payload, ("id",))
        index, existing = _find(result["accepted_decisions"], payload["id"])
        if existing is None:
            raise WorkflowStateError(f"decision {payload['id']} does not exist")
        if existing.get("status") != "accepted":
            raise WorkflowStateError(f"decision {payload['id']} is not accepted")
        result["accepted_decisions"][index]["status"] = "frozen"
    elif event_type == "decision-invalidated":
        _require_payload(payload, ("id", "reason", "evidence_refs"))
        index, existing = _find(result["accepted_decisions"], payload["id"])
        if existing is None:
            raise WorkflowStateError(f"decision {payload['id']} does not exist")
        _nonempty_or_raise(payload["reason"], "reason")
        _mutation_evidence_refs(result, payload["evidence_refs"], require_item=True)
        del result["accepted_decisions"][index]
    elif event_type == "hypothesis-rejected":
        _require_payload(payload, ("id", "statement", "reason", "evidence_refs"))
        for field in ("id", "statement", "reason"):
            _nonempty_or_raise(payload[field], field)
        refs = _mutation_evidence_refs(result, payload["evidence_refs"], require_item=True)
        if _find(result["rejected_hypotheses"], payload["id"])[1] is not None:
            raise WorkflowStateError(f"hypothesis {payload['id']} already rejected")
        result["rejected_hypotheses"].append({
            "id": payload["id"], "statement": payload["statement"],
            "reason": payload["reason"], "evidence_refs": refs,
        })
    elif event_type == "hypothesis-reopened":
        _require_payload(payload, ("id", "reason", "evidence_refs"))
        index, existing = _find(result["rejected_hypotheses"], payload["id"])
        if existing is None:
            raise WorkflowStateError(f"hypothesis {payload['id']} is not rejected")
        _nonempty_or_raise(payload["reason"], "reason")
        _mutation_evidence_refs(result, payload["evidence_refs"], require_item=True)
        del result["rejected_hypotheses"][index]
    elif event_type == "question-opened":
        _require_payload(payload, ("id", "question"))
        _nonempty_or_raise(payload["id"], "id")
        _nonempty_or_raise(payload["question"], "question")
        if _find(result["open_questions"], payload["id"])[1] is not None:
            raise WorkflowStateError(f"question {payload['id']} already open")
        result["open_questions"].append({"id": payload["id"], "question": payload["question"]})
    elif event_type == "question-resolved":
        _require_payload(payload, ("id", "resolution"))
        _nonempty_or_raise(payload["resolution"], "resolution")
        index, existing = _find(result["open_questions"], payload["id"])
        if existing is None:
            raise WorkflowStateError(f"question {payload['id']} is not open")
        del result["open_questions"][index]
    elif event_type == "evidence-added":
        _require_payload(payload, ("ref",))
        if not isinstance(payload["ref"], str) or not STATE_ID_RE.fullmatch(payload["ref"]):
            raise WorkflowStateError("ref must be a sha256: digest")
        if payload["ref"] in result["evidence_refs"]:
            raise WorkflowStateError(f"evidence ref {payload['ref']} already exists")
        result["evidence_refs"].append(payload["ref"])
    elif event_type == "acceptance-updated":
        _require_payload(payload, ("id", "status", "evidence_refs"))
        if payload["id"] not in result["acceptance_status"]:
            raise WorkflowStateError(f"acceptance {payload['id']} does not exist")
        if payload["status"] not in VALID_ACCEPTANCE_STATUS:
            raise WorkflowStateError(f"invalid acceptance status: {payload['status']}")
        refs = _mutation_evidence_refs(result, payload["evidence_refs"])
        result["acceptance_status"][payload["id"]]["status"] = payload["status"]
        result["acceptance_status"][payload["id"]]["evidence_refs"] = refs
    elif event_type == "next-action-updated":
        _require_payload(payload, ("owner", "operation", "allowed_paths"))
        _nonempty_or_raise(payload["owner"], "owner")
        _nonempty_or_raise(payload["operation"], "operation")
        if not isinstance(payload["allowed_paths"], list) or not all(isinstance(path, str) and path for path in payload["allowed_paths"]):
            raise WorkflowStateError("allowed_paths must be an array of non-empty strings")
        result["next_action"] = {key: deepcopy(payload[key]) for key in ("owner", "operation", "allowed_paths")}
    elif event_type == "repository-state-updated":
        _require_payload(payload, ("repository_state_hash",))
        if not isinstance(payload["repository_state_hash"], str) or not STATE_ID_RE.fullmatch(payload["repository_state_hash"]):
            raise WorkflowStateError("repository_state_hash must be a sha256: digest")
        result["repository_state_hash"] = payload["repository_state_hash"]
    elif event_type == "phase-changed":
        _require_payload(payload, ("phase",))
        if payload["phase"] not in VALID_PHASES:
            raise WorkflowStateError(f"invalid phase: {payload['phase']}")
        if payload["phase"] not in PHASE_TRANSITIONS[result["phase"]]:
            raise WorkflowStateError(f"illegal phase transition: {result['phase']} -> {payload['phase']}")
        result["phase"] = payload["phase"]
    result["constraints"] = sorted(result["constraints"], key=lambda item: item["id"])
    result["accepted_decisions"] = sorted(result["accepted_decisions"], key=lambda item: item["id"])
    result["rejected_hypotheses"] = sorted(result["rejected_hypotheses"], key=lambda item: item["id"])
    result["open_questions"] = sorted(result["open_questions"], key=lambda item: item["id"])
    result["evidence_refs"] = sorted(result["evidence_refs"])
    return result


def _nonempty_or_raise(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowStateError(f"{field} must be a non-empty string")


def finalize_transition(previous: Dict[str, Any], mutated: Dict[str, Any], *, actor: str, event_type: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    result = deepcopy(mutated)
    result["parent_state_id"] = previous["state_id"]
    result["revision"] = previous["revision"] + 1
    event_seed = {
        "schema_version": SCHEMA_VERSION,
        "event_id": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "event_type": event_type,
        "base_state_id": previous["state_id"],
        "new_state_id": "",
        "payload": deepcopy(payload),
    }
    result["state_id"] = state_id_for(result)
    event_seed["new_state_id"] = result["state_id"]
    event_seed["event_id"] = event_id_for(event_seed)
    return result, event_seed


def replay_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reconstruct the authoritative state from one complete event chain."""
    if not events:
        raise WorkflowStateError("event log is empty")
    first = events[0]
    if first.get("event_type") != "state-initialized" or first.get("base_state_id") is not None:
        raise WorkflowStateError(
            "first event must be state-initialized with null base_state_id"
        )
    material = first.get("payload", {}).get("initial_state")
    if not isinstance(material, dict):
        raise WorkflowStateError("initial event must contain payload.initial_state")
    current = deepcopy(material)
    current["state_id"] = state_id_for(current)
    if current["state_id"] != first.get("new_state_id"):
        raise WorkflowStateError(
            "initial event new_state_id does not match replayed state"
        )
    initial_errors = validate_state(current)
    if initial_errors:
        raise WorkflowStateError("invalid initial replay state: " + "; ".join(initial_errors))
    for index, event in enumerate(events[1:], 2):
        if event.get("base_state_id") != current["state_id"]:
            raise WorkflowStateError(f"event {index} base_state_id breaks the state chain")
        mutated = apply_mutation(current, event["event_type"], event["payload"])
        mutated["parent_state_id"] = current["state_id"]
        mutated["revision"] = current["revision"] + 1
        mutated["state_id"] = state_id_for(mutated)
        if mutated["state_id"] != event.get("new_state_id"):
            raise WorkflowStateError(
                f"event {index} new_state_id does not match replayed state"
            )
        state_errors = validate_state(mutated)
        if state_errors:
            raise WorkflowStateError(
                f"event {index} produces invalid state: " + "; ".join(state_errors)
            )
        current = mutated
    return current


def validate_transition_event(event: Dict[str, Any]) -> List[str]:
    return validate_event(event)


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def append_events(path: Path, events: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(canonical_json(event) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_json(path: Path) -> Dict[str, Any]:
    try:
        if path.stat().st_size > MAX_JSON_DOCUMENT_BYTES:
            raise WorkflowStateError(
                f"{path} exceeds {MAX_JSON_DOCUMENT_BYTES} byte JSON limit"
            )
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowStateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowStateError(f"{path} must contain a JSON object")
    return value


def load_events(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    if path.stat().st_size > MAX_EVENT_LOG_BYTES:
        raise WorkflowStateError(
            f"{path} exceeds {MAX_EVENT_LOG_BYTES} byte event-log limit"
        )
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_EVENT_LINE_BYTES:
            raise WorkflowStateError(
                f"{path}:{line_number}: exceeds {MAX_EVENT_LINE_BYTES} byte event limit"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkflowStateError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkflowStateError(f"{path}:{line_number}: event must be an object")
        result.append(value)
    return result
