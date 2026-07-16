#!/usr/bin/env python3
"""Create, validate, and summarize repeatable workflow economics experiments."""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, List, Optional

SCHEMA_VERSION = 1
ARMS = ("codex-direct", "delegation-no-spark", "full-workflow")
WORKFLOW_STAGES = (
    "observe", "route", "plan", "worktree_setup", "dispatch",
    "execute", "verify", "review", "artifact_finalization",
)


def _usage_module():
    path = Path(__file__).with_name("model-usage.py")
    spec = importlib.util.spec_from_file_location("aiwf_model_usage", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_manifest(experiment_id: str, task_ids: list[str], repetitions: int) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    unique_tasks = list(dict.fromkeys(task_ids))
    if not unique_tasks or len(unique_tasks) != len(task_ids):
        raise ValueError("task IDs must be non-empty and unique")
    runs = []
    for task_id in unique_tasks:
        for repetition in range(1, repetitions + 1):
            for arm in ARMS:
                run_id = "{}-r{}-{}".format(task_id, repetition, arm)
                runs.append({
                    "run_id": run_id,
                    "task_id": task_id,
                    "repetition": repetition,
                    "arm": arm,
                    "artifact_dir": "runs/{}".format(run_id),
                    "usage_ledger": "runs/{}/model-usage.jsonl".format(run_id),
                    "run_metrics": "runs/{}/run-metrics.json".format(run_id),
                })
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "arms": list(ARMS),
        "task_ids": unique_tasks,
        "repetitions": repetitions,
        "runs": runs,
    }


def validate_manifest(value: dict[str, Any], base: Optional[Path] = None, check_artifacts: bool = False) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be {}".format(SCHEMA_VERSION))
    if value.get("arms") != list(ARMS):
        errors.append("arms must be exactly: {}".format(", ".join(ARMS)))
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

    expected = {(str(task), repetition, arm) for task in tasks for repetition in range(1, repetitions + 1) for arm in ARMS}
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
    missing = expected - observed
    extra = observed - expected
    if missing:
        errors.append("missing run tuples: {}".format(len(missing)))
    if extra:
        errors.append("unexpected run tuples: {}".format(len(extra)))
    return errors


def summarize_manifest(value: dict[str, Any], base: Path) -> dict[str, Any]:
    errors = validate_manifest(value, base, check_artifacts=True)
    if errors:
        raise ValueError("invalid/incomplete experiment: " + "; ".join(errors))
    usage = _usage_module()
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_seconds_by_arm: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    run_rows = []
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
        if not isinstance(metrics.get("total_elapsed_seconds"), (int, float)) or isinstance(metrics.get("total_elapsed_seconds"), bool):
            raise ValueError("total elapsed time missing in {}".format(run["run_id"]))
        for key in ("worktree_setup_seconds", "control_plane_seconds", "total_elapsed_seconds"):
            if isinstance(metrics.get(key), (int, float)) and not isinstance(metrics.get(key), bool):
                stage_seconds.setdefault(key, metrics[key])
        for key, number in stage_seconds.items():
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                stage_seconds_by_arm[arm][str(key)] += float(number)
        run_rows.append({
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "repetition": run["repetition"],
            "arm": arm,
            "elapsed_seconds": metrics.get("total_elapsed_seconds"),
            "accepted": metrics.get("accepted"),
            "first_pass": metrics.get("first_pass"),
            "stage_seconds": stage_seconds,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": value.get("experiment_id"),
        "comparable": True,
        "runs": run_rows,
        "by_arm": {arm: usage.aggregate(by_arm.get(arm, [])) for arm in ARMS},
        "workflow_stage_seconds_by_arm": {
            arm: dict(sorted(stage_seconds_by_arm.get(arm, {}).items())) for arm in ARMS
        },
    }


def prepare_runs(value: dict[str, Any], base: Path) -> list[Path]:
    errors = validate_manifest(value, base, check_artifacts=False)
    if errors:
        raise ValueError("invalid experiment: " + "; ".join(errors))
    created = []
    for run in value["runs"]:
        directory = base / run["artifact_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        context = {
            "AI_WORKFLOW_EXPERIMENT_ARM": run["arm"],
            "AI_WORKFLOW_RUN_ID": run["run_id"],
            "AI_WORKFLOW_TASK_ID": run["task_id"],
            "AI_WORKFLOW_MODEL_USAGE_LEDGER": str((base / run["usage_ledger"]).resolve()),
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
            "stage_seconds": {
                key: None for key in WORKFLOW_STAGES
            },
            "total_elapsed_seconds": None,
        }
        (directory / "run-metrics.template.json").write_text(
            json.dumps(metrics_template, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        created.append(path)
    return created


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
    init.add_argument("--task-id", action="append", required=True)
    init.add_argument("--repetitions", type=int, default=3)
    init.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("validate")
    check.add_argument("manifest", type=Path)
    check.add_argument("--check-artifacts", action="store_true")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("manifest", type=Path)
    report = sub.add_parser("summarize")
    report.add_argument("manifest", type=Path)
    report.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "init":
        _write(build_manifest(args.experiment_id, args.task_id, args.repetitions), args.output)
        return 0
    value = _read(args.manifest)
    if args.command == "prepare":
        created = prepare_runs(value, args.manifest.parent)
        _write({"prepared": len(created), "contexts": [str(path) for path in created]}, None)
        return 0
    if args.command == "validate":
        errors = validate_manifest(value, args.manifest.parent, args.check_artifacts)
        _write({"valid": not errors, "errors": errors}, None)
        return 0 if not errors else 2
    _write(summarize_manifest(value, args.manifest.parent), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
