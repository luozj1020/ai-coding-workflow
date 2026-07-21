#!/usr/bin/env python3
"""Observed Handoff Tax estimation and deterministic route calibration."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


CORE_COMPONENTS = ("serialization_bytes", "reconstruction_seconds", "rediscovery_count", "revision_count")
HASH_PREFIX = "sha256:"


class HandoffRoutingError(ValueError):
    """Observed handoff inputs or routing calibration are invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def document_id(value: Dict[str, Any], field: str) -> str:
    material = deepcopy(value)
    material.pop(field, None)
    return HASH_PREFIX + hashlib.sha256(canonical_json(material).encode()).hexdigest()


def _median(values: Iterable[float]) -> Optional[float]:
    values = list(values)
    return round(float(statistics.median(values)), 6) if values else None


def _known(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _summarizer():
    path = Path(__file__).resolve().with_name("summarize-handoff-metrics.py")
    spec = importlib.util.spec_from_file_location("aiwf_handoff_summary_for_tax", path)
    if spec is None or spec.loader is None:
        raise HandoffRoutingError("cannot load handoff event validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def estimate_paths(
    paths: Iterable[Path], *, sender: Optional[str] = None, receiver: Optional[str] = None,
    task_type: Optional[str] = None, min_samples: int = 3,
) -> Dict[str, Any]:
    if min_samples < 1:
        raise HandoffRoutingError("min_samples must be positive")
    try:
        events = _summarizer().read_handoff_events(paths)
    except (OSError, ValueError) as exc:
        raise HandoffRoutingError(str(exc)) from exc
    details = [event["detail"] for event in events]
    if sender:
        details = [item for item in details if item["sender"] == sender]
    if receiver:
        details = [item for item in details if item["receiver"] == receiver]
    if task_type:
        details = [item for item in details if item["task_type"] == task_type]
    details = [item for item in details if item["sender"] != item["receiver"]]

    fields = {
        "serialization_bytes": "payload_bytes",
        "reconstruction_seconds": "seconds_to_first_meaningful_action",
        "revision_count": "handoff_revision_count",
        "repeated_payload_bytes": "repeated_payload_bytes",
        "receiver_reads": "receiver_reads_before_first_action",
        "receiver_searches": "receiver_searches_before_first_action",
    }
    components: Dict[str, Any] = {}
    known_counts: Dict[str, int] = {}
    for name, field in fields.items():
        values = [item[field] for item in details if _known(item[field])]
        components[name] = _median(values)
        known_counts[name] = len(values)
    rediscovery = [
        item["known_facts_rediscovered"] + item["rejected_hypotheses_revisited"]
        for item in details
        if _known(item["known_facts_rediscovered"]) and _known(item["rejected_hypotheses_revisited"])
    ]
    components["rediscovery_count"] = _median(rediscovery)
    known_counts["rediscovery_count"] = len(rediscovery)
    cache_rates = [
        item["context_cache_hits"] / item["context_objects_requested"]
        for item in details
        if _known(item["context_cache_hits"]) and _known(item["context_objects_requested"])
        and item["context_objects_requested"] > 0
    ]
    components["context_cache_hit_rate"] = _median(cache_rates)
    known_counts["context_cache_hit_rate"] = len(cache_rates)

    count = len(details)
    complete = min((known_counts[name] for name in CORE_COMPONENTS), default=0)
    if count == 0:
        status, reason = "unknown", "no-matching-cross-model-handoffs"
    elif complete >= min_samples:
        status, reason = "calibrated", "minimum-complete-observed-history-met"
    elif complete:
        status, reason = "canary", "insufficient-complete-history"
    else:
        status, reason = "unknown", "core-handoff-components-incomplete"
    result = {
        "schema_version": 1, "estimate_id": "", "source": "observed-handoff-events",
        "status": status, "reason": reason, "sender": sender, "receiver": receiver,
        "task_type": task_type, "sample_count": count, "complete_sample_count": complete,
        "minimum_samples": min_samples, "components": components,
        "known_sample_counts": known_counts,
    }
    result["estimate_id"] = document_id(result, "estimate_id")
    return result


def validate_estimate(value: Any) -> List[str]:
    required = {"schema_version", "estimate_id", "source", "status", "reason", "sender", "receiver", "task_type", "sample_count", "complete_sample_count", "minimum_samples", "components", "known_sample_counts"}
    if not isinstance(value, dict) or set(value) != required:
        return ["Handoff Tax estimate fields do not match contract"]
    errors = []
    if value.get("schema_version") != 1 or value.get("source") != "observed-handoff-events":
        errors.append("estimate version or source is invalid")
    if value.get("status") not in {"unknown", "canary", "calibrated"}:
        errors.append("estimate status is invalid")
    for field in ("sample_count", "complete_sample_count", "minimum_samples"):
        if not isinstance(value.get(field), int) or isinstance(value[field], bool) or value[field] < (1 if field == "minimum_samples" else 0):
            errors.append(f"{field} is invalid")
    components = value.get("components")
    expected_components = {*CORE_COMPONENTS, "repeated_payload_bytes", "receiver_reads", "receiver_searches", "context_cache_hit_rate"}
    if not isinstance(components, dict) or set(components) != expected_components:
        errors.append("estimate components do not match contract")
    elif any(item is not None and (not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0) for item in components.values()):
        errors.append("estimate components must be null or non-negative numbers")
    counts = value.get("known_sample_counts")
    if not isinstance(counts, dict) or set(counts) != expected_components or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in counts.values()):
        errors.append("known_sample_counts is invalid")
    if value.get("estimate_id") != document_id(value, "estimate_id"):
        errors.append("estimate_id does not match canonical content")
    return errors


