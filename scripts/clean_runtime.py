#!/usr/bin/env python3
"""
clean_runtime.py  -  Preview and remove ignored runtime artifacts from a repository.

Usage:
    python scripts/clean_runtime.py [repo-path]           # dry-run: list candidates
    python scripts/clean_runtime.py [repo-path] --apply    # delete candidates
    python scripts/clean_runtime.py [repo-path] --task-id claude-20260709-120000

Targets only runtime artifacts that are ignored by git:
    - .worktrees/* except .gitkeep
    - root tmp-* directories/files
    - stale task-cards/ directory (if ignored by .gitignore)

Never deletes tracked files. Uses only the Python standard library.
"""

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile


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


def _git_ignored_paths(repo_root, paths):
    """Return ignored paths using one Git process for the whole scan."""
    paths = list(paths)
    if not paths:
        return set()
    rels = [os.path.relpath(path, repo_root).replace(os.sep, "/") for path in paths]
    try:
        r = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=repo_root,
            input=("\0".join(rels) + "\0").encode("utf-8"),
            capture_output=True,
        )
        ignored = {
            value.decode("utf-8", errors="replace").rstrip("/")
            for value in r.stdout.split(b"\0") if value
        }
        return {
            _canon_path(path) for path, rel in zip(paths, rels)
            if rel.rstrip("/") in ignored
        }
    except FileNotFoundError:
        return set()


def _tracked_paths(repo_root):
    """Read the repository tracked-path index once."""
    try:
        r = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            capture_output=True,
        )
        if r.returncode != 0:
            return set()
        return {
            value.decode("utf-8", errors="replace")
            for value in r.stdout.split(b"\0") if value
        }
    except FileNotFoundError:
        return set()


def _is_tracked(repo_root, path, tracked):
    rel = os.path.relpath(path, repo_root).replace(os.sep, "/").rstrip("/")
    return rel in tracked or any(value.startswith(rel + "/") for value in tracked)


def _process_running(pid_file):
    """Return True when a PID artifact points to a live process we can detect."""
    try:
        with open(pid_file, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return False
        pid = int(raw)
    except (OSError, ValueError):
        return False

    if sys.platform == "win32":
        return _windows_process_running(pid)

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _windows_process_running(pid):
    """Check process existence on Windows without sending a signal."""
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _canon_path(path):
    """Return a path form suitable for cross-platform equality checks."""
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


_PROCESS_IDENTITY_HELPER = None
_PROCESS_IDENTITY_HELPER_LOADED = False


def _load_process_identity_helper():
    global _PROCESS_IDENTITY_HELPER, _PROCESS_IDENTITY_HELPER_LOADED
    if _PROCESS_IDENTITY_HELPER_LOADED:
        return _PROCESS_IDENTITY_HELPER
    _PROCESS_IDENTITY_HELPER_LOADED = True
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "process-identity.py")
    if not os.path.isfile(helper):
        return None
    spec = importlib.util.spec_from_file_location("aiwf_cleanup_process_identity", helper)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PROCESS_IDENTITY_HELPER = module
    return _PROCESS_IDENTITY_HELPER


def _task_process_state(worktrees_dir, task_id):
    """Classify task liveness from identities, falling back only for legacy runs."""
    identity_paths = [
        os.path.join(worktrees_dir, "{}.{}.process.json".format(task_id, role))
        for role in ("dispatcher", "claude", "checker")
    ]
    identity_paths = [path for path in identity_paths if os.path.isfile(path)]
    if identity_paths:
        helper = _load_process_identity_helper()
        if helper is None:
            return "unknown"
        saw_unknown = False
        for path in identity_paths:
            role = os.path.basename(path)[len(task_id) + 1 : -len(".process.json")]
            try:
                with open(path, encoding="utf-8") as handle:
                    value = json.load(handle)
                state, _detail = helper.check(value, task_id, role)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                state = "invalid-identity"
            if state == "running-same-process":
                return "active"
            if state not in {"not-running", "pid-reused-or-foreign"}:
                saw_unknown = True
        return "unknown" if saw_unknown else "inactive"
    pid_file = os.path.join(worktrees_dir, task_id + ".pid")
    if os.path.isfile(pid_file):
        return "legacy-active" if _process_running(pid_file) else "inactive"
    return "inactive"


def _protected_worktree_prefixes(worktrees_dir):
    """Return task IDs that must not be cleaned and their liveness state."""
    protected = {}
    if not os.path.isdir(worktrees_dir):
        return protected
    for entry in os.listdir(worktrees_dir):
        if not entry.startswith("claude-") or not entry.endswith(".pid"):
            continue
        task_id = entry[:-4]
        state = _task_process_state(worktrees_dir, task_id)
        if state in {"active", "legacy-active", "unknown"}:
            protected[task_id] = state
    for entry in os.listdir(worktrees_dir):
        if not entry.startswith("claude-") or ".process.json" not in entry:
            continue
        task_id = entry.split(".", 1)[0]
        state = _task_process_state(worktrees_dir, task_id)
        if state in {"active", "unknown"}:
            protected[task_id] = state
    return protected


