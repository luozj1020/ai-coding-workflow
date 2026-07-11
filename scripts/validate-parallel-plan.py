#!/usr/bin/env python3
"""validate-parallel-plan.py - Strict stdlib validator/normalizer for schema-v1 DAG plans.

Validates a JSON plan file and emits tab-separated META/TASK records for safe
Bash consumption via `while IFS=$'\t' read -r ...`.  No eval/source of plan data.

Usage:
    python scripts/validate-parallel-plan.py --plan <json> [--output-mode tsv]

Exit codes:
    0  valid plan; META and TASK records written to stdout
    1  validation failed; errors written to stderr
    2  usage error (missing args, file not found, etc.)
"""

import json
import os
import re
import sys
from pathlib import Path

ALLOWED_META_KEYS = frozenset({
    "schema_version", "group_id", "max_concurrency", "failure_policy", "tasks",
})
ALLOWED_TASK_KEYS = frozenset({
    "id", "task_card", "depends_on",
})
VALID_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_FAILURE_POLICIES = frozenset({"skip-dependents"})
DEFAULT_MAX_CONCURRENCY = 2


def has_control_chars(s: str) -> bool:
    """Return True if *s* contains ASCII control characters."""
    for ch in s:
        code = ord(ch)
        if code < 0x20 and ch not in ("\t", "\n", "\r"):
            return True
        if code == 0x7F:
            return True
    return False


def has_tsv_unsafe_chars(s: str) -> bool:
    """Return True if *s* contains characters that would corrupt TSV transport.

    Tabs, newlines, and carriage returns break field boundaries in TSV output.
    """
    for ch in s:
        code = ord(ch)
        if ch in ("\t", "\n", "\r"):
            return True
        if code < 0x20 or code == 0x7F:
            return True
    return False


