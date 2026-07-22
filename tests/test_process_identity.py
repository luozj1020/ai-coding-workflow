import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "process-identity.py"
SPEC = importlib.util.spec_from_file_location("process_identity", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ProcessIdentityTests(unittest.TestCase):
    def test_current_process_identity_matches(self):
        identity = MOD.capture(os.getpid(), "task", "dispatcher")
        status, _ = MOD.check(identity)
        self.assertEqual(status, "running-same-process")

    def test_same_pid_with_foreign_start_time_is_not_running_identity(self):
        identity = MOD.capture(os.getpid(), "task", "dispatcher")
        identity["start_time_ticks"] += 1
        status, detail = MOD.check(identity)
        self.assertEqual(status, "pid-reused-or-foreign")
        self.assertIn("start_time_ticks", detail["mismatched_fields"])

    def test_identity_metadata_must_match_task_and_role(self):
        identity = MOD.capture(os.getpid(), "task", "dispatcher")
        status, detail = MOD.check(identity, "another-task", "dispatcher")
        self.assertEqual(status, "invalid-identity")
        self.assertEqual(detail["mismatched_fields"], ["task_id"])

    def test_cli_capture_and_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "identity.json"
            captured = subprocess.run(
                [sys.executable, str(SCRIPT), "capture", "--pid", str(os.getpid()),
                 "--task-id", "T", "--role", "dispatcher", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(captured.returncode, 0, captured.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["task_id"], "T")
            checked = subprocess.run(
                [sys.executable, str(SCRIPT), "check", "--identity", str(output),
                 "--task-id", "T", "--role", "dispatcher"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stdout)


if __name__ == "__main__":
    unittest.main()
