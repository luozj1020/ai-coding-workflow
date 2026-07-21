#!/usr/bin/env python3
"""Build a deterministic, state-bound HANDOFF_DELTA.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_protocol import HandoffProtocolError, build_delta, validate_delta  # noqa: E402
from workflow_state import atomic_write_json, load_events, load_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="State known by the receiver")
    parser.add_argument("--target", type=Path, required=True, help="New state to hand off")
    parser.add_argument("--events", type=Path, required=True, help="WORKFLOW_EVENTS.jsonl proving ancestry")
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() and not args.force:
            raise HandoffProtocolError("output exists; use --force to replace it")
        delta = build_delta(load_json(args.base), load_json(args.target), load_events(args.events))
        errors = validate_delta(delta)
        if errors:
            raise HandoffProtocolError("generated invalid delta: " + "; ".join(errors))
        atomic_write_json(args.output, delta)
        print(json.dumps({
            "output": str(args.output), "delta_id": delta["delta_id"],
            "base_state_id": delta["base_state_id"], "new_state_id": delta["new_state_id"],
        }, sort_keys=True))
        return 0
    except (OSError, HandoffProtocolError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
