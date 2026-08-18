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
        python_binding = next(
            item for item in value["bindings"] if item["relative_path"] == "src/a.py"
        )
        self.assertTrue(python_binding["candidate_validation_required"])
        self.assertTrue(str(python_binding["staged_initial_sha256"]).startswith("sha256:"))
        self.assertEqual(
            value["candidate_checkpoint_policy"],
            "validate-before-same-inode-write-and-rollback-on-io-failure",
        )
        self.assertEqual(
            value["large_fragment_policy"]["maximum_fraction_without_full_replacement_authority"],
            0.75,
        )

    def test_glob_and_parent_escape_fail_closed(self) -> None:
        for path in (
            "src/*.py", "../outside.py", ".git/config",
            ".aiwf-write-staging/CONTENT",
            ".aiwf-runtime/write-approved-file.py",
        ):
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

    def test_json_renderer_bold_scope_is_accepted(self) -> None:
        self.card.write_text(
            "<!-- aiwf-execution-card-v1; task-mode=builder; builder-mode=standard -->\n"
            "## Scope\n\n"
            "**Write paths:**\n"
            "- `src/a.py`\n"
            "- `tests/test_a.py`\n\n"
            "**Read paths:**\n"
            "- `src/reference.py`\n",
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

    def test_inline_path_annotation_fails_closed(self) -> None:
        self.card.write_text(
            "- Write paths: tests/unit/test_tensor_storage.py (new focused test module)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MOD.SandboxError, "prose or whitespace"):
            MOD.prepare(self.card, self.worktree, self.output)

    def test_backtick_quoted_path_may_contain_spaces(self) -> None:
        self.card.write_text("- Write paths: `docs/file name.md`\n", encoding="utf-8")
        value = MOD.prepare(self.card, self.worktree, self.output)
        self.assertEqual(value["declared_write_paths"], ["docs/file name.md"])

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
        self.assertIn(
            '--ro-bind "$_MANAGED_RUNTIME_SOURCE" "$_MANAGED_RUNTIME_TARGET"',
            dispatcher,
        )
        self.assertIn(
            '--bind "$_CLAUDE_WRITER_INPUT_SOURCE" "$_CLAUDE_WRITER_INPUT_TARGET"',
            dispatcher,
        )
        self.assertIn('--staging-root "$_WRITE_SCOPE_STAGING_ROOT"', dispatcher)
        self.assertIn('--bind "$_write_bind_source" "$_write_bind_target"', dispatcher)
        self.assertIn("write-sandbox-approved-writer-unavailable", dispatcher)
        self.assertIn(".aiwf-write-staging/CONTENT", dispatcher)
        self.assertIn("--source .aiwf-write-staging/CONTENT)", dispatcher)
        self.assertIn('--content-base64 *)', dispatcher)
        self.assertIn('.aiwf-runtime/write-approved-file.py', dispatcher)
        self.assertIn('DISPATCH_OUTCOME="write_staging_failed"', dispatcher)
        self.assertIn('DISPATCH_OUTCOME="missing_required_artifact"', dispatcher)
        self.assertIn("build-scoped-handoff.py", dispatcher)
        self.assertIn('--source-base "$BASE_COMMIT"', dispatcher)
        self.assertIn('--execution-base "$WORKTREE_START_COMMIT"', dispatcher)
        self.assertIn('--scoped-handoff "$SCOPED_HANDOFF_MANIFEST_FILE"', dispatcher)
        self.assertIn(
            '_VALIDATION_HELPER_REL=".aiwf-runtime/run-approved-validation.py"',
            dispatcher,
        )
        self.assertIn('historical_worktree_helpers_used', dispatcher)
        self.assertNotIn('--receipt \\$AI_WORKFLOW_WRITE_SCOPE_RECEIPT', dispatcher)
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
        writer_input_source = self.root / "writer-input"
        writer_input_target = self.worktree / ".aiwf-write-staging"
        writer_input_source.mkdir()
        writer_input_target.mkdir()
        for name in ("CONTENT", "OLD_FRAGMENT", "NEW_FRAGMENT"):
            (writer_input_source / name).write_text("AIWF_WRITER_INPUT_V1\n")
        atomic_edit_probe = [
            "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--bind", str(writer_input_source), str(writer_input_target),
            "--chdir", str(self.worktree), "--", "sh", "-c",
            "printf 'new\\n' > .aiwf-write-staging/.CONTENT.tmp && "
            "mv .aiwf-write-staging/.CONTENT.tmp .aiwf-write-staging/CONTENT",
        ]
        atomic_result = subprocess.run(
            atomic_edit_probe, capture_output=True, text=True
        )
        self.assertEqual(atomic_result.returncode, 0, atomic_result.stderr)
        writer = ROOT / "scripts" / "write-approved-file.py"
        command = [
            "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--bind", value["staging_root"], value["staging_root"],
            "--bind", str(session_source), str(session_target),
            "--bind", str(writer_input_source), str(writer_input_target),
            "--bind", binding["source"], binding["target"],
            "--chdir", str(self.worktree), "--",
            "sh", "-c", 'touch "$HOME/.claude/session-env/ready"; exec "$@"',
            "aiwf", sys.executable, str(writer),
            "--path", "allowed.txt", "--source", ".aiwf-write-staging/CONTENT",
        ]
        env = dict(
            os.environ,
            HOME=str(self.root / "home"),
            AI_WORKFLOW_WRITE_SCOPE_RECEIPT=str(self.output),
        )
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((session_source / "ready").is_file())
        self.assertEqual(Path(binding["source"]).read_text(), "new\n")
        self.assertEqual((self.worktree / "allowed.txt").read_text(), "old\n")
        (writer_input_source / "OLD_FRAGMENT").write_text("new")
        (writer_input_source / "NEW_FRAGMENT").write_text("unique")
        fragment_command = command[:-2] + [
            "--replace-old-source", ".aiwf-write-staging/OLD_FRAGMENT",
            "--replace-new-source", ".aiwf-write-staging/NEW_FRAGMENT",
        ]
        fragment_result = subprocess.run(
            fragment_command, env=env, capture_output=True, text=True
        )
        self.assertEqual(fragment_result.returncode, 0, fragment_result.stderr)
        self.assertEqual(Path(binding["source"]).read_text(), "unique\n")
        MOD.sync_receipt(self.output)
        self.assertEqual((self.worktree / "allowed.txt").read_text(), "unique\n")

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap unavailable")
    def test_dispatcher_runtime_mount_hides_stale_worktree_writer(self) -> None:
        self.card.write_text(
            "- Write paths: allowed.txt\n"
            "- Full file replacement paths: allowed.txt\n",
            encoding="utf-8",
        )
        (self.worktree / "allowed.txt").write_text("old\n", encoding="utf-8")
        value = MOD.prepare(self.card, self.worktree, self.output)
        binding = next(
            item for item in value["bindings"]
            if item["relative_path"] == "allowed.txt"
        )
        runtime_source = self.root / "dispatcher-runtime"
        runtime_target = self.worktree / ".aiwf-runtime"
        runtime_source.mkdir()
        runtime_target.mkdir()
        shutil.copy2(
            ROOT / "scripts" / "write-approved-file.py",
            runtime_source / "write-approved-file.py",
        )
        (runtime_target / "write-approved-file.py").write_text(
            "raise SystemExit('stale worktree writer was selected')\n",
            encoding="utf-8",
        )
        replacement = self.root / "replacement"
        replacement.write_text("new\n", encoding="utf-8")
        command = [
            "bwrap", "--die-with-parent", "--ro-bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--ro-bind", str(runtime_source), str(runtime_target),
            "--bind", value["staging_root"], value["staging_root"],
            "--chdir", str(self.worktree), "--", sys.executable,
            ".aiwf-runtime/write-approved-file.py",
            "--path", "allowed.txt", "--source", str(replacement),
        ]
        result = subprocess.run(
            command,
            env=dict(
                os.environ,
                AI_WORKFLOW_WRITE_SCOPE_RECEIPT=str(self.output),
            ),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(binding["source"]).read_text(), "new\n")
        self.assertIn(
            "stale worktree writer was selected",
            (runtime_target / "write-approved-file.py").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
