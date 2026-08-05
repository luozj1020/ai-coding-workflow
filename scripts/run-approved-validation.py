#!/usr/bin/env python3
"""Audit or execute shell-free validation commands frozen in a task card."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional, Sequence

MAX_COMMANDS = 12
MAX_COMMAND_LENGTH = 500
UNSAFE_RE = re.compile(r"[;&|`><\x00-\x08\x0e-\x1f]")
FENCE_RE = re.compile(r"```[^\n]*(?:validation|check)[^\n]*\n(.*?)```", re.I | re.S)
EXACT_RE = re.compile(r"(?im)^\s*-\s*Exact narrow command:\s*`?([^`\n]+?)`?\s*$")
EMPTY_VALUES = {"none", "not-required", "not required", "tbd"}
RUNTIME_PROTOCOL = "aiwf-validation-runner-v1"
TABLE_MODE_RE = re.compile(r"(?im)^\|\s*Mode\s*\|\s*([^|]+?)\s*\|")
TABLE_BUILDER_MODE_RE = re.compile(r"(?im)^\|\s*Builder mode\s*\|\s*([^|]+?)\s*\|")
VALID_TASK_MODES = {"builder", "checker-test", "mixed-exception", "control-plane"}
VALID_BUILDER_MODES = {
    "auto", "standard", "execution-only", "solution-planning", "batch", "exploratory"
}
TASK_MODE_ALIASES = {
    "solution-planner": ("builder", "solution-planning"),
    "execution-builder": ("builder", "execution-only"),
    "batch-builder": ("builder", "batch"),
    "exploratory-builder": ("builder", "exploratory"),
    "checker": ("checker-test", None),
    "revision": ("builder", None),
}


def extract_commands(text: str) -> tuple[list[str], dict[str, object]]:
    commands: list[str] = []
    counts = {"unsafe": 0, "oversized": 0, "overflow": 0}

    def consider(raw: str) -> None:
        command = raw.strip()
        if not command or command.startswith("#") or command.lower() in EMPTY_VALUES:
            return
        if UNSAFE_RE.search(command):
            counts["unsafe"] += 1
        elif len(command) > MAX_COMMAND_LENGTH:
            counts["oversized"] += 1
        elif len(commands) >= MAX_COMMANDS:
            counts["overflow"] += 1
        elif command not in commands:
            commands.append(command)

    for block in FENCE_RE.finditer(text):
        for line in block.group(1).splitlines():
            consider(line)
    for match in EXACT_RE.finditer(text):
        consider(match.group(1))
    first_launcher = None
    if commands:
        try:
            first_argv = shlex.split(commands[0], posix=True)
            first_launcher = first_argv[0] if first_argv else None
        except ValueError:
            first_launcher = None
    return commands, {"accepted": len(commands), "first_launcher": first_launcher, **counts}


def audit_task_modes(text: str) -> dict[str, object]:
    mode_match = TABLE_MODE_RE.search(text)
    builder_match = TABLE_BUILDER_MODE_RE.search(text)
    declared = mode_match.group(1).strip().lower() if mode_match else None
    declared_builder = builder_match.group(1).strip().lower() if builder_match else None
    effective = declared
    builder_hint = None
    normalized = False
    reason = None
    error = None

    if declared in TASK_MODE_ALIASES:
        effective, builder_hint = TASK_MODE_ALIASES[declared]
        normalized = True
        reason = "role-alias-to-runtime-mode" if declared != "revision" else "revision-to-builder"
    elif declared and declared not in VALID_TASK_MODES:
        error = "unknown-task-mode"

    if declared_builder:
        if declared_builder not in VALID_BUILDER_MODES:
            error = error or "unknown-builder-mode"
        elif declared_builder != "auto":
            if builder_hint and declared_builder != builder_hint:
                error = error or "task-mode-builder-mode-conflict"
            else:
                builder_hint = declared_builder

    if effective and effective != "builder" and builder_hint not in (None, "standard"):
        error = error or "builder-mode-requires-builder-task-mode"

    return {
        "declared_task_mode": declared,
        "effective_task_mode": effective,
        "declared_builder_mode": declared_builder,
        "builder_mode_hint": builder_hint,
        "task_mode_normalized": normalized,
        "task_mode_normalization_reason": reason,
        "task_mode_error": error,
    }


def audit_task_card(path: Path) -> tuple[list[str], dict[str, object]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    commands, summary = extract_commands(text)
    return commands, {**summary, **audit_task_modes(text)}


def run_commands(commands: Sequence[str], *, cwd: Path, timeout: int) -> int:
    for index, command in enumerate(commands, 1):
        try:
            argv = shlex.split(command, posix=True)
        except ValueError:
            print(f"validation_index={index} status=invalid-argv")
            return 2
        if not argv:
            print(f"validation_index={index} status=invalid-argv")
            return 2
        print(f"validation_index={index} status=started")
        try:
            completed = subprocess.run(argv, cwd=cwd, timeout=timeout, check=False)
        except FileNotFoundError:
            print(f"validation_index={index} status=command-not-found")
            return 127
        except subprocess.TimeoutExpired:
            print(f"validation_index={index} status=timeout")
            return 124
        print(f"validation_index={index} status=finished exit_code={completed.returncode}")
        if completed.returncode != 0:
            return completed.returncode
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-protocol",
        action="version",
        version=RUNTIME_PROTOCOL,
        help="print the validation-runner runtime protocol and exit",
    )
    parser.add_argument("action", choices=("audit", "lint", "run"))
    parser.add_argument("--task-card", type=Path, default=Path("CLAUDE_TASK_CARD.md"))
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    commands, summary = audit_task_card(args.task_card)
    if args.action == "audit":
        print(json.dumps(summary, sort_keys=True))
        return 0
    if args.action == "lint":
        rejected = any(summary[key] for key in ("unsafe", "oversized", "overflow")) or bool(
            summary["task_mode_error"]
        )
        status = "rejected" if rejected else (
            "normalized" if summary["task_mode_normalized"] else "accepted"
        )
        print(json.dumps({"status": status, **summary}, sort_keys=True))
        return 2 if rejected else 0
    if any(summary[key] for key in ("unsafe", "oversized", "overflow")):
        print(json.dumps({"status": "rejected", **summary}, sort_keys=True))
        return 2
    if not commands:
        print("validation_status=no-commands")
        return 2
    return run_commands(commands, cwd=args.task_card.resolve().parent, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