def validate_plan(plan_path: str) -> tuple[dict, list[str]]:
    """Validate a plan file. Returns (meta_dict, task_list) on success.

    Raises SystemExit(1) on validation failure with errors on stderr.
    Raises SystemExit(2) on usage errors.
    """
    errors: list[str] = []

    # --- Read and parse JSON ---
    plan_file = Path(plan_path)
    if not plan_file.is_file():
        print(f"Error: plan file not found: {plan_path}", file=sys.stderr)
        sys.exit(2)

    plan_dir = str(plan_file.parent.resolve())

    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in plan file: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(raw, dict):
        errors.append("plan must be a JSON object")
        _fail(errors)

    # --- Unknown top-level keys ---
    unknown_keys = set(raw.keys()) - ALLOWED_META_KEYS
    if unknown_keys:
        errors.append(f"unknown top-level keys: {sorted(unknown_keys)}")

    # --- schema_version ---
    schema_version = raw.get("schema_version")
    if schema_version is None:
        errors.append("missing required key: schema_version")
    elif not isinstance(schema_version, int) or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"unsupported schema_version: {schema_version!r} (expected 1)")

    # --- group_id ---
    group_id = raw.get("group_id", "")
    if not isinstance(group_id, str):
        errors.append("group_id must be a string")
    elif not group_id:
        errors.append("group_id must not be empty")
    elif has_tsv_unsafe_chars(group_id):
        errors.append("group_id contains control characters or TSV-unsafe characters (tab/newline)")
    elif not VALID_ID_RE.match(group_id):
        errors.append(f"group_id contains invalid characters: {group_id!r}")

    # --- max_concurrency ---
    max_concurrency = raw.get("max_concurrency", DEFAULT_MAX_CONCURRENCY)
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
        errors.append("max_concurrency must be an integer (not a boolean)")
    elif max_concurrency < 1:
        errors.append(f"max_concurrency must be >= 1, got {max_concurrency}")

    # --- failure_policy ---
    failure_policy = raw.get("failure_policy", "skip-dependents")
    if not isinstance(failure_policy, str):
        errors.append("failure_policy must be a string")
    elif failure_policy not in SUPPORTED_FAILURE_POLICIES:
        errors.append(f"unsupported failure_policy: {failure_policy!r} (expected 'skip-dependents')")

    # --- tasks ---
    tasks_raw = raw.get("tasks")
    if tasks_raw is None:
        errors.append("missing required key: tasks")
        _fail(errors)
    if not isinstance(tasks_raw, list):
        errors.append("tasks must be an array")
        _fail(errors)
    if len(tasks_raw) == 0:
        errors.append("tasks must not be empty")
        _fail(errors)

    # Validate each task
    seen_ids: set[str] = set()
    seen_cards: set[str] = set()
    all_ids: set[str] = set()
    tasks: list[dict] = []

    for idx, task in enumerate(tasks_raw):
        prefix = f"tasks[{idx}]"

        if not isinstance(task, dict):
            errors.append(f"{prefix}: must be a JSON object")
            continue

        unknown_task_keys = set(task.keys()) - ALLOWED_TASK_KEYS
        if unknown_task_keys:
            errors.append(f"{prefix}: unknown keys: {sorted(unknown_task_keys)}")

        # id
        task_id = task.get("id")
        if task_id is None:
            errors.append(f"{prefix}: missing required key: id")
        elif not isinstance(task_id, str):
            errors.append(f"{prefix}: id must be a string")
        elif not task_id:
            errors.append(f"{prefix}: id must not be empty")
        elif has_tsv_unsafe_chars(task_id):
            errors.append(f"{prefix}: id contains control characters or TSV-unsafe characters (tab/newline)")
        elif not VALID_ID_RE.match(task_id):
            errors.append(f"{prefix}: id contains invalid characters: {task_id!r}")
        elif task_id in seen_ids:
            errors.append(f"{prefix}: duplicate id: {task_id!r}")
        else:
            seen_ids.add(task_id)
            all_ids.add(task_id)

        # task_card
        task_card = task.get("task_card")
        resolved_task_card = ""
        if task_card is None:
            errors.append(f"{prefix}: missing required key: task_card")
        elif not isinstance(task_card, str):
            errors.append(f"{prefix}: task_card must be a string")
        elif not task_card:
            errors.append(f"{prefix}: task_card must not be empty")
        elif has_tsv_unsafe_chars(task_card):
            errors.append(f"{prefix}: task_card contains control characters or TSV-unsafe characters (tab/newline)")
        else:
            resolved = str(Path(plan_dir, task_card).resolve())
            resolved_task_card = resolved
            if not Path(resolved).is_file():
                errors.append(f"{prefix}: task_card not found: {task_card} (resolved: {resolved})")
            norm_card = os.path.normpath(task_card)
            if norm_card in seen_cards:
                errors.append(f"{prefix}: duplicate task_card: {task_card}")
            else:
                seen_cards.add(norm_card)

        # depends_on
        depends_on = task.get("depends_on", [])
        if not isinstance(depends_on, list):
            errors.append(f"{prefix}: depends_on must be an array")
        else:
            seen_deps: set[str] = set()
            for dep_idx, dep in enumerate(depends_on):
                if not isinstance(dep, str):
                    errors.append(f"{prefix}: depends_on[{dep_idx}] must be a string")
                elif not dep:
                    errors.append(f"{prefix}: depends_on[{dep_idx}] must not be empty")
                elif task_id is not None and dep == task_id:
                    errors.append(f"{prefix}: self-dependency: {dep}")
                elif dep in seen_deps:
                    errors.append(f"{prefix}: duplicate dependency: {dep!r}")
                else:
                    seen_deps.add(dep)

        tasks.append({
            "id": task_id or "",
            "task_card": task_card or "",
            "depends_on": depends_on if isinstance(depends_on, list) else [],
            # Resolve only after validation.  On Windows, pathlib raises before
            # the accumulated TSV-safety error can be reported for tabs/newlines.
            "resolved_task_card": resolved_task_card,
        })

    # Cross-task dependency validation (unknown deps, cycles)
    if errors:
        _fail(errors)

    for idx, task in enumerate(tasks):
        prefix = f"tasks[{idx}]"
        for dep in task["depends_on"]:
            if dep not in all_ids:
                errors.append(f"{prefix}: unknown dependency: {dep!r}")

    if errors:
        _fail(errors)

    # Cycle detection via DFS
    adj: dict[str, list[str]] = {}
    for task in tasks:
        adj[task["id"]] = list(task["depends_on"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {tid: WHITE for tid in all_ids}
    path: list[str] = []

    def dfs(node: str) -> bool:
        """Return True if a cycle is found."""
        color[node] = GRAY
        path.append(node)
        for neighbor in adj.get(node, []):
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                errors.append(f"dependency cycle: {' -> '.join(cycle)}")
                return True
            if color[neighbor] == WHITE:
                if dfs(neighbor):
                    return True
        path.pop()
        color[node] = BLACK
        return False

    for tid in all_ids:
        if color[tid] == WHITE:
            if dfs(tid):
                break

    if errors:
        _fail(errors)

    meta = {
        "schema_version": schema_version,
        "group_id": group_id,
        "max_concurrency": max_concurrency,
        "failure_policy": failure_policy,
        "plan_path": str(plan_file.resolve()),
        "plan_dir": plan_dir,
    }
    return meta, tasks


def _fail(errors: list[str]) -> None:
    """Print errors and exit with code 1."""
    for err in errors:
        print(f"Error: {err}", file=sys.stderr)
    sys.exit(1)


def emit_tsv(meta: dict, tasks: list[dict]) -> None:
    """Emit tab-separated META and TASK records to stdout.

    Format:
        META\t<key>\t<value>
        TASK\t<id>\t<task_card>\t<depends_on_csv>\t<resolved_task_card>
    """
    for key in ("schema_version", "group_id", "max_concurrency", "failure_policy", "plan_path", "plan_dir"):
        print(f"META\t{key}\t{meta[key]}")
    for task in tasks:
        deps_csv = ",".join(task["depends_on"]) or "__none__"
        print(f"TASK\t{task['id']}\t{task['task_card']}\t{deps_csv}\t{task['resolved_task_card']}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate a schema-v1 DAG plan.")
    parser.add_argument("--plan", required=True, help="Path to the JSON plan file.")
    parser.add_argument("--output-mode", default="tsv", choices=["tsv"],
                        help="Output format (default: tsv).")
    args = parser.parse_args()

    meta, tasks = validate_plan(args.plan)
    emit_tsv(meta, tasks)


if __name__ == "__main__":
    main()
