from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@unittest.skipIf(os.name == "nt", "POSIX process-tree helper")
class KillClaudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        scripts = self.repo / "scripts"
        scripts.mkdir()
        for name in (
            "kill-claude.sh",
            "claude_task_id.py",
            "prepare-codex-takeover.py",
            "process-identity.py",
            "owner_lease.py",
            "worktree_state_hash.py",
        ):
            shutil.copy2(SCRIPTS / name, scripts / name)
        (self.repo / ".worktrees").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_kill_helper_uses_identity_and_removes_pid_hints(self) -> None:
        task_id = "preflight-kill-test"
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        try:
            prefix = self.repo / ".worktrees" / task_id
            identity = subprocess.run(
                [
                    sys.executable,
                    "scripts/process-identity.py",
                    "capture",
                    "--pid",
                    str(process.pid),
                    "--task-id",
                    task_id,
                    "--role",
                    "claude",
                    "--output",
                    str(prefix) + ".claude.process.json",
                ],
                cwd=self.repo,
                text=True,
                capture_output=True,
            )
            self.assertEqual(identity.returncode, 0, identity.stderr)
            (Path(str(prefix) + ".pid")).write_text(
                f"{process.pid}\n", encoding="utf-8"
            )
            (Path(str(prefix) + ".claude.pid")).write_text(
                f"{process.pid}\n", encoding="utf-8"
            )

            stopped = subprocess.run(
                [
                    "bash",
                    "scripts/kill-claude.sh",
                    task_id,
                    "--kill-after",
                    "2",
                ],
                cwd=self.repo,
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(stopped.returncode, 0, stopped.stderr + stopped.stdout)
            process.wait(timeout=3)
            receipt = json.loads(
                Path(str(prefix) + ".manual-stop.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(receipt["status"], "confirmed-inactive")
            self.assertFalse(Path(str(prefix) + ".pid").exists())
            self.assertFalse(Path(str(prefix) + ".claude.pid").exists())
            self.assertTrue(Path(str(prefix) + ".claude.process.json").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def test_kill_helper_refuses_pid_only_receipt(self) -> None:
        task_id = "claude-pid-only"
        prefix = self.repo / ".worktrees" / task_id
        Path(str(prefix) + ".pid").write_text("12345\n", encoding="utf-8")

        stopped = subprocess.run(
            ["bash", "scripts/kill-claude.sh", task_id],
            cwd=self.repo,
            text=True,
            capture_output=True,
        )

        self.assertEqual(stopped.returncode, 1)
        self.assertIn("Refusing a PID-only kill", stopped.stderr)


if __name__ == "__main__":
    unittest.main()
