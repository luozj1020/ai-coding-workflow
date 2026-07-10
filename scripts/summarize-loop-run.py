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
SEEDED_REPORT_MARKER = "AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT"
FALLBACK_REPORT_MARKER = "AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"
ACK_RE = re.compile(
    r"Direction / Boundary Acknowledgement|My understanding:|Planned scope:|"
    r"Recommendation:\s*(proceed|narrow|split|stop-and-report|stop)",
    re.I,
)


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


def parse_progress_stage_seconds(paths: list[Path]) -> dict[str, int | None]:
    stages: dict[str, datetime] = {}
    for path in paths:
        for line in read_text(path).splitlines():
            match = TIMESTAMP_RE.match(line)
            if not match:
                continue
            try:
                timestamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if "Starting Claude Code:" in line:
                stages.setdefault("claude_starting", timestamp)
            elif "Claude process started:" in line:
                stages.setdefault("claude_process_started", timestamp)
            elif "Claude subprocess ended; dispatcher finalizing artifacts:" in line:
                stages.setdefault("claude_subprocess_ended", timestamp)
            elif "Starting checker helper:" in line:
                stages.setdefault("checker_started", timestamp)
            elif "Checker helper completed:" in line:
                stages.setdefault("checker_completed", timestamp)
            elif "Dispatch evidence classification:" in line:
                stages.setdefault("evidence_classified", timestamp)

    def delta(start: str, end: str) -> int | None:
        if start not in stages or end not in stages:
            return None
        return max(0, int((stages[end] - stages[start]).total_seconds()))

    return {
        "claude_startup_seconds": delta("claude_starting", "claude_process_started"),
        "claude_execution_seconds": delta("claude_process_started", "claude_subprocess_ended"),
        "checker_seconds": delta("checker_started", "checker_completed"),
        "artifact_finalization_seconds": delta("claude_subprocess_ended", "evidence_classified"),
    }


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


def is_spark_report(path: Path) -> bool:
    return path.name == "codex-spark.report.md"


def report_status(path: Path) -> str:
    text = read_text(path)
    if not text.strip():
        return "missing"
    lowered = text.lower()
    if SEEDED_REPORT_MARKER in text or "dispatcher-created draft" in lowered:
        return "seeded report only"
    if (
        FALLBACK_REPORT_MARKER in text
        or "fallback report was generated" in lowered
        or "did not produce a valid claude-owned claude_report.md" in lowered
        or "did not produce a claude-owned claude_report.md" in lowered
    ):
        return "fallback report"
    return "valid report"


def has_diff_evidence(paths: list[Path]) -> bool:
    for path in paths:
        if path.name.startswith("codex-spark."):
            continue
        text = read_text(path).strip()
        if not text:
            continue
        lowered = text.lower()
        if "diff --git" in text or "unstaged name status" in lowered or "staged name status" in lowered:
            return True
        meaningful = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and line.strip() != "(none)"
            and not line.startswith("#")
            and not line.startswith("##")
            and not line.lower().startswith("evidence mode:")
        ]
        if meaningful:
            return True
    return False


def classify_claude_evidence(artifacts: dict[str, list[Path]], decision: str) -> dict[str, str]:
    claude_reports = [path for path in artifacts["report"] if not is_spark_report(path)]
    statuses = [report_status(path) for path in claude_reports]
    valid_report = any(status == "valid report" for status in statuses)
    seeded_report = any(status == "seeded report only" for status in statuses)
    fallback_report = any(status == "fallback report" for status in statuses)
    diff_present = has_diff_evidence(artifacts["diff"])
    evidence_text = "\n".join(read_text(path) for path in artifacts["progress"] + claude_reports)
    ack_only = bool(ACK_RE.search(evidence_text)) and not diff_present and not valid_report

    if diff_present and valid_report:
        state = "diff + valid report"
    elif diff_present and decision == "ACCEPT" and not valid_report:
        state = "no report but diff accepted"
    elif diff_present:
        state = "diff without report"
    elif ack_only:
        state = "acknowledgement only"
    elif fallback_report:
        state = "fallback report"
    elif seeded_report:
        state = "seeded report only"
    elif valid_report:
        state = "valid report without diff"
    else:
        state = "no useful progress"

    if valid_report:
        report_state = "valid report"
    elif fallback_report:
        report_state = "fallback report"
    elif seeded_report:
        report_state = "seeded report only"
    elif claude_reports:
        report_state = ", ".join(statuses)
    else:
        report_state = "missing"

    return {
        "evidence_state": state,
        "report_state": report_state,
        "valid_report": "yes" if valid_report else "no",
        "diff_present": "yes" if diff_present else "no",
        "accepted_without_valid_report": "yes" if state == "no report but diff accepted" else "no",
        "claude_report_count": str(len(claude_reports)),
    }


