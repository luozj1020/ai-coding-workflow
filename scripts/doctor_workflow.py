#!/usr/bin/env python3
"""
doctor_workflow.py  -  Check whether a repository is ready for the ai-coding-workflow dispatch/review loop.

Usage:
    python ai/doctor_workflow.py [repo-path] [--hash-path RELPATH ...]

Read-only diagnostics. Reports errors, warnings, and info.
Exit 0 when no hard errors are detected, non-zero otherwise.

Uses only the Python standard library.
"""

import glob
import os
import shlex
import shutil
import subprocess
import sys
import time
import argparse

# --- Levels ---
ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"

WORKFLOW_REQUIRED_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "ai/task-card-template.md",
    "ai/evidence-packet-template.md",
    "ai/spec-template.md",
    "ai/plan-task-template.md",
    "ai/plan-findings-template.md",
    "ai/plan-progress-template.md",
    "ai/README.md",
    "ai/dispatch-to-claude.sh",
    "ai/check-worktree.sh",
    "ai/review-with-codex.sh",
    "ai/run-codex-spark.sh",
    "ai/run-parallel-loop.sh",
    "ai/run-loop.sh",
    "ai/status-claude.sh",
    "ai/watch-claude.sh",
    "ai/kill-claude.sh",
    "ai/cleanup-worktree.sh",
    "ai/doctor_workflow.py",
    "ai/claude-healthcheck.py",
    "ai/code-search-service.py",
    "ai/clean_runtime.py",
    "ai/install_context_tools.py",
    "ai/locate-code.py",
    "ai/summarize-loop-run.py",
    "ai/benchmark-loop-runs.py",
    "ai/init-spec.py",
    "ai/plan-to-task-cards.py",
    "ai/init-plan.py",
    "ai/session-catchup.py",
    "ai/run-workflow.py",
    ".worktrees/.gitkeep",
]

# Documented runtime helper scripts that should be reported separately
# when missing, distinct from other required workflow files.
WORKFLOW_RUNTIME_HELPERS = [
    "ai/dispatch-to-claude.sh",
    "ai/check-worktree.sh",
    "ai/run-codex-spark.sh",
    "ai/run-parallel-loop.sh",
    "ai/status-claude.sh",
    "ai/watch-claude.sh",
    "ai/doctor_workflow.py",
    "ai/claude-healthcheck.py",
    "ai/code-search-service.py",
    "ai/clean_runtime.py",
    "ai/install_context_tools.py",
    "ai/locate-code.py",
    "ai/summarize-loop-run.py",
    "ai/benchmark-loop-runs.py",
    "ai/init-spec.py",
    "ai/plan-to-task-cards.py",
    "ai/init-plan.py",
    "ai/session-catchup.py",
    "ai/run-workflow.py",
]

WORKFLOW_PLAIN_FILE_SOURCES = [
    ("assets/task-card-template.md", "ai/task-card-template.md"),
    ("assets/evidence-packet-template.md", "ai/evidence-packet-template.md"),
    ("assets/spec-template.md", "ai/spec-template.md"),
    ("assets/plan-task-template.md", "ai/plan-task-template.md"),
    ("assets/plan-findings-template.md", "ai/plan-findings-template.md"),
    ("assets/plan-progress-template.md", "ai/plan-progress-template.md"),
    ("assets/README.md", "ai/README.md"),
    ("scripts/dispatch-to-claude.sh", "ai/dispatch-to-claude.sh"),
    ("scripts/check-worktree.sh", "ai/check-worktree.sh"),
    ("scripts/review-with-codex.sh", "ai/review-with-codex.sh"),
    ("scripts/run-codex-spark.sh", "ai/run-codex-spark.sh"),
    ("scripts/run-parallel-loop.sh", "ai/run-parallel-loop.sh"),
    ("scripts/run-loop.sh", "ai/run-loop.sh"),
    ("scripts/status-claude.sh", "ai/status-claude.sh"),
    ("scripts/watch-claude.sh", "ai/watch-claude.sh"),
    ("scripts/kill-claude.sh", "ai/kill-claude.sh"),
    ("scripts/cleanup-worktree.sh", "ai/cleanup-worktree.sh"),
    ("scripts/pwsh-utf8.ps1", "ai/pwsh-utf8.ps1"),
    ("scripts/doctor_workflow.py", "ai/doctor_workflow.py"),
    ("scripts/claude-healthcheck.py", "ai/claude-healthcheck.py"),
    ("scripts/code-search-service.py", "ai/code-search-service.py"),
    ("scripts/clean_runtime.py", "ai/clean_runtime.py"),
    ("scripts/install_context_tools.py", "ai/install_context_tools.py"),
    ("scripts/locate-code.py", "ai/locate-code.py"),
    ("scripts/summarize-loop-run.py", "ai/summarize-loop-run.py"),
    ("scripts/benchmark-loop-runs.py", "ai/benchmark-loop-runs.py"),
    ("scripts/init-spec.py", "ai/init-spec.py"),
    ("scripts/plan-to-task-cards.py", "ai/plan-to-task-cards.py"),
    ("scripts/init-plan.py", "ai/init-plan.py"),
    ("scripts/session-catchup.py", "ai/session-catchup.py"),
    ("scripts/run-workflow.py", "ai/run-workflow.py"),
]


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


