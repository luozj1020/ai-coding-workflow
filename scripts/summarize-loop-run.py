#!/usr/bin/env python3
"""Summarize workflow run artifacts into Quality / Speed / Cost / Stability metrics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
DECISION_RE = re.compile(r"\b(ACCEPT|REVISE|SPLIT|REJECT)\b", re.I)
NUMBER_RE = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_number(value: str):
    match = NUMBER_RE.search(value)
    if not match:
        return None
    raw = match.group(0)
    return float(raw) if "." in raw else int(raw)


def parse_usage_file(path: Path) -> dict[str, float]:
    usage: dict[str, float] = {}
    for line in read_text(path).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().strip("|")
        if not key:
            continue
        number = parse_number(value)
        if number is None:
            continue
        lower = key.lower()
        if lower.endswith("tokens") or lower in {
            "total_cost_usd",
            "cost_usd",
            "duration_ms",
            "duration_api_ms",
            "num_turns",
            "total_tokens",
        }:
            usage[lower] = usage.get(lower, 0) + float(number)
    return usage


def add_usage(total: dict[str, float], usage: dict[str, float]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value


def parse_progress_seconds(paths: list[Path]) -> int | None:
    timestamps: list[datetime] = []
    for path in paths:
        for line in read_text(path).splitlines():
            match = TIMESTAMP_RE.match(line)
            if not match:
                continue
            try:
                timestamps.append(datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
    if len(timestamps) < 2:
        return None
    return int((max(timestamps) - min(timestamps)).total_seconds())


def parse_decision(paths: list[Path]) -> str:
    for path in reversed(paths):
        text = read_text(path)
        for line in text.splitlines():
            match = DECISION_RE.search(line)
            if match:
                return match.group(1).upper()
    return "UNKNOWN"


def checker_status(path: Path) -> str:
    text = read_text(path)
    if "ALL GREEN" in text:
        return "ALL GREEN"
    if "FAILED" in text:
        return "FAILED"
    if text:
        return "UNKNOWN"
    return "MISSING"


def quality_score(decision: str, checker_results: list[str]) -> float:
    decision_score = {
        "ACCEPT": 1.0,
        "REVISE": 0.5,
        "SPLIT": 0.4,
        "REJECT": 0.0,
        "UNKNOWN": 0.0,
    }.get(decision, 0.0)
    if not checker_results:
        checker_score = 0.0
    else:
        green = sum(1 for result in checker_results if result == "ALL GREEN")
        checker_score = green / len(checker_results)
    return round((decision_score * 0.7) + (checker_score * 0.3), 3)


def stability_findings(paths: list[Path], checker_results: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        text = read_text(path).lower()
        name = path.name
        if "timed out" in text or "runtime timeout" in text:
            findings.append(f"{name}: timeout evidence")
        if "no-output timeout" in text:
            findings.append(f"{name}: no-output timeout evidence")
        if "fallback report" in text or "did not create claude_report.md" in text:
            findings.append(f"{name}: fallback report evidence")
        if "checker mutation guard" in text:
            findings.append(f"{name}: checker mutation guard triggered")
    missing = sum(1 for result in checker_results if result == "MISSING")
    failed = sum(1 for result in checker_results if result == "FAILED")
    if missing:
        findings.append(f"{missing} checker report(s) missing")
    if failed:
        findings.append(f"{failed} checker report(s) failed")
    return findings


def discover_run(path: Path) -> dict[str, list[Path]]:
    if path.is_file():
        root = path.parent
    else:
        root = path
    return {
        "result": sorted(root.rglob("*.result.json")),
        "usage": sorted(root.rglob("*.usage.txt")) + sorted(root.rglob("*.codex-usage.txt")),
        "progress": sorted(root.rglob("*.progress.log")),
        "checker": sorted(root.rglob("*.checker-report.md")),
        "review": sorted(root.rglob("*.review.txt")) + sorted(root.glob("review-*.txt")),
        "status": sorted(root.rglob("*.status.txt")),
        "report": sorted(root.rglob("*.report.md")),
        "diff": sorted(root.rglob("*.diff")),
        "events": sorted(root.rglob("loop-events.jsonl")),
    }


def latest_default_path(repo_root: Path) -> Path:
    worktrees = repo_root / ".worktrees"
    loops = sorted(worktrees.glob("loop-*"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    if loops:
        return loops[-1]
    return worktrees


def summarize(path: Path) -> dict:
    artifacts = discover_run(path)
    usage_total: dict[str, float] = {}
    for usage_file in artifacts["usage"]:
        add_usage(usage_total, parse_usage_file(usage_file))

    checker_results = [checker_status(path) for path in artifacts["checker"]]
    decision = parse_decision(artifacts["review"])
    elapsed_seconds = parse_progress_seconds(artifacts["progress"])
    stability = stability_findings(
        artifacts["progress"] + artifacts["status"] + artifacts["report"] + artifacts["checker"],
        checker_results,
    )

    return {
        "run_path": str(path),
        "decision": decision,
        "quality_score": quality_score(decision, checker_results),
        "speed": {
            "elapsed_seconds_from_progress": elapsed_seconds,
            "progress_logs": len(artifacts["progress"]),
        },
        "cost": usage_total,
        "stability": {
            "finding_count": len(stability),
            "findings": stability,
        },
        "artifacts": {key: len(value) for key, value in artifacts.items()},
        "checker_results": checker_results,
    }


def format_value(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_markdown(summary: dict) -> str:
    lines = [
        "# Workflow Quality Summary",
        "",
        f"Run path: `{summary['run_path']}`",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Decision | {summary['decision']} |",
        f"| Quality score | {summary['quality_score']} |",
        f"| Elapsed seconds | {format_value(summary['speed']['elapsed_seconds_from_progress'])} |",
        f"| Stability findings | {summary['stability']['finding_count']} |",
        "",
        "## Cost",
        "",
        "| Field | Value |",
        "|-------|-------|",
    ]
    if summary["cost"]:
        for key in sorted(summary["cost"]):
            lines.append(f"| {key} | {format_value(summary['cost'][key])} |")
    else:
        lines.append("| usage | unavailable |")

    lines.extend(["", "## Artifacts", "", "| Artifact Type | Count |", "|---------------|-------|"])
    for key in sorted(summary["artifacts"]):
        lines.append(f"| {key} | {summary['artifacts'][key]} |")

    lines.extend(["", "## Checker Results", ""])
    if summary["checker_results"]:
        for idx, result in enumerate(summary["checker_results"], 1):
            lines.append(f"- Checker {idx}: {result}")
    else:
        lines.append("- No checker reports found.")

    lines.extend(["", "## Stability Findings", ""])
    if summary["stability"]["findings"]:
        for finding in summary["stability"]["findings"]:
            lines.append(f"- {finding}")
    else:
        lines.append("- No stability issues detected from available artifacts.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_path", nargs="?", help="Loop directory, dispatch artifact directory, or repository root.")
    parser.add_argument("--output", help="Write Markdown summary to this path instead of stdout.")
    parser.add_argument("--json-output", help="Write machine-readable JSON summary to this path.")
    args = parser.parse_args()

    if args.run_path:
        path = Path(args.run_path).resolve()
    else:
        path = latest_default_path(Path.cwd()).resolve()

    if not path.exists():
        print(f"Error: path not found: {path}", file=sys.stderr)
        return 1

    summary = summarize(path)
    markdown = render_markdown(summary)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")

    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
