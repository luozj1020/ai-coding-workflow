#!/usr/bin/env python3
"""Convert observed Handoff Tax estimates into an auditable routing calibration."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_routing import HandoffRoutingError, calibrate_estimates, load_json  # noqa: E402
from workflow_state import atomic_write_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimate", action="append", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--min-samples", type=int, default=3)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)
    try:
        value = calibrate_estimates(
            [load_json(path, "Handoff Tax estimate") for path in args.estimate],
            load_json(args.policy, "calibration policy"), args.min_samples,
        )
        if args.output:
            atomic_write_json(args.output, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, HandoffRoutingError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