def _is_registered_worktree(path, registered_paths):
    """Return True for git worktrees using path and metadata evidence."""
    if _canon_path(path) in registered_paths:
        return True
    # Git worktree directories contain a .git file pointing to the common dir.
    return os.path.isfile(os.path.join(path, ".git"))


def collect_candidates(repo_root, task_id=None):
    """Collect runtime artifact candidates for cleanup.

    Returns list of (path, description, is_worktree) tuples.
    Only includes paths that are git-ignored and not tracked.
    *is_worktree* is True for directories that are registered git worktrees.

    When *task_id* is provided, only .worktrees/<task_id> and adjacent
    .worktrees/<task_id>.* artifacts are considered. Root tmp-* and legacy
    task-cards/ cleanup are intentionally skipped in that focused mode.
    """
    candidates = []
    wt_paths = _registered_worktree_paths(repo_root)
    tracked = _tracked_paths(repo_root)
    task_id = os.path.basename(task_id) if task_id else None

    # 1. .worktrees/* except .gitkeep
    worktrees_dir = os.path.join(repo_root, ".worktrees")
    if os.path.isdir(worktrees_dir):
        protected_prefixes = _protected_worktree_prefixes(worktrees_dir)
        reserved = {".session-store", "archive", "control-archive"}
        possible = []
        for entry in sorted(os.listdir(worktrees_dir)):
            if entry == ".gitkeep" or entry in reserved:
                continue
            if task_id and entry != task_id and not entry.startswith(task_id + "."):
                continue
            if any(entry == prefix or entry.startswith(prefix + ".") for prefix in protected_prefixes):
                continue
            full = os.path.join(worktrees_dir, entry)
            possible.append((entry, full))
        ignored = _git_ignored_paths(repo_root, (full for _entry, full in possible))
        for entry, full in possible:
            if _canon_path(full) in ignored and not _is_tracked(repo_root, full, tracked):
                is_wt = os.path.isdir(full) and _is_registered_worktree(full, wt_paths)
                candidates.append((full, ".worktrees/{}".format(entry), is_wt))

    if task_id:
        return candidates

    # 2. root tmp-*
    root_possible = []
    for entry in sorted(os.listdir(repo_root)):
        if not entry.startswith("tmp-"):
            continue
        full = os.path.join(repo_root, entry)
        root_possible.append((entry, full))
    ignored = _git_ignored_paths(repo_root, (full for _entry, full in root_possible))
    for entry, full in root_possible:
        if _canon_path(full) in ignored and not _is_tracked(repo_root, full, tracked):
            candidates.append((full, entry, False))

    # 3. stale task-cards/ if ignored
    task_cards_dir = os.path.join(repo_root, "task-cards")
    if os.path.isdir(task_cards_dir):
        if (_canon_path(task_cards_dir) in _git_ignored_paths(repo_root, [task_cards_dir])
                and not _is_tracked(repo_root, task_cards_dir, tracked)):
            candidates.append((task_cards_dir, "task-cards/", False))

    return candidates


def _registered_worktree_paths(repo_root):
    """Return the set of absolute paths for all git-registered worktrees."""
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return set()
    except FileNotFoundError:
        return set()

    paths = set()
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            wt_path = line[len("worktree "):]
            if not os.path.isabs(wt_path):
                wt_path = os.path.join(repo_root, wt_path)
            paths.add(_canon_path(wt_path))
    return paths


def _remove_worktree(repo_root, path):
    """Remove a registered git worktree without --force.

    Returns True on success, False if the worktree is dirty or otherwise
    unsafe to remove.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "remove", path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _remove_orphan_session_store(repo_root, task_id):
    """Remove a lineage session store only after its last worktree is gone."""
    root = os.path.join(repo_root, ".worktrees")
    runtime_path = os.path.join(root, task_id + ".runtime.json")
    try:
        with open(runtime_path, encoding="utf-8") as handle:
            runtime = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        runtime = {}
    lineage = os.path.basename(str(runtime.get("lineage_root_task_id") or task_id))
    if not lineage or lineage in {".", ".."}:
        return None
    store = os.path.join(root, ".session-store", lineage)
    if not os.path.isdir(store):
        return None
    try:
        runtime_names = [name for name in os.listdir(root) if name.endswith(".runtime.json")]
    except OSError:
        return None
    for name in runtime_names:
        other_task = name[: -len(".runtime.json")]
        if other_task == task_id:
            continue
        try:
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                other = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        other_lineage = str(other.get("lineage_root_task_id") or other_task)
        if other_lineage != lineage:
            continue
        other_worktree = str(other.get("worktree") or os.path.join(root, other_task))
        if os.path.isdir(other_worktree):
            return None
    shutil.rmtree(store)
    return store


def remove_path(path):
    """Remove a file or directory tree."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