def parse_markdown_table_fields(text: str, heading: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == f"## {heading}"
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if not key or key.lower() == "field":
            continue
        normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        if normalized:
            fields[normalized] = value
    return fields


def parse_goal_loop_contract(paths: list[Path]) -> dict[str, str]:
    for path in paths:
        fields = parse_markdown_table_fields(read_text(path), "Goal Loop Contract")
        if fields:
            return fields
    return {}


def parse_first_table(paths: list[Path], headings: list[str]) -> dict[str, str]:
    for path in paths:
        text = read_text(path)
        for heading in headings:
            fields = parse_markdown_table_fields(text, heading)
            if fields:
                return fields
    return {}


def parse_all_tables(paths: list[Path], headings: list[str]) -> list[dict[str, str]]:
    """Parse every matching table across all files, not just the first."""
    results: list[dict[str, str]] = []
    for path in paths:
        text = read_text(path)
        for heading in headings:
            # Walk all occurrences of the heading in this file
            lines = text.splitlines()
            idx = 0
            while idx < len(lines):
                if lines[idx].strip() == f"## {heading}":
                    fields: dict[str, str] = {}
                    idx += 1
                    while idx < len(lines):
                        stripped = lines[idx].strip()
                        if not stripped.startswith("|") or "---" in stripped:
                            if stripped.startswith("## "):
                                break
                            idx += 1
                            continue
                        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                        if len(cells) >= 2:
                            key, value = cells[0], cells[1]
                            if key and key.lower() != "field":
                                normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
                                if normalized:
                                    fields[normalized] = value
                        idx += 1
                    if fields:
                        results.append(fields)
                else:
                    idx += 1
    return results


def ordered_unique(values: list[str]) -> list[str]:
    """Return unique values preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


def safe_numeric_sum(values: list[str | int | float]) -> float:
    """Sum numeric values; missing/empty/non-numeric treated as 0."""
    total = 0.0
    for v in values:
        if isinstance(v, (int, float)):
            total += float(v)
        elif isinstance(v, str):
            num = parse_number(v)
            if num is not None:
                total += float(num)
    return total


def _first_str(*candidates: str) -> str:
    """Return first non-empty candidate or 'not recorded'."""
    for c in candidates:
        if c:
            return c
    return "not recorded"


def spark_status(
    gate: dict[str, str],
    followups: list[dict[str, str]],
    spark_reports: list[Path],
) -> dict[str, str | int | list[str]]:
    """Aggregate Spark status from gate and one or more follow-up tables.

    Legacy singular fields use the first followup for backward compatibility.
    New aggregate fields span all followups.
    """
    first = followups[0] if followups else {}

    # --- Legacy singular fields (backward compatible) ---
    enabled = _first_str(
        first.get("spark_enabled_in_task_card"),
        gate.get("spark_enabled"),
    )
    invoked = _first_str(
        first.get("spark_invoked"),
        *([] if spark_reports else []),
    ) if first else ("yes" if spark_reports else "no")
    if not invoked or invoked == "not recorded":
        invoked = "yes" if spark_reports else "no"
    mode = _first_str(first.get("spark_purpose_used"), gate.get("spark_purpose"))
    requested_mode = _first_str(first.get("spark_requested_mode"))
    model = _first_str(first.get("spark_model_used"), gate.get("spark_model"))
    artifact = _first_str(
        first.get("artifact_directory"),
        first.get("invocation_command_or_artifact"),
        *(str(spark_reports[0]),) if spark_reports else (),
    )
    exit_code = _first_str(first.get("spark_exit_code"))
    auto_disabled = _first_str(first.get("spark_auto_disabled"))
    auto_disable_reason = _first_str(first.get("auto_disable_reason"))
    fallback = _first_str(first.get("strong_model_fallback_used"))
    if fallback == "not recorded":
        fallback = "no"
    sandbox = _first_str(first.get("sandbox_used"), gate.get("sandbox"))
    task_size = _first_str(first.get("task_size_classification"))
    route = _first_str(first.get("spark_routing_recommendation"))
    confidence = _first_str(first.get("spark_classification_confidence"))
    accepted = _first_str(
        first.get("accepted_suggestions"),
        first.get("spark_suggestions_accepted"),
        first.get("spark_result_accepted_by_codex"),
    )
    ignored = _first_str(
        first.get("ignored_suggestions"),
        first.get("spark_suggestions_ignored"),
    )
    conflicts_with_claude = _first_str(first.get("conflicts_with_claude"))
    conflicts_with_local_evidence = _first_str(first.get("conflicts_with_local_evidence"))
    acceptance_satisfied = _first_str(
        first.get("acceptance_satisfied_by_spark"),
        first.get("spark_output_can_satisfy_acceptance"),
    )
    if acceptance_satisfied == "not recorded":
        acceptance_satisfied = "no"

    # --- Aggregate staged fields ---
    helper_invocation_count = len(followups)
    calls_values = [f.get("spark_calls_used", "0") for f in followups]
    total_calls = int(safe_numeric_sum(calls_values))
    unique_modes = ordered_unique([f.get("spark_purpose_used", "") for f in followups])
    unique_stages = ordered_unique([f.get("spark_pipeline_stage", "") for f in followups])
    unique_roles: list[str] = []
    for f in followups:
        roles_str = f.get("spark_roles_executed", "")
        if roles_str:
            for role in roles_str.split(","):
                role = role.strip()
                if role:
                    unique_roles.append(role)
    unique_roles = ordered_unique(unique_roles)
    unique_budget_requested = ordered_unique([f.get("spark_budget_mode_requested", "") for f in followups])
    unique_budget_effective = ordered_unique([f.get("spark_budget_mode_effective", "") for f in followups])
    unique_provisional = ordered_unique([f.get("spark_provisional_acceptance", "") for f in followups])
    unique_strong_review = ordered_unique([f.get("strong_review_required", "") for f in followups])
    unique_merge_authorized = ordered_unique([f.get("merge_authorized", "") for f in followups])
    auto_disabled_values = [f.get("spark_auto_disabled", "") for f in followups]
    auto_disabled_reasons = ordered_unique([f.get("auto_disable_reason", "") for f in followups])

    result: dict[str, str | int | list[str]] = {
        # Legacy singular fields
        "enabled": enabled,
        "invoked": invoked,
        "mode": mode,
        "requested_mode": requested_mode,
        "model": model,
        "artifact": artifact,
        "exit_code": exit_code,
        "auto_disabled": auto_disabled,
        "auto_disable_reason": auto_disable_reason,
        "strong_model_fallback": fallback,
        "sandbox": sandbox,
        "task_size_classification": task_size,
        "routing_recommendation": route,
        "classification_confidence": confidence,
        "accepted_suggestions": accepted,
        "ignored_suggestions": ignored,
        "conflicts_with_claude": conflicts_with_claude,
        "conflicts_with_local_evidence": conflicts_with_local_evidence,
        "acceptance_satisfied_by_spark": acceptance_satisfied,
        # Aggregate staged fields
        "helper_invocation_count": helper_invocation_count,
        "total_spark_calls": total_calls,
        "unique_modes": unique_modes,
        "unique_pipeline_stages": unique_stages,
        "unique_roles_executed": unique_roles,
        "unique_budget_requested": unique_budget_requested,
        "unique_budget_effective": unique_budget_effective,
        "unique_provisional_acceptance": unique_provisional,
        "unique_strong_review_required": unique_strong_review,
        "unique_merge_authorized": unique_merge_authorized,
        "auto_disabled_occurrences": sum(1 for v in auto_disabled_values if v.startswith("yes")),
        "auto_disabled_reasons": auto_disabled_reasons,
    }
    return result


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
        "network": sorted(root.rglob("*.network.log")),
        "report": sorted(root.rglob("*.report.md")),
        "spark_report": sorted(root.rglob("codex-spark.report.md")),
        "diff": sorted(root.rglob("*.diff")),
        "events": sorted(root.rglob("loop-events.jsonl")),
        "parallel": sorted(root.rglob("parallel-summary.md")),
        "task_card": sorted(root.rglob("task-card-*.md")) + sorted(root.rglob("CLAUDE_TASK_CARD.md")),
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
    goal_contract = parse_goal_loop_contract(artifacts["task_card"])
    advisor_gate = parse_first_table(artifacts["task_card"], ["Advisor Gate"])
    advisor_followup = parse_first_table(artifacts["report"], ["Advisor Follow-up", "Advisor Result"])
    codex_spark_gate = parse_first_table(artifacts["task_card"], ["Codex Spark Gate"])
    codex_spark_followup = parse_first_table(
        artifacts["spark_report"] + artifacts["report"],
        ["Codex Spark Follow-up"],
    )
    codex_spark_followups = parse_all_tables(
        artifacts["spark_report"] + artifacts["report"],
        ["Codex Spark Follow-up"],
    )
    if not codex_spark_followups and codex_spark_followup:
        codex_spark_followups = [codex_spark_followup]
    parallel_execution_gate = parse_first_table(artifacts["task_card"], ["Parallel Execution Gate"])
    parallel_execution_followup = parse_first_table(
        artifacts["report"] + artifacts["parallel"],
        ["Parallel Execution Follow-up"],
    )
    spec_gate = parse_first_table(artifacts["task_card"], ["Spec Gate"])
    spec_followup = parse_first_table(artifacts["report"], ["Spec Follow-up"])
    root_cause_gate = parse_first_table(artifacts["task_card"], ["Root Cause Gate"])
    root_cause_followup = parse_first_table(artifacts["report"], ["Root Cause Follow-up"])
    tdd_contract = parse_first_table(artifacts["task_card"], ["Test-First / TDD Contract"])
    tdd_followup = parse_first_table(artifacts["report"], ["Test-First / TDD Follow-up"])
    finish_branch_followup = parse_first_table(artifacts["report"], ["Finish Branch Follow-up"])
    elapsed_seconds = parse_progress_seconds(artifacts["progress"])
    stage_seconds = parse_progress_stage_seconds(artifacts["progress"])
    stability = stability_findings(
        artifacts["progress"] + artifacts["status"] + artifacts["report"] + artifacts["checker"],
        checker_results,
    )

    claude_evidence = classify_claude_evidence(artifacts, decision)
    normalized_spark_status = spark_status(
        codex_spark_gate,
        codex_spark_followups,
        artifacts["spark_report"],
    )

    return {
        "run_path": str(path),
        "decision": decision,
        "quality_score": quality_score(decision, checker_results),
        "speed": {
            "elapsed_seconds_from_progress": elapsed_seconds,
            "progress_logs": len(artifacts["progress"]),
            **stage_seconds,
        },
        "cost": usage_total,
        "goal_loop_contract": goal_contract,
        "advisor_gate": advisor_gate,
        "advisor_followup": advisor_followup,
        "codex_spark_gate": codex_spark_gate,
        "codex_spark_followup": codex_spark_followup,
        "codex_spark_followups": codex_spark_followups,
        "spark_status": normalized_spark_status,
        "parallel_execution_gate": parallel_execution_gate,
        "parallel_execution_followup": parallel_execution_followup,
        "spec_gate": spec_gate,
        "spec_followup": spec_followup,
        "root_cause_gate": root_cause_gate,
        "root_cause_followup": root_cause_followup,
        "tdd_contract": tdd_contract,
        "tdd_followup": tdd_followup,
        "finish_branch_followup": finish_branch_followup,
        "stability": {
            "finding_count": len(stability),
            "findings": stability,
        },
        "artifacts": {key: len(value) for key, value in artifacts.items()},
        "claude_evidence": claude_evidence,
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

    lines.extend(["", "## Speed", "", "| Field | Value |", "|-------|-------|"])
    for key in [
        "elapsed_seconds_from_progress",
        "claude_startup_seconds",
        "claude_execution_seconds",
        "checker_seconds",
        "artifact_finalization_seconds",
        "progress_logs",
    ]:
        lines.append(f"| {key} | {format_value(summary['speed'].get(key))} |")

    lines.extend(["", "## Claude Evidence Classification", "", "| Field | Value |", "|-------|-------|"])
    for key in [
        "evidence_state",
        "report_state",
        "valid_report",
        "diff_present",
        "accepted_without_valid_report",
        "claude_report_count",
    ]:
        lines.append(f"| {key} | {summary['claude_evidence'][key]} |")

    lines.extend(["", "## Spark Status", "", "| Field | Value |", "|-------|-------|"])
    spark = summary["spark_status"]
    for key in [
        "enabled",
        "invoked",
        "mode",
        "requested_mode",
        "model",
        "artifact",
        "exit_code",
        "auto_disabled",
        "auto_disable_reason",
        "strong_model_fallback",
        "sandbox",
        "task_size_classification",
        "routing_recommendation",
        "classification_confidence",
        "accepted_suggestions",
        "ignored_suggestions",
        "conflicts_with_claude",
        "conflicts_with_local_evidence",
        "acceptance_satisfied_by_spark",
    ]:
        lines.append(f"| {key} | {spark[key]} |")
    # Aggregate staged fields
    for key in [
        "helper_invocation_count",
        "total_spark_calls",
        "unique_modes",
        "unique_pipeline_stages",
        "unique_roles_executed",
        "unique_budget_requested",
        "unique_budget_effective",
        "unique_provisional_acceptance",
        "unique_strong_review_required",
        "unique_merge_authorized",
        "auto_disabled_occurrences",
        "auto_disabled_reasons",
    ]:
        value = spark[key]
        if isinstance(value, list):
            value = ", ".join(value) if value else "none"
        lines.append(f"| {key} | {format_value(value)} |")

    lines.extend(["", "## Goal Loop Contract", "", "| Field | Value |", "|-------|-------|"])
    if summary["goal_loop_contract"]:
        for key in sorted(summary["goal_loop_contract"]):
            lines.append(f"| {key} | {summary['goal_loop_contract'][key]} |")
    else:
        lines.append("| goal_loop_contract | unavailable |")

    lines.extend(["", "## Advisor Gate", "", "| Field | Value |", "|-------|-------|"])
    if summary["advisor_gate"]:
        for key in sorted(summary["advisor_gate"]):
            lines.append(f"| {key} | {summary['advisor_gate'][key]} |")
    else:
        lines.append("| advisor_gate | unavailable |")

    lines.extend(["", "## Advisor Follow-up", "", "| Field | Value |", "|-------|-------|"])
    if summary["advisor_followup"]:
        for key in sorted(summary["advisor_followup"]):
            lines.append(f"| {key} | {summary['advisor_followup'][key]} |")
    else:
        lines.append("| advisor_followup | unavailable |")

    for heading, key in [
        ("Spec Gate", "spec_gate"),
        ("Spec Follow-up", "spec_followup"),
        ("Codex Spark Gate", "codex_spark_gate"),
        ("Codex Spark Follow-up", "codex_spark_followup"),
        ("Parallel Execution Gate", "parallel_execution_gate"),
        ("Parallel Execution Follow-up", "parallel_execution_followup"),
        ("Root Cause Gate", "root_cause_gate"),
        ("Root Cause Follow-up", "root_cause_followup"),
        ("Test-First / TDD Contract", "tdd_contract"),
        ("Test-First / TDD Follow-up", "tdd_followup"),
        ("Finish Branch Follow-up", "finish_branch_followup"),
    ]:
        lines.extend(["", f"## {heading}", "", "| Field | Value |", "|-------|-------|"])
        if summary[key]:
            for field in sorted(summary[key]):
                lines.append(f"| {field} | {summary[key][field]} |")
        else:
            lines.append(f"| {key} | unavailable |")

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
