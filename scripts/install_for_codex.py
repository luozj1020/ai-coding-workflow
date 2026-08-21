#!/usr/bin/env python3
"""
install_for_codex.py  -  Install this Skill into the Codex skills directory.

Usage:
    python scripts/install_for_codex.py
    python scripts/install_for_codex.py --bootstrap-current
    python scripts/install_for_codex.py --bootstrap-repo /path/to/repo
    python scripts/install_for_codex.py --auto-setup /path/to/repo
    python scripts/install_for_codex.py --auto-setup /path/to/repo --apply

Copies the ai-coding-workflow Skill folder into:
    Windows:  %USERPROFILE%\\.codex\\skills\\ai-coding-workflow
    Unix/macOS: $HOME/.codex/skills/ai-coding-workflow

Excludes: .git, __pycache__, *.pyc, .worktrees, .codegraph, local refs,
bootstrap output, test repos, caches.

--auto-setup detects repository language profiles and plans or installs LSP
tools, CodeGraph initialization, and Zoekt CLI installation. Without --apply it is a
read-only preview; with --apply it runs the planned actions using user-level
package managers that do not require privileged OS mutation.

Uses only the Python standard library.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from fnmatch import fnmatch

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".worktrees", "node_modules", "task-cards",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".codegraph", ".codex",
    ".agents", "ref", "ai",
}
EXCLUDE_FILES = {"*.pyc", ".DS_Store", "Thumbs.db"}
EXCLUDE_ROOT_FILES = {"AGENTS.md", "CLAUDE.md"}
EXCLUDE_NAME_PATTERNS = ["tmp-*", "test-repo", "test_repo"]
EXCLUDE_PATH_PATTERNS = [".cache"]
SKILL_NAME = "ai-coding-workflow"
INSTALL_PROVENANCE_FILE = ".aiwf-install-provenance.json"
LSP_TOOL_CHECKS = [
    ("python", "pyright", "pyright"),
    ("node", "typescript-language-server", "typescript-language-server"),
    ("go", "gopls", "gopls"),
    ("rust", "rust-analyzer", "rust-analyzer"),
]

# Auto-setup scale thresholds (tracked file counts)
SMALL_REPO_MAX = 500
MEDIUM_REPO_MAX = 5000

# User-level managers that do not require privileged OS mutation
MANAGER_PREFERENCE = ["pip", "npm", "cargo", "go", "rustup", "brew", "scoop"]

# File extension → profile for lightweight detection
_EXT_TO_PROFILE = {
    ".py": "python",
    ".ts": "node", ".tsx": "node", ".js": "node", ".jsx": "node",
    ".go": "go",
    ".rs": "rust",
}


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


def git_commit(path):
    """Return the checkout HEAD for *path*, or None outside a Git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", os.path.abspath(path), "rev-parse", "HEAD"],
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


def install_provenance(source):
    """Build stable provenance that lets an installed updater find its checkout."""
    return {
        "schema_version": 1,
        "source_path": os.path.realpath(os.path.abspath(source)),
        "source_commit": git_commit(source),
        "source_dirty": git_dirty(source),
    }


