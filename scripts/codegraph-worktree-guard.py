#!/usr/bin/env python3
"""Validate that CodeGraph evidence belongs to the execution worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout,
    )


def git_value(worktree: Path, *args: str) -> str:
    result = run(["git", "-C", str(worktree), *args], timeout=15)
    return result.stdout.strip() if result.returncode == 0 else ""


def state_hash(worktree: Path) -> str:
    result = run(
        ["git", "-C", str(worktree), "status", "--porcelain=v1", "--untracked-files=all"],
        timeout=30,
    )
    payload = result.stdout if result.returncode == 0 else "unavailable"
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def codegraph_status(binary: str, worktree: Path, timeout: int) -> tuple[dict[str, Any], str]:
    try:
        result = run([binary, "status", str(worktree), "-j"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return {}, "status-timeout"
    if result.returncode != 0:
        return {}, "status-failed"
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}, "status-invalid-json"
    return (value, "ok") if isinstance(value, dict) else ({}, "status-invalid-json")


def pending_count(status: dict[str, Any]) -> int:
    pending = status.get("pendingChanges")
    if not isinstance(pending, dict):
        return 0
    return sum(int(pending.get(key, 0) or 0) for key in ("added", "modified", "removed"))


def identity_ready(status: dict[str, Any], worktree: Path) -> bool:
    project = status.get("projectPath")
    if not status.get("initialized") or not isinstance(project, str):
        return False
    try:
        same = Path(project).resolve() == worktree.resolve()
    except OSError:
        same = False
    return same and not status.get("worktreeMismatch")


def evaluate(source: Path, worktree: Path, policy: str, timeout: int) -> dict[str, Any]:
    source = source.resolve()
    worktree = worktree.resolve()
    binary = shutil.which("codegraph")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "policy": policy,
        "source_worktree": str(source),
        "execution_worktree": str(worktree),
        "source_head": git_value(source, "rev-parse", "HEAD"),
        "execution_head": git_value(worktree, "rev-parse", "HEAD"),
        "execution_state_hash": state_hash(worktree),
        "source_opted_in": (source / ".codegraph").is_dir(),
        "codegraph_available": bool(binary),
        "status": "unavailable",
        "action": "fallback-local",
        "safe_to_use": False,
        "stale_results_allowed": False,
        "reason": "codegraph-cli-unavailable",
    }
    if policy == "off":
        receipt.update(status="disabled", action="disabled", reason="policy-off")
        return receipt
    if not receipt["source_opted_in"] and policy != "repair":
        receipt.update(status="not-requested", action="fallback-local", reason="source-not-indexed")
        return receipt
    if not binary:
        return receipt

    status, error = codegraph_status(binary, worktree, timeout)
    receipt["status_probe"] = error
    receipt["observed_project_path"] = status.get("projectPath")
    receipt["worktree_mismatch"] = status.get("worktreeMismatch")
    receipt["pending_changes"] = status.get("pendingChanges", {})
    ready = identity_ready(status, worktree)
    pending = pending_count(status)
    if ready and pending == 0:
        receipt.update(status="ready", action="use-current-index", safe_to_use=True, reason="identity-current")
        return receipt

    if policy != "repair":
        reason = "different-worktree" if status.get("projectPath") else error
        if ready and pending:
            reason = "pending-changes"
        receipt.update(status="fallback-local", action="fallback-local", reason=reason)
        return receipt

    command = "sync" if ready else "index"
    receipt["repair_command"] = command
    try:
        repaired = run([binary, command, str(worktree)], timeout=timeout)
    except subprocess.TimeoutExpired:
        receipt.update(status="fallback-local", action="fallback-local", reason="repair-timeout")
        return receipt
    receipt["repair_exit_code"] = repaired.returncode
    if repaired.returncode != 0:
        receipt.update(status="fallback-local", action="fallback-local", reason="repair-failed")
        return receipt
    final, final_error = codegraph_status(binary, worktree, timeout)
    receipt["status_probe_after_repair"] = final_error
    receipt["observed_project_path"] = final.get("projectPath")
    receipt["worktree_mismatch"] = final.get("worktreeMismatch")
    receipt["pending_changes"] = final.get("pendingChanges", {})
    if identity_ready(final, worktree) and pending_count(final) == 0:
        receipt.update(status="ready", action=f"{command}-then-use", safe_to_use=True, reason="repaired-current")
    else:
        receipt.update(status="fallback-local", action="fallback-local", reason="repair-did-not-converge")
    return receipt


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("fallback", "repair", "off"), default="fallback")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.worktree.is_dir():
        parser.error("--worktree must be an existing directory")
    value = evaluate(args.source, args.worktree, args.policy, args.timeout)
    atomic_write(args.output, value)
    print(json.dumps(value, sort_keys=True))
    return 0 if value["status"] in {"ready", "fallback-local", "disabled", "unavailable", "not-requested"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
