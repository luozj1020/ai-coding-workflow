"""Focused tests for automatic, observable Spark engagement."""

import importlib.util
import json
import os
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
                False,
                "claude-first-deterministic-route",
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
                self.assertEqual(spark["stage"], "precard-route")
                self.assertEqual(spark["mode"], "execution-cost-estimator")

    def test_generic_value_signals_do_not_trigger_spark_without_route_request(self):
        """Generic uncertainty no longer taxes an otherwise complete owner route."""
        no_risks = {key: "no" for key in RISK_KEYS}
        base = {
            "execution_owner": "claude-builder",
            "target_files_count": 3,
            "predicted_diff_lines": 120,
            "exact_validation": True,
            "effective_risks": no_risks,
        }
        signals = [
            ({"routing_confidence": "medium"}, "signal.routing_confidence_not_high"),
            ({"context_complete": False}, "signal.context_incomplete"),
            ({"may_avoid_claude_retry": True}, "signal.may_avoid_claude_retry"),
            ({"may_avoid_codex_call": True}, "signal.may_avoid_codex_call"),
            ({"predicted_diff_lines": 100, "observed_diff_lines": 150}, "signal.diff_deviates_from_prediction"),
            ({"acceptance_status": "partial"}, "signal.acceptance_partial"),
            ({"failure_attribution": "unclear"}, "signal.failure_attribution_unclear"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (extra, expected_code) in enumerate(signals):
                facts = {**base, **extra}
                spark = self.prepare(root, facts, f"VS-{index}")["spark"]
                self.assertFalse(spark["invoke"], f"Signal {expected_code} must not trigger Spark alone")
                self.assertEqual(spark["trigger_codes"], [])
                self.assertIsNotNone(spark["skip_reason"])

    def test_explicit_uncertain_claude_candidate_triggers_one_estimator(self):
        no_risks = {key: "no" for key in RISK_KEYS}
        facts = {
            "execution_owner": "claude-builder",
            "claude_role": "batch-builder",
            "mechanical_batch": True,
            "task_role": "auxiliary",
            "independent_write_scopes": True,
            "durable_output_required": True,
            "codex_review_scope": "sampled",
            "spark_route_requested": True,
            "target_files_count": 8,
            "predicted_diff_lines": 400,
            "exact_validation": True,
            "effective_risks": no_risks,
        }
        with tempfile.TemporaryDirectory() as tmp:
            spark = self.prepare(Path(tmp), facts, "CANDIDATE")["spark"]
        self.assertTrue(spark["invoke"])
        self.assertEqual(spark["mode"], "execution-cost-estimator")
        self.assertEqual(spark["trigger_codes"], ["route.explicit_claude_candidate_estimate"])

    def test_explicit_on_overrides_no_signals(self):
        """spark_gate=on invokes even without value signals."""
        no_risks = {key: "no" for key in RISK_KEYS}
        facts = {
            "execution_owner": "claude-builder",
            "target_files_count": 3,
            "predicted_diff_lines": 120,
            "exact_validation": True,
            "effective_risks": no_risks,
            "spark_gate": "on",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spark = self.prepare(root, facts, "ON-0")["spark"]
            self.assertTrue(spark["invoke"])
            self.assertEqual(spark["skip_reason"], None)
            self.assertEqual(spark["trigger_codes"], [])

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
                        "execution": {"owner": "claude-builder", "single_pass_allowed": False},
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
                        "execution": {"owner": "claude-builder", "single_pass_allowed": False},
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

    def test_precard_codex_owner_stops_before_any_model_dispatch(self):
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
                        "task_id": "DIRECT",
                        "lane": "standard",
                        "budget": {"claude_calls": 1},
                        "execution": {
                            "owner": "codex-fast-path",
                            "single_pass_allowed": False,
                        },
                        "spark": {"invoke": False, "skip_reason": "skip.precard-direct"},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(dispatch, "_tee_subprocess") as tee:
                exit_code = dispatch.main(
                    [
                        "--plan", str(plan), "--task-card", str(card),
                        "--output-dir", str(output), "--ledger", str(root / "ledger.jsonl"),
                        "--execute",
                    ]
                )
            self.assertEqual(exit_code, 0)
            tee.assert_not_called()
            decision = json.loads((output / "dispatch-decision.json").read_text())
            self.assertEqual(decision["action"], "codex-fast-path")
            self.assertFalse(decision["claude_dispatched"])

    def test_spark_codex_owner_stops_before_claude(self):
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
                        "task_id": "SPARK-DIRECT",
                        "lane": "standard",
                        "budget": {"claude_calls": 1},
                        "execution": {"owner": "claude-builder", "single_pass_allowed": False},
                        "spark": {
                            "invoke": True, "stage": "preflight",
                            "mode": "preflight-bundle", "skip_reason": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_tee(command, **_kwargs):
                calls.append(Path(command[1]).name)
                report = output / "spark-preflight" / "codex-spark.report.md"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    "| Spark auto-disabled? | no |\n"
                    "| Recommended owner | codex-fast-path |\n",
                    encoding="utf-8",
                )
                return 0

            with mock.patch.object(dispatch, "_tee_subprocess", side_effect=fake_tee):
                exit_code = dispatch.main(
                    [
                        "--plan", str(plan), "--task-card", str(card),
                        "--output-dir", str(output), "--ledger", str(root / "ledger.jsonl"),
                        "--execute",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(calls, ["run-codex-spark.sh"])
            record = json.loads((output / "spark-dispatch.json").read_text())
            self.assertFalse(record["continued_to_claude"])


class HostHandoffTests(unittest.TestCase):
    def _prepare(self, root: Path):
        dispatch = load_script("dispatch-efficient.py")
        output = root / "output"
        card = root / "task.md"
        card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "task_id": "HOST-HANDOFF",
                    "lane": "standard",
                    "budget": {"claude_calls": 1},
                    "execution": {"owner": "claude-builder", "single_pass_allowed": False},
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
        args = [
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
        return dispatch, output, args

    @staticmethod
    def _write_handoff_report(output: Path):
        report = output / "spark-preflight" / "codex-spark.report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "| Spark auto-disabled? | yes |\n"
            "| Host handoff required? | yes |\n",
            encoding="utf-8",
        )

    def test_no_authority_records_handoff_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch, output, args = self._prepare(Path(tmp))
            calls = []

            def fake_tee(command, **_kwargs):
                helper = Path(command[1]).name
                calls.append(helper)
                if helper == "run-codex-spark.sh":
                    self._write_handoff_report(output)
                return 0

            with mock.patch.object(dispatch, "_tee_subprocess", side_effect=fake_tee), \
                    mock.patch.object(dispatch, "_run_host_retry_with_timeout") as retry:
                self.assertEqual(dispatch.main(args), 0)

            retry.assert_not_called()
            self.assertEqual(calls, ["run-codex-spark.sh", "dispatch-to-claude.sh"])
            record = json.loads((output / "spark-dispatch.json").read_text(encoding="utf-8"))
            self.assertFalse(record["initial_invoked"])
            self.assertTrue(record["needs_host_execution"])
            self.assertFalse(record["host_retry_attempted"])
            self.assertFalse(record["invoked"])
            self.assertEqual(record["final_state"], "needs_host_execution")
            self.assertEqual(record["skip_reason"], "skip.needs_host_execution")

    def test_uppercase_host_authority_retries_once_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch, output, args = self._prepare(Path(tmp))
            calls = []

            def fake_tee(command, **_kwargs):
                helper = Path(command[1]).name
                calls.append(helper)
                if helper == "run-codex-spark.sh":
                    self._write_handoff_report(output)
                return 0

            def fake_retry(command, timeout, stdout_path, stderr_path):
                self.assertIn("--execution-env", command)
                self.assertEqual(command[command.index("--execution-env") + 1], "host")
                self.assertGreater(timeout, 0)
                self.assertEqual(stdout_path, output / "spark-preflight-host.stdout")
                self.assertEqual(stderr_path, output / "spark-preflight-host.stderr")
                host_report = output / "spark-preflight-host" / "codex-spark.report.md"
                host_report.parent.mkdir(parents=True, exist_ok=True)
                host_report.write_text("| Spark auto-disabled? | no |\n", encoding="utf-8")
                return 0, False

            with mock.patch.dict(os.environ, {"CODEX_SPARK_HOST_AUTHORITY": "TRUE"}), \
                    mock.patch.object(dispatch, "_tee_subprocess", side_effect=fake_tee), \
                    mock.patch.object(
                        dispatch, "_run_host_retry_with_timeout", side_effect=fake_retry
                    ) as retry:
                self.assertEqual(dispatch.main(args), 0)

            retry.assert_called_once()
            self.assertEqual(calls, ["run-codex-spark.sh", "dispatch-to-claude.sh"])
            record = json.loads((output / "spark-dispatch.json").read_text(encoding="utf-8"))
            self.assertTrue(record["host_retry_attempted"])
            self.assertTrue(record["invoked"])
            self.assertFalse(record["auto_disabled"])
            self.assertEqual(record["final_state"], "invoked")

    def test_generic_failure_never_uses_host_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch, output, args = self._prepare(Path(tmp))
            args.append("--host-authority")

            def fake_tee(command, **_kwargs):
                return 7 if Path(command[1]).name == "run-codex-spark.sh" else 0

            with mock.patch.object(dispatch, "_tee_subprocess", side_effect=fake_tee), \
                    mock.patch.object(dispatch, "_run_host_retry_with_timeout") as retry:
                self.assertEqual(dispatch.main(args), 0)

            retry.assert_not_called()
            record = json.loads((output / "spark-dispatch.json").read_text(encoding="utf-8"))
            self.assertFalse(record["needs_host_execution"])
            self.assertFalse(record["host_retry_attempted"])
            self.assertEqual(record["skip_reason"], "skip.spark_failed")

    def test_host_retry_timeout_continues_to_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch, output, args = self._prepare(Path(tmp))
            args.append("--host-authority")
            calls = []

            def fake_tee(command, **_kwargs):
                helper = Path(command[1]).name
                calls.append(helper)
                if helper == "run-codex-spark.sh":
                    self._write_handoff_report(output)
                return 0

            with mock.patch.object(dispatch, "_tee_subprocess", side_effect=fake_tee), \
                    mock.patch.object(
                        dispatch, "_run_host_retry_with_timeout", return_value=(-1, True)
                    ) as retry:
                self.assertEqual(dispatch.main(args), 0)

            retry.assert_called_once()
            self.assertEqual(calls[-1], "dispatch-to-claude.sh")
            record = json.loads((output / "spark-dispatch.json").read_text(encoding="utf-8"))
            self.assertFalse(record["invoked"])
            self.assertTrue(record["host_retry_timed_out"])
            self.assertEqual(record["skip_reason"], "skip.spark_host_retry_timeout")
            self.assertEqual(record["final_state"], "host_retry_timeout")

    def test_invalid_host_retry_timeout_fails_before_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch, _output, args = self._prepare(Path(tmp))
            with mock.patch.object(dispatch, "_tee_subprocess") as tee:
                with self.assertRaises(SystemExit):
                    dispatch.main(args + ["--host-retry-timeout", "0"])
            tee.assert_not_called()

        with tempfile.TemporaryDirectory() as tmp:
            dispatch, _output, args = self._prepare(Path(tmp))
            with mock.patch.dict(
                os.environ, {"CODEX_SPARK_HOST_RETRY_TIMEOUT": "not-a-number"}
            ), mock.patch.object(dispatch, "_tee_subprocess") as tee:
                with self.assertRaises(SystemExit):
                    dispatch.main(args)
            tee.assert_not_called()


if __name__ == "__main__":
    unittest.main()
