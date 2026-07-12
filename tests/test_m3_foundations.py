"""Tests for M3 foundations: Event v2, artifact manifest, resume/replay, bounded review packet.

Covers:
- Event v2 schema validation, causal links, append, duplicate detection
- Event writer atomic append with locking
- Interrupted recovery and replay
- Artifact manifest hashes and traversal
- Bounded diff/log parsing, omitted evidence, prompt size
- Paths with spaces and UTF-8 content
- Binary/redaction detection
- Review shell using stdin/file instead of huge argv
- Python compile and shell syntax
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMAS = ROOT / "schemas"

# Make scripts/ importable
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from event_writer import (
    EventWriter,
    EventValidationError,
    build_event,
    generate_event_id,
    is_legacy_event,
    report_legacy_events,
    validate_event,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_event(**overrides):
    """Return a minimal valid v2 event dict."""
    data = {
        "schema_version": 2,
        "event_id": "test-event-001",
        "parent_event_id": None,
        "run_id": "loop-20260101-120000",
        "task_id": "task-001",
        "iteration": 1,
        "phase": "setup",
        "role": "run-loop",
        "event": "run_start",
        "artifact_refs": [],
        "timestamp": "2026-01-01T12:00:00+00:00",
        "detail": {"key": "value"},
    }
    data.update(overrides)
    return data


def _make_temp_run(tmp_dir: pathlib.Path) -> pathlib.Path:
    """Create a minimal run directory structure for testing."""
    run_dir = tmp_dir / "run"
    run_dir.mkdir()
    return run_dir


# ===========================================================================
# Event v2: schema validation
# ===========================================================================

class TestEventV2Validation(unittest.TestCase):
    """Validate event dicts against v2 schema rules."""

    def test_valid_event(self):
        data = _make_valid_event()
        self.assertEqual(validate_event(data), [])

    def test_missing_schema_version(self):
        data = _make_valid_event()
        del data["schema_version"]
        errors = validate_event(data)
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_wrong_schema_version(self):
        data = _make_valid_event(schema_version=1)
        errors = validate_event(data)
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_missing_event_id(self):
        data = _make_valid_event()
        del data["event_id"]
        errors = validate_event(data)
        self.assertTrue(any("event_id" in e for e in errors))

    def test_empty_event_id(self):
        data = _make_valid_event(event_id="")
        errors = validate_event(data)
        self.assertTrue(any("event_id" in e for e in errors))

    def test_null_parent_event_id(self):
        data = _make_valid_event(parent_event_id=None)
        self.assertEqual(validate_event(data), [])

    def test_valid_parent_event_id(self):
        data = _make_valid_event(parent_event_id="parent-001")
        self.assertEqual(validate_event(data), [])

    def test_empty_parent_event_id(self):
        data = _make_valid_event(parent_event_id="")
        errors = validate_event(data)
        self.assertTrue(any("parent_event_id" in e for e in errors))

    def test_missing_run_id(self):
        data = _make_valid_event()
        del data["run_id"]
        errors = validate_event(data)
        self.assertTrue(any("run_id" in e for e in errors))

    def test_missing_task_id(self):
        data = _make_valid_event()
        del data["task_id"]
        errors = validate_event(data)
        self.assertTrue(any("task_id" in e for e in errors))

    def test_null_iteration(self):
        data = _make_valid_event(iteration=None)
        self.assertEqual(validate_event(data), [])

    def test_positive_iteration(self):
        data = _make_valid_event(iteration=5)
        self.assertEqual(validate_event(data), [])

    def test_zero_iteration(self):
        data = _make_valid_event(iteration=0)
        errors = validate_event(data)
        self.assertTrue(any("iteration" in e for e in errors))

    def test_negative_iteration(self):
        data = _make_valid_event(iteration=-1)
        errors = validate_event(data)
        self.assertTrue(any("iteration" in e for e in errors))

    def test_valid_phases(self):
        for phase in ("setup", "dispatch", "review", "decision", "finalization"):
            data = _make_valid_event(phase=phase)
            self.assertEqual(validate_event(data), [], f"phase '{phase}' should be valid")

    def test_invalid_phase(self):
        data = _make_valid_event(phase="invalid")
        errors = validate_event(data)
        self.assertTrue(any("phase" in e for e in errors))

    def test_valid_roles(self):
        for role in ("run-loop", "dispatch", "reviewer", "claude", "codex", "checker", "system"):
            data = _make_valid_event(role=role)
            self.assertEqual(validate_event(data), [], f"role '{role}' should be valid")

    def test_invalid_role(self):
        data = _make_valid_event(role="invalid")
        errors = validate_event(data)
        self.assertTrue(any("role" in e for e in errors))

    def test_missing_event_name(self):
        data = _make_valid_event()
        del data["event"]
        errors = validate_event(data)
        self.assertTrue(any("event" in e for e in errors))

    def test_empty_event_name(self):
        data = _make_valid_event(event="")
        errors = validate_event(data)
        self.assertTrue(any("event" in e for e in errors))

    def test_artifact_refs_must_be_array(self):
        data = _make_valid_event(artifact_refs="not-an-array")
        errors = validate_event(data)
        self.assertTrue(any("artifact_refs" in e for e in errors))

    def test_artifact_refs_items_must_be_strings(self):
        data = _make_valid_event(artifact_refs=[123])
        errors = validate_event(data)
        self.assertTrue(any("artifact_refs" in e for e in errors))

    def test_missing_timestamp(self):
        data = _make_valid_event()
        del data["timestamp"]
        errors = validate_event(data)
        self.assertTrue(any("timestamp" in e for e in errors))

    def test_detail_must_be_object(self):
        data = _make_valid_event(detail="not-an-object")
        errors = validate_event(data)
        self.assertTrue(any("detail" in e for e in errors))

    def test_not_a_dict(self):
        errors = validate_event("not a dict")
        self.assertTrue(len(errors) > 0)


# ===========================================================================
# Event v2: ID generation
# ===========================================================================

class TestEventIdGeneration(unittest.TestCase):
    """Event ID generation is deterministic enough to correlate but unique."""

    def test_contains_run_id(self):
        eid = generate_event_id("run-001", "test_event")
        self.assertIn("run-001", eid)

    def test_contains_event_name(self):
        eid = generate_event_id("run-001", "dispatch_complete")
        self.assertIn("dispatch_complete", eid)

    def test_contains_iteration(self):
        eid = generate_event_id("run-001", "test", iteration=3)
        self.assertIn("iter3", eid)

    def test_unique_per_call(self):
        ids = {generate_event_id("run-001", "test") for _ in range(100)}
        self.assertEqual(len(ids), 100, "Event IDs should be unique")


# ===========================================================================
# Event v2: building
# ===========================================================================

class TestEventBuilding(unittest.TestCase):
    """build_event produces valid event dicts."""

    def test_minimal_event(self):
        event = build_event(run_id="r1", task_id="t1", event="test")
        self.assertEqual(event["schema_version"], 2)
        self.assertEqual(event["run_id"], "r1")
        self.assertEqual(event["task_id"], "t1")
        self.assertEqual(event["event"], "test")
        self.assertEqual(event["artifact_refs"], [])
        self.assertEqual(event["detail"], {})

    def test_with_all_fields(self):
        event = build_event(
            run_id="r1", task_id="t1", event="test",
            phase="dispatch", role="claude", iteration=2,
            parent_event_id="parent-001",
            artifact_refs=["file.txt"],
            detail={"key": "value"},
        )
        self.assertEqual(event["phase"], "dispatch")
        self.assertEqual(event["role"], "claude")
        self.assertEqual(event["iteration"], 2)
        self.assertEqual(event["parent_event_id"], "parent-001")
        self.assertEqual(event["artifact_refs"], ["file.txt"])

    def test_produces_valid_event(self):
        event = build_event(run_id="r1", task_id="t1", event="test")
        errors = validate_event(event)
        self.assertEqual(errors, [], f"build_event should produce valid events: {errors}")


# ===========================================================================
# Event v2: writer append
# ===========================================================================

class TestEventWriterAppend(unittest.TestCase):
    """EventWriter atomic append with locking."""

    def test_append_single_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            event = build_event(run_id="r1", task_id="t1", event="test")
            event_id = writer.append(event)
            self.assertEqual(event_id, event["event_id"])

    def test_append_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "events.jsonl"
            writer = EventWriter(path)
            event = build_event(run_id="r1", task_id="t1", event="test")
            writer.append(event)
            self.assertTrue(path.exists())

    def test_append_multiple_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            for i in range(5):
                event = build_event(run_id="r1", task_id="t1", event=f"event_{i}")
                writer.append(event)
            events = writer.read_all()
            self.assertEqual(len(events), 5)

    def test_auto_links_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            e1 = build_event(run_id="r1", task_id="t1", event="first")
            writer.append(e1)
            e2 = build_event(run_id="r1", task_id="t1", event="second")
            writer.append(e2)
            events = writer.read_all()
            self.assertIsNone(events[0]["parent_event_id"])
            self.assertEqual(events[1]["parent_event_id"], events[0]["event_id"])

    def test_validation_on_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            with self.assertRaises(EventValidationError):
                writer.append({"invalid": "event"})

    def test_skip_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            # This would fail validation but we skip it
            writer.append({"schema_version": 2, "event_id": "x"}, validate=False)

    def test_utf8_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            event = build_event(run_id="r1", task_id="t1", event="test")
            event["detail"] = {"message": "Passes all tests — go/no-go ✓"}
            writer.append(event)
            events = writer.read_all()
            self.assertIn("✓", events[0]["detail"]["message"])

    def test_spaces_in_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "my folder" / "events.jsonl"
            writer = EventWriter(path)
            event = build_event(run_id="r1", task_id="t1", event="test")
            writer.append(event)
            self.assertTrue(path.exists())


# ===========================================================================
# Event v2: causal links
# ===========================================================================

class TestCausalLinks(unittest.TestCase):
    """Causal parent chain validation."""

    def test_first_event_has_null_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            e = build_event(run_id="r1", task_id="t1", event="start")
            writer.append(e)
            events = writer.read_all()
            self.assertIsNone(events[0]["parent_event_id"])

    def test_chain_links_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventWriter(pathlib.Path(tmp) / "events.jsonl")
            ids = []
            for i in range(5):
                e = build_event(run_id="r1", task_id="t1", event=f"e{i}")
                ids.append(writer.append(e))
            events = writer.read_all()
            for i in range(1, 5):
                self.assertEqual(events[i]["parent_event_id"], events[i-1]["event_id"])


# ===========================================================================
# Event v2: legacy detection
# ===========================================================================

class TestLegacyDetection(unittest.TestCase):
    """Legacy event format detection."""

    def test_v2_event_not_legacy(self):
        data = _make_valid_event()
        self.assertFalse(is_legacy_event(data))

    def test_v1_event_is_legacy(self):
        data = {"time": "2026-01-01", "event": "test"}
        self.assertTrue(is_legacy_event(data))

    def test_explicit_v1_is_legacy(self):
        data = {"schema_version": 1, "event": "test"}
        self.assertTrue(is_legacy_event(data))


# ===========================================================================
# Event v2: validation CLI
# ===========================================================================

class TestValidateRunEventsCLI(unittest.TestCase):
    """validate-run-events.py CLI tests."""

    VALIDATE_SCRIPT = SCRIPTS / "validate-run-events.py"

    def _write_events(self, tmp: pathlib.Path, events: list) -> pathlib.Path:
        path = tmp / "events.jsonl"
        lines = [json.dumps(e, sort_keys=True) for e in events]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_valid_events_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            events = [_make_valid_event(event_id=f"e{i}") for i in range(3)]
            path = self._write_events(tmp, events)
            result = subprocess.run(
                [sys.executable, str(self.VALIDATE_SCRIPT), str(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("ALL VALID", result.stdout)

    def test_duplicate_event_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            events = [
                _make_valid_event(event_id="dup"),
                _make_valid_event(event_id="dup"),
            ]
            path = self._write_events(tmp, events)
            result = subprocess.run(
                [sys.executable, str(self.VALIDATE_SCRIPT), str(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Duplicate", result.stderr)

    def test_missing_parent_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            events = [
                _make_valid_event(event_id="e1", parent_event_id="nonexistent"),
            ]
            path = self._write_events(tmp, events)
            result = subprocess.run(
                [sys.executable, str(self.VALIDATE_SCRIPT), str(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("parent_event_id", result.stderr)

    def test_legacy_report_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            legacy_event = {"time": "2026-01-01", "event": "test"}
            path = self._write_events(tmp, [legacy_event])
            result = subprocess.run(
                [sys.executable, str(self.VALIDATE_SCRIPT), str(path), "--legacy-report"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("legacy", result.stdout.lower())


# ===========================================================================
# Artifact manifest
# ===========================================================================

class TestArtifactManifest(unittest.TestCase):
    """Artifact manifest schema validation."""

    def test_manifest_schema_is_valid_json(self):
        path = SCHEMAS / "artifact-manifest-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_manifest_schema_has_required_fields(self):
        path = SCHEMAS / "artifact-manifest-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        required = set(data.get("required", []))
        self.assertIn("schema_version", required)
        self.assertIn("run_id", required)
        self.assertIn("entries", required)

    def test_manifest_entry_has_required_fields(self):
        path = SCHEMAS / "artifact-manifest-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        entry_props = set(data["properties"]["entries"]["items"]["required"])
        self.assertIn("path", entry_props)
        self.assertIn("sha256", entry_props)
        self.assertIn("size", entry_props)
        self.assertIn("required", entry_props)


# ===========================================================================
# Review packet
# ===========================================================================

class TestReviewPacket(unittest.TestCase):
    """Review packet schema and builder."""

    def test_packet_schema_is_valid_json(self):
        path = SCHEMAS / "review-packet-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_packet_schema_has_required_fields(self):
        path = SCHEMAS / "review-packet-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        required = set(data.get("required", []))
        self.assertIn("schema_version", required)
        self.assertIn("task_summary", required)
        self.assertIn("diff_hunks", required)
        self.assertIn("omitted_evidence", required)


class TestBuildReviewPacket(unittest.TestCase):
    """build-review-packet.py integration tests."""

    BUILD_SCRIPT = SCRIPTS / "build-review-packet.py"

    def test_creates_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            # Create a minimal diff file
            diff_file = run_dir / "dispatch-1.diff"
            diff_file.write_text(
                "diff --git a/test.py b/test.py\n"
                "--- a/test.py\n"
                "+++ b/test.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(self.BUILD_SCRIPT), str(run_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            packet_path = run_dir / "review-packet.json"
            self.assertTrue(packet_path.exists())
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertEqual(packet["schema_version"], 1)
            self.assertIsInstance(packet["diff_hunks"], list)
            self.assertIsInstance(packet["omitted_evidence"], list)

    def test_bounded_diff_hunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            # Create a diff with many files
            diff_lines = []
            for i in range(100):
                diff_lines.extend([
                    f"diff --git a/file{i}.py b/file{i}.py",
                    f"--- a/file{i}.py",
                    f"+++ b/file{i}.py",
                    f"@@ -1 +1 @@",
                    f"-old{i}",
                    f"+new{i}",
                ])
            diff_file = run_dir / "dispatch-1.diff"
            diff_file.write_text("\n".join(diff_lines), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(self.BUILD_SCRIPT), str(run_dir),
                 "--max-diff-hunks", "10"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            packet_path = run_dir / "review-packet.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertLessEqual(len(packet["diff_hunks"]), 10)

    def test_binary_files_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            # Create a binary file
            bin_file = run_dir / "image.png"
            bin_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

            result = subprocess.run(
                [sys.executable, str(self.BUILD_SCRIPT), str(run_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            packet_path = run_dir / "review-packet.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            omitted_paths = [o["path"] for o in packet["omitted_evidence"]]
            self.assertTrue(any("image.png" in p for p in omitted_paths))

    def test_utf8_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            task_file = run_dir / "task-card-001.md"
            task_file.write_text("# Task — Décision ✓", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(self.BUILD_SCRIPT), str(run_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            packet_path = run_dir / "review-packet.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertIn("✓", packet["task_summary"])

    def test_spaces_in_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "my folder" / "run"
            run_dir.mkdir(parents=True)

            result = subprocess.run(
                [sys.executable, str(self.BUILD_SCRIPT), str(run_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)

    def test_prompt_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            prompt_file = pathlib.Path(tmp) / "prompt.txt"

            result = subprocess.run(
                [sys.executable, str(self.BUILD_SCRIPT), str(run_dir),
                 "--prompt-output", str(prompt_file)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(prompt_file.exists())


# ===========================================================================
# Diff parsing: bounded hunks
# ===========================================================================

class TestDiffParsing(unittest.TestCase):
    """Bounded diff hunk parsing."""

    def test_parse_simple_diff(self):
        from build_review_packet import parse_diff_hunks
        diff = (
            "diff --git a/test.py b/test.py\n"
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        hunks = parse_diff_hunks(diff, max_hunks=100)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0]["file"], "test.py")

    def test_truncates_to_max(self):
        from build_review_packet import parse_diff_hunks
        lines = []
        for i in range(50):
            lines.extend([
                f"diff --git a/f{i}.py b/f{i}.py",
                f"--- a/f{i}.py",
                f"+++ b/f{i}.py",
                f"@@ -1 +1 @@",
                f"-old",
                f"+new",
            ])
        hunks = parse_diff_hunks("\n".join(lines), max_hunks=5)
        self.assertEqual(len(hunks), 5)


# ===========================================================================
# Secrets/redaction
# ===========================================================================

class TestRedaction(unittest.TestCase):
    """Secret pattern redaction."""

    def test_redacts_api_key(self):
        from build_review_packet import redact_secrets
        text = "api_key=sk-abc123def456ghi789jkl012mno345pqr678"
        result = redact_secrets(text)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("sk-abc123", result)

    def test_redacts_github_token(self):
        from build_review_packet import redact_secrets
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        result = redact_secrets(text)
        self.assertIn("[REDACTED]", result)

    def test_no_false_positive(self):
        from build_review_packet import redact_secrets
        text = "The event schema_version is 2."
        result = redact_secrets(text)
        self.assertNotIn("[REDACTED]", result)


# ===========================================================================
# Binary detection
# ===========================================================================

class TestBinaryDetection(unittest.TestCase):
    """Binary file detection by extension."""

    def test_png_is_binary(self):
        from build_review_packet import is_binary_path
        self.assertTrue(is_binary_path(pathlib.Path("image.png")))

    def test_py_is_not_binary(self):
        from build_review_packet import is_binary_path
        self.assertFalse(is_binary_path(pathlib.Path("script.py")))

    def test_zip_is_binary(self):
        from build_review_packet import is_binary_path
        self.assertTrue(is_binary_path(pathlib.Path("archive.zip")))

    def test_md_is_not_binary(self):
        from build_review_packet import is_binary_path
        self.assertFalse(is_binary_path(pathlib.Path("readme.md")))


# ===========================================================================
# Resume/replay
# ===========================================================================

class TestResumeRun(unittest.TestCase):
    """resume-run.py integration tests."""

    RESUME_SCRIPT = SCRIPTS / "resume-run.py"

    def test_resume_with_no_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            result = subprocess.run(
                [sys.executable, str(self.RESUME_SCRIPT), str(run_dir)],
                capture_output=True, text=True,
            )
            # Should produce a plan (may warn about missing events)
            plan_path = run_dir / "resume-plan.json"
            self.assertTrue(plan_path.exists())

    def test_resume_with_valid_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "run"
            run_dir.mkdir()
            # Write some events
            writer = EventWriter(run_dir / "loop-events.jsonl")
            e1 = build_event(run_id="r1", task_id="t1", event="run_start", phase="setup")
            writer.append(e1)
            e2 = build_event(run_id="r1", task_id="t1", event="dispatch_complete", phase="dispatch")
            writer.append(e2)

            result = subprocess.run(
                [sys.executable, str(self.RESUME_SCRIPT), str(run_dir)],
                capture_output=True, text=True,
            )
            plan_path = run_dir / "resume-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertIn("resume_safe", plan)
            self.assertIn("latest_safe_phase", plan)


class TestReplayRun(unittest.TestCase):
    """replay-run.py integration tests."""

    REPLAY_SCRIPT = SCRIPTS / "replay-run.py"

    def test_replay_with_valid_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_path = pathlib.Path(tmp) / "events.jsonl"
            writer = EventWriter(events_path)
            e1 = build_event(run_id="r1", task_id="t1", event="run_start", phase="setup")
            writer.append(e1)
            e2 = build_event(run_id="r1", task_id="t1", event="dispatch_complete", phase="dispatch")
            writer.append(e2)

            result = subprocess.run(
                [sys.executable, str(self.REPLAY_SCRIPT), str(events_path)],
                capture_output=True, text=True,
            )
            report_path = events_path.parent / "replay-report.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertGreater(report["total_events"], 0)

    def test_replay_detects_invalid_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_path = pathlib.Path(tmp) / "events.jsonl"
            writer = EventWriter(events_path)
            # setup -> finalization is invalid (should go through dispatch/review/decision)
            e1 = build_event(run_id="r1", task_id="t1", event="run_start", phase="setup")
            writer.append(e1)
            e2 = build_event(run_id="r1", task_id="t1", event="run_complete", phase="finalization")
            writer.append(e2)

            result = subprocess.run(
                [sys.executable, str(self.REPLAY_SCRIPT), str(events_path)],
                capture_output=True, text=True,
            )
            report_path = events_path.parent / "replay-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertGreater(report["summary"]["error_count"], 0)


# ===========================================================================
# Review shell: stdin/file instead of huge argv
# ===========================================================================

class TestReviewShellStdin(unittest.TestCase):
    """review-with-codex.sh must pass prompt via stdin or file."""

    REVIEW_SCRIPT = SCRIPTS / "review-with-codex.sh"

    def test_script_uses_stdin_for_codex(self):
        """review-with-codex.sh must pass prompt via stdin or file, not as argv."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        # Should have stdin/file approach
        self.assertTrue(
            "codex exec --json <" in content or
            "REVIEW_PROMPT_FILE" in content,
            "review-with-codex.sh should pass prompt via stdin or file"
        )

    def test_script_builds_review_packet(self):
        """review-with-codex.sh must call build-review-packet.py."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("build-review-packet.py", content)

    def test_script_has_bounded_fallback(self):
        """review-with-codex.sh must have bounded fallback when packet build fails."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("head -c", content)


