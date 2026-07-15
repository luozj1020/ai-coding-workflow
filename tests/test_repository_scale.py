import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repository-scale.py"
spec = importlib.util.spec_from_file_location("repository_scale", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class RepositoryScaleTests(unittest.TestCase):
    def test_scale_boundaries_and_thresholds(self):
        self.assertEqual(mod.classify_scale(3000, 2000), "small")
        self.assertEqual(mod.classify_scale(3001, 2000), "medium")
        self.assertEqual(mod.classify_scale(10001, 7000), "large")
        self.assertEqual(mod.classify_scale(50001, 35000), "giant")
        self.assertEqual(mod.thresholds("large")["concentrated_lines"], 500)
        self.assertEqual(mod.thresholds("small")["concentrated_lines"], 100)

    def test_collect_counts_tracked_sources_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "main.py").write_text("print('x')\n", encoding="utf-8")
            (repo / "README.md").write_text("docs\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            worktrees = repo / ".worktrees"
            worktrees.mkdir()
            (worktrees / "one.runtime.json").write_text(json.dumps({"worktree_setup_seconds": 150}), encoding="utf-8")
            result = mod.collect(repo)
            self.assertEqual(result["tracked_files"], 2)
            self.assertEqual(result["source_files"], 1)
            self.assertEqual(result["repository_scale_detected"], "small")
            self.assertEqual(result["routing_scale"], "medium")
            self.assertEqual(result["worktree_cost"], "high")
            self.assertTrue(result["io_promoted"])

    def test_scale_override_prevents_io_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            result = mod.collect(repo, "large")
            self.assertEqual(result["routing_scale"], "large")
            self.assertFalse(result["io_promoted"])


if __name__ == "__main__":
    unittest.main()
