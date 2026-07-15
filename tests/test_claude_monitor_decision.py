import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-monitor-decision.py"


def load_module():
    spec = importlib.util.spec_from_file_location("claude_monitor_decision", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class ClaudeMonitorDecisionTests(unittest.TestCase):
    def make_case(self, monitor_event):
        temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(temporary.name)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        worktrees = root / ".worktrees"
        task_id = "claude-20990101-compact"
        (worktrees / task_id).mkdir(parents=True)
        (worktrees / f"{task_id}.progress.log").write_text(
            "Claude still running: elapsed_seconds=700 quiet_seconds=650\n",
            encoding="utf-8",
        )
        (worktrees / f"{task_id}.monitor-events.log").write_text(
            monitor_event + "\n", encoding="utf-8"
        )
        args = argparse.Namespace(
            repo_root=root, task_id=task_id, max_changed_paths=8,
            max_summary_chars=240, stale_after=120, interrupt_after=600,
            confirmations=3,
        )
        return temporary, args

    def test_recent_growth_continues_without_codex_review(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event monitor_level=L2 action=inspect evidence_state=diff "
            "quiet_seconds=650 suspect_count=4 elapsed_seconds=700 artifact_growth=yes running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "continue")
        self.assertEqual(value["interrupt_authorized"], "no")

    def test_corroborated_l3_stall_is_only_an_interrupt_candidate(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event monitor_level=L3 action=inspect evidence_state=no-report "
            "quiet_seconds=650 suspect_count=3 elapsed_seconds=700 artifact_growth=no running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "interrupt-candidate")
        self.assertEqual(value["codex_review_required"], "yes")
        self.assertEqual(value["interrupt_authorized"], "no")

    def test_cli_json_is_bounded_and_machine_readable(self):
        temporary, args = self.make_case(
            "monitor_event monitor_level=L1 action=wait evidence_state=none "
            "quiet_seconds=10 suspect_count=0 elapsed_seconds=20 artifact_growth=yes running=yes"
        )
        with temporary:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "snapshot", "--repo-root", str(args.repo_root),
                 "--task-id", args.task_id, "--format", "json"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["interrupt_authorized"], "no")
        self.assertLess(len(result.stdout), 4096)


if __name__ == "__main__":
    unittest.main()
