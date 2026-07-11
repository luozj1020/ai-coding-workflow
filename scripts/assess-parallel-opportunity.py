#!/usr/bin/env python3
"""assess-parallel-opportunity.py - Zero-token pre-task-card parallel opportunity classifier.

Classifies a task brief as `serial-obvious` or `parallel-candidate` from structured
hints, without reading the repository or invoking any model.  Only `parallel-candidate`
recommends a bounded Spark `parallel-planner` call; this helper never runs it.

Usage:
    python scripts/assess-parallel-opportunity.py --hints <json>
    python scripts/assess-parallel-opportunity.py --work-units 3 --write-scopes "src/a,src/b" --json

Exit codes:
    0  classification produced
    2  usage error (missing args, invalid JSON, etc.)
"""

import argparse
import json
import sys


def classify(hints: dict) -> dict:
    """Classify hints into serial-obvious or parallel-candidate.

    Accepts a dict with keys:
        work_units: int          - number of logical work units (default 1)
        write_scopes: list[str]  - declared write scope paths/identifiers
        estimated_minutes: int   - rough time estimate (default 0)
        validation_count: int    - number of independent validations (default 1)
        hard_risk_flags: list[str] - explicit shared-risk flags (e.g. "shared-api", "migration")

    Returns a dict with decision, reasons, and recommended_next_action.
    """
    work_units = hints.get("work_units", 1)
    write_scopes = hints.get("write_scopes", [])
    estimated_minutes = hints.get("estimated_minutes", 0)
    validation_count = hints.get("validation_count", 1)
    hard_risk_flags = hints.get("hard_risk_flags", [])

    # Normalize write scopes: strip whitespace, drop empties, deduplicate
    normalized_scopes = []
    seen = set()
    for scope in write_scopes:
        s = str(scope).strip()
        if s and s not in seen:
            seen.add(s)
            normalized_scopes.append(s)

    reasons = []

    # --- Serial-obvious conditions ---
    if work_units < 2:
        reasons.append(f"fewer than 2 work units ({work_units})")
    if len(normalized_scopes) < 2:
        reasons.append(f"fewer than 2 distinct write scopes ({len(normalized_scopes)})")
    if estimated_minutes > 0 and estimated_minutes < 10:
        reasons.append(f"very small estimated work ({estimated_minutes} minutes)")
    if hard_risk_flags:
        reasons.append(f"hard shared-risk flag(s): {', '.join(hard_risk_flags)}")

    # Any single serial-obvious condition is enough
    is_serial = (
        work_units < 2
        or len(normalized_scopes) < 2
        or (estimated_minutes > 0 and estimated_minutes < 10)
        or bool(hard_risk_flags)
    )

    if is_serial:
        return {
            "decision": "serial-obvious",
            "reasons": reasons,
            "recommended_next_action": "Proceed with a single serial task card. No Spark call needed.",
        }

    # --- Parallel-candidate conditions ---
    candidate_reasons = [
        f"{work_units} work units identified",
        f"{len(normalized_scopes)} distinct write scopes declared",
    ]
    if estimated_minutes >= 10:
        candidate_reasons.append(f"estimated {estimated_minutes} minutes of work")
    if validation_count >= 2:
        candidate_reasons.append(f"{validation_count} independent validations available")

    return {
        "decision": "parallel-candidate",
        "reasons": candidate_reasons,
        "recommended_next_action": (
            "Consider invoking Spark parallel-planner to produce a reviewed DAG plan. "
            "This helper does not run Spark or dispatch work."
        ),
        "write_scopes": normalized_scopes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zero-token parallel opportunity classifier."
    )

    # Two input modes: structured JSON via --hints, or individual flags
    parser.add_argument("--hints", help="Path to a JSON file with structured hints.")
    parser.add_argument("--work-units", type=int, default=1, help="Number of logical work units.")
    parser.add_argument(
        "--write-scopes",
        default="",
        help="Comma-separated write scope paths/identifiers.",
    )
    parser.add_argument("--estimated-minutes", type=int, default=0, help="Rough time estimate.")
    parser.add_argument("--validation-count", type=int, default=1, help="Independent validation count.")
    parser.add_argument(
        "--hard-risk-flags",
        default="",
        help="Comma-separated hard shared-risk flags (e.g. 'shared-api,migration').",
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON (default when not a TTY).")

    args = parser.parse_args()

    # Build hints dict
    if args.hints:
        try:
            with open(args.hints, "r", encoding="utf-8") as f:
                hints = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Error: cannot read hints file: {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        scopes = [s.strip() for s in args.write_scopes.split(",") if s.strip()]
        flags = [f.strip() for f in args.hard_risk_flags.split(",") if f.strip()] if args.hard_risk_flags else []
        hints = {
            "work_units": args.work_units,
            "write_scopes": scopes,
            "estimated_minutes": args.estimated_minutes,
            "validation_count": args.validation_count,
            "hard_risk_flags": flags,
        }

    result = classify(hints)

    # Output format: JSON by default (or when --json or non-TTY)
    use_json = args.json or not sys.stdout.isatty()
    if use_json:
        print(json.dumps(result, indent=2))
    else:
        # Human-friendly view
        print(f"Decision: {result['decision']}")
        print("Reasons:")
        for r in result["reasons"]:
            print(f"  - {r}")
        print(f"Next action: {result['recommended_next_action']}")
        if "write_scopes" in result:
            print(f"Write scopes: {', '.join(result['write_scopes'])}")


if __name__ == "__main__":
    main()
