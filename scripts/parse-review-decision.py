#!/usr/bin/env python3
"""parse-review-decision.py — CLI to extract and validate a structured Review Decision from review text.

Usage:
    python scripts/parse-review-decision.py <review-text-file> [--output <decision.json>] [--next-task-draft <draft.json>]

Reads a review text file, extracts exactly one JSON decision object,
validates it against the v1 schema, writes canonical JSON atomically,
and optionally writes next-task-draft.json when next_task is present.

Exits 0 on success, 1 on extraction/validation error, 2 on usage error.
Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root or scripts/
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from review_decision import (
    ExtractionError,
    ValidationError,
    extract_next_task_draft,
    load_review_text,
    parse_and_validate,
    write_json_atomic,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract and validate a structured Review Decision from review text."
    )
    parser.add_argument(
        "review_file",
        help="Path to the review text file.",
    )
    parser.add_argument(
        "--output", "-o",
        help="Path to write the canonical decision JSON. Defaults to <review-file-stem>.review-decision.json",
    )
    parser.add_argument(
        "--next-task-draft",
        help="Path to write the next-task draft JSON when next_task is present.",
    )
    args = parser.parse_args()

    review_path = Path(args.review_file)

    # Load review text
    try:
        text = load_review_text(review_path)
    except ValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Parse and validate
    try:
        decision = parse_and_validate(text)
    except ExtractionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = review_path.with_suffix("").with_name(
            review_path.stem + ".review-decision.json"
        )

    # Write canonical JSON atomically
    try:
        write_json_atomic(decision, output_path)
    except OSError as exc:
        print(f"Error writing decision: {exc}", file=sys.stderr)
        return 1

    # Print machine-readable path
    print(f"Review Decision: {output_path}")

    # Write next-task draft if present
    next_task = extract_next_task_draft(decision)
    if next_task is not None:
        if args.next_task_draft:
            draft_path = Path(args.next_task_draft)
        else:
            draft_path = output_path.parent / "next-task-draft.json"

        try:
            write_json_atomic(next_task, draft_path)
        except OSError as exc:
            print(f"Error writing next-task draft: {exc}", file=sys.stderr)
            return 1

        print(f"Next Task Draft: {draft_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
