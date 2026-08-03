#!/usr/bin/env python3
"""Build a compact, non-authoritative acceptance evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


def load(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"artifact": str(path), "parse_error": True}
    return value if isinstance(value, dict) else {"artifact": str(path), "parse_error": True}


def changed_paths(worktree: Path) -> list[str]:
    paths: set[str] = set()
    for argv in (
        ["git", "-C", str(worktree), "diff", "--name-only"],
        ["git", "-C", str(worktree), "diff", "--cached", "--name-only"],
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard"],
    ):
        result = subprocess.run(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            paths.update(line for line in result.stdout.splitlines() if line)
    return sorted(paths)


def status_of(value: dict[str, Any] | None, *keys: str) -> str:
    if value is None:
        return "missing"
    if value.get("parse_error"):
        return "invalid"
    for key in keys:
        if key in value:
            raw = value[key]
            return str(raw).lower() if not isinstance(raw, bool) else ("passed" if raw else "failed")
    return "unknown"


def _optional_arg(args: argparse.Namespace, name: str) -> Path | None:
    value = getattr(args, name, None)
    return value if isinstance(value, Path) else None


def _task_card_source(path: Path | None) -> str:
    if path is None or not path.is_file():
        return "unknown"
    prefix = path.read_text(encoding="utf-8", errors="replace")[:512]
    if "<!-- task-card-components:" in prefix:
        return "deterministic-component-composer"
    if "<!-- workflow-state:" in prefix:
        return "deterministic-state-renderer"
    return "hash-bound-compatible-card"


def _acceptance_index(
    graph: dict[str, Any] | None,
    delta: dict[str, Any] | None,
    matrix: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    if not graph or graph.get("parse_error"):
        return [], {
            "expanded_acceptance_ids": [],
            "omitted_unchanged_accepted": [],
            "deep_codex_review_required": False,
            "reason": "acceptance-graph-unavailable",
        }, []

    delta_items = {
        str(item.get("id")): item
        for item in (delta or {}).get("acceptance_items", [])
        if isinstance(item, dict) and item.get("id")
    }
    matrix_by_acceptance: dict[str, list[dict[str, Any]]] = {}
    unresolved: set[str] = set()
    for row in (matrix or {}).get("rows", []):
        if not isinstance(row, dict):
            continue
        for acceptance_id in row.get("acceptance_ids", []):
            matrix_by_acceptance.setdefault(str(acceptance_id), []).append(row)
        if row.get("coverage_status") in {"uncovered", "contradicted"}:
            unresolved.add(
                f"invariant:{row.get('invariant_id', 'unknown')}:{row.get('coverage_status')}"
            )
    unresolved.update(str(item) for item in (matrix or {}).get("errors", []) if item)

    index = []
    expanded = []
    semantic_review = False
    for item in graph.get("acceptance_items", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        acceptance_id = str(item["id"])
        graph_status = str(item.get("graph_status", "unknown"))
        claims = sorted(str(value) for value in item.get("unverified_claims", []) if value)
        rows = matrix_by_acceptance.get(acceptance_id, [])
        coverage = sorted({str(row.get("coverage_status", "unknown")) for row in rows})
        selected = acceptance_id in delta_items or graph_status != "supported" or bool(claims)
        reasons = []
        if acceptance_id in delta_items:
            reasons.append("delta-selected")
        if graph_status != "supported":
            reasons.append(graph_status)
        if claims:
            reasons.append("unverified-claims")
        if set(coverage) & {"uncovered", "contradicted"}:
            reasons.append("test-coverage-gap")
        if selected or reasons:
            expanded.append(acceptance_id)
        if graph_status in {"contradictory", "reopened"} or claims:
            semantic_review = True
        if "contradicted" in coverage:
            semantic_review = True
        for claim in claims:
            unresolved.add(f"acceptance:{acceptance_id}:{claim}")
        index.append({
            "id": acceptance_id,
            "status": graph_status,
            "evidence_paths": sorted(str(value) for value in item.get("evidence_paths", [])),
            "implementation_ref_count": len(item.get("implementation_refs", [])),
            "test_ref_count": len(item.get("test_refs", [])),
            "result_ref_count": len(item.get("result_refs", [])),
            "test_coverage": coverage,
            "expand": bool(selected or reasons),
            "expand_reasons": sorted(set(reasons)),
        })
    omitted = sorted(
        str(value) for value in (delta or {}).get("omitted_unchanged_accepted", []) if value
    )
    return index, {
        "expanded_acceptance_ids": sorted(set(expanded)),
        "omitted_unchanged_accepted": omitted,
        "deep_codex_review_required": semantic_review,
        "reason": "semantic-risk-delta" if semantic_review else (
            "acceptance-delta" if expanded else "deterministic-evidence-closed"
        ),
    }, sorted(unresolved)


def recommend(
    outcome: dict[str, Any] | None,
    report: dict[str, Any] | None,
    scope: dict[str, Any] | None,
    checker: dict[str, Any] | None,
) -> str:
    if scope and scope.get("enforcement_passed") is False:
        return "revise-scope"
    if checker and checker.get("environment_failure_observed") and not checker.get("enforcement_passed"):
        return "inspect-validation-environment"
    if checker and checker.get("enforcement_passed") is False:
        return "revise-validation"
    report_status = status_of(report, "status")
    if report_status in {"conflict", "invalid", "failed"}:
        return "revise-report-or-code"
    if not outcome or not outcome.get("dispatch_success"):
        return "recover-or-revise"
    if outcome.get("completion_state") == "semantic-review-required":
        return "codex-semantic-review"
    return "inspect-evidence"


def build(args: argparse.Namespace) -> dict[str, Any]:
    outcome = load(args.outcome)
    report = load(args.report_consistency)
    scope = load(args.write_scope)
    checker = load(args.checker_contract)
    recovered = load(args.recovered_completion)
    graph = load(_optional_arg(args, "acceptance_graph"))
    delta = load(_optional_arg(args, "delta_review_packet"))
    matrix = load(_optional_arg(args, "invariant_matrix"))
    symbols = load(_optional_arg(args, "symbol_summary"))
    task_card = _optional_arg(args, "task_card")
    acceptance_index, review_selection, unresolved_risks = _acceptance_index(
        graph, delta, matrix
    )
    checker_skip_reason = None
    if acceptance_index and not review_selection["expanded_acceptance_ids"]:
        checker_skip_reason = "checker skipped: deterministic evidence sufficient"
    value = {
        "schema_version": 1,
        "task_id": (outcome or {}).get("task_id"),
        "authority": "evidence-summary-only",
        "changed_paths": changed_paths(args.worktree),
        "changed_symbols": sorted(
            str(value) for value in (symbols or {}).get("changed_symbols", []) if value
        ),
        "frozen_invariants": [
            {
                "id": row.get("invariant_id"),
                "acceptance_ids": row.get("acceptance_ids", []),
                "coverage_status": row.get("coverage_status", "unknown"),
            }
            for row in (matrix or {}).get("rows", []) if isinstance(row, dict)
        ],
        "acceptance_index": acceptance_index,
        "review_selection": review_selection,
        "unresolved_risks": unresolved_risks,
        "gates": {
            "dispatch": status_of(outcome, "dispatch_success"),
            "artifact": status_of(outcome, "artifact_valid"),
            "report_consistency": status_of(report, "status"),
            "validation": status_of(outcome, "validation_success"),
            "write_scope": status_of(scope, "enforcement_passed"),
            "checker_contract": status_of(checker, "enforcement_passed"),
            "semantic_acceptance": status_of(outcome, "semantic_acceptance"),
        },
        "completion_state": (outcome or {}).get("completion_state", "unknown"),
        "environment_failure_observed": bool(
            checker and checker.get("environment_failure_observed")
        ),
        "recovered_completion_available": recovered is not None,
        "recommended_decision": recommend(outcome, report, scope, checker),
        "checker_skip_reason": checker_skip_reason,
        "codex_output_contract": {
            "intent_freeze": ["goal", "invariants", "acceptance", "forbidden_paths"],
            "planning_review": ["blocking_findings"],
            "final_review": ["decision", "evidence_bound_findings"],
        },
        "task_card": {
            "source": _task_card_source(task_card),
            "sha256": (
                "sha256:" + hashlib.sha256(task_card.read_bytes()).hexdigest()
                if task_card and task_card.is_file() else None
            ),
        },
        "merge_authorized": False,
    }
    return value


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--outcome", type=Path, required=True)
    parser.add_argument("--report-consistency", type=Path)
    parser.add_argument("--write-scope", type=Path)
    parser.add_argument("--checker-contract", type=Path)
    parser.add_argument("--recovered-completion", type=Path)
    parser.add_argument("--acceptance-graph", type=Path)
    parser.add_argument("--delta-review-packet", type=Path)
    parser.add_argument("--invariant-matrix", type=Path)
    parser.add_argument("--symbol-summary", type=Path)
    parser.add_argument("--task-card", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(args)
    atomic_write(args.output, value)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