# ===========================================================================
# Run-loop: Python gate
# ===========================================================================

class TestRunLoopPythonGate(unittest.TestCase):
    """run-loop.sh must fail before loop if Python is missing."""

    RUN_LOOP = SCRIPTS / "run-loop.sh"

    def test_requires_python(self):
        """run-loop.sh must check for Python before starting."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        self.assertIn("Python 3 is required", content)

    def test_uses_event_writer(self):
        """run-loop.sh must use event_writer.py."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        self.assertIn("event_writer.py", content)

    def test_no_legacy_json_write(self):
        """run-loop.sh must NOT write legacy ad-hoc JSON."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        # The old inline JSON approach used printf with hardcoded fields
        self.assertNotIn('printf \'{"time"', content)


# ===========================================================================
# Installer: copies new assets
# ===========================================================================

class TestInstallerCopiesM3(unittest.TestCase):
    """install_workflow.py must copy M3 assets."""

    def test_event_writer_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("event_writer.py", content)
        self.assertIn("ai/event_writer.py", content)

    def test_validate_run_events_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("validate-run-events.py", content)

    def test_build_review_packet_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("build-review-packet.py", content)

    def test_resume_run_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("resume-run.py", content)

    def test_replay_run_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("replay-run.py", content)

    def test_event_v2_schema_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("run-event-v2.schema.json", content)

    def test_artifact_manifest_schema_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("artifact-manifest-v1.schema.json", content)

    def test_review_packet_schema_installed(self):
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("review-packet-v1.schema.json", content)

    def test_installer_integration(self):
        """Run installer and verify M3 assets are created."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "install_workflow.py"), str(repo)],
                cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True, check=True,
            )
            self.assertTrue((repo / "ai" / "event_writer.py").exists())
            self.assertTrue((repo / "ai" / "validate-run-events.py").exists())
            self.assertTrue((repo / "ai" / "build-review-packet.py").exists())
            self.assertTrue((repo / "ai" / "resume-run.py").exists())
            self.assertTrue((repo / "ai" / "replay-run.py").exists())
            self.assertTrue((repo / "ai" / "schemas" / "run-event-v2.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "artifact-manifest-v1.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "review-packet-v1.schema.json").exists())