def write_install_provenance(destination, source):
    path = os.path.join(destination, INSTALL_PROVENANCE_FILE)
    value = install_provenance(source)
    value["package_sha256"] = package_tree_hash(destination)
    fd, temporary = tempfile.mkstemp(
        prefix=".aiwf-provenance-", dir=destination
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git_dirty(path):
    """Return True/False for a checkout, or None outside Git."""
    try:
        result = subprocess.run(
            ["git", "-C", os.path.abspath(path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def package_tree_hash(path):
    """Hash installed package paths and bytes, excluding provenance itself."""
    digest = hashlib.sha256()
    root_path = os.path.abspath(path)
    for root, dirs, files in os.walk(root_path):
        dirs.sort()
        for name in sorted(files):
            full_path = os.path.join(root, name)
            relative = os.path.relpath(full_path, root_path).replace(os.sep, "/")
            if relative == INSTALL_PROVENANCE_FILE:
                continue
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with open(full_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


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


# ---- auto-setup helpers ----


def tracked_files(repo_path):
    """Return git-tracked paths, or None when repository metadata is unavailable."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=os.path.abspath(repo_path),
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def detect_repo_profiles(repo_path, max_files=None):
    """Detect language profiles from git-tracked paths only."""
    profiles = set()
    files = tracked_files(repo_path) or []
    if max_files is not None:
        files = files[:max_files]
    for path in files:
        profile = _EXT_TO_PROFILE.get(os.path.splitext(path)[1].lower())
        if profile:
            profiles.add(profile)
    return profiles


def count_tracked_files(repo_path):
    """Count git-tracked files. Returns -1 on error."""
    files = tracked_files(repo_path)
    return -1 if files is None else len(files)


def classify_repo_scale(file_count):
    """Return 'small', 'medium', 'large', or 'unknown'."""
    if file_count < 0:
        return "unknown"
    if file_count <= SMALL_REPO_MAX:
        return "small"
    if file_count <= MEDIUM_REPO_MAX:
        return "medium"
    return "large"


def _load_context_tools(skill_root):
    """Load install_context_tools module from the skill root."""
    path = os.path.join(skill_root, "scripts", "install_context_tools.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("_ctx_tools", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def select_install_plan(profile, ctx_module):
    """Select install commands for missing tools using best available manager.

    Returns (plan, manual) where:
        plan = [(tool_name, argv_list), ...]
        manual = [tool_name, ...]  (no safe manager on PATH)
    """
    tools = ctx_module.SUGGESTIONS.get(profile, [])
    plan = []
    manual = []
    for tool in tools:
        if ctx_module._is_available(tool["check"]):
            continue
        best_mgr = None
        for mgr in MANAGER_PREFERENCE:
            if mgr in tool["commands"] and shutil.which(mgr) is not None:
                best_mgr = mgr
                break
        if best_mgr:
            plan.append((tool["name"], list(tool["commands"][best_mgr])))
        else:
            manual.append(tool["name"])
    return plan, manual


def plan_codegraph(repo_path, apply, scale, file_count):
    """Return codegraph action dict."""
    cli_exists = shutil.which("codegraph") is not None
    repo_abs = os.path.abspath(repo_path)
    initialized = os.path.isdir(os.path.join(repo_abs, ".codegraph"))

    if initialized:
        return {"component": "codegraph", "status": "reuse",
                "detail": "already initialized"}

    if scale in ("small", "unknown"):
        return {"component": "codegraph", "status": "skip",
                "detail": "{} repository ({} files); CodeGraph not warranted".format(
                    scale, file_count)}

    if not cli_exists:
        return {"component": "codegraph", "status": "manual",
                "detail": "codegraph CLI not found; install it first"}

    if apply:
        return {"component": "codegraph", "status": "install",
                "argv": ["codegraph", "init"], "cwd": repo_abs}

    return {"component": "codegraph", "status": "plan",
            "detail": "would initialize CodeGraph ({scale} repo, {n} files)".format(
                scale=scale, n=file_count)}


def plan_zoekt(repo_path, skill_root, apply, scale):
    """Return zoekt action dict. Only applies to large repos."""
    if scale != "large":
        return {"component": "zoekt", "status": "skip",
                "detail": "{} repository; Zoekt only for large repos".format(scale)}

    zoekt_binaries = ["zoekt", "zoekt-index", "zoekt-git-index"]
    missing = [b for b in zoekt_binaries if shutil.which(b) is None]

    if not missing:
        return {"component": "zoekt", "status": "reuse",
                "detail": "all Zoekt binaries present"}

    helper = os.path.join(skill_root, "scripts", "code-search-service.py")

    if apply:
        if os.path.isfile(helper):
            return {"component": "zoekt", "status": "install",
                    "argv": [sys.executable, helper, "install-zoekt", "--yes"]}
        return {"component": "zoekt", "status": "blocked",
                "detail": "code-search-service.py not found in skill root"}

    return {"component": "zoekt", "status": "plan",
            "detail": "missing {}; would install via code-search-service.py".format(
                ", ".join(missing))}


def run_auto_setup(repo_path, skill_root, apply=False):
    """Run auto-setup for a repository. Returns structured result dict."""
    repo_abs = os.path.abspath(repo_path)
    file_count = count_tracked_files(repo_abs)
    scale = classify_repo_scale(file_count)
    profiles = sorted(detect_repo_profiles(repo_abs))

    ctx_module = _load_context_tools(skill_root)

    workflow_files = [
        "AGENTS.md",
        "ai/task-card-components/catalog.md",
        "ai/compose_task_card.py",
        "ai/dispatch-to-claude.sh",
    ]
    workflow_ready = all(os.path.isfile(os.path.join(repo_abs, path)) for path in workflow_files)
    workflow_result = None
    if apply and not workflow_ready:
        try:
            run_bootstrap(skill_root, repo_abs)
            workflow_result = True
        except (FileNotFoundError, OSError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired):
            workflow_result = False

    lsp_plans = {}
    if ctx_module:
        for profile in profiles:
            lsp_plans[profile] = select_install_plan(profile, ctx_module)

    cg = plan_codegraph(repo_abs, apply, scale, file_count)
    z = plan_zoekt(repo_abs, skill_root, apply, scale)

    lsp_results = {}
    if apply and ctx_module:
        for profile in profiles:
            plan, _ = lsp_plans.get(profile, ([], []))
            results = []
            for tool_name, argv in plan:
                try:
                    r = subprocess.run(
                        argv, capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        timeout=600,
                    )
                    results.append((tool_name, r.returncode == 0))
                except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                    results.append((tool_name, False))
            lsp_results[profile] = results

    cg_result = None
    if apply and cg.get("argv"):
        try:
            r = subprocess.run(
                cg["argv"], cwd=cg.get("cwd"),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=120,
            )
            cg_result = r.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            cg_result = False

    z_result = None
    if apply and z.get("argv"):
        try:
            r = subprocess.run(
                z["argv"], capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=900,
            )
            z_result = r.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            z_result = False

    success = workflow_result is not False
    success = success and all(ok for values in lsp_results.values() for _, ok in values)
    success = success and cg_result is not False and z_result is not False
    return {
        "repo": repo_abs,
        "file_count": file_count,
        "scale": scale,
        "profiles": profiles,
        "workflow_ready": workflow_ready,
        "workflow_result": workflow_result,
        "lsp_plans": lsp_plans,
        "lsp_results": lsp_results,
        "codegraph": cg,
        "codegraph_result": cg_result,
        "zoekt": z,
        "zoekt_result": z_result,
        "apply": apply,
        "success": success,
    }


def format_auto_setup_report(result):
    """Format auto-setup result as deterministic text."""
    lines = []
    lines.append("Auto-setup for: {}".format(result["repo"]))
    lines.append("Tracked files: {}".format(result["file_count"]))
    lines.append("Repository scale: {}".format(result["scale"]))
    lines.append("Detected profiles: {}".format(
        ", ".join(result["profiles"]) if result["profiles"] else "none"))
    lines.append("")

    if result.get("workflow_ready", False):
        lines.append("Workflow: reuse (project workflow already configured)")
    elif result["apply"]:
        lines.append("Workflow: {}".format(
            "configured" if result.get("workflow_result") else "FAILED"))
    else:
        lines.append("Workflow: plan (would bootstrap project workflow files)")
    lines.append("")

    for profile in result["profiles"]:
        plan, manual = result["lsp_plans"].get(profile, ([], []))
        lines.append("[{}] LSP tools:".format(profile))
        if not plan and not manual:
            lines.append("  all tools available")
        for tool_name, argv in plan:
            if result["apply"]:
                status_map = dict(result["lsp_results"].get(profile, []))
                status = "installed" if status_map.get(tool_name) else "FAILED"
                lines.append("  {} {}".format(tool_name, status))
            else:
                lines.append("  {} would install: {}".format(
                    tool_name, " ".join(quote_cmd_arg(part) for part in argv)))
        for tool_name in manual:
            lines.append("  {} manual/blocked (no safe manager available)".format(
                tool_name))
        lines.append("")

    cg = result["codegraph"]
    lines.append("CodeGraph: {}".format(cg["status"]))
    if cg.get("detail"):
        lines.append("  {}".format(cg["detail"]))
    if result["apply"] and result["codegraph_result"] is not None:
        lines.append("  result: {}".format(
            "success" if result["codegraph_result"] else "FAILED"))
    lines.append("")

    z = result["zoekt"]
    lines.append("Zoekt: {}".format(z["status"]))
    if z.get("detail"):
        lines.append("  {}".format(z["detail"]))
    if result["apply"] and result["zoekt_result"] is not None:
        lines.append("  result: {}".format(
            "success" if result["zoekt_result"] else "FAILED"))

    return "\n".join(lines)


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


def _iter_skill_files(source):
    """Yield normalized package-relative paths and source files."""
    source = os.path.abspath(source)
    for root, dirs, files in os.walk(source):
        dirs[:] = [
            name for name in dirs
            if not should_exclude(name, os.path.join(root, name))
        ]
        dirs.sort()
        rel_root = os.path.relpath(root, source)
        for name in sorted(files):
            source_file = os.path.join(root, name)
            if rel_root == "." and name in EXCLUDE_ROOT_FILES:
                continue
            if should_exclude(name, source_file):
                continue
            relative = (
                name if rel_root == "." else os.path.join(rel_root, name)
            ).replace(os.sep, "/")
            if relative == INSTALL_PROVENANCE_FILE:
                continue
            yield relative, source_file


def source_package_hash(source):
    """Hash exactly the source files that would enter an installed Skill."""
    digest = hashlib.sha256()
    for relative, source_file in _iter_skill_files(source):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with open(source_file, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _copy_skill_contents(src, dest):
    """Populate an empty staging directory from the skill source."""
    for relative, source_file in _iter_skill_files(src):
        dest_file = os.path.join(dest, *relative.split("/"))
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        shutil.copy2(source_file, dest_file)


def validate_skill_tree(path):
    """Validate the minimum executable skill surface before activation."""
    required = [
        "SKILL.md",
        "assets/AGENTS.md",
        "assets/CLAUDE.md",
        "scripts/install_for_codex.py",
        "scripts/install_workflow.py",
        "scripts/update_skill.py",
        "scripts/doctor_workflow.py",
    ]
    missing = [rel for rel in required if not os.path.isfile(os.path.join(path, rel))]
    if missing:
        raise ValueError(
            "staged skill is incomplete; missing: {}".format(", ".join(missing))
        )


def copy_skill(src, dest):
    """Transactionally replace the installed skill from a validated staging tree."""
    if paths_equal(src, dest):
        return "source"

    src = os.path.abspath(src)
    dest = os.path.abspath(dest)
    if os.path.isdir(dest) and source_package_hash(src) == package_tree_hash(dest):
        write_install_provenance(dest, src)
        return "unchanged"

    destination_existed = os.path.exists(dest)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".{}-staging-".format(SKILL_NAME), dir=parent)
    backup_root = None
    backup = None
    activated = False
    try:
        _copy_skill_contents(src, staging)
        write_install_provenance(staging, src)
        validate_skill_tree(staging)
        if os.path.exists(dest):
            backup_root = tempfile.mkdtemp(
                prefix=".{}-backup-".format(SKILL_NAME), dir=parent
            )
            backup = os.path.join(backup_root, "previous")
            os.replace(dest, backup)
        try:
            os.replace(staging, dest)
            activated = True
        except Exception:
            if backup and os.path.exists(backup) and not os.path.exists(dest):
                os.replace(backup, dest)
            raise
    finally:
        if not activated and os.path.exists(staging):
            shutil.rmtree(staging)
        backup_consumed = activated or not backup or not os.path.exists(backup)
        if backup_root and backup_consumed and os.path.exists(backup_root):
            shutil.rmtree(backup_root)
    return "updated" if destination_existed else "installed"


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
        "--local-only",
        action="store_true",
        help=(
            "With --bootstrap-current or --bootstrap-repo, keep workflow control-plane "
            "ignores in .git/info/exclude instead of editing .gitignore."
        ),
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "With --bootstrap-current or --bootstrap-repo, run the installed "
            "workflow doctor after the refresh."
        ),
    )
    parser.add_argument(
        "--auto-setup",
        metavar="REPO",
        help="Auto-configure LSP, CodeGraph, and Zoekt for the given repository.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --auto-setup, actually install tools. Without it, preview only.",
    )
    parser.add_argument(
        "--code-search-services",
        choices=["ask", "skip", "check"],
        default="ask",
        help="Ask about optional Zoekt/Sourcegraph services after install, skip, or run a read-only check.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Emit compact machine-readable update summaries and suppress guidance/no-op detail.",
    )
    parser.add_argument(
        "--install-agents",
        action="store_true",
        help="Install Interactive Codex subagent configurations to ~/.codex/agents/.",
    )
    parser.add_argument(
        "--agent-model",
        choices=["spark", "luna", "inherit"],
        default="spark",
        help="Default model for execution subagents (default: spark = gpt-5.3-codex-spark).",
    )
    parser.add_argument(
        "--override-built-in-agents",
        action="store_true",
        help="Install agents without aiwf_ prefix, overriding Codex built-in worker/explorer.",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_current and args.bootstrap_repo:
        parser.error("--bootstrap-current and --bootstrap-repo are mutually exclusive")
    if args.apply and not args.auto_setup:
        parser.error("--apply requires --auto-setup")
    if args.local_only and not (args.bootstrap_current or args.bootstrap_repo):
        parser.error("--local-only requires --bootstrap-current or --bootstrap-repo")
    if args.doctor and not (args.bootstrap_current or args.bootstrap_repo):
        parser.error("--doctor requires --bootstrap-current or --bootstrap-repo")
    if args.auto_setup and (
        args.bootstrap_current
        or args.bootstrap_repo
        or args.local_only
        or args.doctor
    ):
        parser.error(
            "--auto-setup cannot be combined with bootstrap, --local-only, or --doctor"
        )
    return args


def run_bootstrap(installed_skill_dir, repo_path, summary_only=False, local_only=False):
    """Run install_workflow.py from the installed skill against *repo_path*."""
    installer = os.path.join(installed_skill_dir, "scripts", "install_workflow.py")
    if not os.path.isfile(installer):
        raise FileNotFoundError("Workflow installer not found: {}".format(installer))
    repo_abs = os.path.abspath(repo_path)
    command = [sys.executable, installer, repo_abs, "--update-workflow-files"]
    if local_only:
        command.append("--local-only")
    if summary_only:
        command.append("--summary-only")
    else:
        print("\nBootstrapping repository workflow:")
        print("  Repository: {}".format(repo_abs))
        print("  Command:    {}".format(
            " ".join(quote_cmd_arg(part) for part in command)
        ))
    subprocess.run(command, check=True)


def run_doctor(installed_skill_dir, repo_path):
    """Run the doctor shipped in the active installed Skill."""
    doctor = os.path.join(installed_skill_dir, "scripts", "doctor_workflow.py")
    if not os.path.isfile(doctor):
        raise FileNotFoundError("Workflow doctor not found: {}".format(doctor))
    repo_abs = os.path.abspath(repo_path)
    command = [sys.executable, doctor, repo_abs]
    print("\nRunning workflow doctor:")
    print("  Repository: {}".format(repo_abs))
    print("  Command:    {}".format(
        " ".join(quote_cmd_arg(part) for part in command)
    ))
    subprocess.run(command, check=True)


def print_next_steps(installed_skill_dir, source_dir=None):
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
    if source_dir and not paths_equal(source_dir, installed_skill_dir):
        source_arg = quote_cmd_arg(os.path.abspath(source_dir))
        print("  {} --source {}".format(installed_updater_cmd, source_arg))
        print("  {} --source {} --bootstrap-current".format(
            installed_updater_cmd, source_arg
        ))
    else:
        print("  {} --source <path-to-git-checkout>".format(installed_updater_cmd))
        print("  {} --source <path-to-git-checkout> --bootstrap-current".format(
            installed_updater_cmd
        ))
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
    print("Restart Codex after updating: active conversations do not hot-reload Skill or AGENTS.md instructions.")


AGENT_MODEL_MAP = {
    "spark": "gpt-5.3-codex-spark",
    "luna": "gpt-5.6-luna",
    "inherit": None,
}


def install_agents(skill_dir, agent_model="spark", override_built_in=False, summary_only=False):
    """Install Interactive Codex subagent configurations.

    Copies agent TOML files from ``assets/codex-agents/`` to ``~/.codex/agents/``
    and merges ``[agents]`` settings into ``~/.codex/config.toml``.
    """
    import shutil

    source_dir = os.path.join(skill_dir, "assets", "codex-agents")
    if not os.path.isdir(source_dir):
        if not summary_only:
            print(f"Error: agent templates not found: {source_dir}")
        return False

    codex_dir = os.path.expanduser("~/.codex")
    agents_dir = os.path.join(codex_dir, "agents")
    os.makedirs(agents_dir, exist_ok=True)

    model_id = AGENT_MODEL_MAP.get(agent_model)

    # Copy agent files, optionally renaming to remove aiwf_ prefix.
    installed = []
    for filename in sorted(os.listdir(source_dir)):
        if not filename.endswith(".toml"):
            continue
        src = os.path.join(source_dir, filename)
        if override_built_in:
            dest_name = filename.replace("aiwf_", "")
        else:
            dest_name = filename
        dest = os.path.join(agents_dir, dest_name)
        # Read source content and optionally rewrite the name field.
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
        if override_built_in:
            # Rewrite name = "aiwf_worker" → name = "worker"
            agent_name = os.path.splitext(dest_name)[0]
            content = _rewrite_toml_name(content, agent_name)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)
        installed.append(dest_name)

    if not summary_only:
        print(f"\nInstalled {len(installed)} agent(s) to {agents_dir}:")
        for name in installed:
            print(f"  {name}")

    # Merge [agents] settings into config.toml.
    config_path = os.path.join(codex_dir, "config.toml")
    agents_section = {
        "enabled": "true",
        "max_concurrent_threads_per_session": "6",
    }
    if model_id:
        agents_section["default_subagent_model"] = f'"{model_id}"'
        agents_section["default_subagent_reasoning_effort"] = '"medium"'

    _merge_agents_config(config_path, agents_section, summary_only)

    return True


def _rewrite_toml_name(content, new_name):
    """Rewrite the name = '...' line in a TOML agent file."""
    import re
    return re.sub(
        r'^(name\s*=\s*)"[^"]*"',
        f'\\1"{new_name}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )


def _merge_agents_config(config_path, agents_section, summary_only=False):
    """Merge [agents] settings into an existing config.toml.

    Field-aware conservative merge:
    - existing key → preserve
    - missing key → supplement
    - conflict key → report, do not overwrite
    """
    existing = ""
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            existing = f.read()

    import re as _re

    if "[agents]" not in existing:
        # No existing section — append the full block.
        lines = ["\n[agents]"]
        for key, value in agents_section.items():
            lines.append(f"{key} = {value}")
        lines.append("")
        with open(config_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
        if not summary_only:
            print(f"\nMerged [agents] settings into {config_path}:")
            for key, value in agents_section.items():
                print(f"  {key} = {value}")
        return

    # [agents] exists — field-aware merge: add missing keys only.
    added = []
    skipped = []
    for key, value in agents_section.items():
        pattern = rf'^{_re.escape(key)}\s*='
        if _re.search(pattern, existing, _re.MULTILINE):
            skipped.append(key)
        else:
            existing = existing.rstrip("\n") + f"\n{key} = {value}\n"
            added.append(key)

    if added:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(existing)

    if not summary_only:
        if added:
            print(f"\nAdded missing [agents] keys to {config_path}:")
            for key in added:
                print(f"  {key} = {agents_section[key]}")
        if skipped:
            print(f"\nPreserved existing [agents] keys: {', '.join(skipped)}")
        if not added and not skipped:
            print(f"\n[agents] section in {config_path} is already complete.")


def main(argv=None):
    args = parse_args(argv)
    skill_dir = get_skill_dir()

    # Auto-setup is a repository operation, not a skill installation. Keeping
    # it on this early path makes the default preview genuinely read-only.
    if args.auto_setup:
        result = run_auto_setup(args.auto_setup, skill_dir, apply=args.apply)
        print(format_auto_setup_report(result))
        return 0 if result["success"] else 1

    codex_skills_dir = get_codex_skills_dir()
    dest = os.path.join(codex_skills_dir, SKILL_NAME)

    if not args.summary_only:
        print(f"Skill source:  {skill_dir}")
        print(f"Install to:    {dest}")

    if not os.path.isdir(os.path.join(skill_dir, "assets")):
        print(f"Error: Skill assets not found in {skill_dir}")
        sys.exit(1)

    os.makedirs(codex_skills_dir, exist_ok=True)
    if paths_equal(skill_dir, dest):
        install_status = "source"
        if not args.summary_only:
            print("\nSkill source is already the Codex install directory; skipping copy.")
    else:
        install_status = copy_skill(skill_dir, dest)

    # Count installed files
    file_count = 0
    for _, _, files in os.walk(dest):
        file_count += len(files)

    if args.summary_only:
        print("skill_update status={} files={} target={}".format(
            install_status, file_count, dest
        ))
    else:
        print(f"\nInstalled {file_count} files to {dest}")
        print("\nTo update, run this script again.")
        print("To verify, check that SKILL.md exists in the target directory.")
        print_next_steps(dest, source_dir=skill_dir)

    # Install Interactive subagents if requested.
    if args.install_agents:
        install_agents(
            dest,
            agent_model=args.agent_model,
            override_built_in=args.override_built_in_agents,
            summary_only=args.summary_only,
        )

    bootstrap_repo = None
    if args.bootstrap_current:
        bootstrap_repo = git_toplevel(os.getcwd())
    elif args.bootstrap_repo:
        bootstrap_repo = args.bootstrap_repo
    if bootstrap_repo:
        run_bootstrap(
            dest,
            bootstrap_repo,
            summary_only=args.summary_only,
            local_only=args.local_only,
        )
        if args.doctor:
            run_doctor(dest, bootstrap_repo)
        if not args.summary_only:
            print_context_tool_guidance(dest, bootstrap_repo)
    elif not args.summary_only:
        print_context_tool_guidance(dest)
    if not args.summary_only:
        maybe_offer_code_search_services(dest, args.code_search_services)

if __name__ == "__main__":
    sys.exit(main())