def _git_available():
    """Return (path, version_line) if git is reachable."""
    try:
        r = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            return "git", r.stdout.strip().split("\n")[0]
    except FileNotFoundError:
        pass
    # Try common Windows locations
    for candidate in [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]:
        if os.path.isfile(candidate):
            try:
                r = subprocess.run(
                    [candidate, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if r.returncode == 0:
                    return candidate, r.stdout.strip().split("\n")[0]
            except OSError:
                pass
    return None, None


def _git_worktree_dirty(repo_root):
    """Return list of dirty-file descriptions, or empty if clean."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return ["git status failed (rc={})".format(r.returncode)]
        lines = [l for l in r.stdout.strip().split("\n") if l.strip()]
        return lines
    except FileNotFoundError:
        return ["git not available"]


def _count_runtime_artifacts(repo_root):
    """Count .worktrees/ entries (excluding .gitkeep) and root tmp-* dirs."""
    worktrees_dir = os.path.join(repo_root, ".worktrees")
    worktree_count = 0
    if os.path.isdir(worktrees_dir):
        try:
            for entry in os.listdir(worktrees_dir):
                if entry == ".gitkeep":
                    continue
                worktree_count += 1
        except OSError:
            pass

    tmp_count = 0
    for entry in glob.glob(os.path.join(repo_root, "tmp-*")):
        if os.path.isdir(entry) or os.path.isfile(entry):
            tmp_count += 1

    return worktree_count, tmp_count


# Maximum filesystem nodes to visit when computing approximate size.
# Prevents runaway traversal on very large worktree trees.
_WORKTREES_MAX_SIZE_NODES = 5000


def _inventory_worktrees(repo_root):
    """Return a read-only inventory of .worktrees/ entries.

    Returns a dict with:
        entry_count     - immediate children (excluding .gitkeep)
        approximate_bytes - total byte size (may be partial if capped)
        oldest_path     - path of the oldest entry, or None
        oldest_age_days - age in days of the oldest entry, or 0
        buckets         - dict {"<7": n, "7-30": n, ">30": n}
        partial         - True if size traversal was capped at _WORKTREES_MAX_SIZE_NODES
        error           - error string, or None

    Missing .worktrees is treated as clean (entry_count=0).
    Permission/stat errors produce a warning-level error string without crashing.
    """
    result = {
        "entry_count": 0,
        "approximate_bytes": 0,
        "oldest_path": None,
        "oldest_age_days": 0,
        "buckets": {"<7": 0, "7-30": 0, ">30": 0},
        "partial": False,
        "error": None,
    }

    worktrees_dir = os.path.join(repo_root, ".worktrees")
    if not os.path.isdir(worktrees_dir):
        return result

    try:
        entries = os.listdir(worktrees_dir)
    except OSError as exc:
        result["error"] = "cannot list .worktrees/: {}".format(exc)
        return result

    # Filter out .gitkeep; keep only real runtime entries
    runtime_entries = [e for e in entries if e != ".gitkeep"]
    result["entry_count"] = len(runtime_entries)

    if not runtime_entries:
        return result

    now = time.time()
    oldest_mtime = now
    oldest_path = None
    nodes_visited = 0
    total_bytes = 0
    partial = False

    for name in runtime_entries:
        entry_path = os.path.join(worktrees_dir, name)
        try:
            st = os.stat(entry_path)
        except OSError as exc:
            result["error"] = "stat error on {}: {}".format(name, exc)
            continue

        age_days = max(0, (now - st.st_mtime) / 86400)
        if st.st_mtime < oldest_mtime:
            oldest_mtime = st.st_mtime
            oldest_path = entry_path

        # Bucket this entry by age
        if age_days < 7:
            result["buckets"]["<7"] += 1
        elif age_days <= 30:
            result["buckets"]["7-30"] += 1
        else:
            result["buckets"][">30"] += 1

        # Size traversal: walk directory trees, capping total nodes visited.
        # Skip traversal for symlinks to avoid escaping .worktrees boundary.
        if os.path.isdir(entry_path) and not os.path.islink(entry_path):
            for dirpath, dirnames, filenames in os.walk(entry_path):
                if partial:
                    break
                for fname in filenames:
                    nodes_visited += 1
                    if nodes_visited > _WORKTREES_MAX_SIZE_NODES:
                        partial = True
                        break
                    fpath = os.path.join(dirpath, fname)
                    try:
                        total_bytes += os.path.getsize(fpath)
                    except OSError:
                        pass
                # Count the directory itself as a visited node
                nodes_visited += 1
                if nodes_visited > _WORKTREES_MAX_SIZE_NODES:
                    partial = True
        else:
            # Single file entry
            total_bytes += st.st_size

    result["approximate_bytes"] = total_bytes
    result["partial"] = partial
    if oldest_path is not None:
        result["oldest_path"] = oldest_path
        result["oldest_age_days"] = max(0, (now - oldest_mtime) / 86400)

    return result


def _format_bytes(n):
    """Format a byte count into a human-readable string."""
    if n < 1024:
        return "{} B".format(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024.0
        if n < 1024:
            return "{:.1f} {}".format(n, unit)
    return "{:.1f} PiB".format(n)


def _worktrees_ignore_status(repo_root):
    """Return (ok, message) for .worktrees runtime ignore configuration."""
    gitignore = os.path.join(repo_root, ".gitignore")
    required = {"/.worktrees/*", "!/.worktrees/.gitkeep"}
    if os.path.isfile(gitignore):
        try:
            text = _read_text(gitignore)
        except OSError as exc:
            return False, "could not read .gitignore: {}".format(exc)
        lines = {line.strip() for line in text.splitlines()}
        missing = sorted(required - lines)
        if not missing:
            return True, ".worktrees runtime artifacts are ignored"

    local_ok, local_message = _local_only_exclude_status(repo_root)
    if local_ok:
        return True, local_message

    if not os.path.isfile(gitignore):
        return False, ".gitignore is missing .worktrees runtime rules"
    return False, ".gitignore missing: {}".format(", ".join(missing))


def _git_info_exclude_path(repo_root):
    """Return the repo-local git info/exclude path, or None when unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            if not os.path.isabs(path):
                path = os.path.join(repo_root, path)
            return path
    except (FileNotFoundError, OSError):
        pass
    fallback = os.path.join(repo_root, ".git", "info", "exclude")
    if os.path.isdir(os.path.join(repo_root, ".git")):
        return fallback
    return None


def _local_only_exclude_status(repo_root):
    """Return whether local-only control-plane ignores are active."""
    exclude_path = _git_info_exclude_path(repo_root)
    if not exclude_path or not os.path.isfile(exclude_path):
        return False, ".git/info/exclude missing local-only control-plane rules"
    try:
        text = _read_text(exclude_path)
    except OSError as exc:
        return False, "could not read .git/info/exclude: {}".format(exc)
    lines = {line.strip() for line in text.splitlines()}
    required = {"/AGENTS.md", "/CLAUDE.md", "/ai/", "/.worktrees/"}
    missing = sorted(required - lines)
    if missing:
        return False, ".git/info/exclude missing local-only rules: {}".format(", ".join(missing))
    return True, "local-only control-plane ignore active via .git/info/exclude"


def _tracked_file_count(repo_root):
    """Return tracked file count or None when git ls-files is unavailable."""
    try:
        r = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            capture_output=True,
        )
        if r.returncode != 0:
            return None
        if not r.stdout:
            return 0
        return r.stdout.count(b"\0")
    except (FileNotFoundError, OSError):
        return None


def _resolve_bash():
    """Find a usable bash. Returns (path, label) or (None, reason)."""
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c, "Git Bash"

        # Check if PATH bash is WSL or Git Bash
        bash_path = shutil.which("bash")
        if bash_path:
            # Heuristic: if the path contains Git, it's likely Git Bash
            if "Git" in bash_path or "git" in bash_path:
                return bash_path, "Git Bash (via PATH)"
            return bash_path, "bash in PATH (may be WSL)"
        return None, "bash not found"

    # Unix
    bash_path = shutil.which("bash")
    if bash_path:
        return bash_path, "bash"
    return None, "bash not found"


def _check_claude_cli():
    """Check if Claude CLI is available."""
    claude_path = shutil.which("claude")
    if claude_path:
        return claude_path
    return None


def _check_proxy_vars():
    """Return list of (name, masked_value) for common proxy vars that are set.
    Values are masked to avoid leaking credentials."""
    proxy_names = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    ]
    found = []
    for name in proxy_names:
        val = os.environ.get(name)
        if val:
            masked = _mask_proxy_value(val)
            found.append((name, masked))
    return found


