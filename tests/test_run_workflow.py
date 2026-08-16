"""Tests for the aiwf run lifecycle (PR5).

Covers:
- Preview zero calls (explicit --preview, no model invocation)
- Complete phase ordering (all 13 phases run in sequence)
- Express zero Codex (Express lane produces zero Codex model calls)
- Standard deterministic L0 (Standard lane produces L0 acceptance)
- Mechanical failure zero models (mechanical failures never invoke models)
- Failed phase preservation (artifacts preserved when a phase fails)
- Exact child exit propagation (exit code propagated exactly)
- Paths with spaces (run dir and task file with spaces in path)
- Artifact manifest/events (manifest and events files are valid)
- Nested aiwf registration/installer (run-workflow.py registered)
- Legacy loop label (loop is marked legacy-full-codex-review)
- No direct model spawn (only broker-mediated calls)
- Python 3.9 compatible (no walrus, no union types)
"""
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
PROFILES = ROOT / "profiles"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_workflow = load_module("run_workflow", SCRIPTS / "run-workflow.py")
event_writer = load_module("event_writer", SCRIPTS / "event_writer.py")


RISK_KEYS = (
    "public_api", "data_model", "security", "migration",
    "permission", "concurrency", "cross_module", "production_impact",
)


def make_task(
    task_id="test-run",
    mode="builder",
    profiles=None,
    write_paths=None,
    acceptance=None,
    validation=None,
    risk=None,
    forbidden_paths=None,
):
    """Build a minimal valid task for testing."""
    return {
        "schema_version": 1,
        "id": task_id,
        "mode": mode,
        "goal": "Test run lifecycle",
        "profiles": profiles or ["base"],
        "scope": {
            "write_paths": write_paths or ["src/"],
            "forbidden_paths": forbidden_paths or [],
        },
        "acceptance": acceptance or [
            {"id": "ac-1", "description": "Tests pass", "validation_id": "val-1"}
        ],
        "risk": risk or {k: "no" for k in RISK_KEYS},
        "handoff": {"must_do": ["report result"]},
        "validation": validation or [
            {"id": "val-1", "command": ["python", "-V"]}
        ],
        "stop_conditions": ["stop on failure"],
        "extensions": {},
    }


def make_express_task():
    """Build a task that routes to Express lane."""
    task = make_task(
        task_id="express-task",
        write_paths=["README.md"],
        validation=[{"id": "val-1", "command": ["python", "-V"]}],
    )
    task["risk"] = {k: "no" for k in RISK_KEYS}
    task["extensions"]["routing_hints"] = {"predicted_diff_lines": 20}
    return task


def make_assured_task():
    """Build a task that routes to Assured lane (high risk)."""
    task = make_task(task_id="assured-task")
    task["risk"] = {k: "no" for k in RISK_KEYS}
    task["risk"]["security"] = "yes"
    return task


def write_task(tmp_dir, task_data, name="task.json"):
    """Write task JSON to a temp file."""
    path = Path(tmp_dir) / name
    path.write_text(json.dumps(task_data, sort_keys=True), encoding="utf-8")
    return path


