#!/usr/bin/env python3
"""
install_context_tools.py  -  Check and optionally install context tools for
                              LSP, linting, and code intelligence.

Usage:
    python ai/install_context_tools.py                          # read-only check
    python ai/install_context_tools.py --apply PROFILE          # show planned commands (dry-run)
    python ai/install_context_tools.py --apply PROFILE --manager MANAGER  # dry-run for one manager
    python ai/install_context_tools.py --apply PROFILE --manager MANAGER --yes  # actually run

Default invocation is read-only: checks which context tools are available on
PATH and reports status.

With --apply PROFILE, prints the commands that would install missing tools for
that profile, but does NOT run them. Add --manager MANAGER to limit to one
package manager. Add --yes to actually execute.

Installing binaries does NOT automatically expose Codex LSP/codegraph tools.
The Codex agent must be configured separately to use them.

Uses only the Python standard library.
"""

import argparse
import shutil
import subprocess
import sys

# ---- Suggestion table ----
# Each profile maps to a list of tool entries.
# Each entry has: name, check command, and commands per package manager.
# Commands are only suggestions; the script never invents new ones.

SUGGESTIONS = {
    "python": [
        {
            "name": "pyright",
            "check": ["pyright", "--version"],
            "commands": {
                "npm": ["npm", "install", "-g", "pyright"],
                "pip": ["pip", "install", "pyright"],
            },
        },
        {
            "name": "ruff",
            "check": ["ruff", "--version"],
            "commands": {
                "pip": ["pip", "install", "ruff"],
                "cargo": ["cargo", "install", "ruff"],
                "brew": ["brew", "install", "ruff"],
                "choco": ["choco", "install", "ruff"],
                "scoop": ["scoop", "install", "ruff"],
            },
        },
        {
            "name": "mypy",
            "check": ["mypy", "--version"],
            "commands": {
                "pip": ["pip", "install", "mypy"],
                "brew": ["brew", "install", "mypy"],
                "apt": ["apt", "install", "-y", "mypy"],
            },
        },
    ],
    "node": [
        {
            "name": "typescript-language-server",
            "check": ["typescript-language-server", "--version"],
            "commands": {
                "npm": ["npm", "install", "-g", "typescript-language-server", "typescript"],
            },
        },
        {
            "name": "eslint",
            "check": ["eslint", "--version"],
            "commands": {
                "npm": ["npm", "install", "-g", "eslint"],
            },
        },
    ],
    "go": [
        {
            "name": "gopls",
            "check": ["gopls", "version"],
            "commands": {
                "go": ["go", "install", "golang.org/x/tools/gopls@latest"],
            },
        },
    ],
    "rust": [
        {
            "name": "rust-analyzer",
            "check": ["rust-analyzer", "--version"],
            "commands": {
                "rustup": ["rustup", "component", "add", "rust-analyzer"],
                "cargo": ["cargo", "install", "rust-analyzer"],
            },
        },
    ],
}

ALL_MANAGERS = sorted({m for profile in SUGGESTIONS.values() for tool in profile for m in tool["commands"]})


def _resolve_command(cmd):
    """Resolve command[0] through PATH, including PATHEXT on Windows."""
    executable = shutil.which(cmd[0])
    if executable is None:
        return None
    return [executable] + cmd[1:]


def _is_available(check_cmd):
    """Return True if check_cmd succeeds (exit 0)."""
    run_cmd = _resolve_command(check_cmd)
    if run_cmd is None:
        return False
    try:
        r = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _run_command(cmd):
    """Run a command, printing it first. Returns (success, output)."""
    print("  $ " + " ".join(cmd))
    run_cmd = _resolve_command(cmd)
    if run_cmd is None:
        print("    error: command not found")
        return False, "command not found"
    try:
        r = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            return True, r.stdout.strip()
        else:
            stderr = r.stderr.strip()
            if stderr:
                print("    stderr: " + stderr)
            return False, stderr
    except FileNotFoundError:
        print("    error: command not found")
        return False, "command not found"
    except OSError as e:
        print("    error: {}".format(e))
        return False, str(e)