# ===========================================================================
# Python compile and shell syntax
# ===========================================================================

class TestPythonCompile(unittest.TestCase):
    """New Python files must compile without syntax errors."""

    def test_event_writer_compiles(self):
        path = SCRIPTS / "event_writer.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")

    def test_validate_run_events_compiles(self):
        path = SCRIPTS / "validate-run-events.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")

    def test_build_review_packet_compiles(self):
        path = SCRIPTS / "build-review-packet.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")

    def test_resume_run_compiles(self):
        path = SCRIPTS / "resume-run.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")

    def test_replay_run_compiles(self):
        path = SCRIPTS / "replay-run.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")


class TestShellSyntax(unittest.TestCase):
    """Shell scripts must pass bash syntax check."""

    def test_run_loop_syntax(self):
        path = SCRIPTS / "run-loop.sh"
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")

    def test_review_with_codex_syntax(self):
        path = SCRIPTS / "review-with-codex.sh"
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")


# ===========================================================================
# Schema files
# ===========================================================================

class TestSchemaFiles(unittest.TestCase):
    """All new schema files must be valid JSON."""

    def test_run_event_v2_schema_valid(self):
        path = SCHEMAS / "run-event-v2.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertEqual(data["properties"]["schema_version"]["const"], 2)

    def test_artifact_manifest_v1_schema_valid(self):
        path = SCHEMAS / "artifact-manifest-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertEqual(data["properties"]["schema_version"]["const"], 1)

    def test_review_packet_v1_schema_valid(self):
        path = SCHEMAS / "review-packet-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertEqual(data["properties"]["schema_version"]["const"], 1)


if __name__ == "__main__":
    unittest.main()
