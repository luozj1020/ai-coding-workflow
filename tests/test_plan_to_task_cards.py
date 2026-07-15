import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan-to-task-cards.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


class PlanToTaskCardsTests(unittest.TestCase):
    def test_generates_task_cards_from_task_headings(self):
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
            plan_dir = repo / "ai" / "plans" / "PROJ-123"
            plan_dir.mkdir(parents=True)
            plan = plan_dir / "task_plan.md"
            plan.write_text(
                "# Plan\n\n"
                "### Task 1: Add parser\n\n"
                "Implement the parser.\n\n"
                "### Task 2: Validate output\n\n"
                "Add validation evidence.\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "plan-to-task-cards.py"), str(plan), "--repo", str(repo)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            cards = sorted((repo / "ai" / "task-cards").glob("*.md"))
            self.assertEqual(len(cards), 2)
            first = cards[0].read_text(encoding="utf-8")
            self.assertIn("## Builder Contract", first)
            self.assertNotIn("## Spec Gate", first)
            self.assertIn("## Plan Task Extract", first)
            self.assertIn("Task heading: Task 1: Add parser", first)
            self.assertIn("Implement the parser.", first)

    def test_reports_missing_task_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            plan = repo / "plan.md"
            plan.write_text("# Plan\n\nNo task headings.\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(plan), "--repo", str(repo)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("no task sections found", result.stderr)

    def test_init_plan_output_can_be_split_into_task_cards(self):
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

            init_result = subprocess.run(
                [sys.executable, str(repo / "ai" / "init-plan.py"), "PROJ-123", "--repo", str(repo)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            plan = repo / "ai" / "plans" / "PROJ-123" / "task_plan.md"
            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "plan-to-task-cards.py"), str(plan), "--repo", str(repo)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            cards = sorted((repo / "ai" / "task-cards").glob("*.md"))
            self.assertGreaterEqual(len(cards), 2)
            self.assertIn("Task heading: Task 1: Define the first scoped task", cards[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
