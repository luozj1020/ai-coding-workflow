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
    python scripts/worktree_state_hash.py --worktree /path/to/worktree --ignore-empty-untracked
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

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


def _normalize_relative(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


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
    """Return true only for dispatcher-owned controls at worktree root.

    A product is allowed to contain a same-named file in a subdirectory.  The
    dispatcher controls are always created at the execution worktree root, so
    basename-wide exclusion would hide real product changes.
    """
    normalized = _normalize_relative(posix_path)
    return "/" not in normalized and normalized in _CONTROL_ARTIFACTS


def _git_output(args: List[str], cwd: str) -> str:
    """Run a git command and return stdout as string."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit {result.returncode}: "
            f"{(result.stderr or '').strip()}"
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
    if result.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit {result.returncode}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout or b""


def _nul_paths(raw: bytes) -> List[str]:
    return [
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in raw.split(b"\0")
        if item
    ]


def _is_extra_excluded(posix_path: str, excludes: Set[str]) -> bool:
    normalized = _normalize_relative(posix_path)
    return normalized in excludes or (
        "/" not in normalized and PurePosixPath(normalized).name in excludes
    )


def collect_worktree_state(
    worktree: Path,
    *,
    extra_excludes: Optional[List[str]] = None,
    ignore_empty_untracked: bool = False,
) -> Dict[str, Any]:
    """Return one canonical product/control snapshot for runtime decisions."""
    cwd = str(worktree.resolve())
    excludes = {_normalize_relative(item) for item in (extra_excludes or [])}

    unstaged_paths = _nul_paths(_git_binary(["diff", "--name-only", "-z"], cwd))
    staged_paths = _nul_paths(
        _git_binary(["diff", "--cached", "--name-only", "-z"], cwd)
    )
    untracked_paths = _nul_paths(
        _git_binary(["ls-files", "--others", "--exclude-standard", "-z"], cwd)
    )

    product_paths: Set[str] = set()
    control_paths: Set[str] = set()
    usable_untracked: Set[str] = set()
    for path in sorted(set(unstaged_paths + staged_paths + untracked_paths)):
        normalized = _normalize_relative(path)
        if not normalized or normalized == ".worktrees" or normalized.startswith(".worktrees/"):
            continue
        if _is_control_artifact(normalized) or _is_extra_excluded(normalized, excludes):
            control_paths.add(normalized)
            continue
        full_path = worktree / normalized
        if normalized in untracked_paths and ignore_empty_untracked:
            try:
                if full_path.is_file() and not full_path.is_symlink() and full_path.stat().st_size == 0:
                    continue
            except OSError:
                pass
        product_paths.add(normalized)
        if normalized in untracked_paths:
            usable_untracked.add(normalized)

    ordered_product = sorted(product_paths)
    ordered_control = sorted(control_paths)
    # Hash the diff with a fixed-size exclusion pathspec.  Passing every
    # changed product path would exceed the OS argument limit in large edits.
    # Top/literal exclusions affect only the exact dispatcher-owned root files.
    diff_excludes = sorted(set(_CONTROL_ARTIFACTS) | excludes)
    product_pathspec = ["."] + [
        f":(top,exclude,literal){path}" for path in diff_excludes
    ]
    unstaged_diff = _git_binary(
        ["diff", "--binary", "--", *product_pathspec], cwd
    )
    staged_diff = _git_binary(
        ["diff", "--cached", "--binary", "--", *product_pathspec], cwd
    )

    head = _git_output(["rev-parse", "HEAD"], cwd).strip()
    hasher = hashlib.sha256()
    hasher.update(f"head:{head}\n".encode("utf-8"))
    hasher.update(f"unstaged-diff:{len(unstaged_diff)}\n".encode("utf-8"))
    hasher.update(unstaged_diff)
    hasher.update(f"staged-diff:{len(staged_diff)}\n".encode("utf-8"))
    hasher.update(staged_diff)
    product_path_hashes: Dict[str, str] = {}
    for path in sorted(usable_untracked):
        hasher.update(f"untracked:{path}\n".encode("utf-8", errors="surrogateescape"))
        full_path = worktree / path
        if full_path.is_file():
            try:
                content = full_path.read_bytes()
                hasher.update(f"bytes:{len(content)}\n".encode("utf-8"))
                hasher.update(content)
            except OSError:
                hasher.update(b"unreadable\n")
        else:
            hasher.update(b"missing\n")

    for path in ordered_product:
        path_hasher = hashlib.sha256()
        path_hasher.update(f"path:{path}\n".encode("utf-8", errors="surrogateescape"))
        path_hasher.update(f"unstaged:{path in unstaged_paths}\n".encode("ascii"))
        path_hasher.update(f"staged:{path in staged_paths}\n".encode("ascii"))
        path_hasher.update(f"untracked:{path in usable_untracked}\n".encode("ascii"))
        full_path = worktree / path
        try:
            stat_result = full_path.lstat()
            path_hasher.update(f"mode:{stat_result.st_mode:o}\n".encode("ascii"))
            if full_path.is_symlink():
                path_hasher.update(os.readlink(full_path).encode("utf-8", errors="surrogateescape"))
            elif full_path.is_file():
                path_hasher.update(full_path.read_bytes())
            else:
                path_hasher.update(b"non-file")
        except OSError:
            path_hasher.update(b"missing")
        product_path_hashes[path] = path_hasher.hexdigest()

    return {
        "schema_version": 1,
        "status": "ready",
        "head": head,
        "product_hash": hasher.hexdigest(),
        "product_change_count": len(ordered_product),
        "product_changed_paths": ordered_product,
        "product_path_hashes": product_path_hashes,
        "control_change_count": len(ordered_control),
        "control_changed_paths": ordered_control,
        "ignore_empty_untracked": ignore_empty_untracked,
    }


def compute_worktree_state_hash(
    worktree: Path,
    *,
    extra_excludes: Optional[List[str]] = None,
    ignore_empty_untracked: bool = False,
) -> str:
    """Compute a canonical hash of the worktree state.

    The hash deterministically binds:
    - HEAD commit identity
    - unstaged tracked diff (full diff content, not stat)
    - staged diff (full diff content)
    - untracked file paths and bytes (excluding control artifacts, and
      optionally zero-byte placeholders)
    - binary changes (raw bytes from diff)

    Returns a SHA-256 hex digest string.
    """
    return str(collect_worktree_state(
        worktree,
        extra_excludes=extra_excludes,
        ignore_empty_untracked=ignore_empty_untracked,
    )["product_hash"])


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


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
    parser.add_argument(
        "--ignore-empty-untracked", action="store_true",
        help=(
            "Ignore zero-byte untracked placeholders. Intended for progress "
            "detection only; continuation and ownership bindings should keep "
            "the default strict behavior."
        ),
    )
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument("--json", action="store_true", help="Emit the canonical snapshot as JSON")
    output_mode.add_argument("--count", action="store_true", help="Emit only the product change count")
    parser.add_argument("--baseline-hash", help="Record whether the product hash differs from this baseline")
    parser.add_argument("--baseline-state", type=Path, help="Compare product paths with a prior canonical snapshot")
    parser.add_argument("--output", type=Path, help="Atomically write JSON output to this path")
    args = parser.parse_args(argv)

    if not args.worktree.is_dir():
        print("Error: worktree not found", file=sys.stderr)
        return 1

    try:
        value = collect_worktree_state(
            args.worktree,
            extra_excludes=args.exclude_extra if args.exclude_extra else None,
            ignore_empty_untracked=args.ignore_empty_untracked,
        )
        if args.baseline_hash is not None:
            value["baseline_hash"] = args.baseline_hash
            value["product_delta_from_baseline"] = value["product_hash"] != args.baseline_hash
        if args.baseline_state is not None:
            baseline = json.loads(args.baseline_state.read_text(encoding="utf-8"))
            baseline_hash = str(baseline.get("product_hash") or baseline.get("content_digest") or "")
            baseline_paths = baseline.get("product_path_hashes", {})
            if not isinstance(baseline_paths, dict) or not baseline_hash:
                raise RuntimeError("baseline state is missing canonical product hashes")
            current_paths = value.get("product_path_hashes", {})
            incremental_paths = sorted(
                path for path in set(baseline_paths) | set(current_paths)
                if baseline_paths.get(path) != current_paths.get(path)
            )
            value["baseline_hash"] = baseline_hash
            value["product_delta_from_baseline"] = value["product_hash"] != baseline_hash
            value["incremental_product_change_count"] = len(incremental_paths)
            value["incremental_product_changed_paths"] = incremental_paths
        if args.output:
            _atomic_json(args.output, value)
        if args.count:
            print(value.get("incremental_product_change_count", value["product_change_count"]))
        elif args.json or args.output:
            if not args.output:
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        else:
            print(value["product_hash"])
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