def cmd_check(args):
    """Read-only check: report which context tools are available."""
    profiles = list(SUGGESTIONS.keys())
    print("Context tools status:\n")
    any_missing = False
    for profile in profiles:
        print("[{}]".format(profile))
        for tool in SUGGESTIONS[profile]:
            available = _is_available(tool["check"])
            status = "OK" if available else "MISSING"
            print("  {} {}".format(tool["name"], status))
            if not available:
                any_missing = True
        print()

    if any_missing:
        print("Some tools are missing. Use --apply PROFILE to see install commands.")
        print("Note: installing binaries does NOT automatically expose Codex")
        print("LSP/codegraph tools. Configure Codex separately.")
    else:
        print("All context tools are available.")


def cmd_apply(args):
    """Apply mode: show or run install commands for a profile."""
    profile = args.apply
    if profile not in SUGGESTIONS:
        print("Error: unknown profile '{}'".format(profile))
        print("Available profiles: {}".format(", ".join(sorted(SUGGESTIONS.keys()))))
        return 1

    manager = args.manager
    if manager and manager not in ALL_MANAGERS:
        print("Error: unknown manager '{}'".format(manager))
        print("Available managers: {}".format(", ".join(ALL_MANAGERS)))
        return 1

    tools = SUGGESTIONS[profile]
    install_plan = []

    for tool in tools:
        if _is_available(tool["check"]):
            print("[{}] {} already installed".format(profile, tool["name"]))
            continue

        if manager:
            if manager not in tool["commands"]:
                print("[{}] {} has no suggestion for manager '{}'".format(
                    profile, tool["name"], manager))
                continue
            cmd = tool["commands"][manager]
            install_plan.append((tool["name"], cmd))
        else:
            # No manager specified; show all available suggestions
            for mgr, cmd in sorted(tool["commands"].items()):
                install_plan.append(("{} ({})".format(tool["name"], mgr), cmd))

    if not install_plan:
        print("\nNo install commands to run for profile '{}'.".format(profile))
        if manager:
            print("(All tools already installed or no suggestions for manager '{}')".format(manager))
        return 0

    print("\nPlanned install commands for profile '{}':\n".format(profile))
    for label, cmd in install_plan:
        print("  # install {}".format(label))
        print("  $ " + " ".join(cmd))
        print()

    if not args.yes:
        print("Dry-run: no commands were executed.")
        print("Add --yes to actually install.")
        return 0

    # Execute
    print("Installing...\n")
    failures = 0
    for label, cmd in install_plan:
        print("# install {}".format(label))
        ok, _ = _run_command(cmd)
        if ok:
            print("  OK\n")
        else:
            print("  FAILED\n")
            failures += 1

    if failures:
        print("{} tool(s) failed to install.".format(failures))
        return 1

    print("All tools installed successfully.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Check and optionally install context tools (LSP, linting, code intelligence)."
    )
    parser.add_argument(
        "--apply",
        metavar="PROFILE",
        help="Profile to install (e.g., python, node, go, rust). "
             "Without --yes, prints planned commands only.",
    )
    parser.add_argument(
        "--manager",
        metavar="MANAGER",
        help="Package manager to use (e.g., pip, npm, go, cargo, choco, brew, apt, scoop). "
             "Required with --apply to limit to one manager.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually execute install commands. Requires --apply and --manager.",
    )
    args = parser.parse_args()

    # Validation
    if args.yes and not args.apply:
        parser.error("--yes requires --apply PROFILE")

    if args.yes and not args.manager:
        parser.error("--yes requires --manager MANAGER")

    if args.manager and not args.apply:
        parser.error("--manager requires --apply PROFILE")

    if args.apply and not args.manager:
        # --apply without --manager: show all suggestions, don't run
        pass

    if args.apply:
        sys.exit(cmd_apply(args) or 0)
    else:
        cmd_check(args)


if __name__ == "__main__":
    main()
