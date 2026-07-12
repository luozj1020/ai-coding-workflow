#!/usr/bin/env python3
"""Generate a preview-only manual publish/remote validation handoff.

Emits: local-precheck.sh, local-publish.sh, remote-update.sh,
       remote-validate.sh, handoff.md, manifest.json

All scripts are preview-only (echo commands, no execution).
Default: no automatic push, no automatic SSH.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import List, Optional


def safe_id(value: str) -> str:
    """Validate task-id contains only safe characters."""
    if not value or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for c in value
    ):
        raise ValueError("task-id contains unsafe characters")
    return value


def sha_valid(sha: str) -> bool:
    """Check if sha looks like a valid git SHA."""
    return (
        7 <= len(sha) <= 64
        and all(c in "0123456789abcdefABCDEF" for c in sha)
    )


def emit_local_precheck(
    task_id: str,
    branch: str,
    sha: str,
    changed_files: List[str],
    targets: List[str],
) -> str:
    """Emit local-precheck.sh with status/diff-stat/diff-check."""
    q = shlex.quote
    file_list = " ".join(q(f) for f in changed_files) if changed_files else "<reviewed-files>"
    target_list = " ".join(q(t) for t in targets) if targets else "<targets>"

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Local precheck for task: {task_id}",
        f"# Branch: {branch}",
        f"# Expected SHA: {sha}",
        "",
        "# Status check",
        "echo '=== Git Status ==='",
        "git status --short",
        "echo",
        "",
        "# Diff stat",
        "echo '=== Diff Stat ==='",
        f"git diff --stat HEAD~1..HEAD 2>/dev/null || echo '(no prior commit for diff stat)'",
        "echo",
        "",
        "# Diff check (whitespace errors)",
        "echo '=== Diff Check ==='",
        "if git diff --check HEAD~1..HEAD 2>/dev/null; then",
        "  echo 'Diff check: PASSED'",
        "else",
        "  echo 'Diff check: FAILED (whitespace errors found)'",
        "  exit 1",
        "fi",
        "echo",
        "",
        "# Changed files review",
        "echo '=== Changed Files ==='",
        f"echo 'Files to add: {file_list}'",
        "echo",
        "",
        "# SHA verification",
        "echo '=== SHA Verification ==='",
        f"ACTUAL=$(git rev-parse HEAD)",
        f"EXPECTED={q(sha)}",
        'if [ "$ACTUAL" != "$EXPECTED" ]; then',
        '  echo "SHA mismatch: expected $EXPECTED, got $ACTUAL"',
        "  exit 1",
        "fi",
        "echo 'SHA verified: '$ACTUAL",
        "",
        "echo",
        "echo '=== Precheck Complete ==='",
    ]
    return "\n".join(lines) + "\n"


def emit_local_publish(
    task_id: str,
    branch: str,
    sha: str,
    changed_files: List[str],
) -> str:
    """Emit local-publish.sh with explicit add list, commit, and preview-only push."""
    q = shlex.quote
    file_list = " ".join(q(f) for f in changed_files) if changed_files else "<reviewed-files>"

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Local publish for task: {task_id}",
        f"# Branch: {branch}",
        f"# Expected SHA: {sha}",
        "",
        "# Preview mode: print commands only, do not execute",
        "",
        "# Stage reviewed files",
        f"echo 'git add {file_list}'",
        "",
        "# Commit with task reference",
        f"echo 'git commit -m \"task: {task_id}\"'",
        "",
        "# Preview-only push (reviewer must approve)",
        f"echo 'git push origin {q(branch)}'",
        "",
        "echo",
        "echo '=== Publish Preview Complete ==='",
        "echo 'Review the above commands before executing.'",
        "echo 'Default mode: preview only. No automatic push.'",
    ]
    return "\n".join(lines) + "\n"


def emit_remote_update(
    task_id: str,
    repo_url: str,
    branch: str,
    sha: str,
    server_repo_path: str,
) -> str:
    """Emit remote-update.sh with ssh/config placeholders, cd, dirty check,
    fetch, switch/checkout, pull --ff-only, strict SHA comparison."""
    q = shlex.quote

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Remote update for task: {task_id}",
        f"# Repository: {repo_url}",
        f"# Branch: {branch}",
        f"# Expected SHA: {sha}",
        "",
        "# SSH config placeholder — replace with actual SSH config",
        "# export GIT_SSH_COMMAND='ssh -F /path/to/ssh/config'",
        "",
        "# Navigate to server repo",
        f"echo 'cd {q(server_repo_path)}'",
        "",
        "# Dirty check — refuse to operate on dirty worktree",
        "echo 'if [ -n \"$(git status --porcelain)\" ]; then'",
        "echo '  echo \"ERROR: server worktree is dirty. Aborting.\"'",
        "echo '  exit 1'",
        "echo 'fi'",
        "",
        "# Fetch latest",
        "echo 'git fetch origin'",
        "",
        "# Switch to target branch",
        f"echo 'git checkout {q(branch)}'",
        "",
        "# Fast-forward only merge",
        f"echo 'git merge --ff-only origin/{q(branch)}'",
        "",
        "# Strict SHA comparison",
        "echo 'ACTUAL=$(git rev-parse HEAD)'",
        f"echo 'EXPECTED={q(sha)}'",
        'echo \'if [ "$ACTUAL" != "$EXPECTED" ]; then\'',
        'echo \'  echo "SHA mismatch: expected $EXPECTED, got $ACTUAL"\'',
        "echo '  exit 1'",
        "echo 'fi'",
        "",
        "# Environment fingerprint",
        "echo 'echo \"Environment fingerprint:\"'",
        "echo 'echo \"  OS: $(uname -s -r)\"'",
        "echo 'echo \"  Git: $(git --version)\"'",
        "echo 'echo \"  Bazel: $(bazel --version 2>/dev/null || echo not-installed)\"'",
        "echo 'echo \"  Python: $(python3 --version 2>/dev/null || echo not-installed)\"'",
        "echo 'echo \"  SHA: $(git rev-parse HEAD)\"'",
        "",
        "echo",
        "echo '=== Remote Update Preview Complete ==='",
        "echo 'Review the above commands before executing on remote.'",
    ]
    return "\n".join(lines) + "\n"


def emit_remote_validate(
    task_id: str,
    repo_url: str,
    branch: str,
    sha: str,
    targets: List[str],
    server_repo_path: str,
    conda_env: Optional[str],
    log_path: str,
) -> str:
    """Emit remote-validate.sh with pipefail, tee, PIPESTATUS and .exit-code.
    Includes exact Bazel targets."""
    q = shlex.quote

    bazel_cmd = ""
    if targets:
        target_str = " ".join(q(t) for t in targets)
        bazel_cmd = f"bazel test {target_str} --test_output=errors"

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Remote validate for task: {task_id}",
        f"# Repository: {repo_url}",
        f"# Branch: {branch}",
        f"# Expected SHA: {sha}",
        "",
        "# Navigate to server repo",
        f"cd {q(server_repo_path)}",
        "",
        "# Conda environment activation (if needed)",
    ]

    if conda_env:
        lines.append(f"conda activate {q(conda_env)}")
    else:
        lines.append("# No conda environment specified")

    lines += [
        "",
        "# Run Bazel validation with tee and PIPESTATUS capture",
        "EXIT_CODE=0",
    ]

    if bazel_cmd:
        lines += [
            f"{bazel_cmd} 2>&1 | tee {q(log_path)}",
            "EXIT_CODE=${PIPESTATUS[0]}",
        ]
    else:
        lines += [
            "echo 'No Bazel targets specified. Skipping validation.'",
            "echo 'exit=0' > " + q(log_path),
        ]

    lines += [
        "",
        "# Write exit code to .exit-code file",
        'echo "$EXIT_CODE" > .exit-code',
        "",
        "# Report result",
        'if [ "$EXIT_CODE" -eq 0 ]; then',
        "  echo 'Validation: PASSED'",
        "else",
        "  echo \"Validation: FAILED (exit code $EXIT_CODE)\"",
        "fi",
        "",
        "exit $EXIT_CODE",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Generate a preview-only manual publish/remote validation handoff."
    )
    p.add_argument("task_id", type=safe_id)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--repo-url", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--changed-file", action="append", default=[])
    p.add_argument("--server-repo-path", default="<server-repo-path>")
    p.add_argument("--log-path", default="validation.log")
    p.add_argument("--conda-env")
    p.add_argument(
        "--validation-state",
        choices=["local-runnable", "remote-required", "skipped-by-policy"],
        default="remote-required",
    )
    a = p.parse_args()

    if not sha_valid(a.sha):
        p.error("--sha must be a git SHA (7-64 hex chars)")

    out = Path(a.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Build Bazel commands
    commands = []
    if a.target:
        target_str = " ".join(shlex.quote(x) for x in a.target)
        commands.append(f"bazel test {target_str} --test_output=errors")

    # Build manifest
    manifest = {
        "schema_version": 1,
        "task_id": a.task_id,
        "repository": {
            "url": a.repo_url,
            "branch": a.branch,
            "sha": a.sha,
        },
        "validation": {
            "state": a.validation_state,
            "targets": a.target,
            "commands": commands,
        },
        "artifacts": [
            "local-precheck.sh",
            "local-publish.sh",
            "remote-update.sh",
            "remote-validate.sh",
            "handoff.md",
            "manifest.json",
        ],
    }

    # Generate all scripts
    files = {
        "local-precheck.sh": emit_local_precheck(
            a.task_id, a.branch, a.sha, a.changed_file, a.target
        ),
        "local-publish.sh": emit_local_publish(
            a.task_id, a.branch, a.sha, a.changed_file
        ),
        "remote-update.sh": emit_remote_update(
            a.task_id, a.repo_url, a.branch, a.sha, a.server_repo_path
        ),
        "remote-validate.sh": emit_remote_validate(
            a.task_id,
            a.repo_url,
            a.branch,
            a.sha,
            a.target,
            a.server_repo_path,
            a.conda_env,
            a.log_path,
        ),
        "handoff.md": (
            f"# Remote validation handoff: {a.task_id}\n"
            f"\n"
            f"Repository: `{a.repo_url}`\n"
            f"Branch: `{a.branch}`\n"
            f"Expected SHA: `{a.sha}`\n"
            f"Validation state: `{a.validation_state}`\n"
            f"Changed files: {', '.join(a.changed_file) or '(record before publish)'}\n"
            f"\n"
            f"## Scripts\n"
            f"\n"
            f"1. **local-precheck.sh** — Status, diff-stat, diff-check, SHA verification\n"
            f"2. **local-publish.sh** — Stage, commit, preview-only push\n"
            f"3. **remote-update.sh** — SSH placeholder, dirty check, fetch, checkout, ff-only merge, SHA comparison, environment fingerprint\n"
            f"4. **remote-validate.sh** — Bazel validation with pipefail, tee, PIPESTATUS, .exit-code\n"
            f"\n"
            f"All scripts are preview-only. Review and run them manually.\n"
            f"Ingest returned logs with `python scripts/validation-ingest.py {a.log_path} --expected-sha {a.sha}`.\n"
        ),
        "manifest.json": json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
    }

    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")

    print(out)


if __name__ == "__main__":
    main()
