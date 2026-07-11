#!/usr/bin/env python3
"""
update_skill.py  -  Convenience wrapper for updating ai-coding-workflow.

Usage:
    python scripts/update_skill.py
    python scripts/update_skill.py --pull
    python scripts/update_skill.py --bootstrap-current
    python scripts/update_skill.py --bootstrap-repo /path/to/repo
    python scripts/update_skill.py --setup-current
    python scripts/update_skill.py --setup-current --apply
    python scripts/update_skill.py --setup-repo /path/to/repo
    python scripts/update_skill.py --setup-repo /path/to/repo --apply
    python ~/.codex/skills/ai-coding-workflow/scripts/update_skill.py --source /path/to/ai-coding-workflow --bootstrap-current

By default this updates the Codex skill from the local source tree that
contains this script. Bootstrap options also refresh existing project-local
workflow files with install_workflow.py --update-workflow-files. Use --source
when running the helper from an installed skill but updating from a separate
cloned repository.

Guided setup (--setup-current / --setup-repo) coordinates all steps in one
command: skill update, workflow bootstrap/refresh, environment-aware tool
configuration, and a final readiness check. Preview mode (default) prints
the plan without changes; --apply runs the coordinated sequence.
"""

import argparse
import os
import shlex
import subprocess
import sys


def script_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _quote_cmd(value):
    """Quote a command argument for display."""
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


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
        help="After updating the skill, bootstrap and refresh workflow files in the current working directory.",
    )
    parser.add_argument(
        "--bootstrap-repo",
        metavar="PATH",
        help="After updating the skill, bootstrap and refresh workflow files in the given repository path.",
    )
    parser.add_argument(
        "--setup-current",
        action="store_true",
        help="Guided setup for the current working directory: preview all phases, then --apply to run.",
    )
    parser.add_argument(
        "--setup-repo",
        metavar="PATH",
        help="Guided setup for the given repository: preview all phases, then --apply to run.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --setup-current or --setup-repo, execute the coordinated setup sequence.",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_current and args.bootstrap_repo:
        parser.error("--bootstrap-current and --bootstrap-repo are mutually exclusive")
    if args.setup_current and args.setup_repo:
        parser.error("--setup-current and --setup-repo are mutually exclusive")
    if args.apply and not (args.setup_current or args.setup_repo):
        parser.error("--apply is only valid with --setup-current or --setup-repo")
    if (args.bootstrap_current or args.bootstrap_repo) and (args.setup_current or args.setup_repo):
        parser.error("--bootstrap-* and --setup-* modes are mutually exclusive")
    if args.pull and (args.setup_current or args.setup_repo) and not args.apply:
        parser.error("--pull changes the source checkout; use it with guided setup only when --apply is present")
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


def build_guided_phases(source, repo_path, python_cmd=None):
    """Return the ordered list of guided-setup phases.

    Each phase is a dict with keys: label, description, argv, cwd.
    Returns (source, installer, phases).
    """
    python_cmd = python_cmd or sys.executable or "python"
    source = os.path.abspath(source)
    installer = os.path.join(source, "scripts", "install_for_codex.py")
    repo_abs = os.path.abspath(repo_path)
    workflow_installer = os.path.join(source, "scripts", "install_workflow.py")
    doctor = os.path.join(source, "scripts", "doctor_workflow.py")

    phases = [
        {
            "label": "skill-update",
            "description": "Install/update skill from source",
            "argv": [python_cmd, installer],
            "cwd": None,
        },
        {
            "label": "workflow-bootstrap",
            "description": "Bootstrap/refresh workflow in {}".format(repo_abs),
            "argv": [python_cmd, workflow_installer, repo_abs, "--update-workflow-files"],
            "cwd": None,
        },
        {
            "label": "auto-setup",
            "description": "Environment-aware tool configuration",
            "argv": [python_cmd, installer, "--auto-setup", repo_abs, "--apply"],
            "cwd": None,
        },
        {
            "label": "doctor",
            "description": "Final readiness check",
            "argv": [python_cmd, doctor, repo_abs],
            "cwd": None,
        },
    ]
    return source, installer, phases


def print_guided_preview(source, repo_path, phases):
    """Print the guided-setup preview without making changes."""
    repo_abs = os.path.abspath(repo_path)
    print("Guided setup preview (no changes):")
    print("  Source: {}".format(source))
    print("  Repository: {}".format(repo_abs))
    print("")
    for i, phase in enumerate(phases, 1):
        print("Phase {}: {}".format(i, phase["label"]))
        print("  {}".format(phase["description"]))
        print("  {}".format(" ".join(_quote_cmd(a) for a in phase["argv"])))
        print("")
    print("Run with --apply to execute these phases.")


def run_guided_setup(source, repo_path, phases):
    """Execute guided-setup phases in order. Returns 0 on success, non-zero on failure."""
    repo_abs = os.path.abspath(repo_path)
    print("Guided setup for: {}".format(repo_abs))
    print("")
    for i, phase in enumerate(phases, 1):
        label = phase["label"]
        argv = phase["argv"]
        cwd = phase.get("cwd")
        print("[{}/{}] {}".format(i, len(phases), phase["description"]))
        print("  {}".format(" ".join(_quote_cmd(a) for a in argv)))
        try:
            result = subprocess.run(
                argv, cwd=cwd,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            print("  FAILED: command not found: {}".format(argv[0]))
            return 1
        except OSError as exc:
            print("  FAILED: {}".format(exc))
            return 1
        if result.returncode != 0:
            print("  FAILED (exit {}): {}".format(
                result.returncode,
                " ".join(_quote_cmd(a) for a in argv),
            ))
            return 1
        print("  OK")
        print("")
    print("Guided setup complete.")
    return 0


def main(argv=None):
    args = parse_args(argv)
    source, installer = validate_source(args.source)

    maybe_pull(source, args.pull)

    # Guided setup path
    if args.setup_current or args.setup_repo:
        repo_path = os.getcwd() if args.setup_current else args.setup_repo
        _, _, phases = build_guided_phases(source, repo_path)
        if args.apply:
            return run_guided_setup(source, repo_path, phases)
        print_guided_preview(source, repo_path, phases)
        return 0

    # Legacy path: update skill + optional bootstrap
    cmd = build_install_command(installer, args)
    print("Updating ai-coding-workflow:")
    print("  Source: {}".format(source))
    print("  Command: {}".format(" ".join(cmd)))
    subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
