import importlib.util
import unittest.mock
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

    def card(
        self, repo, write_paths, command="python -m py_compile {path}",
        exact_command="",
    ):
        path = repo / "TASK_CARD_FULL.md"
        path.write_text(
            f"- Write paths: {write_paths}\n"
            f"| Per-file validation command | {command} |\n"
            f"- Exact narrow command: {exact_command}\n",
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

    def test_runs_frozen_exact_command_after_per_file_validation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_feature.py").write_text(
                "def test_ok():\n    assert True\n\n"
                "if __name__ == '__main__':\n    test_ok()\n",
                encoding="utf-8",
            )
            result = module.enforce(
                repo,
                self.card(
                    repo, "tests/test_feature.py",
                    exact_command="`python tests/test_feature.py`",
                ),
                repo / "receipt.json",
                30,
            )

            self.assertTrue(result["enforcement_passed"])
            self.assertEqual(
                result["exact_validation"]["validation_kind"],
                "frozen-exact-command",
            )
            self.assertEqual(result["exact_validation"]["exit_code"], 0)
            self.assertEqual(len(result["validations"]), 3)

    def test_rejects_shell_syntax_in_frozen_exact_command(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_feature.py").write_text(
                "def test_ok():\n    assert True\n", encoding="utf-8"
            )
            result = module.enforce(
                repo,
                self.card(
                    repo, "tests/test_feature.py",
                    exact_command="python tests/test_feature.py && echo unsafe",
                ),
                repo / "receipt.json",
                30,
            )

            self.assertFalse(result["enforcement_passed"])
            self.assertTrue(any(
                item.startswith("invalid-exact-validation-command:")
                for item in result["violations"]
            ))

    def test_environment_crash_is_recovered_by_equivalent_file_groups(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            tests = repo / "tests"
            tests.mkdir()
            for name in ("test_a.py", "test_b.py"):
                (tests / name).write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            card = self.card(
                repo,
                "tests",
                command="python -m py_compile {path}",
                exact_command="python -m pytest tests/test_a.py tests/test_b.py -q",
            )
            original = module.execute

            def execute(argv, worktree, timeout, env):
                if argv == [
                    "python", "-m", "pytest",
                    "tests/test_a.py", "tests/test_b.py", "-q",
                ]:
                    return {
                        "argv": argv,
                        "exit_code": 139,
                        "output_tail": "Segmentation fault (core dumped)",
                        "passed": False,
                    }
                if argv in (
                    ["python", "-m", "pytest", "-q", "tests/test_a.py"],
                    ["python", "-m", "pytest", "-q", "tests/test_b.py"],
                ):
                    return {
                        "argv": argv,
                        "exit_code": 0,
                        "output_tail": "1 passed",
                        "passed": True,
                    }
                return original(argv, worktree, timeout, env)

            with unittest.mock.patch.object(module, "execute", side_effect=execute):
                result = module.enforce(repo, card, repo / "receipt.json", 30)

            self.assertTrue(result["enforcement_passed"])
            self.assertTrue(result["environment_failure_observed"])
            self.assertTrue(result["grouped_retry"]["recovered"])
            self.assertEqual(len(result["grouped_retry"]["results"]), 2)

    def test_unsplittable_suite_crash_remains_environment_failure(self):
        module = load_module()
        crash = {
            "argv": ["python", "-m", "pytest", "-q"],
            "exit_code": -11,
            "output_tail": "",
            "passed": False,
        }
        self.assertTrue(module.is_environment_crash(crash))
        self.assertEqual(
            module.pytest_group_commands(
                crash["argv"], ["tests/test_feature.py"]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
