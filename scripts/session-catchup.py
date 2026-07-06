#!/usr/bin/env python3
"""Generate a compact resume-context.md from planning files and workflow artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


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


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit is not None and len(text) > limit:
        return text[-limit:]
    return text


def active_plan_dir(root: Path, explicit: str | None = None) -> Path | None:
    plans_root = root / "ai" / "plans"
    if explicit:
        candidate = plans_root / explicit
        return candidate if candidate.is_dir() else Path(explicit)
    active = plans_root / ".active_plan"
    if active.is_file():
        plan_id = active.read_text(encoding="utf-8", errors="replace").strip()
        if plan_id:
            candidate = plans_root / plan_id
            if candidate.is_dir():
                return candidate
    candidates = [p for p in plans_root.iterdir()] if plans_root.is_dir() else []
    dirs = [p for p in candidates if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def latest_paths(root: Path, pattern: str, limit: int = 3) -> list[Path]:
    worktrees = root / ".worktrees"
    if not worktrees.is_dir():
        return []
    paths = sorted(worktrees.rglob(pattern), key=lambda p: p.stat().st_mtime)
    return paths[-limit:]


def first_heading_summary(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    useful = [line for line in lines if line and not line.startswith("<!--")]
    return "\n".join(useful[:30])


def checker_result(path: Path) -> str:
    text = read_text(path, 4000)
    if "ALL GREEN" in text:
        return "ALL GREEN"
    if "FAILED" in text:
        return "FAILED"
    return "UNKNOWN"


def decision_from_review(path: Path) -> str:
    text = read_text(path, 8000)
    match = re.search(r"\b(ACCEPT|REVISE|SPLIT|REJECT)\b", text, re.I)
    return match.group(1).upper() if match else "UNKNOWN"


def build_resume(root: Path, plan_dir: Path | None) -> str:
    lines = ["# Resume Context", ""]
    if plan_dir:
        lines.extend([f"Plan directory: `{plan_dir}`", ""])
        for filename, title in [
            ("task_plan.md", "Task Plan"),
            ("findings.md", "Findings"),
            ("progress.md", "Progress"),
        ]:
            path = plan_dir / filename
            lines.extend([f"## {title}", ""])
            if path.is_file():
                lines.append(first_heading_summary(read_text(path, 12000)))
            else:
                lines.append(f"Missing: `{path}`")
            lines.append("")
    else:
        lines.extend(["Plan directory: unavailable", ""])

    lines.extend(["## Recent Loop Events", ""])
    event_paths = latest_paths(root, "loop-events.jsonl", 2)
    if event_paths:
        for path in event_paths:
            lines.append(f"### `{path}`")
            lines.append("")
            lines.append("```json")
            lines.append(read_text(path, 6000).strip())
            lines.append("```")
            lines.append("")
    else:
        lines.append("No loop event artifacts found.")
        lines.append("")

    lines.extend(["## Recent Reviews", ""])
    review_paths = latest_paths(root, "review-*.txt", 3) + latest_paths(root, "*.review.txt", 3)
    if review_paths:
        for path in review_paths[-3:]:
            lines.append(f"- `{path}`: {decision_from_review(path)}")
    else:
        lines.append("- No review artifacts found.")
    lines.append("")

    lines.extend(["## Recent Checker Reports", ""])
    checker_paths = latest_paths(root, "*.checker-report.md", 3)
    if checker_paths:
        for path in checker_paths:
            lines.append(f"- `{path}`: {checker_result(path)}")
    else:
        lines.append("- No checker reports found.")
    lines.append("")

    lines.extend(["## Next Step Prompt", ""])
    lines.append("Use the plan files, recent loop events, review decisions, and checker reports above to continue from the latest safe point. Preserve failed evidence and update progress before major actions.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository root or child path.")
    parser.add_argument("--plan", help="Plan id or plan directory. Defaults to active or newest plan.")
    parser.add_argument("--output", help="Output path. Defaults to resume-context.md in the plan directory.")
    args = parser.parse_args(argv)

    root = repo_root(Path(args.repo))
    plan_dir = active_plan_dir(root, args.plan)
    if plan_dir is not None and not plan_dir.is_absolute():
        plan_dir = (root / plan_dir).resolve()
    text = build_resume(root, plan_dir)

    if args.output:
        output = Path(args.output)
    elif plan_dir:
        output = plan_dir / "resume-context.md"
    else:
        output = root / "ai" / "resume-context.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"Resume context: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

