import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-scoped-handoff.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_scoped_handoff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


class ScopedHandoffTests(unittest.TestCase):
    def make_repo(self, root):
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
        (repo / "notes.txt").write_text("original\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "base")
        return repo, git(repo, "rev-parse", "HEAD")

    def run_helper(self, repo, source, execution, output, *extra, check=True):
        return subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--task-id", "task-1",
                "--worktree", str(repo),
                "--source-base", source,
                "--execution-base", execution,
                "--allow-path", "src/",
                "--output-dir", str(output),
                *extra,
            ],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_dirty_snapshot_patch_excludes_preexisting_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, source = self.make_repo(root)
            (repo / "notes.txt").write_text("user dirty snapshot\n", encoding="utf-8")
            git(repo, "add", "notes.txt")
            git(repo, "commit", "-qm", "snapshot")
            execution = git(repo, "rev-parse", "HEAD")
            (repo / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            (repo / "src" / "empty.py").touch()
            (repo / "src" / "new.py").write_text("new = True\n", encoding="utf-8")
            output = root / "handoff"

            self.run_helper(repo, source, execution, output, "--dirty-snapshot")

            manifest = json.loads(
                (output / "task-1.scoped-handoff.json").read_text(encoding="utf-8")
            )
            patch = (output / "task-1.scoped.patch").read_text(encoding="utf-8")
            self.assertEqual(manifest["status"], "ready")
            self.assertTrue(manifest["dirty_snapshot"])
            self.assertFalse(manifest["whole_worktree_merge_allowed"])
            self.assertEqual(
                [item["path"] for item in manifest["changed_files"]],
                ["src/app.py", "src/empty.py", "src/new.py"],
            )
            self.assertNotIn("notes.txt", patch)
            self.assertIn("src/new.py", patch)

            target = root / "target"
            git(repo, "worktree", "add", "-q", "--detach", str(target), execution)
            git(target, "apply", "--check", str(output / "task-1.scoped.patch"))

    def test_unapproved_product_change_blocks_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, source = self.make_repo(root)
            (repo / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            (repo / "outside.py").write_text("bad = True\n", encoding="utf-8")
            output = root / "handoff"

            result = self.run_helper(
                repo, source, source, output, check=False
            )

            self.assertEqual(result.returncode, 2)
            manifest = json.loads(
                (output / "task-1.scoped-handoff.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "blocked")
            self.assertEqual(manifest["unexpected_changed_paths"], ["outside.py"])
            self.assertEqual((output / "task-1.scoped.patch").read_bytes(), b"")

    def test_workflow_control_files_do_not_block_product_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, source = self.make_repo(root)
            (repo / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            (repo / "TASK_CARD.md").write_text("task\n", encoding="utf-8")
            (repo / "CLAUDE_PROMPT.md").write_text("prompt\n", encoding="utf-8")
            (repo / "advisor-response-1.json").write_text("{}\n", encoding="utf-8")
            output = root / "handoff"

            result = self.run_helper(repo, source, source, output)

            self.assertEqual(result.returncode, 0)
            manifest = json.loads(
                (output / "task-1.scoped-handoff.json").read_text(encoding="utf-8")
            )
            patch = (output / "task-1.scoped.patch").read_text(encoding="utf-8")
            self.assertEqual(manifest["status"], "ready")
            self.assertTrue(manifest["deliverable"])
            self.assertEqual(manifest["product_change_count"], 1)
            self.assertEqual(manifest["control_change_count"], 3)
            self.assertEqual(manifest["out_of_scope_product_paths"], [])
            self.assertNotIn("TASK_CARD.md", patch)
            self.assertNotIn("CLAUDE_PROMPT.md", patch)
            self.assertIn("src/app.py", patch)

    def test_product_changes_with_empty_patch_is_internal_error(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, source = self.make_repo(root)
            (repo / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            output = root / "handoff"
            args = SimpleNamespace(
                task_id="task-1",
                worktree=repo,
                source_base=source,
                execution_base=source,
                write_scope=None,
                allow_path=["src/"],
                validation_receipt=None,
                output_dir=output,
                dirty_snapshot=False,
            )

            with mock.patch.object(module, "build_patch", return_value=b""):
                manifest, exit_code = module.build(args)

            self.assertEqual(exit_code, 3)
            self.assertEqual(manifest["status"], "internal-error")
            self.assertFalse(manifest["deliverable"])
            self.assertEqual(
                manifest["internal_error_reason"],
                "product-changes-with-empty-patch",
            )

    def test_artifacts_cannot_be_written_inside_product_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, source = self.make_repo(root)

            result = self.run_helper(
                repo, source, source, repo / "handoff", check=False
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the product worktree", result.stderr)


if __name__ == "__main__":
    unittest.main()
