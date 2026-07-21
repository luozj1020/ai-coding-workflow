#!/usr/bin/env python3
"""Deterministic ownership-continuity and lease selection primitives."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from workflow_state import atomic_write_json, canonical_json


HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OPERATIONS = {"mechanical-revision", "test-fix", "semantic-revision", "validation", "continuation"}
RESUME_STATUSES = {"not-attempted", "succeeded", "failed"}
LEASE_FIELDS = {
    "schema_version", "lease_id", "task_id", "state_id", "operation",
    "original_builder_id", "current_builder_id", "selected_owner_id", "owner_source",
    "current_model", "selected_model",
    "status", "previous_lease_id", "lease_generation", "handoff_count",
    "last_handoff_event_id", "lease_ttl_seconds", "renewal_count", "session",
    "model_switch", "advisor", "reviewer", "transition_reason",
}


class OwnerLeaseError(ValueError):
    """An ownership request or lease is invalid."""


def _hash(value: Dict[str, Any], field: str) -> str:
    material = deepcopy(value)
    material.pop(field, None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode()).hexdigest()


def _strings(value: Any) -> bool:
    return isinstance(value, list) and len(value) == len(set(value)) and all(
        isinstance(item, str) and item for item in value
    )


def _owner_model(owner_id: str) -> str:
    lowered = owner_id.lower()
    if "claude" in lowered:
        return "claude-builder"
    if "codex" in lowered:
        return "codex-fast-path"
    return "unknown"


def validate_request(value: Any) -> List[str]:
    required = {
        "schema_version", "task_id", "state_id", "operation", "original_builder_id",
        "current_builder_id", "current_session_id", "resume_status", "new_evidence_refs",
        "semantic_blockers", "explicit_owner_id", "switch_reason",
        "last_handoff_event_id", "lease_ttl_seconds",
    }
    if not isinstance(value, dict) or set(value) != required:
        return ["ownership request fields do not match contract"]
    errors = []
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("task_id", "original_builder_id", "current_builder_id"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"{field} must be non-empty")
    if not isinstance(value.get("state_id"), str) or not HASH_RE.fullmatch(value["state_id"]):
        errors.append("state_id must be a sha256: digest")
    if value.get("operation") not in OPERATIONS:
        errors.append("operation is invalid")
    if value.get("resume_status") not in RESUME_STATUSES:
        errors.append("resume_status is invalid")
    for field in ("current_session_id", "explicit_owner_id", "switch_reason", "last_handoff_event_id"):
        if value.get(field) is not None and (not isinstance(value[field], str) or not value[field]):
            errors.append(f"{field} must be null or non-empty")
    for field in ("new_evidence_refs", "semantic_blockers"):
        if not _strings(value.get(field)):
            errors.append(f"{field} must be a unique string array")
    if any(not HASH_RE.fullmatch(ref) for ref in value.get("new_evidence_refs", [])):
        errors.append("new_evidence_refs must contain immutable object IDs")
    ttl = value.get("lease_ttl_seconds")
    if not isinstance(ttl, int) or isinstance(ttl, bool) or not 60 <= ttl <= 86400:
        errors.append("lease_ttl_seconds must be an integer from 60 to 86400")
    return errors


def validate_lease(value: Any) -> List[str]:
    if not isinstance(value, dict) or set(value) != LEASE_FIELDS:
        return ["Owner Lease fields do not match schema"]
    errors = []
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("lease_id", "state_id"):
        if not isinstance(value.get(field), str) or not HASH_RE.fullmatch(value[field]):
            errors.append(f"{field} must be a sha256: digest")
    for field in ("task_id", "operation", "original_builder_id", "current_builder_id", "selected_owner_id", "owner_source"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"{field} must be non-empty")
    if not isinstance(value.get("transition_reason"), str) or not value["transition_reason"]:
        errors.append("transition_reason must be non-empty")
    for field in ("current_model", "selected_model"):
        if value.get(field) not in {"claude-builder", "codex-fast-path", "unknown"}:
            errors.append(f"{field} is invalid")
    if value.get("operation") not in OPERATIONS:
        errors.append("operation is invalid")
    if value.get("status") not in {"requested", "granted", "expired", "revoked"}:
        errors.append("status is invalid")
    previous = value.get("previous_lease_id")
    if previous is not None and (not isinstance(previous, str) or not HASH_RE.fullmatch(previous)):
        errors.append("previous_lease_id must be null or a sha256: digest")
    for field in ("lease_generation", "handoff_count", "renewal_count"):
        if not isinstance(value.get(field), int) or isinstance(value[field], bool) or value[field] < 0:
            errors.append(f"{field} must be a non-negative integer")
    ttl = value.get("lease_ttl_seconds")
    if not isinstance(ttl, int) or isinstance(ttl, bool) or not 60 <= ttl <= 86400:
        errors.append("lease_ttl_seconds is invalid")
    session = value.get("session")
    if not isinstance(session, dict) or set(session) != {"session_id", "resume_status", "mode"}:
        errors.append("session fields do not match schema")
    else:
        if session.get("mode") not in {"resumed-session", "new-session", "resume-required"}:
            errors.append("session.mode is invalid")
        if session.get("resume_status") not in RESUME_STATUSES:
            errors.append("session.resume_status is invalid")
        if session.get("session_id") is not None and (not isinstance(session["session_id"], str) or not session["session_id"]):
            errors.append("session.session_id must be null or non-empty")
        if session.get("mode") == "resumed-session" and (
            session.get("resume_status") != "succeeded" or not session.get("session_id")
        ):
            errors.append("resumed-session requires a successful recorded session")
        if session.get("mode") == "resume-required" and session.get("resume_status") != "not-attempted":
            errors.append("resume-required requires not-attempted status")
    switch = value.get("model_switch")
    if not isinstance(switch, dict) or set(switch) != {"required", "from_owner", "to_owner", "reason"}:
        errors.append("model_switch fields do not match schema")
    else:
        if not isinstance(switch.get("required"), bool):
            errors.append("model_switch.required must be a boolean")
        for field in ("from_owner", "to_owner"):
            if not isinstance(switch.get(field), str) or not switch[field]:
                errors.append(f"model_switch.{field} must be non-empty")
        if switch.get("reason") is not None and (not isinstance(switch["reason"], str) or not switch["reason"]):
            errors.append("model_switch.reason must be null or non-empty")
        if switch.get("required") is True and (
            not switch.get("reason") or switch.get("from_owner") == switch.get("to_owner")
        ):
            errors.append("a model switch requires distinct owners and an explicit reason")
        if switch.get("required") is False and (
            switch.get("reason") is not None or switch.get("from_owner") != switch.get("to_owner")
        ):
            errors.append("a non-switch requires identical owners and no reason")
    for name, actions in (("advisor", {"skip", "invoke"}), ("reviewer", {"skip", "invoke"})):
        item = value.get(name)
        expected = {"action", "reason"} | ({"new_evidence_refs"} if name == "reviewer" else set())
        if not isinstance(item, dict) or set(item) != expected or item.get("action") not in actions or not isinstance(item.get("reason"), str) or not item["reason"]:
            errors.append(f"{name} fields are invalid")
    if isinstance(value.get("reviewer"), dict) and not _strings(value["reviewer"].get("new_evidence_refs")):
        errors.append("reviewer.new_evidence_refs is invalid")
    elif isinstance(value.get("reviewer"), dict) and any(
        not HASH_RE.fullmatch(ref) for ref in value["reviewer"]["new_evidence_refs"]
    ):
        errors.append("reviewer.new_evidence_refs must contain immutable object IDs")
    if value.get("status") == "granted" and isinstance(session, dict) and session.get("mode") == "resume-required":
        errors.append("a resume-required lease cannot be granted")
    if isinstance(value.get("lease_id"), str) and value["lease_id"] != _hash(value, "lease_id"):
        errors.append("lease_id does not match canonical lease content")
    return errors


def select_owner(request: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    errors = validate_request(request)
    if errors:
        raise OwnerLeaseError("; ".join(errors))
    if previous is not None:
        errors = validate_lease(previous)
        if errors:
            raise OwnerLeaseError("invalid previous Owner Lease: " + "; ".join(errors))
        if previous["task_id"] != request["task_id"]:
            raise OwnerLeaseError("previous Owner Lease belongs to another task")
        if previous["status"] == "revoked":
            raise OwnerLeaseError("a revoked Owner Lease cannot be renewed")

    explicit = request["explicit_owner_id"]
    if explicit:
        selected, source = explicit, "explicit-human-owner"
    elif request["operation"] in {"mechanical-revision", "test-fix"}:
        selected, source = request["original_builder_id"], "original-builder-continuity"
    else:
        selected, source = request["current_builder_id"], "current-builder-continuity"

    switching = selected != request["current_builder_id"]
    switch_reason = request["switch_reason"]
    if switching and not switch_reason:
        switch_reason = "explicit-human-owner" if explicit else "return-to-original-builder"
    if not switching and switch_reason:
        raise OwnerLeaseError("switch_reason is forbidden when owner does not change")

    resume = request["resume_status"]
    same_owner = not switching
    if same_owner and resume == "succeeded":
        session_mode, status = "resumed-session", "granted"
    elif same_owner and resume == "failed":
        session_mode, status = "new-session", "granted"
    elif same_owner:
        session_mode, status = "resume-required", "requested"
    else:
        session_mode, status = "new-session", "granted"
    if session_mode == "resumed-session" and not request["current_session_id"]:
        raise OwnerLeaseError("resume succeeded requires current_session_id")

    prior_switches = previous["handoff_count"] if previous else 0
    generation = previous["lease_generation"] + 1 if previous else 1
    renewal = (
        previous["renewal_count"] + 1
        if previous and previous["status"] == "granted" and not switching else 0
    )
    lease = {
        "schema_version": 1, "lease_id": "", "task_id": request["task_id"],
        "state_id": request["state_id"], "operation": request["operation"],
        "original_builder_id": request["original_builder_id"],
        "current_builder_id": request["current_builder_id"],
        "selected_owner_id": selected, "owner_source": source, "status": status,
        "current_model": _owner_model(request["current_builder_id"]),
        "selected_model": _owner_model(selected),
        "previous_lease_id": previous["lease_id"] if previous else None,
        "lease_generation": generation, "handoff_count": prior_switches + int(switching),
        "last_handoff_event_id": request["last_handoff_event_id"],
        "lease_ttl_seconds": request["lease_ttl_seconds"], "renewal_count": renewal,
        "session": {"session_id": request["current_session_id"] if session_mode == "resumed-session" else None, "resume_status": resume, "mode": session_mode},
        "model_switch": {"required": switching, "from_owner": request["current_builder_id"], "to_owner": selected, "reason": switch_reason},
        "advisor": {"action": "invoke" if request["semantic_blockers"] else "skip", "reason": "semantic-blockers-present" if request["semantic_blockers"] else "no-semantic-blocker"},
        "reviewer": {"action": "invoke" if request["new_evidence_refs"] else "skip", "reason": "new-evidence-present" if request["new_evidence_refs"] else "no-new-evidence", "new_evidence_refs": sorted(request["new_evidence_refs"])},
        "transition_reason": "owner-selected",
    }
    lease["lease_id"] = _hash(lease, "lease_id")
    errors = validate_lease(lease)
    if errors:
        raise OwnerLeaseError("generated invalid Owner Lease: " + "; ".join(errors))
    return lease


def transition_lease(previous: Dict[str, Any], status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    errors = validate_lease(previous)
    if errors:
        raise OwnerLeaseError("invalid previous Owner Lease: " + "; ".join(errors))
    if status not in {"expired", "revoked"}:
        raise OwnerLeaseError("transition status must be expired or revoked")
    if previous["status"] not in {"requested", "granted"}:
        raise OwnerLeaseError("only a requested or granted lease can become terminal")
    if status == "revoked" and (not isinstance(reason, str) or not reason.strip()):
        raise OwnerLeaseError("revocation requires an explicit reason")
    if status == "expired" and reason is not None:
        raise OwnerLeaseError("expiry reason is deterministically ttl-expired")
    value = deepcopy(previous)
    value["lease_id"] = ""
    value["previous_lease_id"] = previous["lease_id"]
    value["lease_generation"] += 1
    value["status"] = status
    value["owner_source"] = "lease-" + status
    value["transition_reason"] = "ttl-expired" if status == "expired" else reason.strip()
    value["lease_id"] = _hash(value, "lease_id")
    errors = validate_lease(value)
    if errors:
        raise OwnerLeaseError("generated invalid terminal Owner Lease: " + "; ".join(errors))
    return value


def load_json(path: Path) -> Any:
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise OwnerLeaseError("input is unsafe or exceeds 2 MiB")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OwnerLeaseError(f"cannot read input: {exc}") from exc
