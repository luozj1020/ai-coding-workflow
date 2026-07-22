import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create-dirty-snapshot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("create_dirty_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DirtySnapshotTests(unittest.TestCase):
    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def test_snapshot_captures_tracked_untracked_and_deletion_without_mutating_source_index(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self.git(repo, "init")
            self.git(repo, "config", "user.email", "test@example.com")
            self.git(repo, "config", "user.name", "Test")
            (repo / "kept.py").write_text("old\n", encoding="utf-8")
            (repo / "deleted.py").write_text("delete\n", encoding="utf-8")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-m", "base")
            base = self.git(repo, "rev-parse", "HEAD")
            index_before = self.git(repo, "write-tree")

            (repo / "kept.py").write_text("new\n", encoding="utf-8")
            (repo / "deleted.py").unlink()
            (repo / "new.py").write_text("created\n", encoding="utf-8")
            (repo / "TASK_CARD.md").write_text("control\n", encoding="utf-8")
            output = repo / ".snapshot.json"
            receipt = module.create_snapshot(repo, output, ["TASK_CARD.md", ".snapshot.json"])

            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), base)
            self.assertEqual(self.git(repo, "write-tree"), index_before)
            snapshot = str(receipt["snapshot_commit"])
            self.assertEqual(self.git(repo, "show", f"{snapshot}:kept.py"), "new")
            self.assertEqual(self.git(repo, "show", f"{snapshot}:new.py"), "created")
            self.assertNotIn("deleted.py", self.git(repo, "ls-tree", "-r", "--name-only", snapshot))
            self.assertNotIn("TASK_CARD.md", self.git(repo, "ls-tree", "-r", "--name-only", snapshot))
            self.assertFalse(self.git(repo, "branch", "--contains", snapshot))
            stored = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(stored["merge_authorized"])
            self.assertTrue(stored["source_index_unchanged_by_design"])


if __name__ == "__main__":
    unittest.main()
