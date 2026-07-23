"""Tests for reviewed dirty-worktree continuation approval and enforcement."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "prepare-worktree-continuation.py"


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=check,
    )


class ReviewedContinuationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        run("git", "init", "-q", cwd=self.repo)
        run("git", "config", "user.email", "test@example.com", cwd=self.repo)
        run("git", "config", "user.name", "Test", cwd=self.repo)
        (self.repo / "scripts").mkdir()
        shutil.copy2(HELPER, self.repo / "scripts" / HELPER.name)
        shutil.copy2(ROOT / "scripts" / "worktree_state_hash.py", self.repo / "scripts")
        (self.repo / "src.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "test.txt").write_text("base test\n", encoding="utf-8")
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-qm", "base", cwd=self.repo)
        self.head = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.worktree = self.repo / ".worktrees" / "task-worktree"
        self.worktree.parent.mkdir()
        run("git", "worktree", "add", "-q", "-b", "task-branch", str(self.worktree), self.head, cwd=self.repo)
        self.task_id = "claude-task"
        self.prior_card = self.worktree / "TASK_CARD_FULL.md"
        self.prior_card.write_text("| Mode | builder |\n", encoding="utf-8")
        runtime = {
            "schema_version": 1,
            "task_id": self.task_id,
            "strategy": "fresh",
            "task_mode": "builder",
            "worktree": str(self.worktree),
            "source_repository": str(self.repo),
            "base_commit": self.head,
            "pid_files": {},
        }
        (self.repo / ".worktrees" / f"{self.task_id}.runtime.json").write_text(
            json.dumps(runtime), encoding="utf-8"
        )
        (self.worktree / "src.txt").write_text("accepted implementation\n", encoding="utf-8")
        self.card = self.repo / "next-card.md"
        self.card.write_text("| Mode | builder |\n", encoding="utf-8")
        self.approval = self.repo / ".worktrees" / "approval.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def helper(self, command: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable, str(self.repo / "scripts" / HELPER.name), command,
            *args, cwd=self.repo, check=check,
        )

    def prepare(self, *, next_role: str = "builder", allow: str = "src.txt") -> dict:
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", next_role,
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", allow,
            "--output", str(self.approval),
        )
        return json.loads(result.stdout)

    def test_prepare_and_validate_bind_exact_state_and_card(self) -> None:
        approval = self.prepare()
        self.assertEqual(approval["prior_strategy"], "fresh")
        self.assertEqual(approval["accepted_existing_paths"], ["src.txt"])
        self.assertEqual(approval["allow_new_write_paths"], ["src.txt"])
        self.assertIn("sha256", approval["accepted_path_state"]["src.txt"])
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(json.loads(validated.stdout)["approval_id"], approval["approval_id"])

        self.card.write_text("| Mode | builder |\nchanged\n", encoding="utf-8")
        rejected = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("next_task_card_sha256", rejected.stderr)

    def test_dirty_snapshot_continuation_binds_source_and_execution_bases(self) -> None:
        run("git", "add", "src.txt", cwd=self.worktree)
        run("git", "commit", "-qm", "synthetic dirty snapshot", cwd=self.worktree)
        snapshot_commit = run("git", "rev-parse", "HEAD", cwd=self.worktree).stdout.strip()
        (self.worktree / "src.txt").write_text(
            "accepted implementation after snapshot\n", encoding="utf-8"
        )
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime.update({
            "source_base_commit": self.head,
            "execution_base_commit": snapshot_commit,
            "worktree_start_commit": snapshot_commit,
            "dirty_snapshot_commit": snapshot_commit,
        })
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

        approval = self.prepare()

        self.assertEqual(approval["base_commit"], self.head)
        self.assertEqual(approval["source_base_commit"], self.head)
        self.assertEqual(approval["execution_base_commit"], snapshot_commit)
        self.assertEqual(approval["worktree_head"], snapshot_commit)
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(json.loads(validated.stdout)["approval_id"], approval["approval_id"])

    def test_prepare_rejects_wrong_paths_and_non_fresh_strategy(self) -> None:
        wrong = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "test.txt",
            "--allow-new-write-path", "test.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(wrong.returncode, 2)
        self.assertFalse(self.approval.exists())

        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["strategy"] = "reuse-managed"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        rejected = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("not reviewable", rejected.stderr)

    def test_prepare_rejects_zero_byte_placeholder_as_implementation(self) -> None:
        (self.worktree / "src.txt").write_bytes(b"")
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no material implementation evidence", result.stderr)

    def test_validate_rejects_worktree_drift(self) -> None:
        self.prepare()
        (self.worktree / "src.txt").write_text("drifted\n", encoding="utf-8")
        result = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card), check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("state drifted", result.stderr)

    def test_post_run_allows_declared_new_path_and_rejects_outside_path(self) -> None:
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        self.prepare(next_role="checker-test", allow="test.txt")
        (self.worktree / "test.txt").write_text("new test\n", encoding="utf-8")
        passed = self.helper("post-run", "--approval", str(self.approval))
        self.assertTrue(json.loads(passed.stdout)["protected_existing_unchanged"])

        (self.worktree / "outside.txt").write_text("unexpected\n", encoding="utf-8")
        rejected = self.helper(
            "post-run", "--approval", str(self.approval), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("outside approval", rejected.stderr)

    def test_checker_cannot_modify_accepted_implementation(self) -> None:
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        self.prepare(next_role="checker-test", allow="test.txt")
        (self.worktree / "src.txt").write_text("checker changed implementation\n", encoding="utf-8")
        result = self.helper("post-run", "--approval", str(self.approval), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("accepted existing paths", result.stderr)

    def test_installer_and_cli_expose_helper(self) -> None:
        installer = (ROOT / "scripts" / "install_workflow.py").read_text(encoding="utf-8")
        cli = (ROOT / "scripts" / "aiwf.py").read_text(encoding="utf-8")
        dispatch = (ROOT / "scripts" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
        self.assertIn(
            '("prepare-worktree-continuation.py", "ai/prepare-worktree-continuation.py")',
            installer,
        )
        self.assertIn('"reviewed-continuation":"prepare-worktree-continuation.py"', cli)
        self.assertIn("CLAUDE_CODE_REVIEWED_CONTINUATION", dispatch)
        self.assertIn("reviewed-continuation-consumed-", dispatch)


if __name__ == "__main__":
    unittest.main()
