import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "enforce-checker-contract.py"


def load_module():
    spec = importlib.util.spec_from_file_location("enforce_checker_contract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckerContractEnforcementTests(unittest.TestCase):
    def git(self, repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def repo(self, root):
        repo = Path(root) / "repo"
        repo.mkdir()
        self.git(repo, "init")
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test")
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "base")
        return repo

    def card(self, repo, write_paths, command="python -m py_compile {path}"):
        path = repo / "TASK_CARD_FULL.md"
        path.write_text(
            f"- Write paths: {write_paths}\n"
            f"| Per-file validation command | {command} |\n",
            encoding="utf-8",
        )
        return path

    def test_enforces_scope_nonempty_syntax_and_per_file_command(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_feature.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            result = module.enforce(repo, self.card(repo, "tests/test_feature.py"), repo / "receipt.json", 30)
            self.assertTrue(result["enforcement_passed"])
            self.assertEqual(len(result["validations"]), 2)

    def test_rejects_repository_root_helper_outside_write_scope(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            (repo / "scratch.py").write_text("print('scratch')\n", encoding="utf-8")
            result = module.enforce(repo, self.card(repo, "tests/test_feature.py"), repo / "receipt.json", 30)
            self.assertIn("out-of-scope:scratch.py", result["violations"])
            self.assertFalse(result["enforcement_passed"])

    def test_rejects_empty_assigned_test(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_feature.py").touch()
            result = module.enforce(repo, self.card(repo, "tests/test_feature.py"), repo / "receipt.json", 30)
            self.assertIn("missing-or-empty:tests/test_feature.py", result["violations"])


if __name__ == "__main__":
    unittest.main()
