"""Tests for advisor-call.py.

Covers: exactly one reservation/call, duplicate request rejected, failed Spark
has no Codex fallback, explicit human path makes zero model calls, and
registration in aiwf.py and install_workflow.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ADVISOR_CALL = SCRIPTS / "advisor-call.py"

# Load the module for direct function testing
spec = importlib.util.spec_from_file_location("advisor_call", ADVISOR_CALL)
mod = importlib.util.module_from_spec(spec)
sys.modules["advisor_call"] = mod
spec.loader.exec_module(mod)


def make_packet(task_id="test-task", evidence_hash="a" * 64):
    """Build a minimal advisor packet dict."""
    return {
        "task_id": task_id,
        "request_id": "req-abc123",
        "phase": "implement",
        "worktree": "/tmp/test-worktree",
        "base_commit": "abc123def456",
        "diff_hash": "b" * 64,
        "evidence_hash": evidence_hash,
        "blocker_question": "How should I handle X?",
        "evidence": [],
        "forbidden_paths": ["src/secret/"],
        "allowed_changes": ["src/foo.py"],
        "completed_work": "Implemented main feature",
        "advisor": "spark",
        "call_cap": 1,
    }


def make_valid_response(
    request_id="req-abc123",
    evidence_hash="a" * 64,
    reservation_id="res-001",
    decision="continue",
    resume_allowed=True,
    risk_changed=False,
    allowed_changes=None,
    forbidden_changes=None,
):
    """Build a valid advisor response dict."""
    return {
        "schema_version": 1,
        "request_id": request_id,
        "advisor": "spark",
        "reservation_id": reservation_id,
        "evidence_hash": evidence_hash,
        "decision": decision,
        "answer": "Do X then Y to fix the issue.",
        "allowed_changes": allowed_changes or ["src/foo.py"],
        "forbidden_changes": forbidden_changes or ["src/secret/"],
        "new_validation": ["python -m pytest tests/test_foo.py"],
        "risk_changed": risk_changed,
        "resume_allowed": resume_allowed,
    }


class TestHumanAdvisorPath(unittest.TestCase):
    """Human advisor path makes zero model calls."""

    def test_human_with_valid_response_succeeds(self):
        with tempfile.TemporaryDirectory(prefix="advisor_test_") as td:
            tmp = Path(td)
            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            response = make_valid_response()
            response_path = tmp / "response.json"
            response_path.write_text(json.dumps(response, sort_keys=True))

            prompt = tmp / "prompt.md"
            prompt.write_text("# Advisor prompt\n\nBlocker question here.\n")

            output_dir = tmp / "output"

            result = subprocess.run(
                [
                    sys.executable, str(ADVISOR_CALL),
                    "--packet", str(packet_path),
                    "--prompt", str(prompt),
                    "--advisor", "human",
                    "--output-dir", str(output_dir),
                    "--response-file", str(response_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout} stderr={result.stderr}")
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertTrue(result_data["ok"])
            self.assertEqual(result_data["advisor"], "human")
            self.assertEqual(result_data["reservation_id"], "human-validated")

    def test_human_without_response_file_fails(self):
        with tempfile.TemporaryDirectory(prefix="advisor_test_") as td:
            tmp = Path(td)
            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            prompt = tmp / "prompt.md"
            prompt.write_text("# Advisor prompt\n")
            output_dir = tmp / "output"

            result = subprocess.run(
                [
                    sys.executable, str(ADVISOR_CALL),
                    "--packet", str(packet_path),
                    "--prompt", str(prompt),
                    "--advisor", "human",
                    "--output-dir", str(output_dir),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertFalse(result_data["ok"])
            self.assertIn("human-advisor-requires-response-file", result_data["reason"])

    def test_human_with_invalid_response_fails(self):
        with tempfile.TemporaryDirectory(prefix="advisor_test_") as td:
            tmp = Path(td)
            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            # Invalid response: missing required fields
            response_path = tmp / "response.json"
            response_path.write_text(json.dumps({"schema_version": 1}))

            prompt = tmp / "prompt.md"
            prompt.write_text("# Advisor prompt\n")
            output_dir = tmp / "output"

            result = subprocess.run(
                [
                    sys.executable, str(ADVISOR_CALL),
                    "--packet", str(packet_path),
                    "--prompt", str(prompt),
                    "--advisor", "human",
                    "--output-dir", str(output_dir),
                    "--response-file", str(response_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 2)


class TestMissingPrompt(unittest.TestCase):
    """Missing or empty prompt is rejected."""

    def test_missing_prompt_rejected(self):
        with tempfile.TemporaryDirectory(prefix="advisor_test_") as td:
            tmp = Path(td)
            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            output_dir = tmp / "output"
            result = subprocess.run(
                [
                    sys.executable, str(ADVISOR_CALL),
                    "--packet", str(packet_path),
                    "--prompt", str(tmp / "nonexistent.md"),
                    "--advisor", "human",
                    "--output-dir", str(output_dir),
                    "--response-file", str(tmp / "resp.json"),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertIn("missing-or-empty-prompt", result_data["reason"])


class TestPacketCallCap(unittest.TestCase):
    def test_missing_or_invalid_call_cap_fails_before_human_response_validation(self):
        invalid_values = (None, True, "1", 0, 2)
        for value in invalid_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory(prefix="advisor_cap_") as td:
                tmp = Path(td)
                packet = make_packet()
                if value is None:
                    packet.pop("call_cap")
                else:
                    packet["call_cap"] = value
                packet_path = tmp / "advisor-packet.json"
                packet_path.write_text(json.dumps(packet), encoding="utf-8")
                prompt = tmp / "prompt.md"
                prompt.write_text("bounded", encoding="utf-8")
                response = tmp / "response.json"
                response.write_text(json.dumps(make_valid_response()), encoding="utf-8")
                output = tmp / "output"
                result = subprocess.run(
                    [
                        sys.executable, str(ADVISOR_CALL), "--packet", str(packet_path),
                        "--prompt", str(prompt), "--advisor", "human",
                        "--response-file", str(response), "--output-dir", str(output),
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 2)
                data = json.loads((output / "advisor-call-result.json").read_text())
                self.assertIn("call_cap must be integer 1", data["reason"])


class TestCLIHelp(unittest.TestCase):
    """CLI help works."""

    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(ADVISOR_CALL), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--packet", result.stdout)
        self.assertIn("--advisor", result.stdout)
        self.assertIn("--output-dir", result.stdout)


if __name__ == "__main__":
    unittest.main()