POLICY_FIELDS = {
    "schema_version", "task_type", "direct_cost_units", "direct_active_seconds",
    "direct_codex_work_units", "serialization_cost_per_byte",
    "reconstruction_cost_per_second", "rediscovery_cost_per_item",
    "revision_cost_per_item", "codex_work_per_rediscovery", "codex_work_per_revision",
}
CALIBRATION_FIELDS = {
    "schema_version", "calibration_id", "source", "status", "reason", "task_type",
    "sample_count", "minimum_samples", "components", "handoff_cost_units",
    "penalty_cost_ratio", "penalty_active_elapsed_ratio", "penalty_codex_work_ratio",
    "policy", "estimate_ids", "selected_estimate_id",
}


def calibrate_estimates(estimates: Iterable[Dict[str, Any]], policy: Dict[str, Any], min_samples: int = 3) -> Dict[str, Any]:
    if not isinstance(min_samples, int) or isinstance(min_samples, bool) or min_samples < 1:
        raise HandoffRoutingError("min_samples must be positive")
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS or policy.get("schema_version") != 1:
        raise HandoffRoutingError("calibration policy fields do not match contract")
    numeric_fields = POLICY_FIELDS - {"schema_version", "task_type"}
    if not isinstance(policy.get("task_type"), str) or not policy["task_type"]:
        raise HandoffRoutingError("policy task_type must be non-empty")
    if any(not isinstance(policy.get(field), (int, float)) or isinstance(policy[field], bool) or policy[field] < 0 for field in numeric_fields):
        raise HandoffRoutingError("calibration policy costs must be non-negative numbers")
    if any(policy[field] <= 0 for field in ("direct_cost_units", "direct_active_seconds", "direct_codex_work_units")):
        raise HandoffRoutingError("direct baselines must be positive")
    unique = {}
    for estimate in estimates:
        errors = validate_estimate(estimate)
        if errors:
            raise HandoffRoutingError("invalid estimate: " + "; ".join(errors))
        unique[estimate["estimate_id"]] = estimate
    matching = [item for item in unique.values() if item["task_type"] in {None, policy["task_type"]}]
    selected = max(matching, key=lambda item: item["complete_sample_count"], default=None)
    complete_samples = selected["complete_sample_count"] if selected else 0
    medians = {
        name: selected["components"][name] if selected else None
        for name in CORE_COMPONENTS
    }
    complete_components = all(value is not None for value in medians.values())
    tax_cost = None
    if complete_components:
        tax_cost = round(
            medians["serialization_bytes"] * policy["serialization_cost_per_byte"]
            + medians["reconstruction_seconds"] * policy["reconstruction_cost_per_second"]
            + medians["rediscovery_count"] * policy["rediscovery_cost_per_item"]
            + medians["revision_count"] * policy["revision_cost_per_item"], 6,
        )
    if complete_samples >= min_samples and tax_cost is not None:
        status, reason = "calibrated", "observed-history-and-explicit-cost-policy"
    elif complete_samples:
        status, reason = "canary", "insufficient-observed-history"
    else:
        status, reason = "unknown", "no-complete-observed-history"
    codex_penalty = None if not complete_components else (
        medians["rediscovery_count"] * policy["codex_work_per_rediscovery"]
        + medians["revision_count"] * policy["codex_work_per_revision"]
    ) / policy["direct_codex_work_units"]
    result = {
        "schema_version": 1, "calibration_id": "", "source": "observed-calibration",
        "status": status, "reason": reason, "task_type": policy["task_type"],
        "sample_count": complete_samples, "minimum_samples": min_samples,
        "components": medians, "handoff_cost_units": tax_cost,
        "penalty_cost_ratio": round(tax_cost / policy["direct_cost_units"], 6) if tax_cost is not None else None,
        "penalty_active_elapsed_ratio": round(medians["reconstruction_seconds"] / policy["direct_active_seconds"], 6) if complete_components else None,
        "penalty_codex_work_ratio": round(codex_penalty, 6) if codex_penalty is not None else None,
        "policy": deepcopy(policy),
        "estimate_ids": sorted(item["estimate_id"] for item in matching),
        "selected_estimate_id": selected["estimate_id"] if selected else None,
    }
    result["calibration_id"] = document_id(result, "calibration_id")
    return result


