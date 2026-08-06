#!/usr/bin/env python3
"""Render a bounded Claude execution capsule from a full Markdown task card.

The full card remains the audit source.  This helper copies only executable
sections and never summarizes or invents requirements.  ``delta`` mode is for
hash-bound same-lineage continuations and deliberately omits repeated routing,
ownership, and planning policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional


BOOTSTRAP_SECTIONS = {
    "ID", "Task Mode", "Goal", "Scope", "Context", "Claude Context Packet",
    "Builder Contract", "Batch Builder Gate", "Exploratory Builder Contract",
    "Handoff Contract", "Required Changes", "Acceptance Criteria",
    "Spec Gate", "Root Cause Gate", "Test-First / TDD Contract",
    "Post-Implementation Contract", "Required Invariants",
    "Testing Responsibility", "Validation Contract", "Temporary File Policy",
    "Stop Conditions", "Files / Modules", "Required Report",
}

DELTA_SECTIONS = {
    "ID", "Task Mode", "Goal", "Revision Delta", "Dependency Summary",
    "Handoff Contract", "Required Revisions", "Required Changes",
    "Acceptance Criteria", "Testing Responsibility", "Validation Contract",
    "Temporary File Policy", "Stop Conditions", "Required Report",
    "Post-Implementation Contract", "Required Invariants",
}

MAX_CHECKPOINT_BYTES = 32 * 1024


class CapsuleError(RuntimeError):
    pass


def _sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(re.finditer(r"(?m)^##[ \t]+([^\n]+?)\s*$", text))
    preamble = text[: matches[0].start()] if matches else text
    result: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1).strip(), text[match.start():end].rstrip()))
    return preamble.rstrip(), result


def _atomic_write(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _checkpoint(path: Optional[Path]) -> str:
    if path is None:
        return ""
    raw = path.read_bytes()
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise CapsuleError(
            f"rehydration checkpoint exceeds {MAX_CHECKPOINT_BYTES} bytes"
        )
    return raw.decode("utf-8", errors="replace").strip()


def render(text: str, *, mode: str, continuation_kind: str, checkpoint: str) -> str:
    preamble, sections = _sections(text)
    keep = DELTA_SECTIONS if mode == "delta" else BOOTSTRAP_SECTIONS
    selected = [body for name, body in sections if name in keep]
    if not selected:
        raise CapsuleError("task card has no executable sections")
    if not any(name in {"Goal", "Handoff Contract", "Required Changes", "Required Revisions"}
               for name, _ in sections if name in keep):
        raise CapsuleError("capsule lacks Goal/Handoff/Required Changes")

    header = [
        "<!-- AIWF execution capsule v1; TASK_CARD_FULL.md remains the audit source. -->",
        f"<!-- mode={mode}; continuation_kind={continuation_kind} -->",
        "<!-- Execution-only view: executable contract sections only. -->",
        "",
    ]
    if preamble:
        # Keep only the title/non-section identity, never arbitrary long preamble.
        title_lines = [line for line in preamble.splitlines() if line.strip()][:8]
        header.extend(title_lines)
        header.append("")
    body = "\n\n".join(selected)
    if checkpoint:
        body += "\n\n## Accepted Context Checkpoint\n\n" + checkpoint
    return "\n".join(header) + body.rstrip() + "\n"


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--task-card", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--mode", choices=("bootstrap", "delta"), default="bootstrap")
    result.add_argument(
        "--continuation-kind",
        choices=("initial", "next-slice", "revision", "checker-followup"),
        default="initial",
    )
    result.add_argument("--rehydrate-from", type=Path)
    result.add_argument("--receipt", type=Path)
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        source = args.task_card.resolve()
        if not source.is_file():
            raise CapsuleError(f"task card not found: {source}")
        checkpoint = _checkpoint(args.rehydrate_from)
        content = render(
            source.read_text(encoding="utf-8", errors="replace"),
            mode=args.mode,
            continuation_kind=args.continuation_kind,
            checkpoint=checkpoint,
        )
        _atomic_write(args.output, content)
        receipt = {
            "schema": "aiwf-execution-capsule-v1",
            "mode": args.mode,
            "continuation_kind": args.continuation_kind,
            "task_card": str(source),
            "task_card_sha256": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            "checkpoint": str(args.rehydrate_from.resolve()) if args.rehydrate_from else None,
            "output": str(args.output.resolve()),
            "output_sha256": _sha256(content),
            "output_bytes": len(content.encode("utf-8")),
        }
        if args.receipt:
            _atomic_write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (CapsuleError, OSError, UnicodeError) as exc:
        print(f"execution-capsule: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
