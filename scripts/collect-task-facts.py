#!/usr/bin/env python3
"""collect-task-facts.py — Collect routing facts from a Task Card + optional hints.

Usage:
    python scripts/collect-task-facts.py TASK.json [--hints HINTS.json] [--profiles-dir DIR] [--output FILE]

Input is a Task JSON plus an optional hints JSON. Validates and composes
the Task, then emits stable schema-v1 JSON with all routing facts.

Key invariants:
  - Missing risk categories become "unknown".
  - Hints may add/escalate risk but can never lower a declared or effective risk.
  - Hints cannot replace Task scope or validation.
  - routing_facts_hash is canonical and stable for equivalent JSON.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Allow running from repo root or scripts dir
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_hash import canonical_json as _canonical_json, evidence_hash as _evidence_hash
from task_schema import (
    ProfileConflictError,
    ProfileLoadError,
    ValidationError,
    compose_profiles,
    find_default_profiles_dir,
    load_task_json,
    validate_task,
    write_output,
)

# All risk categories from the schema
ALL_RISK_CATEGORIES = [
    "public_api", "data_model", "security", "migration",
    "permission", "concurrency", "cross_module", "production_impact",
]

# Risk escalation order: no < unknown < yes
_RISK_ORDER = {"no": 0, "unknown": 1, "yes": 2}


def _escalate_risk(current: str, hint_value: str) -> str:
    """Return the higher of two risk values. Never lowers."""
    c = _RISK_ORDER.get(current, 1)
    h = _RISK_ORDER.get(hint_value, 1)
    return "yes" if max(c, h) == 2 else ("unknown" if max(c, h) == 1 else "no")


# Canonical JSON and hashing delegated to shared evidence_hash module.
_facts_hash = _evidence_hash


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _repository_root(task_path: Path, repo: Optional[Union[str, Path]]) -> Path:
    candidates = [Path(repo).resolve()] if repo else [task_path.resolve().parent, Path.cwd()]
    for candidate in candidates:
        root = _git(candidate, "rev-parse", "--show-toplevel")
        if root:
            return Path(root).resolve()
    return candidates[0]


def collect_facts(
    task_path: Union[str, Path],
    hints_path: Optional[Union[str, Path]] = None,
    profiles_dir: Optional[Union[str, Path]] = None,
    repo: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Collect routing facts from a task and optional hints.

    Returns a dict conforming to the routing-facts-v1 schema.
    Raises ValidationError or ProfileLoadError on failure.
    """
    task_path = Path(task_path)
    repo_root = _repository_root(task_path, repo)
    profiles_dir_path = Path(profiles_dir) if profiles_dir else find_default_profiles_dir()

    # Step 1: Load and validate raw task
    task = load_task_json(task_path)
    errors = validate_task(task)
    if errors:
        raise ValidationError("; ".join(errors))

    # Step 2: Compose profiles
    try:
        composed = compose_profiles(task.get("profiles", []), profiles_dir_path, task)
    except (ProfileLoadError, ProfileConflictError) as exc:
        raise ValidationError(f"Profile composition failed: {exc}") from exc

    # Step 3: Validate composed task
    composed_errors = validate_task(composed)
    if composed_errors:
        raise ValidationError(f"Composed task invalid: {'; '.join(composed_errors)}")

    # Step 4: Load hints (optional)
    hints: Dict[str, Any] = {}
    if hints_path:
        hints = json.loads(Path(hints_path).read_text(encoding="utf-8"))
        if not isinstance(hints, dict):
            hints = {}

    # Step 5: Build declared risks from composed task
    declared_risks: Dict[str, str] = {}
    task_risk = composed.get("risk", {})
    for cat in ALL_RISK_CATEGORIES:
        declared_risks[cat] = task_risk.get(cat, "unknown")

    # Step 6: Build effective risks (hints can escalate, never lower)
    effective_risks: Dict[str, str] = dict(declared_risks)
    hint_risks = hints.get("risks", {})
    if isinstance(hint_risks, dict):
        for cat in ALL_RISK_CATEGORIES:
            if cat in hint_risks:
                effective_risks[cat] = _escalate_risk(declared_risks[cat], str(hint_risks[cat]))

    # Step 7: Extract target files
    target_files: List[str] = []
    scope = composed.get("scope", {})
    if isinstance(scope.get("write_paths"), list):
        tracked = _git(repo_root, "ls-files").splitlines()
        for pattern in scope["write_paths"]:
            matches = [path for path in tracked if fnmatch.fnmatch(path, pattern)]
            target_files.extend(matches or [pattern])

    # Step 8: Extract validation info
    validation_ids: List[str] = []
    exact_validation = True
    for v in composed.get("validation", []):
        if isinstance(v, dict) and v.get("id"):
            validation_ids.append(v["id"])
            if not v.get("command"):
                exact_validation = False

    # Step 9: Check for remote-validation requirement
    remote_required = False
    ext = composed.get("extensions", {})
    if isinstance(ext, dict):
        cpp_ext = ext.get("cpp_bazel", {})
        remote_ext = ext.get("remote_validation", {})
        if isinstance(cpp_ext, dict):
            remote_required = bool(cpp_ext.get("allow_remote_required", False))
        if isinstance(remote_ext, dict) and remote_ext.get("automation") == "preview":
            remote_required = True

    tracked_files = _git(repo_root, "ls-files").splitlines()
    marker_names = ["WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel", "BUILD", "BUILD.bazel"]
    markers = [name for name in marker_names if (repo_root / name).exists()]
    has_cpp = any(Path(path).suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"} for path in tracked_files)
    file_count = len(tracked_files)
    scale = "small" if file_count < 300 else "medium" if file_count < 3000 else "large" if file_count < 20000 else "monorepo"
    cache_dir = repo_root / ".ai-workflow" / "cache" / "context"
    cache_entries = len(list(cache_dir.glob("*.json"))) if cache_dir.is_dir() else 0

    # Step 10: Build the facts object (exclude hash before computing it)
    facts: Dict[str, Any] = {
        "schema_version": 1,
        "task_id": composed.get("id", ""),
        "goal": composed.get("goal", ""),
        "declared_risks": declared_risks,
        "effective_risks": effective_risks,
        "target_files": sorted(set(target_files)),
        "target_files_count": len(set(target_files)),
        "validation_ids": validation_ids,
        "exact_validation": exact_validation,
        "profiles": composed.get("profiles", []),
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "repository": {"root": str(repo_root), "tracked_files": file_count, "scale": scale, "bazel": bool(markers), "cpp": has_cpp},
        "repository_size": scale,
        "repository_markers": markers,
        "remote_validation_required": remote_required,
        "context_cache_state": {"available": cache_entries > 0, "entries": cache_entries},
        "predicted_diff_lines": hints.get("predicted_diff_lines", hints.get("diff_lines", 999)),
        "quota_mode": hints.get("quota_mode", "normal"),
        "latency_mode": hints.get("latency_mode", "interactive"),
    }

    # Step 11: Compute hash (over everything except the hash field itself)
    facts["routing_facts_hash"] = _facts_hash(facts)

    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect routing facts from a Task Card + optional hints.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Validates and composes the Task, then emits stable schema-v1 JSON.\n"
            "Exit codes: 0=success, 1=validation/composition error."
        ),
    )
    parser.add_argument(
        "task",
        help="Path to the task JSON file.",
    )
    parser.add_argument(
        "--hints",
        default=None,
        help="Path to optional hints JSON file.",
    )
    parser.add_argument(
        "--profiles-dir",
        default=None,
        help="Directory containing profile JSON files. Default: <repo>/profiles/",
    )
    parser.add_argument("--repo", default=None, help="Repository root. Defaults to Git discovery.")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path. Default: stdout.",
    )

    args = parser.parse_args(argv)

    try:
        facts = collect_facts(args.task, args.hints, args.profiles_dir, args.repo)
    except ValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (ProfileLoadError, ProfileConflictError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_str = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    write_output(output_str, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
