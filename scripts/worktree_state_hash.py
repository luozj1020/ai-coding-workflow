#!/usr/bin/env python3
"""Canonical worktree-state hash for advisor continuation binding.

Deterministically binds:
- worktree HEAD/base identity (commit hash)
- unstaged tracked diff content
- staged diff content
- allowed untracked file paths and bytes
- binary changes (raw bytes, no lossy text decoding)

Excludes only known workflow control/runtime artifacts:
CLAUDE_PROGRESS.md, CLAUDE_REPORT.md, advisor packet/prompt/result
artifacts, and equivalent dispatcher metadata.

Uses stable POSIX-relative paths and ordering. Works on Windows/Python 3.9.

Usage as module:
    from worktree_state_hash import compute_worktree_state_hash
    h = compute_worktree_state_hash(worktree_path)

Usage as CLI:
    python scripts/worktree_state_hash.py --worktree /path/to/worktree
    python scripts/worktree_state_hash.py --worktree /path/to/worktree --exclude-extra pattern1 pattern2
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# Known workflow control artifacts to exclude from state hash.
# These are dispatch/runtime metadata, not source state.
_CONTROL_ARTIFACTS: FrozenSet[str] = frozenset({
    "CLAUDE_PROGRESS.md",
    "CLAUDE_REPORT.md",
    "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md",
    "TASK_CARD.md",
    "TASK_CARD_FULL.md",
    "ADVISOR_REQUEST.json",
    "advisor-packet.json",
    "advisor-packet.md",
    "advisor-prompt.md",
    "advisor-decision.json",
    "advisor-call-result.json",
    "advisor-response-raw.json",
    "advisor-response-validated.json",
    "advisor-evidence.json",
    "advisor-model-output.json",
    "advisor-model-stderr.txt",
    "advisor-continuation-card.md",
    "advisor-no-resume.json",
    "truncation-manifest.json",
})


def _posix_relpath(path: str, base: str) -> str:
    """Convert an OS path to a POSIX-relative path from base."""
    # On Windows, Path produces backslashes; normalize to forward slash.
    p = Path(path)
    try:
        rel = p.relative_to(base)
    except ValueError:
        # If not relative, just normalize separators
        return path.replace("\\", "/")
    return str(rel).replace("\\", "/")


def _is_control_artifact(posix_path: str) -> bool:
    """Check if a POSIX path (relative) is a known control artifact."""
    # Check the full path and also just the filename
    name = PurePosixPath(posix_path).name
    return name in _CONTROL_ARTIFACTS or posix_path in _CONTROL_ARTIFACTS


def _git_output(args: List[str], cwd: str) -> str:
    """Run a git command and return stdout as string."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout or ""


def _git_binary(args: List[str], cwd: str) -> bytes:
    """Run a git command and return stdout as raw bytes."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        timeout=30,
    )
    return result.stdout or b""


def compute_worktree_state_hash(
    worktree: Path,
    *,
    extra_excludes: Optional[List[str]] = None,
) -> str:
    """Compute a canonical hash of the worktree state.

    The hash deterministically binds:
    - HEAD commit identity
    - unstaged tracked diff (full diff content, not stat)
    - staged diff (full diff content)
    - untracked file paths and bytes (excluding control artifacts)
    - binary changes (raw bytes from diff)

    Returns a SHA-256 hex digest string.
    """
    cwd = str(worktree.resolve())
    hasher = hashlib.sha256()

    # 1. HEAD identity
    head = _git_output(["rev-parse", "HEAD"], cwd).strip()
    hasher.update(f"head:{head}\n".encode("utf-8"))

    # 2. Unstaged tracked diff (full content, includes binary diffs)
    unstaged_diff = _git_binary(["diff", "--binary"], cwd)
    hasher.update(f"unstaged-diff:{len(unstaged_diff)}\n".encode("utf-8"))
    hasher.update(unstaged_diff)

    # 3. Staged diff (full content, includes binary diffs)
    staged_diff = _git_binary(["diff", "--cached", "--binary"], cwd)
    hasher.update(f"staged-diff:{len(staged_diff)}\n".encode("utf-8"))
    hasher.update(staged_diff)

    # 4. Untracked files: paths and bytes
    # Exclude .worktrees/ directory and known control artifacts.
    untracked_raw = _git_output(
        ["ls-files", "--others", "--exclude-standard"], cwd
    )
    untracked_paths: List[str] = []
    excludes = set(_CONTROL_ARTIFACTS)
    if extra_excludes:
        excludes.update(extra_excludes)

    for line in untracked_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip .worktrees directory
        if line.startswith(".worktrees/") or line.startswith(".worktrees\\"):
            continue
        # Normalize to POSIX path
        posix = line.replace("\\", "/")
        # Skip control artifacts
        name = PurePosixPath(posix).name
        if name in excludes:
            continue
        untracked_paths.append(posix)

    # Sort for deterministic ordering
    untracked_paths.sort()

    for upath in untracked_paths:
        hasher.update(f"untracked:{upath}\n".encode("utf-8"))
        # Read actual file bytes (binary-safe)
        full_path = worktree / upath
        if full_path.is_file():
            try:
                content = full_path.read_bytes()
                hasher.update(f"bytes:{len(content)}\n".encode("utf-8"))
                hasher.update(content)
            except OSError:
                hasher.update(b"unreadable\n")
        else:
            hasher.update(b"missing\n")

    return hasher.hexdigest()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worktree", type=Path, required=True,
        help="Path to the git worktree",
    )
    parser.add_argument(
        "--exclude-extra", nargs="*", default=[],
        help="Additional filenames to exclude from state hash",
    )
    args = parser.parse_args(argv)

    if not args.worktree.is_dir():
        print("Error: worktree not found", file=sys.stderr)
        return 1

    h = compute_worktree_state_hash(
        args.worktree,
        extra_excludes=args.exclude_extra if args.exclude_extra else None,
    )
    print(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
