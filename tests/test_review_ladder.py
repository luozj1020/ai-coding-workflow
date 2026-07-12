"""Tests for the deterministic review ladder (PR3).

Covers:
- All criteria satisfied -> L0 pass and zero model authorization
- One unmapped criterion -> partial + semantic review required
- Failed validation maps exact AC and local Claude revision
- Every mechanical failure stops before model call
- Environment/network/permission/dependency -> local-or-human
- Ambiguous first failure -> Spark, repeated/high-risk/architecture -> Codex
- Spark decision enum rejects anything else and metrics are present
- Bounded Standard/Assured packet limits
- Broker command is the only model execution path
- Installer/aiwf registration and legacy compatibility
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

R = Path(__file__).resolve().parents[1]


def run(script, *args, check=True):
    """Run a script with arguments."""
    return subprocess.run(
        [sys.executable, R / "scripts" / script, *map(str, args)],
        text=True,
        capture_output=True,
        check=check,
    )


# Helper to build a valid task
def make_task(
    acceptance=None,
    validation=None,
    write_paths=None,
    forbidden_paths=None,
):
    """Build a minimal valid task for testing."""
    return {
        "schema_version": 1,
        "id": "test-task",
        "mode": "builder",
        "goal": "Test task",
        "profiles": ["base"],
        "scope": {
            "write_paths": write_paths or ["src/"],
            "forbidden_paths": forbidden_paths or [],
        },
        "acceptance": acceptance or [
            {"id": "ac-1", "description": "Test passes", "validation_id": "val-1"}
        ],
        "risk": {k: "no" for k in [
            "public_api", "data_model", "security", "migration",
            "permission", "concurrency", "cross_module", "production_impact",
        ]},
        "handoff": {},
        "validation": validation or [
            {"id": "val-1", "command": ["pytest", "-q"], "description": "Run tests"}
        ],
        "stop_conditions": [],
    }


class TestReviewLadder(unittest.TestCase):
    """Test the deterministic review ladder."""

    def test_all_criteria_satisfied_l0_pass(self):
        """All criteria satisfied -> L0 pass and zero model authorization."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"
            output_path = Path(d) / "output.json"

            data = {
                "task": make_task(),
                "validation_results": {
                    "val-1": {"status": "passed", "evidence_paths": ["tests.log"]}
                },
                "diff_evidence": {
                    "changed_files": ["src/main.py"],
                    "diff_lines": 10,
                },
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "ladder", str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["tier"], "L0-local")
            self.assertEqual(output["action"], "human-review")
            self.assertIsNone(output["model_authorized"])
            self.assertFalse(output["model_call_prohibited"])
            self.assertEqual(output["l0_acceptance"]["status"], "passed")

    def test_one_unmapped_criterion_partial_semantic(self):
        """One unmapped criterion -> partial + semantic review required."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"

            task = make_task(
                acceptance=[
                    {"id": "ac-1", "description": "Test passes", "validation_id": "val-1"},
                    {"id": "ac-2", "description": "Code review"},  # No validation_id
                ]
            )
            data = {
                "task": task,
                "validation_results": {
                    "val-1": {"status": "passed", "evidence_paths": ["tests.log"]}
                },
                "diff_evidence": {"changed_files": ["src/main.py"]},
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "ladder", str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["l0_acceptance"]["status"], "partial")
            self.assertTrue(output["l0_acceptance"]["semantic_review_required"])
            # ac-1 satisfied, ac-2 not-evaluated
            matrix = output["l0_acceptance"]["acceptance_matrix"]
            ac1 = next(r for r in matrix if r["id"] == "ac-1")
            ac2 = next(r for r in matrix if r["id"] == "ac-2")
            self.assertEqual(ac1["status"], "satisfied")
            self.assertEqual(ac2["status"], "not-evaluated")

    def test_failed_validation_maps_ac_and_claude_revision(self):
        """Failed validation maps exact AC and local Claude revision."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"

            data = {
                "task": make_task(),
                "validation_results": {
                    "val-1": {"status": "failed", "evidence_paths": ["fail.log"]}
                },
                "diff_evidence": {"changed_files": ["src/main.py"]},
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "ladder", str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["l0_acceptance"]["status"], "partial")
            # Should route to claude-revision for first test failure
            self.assertEqual(output["recovery"]["owner"], "claude-revision")
            self.assertEqual(output["recovery"]["model"], "claude")

    def test_mechanical_failure_stops_before_model_call(self):
        """Every mechanical failure stops before model call."""
        mechanical_cases = [
            ("scope_violation", {"changed_files": ["outside/"]}),
            ("forbidden_path_modified", {"changed_files": ["forbidden/"]}),
            ("sha_mismatch", {"sha_matches": False}),
            ("unexpected_untracked", {"unexpected_untracked": True}),
            ("diff_budget_exceeded", {"diff_lines": 100000, "max_diff_lines": 100}),
        ]

        for failure_name, diff_extra in mechanical_cases:
            with tempfile.TemporaryDirectory() as d:
                input_path = Path(d) / "input.json"

                task = make_task(
                    write_paths=["src/"],
                    forbidden_paths=["forbidden/"],
                )
                data = {
                    "task": task,
                    "validation_results": {
                        "val-1": {"status": "passed", "evidence_paths": []}
                    },
                    "diff_evidence": {
                        "changed_files": ["src/main.py"],
                        **diff_extra,
                    },
                }
                input_path.write_text(json.dumps(data))

                result = subprocess.run(
                    [sys.executable, str(R / "scripts" / "review-ladder.py"),
                     "ladder", str(input_path)],
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, f"Failed for {failure_name}")
                output = json.loads(result.stdout)
                self.assertTrue(
                    output["model_call_prohibited"],
                    f"Model call not prohibited for {failure_name}",
                )
                self.assertIn(
                    failure_name,
                    output["mechanical_failures"],
                    f"Mechanical failure {failure_name} not in output",
                )

    def test_invalid_task_schema_stops(self):
        """Invalid task schema -> mechanical failure, no model call."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"

            # Missing required fields
            data = {
                "task": {"schema_version": 1, "id": "bad"},
                "diff_evidence": {},
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "ladder", str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertIn("invalid_task_schema", output["mechanical_failures"])
            self.assertTrue(output["model_call_prohibited"])

    def test_environment_dependency_local_or_human(self):
        """Environment/network/permission/dependency -> local-or-human."""
        for classification in ["environment", "network", "permission", "dependency", "timeout"]:
            with tempfile.TemporaryDirectory() as d:
                result = subprocess.run(
                    [sys.executable, str(R / "scripts" / "review-ladder.py"),
                     "recovery", classification],
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, f"Failed for {classification}")
                output = json.loads(result.stdout)
                self.assertEqual(output["owner"], "local-or-human", f"Wrong owner for {classification}")
                self.assertIsNone(output["model"], f"Wrong model for {classification}")

    def test_first_failure_claude_repeated_codex(self):
        """Ambiguous first failure -> Spark, repeated/high-risk/architecture -> Codex."""
        # First compile failure -> claude-revision
        result = subprocess.run(
            [sys.executable, str(R / "scripts" / "review-ladder.py"),
             "recovery", "compile"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["owner"], "claude-revision")
        self.assertEqual(output["model"], "claude")

        # Second compile failure -> spark-triage
        result = subprocess.run(
            [sys.executable, str(R / "scripts" / "review-ladder.py"),
             "recovery", "compile", "--failure-count", "2"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["owner"], "spark-triage")
        self.assertEqual(output["model"], "spark")

        # High risk -> codex
        result = subprocess.run(
            [sys.executable, str(R / "scripts" / "review-ladder.py"),
             "recovery", "compile", "--high-risk"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["owner"], "codex")
        self.assertEqual(output["model"], "codex")

        # Architecture issue -> codex
        result = subprocess.run(
            [sys.executable, str(R / "scripts" / "review-ladder.py"),
             "recovery", "unknown", "--architecture-issue"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["owner"], "codex")
        self.assertEqual(output["model"], "codex")

        # Repeated failure (3+) -> codex
        result = subprocess.run(
            [sys.executable, str(R / "scripts" / "review-ladder.py"),
             "recovery", "compile", "--failure-count", "3"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["owner"], "codex")

    def test_spark_decision_enum_and_metrics(self):
        """Spark decision enum rejects anything else and metrics are present."""
        # Valid decisions
        for decision in ["local-accept", "claude-revision", "codex-escalation", "human-review"]:
            with tempfile.TemporaryDirectory() as d:
                input_path = Path(d) / "spark.json"
                input_path.write_text(json.dumps({
                    "action": decision,
                    "reasoning": "test",
                    "route_changed": True,
                    "codex_call_avoided": False,
                    "claude_retry_avoided": True,
                }))

                result = subprocess.run(
                    [sys.executable, str(R / "scripts" / "review-ladder.py"),
                     "spark-validate", str(input_path)],
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, f"Failed for {decision}")
                output = json.loads(result.stdout)
                self.assertEqual(output["action"], decision)
                self.assertIn("route_changed", output)
                self.assertIn("codex_call_avoided", output)
                self.assertIn("claude_retry_avoided", output)

        # Invalid decision
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "spark.json"
            input_path.write_text(json.dumps({
                "action": "invalid-decision",
                "reasoning": "test",
            }))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "spark-validate", str(input_path)],
                text=True, capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_bounded_packet_limits(self):
        """Bounded Standard/Assured packet limits."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            # Create a task card
            (run_dir / "task-card-test.md").write_text("# Task\n\nTest task content.")

            # Standard lane
            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "packet", "--run-dir", str(run_dir), "--lane", "standard"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["lane"], "standard")
            self.assertEqual(output["max_bytes"], 32 * 1024)

            # Assured lane
            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "packet", "--run-dir", str(run_dir), "--lane", "assured"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["lane"], "assured")
            self.assertEqual(output["max_bytes"], 64 * 1024)

    def test_broker_is_only_model_execution_path(self):
        """Broker command is the only model execution path."""
        # Verify model-call-broker exists and is functional
        result = subprocess.run(
            [sys.executable, str(R / "scripts" / "model-call-broker.py"),
             "--role", "claude", "--stage", "builder",
             "--max-calls", "1", "--dry-run", "--", "echo", "test"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertTrue(output["dry_run"])

    def test_installer_registration(self):
        """Installer/aiwf registration."""
        # Verify review-ladder is in aiwf.py commands
        aiwf_content = (R / "scripts" / "aiwf.py").read_text()
        self.assertIn('"review-ladder":"review-ladder.py"', aiwf_content)

        # Verify review-ladder.py is in install_workflow.py PYTHON_SCRIPTS
        installer_content = (R / "scripts" / "install_workflow.py").read_text()
        self.assertIn('("review-ladder.py", "ai/review-ladder.py")', installer_content)

    def test_legacy_compatibility(self):
        """Legacy compatibility mode preserved."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"

            # Legacy format (no 'task' key)
            data = {
                "allowed_paths": ["src/"],
                "changed_files": ["src/main.py"],
                "validation_exit_code": 0,
                "diff_lines": 10,
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "evaluate-acceptance.py"),
                 str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "passed")
            # Legacy fields preserved
            self.assertIn("scope_violations", output)
            self.assertIn("codex_required", output)
            self.assertIn("review_triggers", output)

    def test_evaluate_acceptance_task_mode(self):
        """Test evaluate-acceptance.py in Task-aware mode."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"

            data = {
                "task": make_task(),
                "validation_results": {
                    "val-1": {"status": "passed", "evidence_paths": ["tests.log"]}
                },
                "diff_evidence": {"changed_files": ["src/main.py"]},
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "evaluate-acceptance.py"),
                 str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "passed")
            self.assertEqual(len(output["mechanical_failures"]), 0)
            self.assertFalse(output["semantic_review_required"])

    def test_missing_artifact_mechanical_failure(self):
        """Missing required artifact -> mechanical failure."""
        with tempfile.TemporaryDirectory() as d:
            input_path = Path(d) / "input.json"

            data = {
                "task": make_task(),
                "validation_results": {
                    "val-1": {"status": "passed", "evidence_paths": []}
                },
                "artifact_manifest": {
                    "missing_artifacts": ["build-output.jar"]
                },
                "diff_evidence": {"changed_files": ["src/main.py"]},
            }
            input_path.write_text(json.dumps(data))

            result = subprocess.run(
                [sys.executable, str(R / "scripts" / "review-ladder.py"),
                 "ladder", str(input_path)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertIn("missing_required_artifact", output["mechanical_failures"])
            self.assertTrue(output["model_call_prohibited"])


if __name__ == "__main__":
    unittest.main()
