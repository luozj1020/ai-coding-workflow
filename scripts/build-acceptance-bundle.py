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

from evidence_capsule import (
    acceptance_compression_route,
    artifact_ref,
    bounded_selector,
    bounded_text,
    compact_bytes,
    finalize_capsule,
    repository_head,
    spark_tool_request,
)


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


def _review_evidence(
    paths: list[str],
    outcome: dict[str, Any] | None,
    report_artifact: dict[str, Any] | None,
    report: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    handoff: dict[str, Any] | None,
    recovered: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flatten the highest-value deterministic facts for bounded review."""
    patch = (handoff or {}).get("patch")
    patch = patch if isinstance(patch, dict) else {}
    validation_results = []
    raw_results = (validation or {}).get("results", [])
    raw_results = raw_results if isinstance(raw_results, list) else []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        validation_results.append({
            "index": item.get("index"),
            "label": item.get("label"),
            "command": item.get("command"),
            "exit_code": item.get("exit_code"),
        })
    changed_files = (handoff or {}).get("changed_files")
    if not isinstance(changed_files, list):
        changed_files = [{"path": path, "change": "unknown"} for path in paths]
    report_status = status_of(report, "status")
    report_available = report_status in {"consistent", "passed", "success", "valid", "ok"}
    if recovered and recovered.get("claude_report_complete") is False:
        report_available = False
    return {
        "changed_files": changed_files,
        "changed_path_count": len(changed_files),
        "product_change_count": (outcome or {}).get(
            "product_changes", (handoff or {}).get("product_change_count", 0)
        ),
        "control_change_count": (outcome or {}).get(
            "control_changes", (handoff or {}).get("control_change_count", 0)
        ),
        "diff_sha256": (recovered or {}).get("diff_sha256") or patch.get("sha256"),
        "source_base_commit": (handoff or {}).get("source_base_commit"),
        "execution_base_commit": (handoff or {}).get("execution_base_commit"),
        "claude_report_available": report_available,
        "claude_report_artifact_valid": bool((report_artifact or {}).get("valid")),
        "claude_report_invalid_reasons": (report_artifact or {}).get("reasons", []),
        "claude_report_normalized": bool(
            (report_artifact or {}).get("normalization_applied")
        ),
        "report_status": report_status,
        "patch_bytes": patch.get("bytes", 0),
        "handoff_status": (handoff or {}).get("status", "missing"),
        "deliverable": bool((handoff or {}).get("deliverable")),
        "control_changed_paths": (handoff or {}).get("control_changed_paths", []),
        "out_of_scope_product_paths": (handoff or {}).get(
            "out_of_scope_product_paths",
            (handoff or {}).get("unexpected_changed_paths", []),
        ),
        "handoff_internal_error_reason": (handoff or {}).get("internal_error_reason"),
        "validation_status": status_of(validation, "status"),
        "validation_results": validation_results,
        "validation_command_count": len(validation_results),
        "evidence_source": (
            "deterministic-recovery" if recovered is not None
            else "report-and-deterministic-receipts"
        ),
    }


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
    validation: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
) -> str:
    if scope and scope.get("enforcement_passed") is False:
        return "revise-scope"
    if checker and checker.get("environment_failure_observed") and not checker.get("enforcement_passed"):
        return "inspect-validation-environment"
    if checker and checker.get("enforcement_passed") is False:
        return "revise-validation"
    if validation and validation.get("status") == "failed":
        return "revise-validation"
    if handoff and handoff.get("status") == "blocked":
        return "revise-scope"
    if handoff and handoff.get("status") == "internal-error":
        return "inspect-workflow-error"
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
    report_artifact = load(getattr(args, "report_artifact_validation", None))
    report = load(args.report_consistency)
    scope = load(args.write_scope)
    checker = load(args.checker_contract)
    validation = load(getattr(args, "validation_receipt", None))
    handoff = load(getattr(args, "scoped_handoff", None))
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
    paths = changed_paths(args.worktree)
    review_evidence = _review_evidence(
        paths, outcome, report_artifact, report, validation, handoff, recovered
    )
    value = {
        "schema_version": 1,
        "task_id": (outcome or {}).get("task_id"),
        "authority": "evidence-summary-only",
        "changed_paths": paths,
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
            "report_artifact": status_of(report_artifact, "valid"),
            "report_consistency": status_of(report, "status"),
            "validation": status_of(outcome, "validation_success"),
            "write_scope": status_of(scope, "enforcement_passed"),
            "checker_contract": status_of(checker, "enforcement_passed"),
            "validation_fanout": status_of(validation, "status"),
            "scoped_handoff": status_of(handoff, "status"),
            "semantic_acceptance": status_of(outcome, "semantic_acceptance"),
        },
        "completion_state": (outcome or {}).get("completion_state", "unknown"),
        "operator_state": (outcome or {}).get("operator_state", "unknown"),
        "environment_failure_observed": bool(
            checker and checker.get("environment_failure_observed")
        ),
        "recovered_completion_available": recovered is not None,
        "review_evidence": review_evidence,
        "recommended_decision": recommend(
            outcome, report, scope, checker, validation, handoff
        ),
        "scoped_handoff": {
            "manifest": str(getattr(args, "scoped_handoff", "") or "") or None,
            "patch": (handoff or {}).get("patch"),
            "status": (handoff or {}).get("status"),
            "deliverable": bool((handoff or {}).get("deliverable")),
            "out_of_scope_product_paths": (handoff or {}).get(
                "out_of_scope_product_paths",
                (handoff or {}).get("unexpected_changed_paths", []),
            ),
            "dirty_snapshot": (handoff or {}).get("dirty_snapshot"),
            "whole_worktree_merge_allowed": False,
        },
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


def compact_capsule(
    value: dict[str, Any], output: Path, task_card: Path | None, worktree: Path,
) -> dict[str, Any]:
    selection = value.get("review_selection", {})
    acceptance = value.get("acceptance_index", [])
    compression = dict(value.get("compression_route", {}))
    if compression.get("spark_recommended") and task_card is not None:
        compression["tool_request"] = spark_tool_request(task_card, output)
    return {
        "schema_version": 1,
        "kind": "aiwf-acceptance-capsule",
        "task_id": value.get("task_id"),
        "output_path": str(output.resolve()),
        "evidence": artifact_ref(output),
        "task_card": artifact_ref(task_card),
        "repository_head": repository_head(worktree),
        "recommended_decision": value.get("recommended_decision"),
        "completion_state": value.get("completion_state"),
        "operator_state": value.get("operator_state"),
        "diff_sha256": value.get("review_evidence", {}).get("diff_sha256"),
        "claude_report_available": value.get("review_evidence", {}).get(
            "claude_report_available"
        ),
        "validation_command_count": value.get("review_evidence", {}).get(
            "validation_command_count", 0
        ),
        "product_change_count": value.get("review_evidence", {}).get(
            "product_change_count", 0
        ),
        "report_invalid_reasons": bounded_selector(
            value.get("review_evidence", {}).get("claude_report_invalid_reasons", [])
        ),
        "patch_bytes": value.get("review_evidence", {}).get("patch_bytes", 0),
        "deliverable": bool(value.get("review_evidence", {}).get("deliverable")),
        "out_of_scope_product_path_count": len(
            value.get("review_evidence", {}).get("out_of_scope_product_paths", [])
        ),
        "gates": value.get("gates", {}),
        "changed_path_count": len(value.get("changed_paths", [])),
        "changed_symbol_count": len(value.get("changed_symbols", [])),
        "acceptance_item_count": len(acceptance),
        "expanded_acceptance_ids": bounded_selector(
            selection.get("expanded_acceptance_ids", [])
        ),
        "unresolved_risk_count": len(value.get("unresolved_risks", [])),
        "deep_codex_review_required": bool(selection.get("deep_codex_review_required")),
        "checker_skip_reason": bounded_text(value.get("checker_skip_reason")),
        "compression_route": compression,
        "transfer_metrics": {
            "full_evidence_bytes": output.stat().st_size if output.is_file() else None,
        },
        "merge_authorized": False,
    }


def atomic_write(path: Path, value: dict[str, Any], *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if compact:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            else:
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
    parser.add_argument("--report-artifact-validation", type=Path)
    parser.add_argument("--write-scope", type=Path)
    parser.add_argument("--checker-contract", type=Path)
    parser.add_argument("--validation-receipt", type=Path)
    parser.add_argument("--scoped-handoff", type=Path)
    parser.add_argument("--recovered-completion", type=Path)
    parser.add_argument("--acceptance-graph", type=Path)
    parser.add_argument("--delta-review-packet", type=Path)
    parser.add_argument("--invariant-matrix", type=Path)
    parser.add_argument("--symbol-summary", type=Path)
    parser.add_argument("--task-card", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capsule-output", type=Path)
    parser.add_argument(
        "--stdout-mode",
        choices=("compact", "full", "off"),
        default="compact",
        help="Default compact prints only a bounded tool capsule; full preserves legacy JSON.",
    )
    args = parser.parse_args()
    value = build(args)
    provisional_bytes = len(compact_bytes(value))
    value["compression_route"] = acceptance_compression_route(value, provisional_bytes)
    atomic_write(args.output, value)
    capsule = finalize_capsule(
        compact_capsule(value, args.output, args.task_card, args.worktree)
    )
    if args.capsule_output:
        atomic_write(args.capsule_output, capsule, compact=True)
    if args.stdout_mode == "compact":
        print(json.dumps(capsule, sort_keys=True, separators=(",", ":")))
    elif args.stdout_mode == "full":
        print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
