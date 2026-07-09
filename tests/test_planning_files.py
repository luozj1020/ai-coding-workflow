import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
INIT_PLAN = ROOT / "scripts" / "init-plan.py"
SESSION_CATCHUP = ROOT / "scripts" / "session-catchup.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


def init_repo(path):
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(path), capture_output=True, check=True)
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True, check=True)


class PlanningFilesTests(unittest.TestCase):
    def test_init_plan_creates_three_files_and_active_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            init_repo(repo)

            result = subprocess.run(
                [sys.executable, str(INIT_PLAN), "PROJ-123", "--repo", str(repo)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = repo / "ai" / "plans" / "PROJ-123"
            self.assertTrue((plan / "task_plan.md").exists())
            self.assertTrue((plan / "findings.md").exists())
            self.assertTrue((plan / "progress.md").exists())
            self.assertEqual((repo / "ai" / "plans" / ".active_plan").read_text(encoding="utf-8").strip(), "PROJ-123")
            task_plan = (plan / "task_plan.md").read_text(encoding="utf-8")
            self.assertIn("PROJ-123", task_plan)
            self.assertIn("## Task Sections", task_plan)
            self.assertIn("### Task 1: Define the first scoped task", task_plan)

    def test_session_catchup_generates_resume_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            init_repo(repo)
            subprocess.run(
                [sys.executable, str(INIT_PLAN), "PROJ-123", "--repo", str(repo)],
                cwd=str(ROOT),
                capture_output=True,
                check=True,
            )
            worktrees = repo / ".worktrees" / "loop-20990101-000000"
            worktrees.mkdir(parents=True)
            (worktrees / "loop-events.jsonl").write_text(
                json.dumps({"event": "run_start"}) + "\n" + json.dumps({"event": "decision", "decision": "REVISE"}) + "\n",
                encoding="utf-8",
            )
            (worktrees / "review-1.txt").write_text("### Decision\n\n**REVISE**\n", encoding="utf-8")
            (repo / ".worktrees" / "claude-20990101-000000.checker-report.md").write_text("FAILED\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SESSION_CATCHUP), "--repo", str(repo), "--plan", "PROJ-123"],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            resume = repo / "ai" / "plans" / "PROJ-123" / "resume-context.md"
            content = resume.read_text(encoding="utf-8")
            self.assertIn("Resume Context", content)
            self.assertIn("Recent Loop Events", content)
            self.assertIn("REVISE", content)
            self.assertIn("FAILED", content)

    def test_installer_copies_planning_helpers_and_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            subprocess.run(
                [sys.executable, str(INSTALLER), str(repo)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )

            for rel in [
                "ai/plan-task-template.md",
                "ai/plan-findings-template.md",
                "ai/plan-progress-template.md",
                "ai/init-plan.py",
                "ai/session-catchup.py",
            ]:
                self.assertTrue((repo / rel).exists(), rel)


if __name__ == "__main__":
    unittest.main()
