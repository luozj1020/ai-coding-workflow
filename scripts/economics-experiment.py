#!/usr/bin/env python3
"""Create, validate, and summarize repeatable workflow economics experiments."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, List, Optional

SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3)
ARMS = ("codex-direct", "delegation-no-spark", "full-workflow")
PARALLEL_ARM = "delegation-parallel-no-spark"
PARALLEL_ARMS = ("codex-direct", "delegation-no-spark", PARALLEL_ARM, "full-workflow")
WORKFLOW_STAGES = (
    "observe", "route", "plan", "worktree_setup", "dispatch",
    "execute", "verify", "review", "artifact_finalization",
)
CLAUDE_PHASE_KEYS = (
    "context_acquisition_seconds", "implementation_seconds",
    "validation_seconds_observed", "tail_seconds",
)
DEFAULT_BALANCE_POLICY = {
    "max_active_elapsed_ratio": 2.0,
    "min_cost_savings_ratio": 0.15,
    "require_no_first_pass_regression": True,
}
FREE_ECONOMIC_ROLES = ("spark",)
IMPROVEMENT_DESCRIPTIVE_FIELDS = (
    "semantic_diff_lines", "changed_files", "tests_added", "tests_passed",
)

ARM_CONTRACTS = {
    "codex-direct": {
        "route_policy": "fixed-codex-direct",
        "claude_allowed": False,
        "spark_allowed": False,
    },
    "delegation-no-spark": {
        "route_policy": "fixed-claude-delegation",
        "claude_allowed": True,
        "spark_allowed": False,
    },
    PARALLEL_ARM: {
        "route_policy": "fixed-claude-parallel-delegation",
        "claude_allowed": True,
        "spark_allowed": False,
        "parallel_required": True,
        "max_concurrency": 2,
    },
    "full-workflow": {
        "route_policy": "skill-auto-route",
        "claude_allowed": True,
        "spark_allowed": True,
    },
}


def _usage_module():
    path = Path(__file__).with_name("model-usage.py")
    spec = importlib.util.spec_from_file_location("aiwf_model_usage", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError("git {} failed for {}: {}".format(" ".join(args), project, result.stderr.strip()))
    return result.stdout.strip()


def load_task_spec(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("task spec must be a JSON object: {}".format(path))
    task_id = value.get("id")
    if not isinstance(task_id, str) or not task_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in task_id):
        raise ValueError("task spec id must be a safe non-empty string: {}".format(path))
    if not isinstance(value.get("prompt", value.get("task")), str) or not value.get("prompt", value.get("task")).strip():
        raise ValueError("task spec must contain non-empty prompt or task: {}".format(path))
    for key in ("allowed_files", "forbidden_files", "validation_commands"):
        if key in value and (not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key])):
            raise ValueError("{} must be a list of strings: {}".format(key, path))
    units = value.get("improvement_units")
    if units is not None:
        if not isinstance(units, list) or not units:
            raise ValueError("improvement_units must be a non-empty array: {}".format(path))
        seen = set()
        for index, unit in enumerate(units):
            if not isinstance(unit, dict) or set(unit) != {"id", "description", "weight"}:
                raise ValueError("invalid improvement_units[{}]: {}".format(index, path))
            unit_id = unit.get("id")
            weight = unit.get("weight")
            if not isinstance(unit_id, str) or not unit_id or unit_id in seen:
                raise ValueError("improvement unit IDs must be non-empty and unique: {}".format(path))
            if not isinstance(unit.get("description"), str) or not unit["description"]:
                raise ValueError("improvement unit description must be non-empty: {}".format(path))
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                raise ValueError("improvement unit weight must be positive: {}".format(path))
            seen.add(unit_id)
    return value


def _economic_usage(records: list[dict[str, Any]], usage: Any, pricing: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate billable usage while retaining free-role calls separately."""
    billable = [row for row in records if str(row.get("role") or "unknown") not in FREE_ECONOMIC_ROLES]
    free = [row for row in records if str(row.get("role") or "unknown") in FREE_ECONOMIC_ROLES]
    summary = usage.aggregate(billable, pricing)
    totals = summary["totals"]
    if not billable:
        totals["usage_complete"] = True
        totals["provider_cost_complete"] = True
        totals["cost_usd"] = 0.0
        if pricing is not None:
            totals["calculated_cost_complete"] = True
            totals["calculated_cost_usd"] = 0.0
    summary["accounting_policy"] = {
        "free_roles": list(FREE_ECONOMIC_ROLES),
        "spark_tokens_recorded": True,
        "spark_cost_included": False,
    }
    summary["excluded_free_usage"] = usage.aggregate(free, pricing)
    return summary


def _load_improvement_units(base: Path) -> dict[str, dict[str, float]]:
    snapshot_path = base / "experiment-snapshot.json"
    if not snapshot_path.is_file():
        return {}
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, float]] = {}
    for task_id, raw_path in snapshot.get("task_snapshots", {}).items():
        spec = load_task_spec(Path(raw_path))
        units = spec.get("improvement_units")
        if isinstance(units, list):
            result[str(task_id)] = {str(unit["id"]): float(unit["weight"]) for unit in units}
    return result


