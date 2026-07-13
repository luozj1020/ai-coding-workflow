import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("claude_process_state", ROOT / "scripts" / "claude-process-state.py")
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class ClaudeProcessStateTests(unittest.TestCase):
    def classify(self, progress_text, mode):
        with tempfile.TemporaryDirectory() as tmp:
            pid = Path(tmp) / "run.pid"; pid.write_text("99999999")
            progress = Path(tmp) / "run.log"; progress.write_text(progress_text)
            with mock.patch.object(state, "pid_alive", return_value=False):
                return state.classify(pid, progress, mode)

    def test_restricted_unfinished_pid_is_visibility_unknown(self):
        self.assertEqual(self.classify("Claude process started:\nClaude still running", "restricted"), "visibility-unknown")

    def test_terminal_marker_makes_invisible_pid_stopped(self):
        self.assertEqual(self.classify("Claude process started:\nFinal dispatch outcome: success", "restricted"), "not-running")

    def test_finalizing_is_still_unknown_across_namespace(self):
        self.assertEqual(self.classify("Claude process started:\ndispatcher finalizing artifacts", "restricted"), "visibility-unknown")

    def test_normal_environment_preserves_not_running(self):
        self.assertEqual(self.classify("Claude process started:", "normal"), "not-running")

    def test_auto_detects_codex_network_sandbox(self):
        with mock.patch.dict(os.environ, {"CODEX_SANDBOX_NETWORK_DISABLED": "1"}, clear=True):
            self.assertTrue(state.restricted_environment("auto"))

    def test_installer_and_doctor_register_helper(self):
        self.assertIn('("claude-process-state.py", "ai/claude-process-state.py")',
                      (ROOT / "scripts" / "install_workflow.py").read_text())
        self.assertIn("ai/claude-process-state.py", (ROOT / "scripts" / "doctor_workflow.py").read_text())


if __name__ == "__main__":
    unittest.main()
