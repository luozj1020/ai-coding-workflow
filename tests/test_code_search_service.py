import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "code-search-service.py"


class CodeSearchServiceTests(unittest.TestCase):
    def run_helper(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + list(args),
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    def test_doctor_reports_optional_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_helper("--zoekt-index", tmp, "doctor")

            self.assertIn("Code Search Service Doctor", result.stdout)
            self.assertIn("Zoekt index:", result.stdout)
            self.assertIn("Sourcegraph URL:", result.stdout)
            self.assertIn("Recommended locator order", result.stdout)

    def test_install_zoekt_without_yes_is_dry_run_or_missing_go(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "install-zoekt"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

        output = result.stdout + result.stderr
        self.assertIn(result.returncode, (0, 1))
        self.assertTrue(
            "Dry-run only" in output or "go is required" in output,
            output,
        )

    def test_sourcegraph_plan_is_dry_run_guidance(self):
        result = self.run_helper("sourcegraph-plan")

        self.assertIn("Sourcegraph Docker Compose Plan", result.stdout)
        self.assertIn("docker compose up -d", result.stdout)
        self.assertIn("SOURCEGRAPH_URL", result.stdout)


if __name__ == "__main__":
    unittest.main()
