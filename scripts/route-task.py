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
import importlib.util
import json
from pathlib import Path
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

CLAUDE_FIRST_BUDGETS = {
    "normal": {
        "express": [0, 1, 0],
        "standard": [1, 2, 1],
        "assured": [1, 3, 1],
        "recovery": [1, 2, 1],
    },
    "constrained": {
        "express": [0, 1, 0],
        "standard": [1, 2, 1],
        "assured": [1, 3, 1],
        "recovery": [1, 2, 1],
    },
    "critical": {
        "express": [0, 1, 0],
        "standard": [1, 2, 0],
        "assured": [1, 3, 0],
        "recovery": [1, 2, 0],
    },
}


def _load_economics():
    path = Path(__file__).resolve().with_name("workflow_economics.py")
    spec = importlib.util.spec_from_file_location("aiwf_route_economics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


economics = _load_economics()


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
    file_count = data.get("target_files_count", data.get("files", 99))
    diff_lines = data.get("predicted_diff_lines", data.get("diff_lines", 999))
    exact = data.get("exact_validation", False)

    # Determine lane
    if risks & HIGH:
        lane = "assured"
    elif recovery:
        lane = "recovery"
    else:
        # Express requires: small scope, exact validation, no risks
        no_risks = not risks

        if file_count <= 2 and diff_lines <= 100 and exact and no_risks:
            lane = "express"
        else:
            lane = "standard"

    # Optimize the default owner for scarce Codex quota. Independent CLI
    # terminals provide portfolio throughput, so Claude-first treats latency as
    # an observed metric rather than an ownership veto.
    ownership_profile = str(data.get("ownership_profile") or "claude-first")
    if ownership_profile not in ("claude-first", "economy-first"):
        ownership_profile = "claude-first"

    # Quota and latency modes
    qm = data.get("quota_mode", "normal")
    lm = data.get("latency_mode", "interactive")
    budget_table = CLAUDE_FIRST_BUDGETS if ownership_profile == "claude-first" else BUDGETS
    budgets = budget_table.get(qm, budget_table["normal"])
    c, cl, s = budgets.get(lane, budgets["standard"])

    # Explicit human ownership is authoritative. Otherwise Claude-first keeps
    # planning, implementation, revision, test writing, and long validation on
    # Claude; Codex freezes intent and performs one bounded semantic review.
    # Economy-first retains the stricter positive delegation gate.
    owner_hint = data.get("execution_owner", data.get("recommended_owner"))
    explicit_owner_hint = bool(owner_hint)
    delegation_value = data.get("delegation_value")
    requested_role = str(data.get("claude_role") or "none")
    solution_planner_requested = requested_role == "solution-planner"
    explicit_read_only_task = data.get("read_only_task") is True
    read_only_task = explicit_read_only_task or solution_planner_requested
    durable_output_required = data.get("durable_output_required") is True
    durable_structured_output = data.get("durable_structured_output") is True
    work_reduction = data.get("expected_codex_work_reduction_ratio")
    readonly_value = bool(
        read_only_task
        and durable_structured_output
        and isinstance(work_reduction, (int, float))
        and work_reduction >= 0.30
    )
    calibration = data.get("historical_calibration", {})
    economy_facts = dict(data)
    if ownership_profile == "claude-first":
        policy = dict(data.get("economy_policy") or {})
        policy.setdefault("min_cost_savings_ratio", 0.0)
        policy.setdefault("max_active_elapsed_ratio", 1000000.0)
        policy.setdefault("min_codex_work_reduction_ratio", 0.15)
        economy_facts["economy_policy"] = policy
    economy_gate = economics.delegation_economy_gate(economy_facts, calibration)
    historical_bias = calibration.get("owner_bias", "none") if isinstance(calibration, dict) else "none"
    owner_source = "explicit" if owner_hint else "codex-default"
    if not owner_hint and historical_bias == "codex-fast-path":
        owner_hint = historical_bias
        owner_source = "accepted-history"

    open_solution_path = bool(
        data.get("goal_clarity") in ("high", "medium")
        and data.get("bounded_exploration_scope") is True
        and data.get("implementation_path_clarity", data.get("solution_clarity")) in ("medium", "low")
    )
    large_or_multiphase = bool(
        data.get("multi_phase_task") is True
        or data.get("repository_size") in ("large", "giant", "monorepo")
        or (isinstance(file_count, (int, float)) and file_count >= 4)
    )
    solution_planner_candidate = bool(
        (solution_planner_requested or data.get("allow_claude_planner") is True)
        and durable_structured_output
        and (
            ownership_profile == "claude-first"
            or (isinstance(work_reduction, (int, float)) and work_reduction >= 0.30)
        )
        and open_solution_path
        and large_or_multiphase
    )
    batch_candidate = bool(
        (requested_role == "batch-builder" or data.get("mechanical_batch") is True)
        and durable_output_required
        and data.get("task_role") == "auxiliary"
        and data.get("independent_write_scopes") is True
        and data.get("codex_review_scope") in ("sampled", "bounded")
    )
    execution_candidate = bool(
        requested_role == "execution-builder"
        and durable_output_required
        and (
            ownership_profile == "claude-first"
            or (
                data.get("task_role") == "auxiliary"
                and data.get("codex_review_scope") in ("sampled", "bounded")
            )
        )
    )
    exploratory_candidate = bool(
        requested_role == "exploratory-builder"
        and ownership_profile == "claude-first"
        and durable_output_required
        and data.get("bounded_exploration_scope") is True
        and data.get("goal_clarity") in ("high", "medium")
    )
    explicit_claude = owner_hint in ("claude", "claude-builder")
    economical_delegation = economy_gate["status"] in ("pass", "canary")

    confirmed_high_risk = False
    effective_risks = data.get("effective_risks")
    if isinstance(effective_risks, dict):
        confirmed_high_risk = any(effective_risks.get(key) == "yes" for key in HIGH)
    high_risk_codex_bias = bool(
        confirmed_high_risk
        and data.get("task_role") == "core-semantic"
        and data.get("allow_high_risk_claude") is not True
    )

    inferred_claude_role = requested_role
    if inferred_claude_role not in (
        "solution-planner", "exploratory-builder", "batch-builder", "execution-builder"
    ):
        if data.get("mechanical_batch") is True:
            inferred_claude_role = "batch-builder"
        elif (
            open_solution_path and large_or_multiphase
            and data.get("solution_contract_frozen") is not True
            and durable_structured_output
        ):
            inferred_claude_role = "solution-planner"
        elif open_solution_path and data.get("bounded_exploration_scope") is True:
            inferred_claude_role = "exploratory-builder"
        else:
            inferred_claude_role = "execution-builder"
    elif inferred_claude_role == "solution-planner" and not solution_planner_candidate:
        inferred_claude_role = (
            "exploratory-builder"
            if open_solution_path and data.get("bounded_exploration_scope") is True
            else "execution-builder"
        )

    execution_owner = "codex-fast-path"
    claude_role = "none"
    if delegation_value is False or owner_hint in ("codex", "codex-fast-path"):
        owner_source = "explicit" if explicit_owner_hint else "codex-default"
    elif explicit_read_only_task and not readonly_value:
        owner_source = "readonly-without-durable-value"
    elif high_risk_codex_bias:
        owner_source = "confirmed-high-risk-core-codex-bias"
    elif ownership_profile == "claude-first" and (
        not explicit_read_only_task or solution_planner_candidate
    ):
        execution_owner, claude_role = "claude-builder", inferred_claude_role
        owner_source = "explicit-human-owner" if explicit_claude else "claude-first-default"
    elif solution_planner_candidate and (economical_delegation or explicit_claude):
        execution_owner, claude_role = "claude-builder", "solution-planner"
        owner_source = "solution-planner-positive-gate"
    elif batch_candidate and (economical_delegation or explicit_claude):
        execution_owner, claude_role = "claude-builder", "batch-builder"
        owner_source = "batch-positive-gate"
    elif execution_candidate and (economical_delegation or explicit_claude):
        execution_owner, claude_role = "claude-builder", "execution-builder"
        owner_source = "auxiliary-positive-gate"
    elif exploratory_candidate and (economical_delegation or explicit_claude):
        execution_owner, claude_role = "claude-builder", "exploratory-builder"
        owner_source = "exploratory-positive-gate"
    elif explicit_claude:
        # Explicit human ownership remains authoritative, but broad/open
        # implementation is converted to planning when the planner gate fits.
        execution_owner = "claude-builder"
        claude_role = "solution-planner" if solution_planner_candidate else (
            requested_role if requested_role in ("execution-builder", "batch-builder") else "execution-builder"
        )
        owner_source = "explicit-human-owner"
    elif economy_gate["status"] == "reject":
        owner_source = "economy-gate"
    elif solution_planner_requested or requested_role in ("execution-builder", "batch-builder", "exploratory-builder"):
        owner_source = "claude-positive-gate-failed"

    if execution_owner == "codex-fast-path":
        delegation_mode = "rejected" if economy_gate["status"] == "reject" else "direct"
    elif ownership_profile == "claude-first" and not explicit_owner_hint:
        delegation_mode = "claude-first"
    elif economy_gate["status"] == "canary":
        delegation_mode = "canary"
    elif economy_gate["status"] == "pass":
        delegation_mode = "proven"
    elif explicit_owner_hint:
        delegation_mode = "explicit"
    else:
        delegation_mode = "unproven"

    # Spark remains advisory. Request an estimate only when structured output
    # replaces Codex analysis or changes the selected Claude role.
    deterministic_owner = bool(data.get("deterministic_owner_decision")) and owner_hint in (
        "codex", "codex-fast-path", "claude", "claude-builder",
    )
    tiny_direct = bool(
        delegation_value is False
        and execution_owner == "codex-fast-path"
        and isinstance(file_count, (int, float)) and file_count <= 2
        and isinstance(diff_lines, (int, float)) and diff_lines <= 100
        and exact
        and data.get("solution_clarity") == "high"
        and data.get("context_scope") in ("local", "bounded")
        and data.get("codex_review_scope") == "full"
    )
    spark_requested = data.get("spark_route_requested") is True
    if deterministic_owner:
        spark_action, spark_reason = "skip", "explicit-deterministic-owner"
    elif tiny_direct:
        spark_action, spark_reason = "skip", "sized-tiny-fastpath"
    elif spark_requested and (
        solution_planner_candidate or batch_candidate or execution_candidate
        or exploratory_candidate or execution_owner == "claude-builder" or recovery
    ):
        spark_action, spark_reason = "estimate", "explicit-claude-candidate-estimate"
    elif economy_gate["status"] in ("pass", "reject", "canary"):
        spark_action, spark_reason = "skip", "deterministic-economy-gate"
    else:
        spark_action, spark_reason = (
            ("skip", "claude-first-deterministic-route")
            if execution_owner == "claude-builder"
            else ("skip", "codex-default-no-delegation-candidate")
        )

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
    checker_model_dispatch = (
        bool(checker_value_reasons)
        and not single_pass_allowed
        and delegation_mode != "canary"
    )
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
                ["final-review"] if ownership_profile == "claude-first" and c
                else (["architecture-review", "final-review"] if lane == "assured"
                      else (["final-review"] if c else []))
            ),
        },
        "execution": {
            "owner": execution_owner,
            "owner_source": owner_source,
            "ownership_profile": ownership_profile,
            "claude_role": claude_role,
            "builder_mode": (
                "solution-planning" if claude_role == "solution-planner"
                else ("batch" if claude_role == "batch-builder"
                      else ("exploratory" if claude_role == "exploratory-builder"
                            else ("execution-only" if claude_role == "execution-builder" else "standard")))
            ),
            "durable_output_required": (
                durable_structured_output if claude_role == "solution-planner"
                else (durable_output_required if claude_role in (
                    "execution-builder", "exploratory-builder", "batch-builder"
                ) else False)
            ) or execution_owner == "claude-builder",
            "read_only_delegation_allowed": readonly_value,
            "historical_calibration": calibration if isinstance(calibration, dict) else {},
            "economy_gate": economy_gate,
            "delegation_mode": delegation_mode,
            "parallel_release_allowed": False,
            "portfolio_concurrency_owner": "independent-user-terminals",
            "model_failure_limit": 1 if delegation_mode == "canary" else 2,
            "transport_retry_within_call_budget": True,
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
        "planning": {
            "strategy": (
                "claude-converge-codex-freeze"
                if claude_role == "solution-planner"
                else ("claude-owned-implementation" if execution_owner == "claude-builder" else "codex-owned")
            ),
            "draft_owner": "claude" if execution_owner == "claude-builder" else "codex",
            "adversarial_review_owner": "codex",
            "max_adversarial_review_rounds": 1,
            "solution_contract_required": claude_role == "solution-planner",
            "implementation_replan_allowed_after_freeze": False,
            "nonblocking_findings_destination": "backlog",
        },
        "precard_estimator": {
            "spark_action": spark_action,
            "reason_code": spark_reason,
            "decision_complete": spark_action == "skip",
        },
        "estimated_efficiency": {
            "first_pass_confidence": data.get("first_pass_confidence", "medium"),
            "context_cache_reusable": bool(data.get("context_cache")),
        },
        "quota_mode": qm,
        "latency_mode": lm,
        "ownership_profile": ownership_profile,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Route a task by risk and scope.")
    p.add_argument("input", help="Path to collected-facts or hints JSON.")
    a = p.parse_args()
    data = json.loads(open(a.input, encoding="utf-8").read())
    print(json.dumps(route(data), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