def build_manifest(
    experiment_id: str,
    task_ids: list[str],
    repetitions: int,
    *,
    project_root: Optional[str] = None,
    task_inputs: Optional[dict[str, dict[str, str]]] = None,
    forced_full_pipeline: bool = False,
    include_parallel_arm: bool = False,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    unique_tasks = list(dict.fromkeys(task_ids))
    if not unique_tasks or len(unique_tasks) != len(task_ids):
        raise ValueError("task IDs must be non-empty and unique")
    runs = []
    arms = PARALLEL_ARMS if include_parallel_arm else ARMS
    for task_index, task_id in enumerate(unique_tasks):
        for repetition in range(1, repetitions + 1):
            # Rotate arm order to reduce systematic warm-cache/provider-time bias.
            offset = (task_index + repetition - 1) % len(arms)
            ordered_arms = arms[offset:] + arms[:offset]
            for arm_index, arm in enumerate(ordered_arms):
                run_id = "{}-r{}-{}".format(task_id, repetition, arm)
                runs.append({
                    "run_id": run_id,
                    "task_id": task_id,
                    "repetition": repetition,
                    "arm": arm,
                    "artifact_dir": "runs/{}".format(run_id),
                    "usage_ledger": "runs/{}/model-usage.jsonl".format(run_id),
                    "run_metrics": "runs/{}/run-metrics.json".format(run_id),
                    "sequence": len(runs) + 1,
                    "arm_order": arm_index + 1,
                    "arm_contract": dict(ARM_CONTRACTS[arm]),
                })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "arms": list(arms),
        "task_ids": unique_tasks,
        "repetitions": repetitions,
        "runs": runs,
        "forced_full_pipeline": forced_full_pipeline,
    }
    if project_root:
        manifest["project"] = {"root": project_root}
    if task_inputs:
        manifest["task_inputs"] = task_inputs
    return manifest


