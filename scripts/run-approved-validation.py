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


def audit_task_card(path: Path) -> tuple[list[str], dict[str, object]]:
    return extract_commands(path.read_text(encoding="utf-8", errors="replace"))


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
        rejected = any(summary[key] for key in ("unsafe", "oversized", "overflow"))
        print(json.dumps({"status": "rejected" if rejected else "accepted", **summary}, sort_keys=True))
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
