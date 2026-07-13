#!/usr/bin/env python3
"""Deterministically classify a Claude round for retry/takeover accounting."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional

TRANSPORT_RE = re.compile(
    r"api error|connection (?:closed|reset|refused)|econn|dns|tls|socket|timed? ?out|timeout|fetch failed|network",
    re.I,
)
APPROVAL_RE = re.compile(r"approval|permission|sandbox|not permitted", re.I)


def classify(
    *, exit_code: int, outcome: str, semantic_error: bool, diff_changes: int,
    valid_report: bool, progress: str, direction: str, error_text: str,
    blocker_kind: str = "none", advisor_used: bool = False,
) -> dict:
    useful = diff_changes > 0 or valid_report or progress == "useful"
    interacted = useful or progress in {"acknowledgement", "blocker"}
    transport = bool(TRANSPORT_RE.search(error_text)) or outcome in {
        "api_error", "api_error_without_diff", "network_error", "timeout"
    }
    approval = bool(APPROVAL_RE.search(error_text)) or outcome == "approval_blocked"

    if direction == "off-plan":
        failure, action, counts = "direction-deviation", "interrupt-and-narrow", True
    elif useful:
        failure = "none" if outcome in {"success", "passed"} else "recoverable-evidence"
        action, counts = "review-existing-evidence", False
    elif approval:
        failure, action, counts = "external-approval-blocker", "preserve-and-rerun-exact-command", False
    elif transport and not interacted:
        failure, action, counts = "transient-transport", "retry-same-worktree-once", False
    elif progress == "acknowledgement":
        failure, action, counts = "acknowledgement-only", "narrow-and-redispatch-once", True
    elif exit_code == 0 and not semantic_error:
        failure, action, counts = "model-no-progress", "narrow-and-redispatch-once", True
    else:
        failure, action, counts = "unclassified-execution-failure", "inspect-evidence-before-counting", False

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
        "same_worktree_retry_eligible": failure == "transient-transport",
        "successful_interaction_is_authoritative": True,
        "advisor_continuation_eligible": rejection_reason is None,
        "advisor_rejection_reason": rejection_reason,
    }


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
    p.add_argument("--error-text-file", type=Path)
    args = p.parse_args(argv)
    error_text = ""
    if args.error_text_file:
        error_text = args.error_text_file.read_text(encoding="utf-8", errors="replace")[:16384]
    print(json.dumps(classify(
        exit_code=args.exit_code, outcome=args.outcome, semantic_error=args.semantic_error,
        diff_changes=args.diff_changes, valid_report=args.valid_report, progress=args.progress,
        direction=args.direction, error_text=error_text,
        blocker_kind=args.blocker_kind, advisor_used=args.advisor_used,
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