CONTROL_NAMES = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md", "CLAUDE_PROMPT.md",
    "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md", "ADVISOR_REQUEST.json",
}


def cleanup_eligibility(repo_root, worktree):
    """Assess terminal + merged + product-clean eligibility without deleting."""
    task_id = os.path.basename(worktree)
    reasons = []
    status = subprocess.run(
        ["git", "-C", worktree, "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    product_changes = []
    if status.returncode != 0:
        reasons.append("status-unavailable")
    else:
        for line in status.stdout.splitlines():
            path = line[3:].split(" -> ")[-1]
            if os.path.basename(path) not in CONTROL_NAMES:
                product_changes.append(path)
        if product_changes:
            reasons.append("product-worktree-dirty")

    head = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "HEAD"], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    merged = bool(head) and subprocess.run(
        ["git", "-C", repo_root, "merge-base", "--is-ancestor", head, "HEAD"],
        capture_output=True,
    ).returncode == 0
    if not merged:
        reasons.append("head-not-merged-into-current-head")

    terminal = False
    outcome_name = None
    terminal_receipt = None
    terminal_receipt_hash = None
    for suffix in ("outcome.json", "result.json", "abnormal-exit.json"):
        path = os.path.join(repo_root, ".worktrees", task_id + "." + suffix)
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            continue
        outcome_name = value.get("dispatch_outcome") or value.get("status")
        if outcome_name and outcome_name not in {"running", "started", "pending"}:
            terminal = True
            terminal_receipt = os.path.relpath(path, repo_root).replace(os.sep, "/")
            with open(path, "rb") as handle:
                terminal_receipt_hash = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
            break
    if not terminal:
        reasons.append("terminal-receipt-missing")
    repo_head = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "HEAD"], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    status_hash = "sha256:" + hashlib.sha256(
        (status.stdout if status.returncode == 0 else "<unavailable>").encode("utf-8")
    ).hexdigest()
    process_state = _task_process_state(os.path.join(repo_root, ".worktrees"), task_id)
    if process_state in {"active", "legacy-active"}:
        reasons.append("task-process-active")
    elif process_state == "unknown":
        reasons.append("task-process-visibility-unknown")
    binding = {
        "task_id": task_id,
        "worktree": _canon_path(worktree),
        "worktree_head": head or None,
        "repository_head": repo_head or None,
        "worktree_status_hash": status_hash,
        "terminal_receipt": terminal_receipt,
        "terminal_receipt_hash": terminal_receipt_hash,
        "process_state": process_state,
    }
    state_binding = "sha256:" + hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "kind": "cleanup-eligibility",
        "task_id": task_id,
        "eligible": not reasons,
        "reasons": reasons,
        "terminal": terminal,
        "dispatch_outcome": outcome_name,
        "head": head or None,
        "repository_head": repo_head or None,
        "head_merged_into_current_head": merged,
        "untracked_or_modified_product_paths": product_changes,
        "worktree_status_hash": status_hash,
        "terminal_receipt": terminal_receipt,
        "terminal_receipt_hash": terminal_receipt_hash,
        "process_state": process_state,
        "state_binding": state_binding,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


