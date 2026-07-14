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


def _has_samples(metrics: Dict[str, Any], key: str) -> bool:
    """Check if metrics has a usable numeric sample for a given key."""
    val = metrics.get(key)
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def compare(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Compare candidate metrics against baseline.

    Returns gate evaluation results with status: pass, fail, or insufficient-data.
    """
    # --- Sample sufficiency checks ---
    missing = []
    # Codex calls: need both baseline and candidate samples
    bc = baseline.get("total_codex_calls", baseline.get("model_calls", {}).get("codex", 0))
    cc = candidate.get("total_codex_calls", candidate.get("model_calls", {}).get("codex", 0))
    if not _has_samples(baseline, "total_codex_calls") and not baseline.get("model_calls", {}).get("codex"):
        missing.append("baseline_codex_calls")
    if not _has_samples(candidate, "total_codex_calls") and not candidate.get("model_calls", {}).get("codex"):
        missing.append("candidate_codex_calls")

    # Latency: need both baseline and candidate samples for delta computation
    b_latency = baseline.get("p50_latency_seconds", baseline.get("elapsed_seconds", 0))
    c_latency = candidate.get("p50_latency_seconds", candidate.get("elapsed_seconds", 0))
    if not _has_samples(baseline, "p50_latency_seconds") and not _has_samples(baseline, "elapsed_seconds"):
        missing.append("baseline_latency")
    if not _has_samples(candidate, "p50_latency_seconds") and not _has_samples(candidate, "elapsed_seconds"):
        missing.append("candidate_latency")

    # First-pass: need both samples
    b_first_pass = baseline.get("first_pass_rate", baseline.get("first_pass_success_rate", 0))
    c_first_pass = candidate.get("first_pass_rate", candidate.get("first_pass_success_rate", 0))
    if not _has_samples(baseline, "first_pass_rate") and not _has_samples(baseline, "first_pass_success_rate"):
        missing.append("baseline_first_pass_rate")
    if not _has_samples(candidate, "first_pass_rate") and not _has_samples(candidate, "first_pass_success_rate"):
        missing.append("candidate_first_pass_rate")

    # --- Advisor continuation metrics ---
    # Sufficiency: require numeric samples from both sides
    if not (_has_samples(baseline, "full_redispatch_avoided_total")
            or _has_samples(baseline, "full_redispatch_avoided")):
        missing.append("baseline_redispatch")
    if not (_has_samples(candidate, "full_redispatch_avoided_total")
            or _has_samples(candidate, "full_redispatch_avoided")):
        missing.append("candidate_redispatch")
    if not (_has_samples(baseline, "advisor_continuation_succeeded_total")
            or _has_samples(baseline, "continuation_succeeded")):
        missing.append("baseline_continuation_success")
    if not (_has_samples(candidate, "advisor_continuation_succeeded_total")
            or _has_samples(candidate, "continuation_succeeded")):
        missing.append("candidate_continuation_success")
    if not (_has_samples(baseline, "reexploration_yes_total")
            or _has_samples(baseline, "reexploration_yes")):
        missing.append("baseline_reexploration")
    if not (_has_samples(candidate, "reexploration_yes_total")
            or _has_samples(candidate, "reexploration_yes")):
        missing.append("candidate_reexploration")

    # Quality gate samples
    if not _has_samples(baseline, "false_accepts"):
        missing.append("baseline_false_accepts")
    if not _has_samples(candidate, "false_accepts"):
        missing.append("candidate_false_accepts")
    if not _has_samples(baseline, "scope_violations"):
        missing.append("baseline_scope_violations")
    if not _has_samples(candidate, "scope_violations"):
        missing.append("candidate_scope_violations")

    # --- Value extraction (samples validated above) ---
    b_redispatch = baseline.get("full_redispatch_avoided_total",
                                baseline.get("full_redispatch_avoided", 0))
    c_redispatch = candidate.get("full_redispatch_avoided_total",
                                 candidate.get("full_redispatch_avoided", 0))
    b_continuation_success = baseline.get("advisor_continuation_succeeded_total",
                                           baseline.get("continuation_succeeded", 0))
    c_continuation_success = candidate.get("advisor_continuation_succeeded_total",
                                            candidate.get("continuation_succeeded", 0))
    b_reexploration = baseline.get("reexploration_yes_total",
                                   baseline.get("reexploration_yes", 0))
    c_reexploration = candidate.get("reexploration_yes_total",
                                    candidate.get("reexploration_yes", 0))

    # --- Gate computations ---
    reduction = (bc - cc) / bc if bc else None
    delta = (c_latency - b_latency) / b_latency if b_latency else None
    first_pass_delta = (c_first_pass or 0) - (b_first_pass or 0)

    # Quality gates (no increase)
    b_false_accepts = baseline.get("false_accepts", 0)
    c_false_accepts = candidate.get("false_accepts", 0)
    b_scope = baseline.get("scope_violations", 0)
    c_scope = candidate.get("scope_violations", 0)
    quality = c_false_accepts <= b_false_accepts and c_scope <= b_scope

    # Human touches (manual operations) — outside insufficiency requirements
    b_human = baseline.get("human_touches", baseline.get("manual_operations", 0))
    c_human = candidate.get("human_touches", candidate.get("manual_operations", 0))
    human = c_human <= b_human

    # Continuation success non-regression (candidate >= baseline or both zero)
    continuation_gate = (c_continuation_success or 0) >= (b_continuation_success or 0)

    # Re-exploration non-regression (candidate <= baseline or both zero)
    reexploration_gate = (c_reexploration or 0) <= (b_reexploration or 0)

    # Redispatch avoidance non-regression (candidate >= baseline or both zero)
    redispatch_gate = (c_redispatch or 0) >= (b_redispatch or 0)

    # Existing gates
    quota_gate = reduction is not None and reduction >= 0.30
    latency_gate = delta is None or delta <= 0.15
    first_pass_gate = first_pass_delta >= -0.05
    quality_gate = quality
    human_gate = human

    insufficient_data = len(missing) > 0

    all_gates = all([
        quota_gate, latency_gate, first_pass_gate, quality_gate, human_gate,
        continuation_gate, reexploration_gate, redispatch_gate,
    ])

    if insufficient_data:
        status = "insufficient-data"
    elif all_gates:
        status = "pass"
    else:
        status = "fail"

    pareto = bool(
        not insufficient_data
        and reduction is not None
        and reduction > 0
        and (delta is None or delta <= 0.15)
        and first_pass_delta >= -0.05
        and quality
        and human
        and continuation_gate
        and reexploration_gate
        and redispatch_gate
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
        "continuation_gate_pass": continuation_gate,
        "reexploration_gate_pass": reexploration_gate,
        "redispatch_gate_pass": redispatch_gate,
        "pareto_candidate": pareto,
        "insufficient_data": insufficient_data,
        "missing_samples": missing,
        "all_gates_pass": all_gates and not insufficient_data,
        "status": status,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compare benchmark results against baseline."
    )
    p.add_argument("baseline", help="Baseline results file.")
    p.add_argument("candidate", help="Candidate results file.")
    p.add_argument("--enforce", action="store_true",
                   help="Return non-zero when gates fail or evidence is insufficient.")
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

    if a.enforce:
        if result["status"] == "insufficient-data":
            return 2
        if result["status"] == "fail":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
