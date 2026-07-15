#!/usr/bin/env python3
"""Route tasks by risk, expected completion efficiency, quota and latency.

Accepts either a legacy hints dict or a collected-facts dict (from
collect-task-facts.py).  The Router is the sole owner of:
  - execution.builder_checker_split
  - execution.single_pass_allowed
  - execution.single_pass_reason

Key invariants:
  - security=yes or any high-risk yes/unknown category → never Express.
  - Missing/unknown required risks → conservative lane (Assured or Standard).
  - Lane is deterministic for identical input.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Set

# Risk categories that block Express when "yes" or "unknown"
HIGH = {
    "public_api", "data_model", "migration", "security",
    "permission", "concurrency", "cross_module", "production_impact",
}

BUDGETS = {
    "normal": {
        "express": [0, 1, 0],
        "standard": [1, 2, 1],
        "assured": [2, 3, 1],
        "recovery": [1, 2, 1],
    },
    "constrained": {
        "express": [0, 1, 0],
        "standard": [1, 2, 1],
        "assured": [2, 3, 1],
        "recovery": [1, 2, 1],
    },
    "critical": {
        "express": [0, 1, 0],
        "standard": [0, 2, 1],
        "assured": [2, 3, 0],
        "recovery": [0, 2, 1],
    },
}


def _extract_risks(data: Dict[str, Any]) -> Set[str]:
    """Extract risk categories that are 'yes' or 'unknown'.

    Accepts both legacy format (risks dict with truthy values) and
    collected-facts format (effective_risks dict with yes/unknown/no strings).
    """
    risks: Set[str] = set()

    # Collected facts format (effective_risks)
    effective = data.get("effective_risks")
    if isinstance(effective, dict):
        for k in HIGH:
            v = effective.get(k, "unknown")
            if v in ("yes", "unknown"):
                risks.add(k)
        return risks

    # Legacy hints format (risks dict)
    raw = data.get("risks")
    if isinstance(raw, dict):
        for k in HIGH:
            v = raw.get(k, "unknown")
            if v not in (False, "no", 0):
                risks.add(k)
    else:
        risks.update(HIGH)

    return risks


def route(data: Dict[str, Any]) -> Dict[str, Any]:
    """Route a task based on collected facts or legacy hints.

    Returns a routing decision dict with lane, budget, and execution fields.
    """
    risks = _extract_risks(data)
    recovery = bool(data.get("failure_type") or data.get("interrupted"))

    # Determine lane
    if risks & HIGH:
        lane = "assured"
    elif recovery:
        lane = "recovery"
    else:
        # Express requires: small scope, exact validation, no risks
        file_count = data.get("target_files_count", data.get("files", 99))
        diff_lines = data.get("predicted_diff_lines", data.get("diff_lines", 999))
        exact = data.get("exact_validation", False)
        no_risks = not risks

        if file_count <= 2 and diff_lines <= 100 and exact and no_risks:
            lane = "express"
        else:
            lane = "standard"

    # Quota and latency modes
    qm = data.get("quota_mode", "normal")
    lm = data.get("latency_mode", "interactive")
    budgets = BUDGETS.get(qm, BUDGETS["normal"])
    c, cl, s = budgets.get(lane, budgets["standard"])

    # Execution ownership is economic, not risk-derived.  Callers may provide a
    # reviewed pre-card decision; absent that signal, preserve Claude Builder as
    # the compatibility default.
    owner_hint = data.get("execution_owner", data.get("recommended_owner"))
    delegation_value = data.get("delegation_value")
    calibration = data.get("historical_calibration", {})
    historical_bias = calibration.get("owner_bias", "none") if isinstance(calibration, dict) else "none"
    owner_source = "explicit" if owner_hint else "compatibility-default"
    if not owner_hint and historical_bias in ("codex-fast-path", "claude-builder"):
        owner_hint = historical_bias
        owner_source = "accepted-history"
    if delegation_value is False or owner_hint in ("codex", "codex-fast-path"):
        execution_owner = "codex-fast-path"
    else:
        execution_owner = "claude-builder"

    # Single-pass decision: only the Router calculates this.
    single_pass_allowed = lane == "express" and not risks and bool(data.get("exact_validation"))
    checker_value_reasons = []
    for field, reason_code in (
        ("checker_model_required", "explicit-checker-model"),
        ("test_writing_required", "assigned-test-writing"),
        ("long_validation_required", "long-validation"),
        ("evidence_processing_required", "large-evidence-processing"),
    ):
        if data.get(field) is True:
            checker_value_reasons.append(reason_code)
    checker_model_dispatch = bool(checker_value_reasons) and not single_pass_allowed
    builder_checker_split = checker_model_dispatch
    if single_pass_allowed:
        single_pass_reason = "express-lane-exact-validation"
    elif checker_model_dispatch:
        single_pass_reason = "checker-model-has-explicit-delegation-value"
    else:
        single_pass_reason = "deterministic-validation-without-checker-model"

    # Build reason list
    reason = sorted(risks) if risks else (
        ["failure recovery"] if recovery else
        ["bounded deterministic scope"] if lane == "express" else
        ["ordinary scoped work"]
    )

    return {
        "schema_version": 1,
        "lane": lane,
        "reason": reason,
        "budget": {
            "codex_calls": c,
            "claude_calls": cl,
            "spark_calls": s,
            "codex_reserved_for": (
                ["architecture-review", "final-review"] if lane == "assured"
                else (["final-review"] if c else [])
            ),
        },
        "execution": {
            "owner": execution_owner,
            "owner_source": owner_source,
            "historical_calibration": calibration if isinstance(calibration, dict) else {},
            "builder_checker_split": builder_checker_split,
            "checker_model_dispatch": checker_model_dispatch,
            "checker_value_reasons": checker_value_reasons,
            "checker_skip_reason": (
                None if checker_model_dispatch
                else "checker skipped: deterministic evidence sufficient"
            ),
            "single_pass_allowed": single_pass_allowed,
            "single_pass_reason": single_pass_reason,
            "remote_rounds": 1,
        },
        "estimated_efficiency": {
            "first_pass_confidence": data.get("first_pass_confidence", "medium"),
            "context_cache_reusable": bool(data.get("context_cache")),
        },
        "quota_mode": qm,
        "latency_mode": lm,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Route a task by risk and scope.")
    p.add_argument("input", help="Path to collected-facts or hints JSON.")
    a = p.parse_args()
    data = json.loads(open(a.input, encoding="utf-8").read())
    print(json.dumps(route(data), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