def cleanup_receipt_state(repo_root, worktree, current=None):
    """Return valid/missing/stale for the task's cleanup eligibility receipt."""
    task_id = os.path.basename(worktree)
    path = os.path.join(repo_root, ".worktrees", task_id + ".cleanup-eligible.json")
    try:
        with open(path, encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return "missing", path
    current = current or cleanup_eligibility(repo_root, worktree)
    if (not receipt.get("eligible") or not current.get("eligible")
            or receipt.get("task_id") != task_id
            or receipt.get("state_binding") != current.get("state_binding")):
        return "stale", path
    return "valid", path


def write_cleanup_eligibility(repo_root, value):
    task_id = value["task_id"]
    output = os.path.join(repo_root, ".worktrees", task_id + ".cleanup-eligible.json")
    payload = dict(value)
    payload["receipt_hash_basis"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    descriptor, temporary = tempfile.mkstemp(prefix="." + task_id, dir=os.path.dirname(output))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Preview and remove ignored runtime artifacts."
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="Repository path (default: current directory)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete candidates (default is dry-run)",
    )
    parser.add_argument(
        "--task-id",
        help=(
            "Limit cleanup to .worktrees/<task-id> and adjacent "
            ".worktrees/<task-id>.* artifacts"
        ),
    )
    parser.add_argument(
        "--mark-cleanup-eligible",
        action="store_true",
        help="Write receipts only for worktrees proven terminal, merged, and product-clean.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable cleanup preview (cannot be combined with --apply).",
    )
    args = parser.parse_args()

    if args.json and args.apply:
        parser.error("--json cannot be combined with --apply")

    repo_root = _find_repo_root(args.repo)
    if repo_root is None:
        print("ERROR: No .git found from {}".format(os.path.abspath(args.repo)))
        sys.exit(1)

    candidates = collect_candidates(repo_root, task_id=args.task_id)

    eligibility = {}
    receipt_states = {}
    for path, _desc, is_wt in candidates:
        if is_wt:
            eligibility[path] = cleanup_eligibility(repo_root, path)
            receipt_states[path] = cleanup_receipt_state(
                repo_root, path, eligibility[path]
            )[0]
    if args.mark_cleanup_eligible:
        written = []
        for value in eligibility.values():
            if value["eligible"]:
                written.append(write_cleanup_eligibility(repo_root, value))
        if written:
            for path in written:
                print("Marked cleanup eligible: {}".format(os.path.relpath(path, repo_root)))
            for path, _desc, is_wt in candidates:
                if is_wt:
                    receipt_states[path] = cleanup_receipt_state(
                        repo_root, path, eligibility[path]
                    )[0]
        else:
            print("No worktrees satisfy terminal + merged + product-clean eligibility.")
        if not args.apply:
            return

    if not candidates:
        if args.task_id:
            print("No runtime artifacts found for task id: {}".format(os.path.basename(args.task_id)))
        else:
            print("No runtime artifacts found.")
        sys.exit(0)

    if args.json:
        rows = []
        for path, desc, is_wt in candidates:
            value = eligibility.get(path)
            rows.append({
                "path": desc,
                "kind": "worktree" if is_wt else "artifact",
                "cleanup_eligible": value.get("eligible") if value else None,
                "reasons": value.get("reasons", []) if value else [],
                "receipt_state": receipt_states.get(path),
            })
        print(json.dumps({"schema_version": 1, "candidates": rows}, sort_keys=True))
        return

    if args.apply:
        if args.task_id:
            print(
                "Removing {} runtime artifact(s) for task id {}:".format(
                    len(candidates),
                    os.path.basename(args.task_id),
                )
            )
        else:
            print("Removing {} runtime artifact(s):".format(len(candidates)))
        blocked_tasks = {
            os.path.basename(path) for path, _desc, is_wt in candidates
            if is_wt and receipt_states.get(path) != "valid"
        }
        failed_tasks = set()
        for path, desc, is_wt in candidates:
            entry = os.path.basename(path)
            blocked_owner = next(
                (task for task in blocked_tasks | failed_tasks
                 if entry == task or entry.startswith(task + ".")),
                None,
            )
            if blocked_owner:
                reason = receipt_states.get(
                    os.path.join(repo_root, ".worktrees", blocked_owner), "missing"
                )
                print("  skipped: {} (task bundle requires a valid cleanup-eligible receipt; {})".format(desc, reason))
                continue
            if is_wt:
                current_receipt_state, _receipt = cleanup_receipt_state(
                    repo_root, path
                )
                if current_receipt_state != "valid":
                    failed_tasks.add(os.path.basename(path))
                    print("  skipped: {} (cleanup-eligible receipt changed before removal; {})".format(
                        desc, current_receipt_state
                    ))
                    continue
                if _remove_worktree(repo_root, path):
                    print("  removed: {} (worktree)".format(desc))
                    removed_store = _remove_orphan_session_store(
                        repo_root, os.path.basename(path)
                    )
                    if removed_store:
                        print("  removed: {} (orphan session store)".format(
                            os.path.relpath(removed_store, repo_root)
                        ))
                else:
                    failed_tasks.add(os.path.basename(path))
                    print(
                        "  skipped: {} (worktree is dirty or has unmerged"
                        " changes; use 'git worktree remove' manually)".format(desc)
                    )
            else:
                try:
                    remove_path(path)
                    print("  removed: {}".format(desc))
                except OSError as e:
                    print("  FAILED: {} ({})".format(desc, e))
    else:
        if args.task_id:
            print(
                "Dry-run: {} runtime artifact(s) for task id {} would be removed:".format(
                    len(candidates),
                    os.path.basename(args.task_id),
                )
            )
        else:
            print("Dry-run: {} runtime artifact(s) would be removed:".format(len(candidates)))
        for path, desc, is_wt in candidates:
            if is_wt:
                value = eligibility.get(path, {})
                state = "yes" if value.get("eligible") else "no:" + ",".join(value.get("reasons", []))
                label = "{} (worktree; cleanup-eligible={}; receipt={})".format(
                    desc, state, receipt_states.get(path, "missing")
                )
            else:
                label = desc
            print("  {}".format(label))
        print("\nRun with --apply to delete.")


if __name__ == "__main__":
    main()
