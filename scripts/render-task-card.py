#!/usr/bin/env python3
"""render-task-card.py — Render a task card JSON as Markdown.

Usage:
    python scripts/render-task-card.py TASK.json [--profiles-dir DIR] [--view audit|execution] [--output FILE]

Composes profiles, merges with task, and renders as Markdown.
Non-zero exit on invalid schema/profile/conflict.
Deterministic UTF-8 output.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_schema import (
    ProfileConflictError,
    ProfileLoadError,
    TaskSchemaError,
    ValidationError,
    compose_profiles,
    find_default_profiles_dir,
    load_task_json,
    render_task_card,
    validate_task,
    write_output,
)


def main(argv: list[str] | None = None) -> int:
    # Windows pipes otherwise inherit a legacy console code page while callers
    # consume this machine-generated Markdown as UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Render a task card JSON as Markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Loads a task JSON, composes its profiles, and renders the result\n"
            "as Markdown. View 'audit' includes all sections; 'execution'\n"
            "includes only sections relevant for Claude execution.\n"
            "Exit codes: 0=success, 1=validation/composition error."
        ),
    )
    parser.add_argument(
        "task",
        help="Path to the task JSON file.",
    )
    parser.add_argument(
        "--profiles-dir",
        default=None,
        help="Directory containing profile JSON files. Default: <repo>/profiles/",
    )
    parser.add_argument(
        "--view",
        choices=("audit", "execution"),
        default="audit",
        help="Rendering view. 'audit' includes all sections; 'execution' is shorter. Default: audit.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path. Default: stdout.",
    )

    args = parser.parse_args(argv)

    profiles_dir = Path(args.profiles_dir) if args.profiles_dir else find_default_profiles_dir()

    # Load task
    try:
        task = load_task_json(args.task)
    except ValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Validate
    errors = validate_task(task)
    if errors:
        for err in errors:
            print(f"Validation error: {err}", file=sys.stderr)
        return 1

    # Compose profiles
    try:
        composed = compose_profiles(task.get("profiles", []), profiles_dir, task)
    except ProfileLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ProfileConflictError as exc:
        print(f"Conflict: {exc}", file=sys.stderr)
        return 1

    # Render
    rendered = render_task_card(composed, view=args.view)

    # Write output
    write_output(rendered, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
