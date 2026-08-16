#!/usr/bin/env python3
"""Task Schema v1 — shared stdlib loader, validator, and profile composer.

Python 3.9+ compatible. No third-party dependencies.
The checked-in JSON Schema (schemas/task-card-v1.schema.json) is normative;
this module implements matching stdlib validation.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

VALID_MODES = ("builder", "checker-test", "mixed-exception", "control-plane")
VALID_RISK_VALUES = ("no", "yes", "unknown")

REQUIRED_TOP_LEVEL = [
    "schema_version", "id", "mode", "goal", "profiles", "scope",
    "acceptance", "risk", "handoff", "validation", "stop_conditions",
]

REQUIRED_SCOPE_FIELDS = ["write_paths"]
REQUIRED_ACCEPTANCE_FIELDS = ["id", "description"]
REQUIRED_HANDOFF_SECTIONS = []  # handoff sub-fields are all optional
REQUIRED_VALIDATION_FIELDS = ["id", "command"]

TOP_LEVEL_PROPERTY_NAMES = set(REQUIRED_TOP_LEVEL) | {"extensions"}

# Allowed nested keys (for rejecting unknown fields within sub-objects)
VALID_SCOPE_KEYS = {"write_paths", "read_paths", "forbidden_paths"}
VALID_ACCEPTANCE_KEYS = {"id", "description", "validation_id"}
VALID_RISK_KEYS = {
    "public_api", "data_model", "security", "migration",
    "permission", "concurrency", "cross_module", "production_impact",
}
# ``stop_condition`` remains readable for v1 audit compatibility. New task
# contracts put executable stops only in top-level ``stop_conditions``.
VALID_HANDOFF_KEYS = {"must_do", "must_not_do", "may_decide", "must_report", "stop_condition"}
VALID_VALIDATION_KEYS = {"id", "command", "description", "local_allowed"}
VALID_TASK_SHAPE_KEYS = {
    "responsibilities", "new_modules", "split_decision", "split_reason",
}
VALID_SPLIT_DECISIONS = ("split", "exception")
VALID_COMPLEX_GATE_KEYS = {
    "enabled", "counterexamples", "fail_closed_conditions",
    "not_applicable_reason",
}
COMPLEX_GATE_MARKERS = (
    "aggregation", "aggregate", "eligibility", "quorum", "fallback",
    "acceptance gate", "gate logic", "admission gate", "fail-closed",
    "聚合", "门禁", "门控", "验收逻辑", "资格", "回退", "失败关闭",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TaskSchemaError(Exception):
    """Base exception for task schema errors."""
    def __init__(self, message: str, path: str = ""):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class ValidationError(TaskSchemaError):
    """Schema validation failed."""
    pass


class ProfileConflictError(TaskSchemaError):
    """Profile composition encountered a conflict."""
    pass


class ProfileLoadError(TaskSchemaError):
    """Profile file could not be loaded."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_path(prefix: str, key: str) -> str:
    """Build a dotted JSON path."""
    if prefix:
        return f"{prefix}.{key}"
    return key


