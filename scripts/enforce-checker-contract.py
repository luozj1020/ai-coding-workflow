#!/usr/bin/env python3
"""Enforce Checker write scope and immediate per-file validation after dispatch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


CONTROL_FILES = {
    "CLAUDE_PROGRESS.md",
    "CLAUDE_REPORT.md",
    "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md",
    "TASK_CARD.md",
    "TASK_CARD_FULL.md",
}
SHELL_META = re.compile(r"[|&;<>()`\n\r]")


def git(worktree: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(worktree), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return [line for line in proc.stdout.splitlines() if line]


def parse_list(value: str) -> list[str]:
    cleaned = value.replace("`", "").strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "not-required"}:
        return []
    return [item.strip().rstrip("/") for item in re.split(r"[,;]", cleaned) if item.strip()]


def field(card: str, name: str) -> str:
    table = re.search(
        rf"^\|\s*{re.escape(name)}\s*\|\s*(.*?)\s*\|\s*$",
        card,
        re.IGNORECASE | re.MULTILINE,
    )
    if table:
        return table.group(1).strip()
    bullet = re.search(
        rf"^-\s*{re.escape(name)}\s*:\s*(.*?)\s*$",
        card,
        re.IGNORECASE | re.MULTILINE,
    )
    return bullet.group(1).strip() if bullet else ""


def changed_paths(worktree: Path) -> list[str]:
    paths = set(git(worktree, "diff", "--name-only"))
    paths.update(git(worktree, "diff", "--cached", "--name-only"))
    paths.update(git(worktree, "ls-files", "--others", "--exclude-standard"))
    return sorted(path for path in paths if Path(path).name not in CONTROL_FILES)


def in_scope(path: str, allowed: list[str]) -> bool:
    return any(path == root or path.startswith(f"{root}/") for root in allowed)


def safe_command(template: str, path: str) -> list[str]:
    if SHELL_META.search(template):
        raise ValueError("per-file validation command contains shell control syntax")
    if "{path}" not in template:
        raise ValueError("per-file validation command must contain {path}")
    argv = shlex.split(template.replace("{path}", path))
    if not argv:
        raise ValueError("per-file validation command is empty")
    return argv


def execute(argv: list[str], worktree: Path, timeout: int, env: dict[str, str]) -> dict[str, object]:
    try:
        proc = subprocess.run(
            argv,
            cwd=worktree,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        output = (proc.stdout + proc.stderr)[-4000:]
        return {"argv": argv, "exit_code": proc.returncode, "output_tail": output, "passed": proc.returncode == 0}
    except subprocess.TimeoutExpired as exc:
        return {"argv": argv, "exit_code": None, "output_tail": str(exc), "passed": False, "timed_out": True}


def enforce(worktree: Path, card_path: Path, output: Path, timeout: int) -> dict[str, object]:
    card = card_path.read_text(encoding="utf-8", errors="replace")
    allowed = parse_list(field(card, "Write paths"))
    command_template = field(card, "Per-file validation command")
    changed = changed_paths(worktree)
    violations: list[str] = []
    validations: list[dict[str, object]] = []
    if not allowed:
        violations.append("missing-write-paths")
    for path in changed:
        candidate = worktree / path
        if not in_scope(path, allowed):
            violations.append(f"out-of-scope:{path}")
            continue
        if not candidate.is_file() or candidate.stat().st_size == 0:
            violations.append(f"missing-or-empty:{path}")
            continue
        commands: list[list[str]] = []
        if candidate.suffix == ".py":
            commands.append([sys.executable, "-m", "py_compile", path])
        if command_template:
            try:
                commands.append(safe_command(command_template, path))
            except ValueError as exc:
                violations.append(f"invalid-validation-command:{exc}")
        elif candidate.suffix == ".py" and (
            candidate.name.startswith("test_") or "tests" in candidate.parts
        ):
            commands.append([sys.executable, "-m", "pytest", path, "-q"])
        else:
            violations.append(f"missing-per-file-validation:{path}")
        env = dict(os.environ)
        for argv in commands:
            result = execute(argv, worktree, timeout, env)
            result["path"] = path
            validations.append(result)
            if not result["passed"]:
                violations.append(f"validation-failed:{path}")
                break
    if not changed:
        violations.append("no-test-file-output")
    receipt: dict[str, object] = {
        "schema_version": 1,
        "task_mode": "checker-test",
        "allowed_write_paths": allowed,
        "changed_paths": changed,
        "per_file_validation_command": command_template or None,
        "validations": validations,
        "violations": sorted(set(violations)),
        "enforcement_passed": not violations,
        "merge_authorized": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f"{output.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--task-card", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        receipt = enforce(args.worktree.resolve(), args.task_card.resolve(), args.output, args.timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["enforcement_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
