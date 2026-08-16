#!/usr/bin/env python3
"""Validate and normalize one Claude workflow runtime identifier."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Sequence


TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ARTIFACT_SUFFIXES = (
    ".monitor-events.log",
    ".dispatcher.process.json",
    ".claude.process.json",
    ".checker.process.json",
    ".dispatcher.pid",
    ".progress.log",
    ".claude-progress.md",
    ".runtime.json",
    ".outcome.json",
    ".status.txt",
    ".claude.pid",
    ".checker.pid",
    ".pid",
)


def normalize_task_id(value: str, *, artifact_input: bool = False) -> str:
    """Return the canonical safe ID, optionally extracting it from an artifact path."""
    if not isinstance(value, str) or not value:
        raise ValueError("runtime task id must be non-empty")
    name = Path(value).name if artifact_input else value
    if artifact_input:
        for suffix in ARTIFACT_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
    if name in {"", ".", ".."} or not TASK_ID_PATTERN.fullmatch(name):
        raise ValueError("runtime task id contains unsafe or ambiguous characters")
    return name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and normalize a Claude workflow runtime ID."
    )
    parser.add_argument("action", choices=("normalize",))
    parser.add_argument("value")
    parser.add_argument(
        "--artifact-input",
        action="store_true",
        help="Accept an artifact path and strip one known runtime suffix.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        value = normalize_task_id(args.value, artifact_input=args.artifact_input)
    except ValueError as exc:
        build_parser().error(str(exc))
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
