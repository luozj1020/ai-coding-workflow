from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "change-size-advisory.py"
SPEC = importlib.util.spec_from_file_location("change_size_advisory", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ChangeSizeAdvisoryTests(unittest.TestCase):
    def test_large_test_to_implementation_ratio_warns(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "src.py").write_text("base = 1\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests/test_src.py").write_text("def test_base():\n    assert True\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "src.py").write_text("base = 2\n", encoding="utf-8")
            test_lines = "\n".join(f"def test_{index}(): assert True" for index in range(20)) + "\n"
            (repo / "tests/test_src.py").write_text(test_lines, encoding="utf-8")
            value = MOD.analyze(repo, ratio_threshold=1.5, line_threshold=10)
            self.assertEqual(value["status"], "warning")
            self.assertFalse(value["blocking"])
            self.assertIn("prefer parameterized tests", value["recommendations"])

    def test_untracked_tests_are_counted_and_control_artifacts_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "src.py").write_text("base = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "src.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "src.py").write_text("base = 2\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_new.py").write_text(
                "\n".join(f"def test_{index}(): assert True" for index in range(20)) + "\n",
                encoding="utf-8",
            )
            (repo / "CLAUDE_REPORT.md").write_text("noise\n" * 100, encoding="utf-8")

            value = MOD.analyze(repo, ratio_threshold=1.5, line_threshold=10)

            self.assertEqual(value["status"], "warning")
            self.assertEqual(value["test_changed_lines"], 20)
            self.assertEqual(value["implementation_changed_lines"], 2)
            self.assertEqual(value["untracked_files_included"], 1)
            self.assertEqual(value["untracked_files_skipped"], 0)


if __name__ == "__main__":
    unittest.main()
