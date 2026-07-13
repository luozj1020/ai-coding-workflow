#!/usr/bin/env python3
"""Prepare an advisor continuation packet or decision for a blocked Claude attempt.

Reads classification JSON, validates eligibility, and writes advisor artifacts.
Preserves full evidence in file-backed packet artifacts with content hashes.
Emits a bounded advisor prompt with deterministic truncation.
Only a schema-valid response may produce a continuation card/decision.

Python 3.9-compatible, no external dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from worktree_state_hash import compute_worktree_state_hash as _compute_worktree_state_hash

MAX_EVIDENCE_FILE_BYTES = 100 * 1024  # 100 KB per evidence file
MAX_TOTAL_EVIDENCE_BYTES = 500 * 1024  # 500 KB total

# Default prompt size caps per advisor type
PROMPT_CAPS = {
    "spark": 16 * 1024,   # 16 KiB
    "codex": 32 * 1024,   # 32 KiB
    "human": 32 * 1024,   # 32 KiB (for display)
}


def _is_within(child: Path, parent: Path) -> bool:
    """Return True if child is inside parent (resolved)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _content_hash(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _load_evidence(
    paths: List[str], worktree: Path, output: Path
) -> Tuple[List[Dict], Optional[str]]:
    """Load and validate evidence files. Returns (entries, error_or_none).

    Each entry includes path, size, and content hash (not inline content).
    """
    entries: List[Dict] = []
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
        raw_bytes = p.read_bytes()
        content = raw_bytes.decode("utf-8", errors="replace")[:MAX_EVIDENCE_FILE_BYTES]
        hash_val = _content_hash(raw_bytes[:MAX_EVIDENCE_FILE_BYTES])
        entries.append({
            "path": str(p),
            "size": size,
            "content_hash": hash_val,
            "content": content,
        })
    return entries, None


def _compute_diff_hash(worktree: Path) -> str:
    """Compute canonical worktree-state hash for continuation binding.

    Deterministically binds HEAD identity, unstaged/staged diff content,
    untracked file paths and bytes, and binary changes.  Excludes only
    known workflow control artifacts.
    """
    return _compute_worktree_state_hash(worktree)


def _compute_evidence_hash(evidence_entries: List[Dict]) -> str:
    """Compute a stable hash over all evidence content hashes."""
    if not evidence_entries:
        return _content_hash(b"no-evidence")
    combined = "|".join(
        f"{e['path']}:{e['content_hash']}" for e in evidence_entries
    )
    return _content_hash(combined.encode("utf-8"))


def _generate_request_id(task_id: str, phase: str, blocker_question: str) -> str:
    """Generate a stable request ID from packet contents."""
    combined = f"{task_id}|{phase}|{blocker_question}"
    return _content_hash(combined.encode("utf-8"))[:32]


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _truncate_text(text: str, max_bytes: int, label: str) -> Tuple[str, bool]:
    """Truncate text to fit within max_bytes, respecting UTF-8 boundaries.

    Returns (truncated_text, was_truncated).
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # Truncate at byte boundary, then decode with replacement
    truncated_bytes = encoded[:max_bytes]
    truncated_text = truncated_bytes.decode("utf-8", errors="ignore")
    return truncated_text + f"\n... [{label} truncated at {max_bytes} bytes]", True


def _build_bounded_prompt(
    *,
    task_id: str,
    request_id: str,
    phase: str,
    worktree: Path,
    base_commit: str,
    diff_hash: str,
    evidence_hash: str,
    blocker_question: str,
    completed_work: str,
    evidence_entries: List[Dict],
    forbidden_paths: List[str],
    allowed_changes: Optional[List[str]],
    advisor: str,
    prompt_cap: int,
) -> Tuple[str, Dict]:
    """Build a bounded advisor prompt that fits within prompt_cap bytes.

    Returns (prompt_text, truncation_manifest).
    """
    truncation_manifest: Dict[str, bool] = {}

    # Build sections in priority order
    sections: List[str] = []

    # Header
    header = (
        f"# Advisor Packet: {task_id}\n\n"
        f"**Request ID:** {request_id}\n"
        f"**Phase:** {phase}\n"
        f"**Advisor:** {advisor}\n"
        f"**Base Commit:** {base_commit}\n"
        f"**Diff Hash:** {diff_hash}\n"
        f"**Evidence Hash:** {evidence_hash}\n\n"
    )
    sections.append(header)

    # Blocker question (always included, high priority)
    blocker_section = f"## Blocker Question\n\n{blocker_question}\n\n"
    sections.append(blocker_section)

    # Completed work summary (truncatable)
    completed_section = f"## Completed Work\n\n{completed_work}\n\n"
    completed_truncated, was_truncated = _truncate_text(
        completed_section, max(512, prompt_cap // 8), "completed-work"
    )
    truncation_manifest["completed_work"] = was_truncated
    sections.append(completed_truncated)

    # Scope constraints
    scope_lines = ["## Scope Constraints\n\n"]
    if allowed_changes:
        scope_lines.append("### Allowed Changes\n\n")
        for p in allowed_changes:
            scope_lines.append(f"- `{p}`\n")
        scope_lines.append("\n")
    if forbidden_paths:
        scope_lines.append("### Forbidden Paths\n\n")
        for fp in forbidden_paths:
            scope_lines.append(f"- `{fp}`\n")
        scope_lines.append("\n")
    sections.append("".join(scope_lines))

    # Evidence excerpts (truncatable, lowest priority)
    if evidence_entries:
        evidence_lines = ["## Evidence Excerpts\n\n"]
        remaining_budget = max(256, prompt_cap // 4)
        for e in evidence_entries:
            # Include path, hash, and a bounded excerpt
            header_line = f"### `{e['path']}` (hash: {e['content_hash'][:16]}...)\n\n"
            excerpt_bytes = min(remaining_budget, len(e["content"].encode("utf-8")))
            excerpt = e["content"]
            if excerpt_bytes < len(excerpt.encode("utf-8")):
                excerpt_bytes_str = e["content"].encode("utf-8")[:excerpt_bytes].decode("utf-8", errors="ignore")
                excerpt = excerpt_bytes_str + "\n... [truncated]"
                truncation_manifest[f"evidence:{e['path']}"] = True
            else:
                truncation_manifest[f"evidence:{e['path']}"] = False
            evidence_lines.append(header_line + excerpt + "\n\n")
            remaining_budget -= len((header_line + excerpt + "\n\n").encode("utf-8"))
            if remaining_budget <= 0:
                truncation_manifest["evidence:remaining_skipped"] = True
                evidence_lines.append("... [remaining evidence files omitted]\n\n")
                break
        sections.append("".join(evidence_lines))

    # Response schema reminder
    schema_section = (
        "## Response Schema\n\n"
        "Respond with JSON matching the v1 advisor response schema.\n"
        "See the advisor-packet.json for full packet details.\n\n"
    )
    sections.append(schema_section)

    # Concatenate and truncate if needed
    full_prompt = "".join(sections)
    full_bytes = full_prompt.encode("utf-8")
    if len(full_bytes) > prompt_cap:
        # Deterministic truncation: keep header + blocker, truncate evidence
        truncated_prompt, was_truncated = _truncate_text(
            full_prompt, prompt_cap, "advisor-prompt"
        )
        full_prompt = truncated_prompt
        truncation_manifest["prompt"] = was_truncated
    else:
        truncation_manifest["prompt"] = False

    return full_prompt, truncation_manifest


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--classification-file", type=Path, required=True,
                   help="Path to classification JSON from classify-claude-attempt")
    p.add_argument("--task-id", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--base-commit", required=True,
                   help="Base commit hash for the worktree")
    p.add_argument("--question", required=True,
                   help="Non-empty blocker question for the advisor")
    p.add_argument("--evidence", action="append", default=[],
                   help="Path to evidence file (repeatable)")
    p.add_argument("--forbidden", action="append", default=[],
                   help="Forbidden path (repeatable)")
    p.add_argument("--allowed-changes", action="append", default=[],
                   help="Originally allowed change path (repeatable)")
    p.add_argument("--completed-work", required=True,
                   help="Summary of work completed so far")
    p.add_argument("--advisor", default="spark", choices=["spark", "codex", "human"],
                   help="Advisor type for prompt cap selection")
    p.add_argument("--prompt-cap", type=int, default=None,
                   help="Override prompt size cap in bytes")
    p.add_argument("--response-file", type=Path, default=None,
                   help="Optional path to validated advisor response JSON")
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

    # Compute hashes
    diff_hash = _compute_diff_hash(worktree)
    evidence_hash = _compute_evidence_hash(evidence)
    request_id = _generate_request_id(args.task_id, args.phase, question)

    # Determine prompt cap
    prompt_cap = args.prompt_cap or PROMPT_CAPS.get(args.advisor, 32 * 1024)

    # Build bounded prompt
    prompt_text, truncation_manifest = _build_bounded_prompt(
        task_id=args.task_id,
        request_id=request_id,
        phase=args.phase,
        worktree=worktree,
        base_commit=args.base_commit,
        diff_hash=diff_hash,
        evidence_hash=evidence_hash,
        blocker_question=question,
        completed_work=args.completed_work,
        evidence_entries=evidence,
        forbidden_paths=args.forbidden,
        allowed_changes=args.allowed_changes if args.allowed_changes else None,
        advisor=args.advisor,
        prompt_cap=prompt_cap,
    )

    # Eligible — write packet (full evidence, not truncated)
    packet = {
        "task_id": args.task_id,
        "request_id": request_id,
        "phase": args.phase,
        "worktree": str(worktree),
        "base_commit": args.base_commit,
        "diff_hash": diff_hash,
        "evidence_hash": evidence_hash,
        "blocker_question": question,
        "evidence": evidence,
        "forbidden_paths": args.forbidden,
        "allowed_changes": args.allowed_changes,
        "completed_work": args.completed_work,
        "advisor": args.advisor,
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

    # Write bounded prompt
    prompt_path = output_dir / "advisor-prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    # Write truncation manifest
    _write_json(output_dir / "truncation-manifest.json", {
        "request_id": request_id,
        "prompt_cap": prompt_cap,
        "prompt_bytes": len(prompt_text.encode("utf-8")),
        "sections": truncation_manifest,
    })

    # Write packet markdown (summary, not full evidence)
    md_lines = [
        f"# Advisor Packet: {args.task_id}",
        "",
        f"**Request ID:** {request_id}",
        f"**Phase:** {args.phase}",
        f"**Advisor:** {args.advisor}",
        f"**Worktree:** `{worktree}`",
        f"**Base Commit:** `{args.base_commit}`",
        f"**Diff Hash:** `{diff_hash}`",
        f"**Evidence Hash:** `{evidence_hash}`",
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
        md_lines += ["## Evidence Files", ""]
        for e in evidence:
            md_lines.append(f"- `{e['path']}` ({e['size']} bytes, hash: `{e['content_hash'][:16]}...`)")
        md_lines.append("")
    if args.forbidden:
        md_lines += ["## Forbidden Paths", ""]
        for fp in args.forbidden:
            md_lines.append(f"- `{fp}`")
        md_lines.append("")
    if args.allowed_changes:
        md_lines += ["## Allowed Changes", ""]
        for ac in args.allowed_changes:
            md_lines.append(f"- `{ac}`")
        md_lines.append("")

    (output_dir / "advisor-packet.md").write_text(
        "\n".join(md_lines) + "\n", encoding="utf-8"
    )

    # If response provided, it must be a validated JSON response (not arbitrary text).
    if args.response_file is not None:
        rp = args.response_file.resolve()
        if not rp.is_file():
            _write_json(output_dir / "advisor-decision.json", {
                "eligible": False, "reason": "response-file-not-found",
                "task_id": args.task_id,
            })
            return 2

        # Validate the structured response (import from hyphenated filename)
        import importlib.util
        _vr_path = Path(__file__).resolve().parent / "validate-advisor-response.py"
        _vr_spec = importlib.util.spec_from_file_location("validate_advisor_response", _vr_path)
        _vr_mod = importlib.util.module_from_spec(_vr_spec)
        _vr_spec.loader.exec_module(_vr_mod)
        validate_resp = _vr_mod.validate_response

        ok, normalized_response, diagnostic = validate_resp(
            str(rp),
            expected_request_id=request_id,
            expected_evidence_hash=evidence_hash,
            original_allowed_changes=args.allowed_changes if args.allowed_changes else None,
            original_forbidden_changes=args.forbidden,
        )

        if not ok:
            _write_json(output_dir / "advisor-decision.json", {
                "eligible": False,
                "reason": f"invalid-advisor-response: {diagnostic.get('reason', 'unknown')}",
                "task_id": args.task_id,
                "diagnostic": diagnostic,
            })
            return 2

        decision = normalized_response["decision"]
        resume_eligible = normalized_response.get("resume_eligible", False)

        _write_json(output_dir / "advisor-decision.json", {
            "eligible": True,
            "reason": "advisor-continuation-authorized",
            "task_id": args.task_id,
            "decision": decision,
            "resume_eligible": resume_eligible,
            "advisor": normalized_response["advisor"],
            "reservation_id": normalized_response["reservation_id"],
        })

        # Write the validated response
        _write_json(output_dir / "advisor-response-validated.json", normalized_response)

        if resume_eligible:
            # Build continuation card from validated response
            card_lines = [
                f"# Advisor Continuation Card: {args.task_id}",
                "",
                "## Instructions",
                "",
                "This is a **same-worktree retry**. Do not create a new worktree.",
                "Do not re-plan; continue from current progress.",
                "",
                "## Advisor Decision",
                "",
                f"- **Decision:** {decision}",
                f"- **Advisor:** {normalized_response['advisor']}",
                "",
                "## Blocker Answer",
                "",
                normalized_response["answer"],
                "",
                "## Retained Scope",
                "",
                args.completed_work,
                "",
            ]

            if normalized_response["allowed_changes"]:
                card_lines += ["## Allowed Changes", ""]
                for ac in normalized_response["allowed_changes"]:
                    card_lines.append(f"- `{ac}`")
                card_lines.append("")

            if normalized_response["forbidden_changes"]:
                card_lines += ["## Forbidden Paths", ""]
                for fp in normalized_response["forbidden_changes"]:
                    card_lines.append(f"- `{fp}`")
                card_lines.append("")

            if normalized_response["new_validation"]:
                card_lines += ["## New Validation Commands", ""]
                for nv in normalized_response["new_validation"]:
                    card_lines.append(f"- `{nv}`")
                card_lines.append("")

            card_lines += [
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
        else:
            _write_json(output_dir / "advisor-no-resume.json", {
                "task_id": args.task_id,
                "decision": decision,
                "reason": "response-not-resume-eligible",
                "details": {
                    "resume_allowed": normalized_response["resume_allowed"],
                    "risk_changed": normalized_response["risk_changed"],
                },
            })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