def validate_manifest(value: dict[str, Any], base: Optional[Path] = None, check_artifacts: bool = False) -> list[str]:
    errors: list[str] = []
    version = value.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append("schema_version must be one of: {}".format(", ".join(map(str, SUPPORTED_SCHEMA_VERSIONS))))
    modern = isinstance(version, int) and version >= 2
    allowed_arm_sets = (list(ARMS), list(PARALLEL_ARMS)) if version == SCHEMA_VERSION else (list(ARMS),)
    if value.get("arms") not in allowed_arm_sets:
        errors.append("arms must be exactly one supported set: {}".format(
            " or ".join(", ".join(group) for group in allowed_arm_sets)
        ))
    balance_policy = value.get("balance_policy")
    if balance_policy is not None:
        if not isinstance(balance_policy, dict):
            errors.append("balance_policy must be an object")
        else:
            elapsed_ratio = balance_policy.get("max_active_elapsed_ratio", DEFAULT_BALANCE_POLICY["max_active_elapsed_ratio"])
            savings = balance_policy.get("min_cost_savings_ratio", DEFAULT_BALANCE_POLICY["min_cost_savings_ratio"])
            first_pass = balance_policy.get("require_no_first_pass_regression", DEFAULT_BALANCE_POLICY["require_no_first_pass_regression"])
            if not isinstance(elapsed_ratio, (int, float)) or isinstance(elapsed_ratio, bool) or elapsed_ratio <= 0:
                errors.append("balance_policy.max_active_elapsed_ratio must be positive")
            if not isinstance(savings, (int, float)) or isinstance(savings, bool) or not 0 <= savings <= 1:
                errors.append("balance_policy.min_cost_savings_ratio must be between 0 and 1")
            if not isinstance(first_pass, bool):
                errors.append("balance_policy.require_no_first_pass_regression must be boolean")
    tasks = value.get("task_ids")
    repetitions = value.get("repetitions")
    runs = value.get("runs")
    if not isinstance(tasks, list) or not tasks or len(set(tasks)) != len(tasks):
        errors.append("task_ids must be a non-empty unique list")
        tasks = []
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        errors.append("repetitions must be a positive integer")
        repetitions = 0
    if not isinstance(runs, list):
        errors.append("runs must be a list")
        runs = []

    manifest_arms = tuple(value.get("arms")) if value.get("arms") in allowed_arm_sets else ARMS
    expected = {(str(task), repetition, arm) for task in tasks for repetition in range(1, repetitions + 1) for arm in manifest_arms}
    observed: set[tuple[str, int, str]] = set()
    run_ids: set[str] = set()
    root = base or Path.cwd()
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            errors.append("runs[{}] must be an object".format(index))
            continue
        key = (str(run.get("task_id")), run.get("repetition"), str(run.get("arm")))
        if key in observed:
            errors.append("duplicate run tuple: {}".format(key))
        observed.add(key)
        run_id = str(run.get("run_id") or "")
        if not run_id or run_id in run_ids:
            errors.append("run_id must be non-empty and unique: {}".format(run_id))
        run_ids.add(run_id)
        for field in ("artifact_dir", "usage_ledger", "run_metrics"):
            raw = run.get(field)
            if not isinstance(raw, str) or not raw:
                errors.append("{} missing {}".format(run_id or "runs[{}]".format(index), field))
            elif check_artifacts and not (root / raw).exists():
                errors.append("missing artifact: {}".format(root / raw))
        contract = run.get("arm_contract")
        if modern and contract != ARM_CONTRACTS.get(str(run.get("arm"))):
            errors.append("{} arm contract mismatch".format(run_id or "runs[{}]".format(index)))
    missing = expected - observed
    extra = observed - expected
    if missing:
        errors.append("missing run tuples: {}".format(len(missing)))
    if extra:
        errors.append("unexpected run tuples: {}".format(len(extra)))
    if modern:
        sequences = [run.get("sequence") for run in runs if isinstance(run, dict)]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in sequences) or sorted(sequences) != list(range(1, len(runs) + 1)):
            errors.append("run sequence must contain each integer from 1 to {}".format(len(runs)))
    task_inputs = value.get("task_inputs", {})
    if task_inputs and (not isinstance(task_inputs, dict) or set(task_inputs) != set(tasks)):
        errors.append("task_inputs must bind every task_id exactly once")
    if check_artifacts and modern:
        snapshot_path = root / "experiment-snapshot.json"
        if not snapshot_path.is_file():
            errors.append("missing artifact: {}".format(snapshot_path))
        else:
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                for run in runs:
                    context_path = root / run["artifact_dir"] / "run-context.json"
                    if not context_path.is_file():
                        continue
                    context = json.loads(context_path.read_text(encoding="utf-8"))
                    if context.get("base_commit") != snapshot.get("base_commit"):
                        errors.append("base commit mismatch in {}".format(run["run_id"]))
                    expected_hash = snapshot.get("task_input_sha256", {}).get(run["task_id"])
                    if expected_hash and context.get("task_input_sha256") != expected_hash:
                        errors.append("task input mismatch in {}".format(run["run_id"]))
                    metrics_path = root / run["run_metrics"]
                    if metrics_path.is_file():
                        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                        if metrics.get("base_commit") != snapshot.get("base_commit"):
                            errors.append("metrics base commit mismatch in {}".format(run["run_id"]))
                        if expected_hash and metrics.get("task_input_sha256") != expected_hash:
                            errors.append("metrics task input mismatch in {}".format(run["run_id"]))
                        owner = metrics.get("actual_owner")
                        if run["arm"] == "codex-direct" and owner != "codex-fast-path":
                            errors.append("codex-direct owner mismatch in {}".format(run["run_id"]))
                        if run["arm"] == "delegation-no-spark" and owner != "claude-builder":
                            errors.append("delegation owner mismatch in {}".format(run["run_id"]))
                        if run["arm"] == PARALLEL_ARM:
                            if owner != "claude-parallel":
                                errors.append("parallel delegation owner mismatch in {}".format(run["run_id"]))
                            if metrics.get("parallel_max_concurrency") != 2:
                                errors.append("parallel delegation concurrency mismatch in {}".format(run["run_id"]))
                            if not isinstance(metrics.get("parallel_units"), int) or metrics.get("parallel_units", 0) < 2:
                                errors.append("parallel delegation units missing in {}".format(run["run_id"]))
                        if run["arm"] == "full-workflow" and not value.get("forced_full_pipeline") and metrics.get("route_honored") is not True:
                            errors.append("auto route was not honored in {}".format(run["run_id"]))
                    ledger_path = root / run["usage_ledger"]
                    if ledger_path.is_file():
                        roles = set()
                        for raw in ledger_path.read_text(encoding="utf-8").splitlines():
                            if not raw.strip():
                                continue
                            record = json.loads(raw)
                            if isinstance(record, dict):
                                roles.add(str(record.get("role") or "unknown"))
                        if run["arm"] == "codex-direct" and roles.intersection({"claude", "spark"}):
                            errors.append("codex-direct used delegated model in {}".format(run["run_id"]))
                        if run["arm"] == "delegation-no-spark" and "spark" in roles:
                            errors.append("delegation-no-spark used Spark in {}".format(run["run_id"]))
                        if run["arm"] == "delegation-no-spark" and "claude" not in roles:
                            errors.append("delegation-no-spark has no Claude usage in {}".format(run["run_id"]))
                        if run["arm"] == PARALLEL_ARM and "spark" in roles:
                            errors.append("parallel delegation used Spark in {}".format(run["run_id"]))
                        if run["arm"] == PARALLEL_ARM and "claude" not in roles:
                            errors.append("parallel delegation has no Claude usage in {}".format(run["run_id"]))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append("invalid experiment snapshot: {}".format(exc))
    return errors


