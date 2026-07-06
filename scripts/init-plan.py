#!/usr/bin/env python3
"""Create a persistent planning directory under ai/plans/<task-id>."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def repo_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return start.resolve()


def slugify(value: str) -> str:
    value = SLUG_RE.sub("-", value.strip())
    value = value.strip("-._")
    return value or "task"


def script_root() -> Path:
    return Path(__file__).resolve().parent


def template_candidates(script_dir: Path, name: str) -> list[Path]:
    return [
        script_dir.parent / "assets" / name,
        script_dir / name,
        script_dir.parent / name,
    ]


def find_template(script_dir: Path, name: str) -> Path:
    for candidate in template_candidates(script_dir, name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Template not found: {name}")


def create_plan(task_id: str, repo: Path, overwrite: bool = False) -> Path:
    root = repo_root(repo)
    plan_id = slugify(task_id)
    plan_dir = root / "ai" / "plans" / plan_id
    plan_dir.mkdir(parents=True, exist_ok=True)

    templates = {
        "plan-task-template.md": "task_plan.md",
        "plan-findings-template.md": "findings.md",
        "plan-progress-template.md": "progress.md",
    }
    script_dir = script_root()
    for template_name, output_name in templates.items():
        src = find_template(script_dir, template_name)
        dst = plan_dir / output_name
        if dst.exists() and not overwrite:
            continue
        text = src.read_text(encoding="utf-8")
        text = text.replace("<!-- e.g., ACW-123 -->", plan_id)
        text = text.replace("Last updated | |", f"Last updated | {datetime.now(timezone.utc).isoformat()} |")
        dst.write_text(text, encoding="utf-8")

    active = root / "ai" / "plans" / ".active_plan"
    active.write_text(plan_id + "\n", encoding="utf-8")
    return plan_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="Task or plan identifier.")
    parser.add_argument("--repo", default=".", help="Repository root or child path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing planning files.")
    args = parser.parse_args(argv)

    try:
        plan_dir = create_plan(args.task_id, Path(args.repo), overwrite=args.overwrite)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Plan directory: {plan_dir}")
    print(f"Task plan:      {plan_dir / 'task_plan.md'}")
    print(f"Findings:       {plan_dir / 'findings.md'}")
    print(f"Progress:       {plan_dir / 'progress.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
