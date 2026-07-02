#!/usr/bin/env python3
"""
install_for_codex.py  -  Install this Skill into the Codex skills directory.

Usage:
    python scripts/install_for_codex.py

Copies the ai-coding-workflow Skill folder into:
    Windows:  %USERPROFILE%\\.codex\\skills\\ai-coding-workflow
    Unix/macOS: $HOME/.codex/skills/ai-coding-workflow

Excludes: .git, __pycache__, *.pyc, .worktrees, test repos, caches.

Uses only the Python standard library.
"""

import os
import shutil
import sys
from fnmatch import fnmatch

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".worktrees", "node_modules", "task-cards",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
EXCLUDE_FILES = {"*.pyc", ".DS_Store", "Thumbs.db"}
EXCLUDE_NAME_PATTERNS = ["tmp-*", "test-repo", "test_repo"]
EXCLUDE_PATH_PATTERNS = [".cache"]


def get_skill_dir():
    """Return the directory containing this script (scripts/), then go up one level."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_codex_skills_dir():
    """Return the Codex skills directory for the current user."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".codex", "skills")


def should_exclude(name, full_path):
    """Check if a file or directory should be excluded."""
    if name in EXCLUDE_DIRS:
        return True
    for pattern in EXCLUDE_FILES:
        if fnmatch(name, pattern):
            return True
    for pattern in EXCLUDE_NAME_PATTERNS:
        if fnmatch(name, pattern):
            return True
    for pat in EXCLUDE_PATH_PATTERNS:
        if pat in full_path:
            return True
    return False


def copy_skill(src, dest):
    """Copy skill directory, excluding unwanted files."""
    if os.path.exists(dest):
        shutil.rmtree(dest)

    os.makedirs(dest, exist_ok=True)

    for root, dirs, files in os.walk(src):
        # Filter excluded directories (in-place modification)
        dirs[:] = [d for d in dirs if not should_exclude(d, os.path.join(root, d))]

        rel_root = os.path.relpath(root, src)
        dest_root = os.path.join(dest, rel_root) if rel_root != "." else dest

        for f in files:
            src_file = os.path.join(root, f)
            if should_exclude(f, src_file):
                continue
            dest_file = os.path.join(dest_root, f)
            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            shutil.copy2(src_file, dest_file)


def main():
    skill_dir = get_skill_dir()
    codex_skills_dir = get_codex_skills_dir()
    dest = os.path.join(codex_skills_dir, "ai-coding-workflow")

    print(f"Skill source:  {skill_dir}")
    print(f"Install to:    {dest}")

    if not os.path.isdir(os.path.join(skill_dir, "assets")):
        print(f"Error: Skill assets not found in {skill_dir}")
        sys.exit(1)

    os.makedirs(codex_skills_dir, exist_ok=True)
    copy_skill(skill_dir, dest)

    # Count installed files
    file_count = 0
    for _, _, files in os.walk(dest):
        file_count += len(files)

    print(f"\nInstalled {file_count} files to {dest}")
    print("\nTo update, run this script again.")
    print("To verify, check that SKILL.md exists in the target directory.")


if __name__ == "__main__":
    main()
