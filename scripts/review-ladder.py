#!/usr/bin/env python3
"""Deterministic review ladder entry point.

Integrates route-recovery.py and select-review-tier.py into one
deterministic ladder entry point with bounded review packet support.

Routing rules:
- environment/network/permission/dependency -> local-or-human
- first explicit compile/test failure -> Claude revision
- ambiguous failure -> Spark triage
- high risk, repeated failure, architecture issue -> Codex

Spark decisions limited to: local-accept, claude-revision,
codex-escalation, human-review.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent


def _load_module(name: str, filename: str):
    """Load a sibling script as a module."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate_acceptance = _load_module("evaluate_acceptance", "evaluate-acceptance.py")
route_recovery = _load_module("route_recovery", "route-recovery.py")
select_review_tier = _load_module("select_review_tier", "select-review-tier.py")
build_review_packet = _load_module("build_review_packet", "build_review_packet.py")


# ---------------------------------------------------------------------------
# Spark decision enum
# ---------------------------------------------------------------------------

VALID_SPARK_DECISIONS = {
    "local-accept",
    "claude-revision",
    "codex-escalation",
    "human-review",
}


# ---------------------------------------------------------------------------
# Review packet size limits
# ---------------------------------------------------------------------------

PACKET_SIZE_LIMITS = {
    "standard": 32 * 1024,   # 32 KB
    "assured": 64 * 1024,    # 64 KB
}


# ---------------------------------------------------------------------------
# Recovery routing
# ---------------------------------------------------------------------------

def classify_recovery(
    classification: str,
    failure_count: int = 1,
    assured: bool = False,
    high_risk: bool = False,
    architecture_issue: bool = False,
) -> Dict[str, Any]:
    """Route recovery based on failure classification.

    Returns {owner, model, reason}.
    """
    # Environment/network/permission/dependency -> local-or-human
    if classification in {"environment", "dependency", "permission", "network", "timeout"}:
        return {
            "owner": "local-or-human",
            "model": None,
            "reason": f"Infrastructure issue ({classification}) cannot be resolved by model",
        }

    # First explicit compile/test failure -> Claude revision
    if classification in {"compile", "test"} and failure_count < 2:
        return {
            "owner": "claude-revision",
            "model": "claude",
            "reason": f"First {classification} failure, allowing Claude to fix",
        }

    # High risk, repeated failure, architecture issue -> Codex
    if assured or high_risk or architecture_issue or failure_count >= 3:
        return {
            "owner": "codex",
            "model": "codex",
            "reason": "Escalating to Codex: " + (
                "assured lane" if assured else
                "high risk" if high_risk else
                "architecture issue" if architecture_issue else
                f"repeated failure (count={failure_count})"
            ),
        }

    # Ambiguous failure -> Spark triage
    return {
        "owner": "spark-triage",
        "model": "spark",
        "reason": "Ambiguous failure, Spark triage to determine path",
    }


# ---------------------------------------------------------------------------
# Spark decision validation
# ---------------------------------------------------------------------------

def validate_spark_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a Spark decision against the allowed enum.

    Returns validated decision with metrics.
    Raises ValueError if decision is invalid.
    """
    action = decision.get("action")
    if action not in VALID_SPARK_DECISIONS:
        raise ValueError(
            f"Invalid Spark decision '{action}'; "
            f"must be one of {VALID_SPARK_DECISIONS}"
        )

    # Build validated output with metrics
    route_changed = decision.get("route_changed", False)
    codex_call_avoided = decision.get("codex_call_avoided", False)
    claude_retry_avoided = decision.get("claude_retry_avoided", False)

    return {
        "schema_version": 1,
        "action": action,
        "reasoning": decision.get("reasoning", ""),
        "route_changed": route_changed,
        "codex_call_avoided": codex_call_avoided,
        "claude_retry_avoided": claude_retry_avoided,
    }


# ---------------------------------------------------------------------------
# Bounded review packet building
# ---------------------------------------------------------------------------

def build_bounded_packet(
    run_dir: Path,
    lane: str = "standard",
    task_card: Optional[Path] = None,
    diff_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a bounded review packet for Codex L2 review.

    Packet contains: Goal, Risk, Acceptance Matrix, Validation Summary,
    Changed Files, relevant Diff Hunks, unresolved issues,
    previous-decision delta.

    Size limits: Standard 32 KB, Assured 64 KB.
    """
    max_bytes = PACKET_SIZE_LIMITS.get(lane, PACKET_SIZE_LIMITS["standard"])

    packet = build_review_packet.build_review_packet(
        run_dir,
        max_prompt_bytes=max_bytes,
        task_card=task_card,
        diff_file=diff_file,
    )

    # Add lane and size metadata
    packet["lane"] = lane
    packet["max_bytes"] = max_bytes
    packet["actual_bytes"] = len(json.dumps(packet).encode("utf-8"))

    return packet


# ---------------------------------------------------------------------------
# Full ladder evaluation
# ---------------------------------------------------------------------------

