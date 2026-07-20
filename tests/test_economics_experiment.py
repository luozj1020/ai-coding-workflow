import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


experiment = load_script("economics_experiment", "economics-experiment.py")


class EconomicsExperimentTests(unittest.TestCase):
    def test_default_balance_policy_prioritizes_cost_without_large_slowdown(self):
        self.assertEqual(experiment.DEFAULT_BALANCE_POLICY["min_cost_savings_ratio"], 0.15)
        self.assertEqual(experiment.DEFAULT_BALANCE_POLICY["max_active_elapsed_ratio"], 2.0)

    def test_manifest_has_balanced_arms_for_every_task_and_repetition(self):
        value = experiment.build_manifest("e1", ["t1", "t2"], 2)
        self.assertEqual(experiment.validate_manifest(value), [])
        self.assertEqual(len(value["runs"]), 12)

    def test_parallel_arm_is_explicit_and_counterbalanced(self):
        value = experiment.build_manifest("parallel", ["batch"], 2, include_parallel_arm=True)
        self.assertEqual(value["arms"], list(experiment.PARALLEL_ARMS))
        self.assertEqual(experiment.validate_manifest(value), [])
        self.assertEqual(len(value["runs"]), 8)
        parallel = [run for run in value["runs"] if run["arm"] == experiment.PARALLEL_ARM]
        self.assertEqual(len(parallel), 2)
        self.assertTrue(all(run["arm_contract"]["max_concurrency"] == 2 for run in parallel))

    def test_manifest_rejects_missing_and_duplicate_arms(self):
        value = experiment.build_manifest("e1", ["t1"], 1)
        value["runs"].pop()
        value["runs"].append(dict(value["runs"][0]))
        errors = experiment.validate_manifest(value)
        self.assertTrue(any("duplicate run tuple" in item for item in errors))
        self.assertTrue(any("missing run tuples" in item for item in errors))

    def test_manifest_rejects_invalid_balance_policy(self):
        value = experiment.build_manifest("e1", ["t1"], 1)
        value["balance_policy"] = {
            "max_active_elapsed_ratio": 0,
            "min_cost_savings_ratio": 1.5,
            "require_no_first_pass_regression": "yes",
        }
        errors = experiment.validate_manifest(value)
        self.assertEqual(len([item for item in errors if item.startswith("balance_policy")]), 3)

    def test_v1_manifest_remains_readable(self):
        value = experiment.build_manifest("legacy", ["t1"], 1)
        value["schema_version"] = 1
        value.pop("forced_full_pipeline")
        for run in value["runs"]:
            run.pop("sequence")
            run.pop("arm_order")
            run.pop("arm_contract")
        self.assertEqual(experiment.validate_manifest(value), [])

    def test_v2_three_arm_manifest_remains_readable(self):
        value = experiment.build_manifest("legacy-v2", ["t1"], 1)
        value["schema_version"] = 2
        self.assertEqual(experiment.validate_manifest(value), [])

    def test_artifact_check_and_summary(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("e1", ["t1"], 1)
            self.assertTrue(experiment.validate_manifest(value, root, True))
            experiment.prepare_runs(value, root)
            for run in value["runs"]:
                ledger = root / run["usage_ledger"]
                metrics = root / run["run_metrics"]
                ledger.parent.mkdir(parents=True, exist_ok=True)
                ledger.write_text(json.dumps({
                    "schema_version": 1,
                    "call_id": run["run_id"],
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "role": "claude" if run["arm"] == "delegation-no-spark" else "codex",
                    "model": "test-model",
                    "stage": "execute",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "usage_complete": True,
                }) + "\n", encoding="utf-8")
                metrics.write_text(json.dumps({
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "active_elapsed_seconds": 3.0,
                    "human_approval_seconds": 2.0,
                    "unattributed_wait_seconds": 1.0,
                    "end_to_end_elapsed_seconds": 6.0,
                    "total_elapsed_seconds": 3.0,
                    "accepted": True,
                    "first_pass": True,
                    "completed": True,
                    "actual_owner": "claude-builder" if run["arm"] == "delegation-no-spark" else "codex-fast-path",
                    "route_honored": True,
                    "base_commit": None,
                    "task_input_sha256": None,
                    "stage_seconds": {key: 0.1 for key in experiment.WORKFLOW_STAGES},
                }), encoding="utf-8")
                if run["arm"] == "delegation-no-spark":
                    (metrics.parent / "claude-phase-metrics.json").write_text(json.dumps({
                        "context_acquisition_seconds": 1,
                        "implementation_seconds": 2,
                        "validation_seconds_observed": 3,
                        "tail_seconds": 4,
                    }), encoding="utf-8")
            pricing = {"schema_version": 1, "models": [{
                "pattern": "test-*", "input_per_million": 1.0,
                "cached_input_per_million": 0.1, "output_per_million": 2.0,
                "input_includes_cached": True,
            }]}
            summary = experiment.summarize_manifest(value, root, pricing)
            self.assertTrue(summary["comparable"])
            self.assertTrue(summary["cost_comparable"])
            for arm in experiment.ARMS:
                self.assertEqual(summary["by_arm"][arm]["totals"]["input_tokens"], 10)
                self.assertEqual(summary["descriptive_by_arm"][arm]["median_active_elapsed_seconds"], 3.0)
                self.assertEqual(summary["descriptive_by_arm"][arm]["median_end_to_end_elapsed_seconds"], 6.0)
                self.assertEqual(summary["descriptive_by_arm"][arm]["median_human_approval_seconds"], 2.0)
            self.assertEqual(summary["descriptive_by_arm"]["codex-direct"]["claude_phase_metrics_runs"], 0)
            self.assertEqual(summary["descriptive_by_arm"]["delegation-no-spark"]["median_claude_phase_seconds"]["tail_seconds"], 4.0)
            self.assertEqual(summary["claude_phase_seconds_by_arm"]["full-workflow"], {})
            for pair in summary["paired_comparisons"]:
                self.assertTrue(pair["quality_gate"])
                self.assertTrue(pair["efficiency_gate"])
                self.assertFalse(pair["economy_gate"])
                self.assertEqual(pair["balanced_recommendation"], "retain-baseline-insufficient-savings")

    def test_legacy_total_elapsed_is_treated_as_active_time(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("legacy-time", ["t1"], 1)
            experiment.prepare_runs(value, root)
            for run in value["runs"]:
                (root / run["usage_ledger"]).write_text(json.dumps({
                    "schema_version": 1,
                    "call_id": run["run_id"],
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "role": "claude" if run["arm"] == "delegation-no-spark" else "codex",
                    "stage": "execute",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "usage_complete": True,
                }) + "\n", encoding="utf-8")
                (root / run["run_metrics"]).write_text(json.dumps({
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "total_elapsed_seconds": 4.0,
                    "accepted": True,
                    "first_pass": True,
                    "actual_owner": "claude-builder" if run["arm"] == "delegation-no-spark" else "codex-fast-path",
                    "route_honored": True,
                    "stage_seconds": {key: 0.1 for key in experiment.WORKFLOW_STAGES},
                }), encoding="utf-8")
            summary = experiment.summarize_manifest(value, root)
            self.assertEqual(summary["runs"][0]["active_elapsed_seconds"], 4.0)
            self.assertIsNone(summary["runs"][0]["human_approval_seconds"])

    def test_incomplete_usage_suppresses_token_comparison(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("e1", ["t1"], 1)
            experiment.prepare_runs(value, root)
            for run in value["runs"]:
                complete = run["arm"] != "full-workflow"
                (root / run["usage_ledger"]).write_text(json.dumps({
                    "schema_version": 1,
                    "call_id": run["run_id"],
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "role": "claude" if run["arm"] == "delegation-no-spark" else "codex",
                    "stage": "execute",
                    "input_tokens": 10 if complete else None,
                    "output_tokens": 2 if complete else None,
                    "usage_complete": complete,
                }) + "\n", encoding="utf-8")
                (root / run["run_metrics"]).write_text(json.dumps({
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "total_elapsed_seconds": 3.0,
                    "accepted": True,
                    "first_pass": complete,
                    "actual_owner": "claude-builder" if run["arm"] == "delegation-no-spark" else "codex-fast-path",
                    "route_honored": True,
                    "base_commit": None,
                    "task_input_sha256": None,
                    "stage_seconds": {key: 0.1 for key in experiment.WORKFLOW_STAGES},
                }), encoding="utf-8")
            summary = experiment.summarize_manifest(value, root)
            self.assertFalse(summary["comparable"])
            self.assertFalse(summary["token_comparable"])
            full_pair = next(row for row in summary["paired_comparisons"] if row["arm"] == "full-workflow")
            self.assertFalse(full_pair["usage_complete_both"])
            self.assertIsNone(full_pair["input_output_token_delta"])

    def test_prepare_writes_per_run_context_without_faking_results(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("e1", ["t1"], 1)
            contexts = experiment.prepare_runs(value, root)
            self.assertEqual(len(contexts), 3)
            context = json.loads(contexts[0].read_text(encoding="utf-8"))
            self.assertIn(context["AI_WORKFLOW_EXPERIMENT_ARM"], experiment.ARMS)
            self.assertTrue(context["AI_WORKFLOW_MODEL_USAGE_LEDGER"].endswith("model-usage.jsonl"))
            self.assertTrue(context["AI_WORKFLOW_CLAUDE_PHASE_METRICS_FILE"].endswith("claude-phase-metrics.json"))
            self.assertFalse(Path(context["AI_WORKFLOW_MODEL_USAGE_LEDGER"]).exists())
            template = json.loads((contexts[0].parent / "run-metrics.template.json").read_text(encoding="utf-8"))
            self.assertIn("execute", template["stage_seconds"])
            self.assertIn("review", template["stage_seconds"])
            self.assertEqual(template["claude_phase_metrics_file"], "claude-phase-metrics.json")
            self.assertEqual(template["improvement_units_satisfied"], [])
            self.assertIn("semantic_diff_lines", template)

    def test_spark_cost_is_free_and_improvement_quantity_affects_recommendation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            task_path = root / "task.json"
            task_path.write_text(json.dumps({
                "id": "quantity", "prompt": "Implement the frozen behavior.",
                "improvement_units": [
                    {"id": "IU-1", "description": "Core behavior", "weight": 2},
                    {"id": "IU-2", "description": "Regression coverage", "weight": 1},
                ],
            }), encoding="utf-8")
            value = experiment.build_manifest(
                "quantity", ["quantity"], 1,
                task_inputs={"quantity": {"source": str(task_path), "sha256": experiment._sha256(task_path)}},
            )
            experiment.prepare_runs(value, root)
            for run in value["runs"]:
                context = json.loads(
                    (root / run["artifact_dir"] / "run-context.json").read_text(encoding="utf-8")
                )
                role = "claude" if run["arm"] == "delegation-no-spark" else "codex"
                records = [{
                    "schema_version": 1, "call_id": run["run_id"] + "-paid",
                    "run_id": run["run_id"], "task_id": run["task_id"],
                    "experiment_arm": run["arm"], "role": role,
                    "model": role + "-test", "stage": "execute",
                    "input_tokens": 100, "output_tokens": 10, "usage_complete": True,
                }]
                if run["arm"] == "full-workflow":
                    records.append({
                        "schema_version": 1, "call_id": run["run_id"] + "-spark",
                        "run_id": run["run_id"], "task_id": run["task_id"],
                        "experiment_arm": run["arm"], "role": "spark",
                        "model": "spark-test", "stage": "route",
                        "input_tokens": 1_000_000, "output_tokens": 100_000,
                        "cost_usd": 99.0, "usage_complete": True,
                    })
                (root / run["usage_ledger"]).write_text(
                    "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
                )
                satisfied = ["IU-1"]
                if run["arm"] == "full-workflow":
                    satisfied.append("IU-2")
                if run["arm"] == "delegation-no-spark":
                    satisfied = []
                (root / run["run_metrics"]).write_text(json.dumps({
                    "run_id": run["run_id"], "task_id": run["task_id"],
                    "experiment_arm": run["arm"], "active_elapsed_seconds": 3.0,
                    "total_elapsed_seconds": 3.0, "accepted": True, "first_pass": True,
                    "actual_owner": "claude-builder" if run["arm"] == "delegation-no-spark" else "codex-fast-path",
                    "route_honored": True, "base_commit": None,
                    "task_input_sha256": context["task_input_sha256"],
                    "stage_seconds": {key: 0.1 for key in experiment.WORKFLOW_STAGES},
                    "improvement_units_satisfied": satisfied,
                    "semantic_diff_lines": 40, "changed_files": 2,
                    "tests_added": 1, "tests_passed": 4,
                }), encoding="utf-8")
            pricing = {"schema_version": 1, "models": [
                {"pattern": "codex-*", "input_per_million": 1, "cached_input_per_million": 0, "output_per_million": 1},
                {"pattern": "claude-*", "input_per_million": 1, "cached_input_per_million": 0, "output_per_million": 1},
                {"pattern": "spark-*", "input_per_million": 50, "cached_input_per_million": 0, "output_per_million": 50},
            ]}
            summary = experiment.summarize_manifest(value, root, pricing)
            self.assertFalse(summary["cost_accounting_policy"]["spark_cost_included"])
            self.assertGreater(summary["by_arm"]["full-workflow"]["totals"]["calculated_cost_usd"], 1)
            self.assertLess(summary["billable_by_arm"]["full-workflow"]["totals"]["calculated_cost_usd"], 0.001)
            full = next(row for row in summary["paired_comparisons"] if row["arm"] == "full-workflow")
            delegated = next(row for row in summary["paired_comparisons"] if row["arm"] == "delegation-no-spark")
            self.assertTrue(full["content_quantity_gate"])
            self.assertEqual(full["improvement_weight_delta"], 1.0)
            self.assertFalse(delegated["content_quantity_gate"])
            self.assertEqual(delegated["balanced_recommendation"], "retain-baseline-content-regression")

    def test_artifact_validation_rejects_arm_role_violation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("e1", ["t1"], 1)
            experiment.prepare_runs(value, root)
            for run in value["runs"]:
                ledger = root / run["usage_ledger"]
                ledger.write_text(json.dumps({
                    "call_id": run["run_id"],
                    "role": "spark" if run["arm"] == "codex-direct" else "codex",
                }) + "\n", encoding="utf-8")
                metrics = root / run["run_metrics"]
                metrics.write_text(json.dumps({
                    "actual_owner": "codex-fast-path" if run["arm"] != "delegation-no-spark" else "claude-builder",
                    "route_honored": True,
                    "base_commit": None,
                    "task_input_sha256": None,
                }), encoding="utf-8")
            errors = experiment.validate_manifest(value, root, check_artifacts=True)
            self.assertTrue(any("codex-direct used delegated model" in item for item in errors))

    def test_arm_order_is_counterbalanced_and_auto_arm_is_not_forced(self):
        value = experiment.build_manifest("e1", ["t1"], 3)
        orders = []
        for repetition in range(1, 4):
            rows = [run for run in value["runs"] if run["repetition"] == repetition]
            orders.append([run["arm"] for run in rows])
        self.assertEqual(len({tuple(row) for row in orders}), 3)
        full = next(run for run in value["runs"] if run["arm"] == "full-workflow")
        self.assertEqual(full["arm_contract"]["route_policy"], "skill-auto-route")
        self.assertFalse(value["forced_full_pipeline"])

    def test_real_project_task_is_frozen_and_status_detects_head_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            (project / "README.md").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "base"], check=True)
            task_path = root / "task.json"
            task_path.write_text(json.dumps({
                "id": "real-docs",
                "prompt": "Update the documented example.",
                "allowed_files": ["README.md"],
                "forbidden_files": ["src/**"],
                "validation_commands": ["git diff --check"],
            }), encoding="utf-8")
            binding = {"real-docs": {"source": str(task_path), "sha256": experiment._sha256(task_path)}}
            value = experiment.build_manifest(
                "real-e1", ["real-docs"], 1,
                project_root=str(project), task_inputs=binding,
            )
            experiment.prepare_runs(value, root)
            snapshot = json.loads((root / "experiment-snapshot.json").read_text(encoding="utf-8"))
            frozen = Path(snapshot["task_snapshots"]["real-docs"])
            self.assertEqual(experiment._sha256(frozen), snapshot["task_input_sha256"]["real-docs"])
            status = experiment.experiment_status(value, root)
            self.assertFalse(status["project_state"]["head_drifted"])
            self.assertEqual(status["next_run"]["sequence"], 1)

            (project / "second.txt").write_text("next\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "second.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "next"], check=True)
            status = experiment.experiment_status(value, root)
            self.assertTrue(status["project_state"]["head_drifted"])

    def test_prepare_rejects_dirty_real_project_by_default(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            tracked = project / "tracked.txt"
            tracked.write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "base"], check=True)
            tracked.write_text("two\n", encoding="utf-8")
            value = experiment.build_manifest("dirty", ["t1"], 1, project_root=str(project))
            with self.assertRaisesRegex(ValueError, "tracked modifications"):
                experiment.prepare_runs(value, root)


if __name__ == "__main__":
    unittest.main()
