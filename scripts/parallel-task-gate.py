#!/usr/bin/env python3
"""Run the reviewed narrow validation for one completed parallel dispatch."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def table_field(text: str, section: str, field: str) -> str:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    wanted_section = normalize(section)
    wanted_field = normalize(field)
    active = False
    for line in text.splitlines():
        if line.startswith("##"):
            active = normalize(line.lstrip("#").strip()) == wanted_section
            continue
        if active and line.startswith("|") and "---" not in line:
            parts = [part.strip() for part in line.split("|")]
            if len(parts) >= 3 and normalize(parts[1]) == wanted_field:
                return parts[2]
    return ""


def dispatch_path(text: str, label: str) -> str:
    match = re.findall(rf"(?m)^{re.escape(label)}:\s*(.+?)\s*$", text)
    return match[-1].strip() if match else ""


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch-out", type=Path, required=True)
    parser.add_argument("--task-card", type=Path, required=True)
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    args = parser.parse_args()

    result = {
        "schema_version": 1,
        "status": "incomplete",
        "acceptance_satisfied": False,
        "dispatch_out": str(args.dispatch_out),
        "task_card": str(args.task_card),
        "worktree": None,
        "validation_command": None,
        "validation_exit_code": None,
        "reason": None,
    }
    try:
        dispatch = args.dispatch_out.read_text(encoding="utf-8", errors="replace")
        card = args.task_card.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result["reason"] = f"artifact-read-failed:{exc}"
        write(args.output, result)
        return 2

    worktree_raw = dispatch_path(dispatch, "Worktree")
    command = table_field(card, "Parallel Execution Gate", "Validation command").strip()
    result["validation_command"] = command or None
    if not worktree_raw:
        result["reason"] = "missing-worktree-artifact"
        write(args.output, result)
        return 2
    worktree = Path(worktree_raw)
    result["worktree"] = str(worktree)
    if not worktree.is_dir():
        result["reason"] = "worktree-unavailable"
        write(args.output, result)
        return 2
    if not command:
        result["reason"] = "missing-validation-command"
        write(args.output, result)
        return 2
    if not args.checker.is_file():
        result["reason"] = "checker-helper-unavailable"
        write(args.output, result)
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.logs_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["bash", str(args.checker), "--no-discover", "--command",
         f"parallel-gate={command}", "--report", str(args.report),
         "--logs-dir", str(args.logs_dir)],
        cwd=str(worktree), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    result["validation_exit_code"] = completed.returncode
    result["status"] = "passed" if completed.returncode == 0 else "failed"
    result["reason"] = "exact-validation-passed" if completed.returncode == 0 else "exact-validation-failed"
    result["report"] = str(args.report)
    result["acceptance_satisfied"] = False
    write(args.output, result)
    return 0 if completed.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
