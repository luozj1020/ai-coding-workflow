#!/usr/bin/env python3
"""Build a compact, non-authoritative acceptance evidence bundle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


def load(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"artifact": str(path), "parse_error": True}
    return value if isinstance(value, dict) else {"artifact": str(path), "parse_error": True}


def changed_paths(worktree: Path) -> list[str]:
    paths: set[str] = set()
    for argv in (
        ["git", "-C", str(worktree), "diff", "--name-only"],
        ["git", "-C", str(worktree), "diff", "--cached", "--name-only"],
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard"],
    ):
        result = subprocess.run(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            paths.update(line for line in result.stdout.splitlines() if line)
    return sorted(paths)


def status_of(value: dict[str, Any] | None, *keys: str) -> str:
    if value is None:
        return "missing"
    if value.get("parse_error"):
        return "invalid"
    for key in keys:
        if key in value:
            raw = value[key]
            return str(raw).lower() if not isinstance(raw, bool) else ("passed" if raw else "failed")
    return "unknown"


def recommend(
    outcome: dict[str, Any] | None,
    report: dict[str, Any] | None,
    scope: dict[str, Any] | None,
    checker: dict[str, Any] | None,
) -> str:
    if scope and scope.get("enforcement_passed") is False:
        return "revise-scope"
    if checker and checker.get("environment_failure_observed") and not checker.get("enforcement_passed"):
        return "inspect-validation-environment"
    if checker and checker.get("enforcement_passed") is False:
        return "revise-validation"
    report_status = status_of(report, "status")
    if report_status in {"conflict", "invalid", "failed"}:
        return "revise-report-or-code"
    if not outcome or not outcome.get("dispatch_success"):
        return "recover-or-revise"
    if outcome.get("completion_state") == "semantic-review-required":
        return "codex-semantic-review"
    return "inspect-evidence"


def build(args: argparse.Namespace) -> dict[str, Any]:
    outcome = load(args.outcome)
    report = load(args.report_consistency)
    scope = load(args.write_scope)
    checker = load(args.checker_contract)
    recovered = load(args.recovered_completion)
    value = {
        "schema_version": 1,
        "task_id": (outcome or {}).get("task_id"),
        "authority": "evidence-summary-only",
        "changed_paths": changed_paths(args.worktree),
        "gates": {
            "dispatch": status_of(outcome, "dispatch_success"),
            "artifact": status_of(outcome, "artifact_valid"),
            "report_consistency": status_of(report, "status"),
            "validation": status_of(outcome, "validation_success"),
            "write_scope": status_of(scope, "enforcement_passed"),
            "checker_contract": status_of(checker, "enforcement_passed"),
            "semantic_acceptance": status_of(outcome, "semantic_acceptance"),
        },
        "completion_state": (outcome or {}).get("completion_state", "unknown"),
        "environment_failure_observed": bool(
            checker and checker.get("environment_failure_observed")
        ),
        "recovered_completion_available": recovered is not None,
        "recommended_decision": recommend(outcome, report, scope, checker),
        "merge_authorized": False,
    }
    return value


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--outcome", type=Path, required=True)
    parser.add_argument("--report-consistency", type=Path)
    parser.add_argument("--write-scope", type=Path)
    parser.add_argument("--checker-contract", type=Path)
    parser.add_argument("--recovered-completion", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(args)
    atomic_write(args.output, value)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
