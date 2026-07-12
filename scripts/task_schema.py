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
        for i, item in enumerate(data["acceptance"]):
            ap = f"{root}.acceptance[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{ap}: expected object")
                continue
            for f in REQUIRED_ACCEPTANCE_FIELDS:
                if f not in item:
                    errors.append(f"{ap}: missing required field '{f}'")
                elif not isinstance(item[f], str) or not item[f]:
                    errors.append(f"{ap}.{f}: expected non-empty string")

    # risk
    if "risk" not in data:
        errors.append(f"{root}: missing required field 'risk'")
    elif not isinstance(data["risk"], dict):
        errors.append(f"{root}.risk: expected object")
    else:
        for key, val in data["risk"].items():
            if val not in VALID_RISK_VALUES:
                errors.append(f"{_json_path(root, 'risk')}.{key}: expected one of {VALID_RISK_VALUES}, got '{val}'")

    # handoff
    if "handoff" not in data:
        errors.append(f"{root}: missing required field 'handoff'")
    elif not isinstance(data["handoff"], dict):
        errors.append(f"{root}.handoff: expected object")

    # validation
    if "validation" not in data:
        errors.append(f"{root}: missing required field 'validation'")
    elif not isinstance(data["validation"], list):
        errors.append(f"{root}.validation: expected array")
    else:
        for i, item in enumerate(data["validation"]):
            vp = f"{root}.validation[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{vp}: expected object")
                continue
            for f in REQUIRED_VALIDATION_FIELDS:
                if f not in item:
                    errors.append(f"{vp}: missing required field '{f}'")
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

    # stop_conditions
    if "stop_conditions" not in data:
        errors.append(f"{root}: missing required field 'stop_conditions'")
    elif not isinstance(data["stop_conditions"], list):
        errors.append(f"{root}.stop_conditions: expected array")
    else:
        for i, item in enumerate(data["stop_conditions"]):
            if not isinstance(item, str) or not item:
                errors.append(f"{root}.stop_conditions[{i}]: expected non-empty string")

    # extensions (optional, any shape allowed)
    if "extensions" in data and not isinstance(data["extensions"], dict):
        errors.append(f"{root}.extensions: expected object")

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
    if type(base) != type(override):
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
        if not base and not override:
            return []
        if not base:
            return deepcopy(override)
        if not override:
            return deepcopy(base)

        # Determine if arrays contain objects with 'id'
        first_base = base[0]
        first_override = override[0]
        if isinstance(first_base, dict) and "id" in first_base:
            return _merge_arrays_of_objects(base, override, path)
        if isinstance(first_override, dict) and "id" in first_override:
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


def render_task_card(
    task: Dict[str, Any],
    view: str = "audit",
    include_sections: Optional[List[str]] = None,
) -> str:
    """Render a task card as Markdown.

    view='audit': include all sections (for human review).
    view='execution': include only execution-relevant sections (for Claude).
    """
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
    if view == "audit":
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

    # Extensions (audit view only, and only if present and non-empty)
    if view == "audit":
        extensions = task.get("extensions", {})
        if extensions:
            # Only render active extensions (those with enabled=true or meaningful content)
            active_parts = []
            for ext_name, ext_data in extensions.items():
                if isinstance(ext_data, dict) and ext_data.get("enabled") is False:
                    continue  # Skip disabled extensions
                active_parts.append(f"**{ext_name}:**\n```json\n{json.dumps(ext_data, indent=2)}\n```")
            if active_parts:
                sections.append(_render_section("Extensions", "\n\n".join(active_parts)))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def find_default_profiles_dir() -> Path:
    """Find the default profiles directory relative to this script."""
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    return repo_root / "profiles"


def find_default_schema_path() -> Path:
    """Find the default schema path relative to this script."""
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    return repo_root / "schemas" / "task-card-v1.schema.json"


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
