#!/usr/bin/env python3
"""Compare real Markdown-baseline and stateful cross-model transfer runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
from typing import Any


ARMS = ("markdown-baseline", "stateful")
NUMERIC_FIELDS = (
    "codex_input_tokens",
    "active_elapsed_seconds",
    "seconds_to_first_meaningful_action",
    "receiver_reads_before_first_action",
    "handoff_revision_count",
    "payload_bytes",
    "final_diff_reuse_ratio",
)


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_records(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    records = value.get("runs") if isinstance(value, dict) else value
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("input must be a JSON array or an object with a runs array")
    return records


def compare(records: list[dict[str, Any]], minimum_pairs: int = 3) -> dict[str, Any]:
    errors: list[str] = []
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        prefix = f"run[{index}]"
        pair_id, arm = record.get("pair_id"), record.get("arm")
        if not isinstance(pair_id, str) or not pair_id:
            errors.append(f"{prefix}:missing-pair-id")
            continue
        if arm not in ARMS:
            errors.append(f"{prefix}:invalid-arm")
            continue
        if record.get("run_kind") != "real-model":
            errors.append(f"{prefix}:not-real-model")
        if record.get("usage_complete") is not True:
            errors.append(f"{prefix}:usage-incomplete")
        if not isinstance(record.get("accepted"), bool):
            errors.append(f"{prefix}:accepted-missing")
        for field in NUMERIC_FIELDS:
            if not number(record.get(field)):
                errors.append(f"{prefix}:missing-{field}")
        if arm in grouped.setdefault(pair_id, {}):
            errors.append(f"{prefix}:duplicate-arm")
        grouped[pair_id][arm] = record

    complete_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pair_id, arms in sorted(grouped.items()):
        if set(arms) != set(ARMS):
            errors.append(f"pair:{pair_id}:incomplete-arms")
            continue
        baseline, candidate = arms[ARMS[0]], arms[ARMS[1]]
        for binding in ("task_hash", "baseline_commit"):
            if not baseline.get(binding) or baseline.get(binding) != candidate.get(binding):
                errors.append(f"pair:{pair_id}:{binding}-mismatch")
        complete_pairs.append((baseline, candidate))

    if errors or len(complete_pairs) < minimum_pairs:
        if len(complete_pairs) < minimum_pairs:
            errors.append(f"minimum-pairs:{len(complete_pairs)}/{minimum_pairs}")
        return {
            "schema_version": 1,
            "verdict": "insufficient-evidence",
            "comparable": False,
            "complete_pair_count": len(complete_pairs),
            "errors": sorted(set(errors)),
        }

    def med(arm_index: int, field: str) -> float:
        return float(statistics.median(pair[arm_index][field] for pair in complete_pairs))

    baseline_accepts = sum(pair[0]["accepted"] for pair in complete_pairs)
    candidate_accepts = sum(pair[1]["accepted"] for pair in complete_pairs)
    base_tokens, candidate_tokens = med(0, "codex_input_tokens"), med(1, "codex_input_tokens")
    token_saving = (base_tokens - candidate_tokens) / base_tokens if base_tokens else None
    gates = {
        "acceptance_no_regression": candidate_accepts >= baseline_accepts,
        "codex_input_token_saving_at_least_10_percent": token_saving is not None and token_saving >= 0.10,
        "first_meaningful_action_no_slower": med(1, "seconds_to_first_meaningful_action") <= med(0, "seconds_to_first_meaningful_action"),
        "receiver_reads_no_increase": med(1, "receiver_reads_before_first_action") <= med(0, "receiver_reads_before_first_action"),
        "handoff_revisions_no_increase": med(1, "handoff_revision_count") <= med(0, "handoff_revision_count"),
        "diff_reuse_no_regression": med(1, "final_diff_reuse_ratio") >= med(0, "final_diff_reuse_ratio"),
        "active_time_within_2x": med(1, "active_elapsed_seconds") <= 2 * med(0, "active_elapsed_seconds"),
    }
    return {
        "schema_version": 1,
        "verdict": "effective-and-economic" if all(gates.values()) else "not-yet-proven",
        "comparable": True,
        "complete_pair_count": len(complete_pairs),
        "acceptance": {"markdown_baseline": baseline_accepts, "stateful": candidate_accepts},
        "median": {
            arm: {field: med(index, field) for field in NUMERIC_FIELDS}
            for index, arm in enumerate(ARMS)
        },
        "codex_input_token_saving_ratio": token_saving,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--minimum-pairs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.minimum_pairs <= 0:
        parser.error("--minimum-pairs must be positive")
    try:
        result = compare(load_records(args.input), args.minimum_pairs)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if result["comparable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
