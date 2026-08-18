#!/usr/bin/env python3
"""Validate that a Claude report is a bounded, role-correct report artifact."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path


REQUIRED_HEADINGS = (
    "Requirements Summary",
    "Files Changed",
    "Acceptance Criteria Mapping",
    "Out-of-Scope Confirmation",
    "Plan Match",
    "Checks Run",
)
HEADING_ALIASES = {
    "Requirements Summary": {
        "requirements summary", "requirement summary", "requirements",
        "requirements covered", "requirement coverage",
    },
    "Files Changed": {
        "files changed", "changed files", "modified files", "file changes",
    },
    "Acceptance Criteria Mapping": {
        "acceptance criteria mapping", "acceptance mapping",
        "acceptance criteria", "criteria mapping",
    },
    "Out-of-Scope Confirmation": {
        "out of scope confirmation", "out-of-scope confirmation",
        "out of scope", "out-of-scope", "scope confirmation", "scope compliance",
    },
    "Plan Match": {
        "plan match", "plan alignment", "plan conformance", "plan compliance",
    },
    "Checks Run": {
        "checks run", "tests run", "validation", "validations",
        "validation run", "validation evidence", "checks and validation",
    },
}
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


def _normalized_heading(value: str) -> str:
    value = re.sub(r"^[\s\d.)_-]+", "", value.strip().lower())
    value = value.replace("—", " ").replace("–", " ").replace("-", " ")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    title_exact = bool(re.match(
        r"^#\s+.*(?:Claude|Builder|Checker|Planner).*(?:Report|报告)\b",
        first_content,
        re.I,
    ))
    title_normalized = bool(re.match(
        r"^#\s+.*(?:Claude|Builder|Checker|Planner|Implementation|Modification|Execution)"
        r".*(?:Report|报告)\b",
        first_content,
        re.I,
    ))
    if not title_normalized:
        reasons.append("invalid-report-title")
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    normalized_observed = {_normalized_heading(heading): heading for heading in headings}
    heading_mapping: dict[str, str] = {}
    for required in REQUIRED_HEADINGS:
        aliases = {_normalized_heading(value) for value in HEADING_ALIASES[required]}
        observed = next(
            (normalized_observed[value] for value in aliases if value in normalized_observed),
            None,
        )
        if observed is not None:
            heading_mapping[required] = observed
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in heading_mapping]
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
    normalized_headings = {
        required: observed
        for required, observed in heading_mapping.items()
        if required != observed
    }
    normalization_applied = (title_normalized and not title_exact) or bool(normalized_headings)
    return {
        "schema_version": 1,
        "valid": not reasons,
        "reasons": reasons,
        "missing_headings": missing,
        "normalization_applied": normalization_applied,
        "normalized_title": first_content if title_normalized and not title_exact else None,
        "normalized_headings": normalized_headings,
        "required_heading_count": len(REQUIRED_HEADINGS),
        "observed_heading_count": len(headings),
        "progress_field_count": progress_fields,
        "code_line_count": code_lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = validate(args.report)
    if args.output is not None:
        _atomic_json(args.output, value)
    if args.json:
        print(json.dumps(value, sort_keys=True))
    return 0 if value["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