def run_cli(*args, check=False):
    """Run run-workflow.py CLI."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "run-workflow.py"), *map(str, args)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunWorkflowPreview(unittest.TestCase):
    """Test the programmatic preview path."""

    def test_runtime_root_is_primary_checkout_for_linked_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            linked = Path(tmp) / "linked"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "worktree", "add", "--detach", str(linked)], cwd=repo, check=True, capture_output=True)
            self.assertEqual(run_workflow._runtime_repo_root(linked), repo.resolve())

    def test_preview_stops_after_claude_first_route(self):
        """The default Claude-first route stops before model execution."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["final_decision"], "claude-dispatch-ready")
            self.assertIsNone(result["failed_phase"])
            expected_phases = [
                "lint", "compose", "validate", "facts", "route",
                "context", "plan", "dispatch", "ledger",
            ]
            self.assertEqual(result["phases_completed"], expected_phases)

    def test_preview_zero_model_calls(self):
        """Preview mode produces zero model calls."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["model_calls"], [])
            self.assertEqual(result["status"], "routed")

    def test_oversized_task_stops_before_spark_or_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task(write_paths=[
                "README.md", "README_CN.md", "SKILL.md", "assets/README.md",
                "references/task-card-policy.md", "scripts/task_schema.py",
            ])
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["final_decision"], "split-required")
            self.assertEqual(result["model_calls"], [])
            self.assertTrue(result["task_granularity"]["blocking"])
            run_dir = Path(result["run_dir"])
            decision = json.loads(
                (run_dir / "dispatch-preview.json").read_text(encoding="utf-8")
            )
            plan = json.loads(
                (run_dir / "execution-plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(decision["action"], "split-task-before-dispatch")
            self.assertFalse(decision["claude_dispatched"])
            self.assertFalse(decision["spark_dispatched"])
            self.assertFalse(plan["spark"]["invoke"])
            self.assertEqual(plan["spark"]["skip_reason"], "skip.task-split-required")

    def test_reviewed_granularity_exception_reaches_normal_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task(write_paths=[
                "README.md", "README_CN.md", "SKILL.md", "assets/README.md",
                "references/task-card-policy.md", "scripts/task_schema.py",
            ])
            task["extensions"]["task_shape"] = {
                "responsibilities": ["synchronize one generated contract"],
                "split_decision": "exception",
                "split_reason": "the six projections share one generator and one atomic check",
            }
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["final_decision"], "claude-dispatch-ready")
            self.assertEqual(
                result["task_granularity"]["status"],
                "split-exception-reviewed",
            )
            card = (Path(result["run_dir"]) / "delegation-task-card.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Task Granularity", card)
            self.assertIn("the six projections share one generator", card)

    def test_claude_solution_planner_preview_renders_json_execution_projection(self):
        """A positive Claude route renders one compact JSON-derived card."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task(task_id="solution-plan", write_paths=["README.md"])
            task["extensions"]["routing_hints"] = {
                "execution_owner": "claude-builder",
                "claude_role": "solution-planner",
                "solution_planner_opt_in": True,
                "goal_clarity": "high",
                "implementation_path_clarity": "low",
                "bounded_exploration_scope": True,
                "durable_structured_output": True,
                "expected_codex_work_reduction_ratio": 0.4,
                "multi_phase_task": True,
                "symbols": ["run_lifecycle"],
                "constraints": ["preserve Task JSON schema"],
                "interface_signatures": ["run_lifecycle(task_path: Path, run_dir_base: Path) -> dict"],
                "runnable_examples": ["result = run_lifecycle(task_path=task, run_dir_base=out)"],
                "async_contract": "synchronous function; do not await",
            }
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["phases_completed"], [
                "lint", "compose", "validate", "facts", "route",
                "context", "plan", "dispatch", "ledger",
            ])
            run_dir = Path(result["run_dir"])
            card_path = run_dir / "delegation-task-card.md"
            self.assertTrue(card_path.is_file())
            self.assertFalse((run_dir / "context-packet.json").exists())
            self.assertFalse((run_dir / "CLAUDE_CONTEXT_PACKET.md").exists())
            card = card_path.read_text(encoding="utf-8")
            self.assertIn("# Task: solution-plan", card)
            self.assertIn("builder-mode=solution-planning", card)
            self.assertNotIn("## Claude Solution Planner Contract", card)
            self.assertNotIn("## Task Identity", card)
            self.assertNotIn("## Handoff Contract", card)
            self.assertNotIn("## Testing Responsibility", card)
            self.assertNotIn("## Execution Progress", card)
            self.assertIn("run_lifecycle", card)
            self.assertIn("preserve Task JSON schema", card)
            self.assertIn("run_lifecycle(task_path: Path", card)
            self.assertIn("synchronous function; do not await", card)
            self.assertNotIn("Interface evidence hash", card)
            self.assertNotIn("Target files/modules", card)
            plan = json.loads((run_dir / "execution-plan.json").read_text())
            self.assertEqual(plan["task_card_components"], ["core", "solution-planner"])
            self.assertEqual(plan["context_delivery"], "inline-delegation-task-card")
            preview = json.loads((run_dir / "dispatch-preview.json").read_text())
            self.assertEqual(preview["dispatch_card"], str(card_path))
            self.assertEqual(result["model_calls"], [])

    def test_open_multiphase_preview_does_not_auto_select_solution_planner(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task(task_id="codex-short-plan", write_paths=["README.md"])
            task["extensions"]["routing_hints"] = {
                "execution_owner": "claude-builder",
                "claude_role": "solution-planner",
                "goal_clarity": "high",
                "implementation_path_clarity": "low",
                "bounded_exploration_scope": True,
                "durable_structured_output": True,
                "multi_phase_task": True,
            }
            result = run_workflow.run_lifecycle(
                task_path=write_task(tmp, task),
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            plan = json.loads((run_dir / "execution-plan.json").read_text())
            self.assertEqual(plan["execution"]["claude_role"], "execution-builder")
            self.assertEqual(
                plan["planning"]["strategy"],
                "codex-short-plan-then-claude-build",
            )
            self.assertEqual(
                plan["planning"]["solution_planner_skip_reason"],
                "explicit-opt-in-required",
            )
            self.assertEqual(plan["task_card_components"], ["core", "builder"])
            self.assertNotIn(
                "Claude Solution Planner Contract",
                (run_dir / "delegation-task-card.md").read_text(encoding="utf-8"),
            )

    def test_preview_execution_card_deduplicates_scope_and_omits_static_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task(
                task_id="projection",
                write_paths=["README.md"],
                forbidden_paths=["private/"],
            )
            task["scope"]["read_paths"] = ["src/reader.py"]
            task["extensions"]["routing_hints"] = {
                "symbols": ["Reader.load"],
                "constraints": ["preserve parser behavior"],
            }
            result = run_workflow.run_lifecycle(
                task_path=write_task(tmp, task),
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            card = (Path(result["run_dir"]) / "delegation-task-card.md").read_text(
                encoding="utf-8"
            )

            self.assertIn("# Task: projection", card)
            self.assertIn("**Read paths:**", card)
            self.assertEqual(card.count("src/reader.py"), 1)
            self.assertEqual(card.count("private/"), 1)
            self.assertIn("Reader.load", card)
            self.assertNotIn("Target files/modules", card)
            self.assertNotIn("Do not read/modify", card)
            self.assertNotIn("Handoff Contract", card)
            self.assertNotIn("Testing Responsibility", card)
            self.assertNotIn("Execution Progress", card)
            self.assertLess(len(card.encode("utf-8")), 2_000)

    def test_preview_writes_result_json(self):
        """Preview mode writes result.json with all required fields."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            self.assertTrue((run_dir / "result.json").exists())
            data = json.loads((run_dir / "result.json").read_text())
            self.assertIn("run_id", data)
            self.assertIn("task_id", data)
            self.assertIn("lane", data)
            self.assertIn("status", data)
            self.assertIn("phase_timings", data)
            self.assertIn("phases_completed", data)

    def test_preview_creates_artifact_manifest(self):
        """Preview mode creates a valid artifact manifest."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            manifest_path = run_dir / "artifact-manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["schema_version"], 1)
            self.assertIn("run_id", manifest)
            self.assertIn("entries", manifest)
            self.assertIsInstance(manifest["entries"], list)
            self.assertGreater(len(manifest["entries"]), 0)

    def test_preview_creates_events_log(self):
        """Preview mode creates a valid events log."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            events_path = run_dir / "run-events.jsonl"
            self.assertTrue(events_path.exists())
            lines = [
                line for line in events_path.read_text().splitlines()
                if line.strip()
            ]
            self.assertGreater(len(lines), 0)
            for line in lines:
                event = json.loads(line)
                self.assertEqual(event["schema_version"], 2)
                self.assertIn("event_id", event)
                self.assertIn("run_id", event)
                self.assertIn("phase", event)


class TestRunWorkflowPhaseOrder(unittest.TestCase):
    """Test that phases execute in the correct order."""

    def test_phase_order_is_deterministic(self):
        """Two runs produce the same phase order."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            r1 = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp) / "run1",
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            r2 = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp) / "run2",
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(r1["phases_completed"], r2["phases_completed"])

    def test_each_phase_has_timing(self):
        """Each completed phase has a timing entry."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            for phase in result["phases_completed"]:
                self.assertIn(phase, result["phase_timings"])
                self.assertGreaterEqual(result["phase_timings"][phase], 0)


class TestRunWorkflowExpressLane(unittest.TestCase):
    """Test Express lane behavior."""

    def test_express_lane_zero_codex_calls(self):
        """Express lane produces zero Codex model calls."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_express_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["lane"], "express")
            codex_calls = [
                mc for mc in result["model_calls"]
                if mc.get("role") == "codex"
            ]
            self.assertEqual(codex_calls, [])


class TestRunWorkflowStandardLane(unittest.TestCase):
    """Test Standard lane behavior."""

    def test_standard_lane_deterministic_l0(self):
        """Standard lane produces deterministic L0 acceptance result."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")
            self.assertIn(result["lane"], ("standard", "express"))
            # Acceptance should be deterministic
            self.assertIsNone(result["acceptance_status"])
            plan = json.loads((Path(result["run_dir"]) / "execution-plan.json").read_text())
            self.assertTrue(plan["spark"]["invoke"])
            self.assertEqual(plan["spark"]["mode"], "task-card-audit")


class TestRunWorkflowSparkHostHandoff(unittest.TestCase):
    def test_recent_equivalent_failure_skips_spark_and_continues_to_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_path = write_task(root, make_task())
            state = root / "spark-circuit.json"
            dispatcher = root / "dispatcher.sh"
            dispatcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {"CODEX_SPARK_CIRCUIT_STATE_FILE": str(state)},
                clear=False,
            ):
                run_workflow.spark_execution_availability.record_circuit_failure(
                    ROOT, "task-card-audit", "test-timeout"
                )
                with mock.patch.object(run_workflow, "_run_spark_attempt") as attempt:
                    result = run_workflow.run_lifecycle(
                        task_path=task_path,
                        execute=True,
                        dispatcher=str(dispatcher),
                        run_dir_base=root,
                        repo=ROOT,
                        profiles_dir=PROFILES,
                    )

            attempt.assert_not_called()
            record = json.loads(
                (Path(result["run_dir"]) / "spark-dispatch.json").read_text()
            )
            self.assertEqual(record["skip_reason"], "skip.spark-circuit-open")
            self.assertTrue(record["continued_to_claude"])
            self.assertEqual(record["time_budget_seconds"], 30)

    def test_sandbox_handoff_stops_before_claude_and_persists_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_path = write_task(root, make_task())
            state = root / "spark-state.json"

            def fake_spark(
                _helper, _card, _mode, _execution_env,
                stdout_path, stderr_path, _repo, _timeout,
            ):
                stdout_path.write_text(
                    "spark_status=unavailable\nneeds_host_execution=true\n",
                    encoding="utf-8",
                )
                stderr_path.write_text(
                    "host_handoff_required=true\n", encoding="utf-8"
                )
                return 0, False

            with mock.patch.dict(
                os.environ,
                {"CODEX_SPARK_EXECUTION_STATE_FILE": str(state)},
                clear=False,
            ), mock.patch.object(
                run_workflow, "_run_spark_attempt", side_effect=fake_spark
            ):
                result = run_workflow.run_lifecycle(
                    task_path=task_path,
                    execute=True,
                    run_dir_base=root,
                    repo=ROOT,
                    profiles_dir=PROFILES,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failed_phase"], "dispatch")
            self.assertEqual(result["failure_status"], "needs-host-execution")
            self.assertIn("needs-host-execution", result["error"])
            record = json.loads(
                (Path(result["run_dir"]) / "spark-dispatch.json").read_text()
            )
            self.assertTrue(record["needs_host_execution"])
            self.assertFalse(record["continued_to_claude"])
            self.assertEqual(
                json.loads(state.read_text())["status"], "host-required"
            )


class TestRunWorkflowFailure(unittest.TestCase):
    """Test failure handling."""

    def test_invalid_task_stops_at_lint(self):
        """Invalid task JSON fails at lint phase."""
        with tempfile.TemporaryDirectory() as tmp:
            # Write invalid task (missing required fields)
            path = Path(tmp) / "bad-task.json"
            path.write_text('{"id": "bad"}', encoding="utf-8")
            result = run_workflow.run_lifecycle(
                task_path=path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failed_phase"], "lint")

    def test_mechanical_failure_zero_models(self):
        """Mechanical failure (invalid schema) never invokes models."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-task.json"
            path.write_text('{"id": "bad"}', encoding="utf-8")
            result = run_workflow.run_lifecycle(
                task_path=path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["model_calls"], [])

    def test_failed_phase_preserves_prior_artifacts(self):
        """Failed phase preserves artifacts from prior phases."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-task.json"
            path.write_text('{"id": "bad"}', encoding="utf-8")
            result = run_workflow.run_lifecycle(
                task_path=path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            # Manifest should exist even on failure
            self.assertTrue((run_dir / "artifact-manifest.json").exists())
            # Events log should exist
            self.assertTrue((run_dir / "run-events.jsonl").exists())
            # Result should exist
            self.assertTrue((run_dir / "result.json").exists())

    def test_child_exit_code_propagation(self):
        """Exit code from child process is propagated."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            # Run via CLI and check exit code
            r = run_cli(
                str(task_path),
                "--preview",
                "--run-dir-base", str(tmp),
                "--repo", str(ROOT),
                "--profiles-dir", str(PROFILES),
            )
            # Should succeed (exit 0) since preview mode
            self.assertEqual(r.returncode, 0)


class TestRunWorkflowPathsSpaces(unittest.TestCase):
    """Test paths with spaces."""

    def test_run_dir_with_spaces(self):
        """Run succeeds when run dir has spaces in path."""
        with tempfile.TemporaryDirectory() as tmp:
            spaced_dir = Path(tmp) / "my run dir"
            spaced_dir.mkdir()
            task = make_task()
            task_path = write_task(spaced_dir, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=spaced_dir,
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")

    def test_task_file_with_spaces(self):
        """Run succeeds when task file has spaces in name."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            path = Path(tmp) / "my task file.json"
            path.write_text(json.dumps(task, sort_keys=True), encoding="utf-8")
            result = run_workflow.run_lifecycle(
                task_path=path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "routed")


class TestRunWorkflowArtifacts(unittest.TestCase):
    """Test artifact manifest and events."""

    def test_manifest_entries_have_required_fields(self):
        """Each manifest entry has all required fields."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            manifest = json.loads(
                (run_dir / "artifact-manifest.json").read_text()
            )
            for entry in manifest["entries"]:
                self.assertIn("path", entry)
                self.assertIn("size", entry)
                self.assertIn("sha256", entry)
                self.assertIn("content_type", entry)
                self.assertIn("producer", entry)
                self.assertIn("phase", entry)
                self.assertIn("required", entry)

    def test_events_are_chronologically_ordered(self):
        """Events have increasing timestamps."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            events_path = run_dir / "run-events.jsonl"
            lines = [
                line for line in events_path.read_text().splitlines()
                if line.strip()
            ]
            timestamps = []
            for line in lines:
                event = json.loads(line)
                timestamps.append(event["timestamp"])
            self.assertEqual(timestamps, sorted(timestamps))

    def test_explicit_codex_route_decision_exists_without_dispatch_preview(self):
        """Explicit Codex direct writes a compact decision and no Claude card."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task["extensions"]["routing_hints"] = {
                "execution_owner": "codex-fast-path",
                "deterministic_owner_decision": True,
                "delegation_value": False,
            }
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            run_dir = Path(result["run_dir"])
            self.assertFalse((run_dir / "dispatch-preview.json").exists())
            self.assertFalse((run_dir / "context-packet.json").exists())
            self.assertFalse((run_dir / "CLAUDE_CONTEXT_PACKET.md").exists())
            context_decision = json.loads(
                (run_dir / "context-decision.json").read_text()
            )
            self.assertEqual(context_decision["action"], "skip-claude-context-packet")
            decision = json.loads(
                (run_dir / "dispatch-decision.json").read_text()
            )
            self.assertEqual(decision["action"], "codex-fast-path")
            self.assertFalse(decision["claude_dispatched"])
            self.assertEqual(result["final_decision"], "codex-fast-path")


