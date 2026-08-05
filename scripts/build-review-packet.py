#!/usr/bin/env python3
"""build-review-packet.py — CLI wrapper for build_review_packet module.

Delegates all implementation to build_review_packet (underscore module).
Usage:
    python scripts/build-review-packet.py <run_dir> [--output FILE] [--supplemental FILE ...]
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import List

# Ensure the scripts directory is importable
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from build_review_packet import (  # noqa: E402
    DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES,
    DEFAULT_MAX_DIFF_HUNKS,
    DEFAULT_MAX_LOG_TAIL_LINES,
    DEFAULT_MAX_PROMPT_BYTES,
    build_review_packet,
    render_review_prompt,
)
from evidence_capsule import (  # noqa: E402
    artifact_ref,
    bounded_selector,
    compact_bytes,
    finalize_capsule,
    repository_head,
    review_compression_route,
    spark_tool_request,
)


def compact_receipt(
    packet: dict,
    output_path: Path,
    prompt_path: Path | None,
    task_card: Path | None,
    diff_file: Path | None,
    run_dir: Path,
) -> dict:
    compression = dict(packet.get("compression_route", {}))
    if compression.get("spark_recommended") and task_card is not None:
        compression["tool_request"] = spark_tool_request(task_card, output_path)
    changed_paths = [
        item.get("path") for item in packet.get("changed_files", [])
        if isinstance(item, dict) and item.get("path")
    ]
    focus = packet.get("diff_focus", {})
    return {
        "schema_version": 1,
        "kind": "aiwf-review-packet-capsule",
        "review_packet_path": str(output_path.resolve()),
        "review_prompt_path": str(prompt_path.resolve()) if prompt_path else None,
        "evidence": artifact_ref(output_path),
        "task_card": artifact_ref(task_card),
        "diff": artifact_ref(diff_file),
        "repository_head": repository_head(run_dir),
        "legacy_full_prompt_bytes": packet.get("prompt_bytes", 0),
        "diff_hunk_count": len(packet.get("diff_hunks", [])),
        "changed_file_count": len(packet.get("changed_files", [])),
        "changed_files": bounded_selector(changed_paths),
        "changed_symbols": bounded_selector(focus.get("symbols", [])),
        "risk_labels": bounded_selector(sorted(focus.get("risk_hits", {}))),
        "omitted_evidence_count": len(packet.get("omitted_evidence", [])),
        "failure_signal_count": len(packet.get("failures", [])),
        "compression_route": compression,
        "transfer_metrics": {
            "full_evidence_bytes": output_path.stat().st_size,
        },
    }


def render_tool_review_prompt(capsule_path: Path) -> str:
    """Render a small tool-first prompt; evidence bodies stay out of stdin."""
    return "\n".join([
        "# Tool-backed final review",
        "",
        "You are the bounded semantic reviewer. Do not implement or merge.",
        f"Read this compact capsule first: {capsule_path.resolve()}",
        "First run: python ai/verify-evidence-capsule.py "
        + shlex.quote(str(capsule_path.resolve())),
        "Do not rely on task-card, diff, evidence, or repository-HEAD bindings unless that receipt is valid.",
        "Do not cat the full packet, diff, logs, or prior prompt. Start from the bounded changed-file and symbol selectors.",
        "Use targeted git diff/path reads only for semantic hotspots, failed gates, conflicts, or selected acceptance IDs.",
        "A Spark postflight capsule, when appended below, is advisory only; verify its cited evidence selectively.",
        "Return exactly one JSON object with schema_version=1; decision=accept|revise|split|reject;",
        "scope=phase|whole-task; non-empty reasoning; direction.status; acceptance; validation; next_task; lessons.",
        "Missing/stale hashes, contradictory evidence, or unreadable selected evidence is needs-review, never acceptance.",
        "",
    ])


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a bounded review packet from run artifacts."
    )
    parser.add_argument("run_dir", help="Run directory containing artifacts.")
    parser.add_argument("--output", help="Output JSON file path. Defaults to <run_dir>/review-packet.json.")
    parser.add_argument("--prompt-output", help="Write rendered prompt to this file.")
    parser.add_argument("--capsule-output", help="Write the compact review capsule to this file.")
    parser.add_argument(
        "--prompt-mode", choices=("capsule", "full"), default="capsule",
        help="capsule sends only a tool-first evidence reference; full preserves the legacy prompt.",
    )
    parser.add_argument("--max-prompt-bytes", type=int, default=DEFAULT_MAX_PROMPT_BYTES)
    parser.add_argument("--max-diff-hunks", type=int, default=DEFAULT_MAX_DIFF_HUNKS)
    parser.add_argument("--max-log-tail-lines", type=int, default=DEFAULT_MAX_LOG_TAIL_LINES)
    parser.add_argument("--max-artifact-summary-bytes", type=int, default=DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES)
    parser.add_argument("--supplemental", nargs="*", help="Additional artifact paths to include.")
    parser.add_argument("--task-card", help="Exact task card; prevents cross-task artifact selection.")
    parser.add_argument("--diff-file", help="Exact diff; prevents cross-task artifact selection.")
    parser.add_argument(
        "--stdout-mode",
        choices=("compact", "legacy", "off"),
        default="compact",
        help="Default compact prints one machine-readable receipt; legacy preserves prior lines.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        return 1

    supplemental = [Path(p) for p in args.supplemental] if args.supplemental else None
    task_card = Path(args.task_card) if args.task_card else None
    diff_file = Path(args.diff_file) if args.diff_file else None

    packet = build_review_packet(
        run_dir,
        max_prompt_bytes=args.max_prompt_bytes,
        max_diff_hunks=args.max_diff_hunks,
        max_log_tail_lines=args.max_log_tail_lines,
        max_artifact_summary_bytes=args.max_artifact_summary_bytes,
        supplemental_files=supplemental,
        task_card=task_card,
        diff_file=diff_file,
    )

    # Write packet JSON
    output_path = Path(args.output) if args.output else run_dir / "review-packet.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    full_bytes = output_path.stat().st_size
    packet["compression_route"] = review_compression_route(packet, full_bytes)
    output_path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    capsule_path = (
        Path(args.capsule_output) if args.capsule_output
        else output_path.with_name(f"{output_path.stem}-capsule.json")
    )
    prompt_path = Path(args.prompt_output) if args.prompt_output else None
    capsule = compact_receipt(
        packet, output_path, prompt_path, task_card, diff_file, run_dir,
    )

    if prompt_path and args.prompt_mode == "capsule":
        prompt = render_tool_review_prompt(capsule_path)
        capsule["transfer_metrics"]["codex_input_bytes"] = len(prompt.encode("utf-8"))
    capsule = finalize_capsule(capsule)
    capsule_path.parent.mkdir(parents=True, exist_ok=True)
    capsule_path.write_bytes(compact_bytes(capsule) + b"\n")

    # Optionally write rendered prompt
    if prompt_path:
        if args.prompt_mode == "full":
            prompt = render_review_prompt(packet, args.max_prompt_bytes)
        else:
            prompt = render_tool_review_prompt(capsule_path)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
    if args.stdout_mode == "compact":
        print(json.dumps(
            capsule,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ))
    elif args.stdout_mode == "legacy":
        if prompt_path:
            print(f"Review prompt: {prompt_path}")
        print(f"Review packet: {output_path}")
        print(f"Prompt bytes: {packet['prompt_bytes']}")
        print(f"Diff hunks: {len(packet['diff_hunks'])}")
        print(f"Changed files: {len(packet['changed_files'])}")
        print(f"Omitted evidence: {len(packet['omitted_evidence'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
