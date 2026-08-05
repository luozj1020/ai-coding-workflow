#!/usr/bin/env python3
"""Compress a Spark postflight stdout envelope into a bounded advisory capsule."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from evidence_capsule import artifact_ref, bounded_text, compact_bytes, finalize_capsule


FIELD = re.compile(r"^([a-z][a-z0-9_]*)=(.*)$")
HEADINGS = (
    "Decision Summary",
    "Risk Flags",
    "Scope and Boundaries",
    "Acceptance Matrix",
    "Evidence Conflicts",
    "Required Codex Decisions",
    "Recommended Next Action",
)


def parse_envelope(text: str) -> dict[str, object]:
    fields: dict[str, str] = {}
    statuses: list[str] = []
    for line in text.splitlines():
        match = FIELD.match(line.strip())
        if not match:
            continue
        key, value = match.groups()
        fields[key] = value
        if key == "spark_status":
            statuses.append(value)
    terminal = next(
        (item for item in reversed(statuses) if item in {"success", "failed", "unavailable"}),
        None,
    )
    protocol = fields.get("spark_protocol")
    return {
        "protocol": protocol,
        "terminal_status": terminal,
        "complete": protocol == "aiwf-spark-stdout-v1"
        and fields.get("spark_protocol_end") == protocol
        and terminal is not None,
        "truncated": fields.get("spark_output_truncated") == "yes",
        "model_response_received": fields.get("spark_model_response_received") == "yes",
        "failure_class": fields.get("spark_failure_class"),
    }


def extract_sections(text: str) -> dict[str, str | None]:
    positions: list[tuple[int, str, int]] = []
    for heading in HEADINGS:
        match = re.search(rf"(?m)^##?\s+{re.escape(heading)}\s*$", text)
        if match:
            positions.append((match.start(), heading, match.end()))
    positions.sort()
    sections: dict[str, str | None] = {heading: None for heading in HEADINGS}
    for index, (_, heading, content_start) in enumerate(positions):
        content_end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        content = " ".join(text[content_start:content_end].strip().split())
        sections[heading] = bounded_text(content, 320)
    return sections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spark_stdout", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stdout-mode", choices=("compact", "off"), default="compact")
    args = parser.parse_args()

    text = args.spark_stdout.read_text(encoding="utf-8", errors="replace")
    envelope = parse_envelope(text)
    sections = extract_sections(text)
    capsule = finalize_capsule({
        "schema_version": 1,
        "kind": "aiwf-spark-postflight-capsule",
        "source": artifact_ref(args.spark_stdout),
        "envelope": envelope,
        "sections": sections,
        "section_count": sum(value is not None for value in sections.values()),
        "advisory_only": True,
        "codex_semantic_review_required": True,
        "spark_can_authorize_acceptance": False,
        "transfer_metrics": {"full_spark_output_bytes": len(text.encode("utf-8"))},
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(compact_bytes(capsule) + b"\n")
    if args.stdout_mode == "compact":
        print(compact_bytes(capsule).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
