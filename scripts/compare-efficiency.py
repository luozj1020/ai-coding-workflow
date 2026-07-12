#!/usr/bin/env python3
"""compare-efficiency.py — Compare benchmark results against baseline.

Consumes executed case results from run-benchmark-suite.py (PR8).
Evaluates aggregate gates:
  - Codex calls reduction >= 30%
  - p50 lead time increase <= 15%
  - first-pass success decline <= 5 percentage points
  - false accepts/scope violations/manual operations do not increase

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/compare-efficiency.py <baseline> <candidate>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def load_metrics(path: Path) -> Dict[str, Any]:
    """Load metrics from a benchmark results file.

    Supports both executed case results (with aggregates.current)
    and legacy ledger summaries (with metrics).
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    # New format: executed case results with aggregates
    if "aggregates" in data:
        return data["aggregates"].get("current", {})

    # Legacy format: ledger summary with metrics
    if "metrics" in data:
        return data["metrics"]

    return data


def compare(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Compare candidate metrics against baseline.

    Returns gate evaluation results.
    """
    # Codex calls reduction
    bc = baseline.get("total_codex_calls", baseline.get("model_calls", {}).get("codex", 0))
    cc = candidate.get("total_codex_calls", candidate.get("model_calls", {}).get("codex", 0))
    reduction = (bc - cc) / bc if bc else None

    # Latency delta (p50)
    b_latency = baseline.get("p50_latency_seconds", baseline.get("elapsed_seconds", 0))
    c_latency = candidate.get("p50_latency_seconds", candidate.get("elapsed_seconds", 0))
    delta = (c_latency - b_latency) / b_latency if b_latency else None

    # First-pass success delta
    b_first_pass = baseline.get("first_pass_rate", baseline.get("first_pass_success_rate", 0))
    c_first_pass = candidate.get("first_pass_rate", candidate.get("first_pass_success_rate", 0))
    first_pass_delta = (c_first_pass or 0) - (b_first_pass or 0)

    # Quality gates (no increase)
    b_false_accepts = baseline.get("false_accepts", 0)
    c_false_accepts = candidate.get("false_accepts", 0)
    b_scope = baseline.get("scope_violations", 0)
    c_scope = candidate.get("scope_violations", 0)

    quality = c_false_accepts <= b_false_accepts and c_scope <= b_scope

    # Human touches (manual operations)
    b_human = baseline.get("human_touches", baseline.get("manual_operations", 0))
    c_human = candidate.get("human_touches", candidate.get("manual_operations", 0))
    human = c_human <= b_human

    # Gate evaluation
    quota_gate = reduction is not None and reduction >= 0.30
    latency_gate = delta is None or delta <= 0.15
    first_pass_gate = first_pass_delta >= -0.05
    quality_gate = quality
    human_gate = human

    pareto = bool(
        reduction is not None
        and reduction > 0
        and (delta is None or delta <= 0.15)
        and first_pass_delta >= -0.05
        and quality
        and human
    )

    return {
        "codex_call_reduction": reduction,
        "elapsed_delta": delta,
        "first_pass_delta": first_pass_delta,
        "quota_gate_pass": quota_gate,
        "latency_gate_pass": latency_gate,
        "first_pass_gate_pass": first_pass_gate,
        "quality_gate_pass": quality_gate,
        "human_touch_gate_pass": human_gate,
        "pareto_candidate": pareto,
        "all_gates_pass": all([
            quota_gate, latency_gate, first_pass_gate, quality_gate, human_gate,
        ]),
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compare benchmark results against baseline."
    )
    p.add_argument("baseline", help="Baseline results file.")
    p.add_argument("candidate", help="Candidate results file.")
    a = p.parse_args()

    baseline_path = Path(a.baseline)
    candidate_path = Path(a.candidate)

    if not baseline_path.exists():
        print(f"Error: Baseline not found: {a.baseline}", file=sys.stderr)
        return 1
    if not candidate_path.exists():
        print(f"Error: Candidate not found: {a.candidate}", file=sys.stderr)
        return 1

    baseline = load_metrics(baseline_path)
    candidate = load_metrics(candidate_path)

    result = compare(baseline, candidate)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
