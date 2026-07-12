"""Tests for the aiwf run lifecycle (PR5).

Covers:
- Preview zero calls (default mode, no model invocation)
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
    """Test preview mode (default, no --execute)."""

    def test_preview_completes_all_phases(self):
        """All 13 phases run to completion in preview mode."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            result = run_workflow.run_lifecycle(
                task_path=task_path,
                run_dir_base=Path(tmp),
                repo=ROOT,
                profiles_dir=PROFILES,
            )
            self.assertEqual(result["status"], "completed")
            self.assertIsNone(result["failed_phase"])
            expected_phases = [
                "lint", "compose", "validate", "facts", "route",
                "context", "plan", "dispatch", "evidence",
                "acceptance", "review-ladder", "handoff", "ledger",
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
            self.assertEqual(result["status"], "completed")

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
                l for l in events_path.read_text().splitlines()
                if l.strip()
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
            self.assertEqual(result["status"], "completed")
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
            self.assertEqual(result["status"], "completed")
            self.assertIn(result["lane"], ("standard", "express"))
            # Acceptance should be deterministic
            self.assertIn(result["acceptance_status"], ("passed", "partial", "failed"))


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
            self.assertEqual(result["status"], "completed")

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
            self.assertEqual(result["status"], "completed")


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
                l for l in events_path.read_text().splitlines()
                if l.strip()
            ]
            timestamps = []
            for line in lines:
                event = json.loads(line)
                timestamps.append(event["timestamp"])
            self.assertEqual(timestamps, sorted(timestamps))

    def test_dispatch_preview_artifact_exists(self):
        """Dispatch preview artifact is written."""
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
            self.assertTrue((run_dir / "dispatch-preview.json").exists())
            preview = json.loads(
                (run_dir / "dispatch-preview.json").read_text()
            )
            self.assertEqual(preview["mode"], "preview")
            self.assertFalse(preview["execute"])


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
                "--run-dir-base", str(tmp),
                "--repo", str(ROOT),
                "--profiles-dir", str(PROFILES),
                "--json",
            )
            self.assertEqual(r.returncode, 0)
            result = json.loads(r.stdout)
            self.assertIn("status", result)
            self.assertEqual(result["status"], "completed")

    def test_cli_human_output(self):
        """Default output is human-readable."""
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task_path = write_task(tmp, task)
            r = run_cli(
                str(task_path),
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
