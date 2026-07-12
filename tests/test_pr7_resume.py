"""Tests for PR7 Resume Integrity.

Covers:
- Resume types: resume-from-dispatch, resume-from-partial-diff, resume-from-review,
  resume-from-decision, requires-human, unsafe-corrupted
- Hash verification before reuse
- Dispatch complete + review failed resumes from existing evidence
- Corruption fails closed
- review_failed alone is recoverable
- Windows paths and Python 3.9 compatibility
- Installer/aiwf registration
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name, path):
    """Load a Python module from file path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resume_run = load_module("resume_run", SCRIPTS / "resume-run.py")
event_writer = load_module("event_writer", SCRIPTS / "event_writer.py")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_run_dir(tmp_dir, events=None, manifest=None):
    """Create a minimal run directory with events and manifest."""
    run_dir = Path(tmp_dir) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    if events:
        events_path = run_dir / "run-events.jsonl"
        writer = event_writer.EventWriter(events_path)
        for event in events:
            writer.append(event, validate=False)

    if manifest:
        manifest_path = run_dir / "artifact-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    return run_dir


def make_event(run_id="test-run", task_id="test-task", event="setup_complete",
               phase="setup", iteration=None, detail=None):
    """Build a minimal event dict."""
    return event_writer.build_event(
        run_id=run_id,
        task_id=task_id,
        event=event,
        phase=phase,
        iteration=iteration,
        detail=detail or {},
    )


class TestResumeTypes(unittest.TestCase):
    """Test that resume-run.py outputs exact PR7 resume types."""

    def test_resume_from_dispatch(self):
        """Dispatch complete, review not started → resume-from-dispatch."""
        events = [
            make_event(event="setup_complete", phase="setup"),
            make_event(event="dispatch_complete", phase="dispatch"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            self.assertEqual(plan["resume_type"], "resume-from-dispatch")
            self.assertTrue(plan["resume_safe"])

    def test_resume_from_review(self):
        """Review complete, decision not made → resume-from-review."""
        events = [
            make_event(event="setup_complete", phase="setup"),
            make_event(event="dispatch_complete", phase="dispatch"),
            make_event(event="review_complete", phase="review"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            self.assertEqual(plan["resume_type"], "resume-from-review")
            self.assertTrue(plan["resume_safe"])

    def test_resume_from_decision(self):
        """Decision made, finalization not complete → resume-from-decision."""
        events = [
            make_event(event="setup_complete", phase="setup"),
            make_event(event="dispatch_complete", phase="dispatch"),
            make_event(event="review_complete", phase="review"),
            make_event(event="decision", phase="decision"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            self.assertEqual(plan["resume_type"], "resume-from-decision")
            self.assertTrue(plan["resume_safe"])

    def test_resume_from_partial_diff(self):
        """Dispatch incomplete → resume-from-partial-diff."""
        events = [
            make_event(event="setup_complete", phase="setup"),
            make_event(event="dispatch_incomplete", phase="dispatch"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            self.assertEqual(plan["resume_type"], "resume-from-partial-diff")
            self.assertTrue(plan["resume_safe"])

    def test_unsafe_corrupted(self):
        """Hash mismatch → unsafe-corrupted (fails closed)."""
        events = [
            make_event(event="setup_complete", phase="setup"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], ["Hash mismatch for x: expected a..., got b..."],
                [], True
            )
            self.assertEqual(plan["resume_type"], "unsafe-corrupted")
            self.assertFalse(plan["resume_safe"])

    def test_requires_human(self):
        """Ambiguous state → requires-human."""
        # No completion events at all
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            self.assertEqual(plan["resume_type"], "requires-human")
            self.assertFalse(plan["resume_safe"])


class TestReviewFailedRecoverable(unittest.TestCase):
    """Test that review_failed alone is recoverable (not corruption)."""

    def test_dispatch_complete_review_failed_is_recoverable(self):
        """Dispatch complete + review failed → resume-from-review (recoverable)."""
        events = [
            make_event(event="setup_complete", phase="setup"),
            make_event(event="dispatch_complete", phase="dispatch"),
            make_event(event="review_failed", phase="review"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            self.assertEqual(plan["resume_type"], "resume-from-review")
            self.assertTrue(plan["resume_safe"])

    def test_review_failed_alone_is_recoverable(self):
        """review_failed without corruption → not unsafe-corrupted."""
        events = [
            make_event(event="setup_complete", phase="setup"),
            make_event(event="review_failed", phase="review"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            plan = resume_run.build_resume_plan(
                run_dir, events, [], [], [], False
            )
            # review_failed alone is recoverable, not corruption
            self.assertNotEqual(plan["resume_type"], "unsafe-corrupted")


class TestHashVerification(unittest.TestCase):
    """Test hash verification before artifact reuse."""

    def test_valid_hash_passes(self):
        """Valid hash → no corruption."""
        content = b"test content"
        expected_hash = sha256_bytes(content)
        manifest = {
            "schema_version": 1,
            "run_id": "test",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": [
                {
                    "path": "test.txt",
                    "size": len(content),
                    "sha256": expected_hash,
                    "content_type": "text/plain",
                    "producer": "test",
                    "phase": "setup",
                    "required": True,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "test.txt").write_bytes(content)
            (run_dir / "artifact-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            entries, errors, has_corruption = resume_run.validate_manifest(run_dir)
            self.assertFalse(has_corruption)
            self.assertEqual(errors, [])

    def test_invalid_hash_detected(self):
        """Invalid hash → corruption detected."""
        manifest = {
            "schema_version": 1,
            "run_id": "test",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": [
                {
                    "path": "test.txt",
                    "size": 12,
                    "sha256": "0" * 64,
                    "content_type": "text/plain",
                    "producer": "test",
                    "phase": "setup",
                    "required": True,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "test.txt").write_bytes(b"test content")
            (run_dir / "artifact-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            entries, errors, has_corruption = resume_run.validate_manifest(run_dir)
            self.assertTrue(has_corruption)
            self.assertTrue(any("Hash mismatch" in e for e in errors))


class TestCLI(unittest.TestCase):
    """Test resume-run.py CLI interface."""

    def test_missing_run_dir_exits_nonzero(self):
        """Missing run directory exits with nonzero."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "resume-run.py"), "/nonexistent"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_json_output(self):
        """--output flag produces JSON output."""
        events = [make_event(event="setup_complete", phase="setup")]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, events=events)
            output_path = Path(tmp) / "plan.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "resume-run.py"),
                    str(run_dir),
                    "--output", str(output_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            plan = json.loads(output_path.read_text())
            self.assertIn("resume_type", plan)
            self.assertIn("resume_safe", plan)


class TestPython39Compat(unittest.TestCase):
    """Verify Python 3.9 compatibility patterns."""

    def test_no_walrus_operator(self):
        """resume-run.py does not use walrus operator."""
        content = (SCRIPTS / "resume-run.py").read_text()
        self.assertNotIn(":=", content.replace('":=', "").replace("':=", ""))

    def test_no_union_type_annotations(self):
        """resume-run.py does not use X | Y union syntax."""
        content = (SCRIPTS / "resume-run.py").read_text()
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                continue
            if "dict |" in stripped or "list |" in stripped or "str |" in stripped:
                self.fail(f"Union type annotation found: {stripped}")


class TestInstallerRegistration(unittest.TestCase):
    """Test that resume-run.py is registered in installer/aiwf."""

    def test_resume_in_aiwf(self):
        """resume is registered in aiwf.py COMMANDS."""
        content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn('"resume":"resume-run.py"', content)


if __name__ == "__main__":
    unittest.main()
