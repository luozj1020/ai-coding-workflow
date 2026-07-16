#!/usr/bin/env python3
"""Deterministic workflow economics, diff reuse, and route calibration.

The helper never invokes a model.  It measures observable control-plane work,
estimates how much of a Claude diff survives in the final diff, and derives a
conservative repository/task-type routing bias from accepted historical runs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import importlib.util
import json
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1
DEFAULT_HISTORY = Path(".ai-workflow/economics-history.jsonl")


def _usage_summary(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    helper = Path(__file__).with_name("model-usage.py")
    spec = importlib.util.spec_from_file_location("aiwf_model_usage", helper)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.aggregate(module.load_records(path))


def _read_json(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _meaningful_added_lines(text: str) -> Counter[Tuple[str, str]]:
    """Return normalized added lines keyed by file.

    This is an approximation, deliberately labelled as such: it measures
    retained implementation content without claiming semantic equivalence.
    """
    current = "unknown"
    values: Counter[Tuple[str, str]] = Counter()
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split(" b/", 1)
            current = parts[1] if len(parts) == 2 else "unknown"
            continue
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        normalized = " ".join(raw[1:].strip().split())
        if normalized:
            values[(current, normalized)] += 1
    return values


def diff_reuse(claude_diff: Path, final_diff: Path) -> Dict[str, Any]:
    claude = _meaningful_added_lines(claude_diff.read_text(encoding="utf-8", errors="replace"))
    final = _meaningful_added_lines(final_diff.read_text(encoding="utf-8", errors="replace"))
    retained = claude & final
    by_file_total: Dict[str, int] = defaultdict(int)
    by_file_retained: Dict[str, int] = defaultdict(int)
    for (path, _), count in claude.items():
        by_file_total[path] += count
    for (path, _), count in retained.items():
        by_file_retained[path] += count
    total = sum(claude.values())
    retained_total = sum(retained.values())
    return {
        "method": "normalized-added-line-intersection",
        "approximate": True,
        "claude_added_lines": total,
        "retained_added_lines": retained_total,
        "reuse_ratio": round(retained_total / total, 4) if total else None,
        "per_file": {
            path: {
                "claude_added_lines": count,
                "retained_added_lines": by_file_retained.get(path, 0),
                "reuse_ratio": round(by_file_retained.get(path, 0) / count, 4),
            }
            for path, count in sorted(by_file_total.items())
        },
    }


def load_history(path: Path, task_type: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in lines[-limit:]:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("schema_version") != SCHEMA_VERSION:
            continue
        if task_type and row.get("task_type") != task_type:
            continue
        if row.get("accepted") is not True:
            continue
        rows.append(row)
    return rows


def calibrate(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    reuse = [
        float(row["claude_reuse_ratio"])
        for row in rows
        if isinstance(row.get("claude_reuse_ratio"), (int, float))
        and not isinstance(row.get("claude_reuse_ratio"), bool)
    ]
    delegated = [row for row in rows if row.get("owner") == "claude-builder"]
    first_pass = sum(1 for row in delegated if row.get("first_pass") is True)
    takeover = sum(1 for row in delegated if row.get("codex_takeover") is True)
    sample_count = len(rows)
    median_reuse = round(statistics.median(reuse), 4) if reuse else None
    first_pass_rate = round(first_pass / len(delegated), 4) if delegated else None
    takeover_rate = round(takeover / len(delegated), 4) if delegated else None

    bias = "none"
    reason = "insufficient-history"
    if len(reuse) >= 3 and median_reuse is not None:
        if median_reuse < 0.30 or (takeover_rate is not None and takeover_rate > 0.50):
            bias, reason = "codex-fast-path", "low-claude-reuse-or-high-takeover"
        elif median_reuse >= 0.70 and first_pass_rate is not None and first_pass_rate >= 0.70:
            bias, reason = "claude-builder", "high-claude-reuse-and-first-pass-rate"
        else:
            reason = "mixed-history-no-owner-bias"
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_count": sample_count,
        "reuse_sample_count": len(reuse),
        "median_claude_reuse_ratio": median_reuse,
        "claude_first_pass_rate": first_pass_rate,
        "codex_takeover_rate": takeover_rate,
        "owner_bias": bias,
        "reason": reason,
    }


def append_history_once(path: Path, record: Dict[str, Any]) -> bool:
    """Append one accepted terminal record, idempotent by run/task identity."""
    if record.get("accepted") is not True:
        return False
    identity = (str(record.get("run_id", "")), str(record.get("task_id", "")))
    if path.is_file():
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                existing = json.loads(raw)
                if not isinstance(existing, dict):
                    continue
                other = (str(existing.get("run_id", "")), str(existing.get("task_id", "")))
                if identity != ("", "") and other == identity:
                    return False
        except (OSError, json.JSONDecodeError):
            # Do not append into history whose integrity cannot be established.
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def build_record(args: argparse.Namespace) -> Dict[str, Any]:
    metrics = _read_json(args.metrics)
    reuse = diff_reuse(args.claude_diff, args.final_diff) if args.claude_diff and args.final_diff else {}
    calls = metrics.get("model_calls", []) if isinstance(metrics.get("model_calls"), list) else []
    counts = Counter(str(call.get("role", "unknown")) for call in calls if isinstance(call, dict))
    usage = _usage_summary(args.usage_ledger)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": metrics.get("run_id", ""),
        "task_id": args.task_id or metrics.get("task_id", ""),
        "task_type": args.task_type,
        "repository_scale": args.repository_scale,
        "owner": args.owner,
        "accepted": args.accepted,
        "first_pass": args.first_pass,
        "codex_takeover": args.codex_takeover,
        "claude_reuse_ratio": reuse.get("reuse_ratio"),
        "diff_reuse": reuse,
        "model_calls": dict(sorted(counts.items())),
        "model_usage": usage,
        "model_usage_complete": usage.get("totals", {}).get("usage_complete") if usage else None,
        "task_card_bytes": args.task_card.stat().st_size if args.task_card and args.task_card.is_file() else None,
        "review_packet_bytes": args.review_packet.stat().st_size if args.review_packet and args.review_packet.is_file() else None,
        "worktree_setup_seconds": metrics.get("worktree_setup_seconds"),
        "total_elapsed_seconds": metrics.get("total_elapsed_seconds"),
        "checker_model_dispatched": args.checker_model_dispatched,
        "reuse_evidence_available": bool(reuse),
        "reuse_unavailable_reason": None if reuse else "claude-and-final-diff-not-both-bound",
    }


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cal = sub.add_parser("calibrate", help="Read accepted history and emit a route calibration")
    cal.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    cal.add_argument("--task-type")

    rec = sub.add_parser("record", help="Build or append one observable economics record")
    rec.add_argument("--metrics", type=Path)
    rec.add_argument("--usage-ledger", type=Path,
                     help="Canonical model-usage.jsonl produced by model-usage.py")
    rec.add_argument("--claude-diff", type=Path)
    rec.add_argument("--final-diff", type=Path)
    rec.add_argument("--task-card", type=Path)
    rec.add_argument("--review-packet", type=Path)
    rec.add_argument("--task-id")
    rec.add_argument("--task-type", default="unknown")
    rec.add_argument("--repository-scale", default="unknown")
    rec.add_argument("--owner", choices=("codex-fast-path", "claude-builder"), required=True)
    rec.add_argument("--accepted", type=_bool, required=True)
    rec.add_argument("--first-pass", type=_bool, default=False)
    rec.add_argument("--codex-takeover", type=_bool, default=False)
    rec.add_argument("--checker-model-dispatched", type=_bool, default=False)
    rec.add_argument("--output", type=Path)
    rec.add_argument("--append-history", type=Path)
    args = parser.parse_args(argv)

    if args.command == "calibrate":
        value = calibrate(load_history(args.history, args.task_type))
    else:
        if bool(args.claude_diff) != bool(args.final_diff):
            parser.error("--claude-diff and --final-diff must be provided together")
        value = build_record(args)
        if args.append_history:
            value["history_appended"] = append_history_once(args.append_history, value)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if getattr(args, "output", None):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
