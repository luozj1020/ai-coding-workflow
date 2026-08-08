#!/usr/bin/env python3
"""
update_skill.py  -  Convenience wrapper for updating ai-coding-workflow.

Usage:
    python scripts/update_skill.py
    python scripts/update_skill.py --pull
    python scripts/update_skill.py --bootstrap-current
    python scripts/update_skill.py --bootstrap-repo /path/to/repo
    python scripts/update_skill.py --project-only
    python scripts/update_skill.py --project-only --local-only
    python scripts/update_skill.py --bootstrap-current --doctor
    python scripts/update_skill.py --auto-setup /path/to/repo
    python scripts/update_skill.py --auto-setup /path/to/repo --apply
    python scripts/update_skill.py --setup-current
    python scripts/update_skill.py --setup-current --apply
    python scripts/update_skill.py --setup-repo /path/to/repo
    python scripts/update_skill.py --setup-repo /path/to/repo --apply
    python ~/.codex/skills/ai-coding-workflow/scripts/update_skill.py --source /path/to/ai-coding-workflow --bootstrap-current

By default this updates the Codex skill from the local source tree that
contains this script and refreshes the current repository when it is already
bootstrapped. Bootstrap options select an explicit target; --skill-only opts
out of project-local refresh. --project-only refreshes a project without
activating the user-level Skill. Use --source when running the helper from an
installed skill but updating from a separate cloned repository.

Guided setup (--setup-current / --setup-repo) coordinates all steps in one
command: skill update, workflow bootstrap/refresh, environment-aware tool
configuration, and a final readiness check. Preview mode (default) prints
the plan without changes; --apply runs the coordinated sequence.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys

INSTALL_PROVENANCE_FILE = ".aiwf-install-provenance.json"
SKILL_NAME = "ai-coding-workflow"


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
        default=None,
        help=(
            "Skill source checkout to install from. From an installed Skill, "
            "the recorded source checkout is used; from a checkout, that checkout is used."
        ),
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Run 'git pull --ff-only' in --source before installing. Requires --source to be a git checkout.",
    )
    parser.add_argument(
        "--bootstrap-current",
        action="store_true",
        help=(
            "Bootstrap and refresh workflow files in the current repository after "
            "updating the Skill, or select that target with --project-only."
        ),
    )
    parser.add_argument(
        "--bootstrap-repo",
        metavar="PATH",
        help=(
            "Bootstrap and refresh workflow files in PATH after updating the Skill, "
            "or select that target with --project-only."
        ),
    )
    parser.add_argument(
        "--skill-only",
        action="store_true",
        help=(
            "Update only the user-level Skill. By default, an already-bootstrapped "
            "current repository is refreshed too so local ai/ launchers cannot drift."
        ),
    )
    parser.add_argument(
        "--project-only",
        action="store_true",
        help=(
            "Refresh a project from the selected source without updating the "
            "user-level Skill. Defaults to the current Git repository."
        ),
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help=(
            "For a project refresh, keep workflow control-plane ignores in "
            ".git/info/exclude instead of editing .gitignore."
        ),
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run the workflow doctor after a project refresh.",
    )
    parser.add_argument(
        "--code-search-services",
        choices=["ask", "skip", "check"],
        default="skip",
        help=(
            "Optional Zoekt/Sourcegraph service behavior while updating the Skill "
            "(default: skip)."
        ),
    )
    parser.add_argument(
        "--auto-setup",
        metavar="REPO",
        help=(
            "Run the source package's environment-aware setup for REPO; preview "
            "by default and use --apply to make changes."
        ),
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
        help="Execute guided setup or --auto-setup; both otherwise preview their plan.",
    )
    args = parser.parse_args(argv)
    guided_setup = args.setup_current or args.setup_repo
    if args.bootstrap_current and args.bootstrap_repo:
        parser.error("--bootstrap-current and --bootstrap-repo are mutually exclusive")
    if args.skill_only and (
        args.bootstrap_current
        or args.bootstrap_repo
        or args.project_only
        or args.local_only
        or args.doctor
    ):
        parser.error(
            "--skill-only cannot be combined with project refresh, --local-only, or --doctor"
        )
    if args.setup_current and args.setup_repo:
        parser.error("--setup-current and --setup-repo are mutually exclusive")
    if args.project_only and guided_setup:
        parser.error("--project-only and --setup-* modes are mutually exclusive")
    if args.auto_setup and (
        guided_setup
        or args.bootstrap_current
        or args.bootstrap_repo
        or args.skill_only
        or args.project_only
        or args.local_only
        or args.doctor
    ):
        parser.error(
            "--auto-setup cannot be combined with guided setup or project refresh options"
        )
    if args.apply and not (guided_setup or args.auto_setup):
        parser.error("--apply is only valid with --setup-* or --auto-setup")
    if (args.bootstrap_current or args.bootstrap_repo) and guided_setup:
        parser.error("--bootstrap-* and --setup-* modes are mutually exclusive")
    if args.pull and (guided_setup or args.auto_setup) and not args.apply:
        parser.error(
            "--pull changes the source checkout; use it with preview modes only when --apply is present"
        )
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


def git_commit(source):
    """Return HEAD for a valid Git checkout, or None."""
    try:
        result = subprocess.run(
            ["git", "-C", os.path.abspath(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def git_toplevel(path):
    """Return the containing Git worktree root, or the absolute input path."""
    path = os.path.abspath(path)
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return path
    return os.path.abspath(result.stdout.strip() or path)


def bootstrapped_repository(path):
    """Return the containing bootstrapped repository root, if present."""
    root = git_toplevel(path)
    launcher = os.path.join(root, "ai", "dispatch-to-claude.sh")
    return root if os.path.isfile(launcher) else None


def installed_skill_dir():
    """Return the active user-level Skill path used by install_for_codex.py."""
    return os.path.join(os.path.expanduser("~"), ".codex", "skills", SKILL_NAME)


def read_install_provenance(installed_root):
    path = os.path.join(installed_root, INSTALL_PROVENANCE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(
            "installed Skill has no valid update provenance at {}: {}. "
            "Run this updater from a Git checkout or pass --source explicitly."
            .format(path, exc)
        )
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError(
            "installed Skill update provenance is unsupported: {}. "
            "Pass --source explicitly.".format(path)
        )
    return value


def resolve_source(explicit_source=None, running_root=None):
    """Resolve a real update source without silently self-sourcing an install."""
    running_root = os.path.abspath(running_root or script_root())
    if explicit_source:
        return os.path.abspath(explicit_source), None

    running_commit = git_commit(running_root)
    if running_commit:
        return running_root, {
            "schema_version": 1,
            "source_path": os.path.realpath(running_root),
            "source_commit": running_commit,
        }

    provenance = read_install_provenance(running_root)
    source = provenance.get("source_path")
    if not isinstance(source, str) or not source.strip():
        raise RuntimeError(
            "installed Skill update provenance has no source_path. "
            "Pass --source explicitly."
        )
    source = os.path.abspath(source)
    if os.path.normcase(os.path.realpath(source)) == os.path.normcase(
        os.path.realpath(running_root)
    ):
        raise RuntimeError(
            "installed Skill provenance points back to the installed Skill itself. "
            "Pass the real Git checkout with --source."
        )
    if not git_commit(source):
        raise RuntimeError(
            "recorded Skill source is missing or is not a Git checkout: {}. "
            "Pass a current checkout with --source.".format(source)
        )
    return source, provenance


def maybe_pull(source, enabled):
    if not enabled:
        return
    if not os.path.isdir(os.path.join(source, ".git")):
        raise RuntimeError("--pull requires --source to be a git checkout: {}".format(source))
    print("Pulling latest source:")
    print("  git -C {} pull --ff-only".format(source))
    subprocess.run(["git", "-C", source, "pull", "--ff-only"], check=True)


def select_project_repository(args, current_dir=None):
    """Select the requested project target, or an implicit bootstrapped target."""
    current_dir = os.path.abspath(current_dir or os.getcwd())
    if args.bootstrap_current:
        return git_toplevel(current_dir)
    if args.bootstrap_repo:
        return os.path.abspath(args.bootstrap_repo)
    if args.project_only:
        return git_toplevel(current_dir)
    if args.skill_only:
        return None
    return bootstrapped_repository(current_dir)


def build_skill_update_command(installer, code_search_services="skip", python_cmd=None):
    """Build the installer invocation for a user-level Skill update."""
    cmd = [python_cmd or sys.executable or "python", installer]
    # Keep the default update compact. Explicit service handling is an operator
    # request, so retain the installer's normal output for ask/check modes.
    if code_search_services == "skip":
        cmd.append("--summary-only")
    cmd.extend(["--code-search-services", code_search_services])
    return cmd


def build_install_command(installer, args, current_dir=None, project_repo=None):
    """Build a Skill update plus an optional installed-copy project refresh."""
    cmd = build_skill_update_command(installer, args.code_search_services)
    if project_repo is None:
        project_repo = select_project_repository(args, current_dir=current_dir)
    if project_repo:
        cmd.extend(["--bootstrap-repo", project_repo])
        if args.local_only:
            cmd.append("--local-only")
        if args.doctor:
            cmd.append("--doctor")
    elif args.local_only:
        raise RuntimeError(
            "--local-only needs a project target. Use --project-only, "
            "--bootstrap-current, or --bootstrap-repo PATH."
        )
    elif args.doctor:
        raise RuntimeError(
            "--doctor needs a project target. Use --project-only, "
            "--bootstrap-current, or --bootstrap-repo PATH."
        )
    return cmd


def build_project_refresh_command(source, repo_path, args, python_cmd=None):
    """Build a source-backed project refresh without activating the Skill."""
    source = os.path.abspath(source)
    workflow_installer = os.path.join(source, "scripts", "install_workflow.py")
    if not os.path.isfile(workflow_installer):
        raise FileNotFoundError(
            "install_workflow.py not found under source: {}".format(source)
        )
    command = [
        python_cmd or sys.executable or "python",
        workflow_installer,
        os.path.abspath(repo_path),
        "--update-workflow-files",
        "--summary-only",
    ]
    if args.local_only:
        command.append("--local-only")
    return command


def build_doctor_command(source, repo_path, python_cmd=None):
    """Build a source-backed doctor command for --project-only."""
    source = os.path.abspath(source)
    doctor = os.path.join(source, "scripts", "doctor_workflow.py")
    if not os.path.isfile(doctor):
        raise FileNotFoundError(
            "doctor_workflow.py not found under source: {}".format(source)
        )
    return [python_cmd or sys.executable or "python", doctor, os.path.abspath(repo_path)]


def build_auto_setup_command(installer, repo_path, apply=False, python_cmd=None):
    """Build the direct compatibility path for install_for_codex --auto-setup."""
    command = [
        python_cmd or sys.executable or "python",
        installer,
        "--auto-setup",
        os.path.abspath(repo_path),
    ]
    if apply:
        command.append("--apply")
    return command


def build_guided_phases(
    source,
    repo_path,
    python_cmd=None,
    installed_dir=None,
    local_only=False,
    code_search_services="skip",
):
    """Return the ordered list of guided-setup phases.

    Each phase is a dict with keys: label, description, argv, cwd.
    Returns (source, installer, phases).
    """
    python_cmd = python_cmd or sys.executable or "python"
    source = os.path.abspath(source)
    installer = os.path.join(source, "scripts", "install_for_codex.py")
    repo_abs = os.path.abspath(repo_path)
    installed_dir = os.path.abspath(installed_dir or installed_skill_dir())
    workflow_installer = os.path.join(installed_dir, "scripts", "install_workflow.py")
    installed_installer = os.path.join(installed_dir, "scripts", "install_for_codex.py")
    doctor = os.path.join(installed_dir, "scripts", "doctor_workflow.py")
    skill_update = build_skill_update_command(
        installer,
        code_search_services=code_search_services,
        python_cmd=python_cmd,
    )
    workflow_refresh = [
        python_cmd,
        workflow_installer,
        repo_abs,
        "--update-workflow-files",
        "--summary-only",
    ]
    if local_only:
        workflow_refresh.append("--local-only")

    phases = [
        {
            "label": "skill-update",
            "description": "Install/update skill from source",
            "argv": skill_update,
            "cwd": None,
        },
        {
            "label": "workflow-bootstrap",
            "description": "Bootstrap/refresh workflow from the activated Skill in {}".format(repo_abs),
            "argv": workflow_refresh,
            "cwd": None,
        },
        {
            "label": "auto-setup",
            "description": "Environment-aware tool configuration",
            "argv": [python_cmd, installed_installer, "--auto-setup", repo_abs, "--apply"],
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


def run_command(command, heading):
    """Run one update command and return its process status without a traceback."""
    print(heading)
    print("  Command: {}".format(" ".join(_quote_cmd(part) for part in command)))
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError:
        print("Error: command not found: {}".format(command[0]), file=sys.stderr)
        return 1
    except OSError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            "Error: command failed with exit {}.".format(exc.returncode),
            file=sys.stderr,
        )
        return exc.returncode or 1
    return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        source, provenance = resolve_source(args.source)
        source, installer = validate_source(source)
    except (FileNotFoundError, RuntimeError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 2

    try:
        maybe_pull(source, args.pull)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 2
    current_commit = git_commit(source)
    print("Resolved update source:")
    print("  Checkout: {}".format(source))
    print("  HEAD:     {}".format(current_commit or "unknown"))
    if provenance and provenance.get("source_commit") != current_commit:
        print("  Installed from: {}".format(
            provenance.get("source_commit") or "unknown"
        ))
    if provenance:
        print("  Installed source dirty: {}".format(
            provenance.get("source_dirty", "unknown")
        ))

    # Direct compatibility path for the legacy environment setup command. It
    # deliberately does not activate the user-level Skill or alter workflow
    # files unless --apply is selected by the operator.
    if args.auto_setup:
        command = build_auto_setup_command(installer, args.auto_setup, apply=args.apply)
        action = "Applying environment-aware setup:" if args.apply else "Auto-setup preview:"
        return run_command(command, action)

    # Guided setup path
    if args.setup_current or args.setup_repo:
        repo_path = os.getcwd() if args.setup_current else args.setup_repo
        _, _, phases = build_guided_phases(
            source,
            repo_path,
            local_only=args.local_only,
            code_search_services=args.code_search_services,
        )
        if args.apply:
            return run_guided_setup(source, repo_path, phases)
        print_guided_preview(source, repo_path, phases)
        return 0

    project_repo = select_project_repository(args, current_dir=os.getcwd())
    if args.project_only:
        try:
            command = build_project_refresh_command(source, project_repo, args)
        except FileNotFoundError as exc:
            print("Error: {}".format(exc), file=sys.stderr)
            return 2
        print("Refreshing project workflow only (Skill installation skipped):")
        print("  Source: {}".format(source))
        print("  Repository: {}".format(project_repo))
        result = run_command(command, "Project refresh:")
        if result:
            return result
        if args.doctor:
            try:
                doctor_command = build_doctor_command(source, project_repo)
            except FileNotFoundError as exc:
                print("Error: {}".format(exc), file=sys.stderr)
                return 2
            result = run_command(doctor_command, "Running workflow doctor:")
            if result:
                return result
        print("Restart Codex before using refreshed managed AGENTS.md policy.")
        return 0

    # Update the Skill and automatically refresh an already-bootstrapped current
    # repository. This prevents a new managed policy from driving stale ai/
    # launchers that lack stable host/snapshot CLI parameters.
    try:
        cmd = build_install_command(
            installer,
            args,
            current_dir=os.getcwd(),
            project_repo=project_repo,
        )
    except RuntimeError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 2
    print("Updating ai-coding-workflow:")
    print("  Source: {}".format(source))
    result = run_command(cmd, "Skill update:")
    if result:
        return result
    if project_repo:
        print("Project workflow refreshed: {}".format(project_repo))
    elif args.skill_only:
        print("Project workflow refresh: intentionally skipped (--skill-only).")
    else:
        print(
            "Project workflow refresh: skipped because the containing Git repository "
            "is not bootstrapped. Use --bootstrap-current or --bootstrap-repo PATH."
        )
    print("Restart Codex before using the updated Skill or managed AGENTS.md policy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
