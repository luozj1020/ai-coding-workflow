#!/usr/bin/env python3
"""Validate that a Claude report is a bounded, role-correct report artifact."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_HEADINGS = (
    "Requirements Summary",
    "Files Changed",
    "Acceptance Criteria Mapping",
    "Out-of-Scope Confirmation",
    "Plan Match",
    "Checks Run",
)
FORBIDDEN_MARKERS = (
    "AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT",
    "AI-CODING-WORKFLOW:DISPATCH-SEEDED-PROGRESS",
    "AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT",
    "Dispatcher-created draft",
    "fallback report was generated",
    "did not produce a valid Claude-owned CLAUDE_REPORT.md",
    "did not produce a Claude-owned CLAUDE_REPORT.md",
)
PROGRESS_FIELDS = re.compile(
    r"(?im)^\s*-?\s*(?:Current|Execution) Phase\s*:|"
    r"^\s*-?\s*(?:Context Acquisition Complete|Planned First Write|"
    r"Implementation Complete|Tail Work Complete|Completion Ready)\s*:"
)
CODE_LINE = re.compile(
    r"^\s*(?:def |class |import |from .+ import |function |const |let |var |"
    r"public |private |protected |#include|package |func |type |interface |"
    r"if\s*\(|for\s*\(|while\s*\(|return\b|[{}]\s*$)"
)


def validate(path: Path) -> dict[str, object]:
    reasons: list[str] = []
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        return {"schema_version": 1, "valid": False, "reasons": ["missing-or-empty"]}
    if path.stat().st_size > 256 * 1024:
        reasons.append("report-too-large")
    text = path.read_text(encoding="utf-8", errors="replace")
    if any(marker.lower() in text.lower() for marker in FORBIDDEN_MARKERS):
        reasons.append("seeded-fallback-or-progress-marker")
    first_content = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not re.match(r"^#\s+.*(?:Claude|Builder|Checker|Planner).*(?:Report|报告)\b", first_content, re.I):
        reasons.append("invalid-report-title")
    headings = set(re.findall(r"(?m)^##\s+(.+?)\s*$", text))
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in headings]
    if missing:
        reasons.append("missing-headings:" + ",".join(missing))

    # A progress artifact copied into the report has several lifecycle fields
    # but no report structure. One incidental phase reference remains allowed.
    progress_fields = len(PROGRESS_FIELDS.findall(text))
    if progress_fields >= 3:
        reasons.append("progress-report-role-mismatch")

    lines = [line for line in text.splitlines() if line.strip()]
    code_lines = sum(bool(CODE_LINE.match(line)) for line in lines)
    fenced_blocks = re.findall(r"(?ms)^```[^\n]*\n(.*?)^```\s*$", text)
    largest_fence = max((len(block.splitlines()) for block in fenced_blocks), default=0)
    if largest_fence > 120 or (len(lines) >= 100 and code_lines / len(lines) > 0.45):
        reasons.append("source-body-dominates-report")
    return {
        "schema_version": 1,
        "valid": not reasons,
        "reasons": reasons,
        "required_heading_count": len(REQUIRED_HEADINGS),
        "observed_heading_count": len(headings),
        "progress_field_count": progress_fields,
        "code_line_count": code_lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    value = validate(args.report)
    if args.json:
        print(json.dumps(value, sort_keys=True))
    return 0 if value["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