def summarize_manifest(
    value: dict[str, Any], base: Path, pricing: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    errors = validate_manifest(value, base, check_artifacts=True)
    if errors:
        raise ValueError("invalid/incomplete experiment: " + "; ".join(errors))
    usage = _usage_module()
    arms_in_manifest = tuple(value.get("arms", ARMS))
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_seconds_by_arm: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    claude_phase_seconds_by_arm: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    improvement_units_by_task = _load_improvement_units(base)
    run_rows = []
    paired: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for run in value["runs"]:
        records = usage.load_records(base / run["usage_ledger"], strict=True)
        arm = run["arm"]
        if not records:
            raise ValueError("empty usage ledger in {}".format(run["run_id"]))
        for row in records:
            if row.get("experiment_arm") != arm:
                raise ValueError("usage arm mismatch in {}".format(run["run_id"]))
            if row.get("run_id") != run["run_id"] or row.get("task_id") != run["task_id"]:
                raise ValueError("usage run/task mismatch in {}".format(run["run_id"]))
        by_arm[arm].extend(records)
        metrics = json.loads((base / run["run_metrics"]).read_text(encoding="utf-8"))
        stage_seconds = metrics.get("stage_seconds") if isinstance(metrics.get("stage_seconds"), dict) else {}
        if metrics.get("run_id") != run["run_id"] or metrics.get("task_id") != run["task_id"] or metrics.get("experiment_arm") != arm:
            raise ValueError("metrics identity mismatch in {}".format(run["run_id"]))
        if not isinstance(metrics.get("accepted"), bool) or not isinstance(metrics.get("first_pass"), bool):
            raise ValueError("metrics quality fields incomplete in {}".format(run["run_id"]))
        missing_stages = [
            key for key in WORKFLOW_STAGES
            if not isinstance(stage_seconds.get(key), (int, float)) or isinstance(stage_seconds.get(key), bool)
        ]
        if missing_stages:
            raise ValueError("metrics stages incomplete in {}: {}".format(run["run_id"], ",".join(missing_stages)))
        active_elapsed = metrics.get("active_elapsed_seconds", metrics.get("total_elapsed_seconds"))
        if not isinstance(active_elapsed, (int, float)) or isinstance(active_elapsed, bool):
            raise ValueError("active elapsed time missing in {}".format(run["run_id"]))
        human_approval = metrics.get("human_approval_seconds")
        end_to_end_elapsed = metrics.get("end_to_end_elapsed_seconds")
        unattributed_wait = metrics.get("unattributed_wait_seconds")
        for field, number in (
            ("human_approval_seconds", human_approval),
            ("end_to_end_elapsed_seconds", end_to_end_elapsed),
            ("unattributed_wait_seconds", unattributed_wait),
        ):
            if number is not None and (not isinstance(number, (int, float)) or isinstance(number, bool) or number < 0):
                raise ValueError("invalid {} in {}".format(field, run["run_id"]))
        for key in ("worktree_setup_seconds", "control_plane_seconds", "total_elapsed_seconds"):
            if isinstance(metrics.get(key), (int, float)) and not isinstance(metrics.get(key), bool):
                stage_seconds.setdefault(key, metrics[key])
        for key, number in stage_seconds.items():
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                stage_seconds_by_arm[arm][str(key)] += float(number)
        phase_path = base / run["artifact_dir"] / "claude-phase-metrics.json"
        claude_phases: Optional[dict[str, Any]] = None
        claude_owned_run = str(metrics.get("actual_owner") or "").startswith("claude")
        if phase_path.is_file() and claude_owned_run:
            try:
                candidate = json.loads(phase_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("invalid Claude phase metrics in {}: {}".format(run["run_id"], exc))
            if not isinstance(candidate, dict):
                raise ValueError("Claude phase metrics must be an object in {}".format(run["run_id"]))
            invalid_phase = [
                key for key in CLAUDE_PHASE_KEYS
                if not isinstance(candidate.get(key), (int, float))
                or isinstance(candidate.get(key), bool) or candidate.get(key, 0) < 0
            ]
            if invalid_phase:
                raise ValueError("Claude phase metrics incomplete in {}: {}".format(
                    run["run_id"], ",".join(invalid_phase)
                ))
            claude_phases = {key: float(candidate[key]) for key in CLAUDE_PHASE_KEYS}
            for key, number in claude_phases.items():
                claude_phase_seconds_by_arm[arm][key] += number
        usage_summary = usage.aggregate(records, pricing)
        economic_usage = _economic_usage(records, usage, pricing)
        unit_weights = improvement_units_by_task.get(run["task_id"])
        satisfied_units = metrics.get("improvement_units_satisfied")
        if unit_weights is not None:
            if not isinstance(satisfied_units, list) or any(not isinstance(item, str) for item in satisfied_units):
                raise ValueError("improvement_units_satisfied missing in {}".format(run["run_id"]))
            unknown_units = sorted(set(satisfied_units) - set(unit_weights))
            if unknown_units:
                raise ValueError("unknown improvement units in {}: {}".format(run["run_id"], ",".join(unknown_units)))
            total_weight = sum(unit_weights.values())
            satisfied_weight = sum(unit_weights[item] for item in set(satisfied_units))
            improvement_quantity = {
                "available": True,
                "satisfied_ids": sorted(set(satisfied_units)),
                "satisfied_weight": round(satisfied_weight, 6),
                "total_weight": round(total_weight, 6),
                "completion_ratio": round(satisfied_weight / total_weight, 6),
            }
        else:
            improvement_quantity = {
                "available": False,
                "reason": "task-spec-has-no-improvement-units",
            }
        descriptive_quantity = {}
        for field in IMPROVEMENT_DESCRIPTIVE_FIELDS:
            number = metrics.get(field)
            if number is not None and (
                not isinstance(number, (int, float)) or isinstance(number, bool) or number < 0
            ):
                raise ValueError("invalid {} in {}".format(field, run["run_id"]))
            descriptive_quantity[field] = number
        row = {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "repetition": run["repetition"],
            "arm": arm,
            # Economic comparisons use active execution time. Human approval and
            # other externally blocked time remain visible but never inflate it.
            "elapsed_seconds": active_elapsed,
            "active_elapsed_seconds": active_elapsed,
            "human_approval_seconds": human_approval,
            "unattributed_wait_seconds": unattributed_wait,
            "end_to_end_elapsed_seconds": end_to_end_elapsed,
            "accepted": metrics.get("accepted"),
            "first_pass": metrics.get("first_pass"),
            "stage_seconds": stage_seconds,
            "claude_phase_seconds": claude_phases,
            "claude_phase_metrics_available": claude_phases is not None,
            "actual_owner": metrics.get("actual_owner"),
            "route_recommendation": metrics.get("route_recommendation"),
            "route_honored": metrics.get("route_honored"),
            "usage_complete": usage_summary.get("totals", {}).get("usage_complete") is True,
            "usage": usage_summary,
            "economic_usage": economic_usage,
            "improvement_quantity": improvement_quantity,
            "improvement_descriptive": descriptive_quantity,
        }
        run_rows.append(row)
        paired[(run["task_id"], run["repetition"])][arm] = row

    pair_rows = []
    policy = dict(DEFAULT_BALANCE_POLICY)
    if isinstance(value.get("balance_policy"), dict):
        policy.update(value["balance_policy"])
    for (task_id, repetition), arms in sorted(paired.items()):
        direct = arms.get("codex-direct")
        if not direct:
            continue
        direct_totals = direct["economic_usage"].get("totals", {})
        for arm in tuple(item for item in arms_in_manifest if item != "codex-direct"):
            candidate = arms.get(arm)
            if not candidate:
                continue
            candidate_totals = candidate["economic_usage"].get("totals", {})
            usage_complete_both = direct["usage_complete"] and candidate["usage_complete"]
            billable_usage_complete_both = bool(
                direct_totals.get("usage_complete") and candidate_totals.get("usage_complete")
            )
            if direct_totals.get("calculated_cost_complete") and candidate_totals.get("calculated_cost_complete"):
                direct_cost = direct_totals.get("calculated_cost_usd")
                candidate_cost = candidate_totals.get("calculated_cost_usd")
                cost_source = "pricing-catalog"
            elif direct_totals.get("provider_cost_complete") and candidate_totals.get("provider_cost_complete"):
                direct_cost = direct_totals.get("cost_usd")
                candidate_cost = candidate_totals.get("cost_usd")
                cost_source = "provider-reported"
            else:
                direct_cost = candidate_cost = None
                cost_source = "incomplete"
            direct_parts = (direct_totals.get("input_tokens"), direct_totals.get("output_tokens"))
            candidate_parts = (candidate_totals.get("input_tokens"), candidate_totals.get("output_tokens"))
            direct_tokens = sum(direct_parts) if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in direct_parts) else None
            candidate_tokens = sum(candidate_parts) if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in candidate_parts) else None
            elapsed_ratio = round(candidate["elapsed_seconds"] / direct["elapsed_seconds"], 4) if direct["elapsed_seconds"] else None
            cost_ratio = round(candidate_cost / direct_cost, 4) if isinstance(candidate_cost, (int, float)) and isinstance(direct_cost, (int, float)) and direct_cost else None
            direct_quantity = direct["improvement_quantity"]
            candidate_quantity = candidate["improvement_quantity"]
            content_quantity_gate = None
            improvement_weight_delta = None
            cost_per_improvement_weight_ratio = None
            elapsed_per_improvement_weight_ratio = None
            if direct_quantity.get("available") and candidate_quantity.get("available"):
                direct_weight = direct_quantity["satisfied_weight"]
                candidate_weight = candidate_quantity["satisfied_weight"]
                improvement_weight_delta = round(candidate_weight - direct_weight, 6)
                content_quantity_gate = candidate_weight >= direct_weight
                if direct_weight and candidate_weight:
                    direct_cost_per_unit = direct_cost / direct_weight if isinstance(direct_cost, (int, float)) else None
                    candidate_cost_per_unit = candidate_cost / candidate_weight if isinstance(candidate_cost, (int, float)) else None
                    if isinstance(direct_cost_per_unit, (int, float)) and direct_cost_per_unit:
                        cost_per_improvement_weight_ratio = round(candidate_cost_per_unit / direct_cost_per_unit, 4)
                    direct_time_per_unit = direct["elapsed_seconds"] / direct_weight
                    candidate_time_per_unit = candidate["elapsed_seconds"] / candidate_weight
                    if direct_time_per_unit:
                        elapsed_per_improvement_weight_ratio = round(candidate_time_per_unit / direct_time_per_unit, 4)
            quality_gate = bool(
                direct["accepted"] and candidate["accepted"]
                and content_quantity_gate is not False
                and (
                    not policy["require_no_first_pass_regression"]
                    or not direct["first_pass"]
                    or candidate["first_pass"]
                )
            )
            efficiency_gate = isinstance(elapsed_ratio, (int, float)) and elapsed_ratio <= policy["max_active_elapsed_ratio"]
            economy_gate = (
                isinstance(cost_ratio, (int, float))
                and cost_ratio <= 1.0 - policy["min_cost_savings_ratio"]
            ) if cost_ratio is not None else None
            if content_quantity_gate is False:
                balanced_recommendation = "retain-baseline-content-regression"
            elif not quality_gate:
                balanced_recommendation = "retain-baseline-quality-regression"
            elif not efficiency_gate:
                balanced_recommendation = "retain-baseline-efficiency-regression"
            elif economy_gate is None:
                balanced_recommendation = "insufficient-economic-evidence"
            elif not economy_gate:
                balanced_recommendation = "retain-baseline-insufficient-savings"
            else:
                balanced_recommendation = "balanced-candidate"
            pair_rows.append({
                "task_id": task_id,
                "repetition": repetition,
                "arm": arm,
                "accepted_both": direct["accepted"] and candidate["accepted"],
                "usage_complete_both": usage_complete_both,
                "billable_usage_complete_both": billable_usage_complete_both,
                "elapsed_delta_seconds": round(candidate["elapsed_seconds"] - direct["elapsed_seconds"], 6),
                "elapsed_ratio": elapsed_ratio,
                "cost_delta_usd": round(candidate_cost - direct_cost, 6) if isinstance(candidate_cost, (int, float)) and isinstance(direct_cost, (int, float)) else None,
                "cost_ratio": cost_ratio,
                "cost_source": cost_source,
                "spark_cost_included": False,
                "content_quantity_gate": content_quantity_gate,
                "improvement_weight_delta": improvement_weight_delta,
                "cost_per_improvement_weight_ratio": cost_per_improvement_weight_ratio,
                "elapsed_per_improvement_weight_ratio": elapsed_per_improvement_weight_ratio,
                "quality_gate": quality_gate,
                "efficiency_gate": efficiency_gate,
                "economy_gate": economy_gate,
                "balanced_recommendation": balanced_recommendation,
                "input_output_token_delta": candidate_tokens - direct_tokens if billable_usage_complete_both and isinstance(candidate_tokens, (int, float)) and isinstance(direct_tokens, (int, float)) else None,
                "billable_input_output_token_delta": candidate_tokens - direct_tokens if billable_usage_complete_both and isinstance(candidate_tokens, (int, float)) and isinstance(direct_tokens, (int, float)) else None,
            })

    descriptive = {}
    for arm in arms_in_manifest:
        rows = [row for row in run_rows if row["arm"] == arm]
        elapsed = [float(row["elapsed_seconds"]) for row in rows]
        end_to_end = [
            float(row["end_to_end_elapsed_seconds"])
            for row in rows
            if isinstance(row.get("end_to_end_elapsed_seconds"), (int, float))
            and not isinstance(row.get("end_to_end_elapsed_seconds"), bool)
        ]
        approvals = [
            float(row["human_approval_seconds"])
            for row in rows
            if isinstance(row.get("human_approval_seconds"), (int, float))
            and not isinstance(row.get("human_approval_seconds"), bool)
        ]
        costs = [
            row["economic_usage"].get("totals", {}).get("calculated_cost_usd")
            if row["economic_usage"].get("totals", {}).get("calculated_cost_complete")
            else row["economic_usage"].get("totals", {}).get("cost_usd")
            if row["economic_usage"].get("totals", {}).get("provider_cost_complete")
            else None
            for row in rows
        ]
        known_costs = [float(item) for item in costs if isinstance(item, (int, float)) and not isinstance(item, bool)]
        phase_rows = [row["claude_phase_seconds"] for row in rows if row["claude_phase_seconds"] is not None]
        quantity_rows = [
            row["improvement_quantity"] for row in rows
            if row["improvement_quantity"].get("available")
        ]
        descriptive[arm] = {
            "runs": len(rows),
            "accepted_rate": round(sum(row["accepted"] is True for row in rows) / len(rows), 4) if rows else None,
            "first_pass_rate": round(sum(row["first_pass"] is True for row in rows) / len(rows), 4) if rows else None,
            "median_elapsed_seconds": round(statistics.median(elapsed), 6) if elapsed else None,
            "median_active_elapsed_seconds": round(statistics.median(elapsed), 6) if elapsed else None,
            "median_end_to_end_elapsed_seconds": round(statistics.median(end_to_end), 6) if len(end_to_end) == len(rows) and rows else None,
            "median_human_approval_seconds": round(statistics.median(approvals), 6) if len(approvals) == len(rows) and rows else None,
            "median_cost_usd": round(statistics.median(known_costs), 6) if len(known_costs) == len(rows) and rows else None,
            "spark_cost_included": False,
            "cost_complete": len(known_costs) == len(rows),
            "improvement_quantity_runs": len(quantity_rows),
            "median_improvement_weight": round(statistics.median(
                float(item["satisfied_weight"]) for item in quantity_rows
            ), 6) if quantity_rows else None,
            "median_improvement_completion_ratio": round(statistics.median(
                float(item["completion_ratio"]) for item in quantity_rows
            ), 6) if quantity_rows else None,
            "median_improvement_descriptive": {
                field: round(statistics.median(float(row["improvement_descriptive"][field]) for row in rows), 6)
                if rows and all(isinstance(row["improvement_descriptive"].get(field), (int, float)) for row in rows)
                else None
                for field in IMPROVEMENT_DESCRIPTIVE_FIELDS
            },
            "claude_phase_metrics_runs": len(phase_rows),
            "median_claude_phase_seconds": {
                key: round(statistics.median(float(item[key]) for item in phase_rows), 6)
                for key in CLAUDE_PHASE_KEYS
            } if phase_rows else None,
        }
    usage_matrix_complete = all(row["usage_complete"] for row in run_rows)
    billable_usage_matrix_complete = all(
        row["economic_usage"].get("totals", {}).get("usage_complete") is True
        for row in run_rows
    )
    cost_matrix_complete = all(
        row["economic_usage"].get("totals", {}).get("calculated_cost_complete") is True
        or row["economic_usage"].get("totals", {}).get("provider_cost_complete") is True
        for row in run_rows
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": value.get("experiment_id"),
        "comparable": usage_matrix_complete,
        "quality_time_comparable": True,
        "token_comparable": usage_matrix_complete,
        "cost_comparable": billable_usage_matrix_complete and cost_matrix_complete,
        "comparability_notes": [] if usage_matrix_complete else [
            "At least one model call has incomplete token usage; token and cost deltas for affected pairs are suppressed."
        ],
        "balance_policy": policy,
        "cost_accounting_policy": {
            "free_roles": list(FREE_ECONOMIC_ROLES),
            "spark_tokens_recorded": True,
            "spark_cost_included": False,
        },
        "runs": run_rows,
        "descriptive_by_arm": descriptive,
        "paired_comparisons": pair_rows,
        "by_arm": {arm: usage.aggregate(by_arm.get(arm, []), pricing) for arm in arms_in_manifest},
        "billable_by_arm": {
            arm: _economic_usage(by_arm.get(arm, []), usage, pricing) for arm in arms_in_manifest
        },
        "workflow_stage_seconds_by_arm": {
            arm: dict(sorted(stage_seconds_by_arm.get(arm, {}).items())) for arm in arms_in_manifest
        },
        "claude_phase_seconds_by_arm": {
            arm: dict(sorted(claude_phase_seconds_by_arm.get(arm, {}).items()))
            for arm in arms_in_manifest
        },
    }