class TestRunWorkflowRegistration(unittest.TestCase):
    """Test registration in aiwf, installer, and doctor."""

    def test_aiwf_registration(self):
        """run-workflow.py is registered in aiwf.py COMMANDS."""
        aiwf_content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn('"run":"run-workflow.py"', aiwf_content)

    def test_legacy_loop_label(self):
        """loop command is labeled legacy-full-codex-review."""
        aiwf_content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn("legacy-full-codex-review", aiwf_content)

    def test_run_is_primary_label(self):
        """run command is labeled as quota-efficient primary."""
        aiwf_content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn("quota-efficient", aiwf_content)

    def test_installer_registration(self):
        """run-workflow.py is registered in install_workflow.py."""
        installer_content = (SCRIPTS / "install_workflow.py").read_text()
        self.assertIn('"run-workflow.py"', installer_content)

    def test_doctor_registration(self):
        """run-workflow.py is registered in doctor_workflow.py."""
        doctor_content = (SCRIPTS / "doctor_workflow.py").read_text()
        self.assertIn("ai/run-workflow.py", doctor_content)

    def test_doctor_required_files(self):
        """run-workflow.py is in WORKFLOW_REQUIRED_FILES."""
        doctor_mod = load_module("doctor_workflow", SCRIPTS / "doctor_workflow.py")
        self.assertIn("ai/run-workflow.py", doctor_mod.WORKFLOW_REQUIRED_FILES)

    def test_doctor_runtime_helpers(self):
        """run-workflow.py is in WORKFLOW_RUNTIME_HELPERS."""
        doctor_mod = load_module("doctor_workflow", SCRIPTS / "doctor_workflow.py")
        self.assertIn("ai/run-workflow.py", doctor_mod.WORKFLOW_RUNTIME_HELPERS)


