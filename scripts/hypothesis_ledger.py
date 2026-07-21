#!/usr/bin/env python3
"""Rejected Hypothesis Ledger primitives for Phase 3."""
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
import re
import unicodedata
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from workflow_state import WorkflowStateError, canonical_json


SCHEMA_VERSION = 1
LEDGER_FILE = "REJECTED_HYPOTHESES.json"
SIMILARITY_THRESHOLD = 0.88
VALID_ITEM_STATUS = {"rejected", "reopened"}
VALID_MATCH_TYPES = {"explicit", "exact", "similar"}
VALID_OUTCOMES = {"rejected-repeat", "reopen-required", "possible-repeat"}
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class HypothesisLedgerError(WorkflowStateError):
    """The ledger or a hypothesis transition is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_statement(statement: str) -> str:
    normalized = unicodedata.normalize("NFKC", statement).casefold()
    return " ".join("".join(
        character if character.isalnum() else " " for character in normalized
    ).split())


def statement_hash(statement: str) -> str:
    normalized = normalize_statement(statement)
    if not normalized:
        raise HypothesisLedgerError("hypothesis statement must contain letters or numbers")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def statement_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_statement(left), normalize_statement(right)).ratio()


def ledger_id_for(ledger: Dict[str, Any]) -> str:
    material = deepcopy(ledger)
    material.pop("ledger_id", None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def revisit_event_id_for(event: Dict[str, Any]) -> str:
    material = deepcopy(event)
    material.pop("event_id", None)
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def empty_ledger(task_id: str, repository_state_hash: str) -> Dict[str, Any]:
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "ledger_id": "",
        "task_id": task_id,
        "revision": 0,
        "repository_state_hash": repository_state_hash,
        "items": [],
        "revisit_events": [],
    }
    ledger["ledger_id"] = ledger_id_for(ledger)
    return ledger


def _string_list(value: Any, path: str, errors: List[str], *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{path} must be an array of non-empty strings")
    elif nonempty and not value:
        errors.append(f"{path} must not be empty")
    elif len(value) != len(set(value)):
        errors.append(f"{path} must not contain duplicates")


def validate_ledger(ledger: Any, *, verify_hash: bool = True) -> List[str]:
    if not isinstance(ledger, dict):
        return ["ledger must be an object"]
    required = {
        "schema_version", "ledger_id", "task_id", "revision",
        "repository_state_hash", "items", "revisit_events",
    }
    errors: List[str] = []
    if set(ledger) != required:
        missing = sorted(required - set(ledger))
        unknown = sorted(set(ledger) - required)
        if missing:
            errors.append("missing ledger fields: " + ", ".join(missing))
        if unknown:
            errors.append("unknown ledger fields: " + ", ".join(unknown))
        return errors
    if ledger.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    for field in ("ledger_id",):
        if not isinstance(ledger.get(field), str) or not HASH_RE.fullmatch(ledger[field]):
            errors.append(f"{field} must be a sha256: digest")
    for field in ("task_id", "repository_state_hash"):
        if not isinstance(ledger.get(field), str) or not ledger[field]:
            errors.append(f"{field} must be a non-empty string")
    if not isinstance(ledger.get("revision"), int) or isinstance(ledger["revision"], bool) or ledger["revision"] < 0:
        errors.append("revision must be a non-negative integer")
    items = ledger.get("items")
    if not isinstance(items, list):
        errors.append("items must be an array")
        items = []
    seen_ids = set()
    seen_hashes = set()
    item_fields = {
        "id", "statement", "statement_hash", "status", "reason", "evidence_refs",
        "reopen_when", "producer", "repository_state_hash", "scope_refs",
        "rejected_state_id", "reopened_reason", "reopened_evidence_refs", "reopened_by",
    }
    for index, item in enumerate(items):
        path = f"items[{index}]"
        if not isinstance(item, dict) or set(item) != item_fields:
            errors.append(path + " has invalid fields")
            continue
        for field in ("id", "statement", "reason", "producer", "repository_state_hash", "rejected_state_id"):
            if not isinstance(item.get(field), str) or not item[field]:
                errors.append(f"{path}.{field} must be a non-empty string")
        if isinstance(item.get("rejected_state_id"), str) and not HASH_RE.fullmatch(item["rejected_state_id"]):
            errors.append(path + ".rejected_state_id must be a sha256: digest")
        if item.get("id") in seen_ids:
            errors.append(f"duplicate hypothesis id: {item['id']}")
        seen_ids.add(item.get("id"))
        try:
            expected_statement_hash = statement_hash(item.get("statement", ""))
        except HypothesisLedgerError:
            errors.append(path + ".statement is not hashable")
            expected_statement_hash = None
        if expected_statement_hash is not None and item.get("statement_hash") != expected_statement_hash:
            errors.append(path + ".statement_hash does not match statement")
        if item.get("status") == "rejected" and item.get("statement_hash") in seen_hashes:
            errors.append("duplicate active rejected hypothesis statement")
        if item.get("status") == "rejected":
            seen_hashes.add(item.get("statement_hash"))
        if item.get("status") not in VALID_ITEM_STATUS:
            errors.append(path + ".status is invalid")
        _string_list(item.get("evidence_refs"), path + ".evidence_refs", errors, nonempty=True)
        _string_list(item.get("scope_refs"), path + ".scope_refs", errors)
        _string_list(item.get("reopened_evidence_refs"), path + ".reopened_evidence_refs", errors)
        if item.get("reopen_when") is not None and (
            not isinstance(item["reopen_when"], str) or not item["reopen_when"]
        ):
            errors.append(path + ".reopen_when must be null or non-empty string")
        if item.get("status") == "reopened":
            if not item.get("reopened_reason") or not item.get("reopened_by") or not item.get("reopened_evidence_refs"):
                errors.append(path + " reopened metadata is incomplete")
        elif any((item.get("reopened_reason"), item.get("reopened_by"), item.get("reopened_evidence_refs"))):
            errors.append(path + " rejected item cannot contain reopened metadata")
    events = ledger.get("revisit_events")
    if not isinstance(events, list):
        errors.append("revisit_events must be an array")
        events = []
    event_fields = {
        "event_id", "observed_at", "producer", "statement", "statement_hash",
        "evidence_refs", "repository_state_hash", "scope_refs",
        "matched_hypothesis_ids", "match_types", "outcome",
    }
    seen_event_ids = set()
    known_ids = {item.get("id") for item in items if isinstance(item, dict)}
    for index, event in enumerate(events):
        path = f"revisit_events[{index}]"
        if not isinstance(event, dict) or set(event) != event_fields:
            errors.append(path + " has invalid fields")
            continue
        for field in ("observed_at", "producer", "repository_state_hash"):
            if not isinstance(event.get(field), str) or not event[field]:
                errors.append(f"{path}.{field} must be a non-empty string")
        if event.get("event_id") != revisit_event_id_for(event):
            errors.append(path + ".event_id does not match content")
        if event.get("event_id") in seen_event_ids:
            errors.append("duplicate revisit event_id")
        seen_event_ids.add(event.get("event_id"))
        try:
            expected_statement_hash = statement_hash(event.get("statement", ""))
        except HypothesisLedgerError:
            errors.append(path + ".statement is not hashable")
            expected_statement_hash = None
        if expected_statement_hash is not None and event.get("statement_hash") != expected_statement_hash:
            errors.append(path + ".statement_hash does not match statement")
        _string_list(event.get("evidence_refs"), path + ".evidence_refs", errors)
        _string_list(event.get("scope_refs"), path + ".scope_refs", errors)
        _string_list(event.get("matched_hypothesis_ids"), path + ".matched_hypothesis_ids", errors, nonempty=True)
        if not set(event.get("matched_hypothesis_ids", [])).issubset(known_ids):
            errors.append(path + " references unknown hypothesis")
        if not isinstance(event.get("match_types"), dict) or set(event.get("match_types", {})) != set(event.get("matched_hypothesis_ids", [])) or not all(
            key in known_ids and value in VALID_MATCH_TYPES
            for key, value in event.get("match_types", {}).items()
        ):
            errors.append(path + ".match_types is invalid")
        if event.get("outcome") not in VALID_OUTCOMES:
            errors.append(path + ".outcome is invalid")
    if verify_hash and isinstance(ledger.get("ledger_id"), str) and ledger["ledger_id"] != ledger_id_for(ledger):
        errors.append("ledger_id does not match canonical ledger content")
    return errors


def validate_reject_input(value: Any) -> List[str]:
    required = {
        "schema_version", "id", "statement", "reason", "evidence_refs",
        "reopen_when", "producer", "repository_state_hash", "scope_refs",
    }
    if not isinstance(value, dict):
        return ["reject input must be an object"]
    errors = []
    if set(value) != required:
        errors.append("reject input fields do not match schema")
        return errors
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("id", "statement", "reason", "producer", "repository_state_hash"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"{field} must be a non-empty string")
    _string_list(value.get("evidence_refs"), "evidence_refs", errors, nonempty=True)
    _string_list(value.get("scope_refs"), "scope_refs", errors)
    if value.get("reopen_when") is not None and (
        not isinstance(value["reopen_when"], str) or not value["reopen_when"]
    ):
        errors.append("reopen_when must be null or a non-empty string")
    return errors


def validate_reopen_input(value: Any) -> List[str]:
    required = {
        "schema_version", "hypothesis_id", "producer", "reason",
        "new_evidence_refs", "condition_met", "repository_state_hash",
    }
    if not isinstance(value, dict):
        return ["reopen input must be an object"]
    errors = []
    if set(value) != required:
        errors.append("reopen input fields do not match schema")
        return errors
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("hypothesis_id", "producer", "reason", "repository_state_hash"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"{field} must be a non-empty string")
    _string_list(value.get("new_evidence_refs"), "new_evidence_refs", errors, nonempty=True)
    if not isinstance(value.get("condition_met"), bool):
        errors.append("condition_met must be a boolean")
    return errors


def add_rejected_item(
    ledger: Dict[str, Any], value: Dict[str, Any], rejected_state_id: str,
) -> Dict[str, Any]:
    errors = validate_ledger(ledger) + validate_reject_input(value)
    if errors:
        raise HypothesisLedgerError("; ".join(errors))
    key = statement_hash(value["statement"])
    for item in ledger["items"]:
        if item["id"] == value["id"]:
            same_metadata = (
                item["statement_hash"] == key
                and item["status"] == "rejected"
                and item["reason"] == value["reason"]
                and item["evidence_refs"] == sorted(value["evidence_refs"])
                and item["reopen_when"] == value["reopen_when"]
                and item["producer"] == value["producer"]
                and item["repository_state_hash"] == value["repository_state_hash"]
                and item["scope_refs"] == sorted(value["scope_refs"])
            )
            if same_metadata:
                return deepcopy(ledger)
            raise HypothesisLedgerError(f"hypothesis id {value['id']} already exists with different metadata")
        if item["statement_hash"] == key and item["status"] == "rejected":
            raise HypothesisLedgerError(f"equivalent rejected hypothesis already exists as {item['id']}")
    result = deepcopy(ledger)
    result["items"].append({
        "id": value["id"],
        "statement": value["statement"],
        "statement_hash": key,
        "status": "rejected",
        "reason": value["reason"],
        "evidence_refs": sorted(value["evidence_refs"]),
        "reopen_when": value["reopen_when"],
        "producer": value["producer"],
        "repository_state_hash": value["repository_state_hash"],
        "scope_refs": sorted(value["scope_refs"]),
        "rejected_state_id": rejected_state_id,
        "reopened_reason": None,
        "reopened_evidence_refs": [],
        "reopened_by": None,
    })
    result["items"] = sorted(result["items"], key=lambda item: item["id"])
    result["revision"] += 1
    result["repository_state_hash"] = value["repository_state_hash"]
    result["ledger_id"] = ledger_id_for(result)
    return result


def reopen_item(ledger: Dict[str, Any], value: Dict[str, Any]) -> Dict[str, Any]:
    errors = validate_ledger(ledger) + validate_reopen_input(value)
    if errors:
        raise HypothesisLedgerError("; ".join(errors))
    result = deepcopy(ledger)
    item = next((row for row in result["items"] if row["id"] == value["hypothesis_id"]), None)
    if item is None:
        raise HypothesisLedgerError("hypothesis does not exist")
    if item["status"] != "rejected":
        raise HypothesisLedgerError("hypothesis is not currently rejected")
    new_evidence = sorted(set(value["new_evidence_refs"]) - set(item["evidence_refs"]))
    if not new_evidence:
        raise HypothesisLedgerError("reopen requires evidence not present at rejection")
    if not value["condition_met"]:
        raise HypothesisLedgerError("reopen condition must be explicitly confirmed")
    item["status"] = "reopened"
    item["reopened_reason"] = value["reason"]
    item["reopened_evidence_refs"] = new_evidence
    item["reopened_by"] = value["producer"]
    result["revision"] += 1
    result["repository_state_hash"] = value["repository_state_hash"]
    result["ledger_id"] = ledger_id_for(result)
    return result


def validate_proposal(value: Any) -> List[str]:
    required = {
        "schema_version", "statement", "producer", "evidence_refs",
        "repository_state_hash", "scope_refs", "related_hypothesis_ids",
    }
    if not isinstance(value, dict):
        return ["proposal must be an object"]
    errors = []
    if set(value) != required:
        errors.append("proposal fields do not match schema")
        return errors
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("statement", "producer", "repository_state_hash"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"{field} must be a non-empty string")
    for field in ("evidence_refs", "scope_refs", "related_hypothesis_ids"):
        _string_list(value.get(field), field, errors)
    return errors


def check_proposal(
    ledger: Dict[str, Any], proposal: Dict[str, Any], *, max_relevant: int = 8,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    errors = validate_ledger(ledger) + validate_proposal(proposal)
    if errors:
        raise HypothesisLedgerError("; ".join(errors))
    if max_relevant < 1:
        raise HypothesisLedgerError("max_relevant must be positive")
    active = [item for item in ledger["items"] if item["status"] == "rejected"]
    known_ids = {item["id"] for item in ledger["items"]}
    unknown_related = sorted(set(proposal["related_hypothesis_ids"]) - known_ids)
    if unknown_related:
        raise HypothesisLedgerError("proposal references unknown hypotheses: " + ", ".join(unknown_related))
    proposal_hash = statement_hash(proposal["statement"])
    matches: List[Tuple[Dict[str, Any], str, float]] = []
    for item in active:
        if item["id"] in proposal["related_hypothesis_ids"]:
            matches.append((item, "explicit", 1.0))
        elif item["statement_hash"] == proposal_hash:
            matches.append((item, "exact", 1.0))
        else:
            similarity = statement_similarity(item["statement"], proposal["statement"])
            if similarity >= SIMILARITY_THRESHOLD:
                matches.append((item, "similar", similarity))
    match_ids = {item["id"] for item, _, _ in matches}
    scope = set(proposal["scope_refs"])
    matched_items = sorted(
        (item for item in active if item["id"] in match_ids),
        key=lambda item: item["id"],
    )
    scoped_items = sorted((
        item for item in active
        if item["id"] not in match_ids and scope and scope.intersection(item["scope_refs"])
    ), key=lambda item: item["id"])
    relevant = (matched_items + scoped_items)[:max_relevant]
    if not matches:
        return ({
            "status": "novel", "execute_allowed": True, "matched_hypotheses": [],
            "relevant_rejected_hypotheses": relevant,
        }, None, relevant)

    strong = [(item, match_type) for item, match_type, _ in matches if match_type != "similar"]
    has_new_evidence = any(
        set(proposal["evidence_refs"]) - set(item["evidence_refs"])
        for item, _, _ in matches
    )
    repository_changed = any(
        proposal["repository_state_hash"] != item["repository_state_hash"]
        for item, _, _ in matches
    )
    if strong and not has_new_evidence and not repository_changed:
        outcome = "rejected-repeat"
    elif strong:
        outcome = "reopen-required"
    else:
        outcome = "possible-repeat"
    match_rows = [
        {"id": item["id"], "match_type": match_type, "similarity": round(similarity, 4)}
        for item, match_type, similarity in sorted(matches, key=lambda row: row[0]["id"])
    ]
    event = {
        "event_id": "",
        "observed_at": utc_now(),
        "producer": proposal["producer"],
        "statement": proposal["statement"],
        "statement_hash": proposal_hash,
        "evidence_refs": sorted(proposal["evidence_refs"]),
        "repository_state_hash": proposal["repository_state_hash"],
        "scope_refs": sorted(proposal["scope_refs"]),
        "matched_hypothesis_ids": sorted(match_ids),
        "match_types": {row["id"]: row["match_type"] for row in match_rows},
        "outcome": outcome,
    }
    event["event_id"] = revisit_event_id_for(event)
    result = {
        "status": outcome,
        "execute_allowed": False,
        "matched_hypotheses": match_rows,
        "new_evidence_present": has_new_evidence,
        "repository_changed": repository_changed,
        "relevant_rejected_hypotheses": relevant,
    }
    return result, event, relevant


def record_revisit(ledger: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(ledger)
    if any(existing["event_id"] == event["event_id"] for existing in result["revisit_events"]):
        return result
    result["revisit_events"].append(deepcopy(event))
    result["revision"] += 1
    result["repository_state_hash"] = event["repository_state_hash"]
    result["ledger_id"] = ledger_id_for(result)
    return result


@contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
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
