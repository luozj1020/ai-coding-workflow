#!/usr/bin/env python3
"""Build a deterministic, state-bound Acceptance Graph."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_graph import AcceptanceGraphError, build_graph, load_bounded_json, validate_graph  # noqa: E402
from workflow_state import atomic_write_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--previous-graph", type=Path)
    parser.add_argument("--new-diff-ref", action="append", default=[])
    parser.add_argument("--changed-path", action="append", default=[])
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() and not args.force:
            raise AcceptanceGraphError("output exists; use --force to replace it")
        state = load_bounded_json(args.state, 16 * 1024 * 1024, "Workflow State")
        previous = load_bounded_json(args.previous_graph, 16 * 1024 * 1024, "previous Acceptance Graph") if args.previous_graph else None
        graph = build_graph(state, args.store, previous=previous, new_diff_refs=args.new_diff_ref, changed_paths=args.changed_path)
        errors = validate_graph(graph)
        if errors:
            raise AcceptanceGraphError("generated invalid graph: " + "; ".join(errors))
        atomic_write_json(args.output, graph)
        print(json.dumps({"output": str(args.output), "graph_id": graph["graph_id"], "state_id": graph["state_id"], "reopened_acceptance": graph["reopened_acceptance"]}, sort_keys=True))
        return 0
    except (OSError, AcceptanceGraphError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