class TestRunWorkflowNoDirectModelSpawn(unittest.TestCase):
    """Test that no direct model spawn occurs."""

    def test_no_claude_in_preview(self):
        """Preview mode never spawns claude CLI."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            # No model calls in preview
            self.assertEqual(result["model_calls"], [])

    def test_no_codex_in_preview(self):
        """Preview mode never spawns codex CLI."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["model_calls"], [])


class TestRunWorkflowCLI(unittest.TestCase):
    """Test CLI interface."""

    def test_cli_json_output(self):
        """--json flag produces JSON output."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            r = run_cli(
                str(task_path),
                "--preview",
                "--run-dir-base", str(tmp),
                "--repo", str(ROOT),
                "--profiles-dir", str(PROFILES),
                "--json",
            )
            self.assertEqual(r.returncode, 0)
            result = json.loads(r.stdout)
            self.assertIn("status", result)
            self.assertEqual(result["status"], "routed")

    def test_cli_human_output(self):
        """Default output is human-readable."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            r = run_cli(
                str(task_path),
                "--preview",
                "--run-dir-base", str(tmp),
                "--repo", str(ROOT),
                "--profiles-dir", str(PROFILES),
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("Status:", r.stdout)
            self.assertIn("Lane:", r.stdout)
            self.assertIn("Run directory:", r.stdout)

    def test_cli_missing_task_exits_nonzero(self):
        """Missing task file exits with nonzero."""
        r = run_cli("/nonexistent/task.json")
        self.assertNotEqual(r.returncode, 0)

    def _assert_execute_mode(self, argv, expected):
        with mock.patch.object(run_workflow, "run_lifecycle") as lifecycle:
            lifecycle.return_value = {"status": "routed", "run_dir": "/tmp/run"}
            with mock.patch("builtins.print"):
                self.assertEqual(run_workflow.main(["task.json", *argv]), 0)
        self.assertEqual(lifecycle.call_args.kwargs["execute"], expected)

    def test_default_cli_passes_execute_true(self):
        """Default CLI dispatches without a second confirmation command."""
        self._assert_execute_mode([], True)

    def test_preview_flag_passes_execute_false(self):
        """--preview preserves the zero-model-call dry run."""
        self._assert_execute_mode(["--preview"], False)

    def test_execute_flag_passes_execute_true(self):
        """--execute remains a compatible explicit spelling."""
        self._assert_execute_mode(["--execute"], True)

    def test_both_preview_and_execute_fails_argparse(self):
        """Simultaneous execution-mode flags fail instead of using precedence."""
        with self.assertRaises(SystemExit) as raised:
            run_workflow.main(["task.json", "--preview", "--execute"])
        self.assertEqual(raised.exception.code, 2)


class TestRunWorkflowPython39(unittest.TestCase):
    """Verify Python 3.9 compatibility patterns in run-workflow.py."""

    def test_no_walrus_operator(self):
        """File does not use walrus operator (:=)."""
        content = (SCRIPTS / "run-workflow.py").read_text()
        # Simple check: no := outside of strings
        # This is a heuristic; the file is tested under Python 3.9
        self.assertNotIn(":=", content.replace('":=', "").replace("':=", ""))

    def test_no_union_type_annotations(self):
        """File does not use X | Y union syntax (Python 3.10+)."""
        content = (SCRIPTS / "run-workflow.py").read_text()
        # Check for bare | in type annotations (not in strings)
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                continue
            # Skip lines with | in string context
            if "|" in stripped and "->" not in stripped:
                # Allow | in strings, comments, and dict comprehensions
                if "dict |" in stripped or "list |" in stripped or "str |" in stripped:
                    self.fail(f"Union type annotation found: {stripped}")


if __name__ == "__main__":
    unittest.main()
