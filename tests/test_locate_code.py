import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "locate-code.py"


class LocateCodeTests(unittest.TestCase):
    def init_repo(self, repo):
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)

    def run_locator(self, repo, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(repo)] + list(args),
            cwd=str(repo),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    def test_lexical_search_finds_candidate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "tests").mkdir()
            (repo / "src" / "user_service.py").write_text(
                "class UserService:\n"
                "    def authenticate(self, token):\n"
                "        return token == 'ok'\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_user_service.py").write_text(
                "from src.user_service import UserService\n"
                "def test_authenticate():\n"
                "    assert UserService().authenticate('ok')\n",
                encoding="utf-8",
            )
            self.init_repo(repo)

            result = self.run_locator(repo, "--codegraph", "off", "UserService authenticate")

            self.assertIn("CodeGraph: skipped (off)", result.stdout)
            self.assertIn("src/user_service.py", result.stdout)
            self.assertIn("tests/test_user_service.py", result.stdout)
            self.assertIn("def authenticate", result.stdout)
            self.assertIn("Suggested Targeted Reads", result.stdout)

    def test_auto_codegraph_skips_large_repositories_before_cli_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".codegraph").mkdir()
            (repo / "one.py").write_text("alpha = 1\n", encoding="utf-8")
            (repo / "two.py").write_text("alpha = 2\n", encoding="utf-8")
            self.init_repo(repo)

            result = self.run_locator(
                repo,
                "--codegraph",
                "auto",
                "--codegraph-auto-file-threshold",
                "1",
                "alpha",
            )

            self.assertIn("CodeGraph: skipped (auto skipped: tracked files", result.stdout)
            self.assertIn("one.py", result.stdout)
            self.assertIn("two.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
