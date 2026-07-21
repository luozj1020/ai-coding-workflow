#!/usr/bin/env python3
"""Build a state-bound review packet containing only changed or failing subgraphs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_graph import AcceptanceGraphError, build_delta_packet, load_bounded_json  # noqa: E402
from workflow_state import atomic_write_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--previous-graph", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--mode", choices=("review", "revision"), default="review")
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() and not args.force:
            raise AcceptanceGraphError("output exists; use --force to replace it")
        graph = load_bounded_json(args.graph, 16 * 1024 * 1024, "Acceptance Graph")
        previous = load_bounded_json(args.previous_graph, 16 * 1024 * 1024, "previous Acceptance Graph") if args.previous_graph else None
        receipt = load_bounded_json(args.receipt, 2 * 1024 * 1024, "Review Receipt") if args.receipt else None
        packet = build_delta_packet(graph, previous=previous, receipt=receipt, mode=args.mode)
        atomic_write_json(args.output, packet)
        print(json.dumps({"output": str(args.output), "packet_id": packet["packet_id"], "acceptance_count": len(packet["acceptance_items"]), "omitted_count": len(packet["omitted_unchanged_accepted"])}, sort_keys=True))
        return 0
    except (OSError, AcceptanceGraphError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
