import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "spark_execution_availability.py"


def load_module():
    spec = importlib.util.spec_from_file_location("spark_execution_availability", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SparkExecutionAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_host_success_is_reused_within_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state = repo / "state.json"
            with mock.patch.dict(
                os.environ,
                {"CODEX_SPARK_EXECUTION_STATE_FILE": str(state)},
                clear=False,
            ):
                self.module.record(repo, "host-available", "test-success")
                result = self.module.preference(repo)

            self.assertTrue(result["cache_valid"])
            self.assertEqual(result["preferred_execution_env"], "host")
            self.assertEqual(result["status"], "host-available")

    def test_expired_observation_returns_to_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state = repo / "state.json"
            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_SPARK_EXECUTION_STATE_FILE": str(state),
                    "CODEX_SPARK_EXECUTION_STATE_TTL_SECONDS": "10",
                },
                clear=False,
            ):
                self.module.record(repo, "host-required", "test-handoff")
                now = datetime.now(timezone.utc) + timedelta(seconds=11)
                result = self.module.preference(repo, now=now)

            self.assertFalse(result["cache_valid"])
            self.assertEqual(result["preferred_execution_env"], "auto")
            self.assertEqual(result["status"], "expired")

    def test_context_change_invalidates_cached_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state = repo / "state.json"
            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_SPARK_EXECUTION_STATE_FILE": str(state),
                    "CODEX_SPARK_MODEL": "spark-a",
                },
                clear=False,
            ):
                self.module.record(repo, "host-available", "test-success")
            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_SPARK_EXECUTION_STATE_FILE": str(state),
                    "CODEX_SPARK_MODEL": "spark-b",
                },
                clear=False,
            ):
                result = self.module.preference(repo)

            self.assertFalse(result["cache_valid"])
            self.assertEqual(result["status"], "context-mismatch")

    def test_state_file_is_machine_readable_and_atomic_result_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state = repo / "state.json"
            with mock.patch.dict(
                os.environ,
                {"CODEX_SPARK_EXECUTION_STATE_FILE": str(state)},
                clear=False,
            ):
                written = self.module.record(
                    repo,
                    "host-suspected-unavailable",
                    "host-timeout",
                    {"exit_code": -1},
                )
                persisted = json.loads(state.read_text(encoding="utf-8"))

            self.assertEqual(persisted["status"], "host-suspected-unavailable")
            self.assertEqual(persisted["preferred_execution_env"], "host")
            self.assertEqual(written["state_file"], str(state))


if __name__ == "__main__":
    unittest.main()
