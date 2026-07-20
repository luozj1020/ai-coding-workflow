#!/usr/bin/env python3
"""Create task cards from a markdown plan containing '### Task N: title' sections."""

import argparse
import os
import re
import sys

from compose_task_card import component_root, compose, load_catalog


TASK_RE = re.compile(r"^###\s+Task\s+(\d+)\s*:\s*(.+?)\s*$")


def slugify(value):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "task"


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def load_template(repo_root, preset="builder", gates=None):
    """Load a concise component-composed card, with legacy fallback."""
    try:
        root = component_root()
        catalog = load_catalog(root)
        content, _ = compose(root, catalog, preset, gates or [])
        return content
    except (OSError, ValueError):
        pass
    candidates = [
        os.path.join(repo_root, "ai", "task-card-template.md"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "task-card-template.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return read_text(path)
    return "# Task Card\n\n## Goal\n\n"


def extract_tasks(plan_text):
    lines = plan_text.splitlines()
    starts = []
    for idx, line in enumerate(lines):
        match = TASK_RE.match(line)
        if match:
            starts.append((idx, match.group(1), match.group(2)))
    tasks = []
    for pos, (start, number, title) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        tasks.append((number, title, body))
    return tasks


def write_task_cards(repo_root, plan_path, out_dir=None, overwrite=False, preset="builder", gates=None):
    plan_abs = os.path.abspath(plan_path)
    plan_text = read_text(plan_abs)
    tasks = extract_tasks(plan_text)
    if not tasks:
        raise ValueError("no task sections found; expected headings like '### Task 1: title'")

    destination = out_dir or os.path.join(repo_root, "ai", "task-cards")
    os.makedirs(destination, exist_ok=True)
    template = load_template(repo_root, preset, gates).rstrip()
    plan_label = os.path.relpath(plan_abs, repo_root)
    plan_stem = slugify(os.path.splitext(os.path.basename(plan_abs))[0])
    written = []

    for number, title, body in tasks:
        filename = "{}-{:02d}-{}.md".format(plan_stem, int(number), slugify(title))
        path = os.path.join(destination, filename)
        if os.path.exists(path) and not overwrite:
            raise FileExistsError(path)
        content = "\n\n".join(
            [
                template,
                "---",
                "## Plan Task Extract",
                "Source plan: {}".format(plan_label),
                "Task heading: Task {}: {}".format(number, title),
                "",
                body,
            ]
        ).rstrip() + "\n"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        written.append(path)

    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="Markdown plan path")
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--out-dir", default=None, help="Task-card output directory")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing task cards")
    parser.add_argument("--preset", choices=("builder", "batch-builder", "solution-planner", "exploratory-builder", "checker", "revision", "control-plane"), default="builder")
    parser.add_argument("--gate", action="append", default=[], help="Optional task-card gate component")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo)
    try:
        written = write_task_cards(repo_root, args.plan, args.out_dir, args.overwrite, args.preset, args.gate)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
