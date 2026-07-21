#!/usr/bin/env python3
"""Validate a Review Receipt against its exact Acceptance Graph and optional packet."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_graph import AcceptanceGraphError, load_bounded_json, validate_receipt  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--packet", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = load_bounded_json(args.receipt, 2 * 1024 * 1024, "Review Receipt")
        graph = load_bounded_json(args.graph, 16 * 1024 * 1024, "Acceptance Graph")
        packet = load_bounded_json(args.packet, 16 * 1024 * 1024, "review packet") if args.packet else None
        errors = validate_receipt(receipt, graph, packet)
        result = {"valid": not errors, "review_id": receipt.get("review_id"), "state_id": graph.get("state_id"), "errors": errors}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if not errors else 1
    except (OSError, AcceptanceGraphError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
