from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect-workflow-feedback.py"


class WorkflowFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        (self.repo / ".worktrees").mkdir(parents=True)
        (self.repo / "ai").mkdir()
        (self.repo / "ai" / "dispatch-to-claude.sh").write_text("runtime\n", encoding="utf-8")
        (self.repo / ".worktrees" / "TASK-1.runtime.json").write_text(
            json.dumps({
                "task_id": "TASK-1",
                "task_mode": "builder",
                "execution_env": "host",
                "runtime_tool_inventory_verified": False,
                "secret_path": "/home/example/private/API_CONFIG.yaml",
                "prompt": "DO_NOT_EXPORT_PROMPT",
            }),
            encoding="utf-8",
        )
        (self.repo / ".worktrees" / "TASK-1.outcome.json").write_text(
            json.dumps({
                "dispatch_outcome": "timeout",
                "builder_started": True,
                "raw_log": "DO_NOT_EXPORT_LOG",
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def test_preview_is_read_only_and_scrubs_raw_content(self) -> None:
        result = self.run_cli(
            "--preview", "--task-id", "TASK-1",
            "--issue", "false-progress", "--rating", "safety=8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["kind"], "task-feedback")
        self.assertEqual(value["task"]["dispatch_outcome"], "timeout")
        self.assertEqual(value["user_feedback"]["ratings"], {"safety": 8})
        self.assertFalse((self.repo / ".ai-workflow").exists())
        self.assertNotIn("DO_NOT_EXPORT", result.stdout)
        self.assertNotIn("/home/example", result.stdout)
        self.assertFalse(value["privacy"]["raw_logs_included"])

    def test_record_writes_local_feedback_and_bundle_aggregates_without_comments(self) -> None:
        first = self.run_cli(
            "--record", "--task-id", "TASK-1", "--issue", "false-progress",
            "--rating", "efficiency=4", "--comment", "local observation",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_path = Path(first.stdout.strip())
        self.assertTrue(first_path.is_file())
        second = self.run_cli(
            "--record", "--task-id", "TASK-2", "--issue", "false-progress",
            "--rating", "efficiency=6", "--comment", "another comment",
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        bundled = self.run_cli("--bundle")
        self.assertEqual(bundled.returncode, 0, bundled.stderr)
        value = json.loads(bundled.stdout)
        self.assertEqual(value["record_count"], 2)
        self.assertEqual(value["issue_counts"], {"false-progress": 2})
        self.assertEqual(value["rating_averages"], {"efficiency": 5.0})
        self.assertNotIn("local observation", bundled.stdout)
        self.assertNotIn("another comment", bundled.stdout)
        self.assertFalse(value["privacy"]["comments_included"])

    def test_invalid_rating_fails_closed(self) -> None:
        result = self.run_cli("--rating", "efficiency=11")
        self.assertEqual(result.returncode, 2)
        self.assertIn("between 1 and 10", result.stderr)

    def test_cli_and_schema_are_registered(self) -> None:
        aiwf = (ROOT / "scripts" / "aiwf.py").read_text(encoding="utf-8")
        self.assertIn('"feedback":"collect-workflow-feedback.py"', aiwf)
        schema = json.loads(
            (ROOT / "schemas" / "workflow-feedback-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["title"], "Workflow Feedback v1")
        policy = ROOT / "references" / "feedback-policy.md"
        self.assertTrue(policy.is_file())
        self.assertIn("references/feedback-policy.md", (ROOT / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
