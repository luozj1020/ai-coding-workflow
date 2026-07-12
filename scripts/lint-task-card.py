#!/usr/bin/env python3
"""lint-task-card.py — Validate a task card JSON against schema and profiles.

Usage:
    python scripts/lint-task-card.py TASK.json [--profiles-dir DIR] [--json]

Validates schema, loads and composes profiles, reports conflicts.
Non-zero exit on any error. Actionable stderr on failure.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
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
    validate_task,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint a task card JSON against schema and profiles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Validates the task JSON against the v1 schema, then loads and\n"
            "composes the declared profiles. Reports all errors and conflicts.\n"
            "Exit codes: 0=valid, 1=errors found, 2=file error."
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
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON instead of human-readable text.",
    )

    args = parser.parse_args(argv)

    profiles_dir = Path(args.profiles_dir) if args.profiles_dir else find_default_profiles_dir()

    issues: list[dict[str, str]] = []

    # Load task
    try:
        task = load_task_json(args.task)
    except ValidationError as exc:
        issues.append({"level": "error", "category": "load", "message": str(exc)})
        if args.json_output:
            json.dump({"valid": False, "issues": issues}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            for issue in issues:
                print(f"ERROR [{issue['category']}]: {issue['message']}", file=sys.stderr)
        return 2

    # Validate schema
    schema_errors = validate_task(task)
    for err in schema_errors:
        issues.append({"level": "error", "category": "schema", "message": err})

    # Compose profiles (only if schema is valid so far)
    if not schema_errors:
        try:
            composed = compose_profiles(task.get("profiles", []), profiles_dir, task)
        except ProfileLoadError as exc:
            issues.append({"level": "error", "category": "profile", "message": str(exc)})
        except ProfileConflictError as exc:
            issues.append({"level": "error", "category": "conflict", "message": str(exc)})

    # Determine result
    valid = not any(i["level"] == "error" for i in issues)

    if args.json_output:
        json.dump({"valid": valid, "issues": issues}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        if valid:
            print("OK: Task card is valid.")
        else:
            for issue in issues:
                level = issue["level"].upper()
                print(f"{level} [{issue['category']}]: {issue['message']}", file=sys.stderr)

    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
