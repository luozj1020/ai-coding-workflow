#!/usr/bin/env python3
"""
update_skill.py  -  Convenience wrapper for updating ai-coding-workflow.

Usage:
    python scripts/update_skill.py
    python scripts/update_skill.py --pull
    python scripts/update_skill.py --bootstrap-current
    python scripts/update_skill.py --bootstrap-repo /path/to/repo
    python ~/.codex/skills/ai-coding-workflow/scripts/update_skill.py --source /path/to/ai-coding-workflow --bootstrap-current

By default this updates the Codex skill from the local source tree that
contains this script. Use --source when running the helper from an installed
skill but updating from a separate cloned repository.
"""

import argparse
import os
import subprocess
import sys


def script_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Update the ai-coding-workflow Codex skill and optionally bootstrap a repository."
    )
    parser.add_argument(
        "--source",
        metavar="PATH",
        default=script_root(),
        help="Skill source checkout to install from. Defaults to the directory containing this script.",
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Run 'git pull --ff-only' in --source before installing. Requires --source to be a git checkout.",
    )
    parser.add_argument(
        "--bootstrap-current",
        action="store_true",
        help="After updating the skill, bootstrap the current working directory.",
    )
    parser.add_argument(
        "--bootstrap-repo",
        metavar="PATH",
        help="After updating the skill, bootstrap the given repository path.",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_current and args.bootstrap_repo:
        parser.error("--bootstrap-current and --bootstrap-repo are mutually exclusive")
    return args


def validate_source(source):
    source = os.path.abspath(source)
    installer = os.path.join(source, "scripts", "install_for_codex.py")
    assets = os.path.join(source, "assets")
    if not os.path.isfile(installer):
        raise FileNotFoundError("install_for_codex.py not found under source: {}".format(source))
    if not os.path.isdir(assets):
        raise FileNotFoundError("assets directory not found under source: {}".format(source))
    return source, installer


def maybe_pull(source, enabled):
    if not enabled:
        return
    if not os.path.isdir(os.path.join(source, ".git")):
        raise RuntimeError("--pull requires --source to be a git checkout: {}".format(source))
    print("Pulling latest source:")
    print("  git -C {} pull --ff-only".format(source))
    subprocess.run(["git", "-C", source, "pull", "--ff-only"], check=True)


def build_install_command(installer, args):
    cmd = [sys.executable or "python", installer]
    if args.bootstrap_current:
        cmd.append("--bootstrap-current")
    elif args.bootstrap_repo:
        cmd.extend(["--bootstrap-repo", args.bootstrap_repo])
    return cmd


def main(argv=None):
    args = parse_args(argv)
    source, installer = validate_source(args.source)

    maybe_pull(source, args.pull)

    cmd = build_install_command(installer, args)
    print("Updating ai-coding-workflow:")
    print("  Source: {}".format(source))
    print("  Command: {}".format(" ".join(cmd)))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