def evaluate_ladder(
    task: Dict[str, Any],
    validation_results: Optional[Dict[str, Any]] = None,
    artifact_manifest: Optional[Dict[str, Any]] = None,
    diff_evidence: Optional[Dict[str, Any]] = None,
    remote_evidence: Optional[Dict[str, Any]] = None,
    failure_count: int = 1,
    assured: bool = False,
    high_risk: bool = False,
    architecture_issue: bool = False,
) -> Dict[str, Any]:
    """Full deterministic review ladder evaluation.

    1. Evaluate acceptance (L0)
    2. If mechanical failures -> local revision
    3. If passed -> L0 local
    4. If partial -> determine recovery route
    5. Build tier decision
    """
    # Step 1: L0 acceptance evaluation
    acceptance = evaluate_acceptance.evaluate_task(
        task=task,
        validation_results=validation_results,
        artifact_manifest=artifact_manifest,
        diff_evidence=diff_evidence,
        remote_evidence=remote_evidence,
    )

    mechanical_failures = acceptance.get("mechanical_failures", [])

    # Step 2: Mechanical failures -> local revision, no model calls
    if mechanical_failures:
        return {
            "schema_version": 1,
            "l0_acceptance": acceptance,
            "tier": "L0-local",
            "action": "local-revision",
            "recovery": None,
            "model_authorized": None,
            "mechanical_failures": mechanical_failures,
            "model_call_prohibited": True,
            "reason": f"Mechanical failures: {mechanical_failures}",
        }

    # Step 3: All criteria satisfied -> L0 local
    if acceptance["status"] == "passed":
        return {
            "schema_version": 1,
            "l0_acceptance": acceptance,
            "tier": "L0-local",
            "action": "human-review",
            "recovery": None,
            "model_authorized": None,
            "mechanical_failures": [],
            "model_call_prohibited": False,
            "reason": "All acceptance criteria satisfied",
        }

    # Step 4: Partial/failed -> determine recovery route
    # Infer classification from acceptance state
    classification = _infer_classification(acceptance, diff_evidence or {})

    recovery = classify_recovery(
        classification=classification,
        failure_count=failure_count,
        assured=assured,
        high_risk=high_risk,
        architecture_issue=architecture_issue,
    )

    # Step 5: Build tier decision
    if recovery["model"] == "codex":
        tier = "L2-codex"
        action = "codex-review"
    elif recovery["model"] == "spark":
        tier = "L1-spark"
        action = "spark-review"
    elif recovery["model"] == "claude":
        tier = "L0-local"
        action = "claude-revision"
    else:
        tier = "L0-local"
        action = "local-or-human"

    return {
        "schema_version": 1,
        "l0_acceptance": acceptance,
        "tier": tier,
        "action": action,
        "recovery": recovery,
        "model_authorized": recovery["model"],
        "mechanical_failures": [],
        "model_call_prohibited": False,
        "reason": recovery["reason"],
    }


def _infer_classification(
    acceptance: Dict[str, Any],
    diff_evidence: Dict[str, Any],
) -> str:
    """Infer failure classification from acceptance state."""
    # Check for scope violations
    for row in acceptance.get("acceptance_matrix", []):
        if row.get("status") == "failed":
            # Check if it's a validation-linked failure
            if row.get("validation_id"):
                return "test"
    return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic review ladder entry point"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ladder: full evaluation
    ladder_parser = sub.add_parser(
        "ladder",
        help="Full deterministic review ladder evaluation",
    )
    ladder_parser.add_argument("input", help="Input JSON with task, validation, diff evidence")
    ladder_parser.add_argument("--failure-count", type=int, default=1)
    ladder_parser.add_argument("--assured", action="store_true")
    ladder_parser.add_argument("--high-risk", action="store_true")
    ladder_parser.add_argument("--architecture-issue", action="store_true")

    # recovery: recovery routing only
    recovery_parser = sub.add_parser(
        "recovery",
        help="Recovery routing based on failure classification",
    )
    recovery_parser.add_argument("classification", help="Failure classification")
    recovery_parser.add_argument("--failure-count", type=int, default=1)
    recovery_parser.add_argument("--assured", action="store_true")
    recovery_parser.add_argument("--high-risk", action="store_true")
    recovery_parser.add_argument("--architecture-issue", action="store_true")

    # tier: tier selection only
    tier_parser = sub.add_parser(
        "tier",
        help="Select review tier from acceptance result",
    )
    tier_parser.add_argument("input", help="Acceptance result JSON")

    # spark-validate: validate Spark decision
    spark_parser = sub.add_parser(
        "spark-validate",
        help="Validate a Spark decision against allowed enum",
    )
    spark_parser.add_argument("input", help="Spark decision JSON")

    # packet: build bounded review packet
    packet_parser = sub.add_parser(
        "packet",
        help="Build bounded review packet for Codex",
    )
    packet_parser.add_argument("--run-dir", required=True, help="Run directory")
    packet_parser.add_argument("--lane", default="standard", choices=["standard", "assured"])
    packet_parser.add_argument("--task-card", help="Task card path")
    packet_parser.add_argument("--diff-file", help="Diff file path")

    args = parser.parse_args()

    if args.command == "ladder":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = evaluate_ladder(
            task=data.get("task", {}),
            validation_results=data.get("validation_results"),
            artifact_manifest=data.get("artifact_manifest"),
            diff_evidence=data.get("diff_evidence", {}),
            remote_evidence=data.get("remote_evidence"),
            failure_count=args.failure_count,
            assured=args.assured,
            high_risk=args.high_risk,
            architecture_issue=args.architecture_issue,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0

    elif args.command == "recovery":
        result = classify_recovery(
            classification=args.classification,
            failure_count=args.failure_count,
            assured=args.assured,
            high_risk=args.high_risk,
            architecture_issue=args.architecture_issue,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0

    elif args.command == "tier":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = select_review_tier.select_tier(data)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0

    elif args.command == "spark-validate":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = validate_spark_decision(data)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0

    elif args.command == "packet":
        run_dir = Path(args.run_dir)
        task_card = Path(args.task_card) if args.task_card else None
        diff_file = Path(args.diff_file) if args.diff_file else None
        result = build_bounded_packet(
            run_dir=run_dir,
            lane=args.lane,
            task_card=task_card,
            diff_file=diff_file,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
