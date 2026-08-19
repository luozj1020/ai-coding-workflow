#!/usr/bin/env python3
"""Conservatively audit historical Codex inference usage under Protocol v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
PROTOCOL = "codex-wakeup-audit-v1"
RESPONSIBILITY_STAGES = (
    "repository-discovery",
    "intent-freeze",
    "planning-review",
    "monitoring",
    "diff-review",
    "revision-drafting",
    "final-review",
)
PROXIMATE_TRIGGERS = (
    "task-created",
    "builder-started",
    "builder-progress",
    "builder-complete",
    "validation-complete",
    "validation-failed",
    "timeout",
    "transport-failure",
    "session-resume-failure",
    "scope-violation",
    "report-missing",
    "review-revise",
    "human-change",
    "high-risk-boundary",
)
ROOT_CAUSES = (
    "policy-mandated-review",
    "semantic-uncertainty",
    "architecture-decision",
    "contract-conflict",
    "mechanical-failure",
    "transport-runtime",
    "evidence-gap",
    "implementation-defect",
    "user-requirement-change",
    "unknown",
)
COUNTERFACTUALS = (
    "required_freeze",
    "required_final_review",
    "required_semantic_escalation",
    "avoidable_by_deterministic_guard",
    "avoidable_by_owner_convergence",
    "avoidable_by_review_reuse",
    "indeterminate",
)
REQUIRED_COUNTERFACTUALS = frozenset(COUNTERFACTUALS[:3])
AVOIDABLE_COUNTERFACTUALS = frozenset(COUNTERFACTUALS[3:6])
CLASSIFICATION_CONFIDENCE = (
    "deterministic",
    "policy-derived",
    "human-reviewed",
    "indeterminate",
)
SEMANTIC_ROOT_CAUSES = frozenset(
    (
        "semantic-uncertainty",
        "architecture-decision",
        "contract-conflict",
        "user-requirement-change",
    )
)
MECHANICAL_ROOT_CAUSES = frozenset(
    ("mechanical-failure", "transport-runtime", "evidence-gap", "implementation-defect")
)
CORE_USAGE_FIELDS = (
    "stage",
    "model",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "wall_time_ms",
    "api_time_ms",
    "result",
    "usage_complete",
    "experiment_arm",
    "audit_v1",
)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _path_label(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return "external/{}-{}".format(
            resolved.name,
            hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12],
        )


def _read_jsonl(path: Path) -> Tuple[List[Tuple[int, Dict[str, Any]]], Optional[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], "cannot-read:{}".format(type(exc).__name__)
    values: List[Tuple[int, Dict[str, Any]]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return [], "malformed-json-line:{}".format(line_number)
        if not isinstance(value, dict):
            return [], "non-object-line:{}".format(line_number)
        values.append((line_number, value))
    return values, None


def _discover(roots: Sequence[Path], filename: str) -> List[Path]:
    found: Dict[Path, Path] = {}
    for root in roots:
        if root.is_file() and root.name == filename:
            found[root.resolve()] = root
        elif root.is_dir():
            for path in root.rglob(filename):
                if ".git" not in path.parts:
                    found[path.resolve()] = path
    return [found[key] for key in sorted(found, key=lambda item: str(item))]


def _identity(row: Dict[str, Any], source: str, line_number: int) -> Tuple[str, ...]:
    values = tuple(row.get(field) for field in ("run_id", "task_id", "call_id"))
    if all(isinstance(value, str) and value for value in values):
        return ("canonical",) + values  # type: ignore[operator]
    return ("source", source, str(line_number))


def _merge_usage(
    current: Dict[str, Any], incoming: Dict[str, Any], source_ref: str
) -> Dict[str, Any]:
    current["source_refs"].append(source_ref)
    if all(
        current["raw"].get(field) == incoming.get(field) for field in CORE_USAGE_FIELDS
    ):
        return current
    current["conflicting"] = True
    current["raw_conflicts"].append(incoming)
    return current


def load_usage_records(
    paths: Sequence[Path],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    merged: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    invalid: List[Dict[str, str]] = []
    codex_rows = 0
    for path in paths:
        source = _path_label(path)
        rows, error = _read_jsonl(path)
        if error:
            invalid.append({"source": source, "error": error})
            continue
        for line_number, row in rows:
            if row.get("role") != "codex":
                continue
            codex_rows += 1
            source_ref = "{}#L{}".format(source, line_number)
            key = _identity(row, source, line_number)
            if key in merged:
                _merge_usage(merged[key], row, source_ref)
            else:
                merged[key] = {
                    "raw": row,
                    "raw_conflicts": [],
                    "source_refs": [source_ref],
                    "conflicting": False,
                    "identity_complete": key[0] == "canonical",
                }
    quality = {
        "usage_ledgers_discovered": len(paths),
        "usage_ledgers_valid": len(paths) - len(invalid),
        "usage_ledgers_invalid": invalid,
        "codex_rows_before_deduplication": codex_rows,
        "codex_inference_episodes": len(merged),
        "duplicate_rows_folded": max(0, codex_rows - len(merged)),
    }
    return list(merged.values()), quality


def load_event_annotations(
    paths: Sequence[Path],
) -> Tuple[Dict[Tuple[str, str, str], List[Dict[str, Any]]], Dict[str, Any]]:
    annotations: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    invalid: List[Dict[str, str]] = []
    event_count = 0
    for path in paths:
        source = _path_label(path)
        rows, error = _read_jsonl(path)
        if error:
            invalid.append({"source": source, "error": error})
            continue
        for line_number, row in rows:
            if row.get("schema_version") != 2:
                continue
            event_count += 1
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
            audit = (
                detail.get("audit_v1")
                if isinstance(detail.get("audit_v1"), dict)
                else None
            )
            call_id = audit.get("call_id") if audit else None
            identity = (row.get("run_id"), row.get("task_id"), call_id)
            if audit and all(isinstance(value, str) and value for value in identity):
                annotations[identity].append(
                    {**audit, "_source_ref": "{}#L{}".format(source, line_number)}
                )
    return annotations, {
        "event_logs_discovered": len(paths),
        "event_logs_invalid": invalid,
        "event_v2_rows": event_count,
        "call_bound_audit_annotations": sum(
            len(values) for values in annotations.values()
        ),
    }


def _valid_annotation(value: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    enum_fields = (
        ("proximate_trigger", PROXIMATE_TRIGGERS),
        ("root_cause", ROOT_CAUSES),
        ("bookend_counterfactual", COUNTERFACTUALS),
        ("classification_confidence", CLASSIFICATION_CONFIDENCE),
    )
    for field, allowed in enum_fields:
        if value.get(field) is not None and value.get(field) not in allowed:
            errors.append("invalid-{}".format(field))
    for field in ("policy_required", "user_triggered", "semantic_decision_required"):
        if value.get(field) is not None and not isinstance(value.get(field), bool):
            errors.append("invalid-{}".format(field))
    iteration = value.get("iteration")
    if iteration is not None and (
        not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1
    ):
        errors.append("invalid-iteration")
    counterfactual = value.get("bookend_counterfactual")
    refs = value.get("evidence_refs")
    if counterfactual and counterfactual != "indeterminate":
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(item, str) and item for item in refs)
        ):
            errors.append("counterfactual-requires-evidence-refs")
        if value.get("classification_confidence") not in {
            "deterministic",
            "policy-derived",
            "human-reviewed",
        }:
            errors.append("counterfactual-requires-classification-confidence")
    semantic = value.get("semantic_decision_required")
    cause = value.get("root_cause")
    if semantic is True and cause in MECHANICAL_ROOT_CAUSES:
        errors.append("semantic-true-conflicts-with-mechanical-root-cause")
    if semantic is False and cause in SEMANTIC_ROOT_CAUSES:
        errors.append("semantic-false-conflicts-with-semantic-root-cause")
    return errors


def _choose_annotation(
    row: Dict[str, Any],
    event_annotations: Dict[Tuple[str, str, str], List[Dict[str, Any]]],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    candidates: List[Dict[str, Any]] = []
    embedded = row.get("audit_v1")
    if isinstance(embedded, dict):
        candidates.append({**embedded, "_source_ref": "usage-record:audit_v1"})
    identity = (row.get("run_id"), row.get("task_id"), row.get("call_id"))
    if all(isinstance(value, str) and value for value in identity):
        candidates.extend(event_annotations.get(identity, []))  # type: ignore[arg-type]
    if not candidates:
        return None, []
    normalized = [
        {
            key: value
            for key, value in item.items()
            if key not in {"_source_ref", "call_id"}
        }
        for item in candidates
    ]
    if any(item != normalized[0] for item in normalized[1:]):
        return None, ["conflicting-audit-annotations"]
    errors = _valid_annotation(candidates[0])
    return (candidates[0] if not errors else None), errors


def _classify(
    row: Dict[str, Any], annotation: Optional[Dict[str, Any]]
) -> Tuple[str, str]:
    if annotation and annotation.get("bookend_counterfactual") in COUNTERFACTUALS:
        return (
            str(annotation["bookend_counterfactual"]),
            str(annotation.get("classification_confidence") or "indeterminate"),
        )
    if row.get("stage") == "intent-freeze":
        return "required_freeze", "deterministic"
    if row.get("stage") == "final-review":
        return "required_final_review", "deterministic"
    return "indeterminate", "indeterminate"


def _metric_value(row: Dict[str, Any], field: str, conflicting: bool) -> Any:
    if conflicting and field in CORE_USAGE_FIELDS:
        return None
    value = row.get(field)
    return value if _is_number(value) else None


def build_episodes(
    records: Sequence[Dict[str, Any]],
    event_annotations: Dict[Tuple[str, str, str], List[Dict[str, Any]]],
    metrics: Dict[Tuple[str, str], Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    episodes: List[Dict[str, Any]] = []
    classification_errors: List[Dict[str, Any]] = []
    for wrapped in records:
        row = wrapped["raw"]
        annotation, errors = _choose_annotation(row, event_annotations)
        if errors:
            classification_errors.append(
                {
                    "call_id": row.get("call_id"),
                    "errors": errors,
                    "source_refs": wrapped["source_refs"],
                }
            )
        if wrapped["conflicting"]:
            counterfactual, confidence = "indeterminate", "indeterminate"
        else:
            counterfactual, confidence = _classify(row, annotation)
        observed_stage = row.get("stage") if isinstance(row.get("stage"), str) else None
        stage = (
            observed_stage
            if observed_stage in RESPONSIBILITY_STAGES
            else "unclassified"
        )
        run_key = (row.get("run_id"), row.get("task_id"))
        run_metrics = (
            metrics.get(run_key, {})
            if all(isinstance(item, str) and item for item in run_key)
            else {}
        )
        conflicting = bool(wrapped["conflicting"])
        audit = annotation or {}
        identity_complete = bool(wrapped["identity_complete"])
        usage_complete = bool(row.get("usage_complete")) and not conflicting
        if conflicting:
            evidence_quality = "conflicting"
        elif identity_complete and usage_complete:
            evidence_quality = "complete"
        else:
            evidence_quality = "incomplete"
        episode = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "logical_task_id": audit.get("logical_task_id"),
            "run_id": row.get("run_id"),
            "session_id": audit.get("session_id"),
            "task_id": row.get("task_id"),
            "call_id": row.get("call_id"),
            "stage": stage,
            "observed_stage": observed_stage,
            "iteration": audit.get("iteration"),
            "proximate_trigger": audit.get("proximate_trigger"),
            "root_cause": audit.get("root_cause"),
            "policy_required": audit.get("policy_required"),
            "user_triggered": audit.get("user_triggered"),
            "semantic_decision_required": audit.get("semantic_decision_required"),
            "bookend_counterfactual": counterfactual,
            "model": None if conflicting else row.get("model"),
            "input_tokens": _metric_value(row, "input_tokens", conflicting),
            "output_tokens": _metric_value(row, "output_tokens", conflicting),
            "reasoning_tokens": _metric_value(row, "reasoning_tokens", conflicting),
            "active_elapsed_ms": _metric_value(row, "wall_time_ms", conflicting),
            "result": None if conflicting else row.get("result"),
            "usage_complete": usage_complete,
            "experiment_arm": (
                None
                if conflicting
                else row.get("experiment_arm", run_metrics.get("experiment_arm"))
            ),
            "actual_owner": run_metrics.get("actual_owner"),
            "completed": (
                run_metrics.get("completed")
                if isinstance(run_metrics.get("completed"), bool)
                else None
            ),
            "accepted": (
                run_metrics.get("accepted")
                if isinstance(run_metrics.get("accepted"), bool)
                else None
            ),
            "evidence_quality": evidence_quality,
            "classification_confidence": confidence,
            "evidence_refs": list(audit.get("evidence_refs") or []),
            "source_refs": sorted(set(wrapped["source_refs"])),
        }
        episodes.append(episode)
    episodes.sort(
        key=lambda row: tuple(
            str(row.get(key) or "") for key in ("run_id", "task_id", "call_id")
        )
    )
    return episodes, classification_errors


def load_run_metrics(
    usage_paths: Sequence[Path],
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], List[Dict[str, str]]]:
    values: Dict[Tuple[str, str], Dict[str, Any]] = {}
    conflicting_keys: set[Tuple[str, str]] = set()
    errors: List[Dict[str, str]] = []
    candidates = {
        path.with_name("run-metrics.json").resolve(): path.with_name("run-metrics.json")
        for path in usage_paths
    }
    for path in candidates.values():
        if not path.is_file():
            continue
        label = _path_label(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(
                {
                    "source": label,
                    "error": "invalid-run-metrics:{}".format(type(exc).__name__),
                }
            )
            continue
        key = (
            (value.get("run_id"), value.get("task_id"))
            if isinstance(value, dict)
            else (None, None)
        )
        if not all(isinstance(item, str) and item for item in key):
            errors.append({"source": label, "error": "missing-run-task-identity"})
            continue
        if key in conflicting_keys:
            continue
        if key in values and values[key] != value:
            errors.append({"source": label, "error": "conflicting-run-metrics"})
            values.pop(key, None)
            conflicting_keys.add(key)
            continue
        values[key] = value
    return values, errors


def load_broker_summary(paths: Sequence[Path]) -> Dict[str, Any]:
    invalid: List[Dict[str, str]] = []
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    diagnostics = 0
    for path in paths:
        source = _path_label(path)
        rows, error = _read_jsonl(path)
        if error:
            invalid.append({"source": source, "error": error})
            continue
        for _, row in rows:
            if row.get("role") != "codex":
                continue
            if row.get("state") == "diagnostic":
                diagnostics += 1
                continue
            reservation_id = row.get("reservation_id")
            if isinstance(reservation_id, str) and reservation_id:
                latest[(source, reservation_id)] = row
    states: Dict[str, int] = defaultdict(int)
    stages: Dict[str, int] = defaultdict(int)
    call_types: Dict[str, int] = defaultdict(int)
    requests: Dict[Tuple[str, str, str], int] = defaultdict(int)
    evidence_groups: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    for row in latest.values():
        states[str(row.get("state") or "unknown")] += 1
        stages[str(row.get("stage") or "unknown")] += 1
        call_types[str(row.get("call_type") or "unknown")] += 1
        request_id = row.get("request_id")
        if isinstance(request_id, str) and request_id:
            requests[
                (
                    str(row.get("run_id") or "unknown"),
                    str(row.get("task_id") or "unknown"),
                    request_id,
                )
            ] += 1
        input_hash = row.get("input_hash")
        evidence_hash = row.get("evidence_hash")
        if all(
            isinstance(value, str) and value for value in (input_hash, evidence_hash)
        ):
            evidence_groups[
                (
                    str(row.get("task_id") or "unknown"),
                    str(row.get("stage") or "unknown"),
                    input_hash,
                    evidence_hash,
                )
            ] += 1
    return {
        "broker_ledgers_discovered": len(paths),
        "broker_ledgers_invalid": invalid,
        "codex_reservations": len(latest),
        "reservation_terminal_states": dict(sorted(states.items())),
        "reservations_by_stage": dict(sorted(stages.items())),
        "reservations_by_call_type": dict(sorted(call_types.items())),
        "repeated_request_groups": sum(1 for count in requests.values() if count > 1),
        "repeated_input_evidence_groups": sum(
            1 for count in evidence_groups.values() if count > 1
        ),
        "non_inference_control_activity": diagnostics,
        "note": "broker reservations are not added to usage totals without an exact usage identity binding",
    }


def _group(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    fields = ("input_tokens", "output_tokens", "reasoning_tokens", "active_elapsed_ms")
    result: Dict[str, Any] = {
        "calls": len(rows),
        "complete_calls": sum(1 for row in rows if row.get("usage_complete") is True),
    }
    for field in fields:
        known = [row[field] for row in rows if _is_number(row.get(field))]
        complete = len(known) == len(rows)
        result["known_{}".format(field)] = sum(known) if known else 0
        result["{}_missing_calls".format(field)] = len(rows) - len(known)
        result[field] = sum(known) if complete else None
    inputs = [
        row["input_tokens"] for row in rows if _is_number(row.get("input_tokens"))
    ]
    result["known_median_input_tokens"] = statistics.median(inputs) if inputs else None
    result["median_input_tokens"] = (
        statistics.median(inputs) if len(inputs) == len(rows) and inputs else None
    )
    return result


def _task_summary(episodes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    observed_keys = {
        (row.get("run_id"), row.get("task_id"))
        for row in episodes
        if isinstance(row.get("run_id"), str)
        and row.get("run_id")
        and isinstance(row.get("task_id"), str)
        and row.get("task_id")
    }
    logical_ids = {
        row.get("logical_task_id")
        for row in episodes
        if isinstance(row.get("logical_task_id"), str) and row.get("logical_task_id")
    }
    logical_complete = (
        bool(episodes)
        and len(logical_ids) > 0
        and all(row.get("logical_task_id") in logical_ids for row in episodes)
    )
    call_sets_by_source: Dict[Tuple[Any, Any], Dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in episodes:
        key = (row.get("run_id"), row.get("task_id"))
        call_id = row.get("call_id")
        if not all(isinstance(item, str) and item for item in (*key, call_id)):
            continue
        for source_ref in row.get("source_refs", []):
            ledger = str(source_ref).rsplit("#L", 1)[0]
            call_sets_by_source[key][ledger].add(call_id)
    collision_keys = []
    for key, by_source in call_sets_by_source.items():
        distinct_sets = {tuple(sorted(call_ids)) for call_ids in by_source.values()}
        if len(by_source) > 1 and len(distinct_sets) > 1:
            collision_keys.append(key)
    observed_identity_complete = (
        bool(episodes)
        and not collision_keys
        and all(
            isinstance(row.get("run_id"), str)
            and row.get("run_id")
            and isinstance(row.get("task_id"), str)
            and row.get("task_id")
            for row in episodes
        )
    )
    metrics_complete = bool(episodes) and all(
        isinstance(row.get("completed"), bool) and isinstance(row.get("accepted"), bool)
        for row in episodes
    )
    completed = {
        (row.get("run_id"), row.get("task_id"))
        for row in episodes
        if row.get("completed") is True
    }
    accepted = {
        (row.get("run_id"), row.get("task_id"))
        for row in episodes
        if row.get("accepted") is True
    }
    accepted_rows = [row for row in episodes if row.get("accepted") is True]
    accepted_tokens_complete = (
        metrics_complete
        and bool(accepted)
        and all(_is_number(row.get("input_tokens")) for row in accepted_rows)
    )
    return {
        "known_distinct_run_task_keys": len(observed_keys),
        "observed_run_task_identity_complete": observed_identity_complete,
        "observed_run_task_identity_collision_count": len(collision_keys),
        "observed_run_task_count": (
            len(observed_keys) if observed_identity_complete else None
        ),
        "calls_per_observed_run_task": (
            round(len(episodes) / len(observed_keys), 4)
            if observed_identity_complete and observed_keys
            else None
        ),
        "logical_task_identity_complete": logical_complete,
        "logical_task_count": len(logical_ids) if logical_complete else None,
        "calls_per_logical_task": (
            round(len(episodes) / len(logical_ids), 4) if logical_complete else None
        ),
        "run_metrics_complete": metrics_complete,
        "completed_observed_tasks": len(completed) if metrics_complete else None,
        "accepted_observed_tasks": len(accepted) if metrics_complete else None,
        "input_tokens_per_accepted_task": (
            round(sum(row["input_tokens"] for row in accepted_rows) / len(accepted), 4)
            if accepted_tokens_complete
            else None
        ),
    }


def build_summary(
    episodes: Sequence[Dict[str, Any]], quality: Dict[str, Any], broker: Dict[str, Any]
) -> Dict[str, Any]:
    by_stage = {
        stage: _group([row for row in episodes if row.get("stage") == stage])
        for stage in (*RESPONSIBILITY_STAGES, "unclassified")
    }
    raw_unclassified: Dict[str, int] = defaultdict(int)
    for row in episodes:
        if row.get("stage") == "unclassified":
            raw_unclassified[str(row.get("observed_stage") or "unknown")] += 1
    by_arm = {
        arm: _group(
            [
                row
                for row in episodes
                if str(row.get("experiment_arm") or "unknown") == arm
            ]
        )
        for arm in sorted(
            {str(row.get("experiment_arm") or "unknown") for row in episodes}
        )
    }
    by_owner = {
        owner: _group(
            [
                row
                for row in episodes
                if str(row.get("actual_owner") or "unknown") == owner
            ]
        )
        for owner in sorted(
            {str(row.get("actual_owner") or "unknown") for row in episodes}
        )
    }
    by_counterfactual = {
        name: _group(
            [row for row in episodes if row.get("bookend_counterfactual") == name]
        )
        for name in COUNTERFACTUALS
    }
    required_rows = [
        row
        for row in episodes
        if row.get("bookend_counterfactual") in REQUIRED_COUNTERFACTUALS
    ]
    avoidable_rows = [
        row
        for row in episodes
        if row.get("bookend_counterfactual") in AVOIDABLE_COUNTERFACTUALS
    ]
    indeterminate_rows = [
        row for row in episodes if row.get("bookend_counterfactual") == "indeterminate"
    ]
    all_inputs_complete = bool(episodes) and all(
        _is_number(row.get("input_tokens")) for row in episodes
    )
    required_tokens = sum(
        row["input_tokens"]
        for row in required_rows
        if _is_number(row.get("input_tokens"))
    )
    avoidable_tokens = sum(
        row["input_tokens"]
        for row in avoidable_rows
        if _is_number(row.get("input_tokens"))
    )
    indeterminate_tokens = sum(
        row["input_tokens"]
        for row in indeterminate_rows
        if _is_number(row.get("input_tokens"))
    )
    observed_tokens = required_tokens + avoidable_tokens + indeterminate_tokens
    counterfactual = {
        "by_classification": by_counterfactual,
        "known_required_input_tokens": required_tokens,
        "known_safely_avoidable_input_tokens": avoidable_tokens,
        "known_indeterminate_input_tokens": indeterminate_tokens,
        "input_tokens_complete": all_inputs_complete,
        "safe_savings_ratio": (
            round(avoidable_tokens / observed_tokens, 6)
            if all_inputs_complete and observed_tokens
            else None
        ),
        "bookend_retained_input_tokens_interval": {
            "minimum": required_tokens if all_inputs_complete else None,
            "maximum": (
                required_tokens + indeterminate_tokens if all_inputs_complete else None
            ),
        },
        "non_inference_control_activity": broker.get(
            "non_inference_control_activity", 0
        ),
        "status": (
            "sufficient-for-conservative-bound"
            if all_inputs_complete and not quality.get("classification_errors")
            else "insufficient-evidence"
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "observed_facts": {
            "totals": _group(episodes),
            "by_stage": by_stage,
            "raw_unclassified_stages": dict(sorted(raw_unclassified.items())),
            "by_experiment_arm": by_arm,
            "by_actual_owner": by_owner,
            "tasks": _task_summary(episodes),
        },
        "bookend_counterfactual": counterfactual,
        "broker_control_plane": broker,
        "data_quality": quality,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        default=[],
        help="Recursively discover canonical audit artifacts",
    )
    parser.add_argument("--usage-ledger", action="append", type=Path, default=[])
    parser.add_argument("--broker-ledger", action="append", type=Path, default=[])
    parser.add_argument("--event-log", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--episodes-output", type=Path)
    args = parser.parse_args(argv)

    if not args.root and not args.usage_ledger:
        parser.error("at least one --root or --usage-ledger is required")
    usage_paths = list(args.usage_ledger) + _discover(args.root, "model-usage.jsonl")
    usage_paths = list({path.resolve(): path for path in usage_paths}.values())
    broker_paths = list(args.broker_ledger)
    for filename in ("run-ledger.jsonl", "model-call-ledger.jsonl"):
        broker_paths.extend(_discover(args.root, filename))
    broker_paths = list({path.resolve(): path for path in broker_paths}.values())
    event_paths = list(args.event_log) + _discover(args.root, "loop-events.jsonl")
    event_paths = list({path.resolve(): path for path in event_paths}.values())

    records, usage_quality = load_usage_records(usage_paths)
    annotations, event_quality = load_event_annotations(event_paths)
    metrics, metrics_errors = load_run_metrics(usage_paths)
    episodes, classification_errors = build_episodes(records, annotations, metrics)
    broker = load_broker_summary(broker_paths)
    quality = {
        **usage_quality,
        **event_quality,
        "run_metrics_records": len(metrics),
        "run_metrics_errors": metrics_errors,
        "classification_errors": classification_errors,
    }
    summary = build_summary(episodes, quality, broker)
    if args.episodes_output:
        _write_jsonl(args.episodes_output, episodes)
    if args.output:
        _write_json(args.output, summary)
    else:
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
