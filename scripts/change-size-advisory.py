#!/usr/bin/env python3
"""Report disproportionate test growth without blocking a valid change."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict


MAX_UNTRACKED_FILE_BYTES = 4 * 1024 * 1024


def is_control_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    return (
        name.startswith(("CLAUDE_", "TASK_CARD"))
        or normalized.startswith((".ai-workflow/", ".worktrees/"))
    )


def is_test(path: str) -> bool:
    normalized = "/" + path.replace("\\", "/").lower()
    name = Path(path).name.lower()
    return (
        "/tests/" in normalized or "/test/" in normalized or "/__tests__/" in normalized
        or name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.js"))
    )


def changed_line_count(path: Path) -> int | None:
    if path.is_symlink() or not path.is_file():
        return None
    if path.stat().st_size > MAX_UNTRACKED_FILE_BYTES:
        return None
    content = path.read_bytes()
    if b"\0" in content:
        return None
    return content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)


def analyze(worktree: Path, ratio_threshold: float, line_threshold: int) -> Dict[str, Any]:
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--numstat", "HEAD", "--"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git diff --numstat failed")
    totals = {"test": 0, "implementation": 0}
    files = {"test": 0, "implementation": 0}
    counted_paths = set()
    for line in result.stdout.splitlines():
        cells = line.split("\t", 2)
        if len(cells) != 3:
            continue
        added, removed, path = cells
        if not added.isdigit() or not removed.isdigit():
            continue
        if is_control_artifact(path):
            continue
        kind = "test" if is_test(path) else "implementation"
        totals[kind] += int(added) + int(removed)
        files[kind] += 1
        counted_paths.add(path)

    untracked = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True, check=False,
    )
    if untracked.returncode:
        raise ValueError(
            untracked.stderr.decode("utf-8", errors="replace").strip()
            or "git ls-files --others failed"
        )
    untracked_count = 0
    untracked_skipped = 0
    for raw_path in untracked.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if path in counted_paths or is_control_artifact(path):
            continue
        lines = changed_line_count(worktree / path)
        if lines is None:
            untracked_skipped += 1
            continue
        kind = "test" if is_test(path) else "implementation"
        totals[kind] += lines
        files[kind] += 1
        untracked_count += 1
    denominator = max(totals["implementation"], 1)
    ratio = totals["test"] / denominator
    warn = totals["test"] >= line_threshold and ratio > ratio_threshold
    return {
        "schema_version": 1,
        "status": "warning" if warn else "ok",
        "test_changed_lines": totals["test"],
        "implementation_changed_lines": totals["implementation"],
        "test_to_implementation_ratio": round(ratio, 3),
        "test_files": files["test"],
        "implementation_files": files["implementation"],
        "untracked_files_included": untracked_count,
        "untracked_files_skipped": untracked_skipped,
        "ratio_threshold": ratio_threshold,
        "test_line_threshold": line_threshold,
        "recommendations": (
            ["prefer parameterized tests", "reuse the strict source-of-truth fixture/layout"]
            if warn else []
        ),
        "blocking": False,
    }


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ratio-threshold", type=float, default=1.5)
    parser.add_argument("--test-line-threshold", type=int, default=300)
    args = parser.parse_args()
    try:
        value = analyze(args.worktree.resolve(), args.ratio_threshold, args.test_line_threshold)
        atomic_json(args.output.resolve(), value)
    except (OSError, ValueError) as exc:
        print(f"change-size advisory: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
