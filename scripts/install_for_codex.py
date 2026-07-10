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

Excludes: .git, __pycache__, *.pyc, .worktrees, .codegraph, local refs,
bootstrap output, test repos, caches.

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
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".codegraph", "ref", "ai",
}
EXCLUDE_FILES = {"*.pyc", ".DS_Store", "Thumbs.db"}
EXCLUDE_ROOT_FILES = {"AGENTS.md", "CLAUDE.md"}
EXCLUDE_NAME_PATTERNS = ["tmp-*", "test-repo", "test_repo"]
EXCLUDE_PATH_PATTERNS = [".cache"]
SKILL_NAME = "ai-coding-workflow"
LSP_TOOL_CHECKS = [
    ("python", "pyright", "pyright"),
    ("node", "typescript-language-server", "typescript-language-server"),
    ("go", "gopls", "gopls"),
    ("rust", "rust-analyzer", "rust-analyzer"),
]


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


def build_bootstrap_command(installed_skill_dir, repo_path, update_workflow_files=False):
    """Return the command that bootstraps *repo_path* using the installed skill."""
    installer = os.path.join(installed_skill_dir, "scripts", "install_workflow.py")
    python_cmd = sys.executable or "python"
    cmd = "{} {} {}".format(
        quote_cmd_arg(python_cmd),
        quote_cmd_arg(installer),
        quote_cmd_arg(repo_path),
    )
    if update_workflow_files:
        cmd += " --update-workflow-files"
    return cmd


def build_context_tools_command(installed_skill_dir):
    """Return the read-only context-tools check command for an installed skill."""
    helper = os.path.join(installed_skill_dir, "scripts", "install_context_tools.py")
    python_cmd = sys.executable or "python"
    return "{} {}".format(quote_cmd_arg(python_cmd), quote_cmd_arg(helper))


def build_code_search_command(installed_skill_dir):
    """Return the optional code-search service helper command."""
    helper = os.path.join(installed_skill_dir, "scripts", "code-search-service.py")
    python_cmd = sys.executable or "python"
    return "{} {}".format(quote_cmd_arg(python_cmd), quote_cmd_arg(helper))


def detect_context_tools(repo_path=None):
    """Return read-only LSP/CodeGraph availability information."""
    lsp = []
    for profile, name, executable in LSP_TOOL_CHECKS:
        lsp.append({
            "profile": profile,
            "name": name,
            "available": shutil.which(executable) is not None,
        })

    codegraph_cli = shutil.which("codegraph") is not None
    codegraph_initialized = None
    if repo_path:
        codegraph_initialized = os.path.isdir(os.path.join(os.path.abspath(repo_path), ".codegraph"))

    return {
        "lsp": lsp,
        "codegraph_cli": codegraph_cli,
        "codegraph_initialized": codegraph_initialized,
    }


def print_context_tool_guidance(installed_skill_dir, repo_path=None):
    """Print read-only guidance for LSP and CodeGraph setup."""
    status = detect_context_tools(repo_path)
    missing_lsp = [item for item in status["lsp"] if not item["available"]]

    print("\nContext intelligence check:")
    print("  LSP tools:")
    for item in status["lsp"]:
        marker = "OK" if item["available"] else "MISSING"
        print("    [{}] {} {}".format(item["profile"], item["name"], marker))

    print("  Read-only check:")
    print("    {}".format(build_context_tools_command(installed_skill_dir)))
    if missing_lsp:
        print("  Suggestion: install only the LSP profile(s) this machine needs.")
        print("  Dry-run install example:")
        print("    {} --apply python".format(build_context_tools_command(installed_skill_dir)))
    else:
        print("  LSP suggestion: all known LSP tools are available.")

    print("  CodeGraph CLI: {}".format("OK" if status["codegraph_cli"] else "MISSING"))
    if not status["codegraph_cli"]:
        print("  Suggestion: install CodeGraph if this repository benefits from indexed code intelligence.")
        print("    codegraph install")

    if repo_path:
        repo_abs = os.path.abspath(repo_path)
        initialized = status["codegraph_initialized"]
        print("  CodeGraph index for {}: {}".format(
            repo_abs,
            "initialized" if initialized else "not initialized",
        ))
        if status["codegraph_cli"] and not initialized:
            print("  Initialize when you want CodeGraph for this repo:")
            print("    cd {}".format(quote_cmd_arg(repo_abs)))
            print("    codegraph init")
    else:
        print("  CodeGraph init: run `codegraph init` inside each target repository when you want it indexed.")


