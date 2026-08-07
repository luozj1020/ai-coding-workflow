#!/usr/bin/env python3
"""Deterministically classify a Claude round for retry/takeover accounting."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

TRANSPORT_RE = re.compile(
    r"api error|connection (?:closed|reset|refused)|econn|dns|tls|socket|timed? ?out|timeout|fetch failed|network",
    re.I,
)
APPROVAL_RE = re.compile(
    r"approval|permission|sandbox|not permitted|contains simple_expansion|"
    r"workspace.{0,40}not.{0,20}trusted",
    re.I,
)


def classify(
    *, exit_code: int, outcome: str, semantic_error: bool, diff_changes: int,
    valid_report: bool, progress: str, direction: str, error_text: str,
    blocker_kind: str = "none", advisor_used: bool = False,
    delegation_mode: str = "unknown", retry_ordinal: int = 0,
    task_mode: str = "unknown", report_consistency: str = "not-run",
    attempt_identity: Optional[dict] = None,
) -> dict:
    report_mismatch = report_consistency in {"contradictory", "error", "role-mismatch"}
    report_useful = valid_report and not report_mismatch and (
        diff_changes > 0 or task_mode in {"checker-test", "control-plane", "solution-planning"}
    )
    useful = diff_changes > 0 or report_useful or progress == "useful"
    interacted = useful or valid_report or progress in {"acknowledgement", "blocker"}
    transport = outcome != "execution_timeout" and (bool(TRANSPORT_RE.search(error_text)) or outcome in {
        "api_error", "api_error_without_diff", "network_error", "timeout"
    })
    approval = bool(APPROVAL_RE.search(error_text)) or outcome == "approval_blocked"

    if outcome == "runtime_evidence_error":
        failure, action, counts = "control-plane-evidence-error", "repair-runtime-before-retry", False
    elif direction == "off-plan":
        failure, action, counts = "direction-deviation", "interrupt-and-narrow", True
    elif report_mismatch and diff_changes == 0:
        failure, action, counts = "report-evidence-mismatch", "narrow-and-redispatch-once", True
    elif useful:
        failure = "none" if outcome in {"success", "passed"} else "recoverable-evidence"
        action, counts = "review-existing-evidence", False
    elif approval:
        failure, action, counts = "external-approval-blocker", "preserve-and-rerun-exact-command", False
    elif outcome == "execution_timeout" and not useful and progress != "blocker":
        failure, action, counts = "model-no-progress", "narrow-and-redispatch-once", True
    elif transport and not interacted:
        failure, counts = "transient-transport", False
        action = "retry-same-worktree-once" if retry_ordinal < 1 else "fallback-local-or-reroute"
    elif progress == "acknowledgement":
        failure, action, counts = "acknowledgement-only", "narrow-and-redispatch-once", True
    elif exit_code == 0 and not semantic_error:
        failure, action, counts = "model-no-progress", "narrow-and-redispatch-once", True
    else:
        failure, action, counts = "unclassified-execution-failure", "inspect-evidence-before-counting", False

    economic_stop_loss = bool(
        delegation_mode == "canary"
        and counts
        and failure in {"direction-deviation", "acknowledgement-only", "model-no-progress"}
    )
    if economic_stop_loss:
        action = "reroute-before-redispatch"

    # Advisor continuation eligibility
    rejection_reason = None
    if not useful:
        rejection_reason = "no-useful-evidence"
    elif direction != "on-plan":
        rejection_reason = "direction-not-on-plan"
    elif blocker_kind != "semantic":
        rejection_reason = "blocker-not-semantic"
    elif transport:
        rejection_reason = "transport-failure"
    elif approval:
        rejection_reason = "approval-blocked"
    elif advisor_used:
        rejection_reason = "advisor-already-used"

    return {
        "schema_version": 1,
        "interaction_state": "useful-progress" if useful else ("established" if interacted else "not-established"),
        "failure_class": failure,
        "counts_toward_takeover": counts,
        "recommended_action": action,
        "same_worktree_retry_eligible": failure == "transient-transport" and retry_ordinal < 1,
        "retry_ordinal": retry_ordinal,
        "retry_budget_remaining": max(0, 1 - retry_ordinal),
        "successful_interaction_is_authoritative": True,
        "advisor_continuation_eligible": rejection_reason is None,
        "advisor_rejection_reason": rejection_reason,
        "delegation_mode": delegation_mode,
        "economic_stop_loss": economic_stop_loss,
        "reroute_required": economic_stop_loss,
        "takeover_authorized": False,
        "task_mode": task_mode,
        "report_consistency": report_consistency,
        "attempt_identity": attempt_identity,
    }


def _task_card_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exit-code", type=int, required=True)
    p.add_argument("--outcome", default="unknown")
    p.add_argument("--semantic-error", action="store_true")
    p.add_argument("--diff-changes", type=int, default=0)
    p.add_argument("--valid-report", action="store_true")
    p.add_argument("--progress", choices=["none", "acknowledgement", "blocker", "useful"], default="none")
    p.add_argument("--direction", choices=["unknown", "on-plan", "off-plan"], default="unknown")
    p.add_argument("--blocker-kind", choices=["none", "semantic", "transport", "approval", "direction", "unknown"], default="none")
    p.add_argument("--advisor-used", action="store_true")
    p.add_argument("--delegation-mode", choices=["unknown", "unproven", "canary", "proven", "explicit", "direct", "rejected"], default="unknown")
    p.add_argument("--retry-ordinal", type=int, default=0)
    p.add_argument("--task-mode", default="unknown")
    p.add_argument("--report-consistency", default="not-run")
    p.add_argument("--error-text-file", type=Path)
    p.add_argument("--task-id")
    p.add_argument("--lineage-root-task-id")
    p.add_argument("--task-card", type=Path)
    p.add_argument("--source-base-commit")
    p.add_argument("--execution-base-commit")
    p.add_argument("--source-repository")
    p.add_argument("--worktree")
    p.add_argument("--claude-session-id")
    p.add_argument("--retry-of")
    args = p.parse_args(argv)
    error_text = ""
    if args.error_text_file:
        error_text = args.error_text_file.read_text(encoding="utf-8", errors="replace")[:16384]
    identity_values = (
        args.task_id, args.lineage_root_task_id, args.task_card,
        args.source_base_commit, args.execution_base_commit,
        args.source_repository, args.worktree, args.claude_session_id,
    )
    attempt_identity = None
    if any(value is not None for value in identity_values):
        if not all(identity_values):
            p.error(
                "attempt identity requires task, lineage, card, baseline, repository, worktree, and session"
            )
        if not args.task_card.is_file():
            p.error("attempt identity task card is unavailable")
        attempt_identity = {
            "schema": "aiwf-attempt-identity-v1",
            "task_id": args.task_id,
            "lineage_root_task_id": args.lineage_root_task_id,
            "task_card_sha256": _task_card_sha256(args.task_card),
            "source_base_commit": args.source_base_commit,
            "execution_base_commit": args.execution_base_commit,
            "source_repository": str(Path(args.source_repository).resolve()),
            "worktree": str(Path(args.worktree).resolve()),
            "claude_session_id": args.claude_session_id,
            "retry_of": args.retry_of or None,
        }
    print(json.dumps(classify(
        exit_code=args.exit_code, outcome=args.outcome, semantic_error=args.semantic_error,
        diff_changes=args.diff_changes, valid_report=args.valid_report, progress=args.progress,
        direction=args.direction, error_text=error_text,
        blocker_kind=args.blocker_kind, advisor_used=args.advisor_used,
        delegation_mode=args.delegation_mode, retry_ordinal=max(0, args.retry_ordinal),
        task_mode=args.task_mode, report_consistency=args.report_consistency,
        attempt_identity=attempt_identity,
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
