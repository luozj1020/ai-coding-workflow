from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
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
        self.assertEqual(len(value["bindings"]), len(value["bind_targets"]))
        staging_root = Path(value["staging_root"]).resolve()
        self.assertTrue(
            all(Path(item["source"]).resolve().is_relative_to(staging_root) for item in value["bindings"])
        )

    def test_glob_and_parent_escape_fail_closed(self) -> None:
        for path in ("src/*.py", "../outside.py", ".git/config"):
            with self.subTest(path=path):
                self.card.write_text(f"- Write paths: {path}\n", encoding="utf-8")
                with self.assertRaises(MOD.SandboxError):
                    MOD.prepare(self.card, self.worktree, self.output)

    def test_composed_multiline_write_paths_are_exact(self) -> None:
        self.card.write_text(
            "## Scope\n\n"
            "- Write paths:\n"
            "  - `src/a.py`\n"
            "  - `tests/test_a.py`\n"
            "- Read paths:\n"
            "  - `src/reference.py`\n"
            "- Forbidden paths: deploy/\n",
            encoding="utf-8",
        )
        value = MOD.prepare(self.card, self.worktree, self.output)
        self.assertEqual(
            value["declared_write_paths"],
            ["src/a.py", "tests/test_a.py"],
        )
        self.assertNotIn("src/reference.py", value["declared_write_paths"])

    def test_multiline_write_path_prose_fails_closed(self) -> None:
        self.card.write_text(
            "- Write paths:\n"
            "  - runtime evidence only: CLAUDE_PROGRESS.md\n"
            "- Read paths: src/\n",
            encoding="utf-8",
        )
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
        self.assertIn('--staging-root "$_WRITE_SCOPE_STAGING_ROOT"', dispatcher)
        self.assertIn('--bind "$_write_bind_source" "$_write_bind_target"', dispatcher)
        self.assertIn("write-sandbox-allowed-path-read-only", dispatcher)
        self.assertNotIn('--bind "$_SYSTEM_TMP_ROOT" "$_SYSTEM_TMP_ROOT"', dispatcher)

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap unavailable")
    def test_read_only_root_blocks_bash_write_outside_bind(self) -> None:
        self.card.write_text(
            "- Write paths: allowed.txt\n"
            "- Full file replacement paths: allowed.txt\n",
            encoding="utf-8",
        )
        value = MOD.prepare(self.card, self.worktree, self.output)
        allowed = self.worktree / "allowed.txt"
        denied = self.worktree / "denied.txt"
        binding = next(item for item in value["bindings"] if item["relative_path"] == "allowed.txt")
        source = Path(binding["source"])
        command = [
            "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--bind", str(source), str(allowed), "--",
            "sh", "-c", f"printf allowed > '{allowed}'; printf denied > '{denied}'",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(source.read_text(encoding="utf-8"), "allowed")
        self.assertEqual(allowed.read_text(encoding="utf-8"), "")
        MOD.sync_receipt(self.output)
        self.assertEqual(allowed.read_text(encoding="utf-8"), "allowed")
        self.assertFalse(denied.exists())
        self.assertIn(str(allowed), value["bind_targets"])

    def test_sync_copies_only_declared_staging_paths(self) -> None:
        self.card.write_text("- Write paths: allowed.txt\n", encoding="utf-8")
        value = MOD.prepare(self.card, self.worktree, self.output)
        binding = next(item for item in value["bindings"] if item["relative_path"] == "allowed.txt")
        Path(binding["source"]).write_text("accepted\n", encoding="utf-8")
        (Path(value["staging_root"]) / "not-allowed.txt").write_text(
            "discarded\n", encoding="utf-8"
        )

        result = MOD.sync_receipt(self.output)

        self.assertEqual(result["status"], "synced")
        self.assertEqual((self.worktree / "allowed.txt").read_text(), "accepted\n")
        self.assertFalse((self.worktree / "not-allowed.txt").exists())

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap unavailable")
    def test_session_env_and_receipt_writer_work_inside_final_read_only_layout(self) -> None:
        self.card.write_text(
            "- Write paths: allowed.txt\n"
            "- Full file replacement paths: allowed.txt\n",
            encoding="utf-8",
        )
        (self.worktree / "allowed.txt").write_text("old\n", encoding="utf-8")
        value = MOD.prepare(self.card, self.worktree, self.output)
        binding = next(item for item in value["bindings"] if item["relative_path"] == "allowed.txt")
        session_source = self.root / "session-source"
        session_target = self.root / "home" / ".claude" / "session-env"
        session_source.mkdir()
        session_target.mkdir(parents=True)
        replacement = self.root / "replacement.txt"
        replacement.write_text("new\n", encoding="utf-8")
        writer = ROOT / "scripts" / "write-approved-file.py"
        command = [
            "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--bind", value["staging_root"], value["staging_root"],
            "--bind", str(session_source), str(session_target),
            "--bind", binding["source"], binding["target"], "--",
            "sh", "-c", 'touch "$HOME/.claude/session-env/ready"; exec "$@"',
            "aiwf", sys.executable, str(writer), "--receipt", str(self.output),
            "--path", "allowed.txt", "--source", str(replacement),
        ]
        env = dict(os.environ, HOME=str(self.root / "home"))
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((session_source / "ready").is_file())
        self.assertEqual(Path(binding["source"]).read_text(), "new\n")
        self.assertEqual((self.worktree / "allowed.txt").read_text(), "old\n")
        old_fragment = self.root / "old-fragment.txt"
        new_fragment = self.root / "new-fragment.txt"
        old_fragment.write_text("new", encoding="utf-8")
        new_fragment.write_text("unique", encoding="utf-8")
        fragment_command = command[:-2] + [
            "--replace-old-source", str(old_fragment),
            "--replace-new-source", str(new_fragment),
        ]
        fragment_result = subprocess.run(
            fragment_command, env=env, capture_output=True, text=True
        )
        self.assertEqual(fragment_result.returncode, 0, fragment_result.stderr)
        self.assertEqual(Path(binding["source"]).read_text(), "unique\n")
        MOD.sync_receipt(self.output)
        self.assertEqual((self.worktree / "allowed.txt").read_text(), "unique\n")


if __name__ == "__main__":
    unittest.main()