def validate_calibration(value: Any) -> List[str]:
    if not isinstance(value, dict) or set(value) != CALIBRATION_FIELDS:
        return ["Handoff routing calibration fields do not match contract"]
    errors = []
    if value.get("schema_version") != 1 or value.get("source") != "observed-calibration":
        errors.append("calibration version or source is invalid")
    if value.get("status") not in {"unknown", "canary", "calibrated"}:
        errors.append("calibration status is invalid")
    for field in ("sample_count", "minimum_samples"):
        if not isinstance(value.get(field), int) or isinstance(value[field], bool) or value[field] < (1 if field == "minimum_samples" else 0):
            errors.append(f"{field} is invalid")
    if not isinstance(value.get("task_type"), str) or not value["task_type"]:
        errors.append("task_type is invalid")
    components = value.get("components")
    if not isinstance(components, dict) or set(components) != set(CORE_COMPONENTS):
        errors.append("calibration components do not match contract")
    elif any(item is not None and (not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0) for item in components.values()):
        errors.append("calibration components are invalid")
    for field in ("handoff_cost_units", "penalty_cost_ratio", "penalty_active_elapsed_ratio", "penalty_codex_work_ratio"):
        item = value.get(field)
        if item is not None and (not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0):
            errors.append(f"{field} is invalid")
    if not isinstance(value.get("policy"), dict) or set(value["policy"]) != POLICY_FIELDS:
        errors.append("calibration policy is invalid")
    ids = value.get("estimate_ids")
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or any(not isinstance(item, str) or not item.startswith(HASH_PREFIX) for item in ids):
        errors.append("estimate_ids is invalid")
    selected = value.get("selected_estimate_id")
    if selected is not None and selected not in (ids or []):
        errors.append("selected_estimate_id is not in estimate_ids")
    if value.get("status") == "calibrated" and (
        value.get("sample_count", 0) < value.get("minimum_samples", 1)
        or any(value.get(field) is None for field in ("handoff_cost_units", "penalty_cost_ratio", "penalty_active_elapsed_ratio", "penalty_codex_work_ratio"))
    ):
        errors.append("calibrated status lacks sufficient complete evidence")
    if value.get("calibration_id") != document_id(value, "calibration_id"):
        errors.append("calibration_id does not match canonical content")
    return errors


def load_json(path: Path, label: str) -> Any:
    try:
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            raise HandoffRoutingError(f"{label} is unsafe or exceeds 8 MiB")
        return json.loads(path.read_text(encoding="utf-8"))
    except HandoffRoutingError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffRoutingError(f"cannot read {label}: {exc}") from exc
