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

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Parallel Execution Gate field extraction from task-card markdown tables
# ---------------------------------------------------------------------------

def _normalize_field_name(name: str) -> str:
    """Normalize a markdown table field name for lookup."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def extract_gate_field(content: str, section: str, field: str) -> str:
    """Extract a value from a markdown table inside a named section.

    Looks for ``## <section>`` then a ``| Field | Value |`` table and returns
    the value for the row whose Field normalizes to *field*.
    """
    normalized_field = _normalize_field_name(field)
    lines = content.split("\n")
    in_section = False
    for line in lines:
        # Detect section heading
        if line.startswith("##"):
            heading = line.lstrip("#").strip()
            in_section = _normalize_field_name(heading) == _normalize_field_name(section)
            continue
        if in_section and line.startswith("|") and "---" not in line:
            parts = [p.strip() for p in line.split("|")]
            # parts[0] is empty (before first |), parts[1] is key, parts[2] is value
            if len(parts) >= 3:
                key = _normalize_field_name(parts[1])
                if key == normalized_field:
                    return parts[2].strip()
    return ""


def extract_scopes(raw: str) -> list[str]:
    """Split a comma/semicolon-delimited scope string into a normalized list."""
    tokens = re.split(r"[,;]+", raw)
    scopes = []
    seen: set[str] = set()
    for tok in tokens:
        s = tok.strip().strip("`*\"'")
        if s and s not in seen:
            seen.add(s)
            scopes.append(s)
    return scopes


def normalize_path(p: str) -> str:
    """Normalize a file/directory path for overlap comparison."""
    # Strip leading/trailing whitespace and quotes
    p = p.strip().strip("`*\"'")
    # Normalize separators and remove trailing slash
    p = p.replace("\\", "/").rstrip("/")
    # Normalize path components
    return os.path.normpath(p).replace("\\", "/")


def is_parent_or_child(a: str, b: str) -> bool:
    """Return True if path *a* is a parent of *b*, or *b* is a parent of *a*, or they are equal."""
    na = normalize_path(a)
    nb = normalize_path(b)
    if na == nb:
        return True
    # Check if one is a prefix (directory containment)
    # Add "/" to avoid "src/a" matching "src/abc"
    if na.startswith(nb + "/") or nb.startswith(na + "/"):
        return True
    return False

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


def validate_dispatch_constraints(
    plan_path: str,
    expected_base_commit: str | None = None,
    allow_write_overlap: bool = False,
) -> tuple[dict, list[dict], list[str]]:
    """Validate deterministic dispatch constraints on a plan and its task cards.

    Checks:
    - Write scope normalization: reject exact and parent/child overlap.
    - Owned contract overlap: reject non-empty contracts that collide.
    - Base commit: require a common explicit base commit; verify against expected if given.
    - Validation ownership: require explicit validation owner/command per card.
    - DAG cycle/dependency checks (delegated to validate_plan).

    Returns (meta, tasks_with_gate_fields, errors) where errors is empty on success.
    """
    errors: list[str] = []

    # First run the standard plan validation
    try:
        meta, tasks = validate_plan(plan_path)
    except SystemExit:
        # validate_plan already printed errors to stderr.
        # Re-raise so the caller sees the failure; no dispatch checks possible.
        raise

    # Extract gate fields from each task card
    task_gates: list[dict] = []
    for task in tasks:
        card_path = task["resolved_task_card"]
        if not card_path or not Path(card_path).is_file():
            errors.append(f"task {task['id']}: task card not found: {card_path}")
            task_gates.append({"id": task["id"], "scopes": [], "contracts": [], "base_commit": "", "validation_owner": "", "validation_command": ""})
            continue

        content = Path(card_path).read_text(encoding="utf-8", errors="replace")

        allowed = extract_gate_field(content, "Parallel Execution Gate", "Allowed files/modules")
        if not allowed:
            allowed = extract_gate_field(content, "Parallel Execution Gate", "Conflict files/modules")
        scopes = extract_scopes(allowed)

        contracts_raw = extract_gate_field(content, "Parallel Execution Gate", "Owned contracts")
        contracts = [c.strip() for c in re.split(r"[,;]+", contracts_raw) if c.strip()]

        base_commit = extract_gate_field(content, "Parallel Execution Gate", "Base commit").strip()
        validation_owner = extract_gate_field(content, "Parallel Execution Gate", "Validation owner").strip()
        validation_command = extract_gate_field(content, "Parallel Execution Gate", "Validation command").strip()

        task_gates.append({
            "id": task["id"],
            "scopes": scopes,
            "contracts": contracts,
            "base_commit": base_commit,
            "validation_owner": validation_owner,
            "validation_command": validation_command,
        })

    if errors:
        return meta, task_gates, errors

    # --- Check 0: Reject empty/placeholder/broad scopes ---
    PLACEHOLDER_SCOPES = frozenset({
        "", "tbd", "todo", "fixme", "placeholder", "none", "n/a", "na",
        "unknown", "unspecified", "auto", "default",
    })
    for tg in task_gates:
        if not tg["scopes"]:
            errors.append(
                f"task {tg['id']}: empty Allowed files/modules in Parallel Execution Gate "
                f"(every parallel card must declare a non-empty write scope)"
            )
            continue
        for scope in tg["scopes"]:
            scope_lower = scope.lower().strip()
            if scope_lower in PLACEHOLDER_SCOPES:
                errors.append(
                    f"task {tg['id']}: placeholder write scope '{scope}' "
                    f"(every parallel card must declare a real write scope)"
                )
            # Conservative broad-glob detection: warn when scope is a
            # top-level wildcard that could evade parent/child overlap.
            if scope.rstrip("/") in ("src", "lib", "app", "pkg", "internal", "cmd"):
                errors.append(
                    f"task {tg['id']}: overly broad write scope '{scope}' "
                    f"(use a more specific path to enable reliable overlap detection)"
                )

    if errors:
        return meta, task_gates, errors

    # --- Check 1: Write scope parent/child overlap ---
    if not allow_write_overlap:
        for i in range(len(task_gates)):
            for j in range(i + 1, len(task_gates)):
                ti = task_gates[i]
                tj = task_gates[j]
                for si in ti["scopes"]:
                    for sj in tj["scopes"]:
                        if is_parent_or_child(si, sj):
                            errors.append(
                                f"write scope overlap: task {ti['id']} scope '{si}' "
                                f"overlaps with task {tj['id']} scope '{sj}'"
                            )

    # --- Check 2: Owned contract overlap ---
    all_contracts: dict[str, str] = {}  # contract -> task_id
    for tg in task_gates:
        for contract in tg["contracts"]:
            if contract in all_contracts:
                errors.append(
                    f"owned contract overlap: contract '{contract}' is owned by "
                    f"both {all_contracts[contract]} and {tg['id']}"
                )
            else:
                all_contracts[contract] = tg["id"]

    # --- Check 3: Base commit consistency ---
    # Every parallel card must declare a non-placeholder base commit.
    PLACEHOLDER_BASE_COMMITS = frozenset({
        "", "abc123", "abc123def", "0000000", "0000000000000000000000000000000000000000",
        "HEAD", "main", "master", "base", "TBD", "tbd", "TODO", "todo",
        "placeholder", "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    })
    declared_commits: list[tuple[str, str]] = []  # (task_id, commit)
    for tg in task_gates:
        commit = tg["base_commit"]
        if not commit:
            errors.append(
                f"task {tg['id']}: missing Base commit in Parallel Execution Gate "
                f"(every parallel card must declare a non-placeholder base commit)"
            )
        elif commit.lower() in {p.lower() for p in PLACEHOLDER_BASE_COMMITS}:
            errors.append(
                f"task {tg['id']}: placeholder Base commit '{commit}' in Parallel Execution Gate "
                f"(every parallel card must declare a non-placeholder base commit)"
            )
        else:
            declared_commits.append((tg["id"], commit))

    if declared_commits:
        unique_commits = set(c for _, c in declared_commits)
        if len(unique_commits) > 1:
            details = ", ".join(f"{tid}={c}" for tid, c in declared_commits)
            errors.append(f"base commit mismatch across tasks: {details}")
        elif expected_base_commit:
            actual = declared_commits[0][1]
            if actual != expected_base_commit:
                errors.append(
                    f"base commit mismatch: declared {actual} but expected {expected_base_commit}"
                )

    # --- Check 4: Validation ownership ---
    for tg in task_gates:
        if not tg["validation_owner"] and not tg["validation_command"]:
            errors.append(
                f"task {tg['id']}: missing validation owner and validation command "
                f"in Parallel Execution Gate"
            )

    return meta, task_gates, errors


def topological_sort(tasks: list[dict]) -> list[dict]:
    """Return tasks in a stable topological order using Kahn's algorithm.

    Tasks with no dependencies come first. Among peers at the same level,
    order is deterministic (sorted by task id).
    """
    # Build adjacency and in-degree maps
    task_by_id: dict[str, dict] = {}
    in_degree: dict[str, int] = {}
    children: dict[str, list[str]] = {}

    for task in tasks:
        tid = task["id"]
        task_by_id[tid] = task
        in_degree[tid] = len(task.get("depends_on", []))
        children.setdefault(tid, [])

    for task in tasks:
        for dep in task.get("depends_on", []):
            children.setdefault(dep, []).append(task["id"])

    # Kahn's algorithm with sorted tie-breaking for determinism
    ready = sorted(tid for tid, deg in in_degree.items() if deg == 0)
    result: list[dict] = []

    while ready:
        tid = ready.pop(0)
        result.append(task_by_id[tid])
        next_ids = sorted(children.get(tid, []))
        for child_id in next_ids:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                # Insert in sorted position to maintain deterministic order
                ready.append(child_id)
                ready.sort()

    return result


def build_serial_fallback(meta: dict, tasks: list[dict], task_gates: list[dict]) -> str:
    """Build a serial fallback recommendation from plan metadata and task gates.

    Returns a concise human-readable recommendation with deterministic topological
    task order. The order is stable: prerequisites always precede dependents, and
    peers are sorted by task id.
    """
    sorted_tasks = topological_sort(tasks)
    # Build index map for dependency annotation
    lines = ["Serial fallback recommended. Deterministic task order:"]
    for i, task in enumerate(sorted_tasks):
        deps = task.get("depends_on", [])
        dep_str = f" (after {', '.join(deps)})" if deps else ""
        lines.append(f"  {i + 1}. {task['id']}{dep_str}")
    lines.append("")
    lines.append("Run tasks serially in the order above. Each task must complete "
                 "and be reviewed before starting the next.")
    return "\n".join(lines)


def _fail(errors: list[str]) -> None:
    """Print errors and exit with code 1."""
    for err in errors:
        print(f"Error: {err}", file=sys.stderr)
    sys.exit(1)


def validate_flat_dispatch_constraints(
    card_paths: list[str],
    expected_base_commit: str | None = None,
    allow_write_overlap: bool = False,
) -> tuple[dict, list[str]]:
    """Validate deterministic dispatch constraints on flat positional task cards.

    Performs the same checks as validate_dispatch_constraints but without
    requiring a plan file: normalized parent/child write overlap, owned-contract
    overlap, base commit agreement, validation ownership, and scope sanity.

    Returns (task_gates, errors) where errors is empty on success.
    """
    errors: list[str] = []

    task_gates: list[dict] = []
    for idx, card_path in enumerate(card_paths):
        resolved = str(Path(card_path).resolve())
        if not Path(resolved).is_file():
            errors.append(f"card {idx}: task card not found: {card_path}")
            task_gates.append({
                "id": f"card-{idx}",
                "scopes": [], "contracts": [], "base_commit": "",
                "validation_owner": "", "validation_command": "",
            })
            continue

        content = Path(resolved).read_text(encoding="utf-8", errors="replace")

        allowed = extract_gate_field(content, "Parallel Execution Gate", "Allowed files/modules")
        if not allowed:
            allowed = extract_gate_field(content, "Parallel Execution Gate", "Conflict files/modules")
        scopes = extract_scopes(allowed)

        contracts_raw = extract_gate_field(content, "Parallel Execution Gate", "Owned contracts")
        contracts = [c.strip() for c in re.split(r"[,;]+", contracts_raw) if c.strip()]

        base_commit = extract_gate_field(content, "Parallel Execution Gate", "Base commit").strip()
        validation_owner = extract_gate_field(content, "Parallel Execution Gate", "Validation owner").strip()
        validation_command = extract_gate_field(content, "Parallel Execution Gate", "Validation command").strip()

        # Use card basename as id for error messages
        card_name = Path(card_path).stem
        task_gates.append({
            "id": card_name,
            "scopes": scopes,
            "contracts": contracts,
            "base_commit": base_commit,
            "validation_owner": validation_owner,
            "validation_command": validation_command,
        })

    if errors:
        return task_gates, errors

    # --- Check 0: Reject empty/placeholder/broad scopes ---
    PLACEHOLDER_SCOPES = frozenset({
        "", "tbd", "todo", "fixme", "placeholder", "none", "n/a", "na",
        "unknown", "unspecified", "auto", "default",
    })
    for tg in task_gates:
        if not tg["scopes"]:
            errors.append(
                f"task {tg['id']}: empty Allowed files/modules in Parallel Execution Gate "
                f"(every parallel card must declare a non-empty write scope)"
            )
            continue
        for scope in tg["scopes"]:
            scope_lower = scope.lower().strip()
            if scope_lower in PLACEHOLDER_SCOPES:
                errors.append(
                    f"task {tg['id']}: placeholder write scope '{scope}' "
                    f"(every parallel card must declare a real write scope)"
                )
            if scope.rstrip("/") in ("src", "lib", "app", "pkg", "internal", "cmd"):
                errors.append(
                    f"task {tg['id']}: overly broad write scope '{scope}' "
                    f"(use a more specific path to enable reliable overlap detection)"
                )

    if errors:
        return task_gates, errors

    # --- Check 1: Write scope parent/child overlap ---
    if not allow_write_overlap:
        for i in range(len(task_gates)):
            for j in range(i + 1, len(task_gates)):
                ti = task_gates[i]
                tj = task_gates[j]
                for si in ti["scopes"]:
                    for sj in tj["scopes"]:
                        if is_parent_or_child(si, sj):
                            errors.append(
                                f"write scope overlap: task {ti['id']} scope '{si}' "
                                f"overlaps with task {tj['id']} scope '{sj}'"
                            )

    # --- Check 2: Owned contract overlap ---
    all_contracts: dict[str, str] = {}
    for tg in task_gates:
        for contract in tg["contracts"]:
            if contract in all_contracts:
                errors.append(
                    f"owned contract overlap: contract '{contract}' is owned by "
                    f"both {all_contracts[contract]} and {tg['id']}"
                )
            else:
                all_contracts[contract] = tg["id"]

    # --- Check 3: Base commit consistency ---
    PLACEHOLDER_BASE_COMMITS = frozenset({
        "", "abc123", "abc123def", "0000000", "0000000000000000000000000000000000000000",
        "HEAD", "main", "master", "base", "TBD", "tbd", "TODO", "todo",
        "placeholder", "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    })
    declared_commits: list[tuple[str, str]] = []
    for tg in task_gates:
        commit = tg["base_commit"]
        if not commit:
            errors.append(
                f"task {tg['id']}: missing Base commit in Parallel Execution Gate "
                f"(every parallel card must declare a non-placeholder base commit)"
            )
        elif commit.lower() in {p.lower() for p in PLACEHOLDER_BASE_COMMITS}:
            errors.append(
                f"task {tg['id']}: placeholder Base commit '{commit}' in Parallel Execution Gate "
                f"(every parallel card must declare a non-placeholder base commit)"
            )
        else:
            declared_commits.append((tg["id"], commit))

    if declared_commits:
        unique_commits = set(c for _, c in declared_commits)
        if len(unique_commits) > 1:
            details = ", ".join(f"{tid}={c}" for tid, c in declared_commits)
            errors.append(f"base commit mismatch across tasks: {details}")
        elif expected_base_commit:
            actual = declared_commits[0][1]
            if actual != expected_base_commit:
                errors.append(
                    f"base commit mismatch: declared {actual} but expected {expected_base_commit}"
                )

    # --- Check 4: Validation ownership ---
    for tg in task_gates:
        if not tg["validation_owner"] and not tg["validation_command"]:
            errors.append(
                f"task {tg['id']}: missing validation owner and validation command "
                f"in Parallel Execution Gate"
            )

    return task_gates, errors


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

    parser = argparse.ArgumentParser(description="Validate a schema-v1 DAG plan or flat task cards.")
    parser.add_argument("--plan", help="Path to the JSON plan file.")
    parser.add_argument("--output-mode", default="tsv", choices=["tsv"],
                        help="Output format (default: tsv).")
    parser.add_argument("--validate-dispatch", action="store_true",
                        help="Validate deterministic dispatch constraints (write scope overlap, "
                             "owned contracts, base commit, validation ownership) on a plan.")
    parser.add_argument("--validate-flat", action="store_true",
                        help="Validate dispatch constraints on flat positional task cards "
                             "(used by run-parallel-loop.sh flat mode).")
    parser.add_argument("--flat-card", action="append", default=[],
                        help="Path to a task card (repeat for each card; used with --validate-flat).")
    parser.add_argument("--expected-base-commit", default=None,
                        help="Expected base commit SHA for dispatch validation.")
    parser.add_argument("--allow-write-overlap", action="store_true",
                        help="Allow write-scope overlap for an explicit manual-reconcile experiment; other checks remain enforced.")
    args = parser.parse_args()

    if args.validate_flat:
        if not args.flat_card:
            print("Error: --validate-flat requires at least one --flat-card", file=sys.stderr)
            sys.exit(2)
        task_gates, errors = validate_flat_dispatch_constraints(
            args.flat_card, args.expected_base_commit, args.allow_write_overlap
        )
        if errors:
            for err in errors:
                print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
        else:
            print("Flat dispatch validation passed.", file=sys.stderr)
            sys.exit(0)

    if not args.plan:
        print("Error: --plan is required unless --validate-flat is used", file=sys.stderr)
        sys.exit(2)

    if args.validate_dispatch:
        meta, task_gates, errors = validate_dispatch_constraints(
            args.plan, args.expected_base_commit, args.allow_write_overlap
        )
        if errors:
            for err in errors:
                print(f"Error: {err}", file=sys.stderr)
            # Build and print serial fallback with topological order
            _, tasks = validate_plan(args.plan)
            fallback = build_serial_fallback(meta, tasks, task_gates)
            print(fallback, file=sys.stderr)
            sys.exit(1)
        else:
            print("Dispatch validation passed.", file=sys.stderr)
            sys.exit(0)

    meta, tasks = validate_plan(args.plan)
    emit_tsv(meta, tasks)


if __name__ == "__main__":
    main()
