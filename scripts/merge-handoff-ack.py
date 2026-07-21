#!/usr/bin/env python3
"""Merge an initial ACK with one receiver-authored bounded repair ACK."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_protocol import MAX_ACK_BYTES, HandoffProtocolError, merge_acks  # noqa: E402
from workflow_state import atomic_write_json, load_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ack", type=Path, required=True)
    parser.add_argument("--repair-ack", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=MAX_ACK_BYTES)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.max_bytes <= MAX_ACK_BYTES:
            raise HandoffProtocolError(f"--max-bytes must be between 1 and {MAX_ACK_BYTES}")
        for path in (args.base_ack, args.repair_ack):
            if path.stat().st_size > args.max_bytes:
                raise HandoffProtocolError(f"ACK file exceeds {args.max_bytes} byte limit: {path}")
        if args.output.exists() and not args.force:
            raise HandoffProtocolError("output exists; use --force to replace it")
        merged = merge_acks(
            load_json(args.base_ack), load_json(args.repair_ack), max_bytes=args.max_bytes,
        )
        atomic_write_json(args.output, merged)
        print(json.dumps({
            "output": str(args.output), "state_id": merged["state_id"],
            "receiver": merged["receiver"], "repair_attempt": merged["repair_attempt"],
        }, sort_keys=True))
        return 0
    except (OSError, HandoffProtocolError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