def _type_name(value: Any) -> str:
    """Return a human-readable type name."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _validate_text_array(
    errors: List[str], value: Any, path: str, *, minimum: int = 0
) -> None:
    """Validate a small machine-readable list of non-empty strings."""
    if not isinstance(value, list):
        errors.append(f"{path}: expected array")
        return
    if len(value) < minimum:
        errors.append(f"{path}: expected at least {minimum} item(s)")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{path}[{index}]: expected non-empty string")


def _validate_known_extensions(
    errors: List[str], extensions: Dict[str, Any], root: str
) -> None:
    """Validate extension contracts whose semantics are owned by aiwf core."""
    task_shape = extensions.get("task_shape")
    shape_path = f"{root}.extensions.task_shape"
    if task_shape is not None:
        if not isinstance(task_shape, dict):
            errors.append(f"{shape_path}: expected object")
        else:
            for key in task_shape:
                if key not in VALID_TASK_SHAPE_KEYS:
                    errors.append(f"{shape_path}: unknown key '{key}'")
            for key in ("responsibilities", "new_modules"):
                if key in task_shape:
                    _validate_text_array(errors, task_shape[key], f"{shape_path}.{key}")
            decision = task_shape.get("split_decision")
            if decision is not None and decision not in VALID_SPLIT_DECISIONS:
                errors.append(
                    f"{shape_path}.split_decision: expected one of "
                    f"{VALID_SPLIT_DECISIONS}, got '{decision}'"
                )
            reason = task_shape.get("split_reason")
            if decision is not None and (not isinstance(reason, str) or not reason.strip()):
                errors.append(
                    f"{shape_path}.split_reason: required when split_decision is set"
                )
            elif reason is not None and (not isinstance(reason, str) or not reason.strip()):
                errors.append(f"{shape_path}.split_reason: expected non-empty string")

    gate = extensions.get("complex_gate_contract")
    gate_path = f"{root}.extensions.complex_gate_contract"
    if gate is not None:
        if not isinstance(gate, dict):
            errors.append(f"{gate_path}: expected object")
            return
        for key in gate:
            if key not in VALID_COMPLEX_GATE_KEYS:
                errors.append(f"{gate_path}: unknown key '{key}'")
        enabled = gate.get("enabled")
        if not isinstance(enabled, bool):
            errors.append(f"{gate_path}.enabled: expected boolean")
        if enabled is True:
            _validate_text_array(
                errors, gate.get("counterexamples"),
                f"{gate_path}.counterexamples", minimum=2,
            )
            _validate_text_array(
                errors, gate.get("fail_closed_conditions"),
                f"{gate_path}.fail_closed_conditions", minimum=1,
            )
        elif enabled is False:
            reason = gate.get("not_applicable_reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(
                    f"{gate_path}.not_applicable_reason: required when enabled is false"
                )


def _requires_complex_gate_contract(data: Dict[str, Any]) -> bool:
    """Detect explicit aggregation/gate language without guessing architecture."""
    values = [data.get("goal", "")]
    acceptance = data.get("acceptance", [])
    if isinstance(acceptance, list):
        values.extend(
            item.get("description", "")
            for item in acceptance if isinstance(item, dict)
        )
    extensions = data.get("extensions", {})
    shape = extensions.get("task_shape", {}) if isinstance(extensions, dict) else {}
    if isinstance(shape, dict):
        responsibilities = shape.get("responsibilities", [])
        if isinstance(responsibilities, list):
            values.extend(responsibilities)
    text = "\n".join(str(value).lower() for value in values)
    return any(marker in text for marker in COMPLEX_GATE_MARKERS)


def assess_task_granularity(
    task: Dict[str, Any], repo: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Return a deterministic pre-dispatch split advisory for one JSON task.

    The gate is intentionally conservative: a single large dimension emits a
    strong advisory, while clearly oversized or compound work blocks model
    dispatch until the card is split or a reviewed exception is recorded.
    """
    scope = task.get("scope", {}) if isinstance(task.get("scope"), dict) else {}
    write_paths = [
        str(value).strip().replace("\\", "/")
        for value in scope.get("write_paths", [])
        if str(value).strip()
    ]
    extensions = task.get("extensions", {})
    extensions = extensions if isinstance(extensions, dict) else {}
    shape = extensions.get("task_shape", {})
    shape = shape if isinstance(shape, dict) else {}
    responsibilities = [
        str(value).strip() for value in shape.get("responsibilities", [])
        if str(value).strip()
    ]
    declared_new_modules = [
        str(value).strip().replace("\\", "/")
        for value in shape.get("new_modules", []) if str(value).strip()
    ]
    repo_path = Path(repo).resolve() if repo is not None else None
    inferred_new_modules: List[str] = []
    broad_paths: List[str] = []
    for path in write_paths:
        has_pattern = any(char in path for char in "*?[")
        candidate = repo_path / path if repo_path is not None else None
        is_directory = path.endswith("/") or bool(candidate and candidate.is_dir())
        if has_pattern or is_directory:
            broad_paths.append(path)
        elif candidate is not None and not candidate.exists():
            inferred_new_modules.append(path)

    new_modules = sorted(set(declared_new_modules + inferred_new_modules))
    responsibility_count = len(responsibilities) if responsibilities else 1
    write_path_count = len(write_paths)
    new_module_count = len(new_modules)

    blocking_reasons: List[str] = []
    advisory_reasons: List[str] = []
    if write_path_count >= 6:
        blocking_reasons.append("six-or-more-write-paths")
    elif write_path_count >= 4:
        advisory_reasons.append("four-or-more-write-paths")
    if responsibility_count >= 3:
        blocking_reasons.append("three-or-more-responsibilities")
    elif responsibility_count >= 2:
        advisory_reasons.append("multiple-responsibilities")
    if new_module_count >= 3:
        blocking_reasons.append("three-or-more-new-modules")
    elif new_module_count >= 2:
        advisory_reasons.append("multiple-new-modules")
    if write_path_count >= 4 and responsibility_count >= 2:
        blocking_reasons.append("compound-multi-file-task")
    if broad_paths:
        advisory_reasons.append("broad-write-scope")

    decision = str(shape.get("split_decision", "")).strip()
    reason = str(shape.get("split_reason", "")).strip()
    if decision == "split":
        blocking_reasons.append("reviewed-split-decision")
    split_required = bool(blocking_reasons)
    exception_applied = split_required and decision == "exception" and bool(reason)
    blocking = split_required and not exception_applied
    if blocking:
        status = "split-required"
        action = "split-task-before-dispatch"
    elif exception_applied:
        status = "split-exception-reviewed"
        action = "proceed-with-recorded-exception"
    elif advisory_reasons:
        status = "split-advised"
        action = "review-task-shape-before-dispatch"
    else:
        status = "ready"
        action = "proceed"
    return {
        "schema_version": 1,
        "status": status,
        "blocking": blocking,
        "action": action,
        "write_path_count": write_path_count,
        "responsibility_count": responsibility_count,
        "new_module_count": new_module_count,
        "broad_write_paths": sorted(set(broad_paths)),
        "responsibilities": responsibilities or [task.get("goal", "task responsibility")],
        "new_modules": new_modules,
        "reason_codes": sorted(set(blocking_reasons + advisory_reasons)),
        "blocking_reason_codes": sorted(set(blocking_reasons)),
        "split_decision": decision or None,
        "split_reason": reason or None,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_task(data: Any, path: str = "") -> List[str]:
    """Validate a task instance against the v1 schema rules.

    Returns a list of error strings (empty if valid).
    Uses stdlib only — no jsonschema dependency.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return [f"{path or '<root>'}: expected object, got {_type_name(data)}"]

    root = path or "<root>"

    # Check for unknown top-level fields
    for key in data:
        if key not in TOP_LEVEL_PROPERTY_NAMES:
            errors.append(f"{root}: unknown top-level field '{key}'")

    # schema_version
    if "schema_version" not in data:
        errors.append(f"{root}: missing required field 'schema_version'")
    elif data["schema_version"] != SCHEMA_VERSION:
        errors.append(f"{root}.schema_version: expected {SCHEMA_VERSION}, got {data['schema_version']}")

    # id
    if "id" not in data:
        errors.append(f"{root}: missing required field 'id'")
    elif not isinstance(data["id"], str) or not data["id"]:
        errors.append(f"{root}.id: expected non-empty string")

    # mode
    if "mode" not in data:
        errors.append(f"{root}: missing required field 'mode'")
    elif data["mode"] not in VALID_MODES:
        errors.append(f"{root}.mode: expected one of {VALID_MODES}, got '{data['mode']}'")

    # goal
    if "goal" not in data:
        errors.append(f"{root}: missing required field 'goal'")
    elif not isinstance(data["goal"], str) or not data["goal"]:
        errors.append(f"{root}.goal: expected non-empty string")

    # profiles
    if "profiles" not in data:
        errors.append(f"{root}: missing required field 'profiles'")
    elif not isinstance(data["profiles"], list):
        errors.append(f"{root}.profiles: expected array")
    elif not data["profiles"]:
        errors.append(f"{root}.profiles: expected non-empty array")
    else:
        for i, p in enumerate(data["profiles"]):
            if not isinstance(p, str) or not p:
                errors.append(f"{root}.profiles[{i}]: expected non-empty string")

    # scope
    if "scope" not in data:
        errors.append(f"{root}: missing required field 'scope'")
    elif not isinstance(data["scope"], dict):
        errors.append(f"{root}.scope: expected object")
    else:
        scope = data["scope"]
        for key in scope:
            if key not in VALID_SCOPE_KEYS:
                errors.append(f"{_json_path(root, 'scope')}: unknown key '{key}'")
        for f in REQUIRED_SCOPE_FIELDS:
            if f not in scope:
                errors.append(f"{_json_path(root, 'scope')}: missing required field '{f}'")
        if "write_paths" in scope:
            if not isinstance(scope["write_paths"], list):
                errors.append(f"{_json_path(root, 'scope')}.write_paths: expected array")
            elif not scope["write_paths"]:
                errors.append(f"{_json_path(root, 'scope')}.write_paths: expected non-empty array")
            else:
                for i, p in enumerate(scope["write_paths"]):
                    if not isinstance(p, str) or not p:
                        errors.append(f"{_json_path(root, 'scope')}.write_paths[{i}]: expected non-empty string")
        for optional_field in ("read_paths", "forbidden_paths"):
            if optional_field in scope:
                if not isinstance(scope[optional_field], list):
                    errors.append(f"{_json_path(root, 'scope')}.{optional_field}: expected array")

    # acceptance
    if "acceptance" not in data:
        errors.append(f"{root}: missing required field 'acceptance'")
    elif not isinstance(data["acceptance"], list):
        errors.append(f"{root}.acceptance: expected array")
    elif not data["acceptance"]:
        errors.append(f"{root}.acceptance: expected non-empty array")
    else:
        seen_acceptance_ids: set[str] = set()
        for i, item in enumerate(data["acceptance"]):
            ap = f"{root}.acceptance[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{ap}: expected object")
                continue
            for key in item:
                if key not in VALID_ACCEPTANCE_KEYS:
                    errors.append(f"{ap}: unknown key '{key}'")
            for f in REQUIRED_ACCEPTANCE_FIELDS:
                if f not in item:
                    errors.append(f"{ap}: missing required field '{f}'")
                elif not isinstance(item[f], str) or not item[f]:
                    errors.append(f"{ap}.{f}: expected non-empty string")
            if "id" in item and isinstance(item["id"], str) and item["id"]:
                if item["id"] in seen_acceptance_ids:
                    errors.append(f"{ap}: duplicate acceptance id '{item['id']}'")
                seen_acceptance_ids.add(item["id"])
            if "validation_id" in item:
                vid = item["validation_id"]
                if not isinstance(vid, str) or not vid:
                    errors.append(f"{ap}.validation_id: expected non-empty string")

    # risk
    if "risk" not in data:
        errors.append(f"{root}: missing required field 'risk'")
    elif not isinstance(data["risk"], dict):
        errors.append(f"{root}.risk: expected object")
    else:
        for key in data["risk"]:
            if key not in VALID_RISK_KEYS:
                errors.append(f"{_json_path(root, 'risk')}: unknown key '{key}'")
        for key, val in data["risk"].items():
            if val not in VALID_RISK_VALUES:
                errors.append(f"{_json_path(root, 'risk')}.{key}: expected one of {VALID_RISK_VALUES}, got '{val}'")

    # handoff
    if "handoff" not in data:
        errors.append(f"{root}: missing required field 'handoff'")
    elif not isinstance(data["handoff"], dict):
        errors.append(f"{root}.handoff: expected object")
    else:
        for key in data["handoff"]:
            if key not in VALID_HANDOFF_KEYS:
                errors.append(f"{_json_path(root, 'handoff')}: unknown key '{key}'")
        for key, val in data["handoff"].items():
            if not isinstance(val, list):
                errors.append(f"{_json_path(root, 'handoff')}.{key}: expected array")
            else:
                for i, item in enumerate(val):
                    if not isinstance(item, str) or not item:
                        errors.append(f"{_json_path(root, 'handoff')}.{key}[{i}]: expected non-empty string")

    # validation
    if "validation" not in data:
        errors.append(f"{root}: missing required field 'validation'")
    elif not isinstance(data["validation"], list):
        errors.append(f"{root}.validation: expected array")
    else:
        seen_validation_ids: set[str] = set()
        for i, item in enumerate(data["validation"]):
            vp = f"{root}.validation[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{vp}: expected object")
                continue
            for key in item:
                if key not in VALID_VALIDATION_KEYS:
                    errors.append(f"{vp}: unknown key '{key}'")
            for f in REQUIRED_VALIDATION_FIELDS:
                if f not in item:
                    errors.append(f"{vp}: missing required field '{f}'")
            if "id" in item and isinstance(item["id"], str) and item["id"]:
                if item["id"] in seen_validation_ids:
                    errors.append(f"{vp}: duplicate validation id '{item['id']}'")
                seen_validation_ids.add(item["id"])
            if "command" in item:
                cmd = item["command"]
                if not isinstance(cmd, list):
                    errors.append(f"{vp}.command: expected array (argv)")
                elif not cmd:
                    errors.append(f"{vp}.command: expected non-empty array")
                else:
                    for j, arg in enumerate(cmd):
                        if not isinstance(arg, str):
                            errors.append(f"{vp}.command[{j}]: expected string")
                        elif not arg:
                            errors.append(f"{vp}.command[{j}]: expected non-empty string")

        # Cross-reference: every acceptance validation_id must reference an existing validation id
        if "acceptance" in data and isinstance(data["acceptance"], list):
            for i, item in enumerate(data["acceptance"]):
                if not isinstance(item, dict):
                    continue
                vid = item.get("validation_id")
                if vid is not None:
                    if vid not in seen_validation_ids:
                        errors.append(
                            f"{root}.acceptance[{i}].validation_id: "
                            f"references unknown validation id '{vid}'"
                        )

    # stop_conditions
    if "stop_conditions" not in data:
        errors.append(f"{root}: missing required field 'stop_conditions'")
    elif not isinstance(data["stop_conditions"], list):
        errors.append(f"{root}.stop_conditions: expected array")
    else:
        for i, item in enumerate(data["stop_conditions"]):
            if not isinstance(item, str) or not item:
                errors.append(f"{root}.stop_conditions[{i}]: expected non-empty string")

    # extensions are open for profiles, while core-owned contracts are strict.
    if "extensions" in data and not isinstance(data["extensions"], dict):
        errors.append(f"{root}.extensions: expected object")
    elif isinstance(data.get("extensions"), dict):
        _validate_known_extensions(errors, data["extensions"], root)
    extensions = data.get("extensions")
    if _requires_complex_gate_contract(data) and not (
        isinstance(extensions, dict) and "complex_gate_contract" in extensions
    ):
        errors.append(
            f"{root}.extensions.complex_gate_contract: required for explicit "
            "aggregation/gate/fallback semantics"
        )

    return errors


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_profile(name: str, profiles_dir: Union[str, Path]) -> Dict[str, Any]:
    """Load a profile by name from the profiles directory.

    Raises ProfileLoadError if not found or invalid.
    """
    profiles_dir = Path(profiles_dir)
    profile_path = profiles_dir / f"{name}.json"
    if not profile_path.is_file():
        raise ProfileLoadError(f"Profile not found: {profile_path}")

    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ProfileLoadError(f"Invalid JSON in {profile_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ProfileLoadError(f"Profile must be an object: {profile_path}")

    if "name" not in data:
        raise ProfileLoadError(f"Profile missing 'name' field: {profile_path}")

    if "profile_version" not in data:
        raise ProfileLoadError(f"Profile missing 'profile_version' field: {profile_path}")

    if data["name"] != name:
        raise ProfileLoadError(
            f"Profile name '{data['name']}' does not match filename '{name}': {profile_path}"
        )

    return data


# ---------------------------------------------------------------------------
# Profile composition (deterministic, fail-closed)
# ---------------------------------------------------------------------------

def _merge_scalars(existing: Any, incoming: Any, path: str) -> Any:
    """Merge two scalar values. Reject unless identical."""
    if existing == incoming:
        return existing
    raise ProfileConflictError(
        f"conflicting scalar values at {path}: {_type_name(existing)}({existing!r}) vs {_type_name(incoming)}({incoming!r})"
    )


def _merge_arrays_of_scalars(existing: List[Any], incoming: List[Any], path: str) -> List[Any]:
    """Stable-deduplicate arrays of scalar values."""
    seen: List[Any] = []
    for item in existing + incoming:
        if item not in seen:
            seen.append(item)
    return seen


def _merge_arrays_of_objects(existing: List[Dict], incoming: List[Dict], path: str) -> List[Dict]:
    """Merge arrays of objects with 'id' by id, rejecting conflicts."""
    by_id: Dict[str, Dict] = {}
    for item in existing:
        if "id" not in item:
            raise ProfileConflictError(f"object in array at {path} missing 'id' field")
        by_id[item["id"]] = deepcopy(item)

    for item in incoming:
        if "id" not in item:
            raise ProfileConflictError(f"object in array at {path} missing 'id' field")
        item_id = item["id"]
        if item_id in by_id:
            merged = _deep_merge(by_id[item_id], item, _json_path(path, f"[id={item_id}]"))
            by_id[item_id] = merged
        else:
            by_id[item_id] = deepcopy(item)

    return list(by_id.values())


def _deep_merge(base: Any, override: Any, path: str) -> Any:
    """Recursively merge override into base.

    Rules:
    - Objects: recursive merge
    - Arrays of scalars: stable-deduplicate
    - Arrays of objects with 'id': merge by id, reject conflicts
    - Scalars: reject unless identical
    - Incompatible types: reject
    """
    if type(base) is not type(override):
        raise ProfileConflictError(
            f"incompatible types at {path}: {_type_name(base)} vs {_type_name(override)}"
        )

    if isinstance(base, dict):
        result = deepcopy(base)
        for key in override:
            if key in result:
                result[key] = _deep_merge(result[key], override[key], _json_path(path, key))
            else:
                result[key] = deepcopy(override[key])
        return result

    if isinstance(base, list):
        combined = base + override
        if not combined:
            return []

        # Object arrays are a structured contract even when one side is
        # empty. Validate every element before any empty-side shortcut can
        # bypass the required stable id.
        if any(isinstance(item, dict) for item in combined):
            if not all(isinstance(item, dict) for item in combined):
                raise ProfileConflictError(
                    f"mixed scalar/object array at {path}"
                )
            for item in combined:
                if not isinstance(item.get("id"), str) or not item["id"]:
                    raise ProfileConflictError(
                        f"object in array at {path} missing 'id' field or id is empty"
                    )
            return _merge_arrays_of_objects(base, override, path)

        # Arrays of scalars
        return _merge_arrays_of_scalars(base, override, path)

    # Scalars
    return _merge_scalars(base, override, path)


def compose_profiles(
    profile_names: List[str],
    profiles_dir: Union[str, Path],
    task_instance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compose profiles in order and merge with task instance.

    Profile merge order: first profile is lowest priority, last is highest.
    Task instance values may fill missing fields but may not silently override
    a conflicting profile contract.

    Returns the composed result.
    Raises ProfileConflictError on conflicts.
    Raises ProfileLoadError on missing/invalid profiles.
    """
    # Load and merge profiles
    composed: Dict[str, Any] = {}
    for name in profile_names:
        profile = load_profile(name, profiles_dir)
        # Remove profile metadata before merging
        profile_data = {k: v for k, v in profile.items() if k not in ("name", "description", "profile_version")}
        composed = _deep_merge(composed, profile_data, f"profile:{name}")

    # Merge with task instance
    if task_instance is not None:
        composed = _deep_merge(composed, task_instance, "task")
        errors = validate_task(composed)
        if errors:
            raise ValidationError("Composed task invalid: " + "; ".join(errors))

    return composed


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_section(title: str, content: str, level: int = 2) -> str:
    """Render a Markdown section."""
    prefix = "#" * level
    return f"{prefix} {title}\n\n{content}\n"


def _render_table(headers: List[str], rows: List[List[str]]) -> str:
    """Render a Markdown table."""
    if not rows:
        return ""
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _render_list(items: List[str], ordered: bool = False) -> str:
    """Render a Markdown list."""
    if not items:
        return "_(none)_"
    lines = []
    for i, item in enumerate(items):
        prefix = f"{i+1}." if ordered else "-"
        lines.append(f"{prefix} {item}")
    return "\n".join(lines)


def _text_items(value: Any) -> List[str]:
    """Return non-empty text values without inventing execution context."""
    if isinstance(value, (list, tuple)):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _metadata_token(value: Any, fallback: str) -> str:
    """Render a bounded machine-readable token for the execution-card header."""
    rendered = "".join(
        char.lower() if char.isalnum() or char in "-_" else "-"
        for char in str(value or "")
    ).strip("-_")
    return rendered or fallback


def _render_execution_context(context: Dict[str, Any]) -> str:
    """Render only task-specific context that is actually available."""
    entries = [
        ("Exact symbols/tests", context.get("symbols")),
        ("Interface signatures", context.get("interface_signatures")),
        ("Runnable call example", context.get("runnable_examples")),
        ("Async/sync contract", context.get("async_contract")),
        ("Root-cause evidence", context.get("root_cause_evidence")),
        ("Reference implementation", context.get("source_of_truth_example")),
        ("Known constraints", context.get("constraints")),
    ]
    parts = []
    for label, value in entries:
        values = _text_items(value)
        if not values:
            continue
        if len(values) == 1:
            parts.append("**{}:** {}".format(label, values[0]))
        else:
            parts.append("**{}:**\n{}".format(label, _render_list(values)))

    # Batch units are not a second scope list: they are only emitted when the
    # router resolved independent units for a batch-specific assignment.
    if context.get("builder_mode") == "batch":
        transformation = _text_items(context.get("transformation_rule"))
        units = _text_items(context.get("independent_write_units"))
        if transformation:
            parts.append("**Transformation rule:** {}".format(transformation[0]))
        if units:
            parts.append("**Independent write units:**\n{}".format(_render_list(units)))
    return "\n\n".join(parts)


def _render_complex_gate_contract(task: Dict[str, Any]) -> str:
    extensions = task.get("extensions", {})
    extensions = extensions if isinstance(extensions, dict) else {}
    contract = extensions.get("complex_gate_contract")
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        return ""
    return "\n\n".join((
        "**Counterexamples that must remain rejected:**\n{}".format(
            _render_list(_text_items(contract.get("counterexamples")))
        ),
        "**Fail-closed conditions:**\n{}".format(
            _render_list(_text_items(contract.get("fail_closed_conditions")))
        ),
        "If required evidence is missing, contradictory, or unparseable, preserve the "
        "rejected/pending state; do not infer success from partial inputs.",
    ))


def _render_task_granularity(context: Dict[str, Any]) -> str:
    assessment = context.get("task_granularity")
    if not isinstance(assessment, dict) or assessment.get("status") == "ready":
        return ""
    lines = [
        "**Status:** {}".format(assessment.get("status", "unknown")),
        "**Required action:** {}".format(assessment.get("action", "review")),
        "**Shape:** write paths={}, responsibilities={}, new modules={}".format(
            assessment.get("write_path_count", 0),
            assessment.get("responsibility_count", 0),
            assessment.get("new_module_count", 0),
        ),
        "**Reason codes:** {}".format(
            ", ".join(_text_items(assessment.get("reason_codes"))) or "none"
        ),
    ]
    if assessment.get("split_reason"):
        lines.append("**Reviewed exception:** {}".format(assessment["split_reason"]))
    return "\n\n".join(lines)


def _render_execution_task_card(
    task: Dict[str, Any], execution_context: Optional[Dict[str, Any]]
) -> str:
    """Project one reviewed JSON task into a compact Claude execution card.

    This is deliberately not an editable second contract.  It retains only
    execution-relevant JSON fields plus conditional routing facts that cannot
    be represented by task scope (for example an exact symbol or interface
    signature).  Static builder protocol lives in the dispatcher prompt.
    """
    context = execution_context if isinstance(execution_context, dict) else {}
    task_mode = _metadata_token(context.get("task_mode", task.get("mode")), "unknown")
    builder_mode = _metadata_token(context.get("builder_mode"), "standard")
    sections = [
        "<!-- aiwf-execution-card-v1; task-mode={}; builder-mode={} -->".format(
            task_mode, builder_mode
        ),
        "# Task: {}".format(task.get("id", "unknown")),
        _render_section("Goal", str(task.get("goal", ""))),
    ]

    scope = task.get("scope", {}) if isinstance(task.get("scope"), dict) else {}
    scope_parts = []
    for key, label in (
        ("write_paths", "Write paths"),
        ("read_paths", "Read paths"),
        ("forbidden_paths", "Forbidden paths"),
    ):
        values = _text_items(scope.get(key))
        if values:
            scope_parts.append("**{}:**\n{}".format(label, _render_list(values)))
    if scope_parts:
        sections.append(_render_section("Scope", "\n\n".join(scope_parts)))

    context_body = _render_execution_context(context)
    if context_body:
        sections.append(_render_section("Execution Context", context_body))

    granularity_body = _render_task_granularity(context)
    if granularity_body:
        sections.append(_render_section("Task Granularity", granularity_body))

    complex_gate_body = _render_complex_gate_contract(task)
    if complex_gate_body:
        sections.append(_render_section("Complex Gate Contract", complex_gate_body))

    acceptance = task.get("acceptance", [])
    if isinstance(acceptance, list) and acceptance:
        rows = [
            [item.get("id", ""), item.get("description", "")]
            for item in acceptance if isinstance(item, dict)
        ]
        if rows:
            sections.append(
                _render_section("Acceptance Criteria", _render_table(["ID", "Description"], rows))
            )

    validation = task.get("validation", [])
    if isinstance(validation, list) and validation:
        lines = []
        for item in validation:
            if not isinstance(item, dict):
                continue
            command = " ".join(_text_items(item.get("command")))
            if not command:
                continue
            label = str(item.get("id", "validation"))
            description = str(item.get("description", "")).strip()
            local_note = " (local execution disabled)" if item.get("local_allowed") is False else ""
            suffix = " — {}".format(description) if description else ""
            lines.append("- **{}:** `{}`{}{}".format(label, command, suffix, local_note))
        if lines:
            sections.append(_render_section("Validation Contract", "\n".join(lines)))

    # ``handoff.stop_condition`` is a legacy v1 audit field. The top-level
    # list is the sole execution source, preserving old input compatibility
    # without reproducing the same stop semantics in the projection.
    stop_conditions = _text_items(task.get("stop_conditions"))
    if stop_conditions:
        sections.append(_render_section("Stop Conditions", _render_list(stop_conditions)))

    return "\n".join(sections)


def render_task_card(
    task: Dict[str, Any],
    view: str = "audit",
    include_sections: Optional[List[str]] = None,
    execution_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a task card as Markdown.

    view='audit': include all sections (for human review).
    view='execution': include only execution-relevant sections (for Claude).
    """
    if view == "execution":
        return _render_execution_task_card(task, execution_context)

    sections: List[str] = []

    # Header
    sections.append(f"# Task Card: {task.get('goal', 'Untitled')}\n")

    # Identity
    identity_rows = [
        ["ID", task.get("id", "")],
        ["Mode", task.get("mode", "")],
        ["Schema Version", str(task.get("schema_version", ""))],
        ["Profiles", ", ".join(task.get("profiles", []))],
    ]
    sections.append(_render_section("Task Identity", _render_table(["Field", "Value"], identity_rows)))

    # Goal
    sections.append(_render_section("Goal", task.get("goal", "")))

    # Scope
    scope = task.get("scope", {})
    scope_parts = []
    if "write_paths" in scope:
        scope_parts.append("**Write paths:**\n" + _render_list(scope["write_paths"]))
    if "read_paths" in scope:
        scope_parts.append("**Read paths:**\n" + _render_list(scope["read_paths"]))
    if "forbidden_paths" in scope:
        scope_parts.append("**Forbidden paths:**\n" + _render_list(scope["forbidden_paths"]))
    sections.append(_render_section("Scope", "\n\n".join(scope_parts)))

    # Acceptance
    acceptance = task.get("acceptance", [])
    if acceptance:
        rows = [[a.get("id", ""), a.get("description", ""), a.get("validation_id", "")] for a in acceptance]
        sections.append(_render_section("Acceptance Criteria", _render_table(["ID", "Description", "Validation"], rows)))

    # Risk
    risk = task.get("risk", {})
    risk_rows = [[k, v] for k, v in risk.items()]
    sections.append(_render_section("Risk Assessment", _render_table(["Category", "Value"], risk_rows)))

    # Handoff
    handoff = task.get("handoff", {})
    if handoff:
        handoff_parts = []
        for key in ("must_do", "must_not_do", "may_decide", "must_report", "stop_condition"):
            if key in handoff:
                label = key.replace("_", " ").title()
                handoff_parts.append(f"**{label}:**\n" + _render_list(handoff[key]))
        sections.append(_render_section("Handoff Contract", "\n\n".join(handoff_parts)))

    # Validation
    validation = task.get("validation", [])
    if validation:
        rows = []
        for v in validation:
            cmd = " ".join(v.get("command", []))
            rows.append([v.get("id", ""), cmd, v.get("description", "")])
        sections.append(_render_section("Validation", _render_table(["ID", "Command", "Description"], rows)))

    # Stop conditions
    stop = task.get("stop_conditions", [])
    if stop:
        sections.append(_render_section("Stop Conditions", _render_list(stop)))

    # Extensions are audit-only and only appear when present and non-empty.
    extensions = task.get("extensions", {})
    if extensions:
        active_parts = []
        for ext_name, ext_data in extensions.items():
            if isinstance(ext_data, dict) and ext_data.get("enabled") is False:
                continue
            active_parts.append(f"**{ext_name}:**\n```json\n{json.dumps(ext_data, indent=2)}\n```")
        if active_parts:
            sections.append(_render_section("Extensions", "\n\n".join(active_parts)))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def find_default_profiles_dir() -> Path:
    """Find the default profiles directory relative to this script.

    Checks source-checkout layout first (<repo>/profiles/), then
    installed layout (<repo>/ai/profiles/). Returns the first that
    exists; falls back to source-checkout path for determinism.
    """
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    source_path = repo_root / "profiles"
    installed_path = repo_root / "ai" / "profiles"
    if source_path.is_dir():
        return source_path
    if installed_path.is_dir():
        return installed_path
    return source_path


def find_default_schema_path() -> Path:
    """Find the default schema path relative to this script.

    Checks source-checkout layout first (<repo>/schemas/), then
    installed layout (<repo>/ai/schemas/). Returns the first that
    exists; falls back to source-checkout path for determinism.
    """
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    source_path = repo_root / "schemas" / "task-card-v1.schema.json"
    installed_path = repo_root / "ai" / "schemas" / "task-card-v1.schema.json"
    if source_path.is_file():
        return source_path
    if installed_path.is_file():
        return installed_path
    return source_path


def load_task_json(path: Union[str, Path]) -> Dict[str, Any]:
    """Load a task JSON file.

    Raises ValidationError on invalid JSON.
    """
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"Task file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"Task file must contain a JSON object: {path}")
    return data


def write_output(content: str, output: Optional[Union[str, Path]] = None) -> None:
    """Write content to output path or stdout."""
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        sys.stdout.write(content)