def prepare_runs(value: dict[str, Any], base: Path, *, allow_dirty: bool = False) -> list[Path]:
    errors = validate_manifest(value, base, check_artifacts=False)
    if errors:
        raise ValueError("invalid experiment: " + "; ".join(errors))
    project = value.get("project") if isinstance(value.get("project"), dict) else {}
    project_root = Path(project.get("root", base)).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError("project root does not exist: {}".format(project_root))
    try:
        base_commit = _git(project_root, "rev-parse", "HEAD")
        tracked_status = _git(project_root, "status", "--porcelain", "--untracked-files=no")
        branch = _git(project_root, "branch", "--show-current")
    except ValueError:
        if project:
            raise
        base_commit, tracked_status, branch = None, None, None
    if tracked_status and not allow_dirty:
        raise ValueError(
            "project has tracked modifications; commit/stash them or rerun prepare with --allow-dirty "
            "(dirty experiments are recorded but are not reproducible)"
        )

    task_hashes: dict[str, str] = {}
    task_snapshots: dict[str, str] = {}
    inputs = value.get("task_inputs") if isinstance(value.get("task_inputs"), dict) else {}
    for task_id, binding in inputs.items():
        source = Path(binding["source"]).expanduser().resolve()
        spec = load_task_spec(source)
        if spec["id"] != task_id:
            raise ValueError("task spec ID changed for {}".format(source))
        digest = _sha256(source)
        expected = binding.get("sha256")
        if expected and digest != expected:
            raise ValueError("task spec changed since manifest init: {}".format(source))
        target = base / "inputs" / (task_id + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        task_hashes[task_id] = _sha256(target)
        task_snapshots[task_id] = str(target.resolve())

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": value.get("experiment_id"),
        "project_root": str(project_root),
        "base_commit": base_commit,
        "branch": branch,
        "tracked_worktree_clean": tracked_status == "" if tracked_status is not None else None,
        "tracked_status": tracked_status,
        "task_input_sha256": task_hashes,
        "task_snapshots": task_snapshots,
    }
    (base / "experiment-snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    created = []
    for run in value["runs"]:
        directory = base / run["artifact_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        context = {
            "AI_WORKFLOW_EXPERIMENT_ARM": run["arm"],
            "AI_WORKFLOW_RUN_ID": run["run_id"],
            "AI_WORKFLOW_TASK_ID": run["task_id"],
            "AI_WORKFLOW_MODEL_USAGE_LEDGER": str((base / run["usage_ledger"]).resolve()),
            "AI_WORKFLOW_CLAUDE_PHASE_METRICS_FILE": str((directory / "claude-phase-metrics.json").resolve()),
            "project_root": str(project_root),
            "base_commit": base_commit,
            "task_input": task_snapshots.get(run["task_id"]),
            "task_input_sha256": task_hashes.get(run["task_id"]),
            "sequence": run.get("sequence"),
            "arm_contract": run.get("arm_contract", ARM_CONTRACTS[run["arm"]]),
            "forced_full_pipeline": value.get("forced_full_pipeline", False),
        }
        path = directory / "run-context.json"
        path.write_text(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        metrics_template = {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "experiment_arm": run["arm"],
            "accepted": None,
            "first_pass": None,
            "codex_takeover": None,
            "claude_reuse_ratio": None,
            "parallel_units": None,
            "parallel_max_concurrency": None,
            "parallel_conflicts": None,
            "reconciliation_seconds": None,
            "completed": None,
            "actual_owner": None,
            "route_recommendation": None,
            "route_honored": None,
            "validation_status": None,
            "failure_classification": None,
            "improvement_units_satisfied": [],
            "semantic_diff_lines": None,
            "changed_files": None,
            "tests_added": None,
            "tests_passed": None,
            "base_commit": base_commit,
            "task_input_sha256": task_hashes.get(run["task_id"]),
            "stage_seconds": {
                key: None for key in WORKFLOW_STAGES
            },
            "claude_phase_metrics_file": "claude-phase-metrics.json",
            "active_elapsed_seconds": None,
            "human_approval_seconds": None,
            "unattributed_wait_seconds": None,
            "end_to_end_elapsed_seconds": None,
            # Compatibility alias for older result writers. New runs should set
            # active_elapsed_seconds and leave this equal to active time.
            "total_elapsed_seconds": None,
        }
        (directory / "run-metrics.template.json").write_text(
            json.dumps(metrics_template, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        created.append(path)
    return created


def experiment_status(value: dict[str, Any], base: Path) -> dict[str, Any]:
    rows = []
    counts = defaultdict(int)
    for fallback_sequence, run in enumerate(sorted(value.get("runs", []), key=lambda item: item.get("sequence", 0)), 1):
        metrics_path = base / run["run_metrics"]
        ledger_path = base / run["usage_ledger"]
        state = "pending"
        accepted = None
        if metrics_path.is_file():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                accepted = metrics.get("accepted")
                state = "completed" if metrics.get("completed") is True or isinstance(accepted, bool) else "in-progress"
            except (OSError, json.JSONDecodeError):
                state = "invalid"
        elif ledger_path.is_file():
            state = "in-progress"
        counts[state] += 1
        rows.append({
            "sequence": run.get("sequence", fallback_sequence),
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "arm": run["arm"],
            "state": state,
            "accepted": accepted,
            "usage_ledger_present": ledger_path.is_file(),
        })
    project_state: dict[str, Any] = {}
    snapshot_path = base / "experiment-snapshot.json"
    if snapshot_path.is_file():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            project_root = Path(snapshot.get("project_root", ""))
            if snapshot.get("base_commit") and project_root.is_dir():
                current = _git(project_root, "rev-parse", "HEAD")
                project_state = {
                    "project_root": str(project_root),
                    "base_commit": snapshot.get("base_commit"),
                    "current_commit": current,
                    "head_drifted": current != snapshot.get("base_commit"),
                }
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            project_state = {"status_error": str(exc)}
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": value.get("experiment_id"),
        "total": len(rows),
        "counts": dict(sorted(counts.items())),
        "next_run": next((row for row in rows if row["state"] == "pending"), None),
        "project_state": project_state,
        "runs": rows,
    }


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def _write(value: dict[str, Any], path: Optional[Path]) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--experiment-id", required=True)
    init.add_argument("--task-id", action="append", default=[])
    init.add_argument("--task-spec", action="append", type=Path, default=[],
                      help="JSON task spec to freeze for real-project execution")
    init.add_argument("--project-root", type=Path,
                      help="Git repository under test; recorded without mutation")
    init.add_argument("--forced-full-pipeline", action="store_true",
                      help="Diagnostic only: ignore auto-route early exit in full-workflow arm")
    init.add_argument("--include-parallel-arm", action="store_true",
                      help="Add a fixed Claude max-concurrency-2 arm for independent task bundles")
    init.add_argument("--repetitions", type=int, default=3)
    init.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("validate")
    check.add_argument("manifest", type=Path)
    check.add_argument("--check-artifacts", action="store_true")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("manifest", type=Path)
    prepare.add_argument("--allow-dirty", action="store_true")
    status = sub.add_parser("status", help="Show resumable partial experiment progress")
    status.add_argument("manifest", type=Path)
    report = sub.add_parser("summarize")
    report.add_argument("manifest", type=Path)
    report.add_argument("--output", type=Path)
    report.add_argument("--pricing", type=Path,
                        help="Versioned API price catalog for calculated cost comparisons")
    args = parser.parse_args(argv)
    if args.command == "init":
        task_ids = list(args.task_id)
        task_inputs: dict[str, dict[str, str]] = {}
        for source in args.task_spec:
            source = source.expanduser().resolve()
            spec = load_task_spec(source)
            task_ids.append(spec["id"])
            task_inputs[spec["id"]] = {"source": str(source), "sha256": _sha256(source)}
        if not task_ids:
            parser.error("at least one --task-id or --task-spec is required")
        _write(build_manifest(
            args.experiment_id,
            task_ids,
            args.repetitions,
            project_root=str(args.project_root.expanduser().resolve()) if args.project_root else None,
            task_inputs=task_inputs or None,
            forced_full_pipeline=args.forced_full_pipeline,
            include_parallel_arm=args.include_parallel_arm,
        ), args.output)
        return 0
    value = _read(args.manifest)
    if args.command == "prepare":
        created = prepare_runs(value, args.manifest.parent, allow_dirty=args.allow_dirty)
        _write({"prepared": len(created), "contexts": [str(path) for path in created]}, None)
        return 0
    if args.command == "validate":
        errors = validate_manifest(value, args.manifest.parent, args.check_artifacts)
        _write({"valid": not errors, "errors": errors}, None)
        return 0 if not errors else 2
    if args.command == "status":
        _write(experiment_status(value, args.manifest.parent), None)
        return 0
    pricing = _usage_module().load_pricing(args.pricing) if args.pricing else None
    _write(summarize_manifest(value, args.manifest.parent, pricing), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
