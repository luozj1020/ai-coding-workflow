import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-claude-report.py"


class VerifyClaudeReportTests(unittest.TestCase):
    def _repo(self, root: pathlib.Path):
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "source.py").write_text("def old():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        (repo / "source.py").write_text("def new_symbol():\n    return 2\n", encoding="utf-8")
        return repo

    def _run(self, repo: pathlib.Path, report_text: str, fail=False):
        report = repo / "CLAUDE_REPORT.md"
        report.write_text(report_text, encoding="utf-8")
        output = repo.parent / "consistency.json"
        cmd = ["python", str(SCRIPT), "--report", str(report), "--worktree", str(repo), "--output", str(output)]
        if fail:
            cmd.append("--fail-on-conflict")
        result = subprocess.run(cmd, text=True, capture_output=True)
        return result, json.loads(output.read_text(encoding="utf-8"))

    def test_matching_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            result, data = self._run(repo, "\n".join([
                "# Report", "claimed_file=source.py", "claimed_changed_file_count=1",
                "claimed_symbol=new_symbol", "claimed_no_unexpected_files=yes",
            ]))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data["status"], "matched")
            self.assertTrue(data["semantic_review_required"])
            self.assertFalse(data["acceptance_satisfied"])

    def test_unclaimed_actual_file_is_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            result, data = self._run(repo, "\n".join([
                "# Report", "claimed_file=wrong.py", "claimed_changed_file_count=1",
                "claimed_symbol=missing_symbol", "claimed_no_unexpected_files=yes",
            ]), fail=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(data["status"], "conflict")
            self.assertTrue(data["conflicts"])

    def test_old_report_is_insufficient_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            result, data = self._run(repo, "# Report\nChanged source.py.\n")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(data["status"], "insufficient-claims")

    def test_claimed_new_file_is_not_unexpected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            (repo / "new_file.py").write_text("def added():\n    pass\n", encoding="utf-8")
            result, data = self._run(repo, "\n".join([
                "# Report", "claimed_file=source.py", "claimed_file=new_file.py",
                "claimed_changed_file_count=2", "claimed_no_unexpected_files=yes",
            ]))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data["status"], "matched")


if __name__ == "__main__":
    unittest.main()
