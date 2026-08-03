#!/usr/bin/env python3
"""Select deterministic L0, Spark L1, or compact Codex L2 review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _bundle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidate = data.get("acceptance_bundle")
    if isinstance(candidate, dict):
        return candidate
    if isinstance(data.get("acceptance_index"), list):
        return data
    return None


def _bundle_review(bundle: Dict[str, Any]) -> tuple[str, List[str]]:
    selection = bundle.get("review_selection")
    selection = selection if isinstance(selection, dict) else {}
    items = [item for item in bundle.get("acceptance_index", []) if isinstance(item, dict)]
    reasons: List[str] = []
    statuses = {str(item.get("status", "unknown")) for item in items}
    expanded = [str(value) for value in selection.get("expanded_acceptance_ids", []) if value]

    semantic_risk = bool(selection.get("deep_codex_review_required"))
    semantic_risk = semantic_risk or bool(bundle.get("semantic_risk"))
    semantic_risk = semantic_risk or bool(statuses & {"contradictory", "reopened"})
    if semantic_risk:
        reasons.append("semantic-risk-acceptance-delta")
        return "L2-codex", reasons
    if expanded or statuses - {"supported"}:
        reasons.append("mechanical-or-coverage-acceptance-delta")
        return "L1-spark", reasons
    if items and statuses <= {"supported"}:
        reasons.append("deterministic-acceptance-evidence-sufficient")
        return "L0-local", reasons
    reasons.append("acceptance-index-unavailable")
    return "legacy", reasons


def select_tier(data: Dict[str, Any]) -> Dict[str, Any]:
    lane = data.get("lane", "standard")
    bundle = _bundle(data)
    reasons: List[str] = []
    bundle_tier = "legacy"
    if bundle is not None:
        bundle_tier, reasons = _bundle_review(bundle)

    if lane == "assured" or data.get("codex_required") or data.get("review_triggers"):
        tier = "L2-codex"
        reasons.append("assured-or-explicit-codex-review")
    elif bundle_tier != "legacy":
        tier = bundle_tier
    elif data.get("status") != "passed" or data.get("semantic_uncertainty"):
        tier = "L1-spark"
        reasons.append("legacy-non-passing-or-uncertain-evidence")
    else:
        tier = "L0-local"
        reasons.append("legacy-deterministic-evidence-sufficient")

    if data.get("codex_available") is False and tier == "L2-codex":
        action = "stop" if lane == "assured" else "human-review"
    else:
        action = {
            "L0-local": "human-review",
            "L1-spark": "spark-review",
            "L2-codex": "codex-review",
        }[tier]
    deterministic_closed = tier == "L0-local" and bundle_tier == "L0-local"
    return {
        "schema_version": 1,
        "tier": tier,
        "action": action,
        "reason_codes": sorted(set(reasons)),
        "checker_model_dispatch": False if deterministic_closed else None,
        "checker_skip_reason": (
            "checker skipped: deterministic evidence sufficient"
            if deterministic_closed else None
        ),
        "codex_deep_review_skipped": deterministic_closed,
        "codex_deep_review_skip_reason": (
            "no semantic-risk acceptance delta"
            if deterministic_closed else None
        ),
        "evidence_expansion": "on-demand" if tier == "L2-codex" else "none",
        "final_merge_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(json.dumps(select_tier(data), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
