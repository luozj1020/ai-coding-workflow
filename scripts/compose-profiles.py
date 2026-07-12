#!/usr/bin/env python3
"""compose-profiles.py — Compose profiles with a task instance.

Usage:
    python scripts/compose-profiles.py TASK.json [--profiles-dir DIR] [--output FILE]

Reads a task JSON, composes its declared profiles, merges with the task
instance, and writes the composed result. Non-zero exit on invalid
schema, missing profiles, or composition conflict.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root or scripts dir
sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_schema import (
    ProfileConflictError,
    ProfileLoadError,
    TaskSchemaError,
    ValidationError,
    compose_profiles,
    find_default_profiles_dir,
    load_task_json,
    validate_task,
    write_output,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compose profiles with a task instance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Reads a task JSON file, loads and composes the declared profiles,\n"
            "merges the result with the task instance, and writes the composed\n"
            "output. Exit codes: 0=success, 1=validation/composition error."
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

    # Validate task
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

    # Write output
    output_str = json.dumps(composed, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    write_output(output_str, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
