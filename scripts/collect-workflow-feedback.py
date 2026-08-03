#!/usr/bin/env python3
"""Collect privacy-bounded local workflow feedback without invoking a model."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


ISSUE_CATEGORIES = (
    "repeated-host-confirmation",
    "spark-terminal-missing",
    "false-progress",
    "completion-tail-delay",
    "session-resume-failed",
    "write-scope-blocked",
    "tool-capability-mismatch",
    "monitor-noise",
    "process-lifecycle",
    "report-inconsistent",
    "validation-environment-failure",
    "other",
)
RATING_CATEGORIES = ("safety", "recoverability", "correctness", "efficiency", "explainability")
MAX_JSON_BYTES = 1024 * 1024
MAX_COMMENT_CHARS = 1000


class FeedbackError(ValueError):
    pass


def _safe_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        return {}
    if path.stat().st_size > MAX_JSON_BYTES:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return "external-artifact-redacted"


def _git_head(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _runtime_candidates(repo: Path, task_id: str | None) -> list[Path]:
    root = repo / ".worktrees"
    if not root.is_dir():
        return []
    values = [path for path in root.glob("*.runtime.json") if path.is_file() and not path.is_symlink()]
    if task_id:
        matched = []
        for path in values:
            value = _safe_json(path)
            if value.get("task_id") == task_id or path.name == f"{task_id}.runtime.json":
                matched.append(path)
        values = matched
    return sorted(values, key=lambda path: path.stat().st_mtime, reverse=True)


def _artifact(repo: Path, task_id: str, suffix: str) -> Path:
    return repo / ".worktrees" / f"{task_id}.{suffix}"


def _parse_ratings(values: list[str]) -> dict[str, int]:
    ratings: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise FeedbackError("ratings must use CATEGORY=1..10")
        category, raw = value.split("=", 1)
        if category not in RATING_CATEGORIES:
            raise FeedbackError(f"unsupported rating category: {category}")
        try:
            score = int(raw)
        except ValueError as exc:
            raise FeedbackError("rating values must be integers") from exc
        if not 1 <= score <= 10:
            raise FeedbackError("rating values must be between 1 and 10")
        ratings[category] = score
    return ratings


def _clean_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    comment = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", comment).strip()
    if len(comment) > MAX_COMMENT_CHARS:
        raise FeedbackError(f"comment exceeds {MAX_COMMENT_CHARS} characters")
    return comment or None


def collect(
    repo: Path, task_id: str | None, issues: list[str], ratings: dict[str, int],
    comment: str | None,
) -> dict[str, Any]:
    repo = repo.resolve()
    candidates = _runtime_candidates(repo, task_id)
    runtime_path = candidates[0] if candidates else None
    runtime = _safe_json(runtime_path) if runtime_path else {}
    resolved_task_id = str(runtime.get("task_id") or task_id or "repository-feedback")
    outcome_path = _artifact(repo, resolved_task_id, "outcome.json")
    result_path = _artifact(repo, resolved_task_id, "result.json")
    phase_path = _artifact(repo, resolved_task_id, "phase-metrics.json")
    outcome = _safe_json(outcome_path)
    result = _safe_json(result_path)
    phase = _safe_json(phase_path)
    resume_failure = _artifact(repo, resolved_task_id, "session-resume-failure.json")
    write_scope = _artifact(repo, resolved_task_id, "write-scope-enforcement.json")
    dirty_snapshot = _artifact(repo, resolved_task_id, "dirty-snapshot.json")
    sources = [path for path in (runtime_path, outcome_path, result_path, phase_path) if path and path.is_file()]

    runtime_script = repo / "ai" / "dispatch-to-claude.sh"
    runtime_sha = None
    if runtime_script.is_file() and not runtime_script.is_symlink():
        runtime_sha = "sha256:" + hashlib.sha256(runtime_script.read_bytes()).hexdigest()

    dispatch_outcome = outcome.get("dispatch_outcome") or result.get("dispatch_outcome")
    model_started = outcome.get("builder_started")
    if not isinstance(model_started, bool):
        model_started = bool(runtime.get("claude_launched", False))
    duration = phase.get("total_seconds")
    if not isinstance(duration, (int, float)) or duration < 0:
        duration = None

    return {
        "schema_version": 1,
        "kind": "task-feedback",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": {
            "name": repo.name,
            "head": _git_head(repo),
            "runtime_sha256": runtime_sha,
        },
        "task": {
            "task_id": resolved_task_id,
            "task_mode": runtime.get("task_mode") if isinstance(runtime.get("task_mode"), str) else None,
            "execution_env": runtime.get("execution_env") if isinstance(runtime.get("execution_env"), str) else None,
            "dispatch_outcome": dispatch_outcome if isinstance(dispatch_outcome, str) else None,
            "model_interaction_started": model_started,
            "duration_seconds": duration,
        },
        "signals": {
            "session_resume_failure": resume_failure.is_file(),
            "write_scope_receipt": write_scope.is_file(),
            "dirty_snapshot_receipt": dirty_snapshot.is_file(),
            "needs_host_execution": bool(
                outcome.get("needs_host_execution") or result.get("needs_host_execution")
            ),
            "runtime_tool_inventory_verified": runtime.get("runtime_tool_inventory_verified")
            if isinstance(runtime.get("runtime_tool_inventory_verified"), bool) else None,
        },
        "user_feedback": {
            "issues": sorted(set(issues)),
            "ratings": ratings,
            "comment": comment,
        },
        "provenance": {
            "sources": [_relative(repo, path) for path in sources],
            "source_count": len(sources),
        },
        "privacy": {
            "local_only": True,
            "raw_logs_included": False,
            "prompts_included": False,
            "source_or_diff_included": False,
            "api_configuration_read": False,
            "absolute_paths_included": False,
        },
    }


def bundle(repo: Path) -> dict[str, Any]:
    feedback_root = repo.resolve() / ".ai-workflow" / "feedback"
    records = []
    if feedback_root.is_dir():
        for path in sorted(feedback_root.glob("*.json")):
            value = _safe_json(path)
            if value.get("kind") == "task-feedback" and value.get("schema_version") == 1:
                records.append(value)
    issue_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    rating_values: dict[str, list[int]] = defaultdict(list)
    for record in records:
        feedback = record.get("user_feedback", {})
        for issue in feedback.get("issues", []):
            if isinstance(issue, str):
                issue_counts[issue] += 1
        for category, score in feedback.get("ratings", {}).items():
            if category in RATING_CATEGORIES and isinstance(score, int):
                rating_values[category].append(score)
        outcome = record.get("task", {}).get("dispatch_outcome")
        if isinstance(outcome, str):
            outcome_counts[outcome] += 1
    return {
        "schema_version": 1,
        "kind": "feedback-bundle",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "issue_counts": dict(sorted(issue_counts.items())),
        "rating_averages": {
            category: round(sum(values) / len(values), 2)
            for category, values in sorted(rating_values.items()) if values
        },
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "privacy": {
            "comments_included": False,
            "raw_records_included": False,
            "local_only": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--task-id")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--record", action="store_true")
    modes.add_argument("--bundle", action="store_true")
    parser.add_argument("--issue", action="append", choices=ISSUE_CATEGORIES, default=[])
    parser.add_argument("--rating", action="append", default=[], metavar="CATEGORY=1..10")
    parser.add_argument("--comment")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.bundle:
            if args.task_id or args.issue or args.rating or args.comment:
                raise FeedbackError("--bundle cannot include task feedback fields")
            value = bundle(args.repo)
        else:
            value = collect(
                args.repo, args.task_id, args.issue,
                _parse_ratings(args.rating), _clean_comment(args.comment),
            )
        if args.record or (args.bundle and args.output):
            if args.output:
                output = args.output.resolve()
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                task = re.sub(r"[^A-Za-z0-9_.-]", "_", value["task"]["task_id"])
                output = args.repo.resolve() / ".ai-workflow" / "feedback" / f"{task}-{stamp}.json"
            _atomic_json(output, value)
            print(output)
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
    except (FeedbackError, OSError) as exc:
        print(f"workflow feedback: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
