#!/usr/bin/env python3
"""run-benchmark-suite.py — Execute benchmark cases through deterministic fake adapters.

Case schema fields (PR8):
  - id: unique case identifier
  - fixture: fixture directory name under benchmarks/cases/<id>/
  - task: task description or inline task dict
  - expected_lane: expected routing lane (express/standard/assured)
  - expected_tier: expected review tier (L0/L1/L2)
  - allowed_files: list of file patterns that may be modified
  - forbidden_files: list of file patterns that must NOT be modified
  - validations: list of validation commands (fake, just recorded)
  - quota_budget: max model calls allowed
  - latency_budget_seconds: max wall-clock time allowed
  - routing_facts: optional deterministic owner-route overrides

Deterministic fake adapters (no real model calls):
  - FakeClaudeAdapter: simulates Claude dispatch with deterministic output
  - FakeSparkAdapter: simulates Spark execution
  - FakeCodexAdapter: simulates Codex direct implementation or review

Full pipeline: Task → Deterministic Route → Direct/Dispatch → Evidence → Review → Decision

Produces per-case outcomes and aggregate gates:
  - Codex calls reduction >= 30%
  - p50 lead time increase <= 15%
  - first-pass success decline <= 5 percentage points
  - false accepts/scope violations/manual operations do not increase

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/run-benchmark-suite.py [--cases benchmarks/cases] [--output FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Case schema
# ---------------------------------------------------------------------------

CASE_SCHEMA_VERSION = 2

CASE_REQUIRED_FIELDS = {"id", "task"}

CASE_OPTIONAL_FIELDS = {
    "fixture",
    "expected_lane",
    "expected_tier",
    "allowed_files",
    "forbidden_files",
    "validations",
    "quota_budget",
    "latency_budget_seconds",
    "trust",
    "routing_facts",
}


def _load_route_module():
    path = Path(__file__).resolve().with_name("route-task.py")
    spec = importlib.util.spec_from_file_location("aiwf_benchmark_route", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route_task = _load_route_module()


def validate_case(case: Dict[str, Any]) -> List[str]:
    """Validate a benchmark case against the schema."""
    errors: List[str] = []

    for field in CASE_REQUIRED_FIELDS:
        if field not in case:
            errors.append(f"Missing required field: {field}")

    # Validate id format
    case_id = case.get("id", "")
    if not isinstance(case_id, str) or not case_id:
        errors.append("id must be a non-empty string")
    elif any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in case_id):
        errors.append(f"id contains unsafe characters: {case_id!r}")

    # Validate optional fields
    lane = case.get("expected_lane")
    if lane is not None and lane not in ("express", "standard", "assured"):
        errors.append(f"expected_lane must be express/standard/assured, got {lane!r}")

    tier = case.get("expected_tier")
    if tier is not None and tier not in ("L0", "L1", "L2"):
        errors.append(f"expected_tier must be L0/L1/L2, got {tier!r}")

    quota = case.get("quota_budget")
    if quota is not None and (not isinstance(quota, int) or quota < 0):
        errors.append(f"quota_budget must be a non-negative integer, got {quota!r}")

    latency = case.get("latency_budget_seconds")
    if latency is not None and (not isinstance(latency, (int, float)) or latency < 0):
        errors.append(f"latency_budget_seconds must be non-negative, got {latency!r}")

    routing_facts = case.get("routing_facts")
    if routing_facts is not None and not isinstance(routing_facts, dict):
        errors.append("routing_facts must be an object")

    return errors


# ---------------------------------------------------------------------------
# Deterministic fake adapters
# ---------------------------------------------------------------------------

def deterministic_seed(case_id: str) -> int:
    """Generate a deterministic seed from case id."""
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16)


class FakeClaudeAdapter:
    """Simulates Claude dispatch with deterministic output."""

    def __init__(self) -> None:
        self.call_count = 0

    def dispatch(self, task: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate Claude dispatch."""
        self.call_count += 1
        case_id = task.get("id", "unknown")
        seed = deterministic_seed(case_id)

        # Determine lane based on task properties
        lane = task.get("expected_lane", "standard")
        if lane is None:
            lane = "standard"

        # Simulate diff based on seed
        diff_lines = (seed % 50) + 1
        diff = "\n".join([f"+line {i}" for i in range(diff_lines)])

        return {
            "adapter": "claude",
            "call_id": f"claude-{self.call_count}",
            "lane": lane,
            "diff": diff,
            "diff_lines": diff_lines,
            "files_changed": [f"src/file_{seed % 10}.py"],
            "success": True,
            "latency_ms": (seed % 200) + 50,
        }