def prompt_yes_no(question, default=False):
    """Ask a yes/no question on an interactive terminal."""
    suffix = " [Y/n] " if default else " [y/N] "
    answer = input(question + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def maybe_offer_code_search_services(installed_skill_dir, mode):
    """Optionally offer Zoekt/Sourcegraph setup after skill installation."""
    helper = os.path.join(installed_skill_dir, "scripts", "code-search-service.py")
    if not os.path.isfile(helper):
        print("\nOptional code-search services: helper not installed; skipping.")
        return

    print("\nOptional code-search services:")
    print("  Zoekt: local indexed search for repeated work in large repositories.")
    print("  Sourcegraph: external/self-hosted service integration; not a default dependency.")
    print("  Readiness check:")
    print("    {} doctor".format(build_code_search_command(installed_skill_dir)))

    if mode == "skip":
        print("  Skipped by --code-search-services=skip.")
        return
    if mode == "check":
        subprocess.run([sys.executable, helper, "doctor"], check=False)
        return
    if mode == "ask" and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("  Non-interactive install; skipping service prompt.")
        print("  Re-run with --code-search-services=check for diagnostics, or run the helper manually.")
        return

    if mode == "ask":
        if not prompt_yes_no("Configure optional code-search services now?", default=False):
            print("  Skipped optional code-search services.")
            return

    subprocess.run([sys.executable, helper, "doctor"], check=False)
    if prompt_yes_no("Install Zoekt CLI tools with `go install` if Go is available?", default=False):
        subprocess.run([sys.executable, helper, "install-zoekt", "--yes"], check=False)
    else:
        print("  Zoekt install skipped.")
    print("  Sourcegraph setup is not started automatically. To inspect the Docker Compose plan:")
    print("    {} sourcegraph-plan".format(build_code_search_command(installed_skill_dir)))


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
            if rel_root == "." and f in EXCLUDE_ROOT_FILES:
                continue
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
    parser.add_argument(
        "--code-search-services",
        choices=["ask", "skip", "check"],
        default="ask",
        help="Ask about optional Zoekt/Sourcegraph services after install, skip, or run a read-only check.",
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
    print("  Command:    {}".format(build_bootstrap_command(installed_skill_dir, repo_abs, update_workflow_files=True)))
    subprocess.run([sys.executable, installer, repo_abs, "--update-workflow-files"], check=True)


def print_next_steps(installed_skill_dir):
    """Print commands that connect skill installation to project bootstrap."""
    installed_installer = os.path.join(installed_skill_dir, "scripts", "install_for_codex.py")
    installed_updater = os.path.join(installed_skill_dir, "scripts", "update_skill.py")
    installed_installer_cmd = "{} {}".format(
        quote_cmd_arg(sys.executable or "python"),
        quote_cmd_arg(installed_installer),
    )
    installed_updater_cmd = "{} {}".format(
        quote_cmd_arg(sys.executable or "python"),
        quote_cmd_arg(installed_updater),
    )
    print("\nConvenient update command:")
    print("  {}".format(installed_updater_cmd))
    print("  {} --bootstrap-current".format(installed_updater_cmd))
    print("")
    print("\nNext step for each target repository:")
    print("  cd <your-repository>")
    print("  {}".format(build_bootstrap_command(installed_skill_dir, ".")))
    print("  To refresh an already bootstrapped repository:")
    print("  {} --update-workflow-files".format(build_bootstrap_command(installed_skill_dir, ".")))
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
        print_context_tool_guidance(dest, bootstrap_repo)
    else:
        print_context_tool_guidance(dest)
    maybe_offer_code_search_services(dest, args.code_search_services)


if __name__ == "__main__":
    main()
