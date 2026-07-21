#!/usr/bin/env python3
"""Validate a short ACK against receiver base, Delta, and target State IR.

Exit codes: 0 accepted, 1 invalid/state mismatch, 2 bounded repair required,
3 blocked after the single repair attempt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_protocol import (  # noqa: E402
    HANDOFF_ACK_REPAIR_FILE, MAX_ACK_BYTES, HandoffProtocolError,
    build_repair_packet, evaluate_ack,
)
from workflow_state import atomic_write_json, load_events, load_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ack", type=Path, required=True)
    parser.add_argument("--base-state", type=Path, required=True, help="WORKFLOW_STATE.json held by the receiver")
    parser.add_argument("--state", type=Path, required=True, help="Target WORKFLOW_STATE.json")
    parser.add_argument("--delta", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True, help="WORKFLOW_EVENTS.jsonl proving state ancestry")
    parser.add_argument("--receiver-state-id", required=True, help="Last state ID held by the receiver")
    parser.add_argument("--result-output", type=Path)
    parser.add_argument("--repair-output", type=Path)
    parser.add_argument("--max-bytes", type=int, default=MAX_ACK_BYTES)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.max_bytes <= MAX_ACK_BYTES:
            raise HandoffProtocolError(f"--max-bytes must be between 1 and {MAX_ACK_BYTES}")
        raw_size = args.ack.stat().st_size
        if raw_size > args.max_bytes:
            result = {
                "status": "invalid", "execute_allowed": False,
                "errors": [f"ACK file exceeds {args.max_bytes} byte limit"],
                "ack_file_bytes": raw_size,
            }
            if args.result_output:
                atomic_write_json(args.result_output, result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 1
        ack = load_json(args.ack)
        base_state = load_json(args.base_state)
        state = load_json(args.state)
        delta = load_json(args.delta)
        events = load_events(args.events)
        result = evaluate_ack(
            ack, base_state, state, delta, events, args.receiver_state_id,
            max_bytes=args.max_bytes,
        )
        if result["status"] == "repair-required":
            repair_output = args.repair_output or args.ack.with_name(HANDOFF_ACK_REPAIR_FILE)
            repair = build_repair_packet(result, ack, state)
            atomic_write_json(repair_output, repair)
            result["repair_output"] = str(repair_output)
        if args.result_output:
            atomic_write_json(args.result_output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return {"accepted": 0, "repair-required": 2, "blocked": 3}.get(result["status"], 1)
    except (OSError, HandoffProtocolError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
