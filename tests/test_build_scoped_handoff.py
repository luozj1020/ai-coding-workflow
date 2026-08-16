import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-scoped-handoff.py"


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
