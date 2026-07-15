#!/usr/bin/env python3
"""Collect deterministic repository-scale facts for execution ownership routing."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
    ".cs", ".rb", ".php", ".swift", ".kt", ".kts", ".scala", ".sh",
    ".bash", ".ps1", ".sql", ".proto", ".bzl",
}
SCALE_ORDER = ("small", "medium", "large", "giant")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def classify_scale(tracked_files: int, source_files: int) -> str:
    if tracked_files <= 3000 and source_files <= 2000:
        return "small"
    if tracked_files <= 10000 and source_files <= 7000:
        return "medium"
    if tracked_files <= 50000 and source_files <= 35000:
        return "large"
    return "giant"


def thresholds(scale: str) -> Dict[str, int]:
    return {
        "small": {"ordinary_lines": 100, "ordinary_files": 2, "concentrated_lines": 100, "concentrated_files": 2},
        "medium": {"ordinary_lines": 100, "ordinary_files": 2, "concentrated_lines": 250, "concentrated_files": 3},
        "large": {"ordinary_lines": 150, "ordinary_files": 3, "concentrated_lines": 500, "concentrated_files": 5},
        "giant": {"ordinary_lines": 200, "ordinary_files": 3, "concentrated_lines": 500, "concentrated_files": 5},
    }[scale]


def _recent_runtime_paths(root: Path, limit: int = 200) -> List[Path]:
    if not root.is_dir():
        return []
    entries = []
    try:
        for entry in os.scandir(root):
            if entry.is_file(follow_symlinks=False) and entry.name.endswith(".runtime.json"):
                try:
                    entries.append((entry.stat().st_mtime, Path(entry.path)))
                except OSError:
                    continue
    except OSError:
        return []
    entries.sort(reverse=True)
    return [path for _, path in entries[:limit]]


def _worktree_durations(root: Path) -> List[float]:
    values: List[float] = []
    for path in _recent_runtime_paths(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("worktree_setup_seconds")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                values.append(float(value))
        except (OSError, json.JSONDecodeError):
            continue
    return values


def _git_size_kib(repo: Path) -> Optional[int]:
    try:
        text = _git(repo, "count-objects", "-v")
    except RuntimeError:
        return None
    fields: Dict[str, int] = {}
    for line in text.splitlines():
        key, sep, raw = line.partition(":")
        if sep and raw.strip().isdigit():
            fields[key.strip()] = int(raw.strip())
    if not fields:
        return None
    return fields.get("size", 0) + fields.get("size-pack", 0)


def collect(repo: Path, scale_override: str = "auto") -> Dict[str, object]:
    repo = Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    paths = [line for line in _git(repo, "ls-files").splitlines() if line]
    tracked = len(paths)
    source = sum(1 for path in paths if Path(path).suffix.lower() in SOURCE_SUFFIXES)
    detected = classify_scale(tracked, source)
    durations = _worktree_durations(repo / ".worktrees")
    median = statistics.median(durations) if durations else None
    worktree_cost = "unknown" if median is None else "high" if median >= 120 else "medium" if median >= 30 else "low"
    effective = detected if scale_override == "auto" else scale_override
    io_promoted = False
    if scale_override == "auto" and worktree_cost == "high" and effective != "giant":
        effective = SCALE_ORDER[SCALE_ORDER.index(effective) + 1]
        io_promoted = True
    result: Dict[str, object] = {
        "schema_version": 1,
        "repository_scale_detected": detected,
        "routing_scale": effective,
        "scale_override": scale_override,
        "tracked_files": tracked,
        "source_files": source,
        "git_size_kib": _git_size_kib(repo),
        "worktree_history_samples": len(durations),
        "worktree_setup_median_seconds": median,
        "worktree_cost": worktree_cost,
        "io_promoted": io_promoted,
    }
    result.update(thresholds(effective))
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--scale", choices=("auto",) + SCALE_ORDER, default="auto")
    parser.add_argument("--format", choices=("json", "shell"), default="json")
    args = parser.parse_args(argv)
    try:
        result = collect(args.repo, args.scale)
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key in (
            "repository_scale_detected", "routing_scale", "tracked_files", "source_files",
            "git_size_kib", "worktree_history_samples", "worktree_setup_median_seconds",
            "worktree_cost", "io_promoted", "ordinary_lines", "ordinary_files",
            "concentrated_lines", "concentrated_files",
        ):
            value = result.get(key)
            if value is None:
                value = "unknown"
            elif isinstance(value, bool):
                value = "yes" if value else "no"
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
