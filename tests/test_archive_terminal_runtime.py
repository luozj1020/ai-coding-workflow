from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive-terminal-runtime.py"
SPEC = importlib.util.spec_from_file_location("archive_terminal_runtime", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ArchiveTerminalRuntimeTests(unittest.TestCase):
    def test_preview_then_apply_retains_minimal_final_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            worktree = repo / "execution"
            worktrees = repo / ".worktrees"
            worktree.mkdir(parents=True)
            worktrees.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=worktree, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=worktree, check=True)
            (worktree / "TASK_CARD_FULL.md").write_text("# card\n", encoding="utf-8")
            (worktree / "source.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=worktree, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=worktree, check=True)
            task_id = "task-1"
            (worktrees / f"{task_id}.runtime.json").write_text(json.dumps({
                "task_id": task_id, "worktree": str(worktree),
            }), encoding="utf-8")
            (worktrees / f"{task_id}.progress.log").write_text("noise\n", encoding="utf-8")
            (worktrees / f"{task_id}.diff").write_text("diff\n", encoding="utf-8")
            (worktrees / f"{task_id}.codex-write-owner.json").write_text(
                '{"status":"authorized"}\n', encoding="utf-8",
            )
            preview = MOD.plan(repo, task_id)
            self.assertEqual(preview["status"], "preview")
            self.assertTrue((worktrees / f"{task_id}.progress.log").exists())
            final = MOD.apply(preview)
            self.assertEqual(final["status"], "archived")
            self.assertTrue((worktrees / f"{task_id}.task-card.md").exists())
            self.assertTrue((worktrees / f"{task_id}.diff").exists())
            self.assertTrue((worktrees / f"{task_id}.codex-write-owner.json").exists())
            self.assertTrue((worktrees / f"{task_id}.final-index.json").exists())
            self.assertFalse((worktrees / f"{task_id}.progress.log").exists())
            self.assertTrue((worktrees / "archive" / task_id / f"{task_id}.progress.log").exists())


if __name__ == "__main__":
    unittest.main()
