#!/usr/bin/env python3
"""
install_for_codex.py  -  Install this Skill into the Codex skills directory.

Usage:
    python scripts/install_for_codex.py
    python scripts/install_for_codex.py --bootstrap-current
    python scripts/install_for_codex.py --bootstrap-repo /path/to/repo

Copies the ai-coding-workflow Skill folder into:
    Windows:  %USERPROFILE%\\.codex\\skills\\ai-coding-workflow
    Unix/macOS: $HOME/.codex/skills/ai-coding-workflow

Excludes: .git, __pycache__, *.pyc, .worktrees, test repos, caches.

Uses only the Python standard library.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from fnmatch import fnmatch

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".worktrees", "node_modules", "task-cards",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
EXCLUDE_FILES = {"*.pyc", ".DS_Store", "Thumbs.db"}
EXCLUDE_NAME_PATTERNS = ["tmp-*", "test-repo", "test_repo"]
EXCLUDE_PATH_PATTERNS = [".cache"]
SKILL_NAME = "ai-coding-workflow"


def get_skill_dir():
    """Return the directory containing this script (scripts/), then go up one level."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_codex_skills_dir():
    """Return the Codex skills directory for the current user."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".codex", "skills")


def paths_equal(left, right):
    """Return True when two paths point at the same filesystem location."""
    left_abs = os.path.abspath(left)
    right_abs = os.path.abspath(right)
    try:
        return os.path.samefile(left_abs, right_abs)
    except OSError:
        return os.path.normcase(left_abs) == os.path.normcase(right_abs)


def quote_cmd_arg(value):
    """Quote a command argument for display."""
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def build_bootstrap_command(installed_skill_dir, repo_path):
    """Return the command that bootstraps *repo_path* using the installed skill."""
    installer = os.path.join(installed_skill_dir, "scripts", "install_workflow.py")
    python_cmd = sys.executable or "python"
    return "{} {} {}".format(
        quote_cmd_arg(python_cmd),
        quote_cmd_arg(installer),
        quote_cmd_arg(repo_path),
    )


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
    if paths_equal(src, dest):
        return
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Install ai-coding-workflow into the Codex skills directory."
    )
    parser.add_argument(
        "--bootstrap-current",
        action="store_true",
        help="After installing the skill, bootstrap the current working directory.",
    )
    parser.add_argument(
        "--bootstrap-repo",
        metavar="PATH",
        help="After installing the skill, bootstrap the given repository path.",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_current and args.bootstrap_repo:
        parser.error("--bootstrap-current and --bootstrap-repo are mutually exclusive")
    return args


def run_bootstrap(installed_skill_dir, repo_path):
    """Run install_workflow.py from the installed skill against *repo_path*."""
    installer = os.path.join(installed_skill_dir, "scripts", "install_workflow.py")
    if not os.path.isfile(installer):
        raise FileNotFoundError("Workflow installer not found: {}".format(installer))
    repo_abs = os.path.abspath(repo_path)
    print("\nBootstrapping repository workflow:")
    print("  Repository: {}".format(repo_abs))
    print("  Command:    {}".format(build_bootstrap_command(installed_skill_dir, repo_abs)))
    subprocess.run([sys.executable, installer, repo_abs], check=True)


def print_next_steps(installed_skill_dir):
    """Print commands that connect skill installation to project bootstrap."""
    installed_installer = os.path.join(installed_skill_dir, "scripts", "install_for_codex.py")
    installed_installer_cmd = "{} {}".format(
        quote_cmd_arg(sys.executable or "python"),
        quote_cmd_arg(installed_installer),
    )
    print("\nNext step for each target repository:")
    print("  cd <your-repository>")
    print("  {}".format(build_bootstrap_command(installed_skill_dir, ".")))
    print("")
    print("Shortcut when your shell is already in the target repository:")
    print("  {} --bootstrap-current".format(installed_installer_cmd))
    print("")
    print("Shortcut for a specific repository:")
    print("  {} --bootstrap-repo <path-to-repository>".format(installed_installer_cmd))
    print("")
    print("If dispatch reports that ai/dispatch-to-claude.sh is missing, run the bootstrap command above first.")


def main(argv=None):
    args = parse_args(argv)
    skill_dir = get_skill_dir()
    codex_skills_dir = get_codex_skills_dir()
    dest = os.path.join(codex_skills_dir, SKILL_NAME)

    print(f"Skill source:  {skill_dir}")
    print(f"Install to:    {dest}")

    if not os.path.isdir(os.path.join(skill_dir, "assets")):
        print(f"Error: Skill assets not found in {skill_dir}")
        sys.exit(1)

    os.makedirs(codex_skills_dir, exist_ok=True)
    if paths_equal(skill_dir, dest):
        print("\nSkill source is already the Codex install directory; skipping copy.")
    else:
        copy_skill(skill_dir, dest)

    # Count installed files
    file_count = 0
    for _, _, files in os.walk(dest):
        file_count += len(files)

    print(f"\nInstalled {file_count} files to {dest}")
    print("\nTo update, run this script again.")
    print("To verify, check that SKILL.md exists in the target directory.")
    print_next_steps(dest)

    bootstrap_repo = None
    if args.bootstrap_current:
        bootstrap_repo = os.getcwd()
    elif args.bootstrap_repo:
        bootstrap_repo = args.bootstrap_repo
    if bootstrap_repo:
        run_bootstrap(dest, bootstrap_repo)


if __name__ == "__main__":
    main()
