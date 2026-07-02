#!/usr/bin/env python3
"""
clean_runtime.py  -  Preview and remove ignored runtime artifacts from a repository.

Usage:
    python scripts/clean_runtime.py [repo-path]           # dry-run: list candidates
    python scripts/clean_runtime.py [repo-path] --apply    # delete candidates

Targets only runtime artifacts that are ignored by git:
    - .worktrees/* except .gitkeep
    - root tmp-* directories/files
    - stale task-cards/ directory (if ignored by .gitignore)

Never deletes tracked files. Uses only the Python standard library.
"""

import argparse
import os
import shutil
import subprocess
import sys


def _find_repo_root(start):
    """Walk upward from *start* until a directory containing .git is found."""
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(
            os.path.join(cur, ".git")
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _is_git_ignored(repo_root, path):
    """Check if a path is ignored by git. Returns True if ignored."""
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode == 0
    except FileNotFoundError:
        # git not available; fall back to not ignoring
        return False


def _is_tracked(repo_root, path):
    """Check whether a file or any file under a directory is tracked by git."""
    try:
        rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
        if os.path.isdir(path):
            r = subprocess.run(
                ["git", "ls-files", "--", rel.rstrip("/") + "/"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return bool(r.stdout.strip())
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _process_running(pid_file):
    """Return True when a PID artifact points to a live process we can detect."""
    try:
        with open(pid_file, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return False
        pid = int(raw)
    except (OSError, ValueError):
        return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _active_worktree_prefixes(worktrees_dir):
    """Return claude-* prefixes whose PID artifact still appears active."""
    active = set()
    if not os.path.isdir(worktrees_dir):
        return active
    for entry in os.listdir(worktrees_dir):
        if not entry.startswith("claude-") or not entry.endswith(".pid"):
            continue
        pid_file = os.path.join(worktrees_dir, entry)
        if _process_running(pid_file):
            active.add(entry[:-4])
    return active


def collect_candidates(repo_root):
    """Collect runtime artifact candidates for cleanup.

    Returns list of (path, description) tuples.
    Only includes paths that are git-ignored and not tracked.
    """
    candidates = []

    # 1. .worktrees/* except .gitkeep
    worktrees_dir = os.path.join(repo_root, ".worktrees")
    if os.path.isdir(worktrees_dir):
        active_prefixes = _active_worktree_prefixes(worktrees_dir)
        for entry in sorted(os.listdir(worktrees_dir)):
            if entry == ".gitkeep":
                continue
            if any(entry == prefix or entry.startswith(prefix + ".") for prefix in active_prefixes):
                continue
            full = os.path.join(worktrees_dir, entry)
            if _is_git_ignored(repo_root, full) and not _is_tracked(repo_root, full):
                candidates.append((full, ".worktrees/{}".format(entry)))

    # 2. root tmp-*
    for entry in sorted(os.listdir(repo_root)):
        if not entry.startswith("tmp-"):
            continue
        full = os.path.join(repo_root, entry)
        if _is_git_ignored(repo_root, full) and not _is_tracked(repo_root, full):
            candidates.append((full, entry))

    # 3. stale task-cards/ if ignored
    task_cards_dir = os.path.join(repo_root, "task-cards")
    if os.path.isdir(task_cards_dir) and _is_git_ignored(
        repo_root, task_cards_dir
    ):
        if not _is_tracked(repo_root, task_cards_dir):
            candidates.append((task_cards_dir, "task-cards/"))

    return candidates


def remove_path(path):
    """Remove a file or directory tree."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def main():
    parser = argparse.ArgumentParser(
        description="Preview and remove ignored runtime artifacts."
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="Repository path (default: current directory)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete candidates (default is dry-run)",
    )
    args = parser.parse_args()

    repo_root = _find_repo_root(args.repo)
    if repo_root is None:
        print("ERROR: No .git found from {}".format(os.path.abspath(args.repo)))
        sys.exit(1)

    candidates = collect_candidates(repo_root)

    if not candidates:
        print("No runtime artifacts found.")
        sys.exit(0)

    if args.apply:
        print("Removing {} runtime artifact(s):".format(len(candidates)))
        for path, desc in candidates:
            try:
                remove_path(path)
                print("  removed: {}".format(desc))
            except OSError as e:
                print("  FAILED: {} ({})".format(desc, e))
    else:
        print("Dry-run: {} runtime artifact(s) would be removed:".format(len(candidates)))
        for _, desc in candidates:
            print("  {}".format(desc))
        print("\nRun with --apply to delete.")


if __name__ == "__main__":
    main()
