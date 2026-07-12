#!/usr/bin/env python3
"""Review Decision v1 — shared stdlib loader, validator, and extractor.

Python 3.9+ compatible. No third-party dependencies.
The checked-in JSON Schema (schemas/review-decision-v1.schema.json) is normative;
this module implements matching stdlib validation.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

VALID_DECISIONS = ("accept", "revise", "split", "reject")
VALID_SCOPES = ("phase", "whole-task")
VALID_DIRECTION_STATUSES = ("accepted", "rejected", "needs-revision", "not-applicable")
VALID_ACCEPTANCE_STATUSES = ("satisfied", "failed", "partial", "not-evaluated")
VALID_VALIDATION_STATUSES = ("passed", "failed", "partial", "not-run")
VALID_MODES = ("builder", "checker-test", "mixed-exception", "control-plane")

REQUIRED_TOP_LEVEL = [
    "schema_version", "decision", "scope", "reasoning",
    "direction", "acceptance", "validation", "next_task", "lessons",
]

TOP_LEVEL_PROPERTY_NAMES = set(REQUIRED_TOP_LEVEL)

VALID_ACCEPTANCE_KEYS = {"id", "status", "evidence"}
VALID_DIRECTION_KEYS = {"status"}
VALID_VALIDATION_KEYS = {"status", "failed_checks"}
VALID_NEXT_TASK_KEYS = {"mode", "goal", "acceptance", "scope", "profile"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ReviewDecisionError(Exception):
    """Base exception for review decision errors."""
    def __init__(self, message: str, path: str = ""):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class ValidationError(ReviewDecisionError):
    """Schema validation failed."""
    pass


class ExtractionError(ReviewDecisionError):
    """Could not extract a valid JSON object from review text."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _json_path(prefix: str, key: str) -> str:
    """Build a dotted JSON path."""
    if prefix:
        return f"{prefix}.{key}"
    return key


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_decision_json(text: str) -> Dict[str, Any]:
    """Extract exactly one review decision JSON object from review text.

    The JSON may be the entire text, or inside exactly one fenced ```json block.
    Rejects zero matches, multiple matches, malformed JSON, or non-object values.

    Raises ExtractionError on any failure.
    """
    candidates: List[Dict[str, Any]] = []

    # Strategy 1: try the entire text as JSON
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                candidates.append(obj)
        except json.JSONDecodeError:
            pass

    # Strategy 2: extract from fenced json blocks
    fenced_pattern = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
    for match in fenced_pattern.finditer(text):
        block = match.group(1).strip()
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                candidates.append(obj)
        except json.JSONDecodeError:
            pass

    if not candidates:
        raise ExtractionError("No valid JSON object found in review text")

    if len(candidates) > 1:
        raise ExtractionError(
            f"Found {len(candidates)} JSON objects; expected exactly one"
        )

    return candidates[0]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_decision(data: Any, path: str = "") -> List[str]:
    """Validate a review decision instance against the v1 schema rules.

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
        errors.append(
            f"{root}.schema_version: expected {SCHEMA_VERSION}, got {data['schema_version']}"
        )

    # decision
    if "decision" not in data:
        errors.append(f"{root}: missing required field 'decision'")
    elif data["decision"] not in VALID_DECISIONS:
        errors.append(
            f"{root}.decision: expected one of {VALID_DECISIONS}, got '{data['decision']}'"
        )

    # scope
    if "scope" not in data:
        errors.append(f"{root}: missing required field 'scope'")
    elif data["scope"] not in VALID_SCOPES:
        errors.append(
            f"{root}.scope: expected one of {VALID_SCOPES}, got '{data['scope']}'"
        )

    # reasoning
    if "reasoning" not in data:
        errors.append(f"{root}: missing required field 'reasoning'")
    elif not isinstance(data["reasoning"], str) or not data["reasoning"]:
        errors.append(f"{root}.reasoning: expected non-empty string")

    # direction
    if "direction" not in data:
        errors.append(f"{root}: missing required field 'direction'")
    elif not isinstance(data["direction"], dict):
        errors.append(f"{root}.direction: expected object")
    else:
        direction = data["direction"]
        for key in direction:
            if key not in VALID_DIRECTION_KEYS:
                errors.append(f"{_json_path(root, 'direction')}: unknown key '{key}'")
        if "status" not in direction:
            errors.append(f"{_json_path(root, 'direction')}: missing required field 'status'")
        elif direction["status"] not in VALID_DIRECTION_STATUSES:
            errors.append(
                f"{_json_path(root, 'direction')}.status: "
                f"expected one of {VALID_DIRECTION_STATUSES}, got '{direction['status']}'"
            )

    # acceptance
    if "acceptance" not in data:
        errors.append(f"{root}: missing required field 'acceptance'")
    elif not isinstance(data["acceptance"], list):
        errors.append(f"{root}.acceptance: expected array")
    else:
        seen_ids: set[str] = set()
        for i, item in enumerate(data["acceptance"]):
            ap = f"{root}.acceptance[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{ap}: expected object")
                continue
            for key in item:
                if key not in VALID_ACCEPTANCE_KEYS:
                    errors.append(f"{ap}: unknown key '{key}'")
            # id
            if "id" not in item:
                errors.append(f"{ap}: missing required field 'id'")
            elif not isinstance(item["id"], str) or not item["id"]:
                errors.append(f"{ap}.id: expected non-empty string")
            elif item["id"] in seen_ids:
                errors.append(f"{ap}: duplicate acceptance id '{item['id']}'")
            else:
                seen_ids.add(item["id"])
            # status
            if "status" not in item:
                errors.append(f"{ap}: missing required field 'status'")
            elif item["status"] not in VALID_ACCEPTANCE_STATUSES:
                errors.append(
                    f"{ap}.status: expected one of {VALID_ACCEPTANCE_STATUSES}, "
                    f"got '{item['status']}'"
                )
            # evidence
            if "evidence" not in item:
                errors.append(f"{ap}: missing required field 'evidence'")
            elif not isinstance(item["evidence"], list):
                errors.append(f"{ap}.evidence: expected array")
            else:
                for j, ev in enumerate(item["evidence"]):
                    if not isinstance(ev, str) or not ev:
                        errors.append(f"{ap}.evidence[{j}]: expected non-empty string")

    # validation
    if "validation" not in data:
        errors.append(f"{root}: missing required field 'validation'")
    elif not isinstance(data["validation"], dict):
        errors.append(f"{root}.validation: expected object")
    else:
        validation = data["validation"]
        for key in validation:
            if key not in VALID_VALIDATION_KEYS:
                errors.append(f"{_json_path(root, 'validation')}: unknown key '{key}'")
        if "status" not in validation:
            errors.append(f"{_json_path(root, 'validation')}: missing required field 'status'")
        elif validation["status"] not in VALID_VALIDATION_STATUSES:
            errors.append(
                f"{_json_path(root, 'validation')}.status: "
                f"expected one of {VALID_VALIDATION_STATUSES}, got '{validation['status']}'"
            )
        if "failed_checks" in validation:
            fc = validation["failed_checks"]
            if not isinstance(fc, list):
                errors.append(f"{_json_path(root, 'validation')}.failed_checks: expected array")
            else:
                for j, check in enumerate(fc):
                    if not isinstance(check, str) or not check:
                        errors.append(
                            f"{_json_path(root, 'validation')}.failed_checks[{j}]: "
                            f"expected non-empty string"
                        )

    # next_task
    if "next_task" not in data:
        errors.append(f"{root}: missing required field 'next_task'")
    elif data["next_task"] is not None:
        nt = data["next_task"]
        if not isinstance(nt, dict):
            errors.append(f"{root}.next_task: expected null or object")
        else:
            # Required fields for next_task object
            for req in ("mode", "goal", "acceptance"):
                if req not in nt:
                    errors.append(f"{root}.next_task: missing required field '{req}'")
            if "mode" in nt and nt["mode"] not in VALID_MODES:
                errors.append(
                    f"{root}.next_task.mode: expected one of {VALID_MODES}, got '{nt['mode']}'"
                )
            if "goal" in nt and (not isinstance(nt["goal"], str) or not nt["goal"]):
                errors.append(f"{root}.next_task.goal: expected non-empty string")
            if "acceptance" in nt:
                if not isinstance(nt["acceptance"], list):
                    errors.append(f"{root}.next_task.acceptance: expected array")
                elif not nt["acceptance"]:
                    errors.append(f"{root}.next_task.acceptance: expected non-empty array")
                else:
                    for j, ac in enumerate(nt["acceptance"]):
                        ap = f"{root}.next_task.acceptance[{j}]"
                        if not isinstance(ac, dict):
                            errors.append(f"{ap}: expected object")
                            continue
                        for req in ("id", "description"):
                            if req not in ac:
                                errors.append(f"{ap}: missing required field '{req}'")
                            elif not isinstance(ac[req], str) or not ac[req]:
                                errors.append(f"{ap}.{req}: expected non-empty string")

    # Consistency checks
    decision = data.get("decision")
    scope = data.get("scope")
    next_task = data.get("next_task")

    # whole-task accept requires next_task to be null
    if decision == "accept" and scope == "whole-task" and next_task is not None:
        errors.append(
            f"{root}: whole-task accept requires next_task to be null"
        )

    # revise/split require next_task to be an object
    if decision in ("revise", "split") and next_task is None:
        errors.append(
            f"{root}: {decision} requires next_task to be an object"
        )

    # lessons
    if "lessons" not in data:
        errors.append(f"{root}: missing required field 'lessons'")
    elif not isinstance(data["lessons"], list):
        errors.append(f"{root}.lessons: expected array")
    else:
        for i, lesson in enumerate(data["lessons"]):
            if not isinstance(lesson, str) or not lesson:
                errors.append(f"{root}.lessons[{i}]: expected non-empty string")

    return errors


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def write_json_atomic(data: Dict[str, Any], dest: Union[str, Path]) -> None:
    """Write JSON atomically using a temp file + rename.

    Supports paths with spaces/UTF-8. Python 3.9+/Windows compatible.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory for atomic rename
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=".review-decision-",
        dir=str(dest.parent),
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        Path(tmp_path).replace(dest)
    except BaseException:
        # Clean up temp file on failure
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_and_validate(text: str) -> Dict[str, Any]:
    """Extract and validate a review decision from text.

    Returns the validated decision dict.
    Raises ExtractionError or ValidationError on failure.
    """
    data = extract_decision_json(text)
    errors = validate_decision(data)
    if errors:
        raise ValidationError("; ".join(errors))
    return data


def extract_next_task_draft(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract next_task from a validated decision, or None if absent."""
    return data.get("next_task")


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def find_default_schema_path() -> Path:
    """Find the default schema path relative to this script.

    Checks source-checkout layout first (<repo>/schemas/), then
    installed layout (<repo>/ai/schemas/). Returns the first that
    exists; falls back to source-checkout path for determinism.
    """
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    source_path = repo_root / "schemas" / "review-decision-v1.schema.json"
    installed_path = repo_root / "ai" / "schemas" / "review-decision-v1.schema.json"
    if source_path.is_file():
        return source_path
    if installed_path.is_file():
        return installed_path
    return source_path


def load_review_text(path: Union[str, Path]) -> str:
    """Load review text from a file.

    Raises ValidationError on missing file.
    """
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"Review file not found: {path}")
    return path.read_text(encoding="utf-8")
