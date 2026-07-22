import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive-control-files.py"
SPEC = importlib.util.spec_from_file_location("archive_control_files", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ArchiveControlFilesTests(unittest.TestCase):
    def test_only_recognized_root_controls_are_snapshotted(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            archive = Path(temporary) / "archive"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            (repo / "CLAUDE_PROGRESS.md").write_text("old progress", encoding="utf-8")
            (repo / "notes.txt").write_text("user file", encoding="utf-8")
            value = MOD.archive(repo, archive)
            self.assertEqual(value["archived_paths"], ["CLAUDE_PROGRESS.md"])
            self.assertTrue((archive / "CLAUDE_PROGRESS.md").is_file())
            self.assertTrue((repo / "CLAUDE_PROGRESS.md").is_file())
            self.assertFalse((archive / "notes.txt").exists())


if __name__ == "__main__":
    unittest.main()
