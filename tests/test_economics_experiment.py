import importlib.util
import json
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
    def test_manifest_has_balanced_arms_for_every_task_and_repetition(self):
        value = experiment.build_manifest("e1", ["t1", "t2"], 2)
        self.assertEqual(experiment.validate_manifest(value), [])
        self.assertEqual(len(value["runs"]), 12)

    def test_manifest_rejects_missing_and_duplicate_arms(self):
        value = experiment.build_manifest("e1", ["t1"], 1)
        value["runs"].pop()
        value["runs"].append(dict(value["runs"][0]))
        errors = experiment.validate_manifest(value)
        self.assertTrue(any("duplicate run tuple" in item for item in errors))
        self.assertTrue(any("missing run tuples" in item for item in errors))

    def test_artifact_check_and_summary(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("e1", ["t1"], 1)
            self.assertTrue(experiment.validate_manifest(value, root, True))
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
                    "role": "codex",
                    "stage": "execute",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "usage_complete": True,
                }) + "\n", encoding="utf-8")
                metrics.write_text(json.dumps({
                    "run_id": run["run_id"],
                    "task_id": run["task_id"],
                    "experiment_arm": run["arm"],
                    "total_elapsed_seconds": 3.0,
                    "accepted": True,
                    "first_pass": True,
                    "stage_seconds": {key: 0.1 for key in experiment.WORKFLOW_STAGES},
                }), encoding="utf-8")
            summary = experiment.summarize_manifest(value, root)
            self.assertTrue(summary["comparable"])
            for arm in experiment.ARMS:
                self.assertEqual(summary["by_arm"][arm]["totals"]["input_tokens"], 10)

    def test_prepare_writes_per_run_context_without_faking_results(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = experiment.build_manifest("e1", ["t1"], 1)
            contexts = experiment.prepare_runs(value, root)
            self.assertEqual(len(contexts), 3)
            context = json.loads(contexts[0].read_text(encoding="utf-8"))
            self.assertIn(context["AI_WORKFLOW_EXPERIMENT_ARM"], experiment.ARMS)
            self.assertTrue(context["AI_WORKFLOW_MODEL_USAGE_LEDGER"].endswith("model-usage.jsonl"))
            self.assertFalse(Path(context["AI_WORKFLOW_MODEL_USAGE_LEDGER"]).exists())
            template = json.loads((contexts[0].parent / "run-metrics.template.json").read_text(encoding="utf-8"))
            self.assertIn("execute", template["stage_seconds"])
            self.assertIn("review", template["stage_seconds"])


if __name__ == "__main__":
    unittest.main()
