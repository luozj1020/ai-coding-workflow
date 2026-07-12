#!/usr/bin/env python3
"""L0 deterministic acceptance evaluator with Task schema validation.

Accepts composed Task JSON, Artifact Manifest, Validation Results,
Git Diff/scope evidence, and optional Remote Validation Evidence.
Preserves legacy evidence-only compatibility mode.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent


def _load_module(name: str, filename: str):
    """Load a sibling script as a module."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


task_schema = _load_module("task_schema", "task_schema.py")


# ---------------------------------------------------------------------------
# Mechanical failure types
# ---------------------------------------------------------------------------

MECHANICAL_FAILURES = {
    "scope_violation",
    "forbidden_path_modified",
    "sha_mismatch",
    "missing_required_artifact",
    "unexpected_untracked",
    "diff_budget_exceeded",
    "invalid_task_schema",
    "invalid_profile",
}


# ---------------------------------------------------------------------------
# Acceptance evaluation (Task-aware mode)
# ---------------------------------------------------------------------------

def evaluate_task(
    task: Dict[str, Any],
    validation_results: Optional[Dict[str, Any]] = None,
    artifact_manifest: Optional[Dict[str, Any]] = None,
    diff_evidence: Optional[Dict[str, Any]] = None,
    remote_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate acceptance against a composed Task JSON.

    Validates the Task with task_schema.validate_task and fails closed.
    Maps acceptance criteria to validation evidence.
    Returns schema-v1 with status, per-criterion rows, mechanical_failures,
    and semantic_review_required.
    """
    validation_results = validation_results or {}
    artifact_manifest = artifact_manifest or {}
    diff_evidence = diff_evidence or {}

    # Step 1: Validate Task schema - fail closed
    schema_errors = task_schema.validate_task(task)
    if schema_errors:
        return {
            "schema_version": 1,
            "status": "failed",
            "acceptance_matrix": [],
            "mechanical_failures": ["invalid_task_schema"],
            "semantic_review_required": False,
            "schema_errors": schema_errors,
        }

    # Step 2: Check for mechanical failures
    mechanical_failures: List[str] = []

    # Scope violations
    allowed_paths = task.get("scope", {}).get("write_paths", [])
    forbidden_paths = task.get("scope", {}).get("forbidden_paths", [])
    changed_files = diff_evidence.get("changed_files", [])

    if allowed_paths and changed_files:
        outside = [
            f for f in changed_files
            if not any(
                f == p or PurePosixPath(p) in PurePosixPath(f).parents
                for p in allowed_paths
            )
        ]
        if outside:
            mechanical_failures.append("scope_violation")

    # Forbidden path violations
    if forbidden_paths and changed_files:
        forbidden_modified = [
            f for f in changed_files
            if any(
                f == p or PurePosixPath(p) in PurePosixPath(f).parents
                for p in forbidden_paths
            )
        ]
        if forbidden_modified:
            mechanical_failures.append("forbidden_path_modified")

    # SHA mismatch
    if diff_evidence.get("sha_matches") is False:
        mechanical_failures.append("sha_mismatch")

    # Missing required artifacts
    if artifact_manifest.get("missing_artifacts"):
        mechanical_failures.append("missing_required_artifact")

    # Unexpected untracked files
    if diff_evidence.get("unexpected_untracked"):
        mechanical_failures.append("unexpected_untracked")

    # Diff budget exceeded
    diff_lines = diff_evidence.get("diff_lines", 0)
    max_diff_lines = diff_evidence.get("max_diff_lines", 10000)
    if diff_lines > max_diff_lines:
        mechanical_failures.append("diff_budget_exceeded")

    # Step 3: Build acceptance matrix from Task acceptance criteria
    acceptance_criteria = task.get("acceptance", [])
    validations = task.get("validation", [])

    # Build validation lookup by id
    validation_lookup: Dict[str, Dict[str, Any]] = {}
    for v in validations:
        vid = v.get("id")
        if vid:
            validation_lookup[vid] = v

    acceptance_matrix: List[Dict[str, Any]] = []
    all_satisfied = True
    has_unmapped = False

    for ac in acceptance_criteria:
        ac_id = ac.get("id", "")
        ac_desc = ac.get("description", "")
        validation_id = ac.get("validation_id")

        if validation_id:
            # Linked criterion: satisfied only by matching successful validation
            val_result = validation_results.get(validation_id, {})
            val_status = val_result.get("status", "not-run")
            evidence_paths = val_result.get("evidence_paths", [])

            if val_status == "passed":
                status = "satisfied"
            elif val_status == "failed":
                status = "failed"
                all_satisfied = False
            else:
                status = "not-evaluated"
                all_satisfied = False

            acceptance_matrix.append({
                "id": ac_id,
                "validation_id": validation_id,
                "status": status,
                "evidence": evidence_paths,
            })
        else:
            # Unlinked semantic criterion: remains not-evaluated
            # unless explicit trustworthy artifact evidence maps it
            artifact_evidence = _find_artifact_evidence(
                ac_id, artifact_manifest, remote_evidence
            )
            if artifact_evidence.get("status") == "satisfied":
                status = "satisfied"
            else:
                status = "not-evaluated"
                has_unmapped = True
                all_satisfied = False

            acceptance_matrix.append({
                "id": ac_id,
                "status": status,
                "evidence": artifact_evidence.get("evidence_paths", []),
            })

    # Step 4: Determine overall status
    if mechanical_failures:
        status = "failed"
    elif all_satisfied and not has_unmapped:
        status = "passed"
    else:
        status = "partial"

    # Step 5: Determine if semantic review is required
    semantic_review_required = has_unmapped or any(
        row["status"] == "not-evaluated" for row in acceptance_matrix
    )

    return {
        "schema_version": 1,
        "status": status,
        "acceptance_matrix": acceptance_matrix,
        "mechanical_failures": mechanical_failures,
        "semantic_review_required": semantic_review_required,
    }


def _find_artifact_evidence(
    ac_id: str,
    artifact_manifest: Dict[str, Any],
    remote_evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Find artifact evidence for an unlinked acceptance criterion.

    Returns {status, evidence_paths} if trustworthy evidence found,
    otherwise {status: "not-evaluated", evidence_paths: []}.
    """
    # Check artifact manifest for matching entries
    entries = artifact_manifest.get("entries", [])
    for entry in entries:
        if entry.get("acceptance_id") == ac_id:
            if entry.get("verified", False):
                return {
                    "status": "satisfied",
                    "evidence_paths": [entry.get("path", "")],
                }

    # Check remote validation evidence
    if remote_evidence:
        remote_results = remote_evidence.get("results", [])
        for result in remote_results:
            if result.get("acceptance_id") == ac_id:
                if result.get("status") == "passed":
                    return {
                        "status": "satisfied",
                        "evidence_paths": result.get("evidence_paths", []),
                    }

    return {"status": "not-evaluated", "evidence_paths": []}


# ---------------------------------------------------------------------------
# Legacy compatibility mode
# ---------------------------------------------------------------------------

def evaluate_legacy(d: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy evidence-only evaluation mode.

    Preserves backward compatibility with existing callers.
    """
    allowed = d.get("allowed_paths", [])
    changed = d.get("changed_files", [])
    outside = [
        x for x in changed
        if not any(
            x == y or PurePosixPath(y) in PurePosixPath(x).parents
            for y in allowed
        )
    ]

    checks = {
        "scope": not outside,
        "validation": d.get("validation_exit_code") == 0,
        "artifacts": not d.get("missing_artifacts"),
        "sha": d.get("sha_matches", True),
        "untracked": not d.get("unexpected_untracked"),
        "diff_budget": d.get("diff_lines", 0) <= d.get("max_diff_lines", 100),
    }

    matrix = [
        {"id": k, "status": "satisfied" if v else "failed", "evidence": []}
        for k, v in checks.items()
    ]

    out = {
        "schema_version": 1,
        "status": "passed" if all(checks.values()) else "failed",
        "acceptance_matrix": matrix,
        "scope_violations": outside,
        "codex_required": bool(
            d.get("semantic_uncertainty") or d.get("evidence_conflict")
        ),
        "review_triggers": [
            k
            for k in (
                "semantic_uncertainty",
                "evidence_conflict",
                "design_uncertain",
                "cross_module_risk_discovered",
            )
            if d.get(k)
        ],
    }
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-detect mode and evaluate.

    If 'task' key is present, use Task-aware mode.
    Otherwise, use legacy evidence-only mode.
    """
    if "task" in data:
        return evaluate_task(
            task=data["task"],
            validation_results=data.get("validation_results"),
            artifact_manifest=data.get("artifact_manifest"),
            diff_evidence=data.get("diff_evidence", {}),
            remote_evidence=data.get("remote_evidence"),
        )
    else:
        return evaluate_legacy(data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="L0 deterministic acceptance evaluator"
    )
    parser.add_argument("input", help="Input JSON file")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out = evaluate(data)

    print(json.dumps(out, sort_keys=True, indent=2))
    return 0 if out["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
