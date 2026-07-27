from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare-write-sandbox.py"
SPEC = importlib.util.spec_from_file_location("prepare_write_sandbox", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class PrepareWriteSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.card = self.root / "card.md"
        self.output = self.root / "receipt.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_paths_and_controls_are_prepared(self) -> None:
        self.card.write_text("- Write paths: src/a.py, tests/\n", encoding="utf-8")
        value = MOD.prepare(self.card, self.worktree, self.output)
        self.assertEqual(value["status"], "ready")
        self.assertTrue((self.worktree / "src/a.py").is_file())
        self.assertTrue((self.worktree / "tests").is_dir())
        self.assertTrue((self.worktree / "CLAUDE_REPORT.md").is_file())
        self.assertTrue(value["bash_cannot_bypass_scope"])
        self.assertEqual(json.loads(self.output.read_text())["declared_write_paths"], ["src/a.py", "tests/"])

    def test_glob_and_parent_escape_fail_closed(self) -> None:
        for path in ("src/*.py", "../outside.py", ".git/config"):
            with self.subTest(path=path):
                self.card.write_text(f"- Write paths: {path}\n", encoding="utf-8")
                with self.assertRaises(MOD.SandboxError):
                    MOD.prepare(self.card, self.worktree, self.output)

    def test_symlink_parent_and_hard_link_fail_closed(self) -> None:
        (self.worktree / ".git").mkdir()
        (self.worktree / "alias").symlink_to(".git", target_is_directory=True)
        self.card.write_text("- Write paths: alias/config\n", encoding="utf-8")
        with self.assertRaises(MOD.SandboxError):
            MOD.prepare(self.card, self.worktree, self.output)

        original = self.worktree / "original.txt"
        original.write_text("shared", encoding="utf-8")
        linked = self.worktree / "linked.txt"
        os.link(original, linked)
        self.card.write_text("- Write paths: linked.txt\n", encoding="utf-8")
        with self.assertRaises(MOD.SandboxError):
            MOD.prepare(self.card, self.worktree, self.output)

    def test_dispatcher_writes_only_to_task_scoped_temp_directory(self) -> None:
        dispatcher = (ROOT / "scripts" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
        self.assertIn('--bind "$TASK_TMPDIR" "$TASK_TMPDIR"', dispatcher)
        self.assertNotIn('--bind "$_SYSTEM_TMP_ROOT" "$_SYSTEM_TMP_ROOT"', dispatcher)

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap unavailable")
    def test_read_only_root_blocks_bash_write_outside_bind(self) -> None:
        self.card.write_text("- Write paths: allowed.txt\n", encoding="utf-8")
        value = MOD.prepare(self.card, self.worktree, self.output)
        allowed = self.worktree / "allowed.txt"
        denied = self.worktree / "denied.txt"
        command = [
            "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--bind", str(allowed), str(allowed), "--",
            "sh", "-c", f"printf allowed > '{allowed}'; printf denied > '{denied}'",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(allowed.read_text(encoding="utf-8"), "allowed")
        self.assertFalse(denied.exists())
        self.assertIn(str(allowed), value["bind_targets"])


if __name__ == "__main__":
    unittest.main()
