import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "init-spec.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


class InitSpecTests(unittest.TestCase):
    def test_creates_spec_from_template(self):
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

            result = subprocess.run(
                [
                    sys.executable,
                    str(repo / "ai" / "init-spec.py"),
                    "Search Filters",
                    "--repo",
                    str(repo),
                    "--date",
                    "2099-01-02",
                ],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            spec = repo / "ai" / "specs" / "2099-01-02--search-filters.md"
            self.assertTrue(spec.exists())
            text = spec.read_text(encoding="utf-8")
            self.assertIn("Search Filters", text)
            self.assertIn("## Non-Goals", text)
            self.assertIn("## Acceptance Surface", text)

    def test_refuses_to_overwrite_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            first = subprocess.run(
                [sys.executable, str(SCRIPT), "Duplicate Spec", "--repo", str(repo), "--date", "2099-01-02"],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "Duplicate Spec", "--repo", str(repo), "--date", "2099-01-02"],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 1)
            self.assertIn("spec already exists", second.stderr)


if __name__ == "__main__":
    unittest.main()
