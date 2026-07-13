"""Focused tests for automatic, observable Spark engagement."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RISK_KEYS = (
    "public_api",
    "data_model",
    "security",
    "migration",
    "permission",
    "concurrency",
    "cross_module",
    "production_impact",
)


def load_script(filename: str):
    spec = importlib.util.spec_from_file_location(filename, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SparkEngagementTests(unittest.TestCase):
    def prepare(self, root: Path, facts: dict, task_id: str) -> dict:
        card = root / "task.md"
        card.write_text("| Mode | builder |\n", encoding="utf-8")
        hints = root / f"{task_id}.json"
        hints.write_text(json.dumps({**facts, "task_id": task_id}), encoding="utf-8")
        output = root / task_id
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "efficiency-control.py"),
                "prepare",
                "--facts",
                str(hints),
                "--task-card",
                str(card),
                "--output-dir",
                str(output),
                "--cache-dir",
                str(root / "cache"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads((output / "execution-plan.json").read_text(encoding="utf-8"))

    def test_prepare_defaults_and_stable_skip_reasons(self):
        no_risks = {key: "no" for key in RISK_KEYS}
        cases = [
            (
                {
                    "target_files_count": 3,
                    "predicted_diff_lines": 120,
                    "exact_validation": True,
                    "effective_risks": no_risks,
                },
                True,
                None,
            ),
            (
                {
                    "target_files_count": 1,
                    "predicted_diff_lines": 5,
                    "exact_validation": True,
                    "effective_risks": no_risks,
                },
                False,
                "skip.sized_tiny_fastpath",
            ),
            (
                {
                    "target_files_count": 3,
                    "predicted_diff_lines": 120,
                    "exact_validation": True,
                    "effective_risks": no_risks,
                    "spark_gate": "off",
                },
                False,
                "skip.explicit_gate_off",
            ),
            (
                {
                    "target_files_count": 3,
                    "predicted_diff_lines": 120,
                    "exact_validation": True,
                    "effective_risks": {**no_risks, "security": "yes"},
                    "quota_mode": "critical",
                },
                False,
                "skip.budget_zero",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (facts, expected_invoke, expected_skip) in enumerate(cases):
                spark = self.prepare(root, facts, f"T-{index}")["spark"]
                self.assertEqual(spark["invoke"], expected_invoke)
                self.assertEqual(spark["skip_reason"], expected_skip)
                self.assertEqual(spark["stage"], "preflight")
                self.assertEqual(spark["mode"], "preflight-bundle")

    def test_preview_exposes_plan_without_invoking_any_model(self):
        dispatch = load_script("dispatch-efficient.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            card = root / "task.md"
            card.write_text("| Mode | builder |\n", encoding="utf-8")
            plan = root / "plan.json"
            spark_policy = {
                "invoke": True,
                "stage": "preflight",
                "mode": "preflight-bundle",
                "skip_reason": None,
            }
            plan.write_text(
                json.dumps(
                    {
                        "task_id": "T",
                        "lane": "standard",
                        "budget": {"claude_calls": 1},
                        "execution": {"single_pass_allowed": False},
                        "spark": spark_policy,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(dispatch, "_tee_subprocess") as tee:
                exit_code = dispatch.main(
                    [
                        "--plan",
                        str(plan),
                        "--task-card",
                        str(card),
                        "--output-dir",
                        str(output),
                        "--ledger",
                        str(root / "ledger.jsonl"),
                    ]
                )

            self.assertEqual(exit_code, 0)
            tee.assert_not_called()
            preview = json.loads(
                (output / "dispatch-preview.json").read_text(encoding="utf-8")
            )
            self.assertFalse(preview["execute"])
            self.assertEqual(preview["spark"], spark_policy)
            self.assertFalse((output / "spark-dispatch.json").exists())

    def test_execute_runs_spark_before_claude_and_continues_on_failure(self):
        dispatch = load_script("dispatch-efficient.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            card = root / "task.md"
            card.write_text("| Mode | builder |\n", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "task_id": "T",
                        "lane": "standard",
                        "budget": {"claude_calls": 1},
                        "execution": {"single_pass_allowed": False},
                        "spark": {
                            "invoke": True,
                            "stage": "preflight",
                            "mode": "preflight-bundle",
                            "skip_reason": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_tee(command, **_kwargs):
                helper = Path(command[1]).name
                calls.append(helper)
                return 7 if helper == "run-codex-spark.sh" else 0

            with mock.patch.object(dispatch, "_tee_subprocess", side_effect=fake_tee):
                exit_code = dispatch.main(
                    [
                        "--plan",
                        str(plan),
                        "--task-card",
                        str(card),
                        "--output-dir",
                        str(output),
                        "--ledger",
                        str(root / "ledger.jsonl"),
                        "--execute",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(calls, ["run-codex-spark.sh", "dispatch-to-claude.sh"])
            record = json.loads(
                (output / "spark-dispatch.json").read_text(encoding="utf-8")
            )
            self.assertEqual(record["exit_code"], 7)
            self.assertTrue(record["continued_to_claude"])
            self.assertEqual(record["skip_reason"], "skip.spark_failed")


if __name__ == "__main__":
    unittest.main()
