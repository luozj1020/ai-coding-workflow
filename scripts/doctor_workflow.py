#!/usr/bin/env python3
"""
doctor_workflow.py  -  Check whether a repository is ready for the ai-coding-workflow dispatch/review loop.

Usage:
    python ai/doctor_workflow.py [repo-path]

Read-only diagnostics. Reports errors, warnings, and info.
Exit 0 when no hard errors are detected, non-zero otherwise.

Uses only the Python standard library.
"""

import glob
import os
import shutil
import subprocess
import sys

# --- Levels ---
ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


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
        for entry in os.listdir(worktrees_dir):
            if entry == ".gitkeep":
                continue
            worktree_count += 1

    tmp_count = 0
    for entry in glob.glob(os.path.join(repo_root, "tmp-*")):
        if os.path.isdir(entry) or os.path.isfile(entry):
            tmp_count += 1

    return worktree_count, tmp_count


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


def run_doctor(repo_path=None):
    """Run all checks. Returns (findings, has_error).

    Each finding is (level, category, message).
    """
    findings = []
    has_error = False

    # 1. Repository root / git availability
    start = repo_path or os.getcwd()
    root = _find_repo_root(start)
    if root is None:
        findings.append((ERROR, "repo", "No .git found from {}".format(start)))
        has_error = True
        # Can't continue without a repo
        return findings, has_error

    findings.append((INFO, "repo", "Repository root: {}".format(root)))

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

    # 3. Runtime artifact counts
    wt_count, tmp_count = _count_runtime_artifacts(root)
    if wt_count > 0:
        findings.append((INFO, "artifacts", ".worktrees/ has {} runtime entry/entries".format(wt_count)))
    else:
        findings.append((INFO, "artifacts", ".worktrees/ is clean (no runtime entries)"))
    if tmp_count > 0:
        findings.append((INFO, "artifacts", "Root has {} tmp-* artifact(s)".format(tmp_count)))

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
    repo_path = sys.argv[1] if len(sys.argv) > 1 else None
    findings, has_error = run_doctor(repo_path)
    print(format_findings(findings))
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
