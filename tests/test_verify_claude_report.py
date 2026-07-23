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

    def _run(self, repo: pathlib.Path, report_text: str, fail=False, task_card_text=None):
        report = repo / "CLAUDE_REPORT.md"
        report.write_text(report_text, encoding="utf-8")
        output = repo.parent / "consistency.json"
        cmd = ["python", str(SCRIPT), "--report", str(report), "--worktree", str(repo), "--output", str(output)]
        if task_card_text is not None:
            task_card = repo / "TASK_CARD_FULL.md"
            task_card.write_text(task_card_text, encoding="utf-8")
            cmd.extend(["--task-card", str(task_card)])
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

    def test_prose_test_count_without_test_diff_is_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            _, data = self._run(repo, "\n".join([
                "# Report", "Added 15 tests.", "claimed_file=source.py",
                "claimed_changed_file_count=1", "claimed_no_unexpected_files=yes",
            ]))
            self.assertEqual(data["status"], "conflict")
            self.assertEqual(data["actual_test_files"], [])
            self.assertTrue(any("15" in item for item in data["conflicts"]))

    def test_prose_changed_files_and_count_must_match_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            _, data = self._run(repo, "\n".join([
                "# Report", "Changed 5 files.", "## Files Changed", "- `source.py`", "- `missing.py`",
                "claimed_file=source.py", "claimed_changed_file_count=1",
                "claimed_no_unexpected_files=yes",
            ]))
            self.assertEqual(data["status"], "conflict")
            self.assertTrue(any("missing.py" in item or "claimed=5" in item for item in data["conflicts"]))

    def test_assigned_test_writing_requires_test_diff_and_count_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            _, data = self._run(
                repo,
                "\n".join([
                    "# Report", "claimed_file=source.py", "claimed_changed_file_count=1",
                    "claimed_no_unexpected_files=yes",
                ]),
                task_card_text="| Test writing | Claude Checker |\n",
            )
            self.assertEqual(data["status"], "conflict")
            self.assertTrue(data["task_requirements"]["tests_required"])

    def test_test_diff_count_and_validation_claims_are_exposed_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            (repo / "tests").mkdir()
            (repo / "tests" / "test_new.py").write_text(
                "def test_new_behavior():\n    assert True\n", encoding="utf-8",
            )
            _, data = self._run(
                repo,
                "\n".join([
                    "# Report", "claimed_file=source.py", "claimed_file=tests/test_new.py",
                    "claimed_changed_file_count=2", "claimed_no_unexpected_files=yes",
                    "claimed_test_count=1", "claimed_validation_command=python -m pytest tests/test_new.py",
                    "claimed_validation_exit_code=0",
                ]),
                task_card_text=(
                    "| Test writing | Claude Checker |\n"
                    "| Narrow validation | Claude Checker |\n"
                ),
            )
            self.assertEqual(data["status"], "matched")
            self.assertEqual(data["detected_added_test_declarations"], 1)
            self.assertEqual(data["validation_status"], "claimed-unverified")
            self.assertFalse(data["acceptance_satisfied"])

    def test_resolved_finding_requires_file_symbol_and_test_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(pathlib.Path(tmp))
            _, data = self._run(repo, "\n".join([
                "# Report", "claimed_file=source.py", "claimed_changed_file_count=1",
                "claimed_no_unexpected_files=yes",
                "resolved_finding=F-1|file=wrong.py|symbol=missing|test=test_missing",
            ]))
            self.assertEqual(data["status"], "conflict")
            self.assertGreaterEqual(len(data["conflicts"]), 3)


if __name__ == "__main__":
    unittest.main()