class FakeSparkAdapter:
    """Simulates Spark execution."""

    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, task: Dict[str, Any], dispatch_result: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate Spark execution."""
        self.call_count += 1
        return {
            "adapter": "spark",
            "call_id": f"spark-{self.call_count}",
            "validation_passed": True,
            "latency_ms": 100,
        }


class FakeCodexAdapter:
    """Simulates Codex direct implementation and semantic review."""

    def __init__(self) -> None:
        self.call_count = 0

    def review(
        self,
        task: Dict[str, Any],
        dispatch_result: Dict[str, Any],
        spark_result: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Simulate Codex review."""
        self.call_count += 1
        case_id = task.get("id", "unknown")
        seed = deterministic_seed(case_id)

        # Determine tier based on task properties
        tier = task.get("expected_tier", "L0")
        if tier is None:
            tier = "L0"

        return {
            "adapter": "codex",
            "call_id": f"codex-{self.call_count}",
            "tier": tier,
            "accepted": True,
            "false_accept": False,
            "scope_violation": False,
            "latency_ms": (seed % 300) + 100,
        }

    def implement(self, task: Dict[str, Any], route_result: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate one Codex-owned implementation call."""
        self.call_count += 1
        case_id = task.get("id", "unknown")
        seed = deterministic_seed(case_id)
        diff_lines = (seed % 40) + 1
        diff = "\n".join([f"+line {i}" for i in range(diff_lines)])
        return {
            "adapter": "codex",
            "call_id": f"codex-{self.call_count}",
            "lane": route_result.get("lane", "standard"),
            "diff": diff,
            "diff_lines": diff_lines,
            "files_changed": [f"src/file_{seed % 10}.py"],
            "success": True,
            "accepted": True,
            "false_accept": False,
            "scope_violation": False,
            "tier": task.get("expected_tier") or "L0",
            "latency_ms": (seed % 250) + 80,
        }


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

def _routing_facts(case: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    """Build deterministic route input while preserving optional case overrides."""
    no_risks = {
        key: "no" for key in (
            "public_api", "data_model", "security", "migration", "permission",
            "concurrency", "cross_module", "production_impact",
        )
    }
    lane = case.get("expected_lane", "standard")
    risks = dict(no_risks)
    if lane == "assured":
        risks["production_impact"] = "yes"
    base = {
        "task_id": task.get("id", "unknown"),
        "effective_risks": risks,
        "target_files_count": 1 if lane == "express" else 3,
        "predicted_diff_lines": 20 if lane == "express" else 200,
        "exact_validation": True,
        "repository_size": "small",
        "task_role": "core-semantic",
        "claude_role": "none",
        "codex_review_scope": "full",
    }
    overrides = case.get("routing_facts", {})
    if isinstance(overrides, dict):
        base.update(overrides)
    return base


def execute_case(
    case: Dict[str, Any],
    claude: FakeClaudeAdapter,
    spark: FakeSparkAdapter,
    codex: FakeCodexAdapter,
) -> Dict[str, Any]:
    """Execute a single benchmark case through the full pipeline."""
    start_time = time.monotonic()

    task = case.get("task", case)
    if isinstance(task, str):
        task = {"description": task, "id": case.get("id", "unknown")}

    # Merge case metadata into task
    task["id"] = case.get("id", task.get("id", "unknown"))
    task["expected_lane"] = case.get("expected_lane")
    task["expected_tier"] = case.get("expected_tier")

    before = (claude.call_count, spark.call_count, codex.call_count)

    # Step 1: use the same deterministic owner route as the real workflow.
    route_result = route_task.route(_routing_facts(case, task))
    owner = route_result.get("execution", {}).get("owner", "codex-fast-path")

    # Step 2: direct Codex implementation is the default; Claude is positive-gated.
    if owner == "claude-builder":
        dispatch_result = claude.dispatch(task, {"route": route_result})
    else:
        dispatch_result = codex.implement(task, route_result)

    # Step 3: Evidence
    evidence = {
        "diff": dispatch_result.get("diff", ""),
        "files_changed": dispatch_result.get("files_changed", []),
        "diff_lines": dispatch_result.get("diff_lines", 0),
    }

    # Step 4: Spark is opt-in and only follows the router's bounded request.
    spark_result = None
    if route_result.get("precard_estimator", {}).get("spark_action") == "estimate":
        spark_result = spark.execute(task, dispatch_result)

    # Step 5: delegated work receives Codex semantic review; direct work already
    # consumed its single combined implementation/review call.
    if owner == "claude-builder":
        review_result = codex.review(task, dispatch_result, spark_result)
    else:
        review_result = dispatch_result

    # Step 6: Decision
    elapsed = time.monotonic() - start_time
    decision = {
        "accepted": review_result.get("accepted", False),
        "false_accept": review_result.get("false_accept", False),
        "scope_violation": review_result.get("scope_violation", False),
        "tier": review_result.get("tier", "L0"),
        "lane": route_result["lane"],
    }

    # Check constraints
    allowed_files = case.get("allowed_files", [])
    forbidden_files = case.get("forbidden_files", [])
    files_changed = dispatch_result.get("files_changed", [])

    constraint_violations: List[str] = []
    for f in files_changed:
        if forbidden_files and any(
            f.startswith(pat.rstrip("*")) for pat in forbidden_files
        ):
            constraint_violations.append(f"Forbidden file modified: {f}")

    # Check quota budget
    quota_budget = case.get("quota_budget")
    per_case = (
        claude.call_count - before[0],
        spark.call_count - before[1],
        codex.call_count - before[2],
    )
    total_calls = sum(per_case)
    quota_exceeded = quota_budget is not None and total_calls > quota_budget

    return {
        "case_id": case.get("id", "unknown"),
        "status": "passed" if decision["accepted"] and not constraint_violations else "failed",
        "decision": decision,
        "route": {
            "owner": owner,
            "claude_role": route_result.get("execution", {}).get("claude_role", "none"),
            "spark_action": route_result.get("precard_estimator", {}).get("spark_action", "skip"),
        },
        "evidence": evidence,
        "constraint_violations": constraint_violations,
        "quota_exceeded": quota_exceeded,
        "total_model_calls": total_calls,
        "claude_calls": per_case[0],
        "spark_calls": per_case[1],
        "codex_calls": per_case[2],
        "elapsed_seconds": elapsed,
        "latency_ms": {
            "claude": dispatch_result.get("latency_ms", 0),
            "spark": spark_result.get("latency_ms", 0) if spark_result else 0,
            "codex": review_result.get("latency_ms", 0),
        },
    }


# ---------------------------------------------------------------------------
# Aggregate gates
# ---------------------------------------------------------------------------

def compute_aggregate_gates(
    results: List[Dict[str, Any]],
    baseline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute aggregate gate results from case outcomes.

    Gates:
      - Codex calls reduction >= 30% (vs baseline)
      - p50 lead time increase <= 15%
      - first-pass success decline <= 5 percentage points
      - false accepts/scope violations/manual operations do not increase
    """
    total_cases = len(results)
    if total_cases == 0:
        return {"error": "No results to aggregate"}

    # Current metrics
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = total_cases - passed
    first_pass = sum(
        1 for r in results if r.get("status") == "passed" and r.get("claude_calls", 0) <= 1
    )
    false_accepts = sum(1 for r in results if r.get("decision", {}).get("false_accept"))
    scope_violations = sum(
        1 for r in results if r.get("decision", {}).get("scope_violation")
    )
    total_codex_calls = sum(r.get("codex_calls", 0) for r in results)
    total_claude_calls = sum(r.get("claude_calls", 0) for r in results)
    total_spark_calls = sum(r.get("spark_calls", 0) for r in results)

    # Latency percentiles
    latencies = sorted(r.get("elapsed_seconds", 0) for r in results)
    p50_latency = latencies[len(latencies) // 2] if latencies else 0

    current = {
        "total_cases": total_cases,
        "passed": passed,
        "failed": failed,
        "first_pass_count": first_pass,
        "first_pass_rate": first_pass / total_cases if total_cases else 0,
        "false_accepts": false_accepts,
        "scope_violations": scope_violations,
        "total_codex_calls": total_codex_calls,
        "total_claude_calls": total_claude_calls,
        "total_spark_calls": total_spark_calls,
        "codex_calls_per_case": total_codex_calls / total_cases if total_cases else 0,
        "p50_latency_seconds": p50_latency,
    }

    # Gate evaluation
    gates: Dict[str, Any] = {}

    if baseline:
        b_codex = baseline.get("total_codex_calls", 0)
        c_codex = total_codex_calls
        codex_reduction = (b_codex - c_codex) / b_codex if b_codex else None
        gates["codex_calls_reduction"] = {
            "value": codex_reduction,
            "threshold": 0.30,
            "passed": codex_reduction is not None and codex_reduction >= 0.30,
        }

        b_p50 = baseline.get("p50_latency_seconds", 0)
        latency_delta = (p50_latency - b_p50) / b_p50 if b_p50 else None
        gates["p50_latency_increase"] = {
            "value": latency_delta,
            "threshold": 0.15,
            "passed": latency_delta is None or latency_delta <= 0.15,
        }

        b_first_pass = baseline.get("first_pass_rate", 0)
        first_pass_delta = current["first_pass_rate"] - b_first_pass
        gates["first_pass_success_decline"] = {
            "value": first_pass_delta,
            "threshold": -0.05,
            "passed": first_pass_delta >= -0.05,
        }

        b_false_accepts = baseline.get("false_accepts", 0)
        gates["false_accepts_no_increase"] = {
            "current": false_accepts,
            "baseline": b_false_accepts,
            "passed": false_accepts <= b_false_accepts,
        }

        b_scope = baseline.get("scope_violations", 0)
        gates["scope_violations_no_increase"] = {
            "current": scope_violations,
            "baseline": b_scope,
            "passed": scope_violations <= b_scope,
        }
    else:
        # No baseline — report current state only
        gates["note"] = "No baseline provided; gate evaluation requires baseline metrics"

    return {
        "current": current,
        "gates": gates,
        "all_passed": (
            all(g.get("passed", True) for g in gates.values() if isinstance(g, dict))
            if baseline
            else None
        ),
    }


def compute_legacy_ledger_metrics(path: Optional[str]) -> Dict[str, Any]:
    """Preserve the v1 ledger summary consumed by existing automation."""
    if not path:
        return {}
    records: List[Dict[str, Any]] = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}
    accepted = [r for r in records if r.get("accepted") is True]
    accepted_task_ids = {str(r.get("task_id", "")) for r in accepted}
    codex_task_ids = {
        str(r.get("task_id", "")) for r in records if str(r.get("model", "")).lower() == "codex"
    }
    zero_codex = len(accepted_task_ids - codex_task_ids)
    validation_passed = sum(
        1 for r in accepted if r.get("validation_status") == "passed"
    )
    return {
        "accepted_tasks": len(accepted_task_ids),
        "zero_codex_completion_rate": (
            zero_codex / len(accepted_task_ids) if accepted_task_ids else 0.0
        ),
        "first_pass_success_rate": (
            validation_passed / len(accepted) if accepted else 0.0
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Execute benchmark cases through deterministic fake adapters."
    )
    p.add_argument("--cases", default="benchmarks/cases", help="Cases directory.")
    p.add_argument("--output", help="Write results to this file.")
    p.add_argument("--baseline", help="Baseline metrics file for gate comparison.")
    p.add_argument("--ledger", help="Optional v1 JSONL ledger for compatibility metrics.")
    p.add_argument(
        "--case-id", action="append", default=[], help="Run only these case IDs."
    )
    a = p.parse_args()

    root = Path(a.cases)
    if not root.exists():
        print(f"Error: Cases directory not found: {root}", file=sys.stderr)
        return 1

    # Load cases
    cases: List[Dict[str, Any]] = []
    for case_dir in sorted(root.iterdir()):
        if not case_dir.is_dir():
            continue
        case_file = case_dir / "case.json"
        if not case_file.exists():
            continue
        try:
            case = json.loads(case_file.read_text(encoding="utf-8"))
            # Validate case
            errors = validate_case(case)
            if errors:
                print(f"Warning: Case {case_dir.name} has errors: {errors}", file=sys.stderr)
                continue
            # Set fixture path
            case["_fixture_dir"] = str(case_dir)
            cases.append(case)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Cannot load {case_file}: {e}", file=sys.stderr)

    # Filter by case ID if specified
    if a.case_id:
        cases = [c for c in cases if c.get("id") in a.case_id]

    if not cases:
        print("Error: No valid cases found.", file=sys.stderr)
        return 1

    # Execute cases
    results: List[Dict[str, Any]] = []
    claude = FakeClaudeAdapter()
    spark = FakeSparkAdapter()
    codex = FakeCodexAdapter()

    for case in cases:
        result = execute_case(case, claude, spark, codex)
        results.append(result)

    # Load baseline if provided
    baseline: Optional[Dict[str, Any]] = None
    if a.baseline:
        baseline_path = Path(a.baseline)
        if baseline_path.exists():
            try:
                baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
                baseline = baseline_data.get("current", baseline_data)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: Cannot load baseline: {e}", file=sys.stderr)

    # Compute aggregate gates
    aggregates = compute_aggregate_gates(results, baseline)

    # Build output
    output = {
        "schema_version": CASE_SCHEMA_VERSION,
        "timestamp": int(time.time()),
        "cases_dir": str(root),
        "total_cases": len(results),
        "count": len(results),
        "results": results,
        "aggregates": aggregates,
        "adapter_calls": {
            "claude": claude.call_count,
            "spark": spark.call_count,
            "codex": codex.call_count,
        },
        "metrics": compute_legacy_ledger_metrics(a.ledger),
    }

    # Write output
    output_json = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if a.output:
        output_path = Path(a.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_json, encoding="utf-8")
        print(f"Results: {output_path}")
    else:
        print(output_json)

    # Summary
    passed = sum(1 for r in results if r.get("status") == "passed")
    print(f"Summary: {passed}/{len(results)} cases passed", file=sys.stderr)
    if baseline:
        print(f"All gates passed: {aggregates.get('all_passed')}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
