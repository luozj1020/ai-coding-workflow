#!/usr/bin/env python3
"""Select a continuity-preserving owner and emit OWNER_LEASE.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from owner_lease import OwnerLeaseError, load_json, select_owner, transition_lease  # noqa: E402
from workflow_state import atomic_write_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--previous-lease", type=Path)
    parser.add_argument("--transition", choices=("expired", "revoked"))
    parser.add_argument("--transition-reason")
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() and not args.force:
            raise OwnerLeaseError("output exists; use --force to replace it")
        if args.transition:
            if args.request or not args.previous_lease:
                raise OwnerLeaseError("--transition requires --previous-lease and forbids --request")
            lease = transition_lease(load_json(args.previous_lease), args.transition, args.transition_reason)
        else:
            if not args.request:
                raise OwnerLeaseError("--request is required when no transition is selected")
            if args.transition_reason:
                raise OwnerLeaseError("--transition-reason requires --transition")
            lease = select_owner(load_json(args.request), load_json(args.previous_lease) if args.previous_lease else None)
        atomic_write_json(args.output, lease)
        print(json.dumps({"lease_id": lease["lease_id"], "selected_owner_id": lease["selected_owner_id"], "selected_model": lease["selected_model"], "status": lease["status"], "session_mode": lease["session"]["mode"], "advisor_action": lease["advisor"]["action"], "reviewer_action": lease["reviewer"]["action"]}, sort_keys=True))
        return 0
    except (OSError, OwnerLeaseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
