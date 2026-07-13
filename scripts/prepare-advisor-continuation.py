#!/usr/bin/env python3
"""Prepare an advisor continuation packet or decision for a blocked Claude attempt.

Reads classification JSON, validates eligibility, and writes advisor artifacts.
Python 3.9-compatible, no external dependencies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

MAX_EVIDENCE_FILE_BYTES = 100 * 1024  # 100 KB per evidence file
MAX_TOTAL_EVIDENCE_BYTES = 500 * 1024  # 500 KB total


def _is_within(child: Path, parent: Path) -> bool:
    """Return True if child is inside parent (resolved)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _load_evidence(paths: list[str], worktree: Path, output: Path) -> tuple[list[dict], Optional[str]]:
    """Load and validate evidence files. Returns (entries, error_or_none)."""
    entries: list[dict] = []
    total = 0
    for raw in paths:
        p = Path(raw).resolve()
        if not p.is_file():
            return [], f"evidence-not-found: {raw}"
        if not (_is_within(p, worktree) or _is_within(p, output)):
            return [], f"evidence-outside-scope: {raw}"
        size = p.stat().st_size
        if size > MAX_EVIDENCE_FILE_BYTES:
            return [], f"evidence-oversized: {raw} ({size} > {MAX_EVIDENCE_FILE_BYTES})"
        total += size
        if total > MAX_TOTAL_EVIDENCE_BYTES:
            return [], f"total-evidence-oversized ({total} > {MAX_TOTAL_EVIDENCE_BYTES})"
        text = p.read_text(encoding="utf-8", errors="replace")[:MAX_EVIDENCE_FILE_BYTES]
        entries.append({"path": str(p), "size": size, "content": text})
    return entries, None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--classification-file", type=Path, required=True,
                   help="Path to classification JSON from classify-claude-attempt")
    p.add_argument("--task-id", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--question", required=True,
                   help="Non-empty blocker question for the advisor")
    p.add_argument("--evidence", action="append", default=[],
                   help="Path to evidence file (repeatable)")
    p.add_argument("--forbidden", action="append", default=[],
                   help="Forbidden path (repeatable)")
    p.add_argument("--completed-work", required=True,
                   help="Summary of work completed so far")
    p.add_argument("--response-file", type=Path, default=None,
                   help="Optional path to advisor response file")
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args(argv)

    # Load classification
    classification = json.loads(args.classification_file.read_text(encoding="utf-8"))

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Validate question
    question = args.question.strip()
    if not question:
        _write_json(output_dir / "advisor-decision.json", {
            "eligible": False, "reason": "empty-question",
            "task_id": args.task_id,
        })
        return 2

    # Validate worktree exists
    worktree = args.worktree.resolve()
    if not worktree.is_dir():
        _write_json(output_dir / "advisor-decision.json", {
            "eligible": False, "reason": "worktree-not-found",
            "task_id": args.task_id,
        })
        return 2

    # Load and validate evidence
    evidence, evidence_err = _load_evidence(args.evidence, worktree, output_dir)
    if evidence_err:
        _write_json(output_dir / "advisor-decision.json", {
            "eligible": False, "reason": evidence_err,
            "task_id": args.task_id,
        })
        return 2

    # Validate response file if provided
    response_text: Optional[str] = None
    if args.response_file is not None:
        rp = args.response_file.resolve()
        if not rp.is_file():
            _write_json(output_dir / "advisor-decision.json", {
                "eligible": False, "reason": "response-file-not-found",
                "task_id": args.task_id,
            })
            return 2
        response_text = rp.read_text(encoding="utf-8", errors="replace").strip()
        if not response_text:
            _write_json(output_dir / "advisor-decision.json", {
                "eligible": False, "reason": "empty-response",
                "task_id": args.task_id,
            })
            return 2

    # Check eligibility from classification
    eligible = classification.get("advisor_continuation_eligible", False)
    rejection_reason = classification.get("advisor_rejection_reason")

    if not eligible:
        _write_json(output_dir / "advisor-decision.json", {
            "eligible": False,
            "reason": rejection_reason or "not-eligible",
            "task_id": args.task_id,
            "failure_class": classification.get("failure_class"),
        })
        return 2

    # Eligible — write packet
    packet = {
        "task_id": args.task_id,
        "phase": args.phase,
        "worktree": str(worktree),
        "blocker_question": question,
        "evidence": evidence,
        "forbidden_paths": args.forbidden,
        "completed_work": args.completed_work,
        "call_cap": 1,
        "stop_conditions": [
            "stop after one advisor response",
            "stop if the response expands scope or conflicts with local evidence",
            "stop if the semantic blocker remains unresolved",
        ],
        "classification": {
            "failure_class": classification.get("failure_class"),
            "interaction_state": classification.get("interaction_state"),
        },
    }
    _write_json(output_dir / "advisor-packet.json", packet)

    # Write packet markdown
    md_lines = [
        f"# Advisor Packet: {args.task_id}",
        "",
        f"**Phase:** {args.phase}",
        f"**Worktree:** `{worktree}`",
        "",
        "## Blocker Question",
        "",
        question,
        "",
        "## Completed Work",
        "",
        args.completed_work,
        "",
        "## Call Cap and Stop Conditions",
        "",
        "- Advisor call cap: 1",
        "- Stop after one advisor response.",
        "- Stop if the response expands scope, conflicts with local evidence, or leaves the blocker unresolved.",
        "",
    ]
    if evidence:
        md_lines += ["## Evidence", ""]
        for e in evidence:
            md_lines.append(f"- `{e['path']}` ({e['size']} bytes)")
        md_lines.append("")
    if args.forbidden:
        md_lines += ["## Forbidden Paths", ""]
        for fp in args.forbidden:
            md_lines.append(f"- `{fp}`")
        md_lines.append("")

    (output_dir / "advisor-packet.md").write_text(
        "\n".join(md_lines) + "\n", encoding="utf-8"
    )

    # If response provided, write decision and continuation card
    if response_text is not None:
        _write_json(output_dir / "advisor-decision.json", {
            "eligible": True,
            "reason": "advisor-continuation-authorized",
            "task_id": args.task_id,
            "response_summary": response_text[:2048],
        })

        card_lines = [
            f"# Advisor Continuation Card: {args.task_id}",
            "",
            "## Instructions",
            "",
            "This is a **same-worktree retry**. Do not create a new worktree.",
            "",
            "## Blocker Answer",
            "",
            response_text,
            "",
            "## Retained Scope",
            "",
            args.completed_work,
            "",
            "## Forbidden Paths",
            "",
        ]
        for fp in args.forbidden:
            card_lines.append(f"- `{fp}`")
        card_lines += [
            "",
            "## Rules",
            "",
            "- Do **not** repeat planning; continue from current progress.",
            "- Update `CLAUDE_PROGRESS.md` with your continuation status.",
            "- Update `CLAUDE_REPORT.md` when finished.",
            "",
        ]
        (output_dir / "advisor-continuation-card.md").write_text(
            "\n".join(card_lines) + "\n", encoding="utf-8"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
