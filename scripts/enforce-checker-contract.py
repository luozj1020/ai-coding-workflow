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
PYTHON_CRASH = re.compile(
    r"segmentation fault|fatal python error|core dumped",
    re.IGNORECASE,
)


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


def command_text(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("`") and cleaned.endswith("`") and cleaned.count("`") == 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def safe_command(template: str, path: str) -> list[str]:
    template = command_text(template)
    if SHELL_META.search(template):
        raise ValueError("per-file validation command contains shell control syntax")
    if "{path}" not in template:
        raise ValueError("per-file validation command must contain {path}")
    argv = shlex.split(template.replace("{path}", path))
    if not argv:
        raise ValueError("per-file validation command is empty")
    return argv


def safe_exact_command(command: str) -> list[str]:
    command = command_text(command)
    if SHELL_META.search(command):
        raise ValueError("exact validation command contains shell control syntax")
    if "{" in command or "}" in command:
        raise ValueError("exact validation command contains unresolved placeholders")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("exact validation command is empty")
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


def is_environment_crash(result: dict[str, object]) -> bool:
    code = result.get("exit_code")
    output = str(result.get("output_tail") or "")
    return (
        isinstance(code, int)
        and (code < 0 or code in {134, 139})
    ) or bool(PYTHON_CRASH.search(output))


def pytest_prefix(argv: list[str]) -> tuple[list[str], int] | None:
    if argv and Path(argv[0]).name in {"pytest", "py.test"}:
        return [argv[0]], 1
    if len(argv) >= 3 and Path(argv[0]).name.startswith("python") and argv[1:3] == ["-m", "pytest"]:
        return argv[:3], 3
    return None


def pytest_group_commands(argv: list[str], changed: list[str]) -> list[list[str]]:
    """Split an explicit multi-file pytest command into equivalent file groups.

    A command without explicit file targets is not broadened or declared
    recovered: changed files are not evidence for an entire repository suite.
    """
    prefix_info = pytest_prefix(argv)
    if prefix_info is None:
        return []
    prefix, start = prefix_info
    tail = argv[start:]
    targets = [
        value for value in tail
        if not value.startswith("-") and (
            value.endswith(".py") or "::" in value
        )
    ]
    if not targets:
        return []
    changed_tests = {
        path for path in changed
        if path.endswith(".py") and (
            Path(path).name.startswith("test_") or "tests" in Path(path).parts
        )
    }
    target_files = {value.split("::", 1)[0] for value in targets}
    if not target_files or not target_files.issubset(changed_tests):
        return []
    options = [value for value in tail if value not in targets]
    return [prefix + options + [target] for target in targets]


def recover_pytest_crash(
    argv: list[str],
    changed: list[str],
    worktree: Path,
    timeout: int,
    env: dict[str, str],
) -> dict[str, object]:
    commands = pytest_group_commands(argv, changed)
    results = [execute(command, worktree, timeout, env) for command in commands]
    recovered = bool(results) and all(result["passed"] for result in results)
    return {
        "attempted": bool(commands),
        "strategy": "explicit-test-file-groups" if commands else "not-safe-to-split",
        "commands": commands,
        "results": results,
        "recovered": recovered,
        "coverage_equivalent": bool(commands),
    }


def enforce(worktree: Path, card_path: Path, output: Path, timeout: int) -> dict[str, object]:
    card = card_path.read_text(encoding="utf-8", errors="replace")
    allowed = parse_list(field(card, "Write paths"))
    command_template = field(card, "Per-file validation command")
    exact_command = field(card, "Exact narrow command")
    changed = changed_paths(worktree)
    violations: list[str] = []
    validations: list[dict[str, object]] = []
    exact_validation: dict[str, object] | None = None
    grouped_retry: dict[str, object] | None = None
    environment_failure_observed = False
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
            result["validation_kind"] = "per-file"
            validations.append(result)
            if not result["passed"]:
                violations.append(f"validation-failed:{path}")
                break
    if not changed:
        violations.append("no-test-file-output")
    if exact_command and not violations:
        try:
            exact_argv = safe_exact_command(exact_command)
        except ValueError as exc:
            violations.append(f"invalid-exact-validation-command:{exc}")
        else:
            exact_validation = execute(exact_argv, worktree, timeout, dict(os.environ))
            exact_validation["validation_kind"] = "frozen-exact-command"
            validations.append(exact_validation)
            if not exact_validation["passed"]:
                if is_environment_crash(exact_validation):
                    environment_failure_observed = True
                    exact_validation["failure_class"] = "environment-crash"
                    grouped_retry = recover_pytest_crash(
                        exact_argv, changed, worktree, timeout, dict(os.environ)
                    )
                    for result in grouped_retry["results"]:
                        result["validation_kind"] = "grouped-crash-retry"
                        validations.append(result)
                    if not grouped_retry["recovered"]:
                        violations.append("exact-validation-environment-crash")
                else:
                    exact_validation["failure_class"] = "assertion-or-command-failure"
                    violations.append("exact-validation-failed")
    receipt: dict[str, object] = {
        "schema_version": 2,
        "task_mode": "checker-test",
        "allowed_write_paths": allowed,
        "changed_paths": changed,
        "per_file_validation_command": command_template or None,
        "exact_validation_command": exact_command or None,
        "exact_validation": exact_validation,
        "grouped_retry": grouped_retry,
        "environment_failure_observed": environment_failure_observed,
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