def _mask_proxy_value(val):
    """Mask credentials in proxy URL. Keep scheme, host, port; hide userinfo."""
    # Handle http://user:pass@host:port style
    if "@" in val:
        scheme_end = val.find("://")
        if scheme_end >= 0:
            scheme = val[:scheme_end + 3]
            rest = val[scheme_end + 3:]
            at_idx = rest.find("@")
            if at_idx >= 0:
                return scheme + "***:***@" + rest[at_idx + 1:]
    return val


def _check_codex_skill():
    """Check if the ai-coding-workflow skill is installed in Codex skills dir."""
    home = os.path.expanduser("~")
    skill_path = os.path.join(home, ".codex", "skills", "ai-coding-workflow")
    if os.path.isdir(skill_path):
        skill_md = os.path.join(skill_path, "SKILL.md")
        if os.path.isfile(skill_md):
            return skill_path
        return skill_path + " (SKILL.md missing)"
    return None


def _quote_cmd_arg(value):
    """Quote a command argument for display."""
    if sys.platform == "win32":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def _candidate_workflow_installers():
    """Return candidate install_workflow.py paths without requiring they exist."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".codex", "skills", "ai-coding-workflow", "scripts", "install_workflow.py"),
        os.path.join(script_dir, "install_workflow.py"),
    ]


def _workflow_bootstrap_command(repo_root, update_workflow_files=False):
    """Return a concrete command to bootstrap this repository when possible."""
    python_cmd = sys.executable or "python"
    for installer in _candidate_workflow_installers():
        if os.path.isfile(installer):
            cmd = "{} {} {}".format(
                _quote_cmd_arg(python_cmd),
                _quote_cmd_arg(installer),
                _quote_cmd_arg(repo_root),
            )
            if update_workflow_files:
                cmd += " --update-workflow-files"
            return cmd
    if sys.platform == "win32":
        installer = r"%USERPROFILE%\.codex\skills\ai-coding-workflow\scripts\install_workflow.py"
    else:
        installer = "~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py"
    cmd = "python {} {}".format(installer, _quote_cmd_arg(repo_root))
    if update_workflow_files:
        cmd += " --update-workflow-files"
    return cmd


def _missing_project_workflow_files(repo_root):
    """Return workflow files missing from a bootstrapped target repository."""
    missing = []
    for rel in WORKFLOW_REQUIRED_FILES:
        path = os.path.join(repo_root, *rel.split("/"))
        if not os.path.exists(path):
            missing.append(rel)
    return missing


def _candidate_skill_roots():
    """Return installed skill roots that can be used as workflow references."""
    home = os.path.expanduser("~")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(home, ".codex", "skills", "ai-coding-workflow"),
        os.path.dirname(script_dir),
    ]


def _normalize_for_compare(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _reference_skill_root():
    """Return the first candidate skill root with expected assets/scripts."""
    for root in _candidate_skill_roots():
        if os.path.isfile(os.path.join(root, "assets", "task-card-template.md")) and os.path.isfile(
            os.path.join(root, "scripts", "dispatch-to-claude.sh")
        ):
            return root
    return None


def _outdated_project_workflow_files(repo_root):
    """Return local workflow files that differ from the installed skill copy."""
    skill_root = _reference_skill_root()
    if not skill_root:
        return []
    outdated = []
    for src_rel, dest_rel in WORKFLOW_PLAIN_FILE_SOURCES:
        src = os.path.join(skill_root, *src_rel.split("/"))
        dest = os.path.join(repo_root, *dest_rel.split("/"))
        if not os.path.isfile(src) or not os.path.isfile(dest):
            continue
        if _normalize_for_compare(_read_text(src)) != _normalize_for_compare(_read_text(dest)):
            outdated.append(dest_rel)
    return outdated


# Context tools to check. Each entry: (name, check_command).
# These are common LSP/linting tools. Presence on PATH is informational only;
# installing them does NOT guarantee Codex can see them as LSP/codegraph APIs.
CONTEXT_TOOLS = [
    ("pyright", ["pyright", "--version"]),
    ("ruff", ["ruff", "--version"]),
    ("mypy", ["mypy", "--version"]),
    ("typescript-language-server", ["typescript-language-server", "--version"]),
    ("gopls", ["gopls", "version"]),
    ("rust-analyzer", ["rust-analyzer", "--version"]),
]


def _check_context_tools():
    """Check which context tools are available on PATH.

    Returns list of (name, available) tuples.
    """
    results = []
    for name, check_cmd in CONTEXT_TOOLS:
        run_cmd = list(check_cmd)
        executable = shutil.which(run_cmd[0])
        if executable is None:
            results.append((name, False))
            continue
        run_cmd[0] = executable
        try:
            r = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            results.append((name, r.returncode == 0))
        except (FileNotFoundError, OSError):
            results.append((name, False))
    return results


def _zoekt_index_path():
    return os.environ.get(
        "AI_CODE_ZOEKT_INDEX",
        os.path.join(os.path.expanduser("~"), ".cache", "ai-coding-workflow", "zoekt"),
    )


def _check_code_search_services():
    """Return informational code-search backend availability rows."""
    rows = []
    zoekt_bins = ["zoekt-git-index", "zoekt-index", "zoekt"]
    missing_zoekt = [name for name in zoekt_bins if not shutil.which(name)]
    if missing_zoekt:
        rows.append(("code-search", "Zoekt CLI missing: {}".format(", ".join(missing_zoekt))))
    else:
        rows.append(("code-search", "Zoekt CLI available"))
    index = _zoekt_index_path()
    rows.append((
        "code-search",
        "Zoekt index {}: {}".format(index, "present" if os.path.isdir(index) else "missing"),
    ))
    sourcegraph_url = os.environ.get("SOURCEGRAPH_URL", "")
    if sourcegraph_url:
        rows.append(("code-search", "Sourcegraph URL configured: {}".format(sourcegraph_url)))
    else:
        rows.append(("code-search", "Sourcegraph URL not configured"))
    rows.append(("code-search", "Docker: {}".format(shutil.which("docker") or "missing")))
    return rows


def _validate_hash_path(path, repo_root):
    """Validate a --hash-path value. Returns (ok, error_message).

    Rejects absolute paths, traversal (..), missing paths, and directories.
    """
    if os.path.isabs(path):
        return False, "absolute path not allowed: {}".format(path)
    if ".." in path.split(os.sep) or ".." in path.split("/"):
        return False, "traversal (..) not allowed: {}".format(path)
    full = os.path.join(repo_root, path)
    repo_real = os.path.realpath(repo_root)
    full_real = os.path.realpath(full)
    try:
        inside_repo = os.path.commonpath([repo_real, full_real]) == repo_real
    except ValueError:
        inside_repo = False
    if not inside_repo:
        return False, "path resolves outside repository: {}".format(path)
    if not os.path.exists(full):
        return False, "path does not exist: {}".format(path)
    if os.path.isdir(full):
        return False, "directory not allowed (only files): {}".format(path)
    return True, None


def _hash_path_diagnostics(repo_root, paths):
    """Compare filesystem hash, index hash, and porcelain status for each path.

    Returns a list of (level, category, message) findings.
    Never mutates anything. Labels as target-only scope.
    """
    findings = []
    if not paths:
        return findings

    findings.append((INFO, "hash-check",
                     "Target-only scope: checking {} path(s); "
                     "this does not prove global worktree cleanliness; "
                     "git add --renormalize is never automatic and requires human judgment".format(len(paths))))

    for path in paths:
        rel = path.replace("\\", "/")
        full = os.path.join(repo_root, rel)

        # Filesystem hash: git hash-object --no-filters
        fs_hash = None
        try:
            r = subprocess.run(
                ["git", "hash-object", "--no-filters", "--", rel],
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0 and r.stdout.strip():
                fs_hash = r.stdout.strip()
        except (FileNotFoundError, OSError):
            pass

        # Index hash: git rev-parse :path
        idx_hash = None
        try:
            r = subprocess.run(
                ["git", "rev-parse", ":{}".format(rel)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0 and r.stdout.strip():
                idx_hash = r.stdout.strip()
        except (FileNotFoundError, OSError):
            pass

        # Scoped porcelain status for this path
        status_clean = False
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain", "--", rel],
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0:
                status_clean = not r.stdout.strip()
        except (FileNotFoundError, OSError):
            pass

        if fs_hash is None and idx_hash is None:
            findings.append((WARN, "hash-check",
                             "{}: could not compute filesystem or index hash".format(rel)))
            continue

        if fs_hash and idx_hash:
            if fs_hash != idx_hash:
                if status_clean:
                    findings.append((WARN, "hash-check",
                                     "{}: filesystem hash {} differs from index {} "
                                     "but porcelain status is empty (possible stat-cache/index mismatch). "
                                     "Next read-only check: git diff --no-ext-diff -- {}; "
                                     "git add --renormalize is never automatic and requires human judgment".format(
                                         rel, fs_hash[:12], idx_hash[:12], rel)))
                else:
                    findings.append((INFO, "hash-check",
                                     "{}: filesystem hash {} differs from index {} (status shows changes)".format(
                                         rel, fs_hash[:12], idx_hash[:12])))
            else:
                findings.append((INFO, "hash-check",
                                 "{}: filesystem and index hashes match ({})".format(
                                     rel, fs_hash[:12])))
        elif fs_hash:
            findings.append((INFO, "hash-check",
                             "{}: filesystem hash {} (not in index)".format(rel, fs_hash[:12])))
        elif idx_hash:
            findings.append((INFO, "hash-check",
                             "{}: index hash {} (file missing from filesystem)".format(rel, idx_hash[:12])))

    return findings


def run_doctor(repo_path=None, hash_paths=None):
    """Run all checks. Returns (findings, has_error).

    Each finding is (level, category, message).
    """
    findings = []
    has_error = False
    hash_paths = hash_paths or []

    # 1. Repository root / git availability
    start = repo_path or os.getcwd()
    root = _find_repo_root(start)
    if root is None:
        findings.append((ERROR, "repo", "No .git found from {}".format(start)))
        has_error = True
        # Can't continue without a repo
        return findings, has_error

    findings.append((INFO, "repo", "Repository root: {}".format(root)))

    missing_workflow = _missing_project_workflow_files(root)
    if missing_workflow:
        has_error = True
        # Separate documented runtime helpers from other missing files
        missing_helpers = [f for f in missing_workflow if f in WORKFLOW_RUNTIME_HELPERS]
        missing_other = [f for f in missing_workflow if f not in WORKFLOW_RUNTIME_HELPERS]
        if missing_other:
            shown = ", ".join(missing_other[:6])
            if len(missing_other) > 6:
                shown += ", ..."
            findings.append((ERROR, "workflow",
                             "Project workflow is not bootstrapped; missing: {}".format(shown)))
        if missing_helpers:
            shown_h = ", ".join(missing_helpers[:6])
            if len(missing_helpers) > 6:
                shown_h += ", ..."
            findings.append((ERROR, "workflow-helpers",
                             "Documented runtime helpers missing: {}".format(shown_h)))
        findings.append((INFO, "workflow",
                         "Bootstrap/refresh command: {}".format(
                             _workflow_bootstrap_command(root, update_workflow_files=True))))
    else:
        findings.append((INFO, "workflow", "Project workflow files are installed"))
        outdated_workflow = _outdated_project_workflow_files(root)
        if outdated_workflow:
            shown = ", ".join(outdated_workflow[:6])
            if len(outdated_workflow) > 6:
                shown += ", ..."
            findings.append((WARN, "workflow-version", "Local workflow files differ from installed skill: {}".format(shown)))
            findings.append((INFO, "workflow-version", "Refresh command: {}".format(_workflow_bootstrap_command(root, update_workflow_files=True))))
        else:
            findings.append((INFO, "workflow-version", "Local workflow files match the installed skill"))

    git_path, git_version = _git_available()
    if git_path is None:
        findings.append((ERROR, "git", "git not found in PATH or common locations"))
        has_error = True
    else:
        findings.append((INFO, "git", "git available: {}".format(git_version)))

    # 2. Git worktree dirty state
    dirty = _git_worktree_dirty(root)
    if dirty:
        findings.append((WARN, "dirty", "Source worktree has {} uncommitted change(s)".format(len(dirty))))
    else:
        findings.append((INFO, "dirty", "Source worktree is clean"))

    # 3. Runtime artifact inventory
    inv = _inventory_worktrees(root)
    wt_count = inv["entry_count"]

    if inv["error"]:
        findings.append((WARN, "worktrees-inventory", inv["error"]))

    if wt_count == 0:
        findings.append((INFO, "artifacts", ".worktrees/ is clean (no runtime entries)"))
    else:
        size_label = _format_bytes(inv["approximate_bytes"])
        if inv["partial"]:
            size_label += " (approximate; traversal capped at {} nodes)".format(_WORKTREES_MAX_SIZE_NODES)
        findings.append((INFO, "worktrees-inventory",
                         ".worktrees/ has {} entries, {} total".format(wt_count, size_label)))

        oldest_age = inv["oldest_age_days"]
        if inv["oldest_path"]:
            findings.append((INFO, "worktrees-inventory",
                             "Oldest: {} ({:.0f} days)".format(
                                 os.path.basename(inv["oldest_path"]), oldest_age)))

        buckets = inv["buckets"]
        findings.append((INFO, "worktrees-inventory",
                         "Age buckets: <7d={}, 7-30d={}, >30d={}".format(
                             buckets["<7"], buckets["7-30"], buckets[">30"])))
        findings.append((INFO, "artifacts",
                         "Doctor never deletes automatically. To preview cleanup: python ai/clean_runtime.py"))

        # Cleanup suggestions when thresholds are exceeded (preview only)
        GIB = 1024 * 1024 * 1024
        needs_cleanup = wt_count >= 100 or inv["approximate_bytes"] >= GIB or oldest_age >= 30
        if needs_cleanup:
            if wt_count >= 100:
                findings.append((INFO, "artifacts",
                                 "High entry count ({}). Consider cleaning stale worktrees by task ID or age.".format(wt_count)))
            if inv["approximate_bytes"] >= GIB:
                findings.append((INFO, "artifacts",
                                 "Disk usage is large ({}). Run 'python ai/clean_runtime.py' to preview reclaimable space.".format(size_label)))
            if oldest_age >= 30:
                findings.append((INFO, "artifacts",
                                 "Oldest entry is {:.0f} days old. Consider removing entries older than 30 days.".format(oldest_age)))

    tmp_count = _count_runtime_artifacts(root)[1]
    if tmp_count > 0:
        findings.append((INFO, "artifacts", "Root has {} tmp-* artifact(s)".format(tmp_count)))
        findings.append((INFO, "artifacts",
                         "Doctor never deletes automatically. "
                         "To preview cleanup: python ai/clean_runtime.py"))

    ignore_ok, ignore_message = _worktrees_ignore_status(root)
    if ignore_ok:
        findings.append((INFO, "worktrees-ignore", ignore_message))
    else:
        findings.append((WARN, "worktrees-ignore", "{}. Add '/.worktrees/*' and '!/.worktrees/.gitkeep' or rerun the installer.".format(ignore_message)))

    tracked_count = _tracked_file_count(root)
    if tracked_count is not None:
        if tracked_count >= 10000:
            findings.append((WARN, "large-repo",
                             "{} tracked files; dispatch worktree creation may be slow. "
                             "Fill Worktree / Large Repo Strategy Gate before dispatch.".format(tracked_count)))
            findings.append((INFO, "large-repo",
                             "Before dispatch, run ai/locate-code.py for low-token candidates when targets are "
                             "unclear, then fill Claude Context Packet with target files, relevant symbols, "
                             "source-of-truth examples, forbidden paths, constraints, and narrow validation commands."))
            findings.append((INFO, "large-repo",
                             "Use Spark task-size-classifier for uncertain scope before spending "
                             "stronger-model context: bash ai/run-codex-spark.sh <task-card>"))
            findings.append((INFO, "codegraph",
                             "For large repositories, prefer ai/locate-code.py. Use CodeGraph only for concrete "
                             "files/symbols with a short timeout; if it times out, record it once and continue "
                             "with locator output plus targeted line reads."))
            # Fast-large-repo / reuse is conditional on:
            #   low risk, exact targets, serial safety, and accepted evidence reduction.
            # Otherwise use fresh/full dispatch.
            findings.append((INFO, "large-repo",
                             "fast-large-repo or reuse dispatch is recommended only when all of: "
                             "low risk, exact targets, serial safety, and accepted evidence reduction. "
                             "Otherwise prefer fresh/full dispatch."))
            findings.append((INFO, "large-repo",
                             "Fast dispatch when the evidence tradeoff is acceptable: "
                             "CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo bash ai/dispatch-to-claude.sh <task-card>"))
            findings.append((INFO, "large-repo",
                             "Manual knobs: CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed and "
                             "CLAUDE_CODE_LARGE_REPO_MODE=1; reset only .worktrees/reuse/claude-managed with "
                             "CLAUDE_CODE_REUSE_WORKTREE_RESET=1 after preserving evidence."))
            findings.append((INFO, "large-repo",
                             "Exact mechanical Builder tasks may use "
                             "CLAUDE_CODE_BUILDER_MODE=execution-only. A clean no-diff same-task retry may use "
                             "CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID=<prior-task-id>."))
            if tracked_count >= 50000:
                findings.append((WARN, "large-repo",
                                 "{} tracked files is very large; prefer local-only workflow bootstrap. "
                                 "Managed reuse remains conditional on the low-risk/exact-target/serial/evidence gate above.".format(tracked_count)))
                findings.append((INFO, "large-repo",
                                 "Suggested local-only bootstrap for business repositories: "
                                 "python scripts/install_workflow.py . --local-only"))
                findings.append((INFO, "large-repo",
                                 "Suggested repeated-dispatch profile: "
                                 "CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed "
                                 "CLAUDE_CODE_LARGE_REPO_MODE=1 "
                                 "CLAUDE_CODE_EVIDENCE_MODE=summary "
                                 "bash ai/dispatch-to-claude.sh <task-card>"))
        else:
            findings.append((INFO, "large-repo", "{} tracked files".format(tracked_count)))

    # 4. Bash resolution
    bash_path, bash_label = _resolve_bash()
    if bash_path is None:
        findings.append((WARN, "bash", bash_label))
    else:
        findings.append((INFO, "bash", "{}: {}".format(bash_label, bash_path)))
        # Warn if it might be WSL on Windows
        if sys.platform == "win32" and "may be WSL" in bash_label:
            findings.append((WARN, "bash", "bash resolves to WSL; dispatch may fail without a WSL distro"))

    # 5. Claude CLI availability
    claude_path = _check_claude_cli()
    if claude_path is None:
        findings.append((WARN, "claude", "Claude CLI not found in PATH"))
    else:
        findings.append((INFO, "claude", "Claude CLI: {}".format(claude_path)))

    # 6. Proxy environment variables
    proxy_vars = _check_proxy_vars()
    if proxy_vars:
        for name, masked in proxy_vars:
            findings.append((INFO, "proxy", "{}={}".format(name, masked)))
    else:
        findings.append((INFO, "proxy", "No common proxy environment variables set"))

    # 7. Codex skill path
    skill_path = _check_codex_skill()
    if skill_path is None:
        findings.append((WARN, "codex-skill", "ai-coding-workflow skill not found in ~/.codex/skills/"))
    elif "missing" in skill_path:
        findings.append((WARN, "codex-skill", skill_path))
    else:
        findings.append((INFO, "codex-skill", "Skill installed: {}".format(skill_path)))

    # 8. Context tools (LSP/linting availability)
    ctx_tools = _check_context_tools()
    found = [name for name, ok in ctx_tools if ok]
    missing = [name for name, ok in ctx_tools if not ok]
    if found:
        findings.append((INFO, "context-tools", "Available: {}".format(", ".join(found))))
    if missing:
        findings.append((WARN, "context-tools",
                         "Missing: {}. Run 'python ai/install_context_tools.py' for details. "
                         "Note: installing binaries does NOT guarantee Codex LSP/codegraph exposure.".format(
                             ", ".join(missing))))
    elif found:
        findings.append((INFO, "context-tools",
                         "All checked context tools are available. "
                         "Note: installing binaries does NOT guarantee Codex LSP/codegraph exposure."))

    # 9. Optional indexed code-search backends
    for label, message in _check_code_search_services():
        findings.append((INFO, label, message))
    findings.append((INFO, "code-search", "Run 'python ai/code-search-service.py doctor' for Zoekt/Sourcegraph setup details."))

    # 10. Hash-path diagnostics (target-only scope)
    if hash_paths:
        findings.extend(_hash_path_diagnostics(root, hash_paths))

    return findings, has_error


def format_findings(findings):
    """Format findings into a readable report."""
    lines = []
    lines.append("=== Workflow Doctor ===")
    lines.append("")

    for level, category, message in findings:
        prefix = {"ERROR": "ERROR", "WARN": "WARN", "INFO": "INFO"}.get(level, "INFO")
        lines.append("  {} [{}] {}".format(prefix, category, message))

    lines.append("")
    errors = [f for f in findings if f[0] == ERROR]
    warnings = [f for f in findings if f[0] == WARN]
    if errors:
        lines.append("Result: {} error(s), {} warning(s)".format(len(errors), len(warnings)))
    elif warnings:
        lines.append("Result: 0 errors, {} warning(s)".format(len(warnings)))
    else:
        lines.append("Result: All checks passed.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Check whether a repository is ready for the ai-coding-workflow dispatch/review loop."
    )
    parser.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        help="Repository path (default: current directory)",
    )
    parser.add_argument(
        "--hash-path",
        action="append",
        default=[],
        dest="hash_paths",
        help="File path to check hash consistency (repeatable, max 20). "
             "Relative to repo root; rejects absolute, traversal, missing, or directory paths.",
    )
    args = parser.parse_args()

    # Validate --hash-path count
    if len(args.hash_paths) > 20:
        print("ERROR: --hash-path specified {} times; maximum is 20".format(len(args.hash_paths)))
        sys.exit(1)

    # Validate each --hash-path before running doctor
    repo_root = _find_repo_root(args.repo_path or os.getcwd())
    if repo_root is None:
        # run_doctor will report the missing repo error
        repo_root = args.repo_path or os.getcwd()

    validated_paths = []
    for hp in args.hash_paths:
        # Normalize separators
        hp_norm = hp.replace("\\", "/")
        ok, err = _validate_hash_path(hp_norm, repo_root)
        if not ok:
            print("ERROR: --hash-path: {}".format(err))
            sys.exit(1)
        validated_paths.append(hp_norm)

    findings, has_error = run_doctor(args.repo_path, hash_paths=validated_paths)
    print(format_findings(findings))
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
