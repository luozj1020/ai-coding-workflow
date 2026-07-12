#!/usr/bin/env python3
"""build-review-packet.py — CLI wrapper for build_review_packet module.

Delegates all implementation to build_review_packet (underscore module).
Usage:
    python scripts/build-review-packet.py <run_dir> [--output FILE] [--supplemental FILE ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

# Ensure the scripts directory is importable
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from build_review_packet import (
    DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES,
    DEFAULT_MAX_DIFF_HUNKS,
    DEFAULT_MAX_LOG_TAIL_LINES,
    DEFAULT_MAX_PROMPT_BYTES,
    build_review_packet,
    render_review_prompt,
)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a bounded review packet from run artifacts."
    )
    parser.add_argument("run_dir", help="Run directory containing artifacts.")
    parser.add_argument("--output", help="Output JSON file path. Defaults to <run_dir>/review-packet.json.")
    parser.add_argument("--prompt-output", help="Write rendered prompt to this file.")
    parser.add_argument("--max-prompt-bytes", type=int, default=DEFAULT_MAX_PROMPT_BYTES)
    parser.add_argument("--max-diff-hunks", type=int, default=DEFAULT_MAX_DIFF_HUNKS)
    parser.add_argument("--max-log-tail-lines", type=int, default=DEFAULT_MAX_LOG_TAIL_LINES)
    parser.add_argument("--max-artifact-summary-bytes", type=int, default=DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES)
    parser.add_argument("--supplemental", nargs="*", help="Additional artifact paths to include.")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        return 1

    supplemental = [Path(p) for p in args.supplemental] if args.supplemental else None

    packet = build_review_packet(
        run_dir,
        max_prompt_bytes=args.max_prompt_bytes,
        max_diff_hunks=args.max_diff_hunks,
        max_log_tail_lines=args.max_log_tail_lines,
        max_artifact_summary_bytes=args.max_artifact_summary_bytes,
        supplemental_files=supplemental,
    )

    # Write packet JSON
    output_path = Path(args.output) if args.output else run_dir / "review-packet.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Optionally write rendered prompt
    if args.prompt_output:
        prompt = render_review_prompt(packet, args.max_prompt_bytes)
        prompt_path = Path(args.prompt_output)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"Review prompt: {prompt_path}")

    print(f"Review packet: {output_path}")
    print(f"Prompt bytes: {packet['prompt_bytes']}")
    print(f"Diff hunks: {len(packet['diff_hunks'])}")
    print(f"Changed files: {len(packet['changed_files'])}")
    print(f"Omitted evidence: {len(packet['omitted_evidence'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
