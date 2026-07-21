#!/usr/bin/env python3
"""Estimate the observable four-component Handoff Tax from run events."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_routing import HandoffRoutingError, estimate_paths  # noqa: E402
from workflow_state import atomic_write_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", nargs="+", type=Path)
    parser.add_argument("--sender")
    parser.add_argument("--receiver")
    parser.add_argument("--task-type")
    parser.add_argument("--min-samples", type=int, default=3)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)
    try:
        value = estimate_paths(args.events, sender=args.sender, receiver=args.receiver, task_type=args.task_type, min_samples=args.min_samples)
        if args.output:
            atomic_write_json(args.output, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, HandoffRoutingError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
