import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_for_codex.py"


def load_module():
    spec = importlib.util.spec_from_file_location("install_for_codex", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallForCodexTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_should_exclude_pyc_files_by_wildcard(self):
        self.assertTrue(self.module.should_exclude("cache.pyc", "/repo/cache.pyc"))
        self.assertTrue(self.module.should_exclude(".git", "/repo/.git"))
        self.assertTrue(self.module.should_exclude("task-cards", "/repo/task-cards"))
        self.assertTrue(self.module.should_exclude("tmp-smoke", "/repo/tmp-smoke"))
        self.assertFalse(self.module.should_exclude("README.md", "/repo/README.md"))

    def test_copy_skill_excludes_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "src"
            dest = pathlib.Path(tmp) / "dest"
            (src / "scripts").mkdir(parents=True)
            (src / "__pycache__").mkdir()
            (src / ".worktrees").mkdir()
            (src / "README.md").write_text("ok\n", encoding="utf-8")
            (src / "scripts" / "tool.pyc").write_text("compiled\n", encoding="utf-8")
            (src / "__pycache__" / "x.pyc").write_text("compiled\n", encoding="utf-8")
            (src / ".worktrees" / "artifact.txt").write_text("artifact\n", encoding="utf-8")

            self.module.copy_skill(str(src), str(dest))

            self.assertTrue((dest / "README.md").exists())
            self.assertFalse((dest / "scripts" / "tool.pyc").exists())
            self.assertFalse((dest / "__pycache__").exists())
            self.assertFalse((dest / ".worktrees").exists())
            self.assertFalse((dest / "task-cards").exists())
            self.assertFalse((dest / "tmp-smoke").exists())


if __name__ == "__main__":
    unittest.main()
