import pathlib
import subprocess
import sys
import tempfile
import unittest
import contextlib
import importlib.util
import io


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "code-search-service.py"


def load_module():
    spec = importlib.util.spec_from_file_location("code_search_service", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        if "Dry-run only" in output:
            self.assertIn("github.com/sourcegraph/zoekt/cmd/zoekt@latest", output)
            self.assertNotIn("cmd/zoekt-query", output)

    def test_zoekt_packages_match_current_upstream_cli_names(self):
        module = load_module()

        self.assertEqual(
            module.ZOEKT_PACKAGES,
            [
                "github.com/sourcegraph/zoekt/cmd/zoekt-git-index@latest",
                "github.com/sourcegraph/zoekt/cmd/zoekt-index@latest",
                "github.com/sourcegraph/zoekt/cmd/zoekt@latest",
            ],
        )

    def test_long_running_command_streams_heartbeat(self):
        module = load_module()
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            rc, stdout, stderr, elapsed = module.run_command_stream(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.25); print('done')",
                ],
                timeout=5,
                heartbeat=0.05,
            )

        rendered = buffer.getvalue()
        self.assertEqual(rc, 0, stderr)
        self.assertIn("still running after", rendered)
        self.assertIn("done", rendered)
        self.assertIn("done", stdout)
        self.assertGreaterEqual(elapsed, 0.2)

    def test_sourcegraph_plan_is_dry_run_guidance(self):
        result = self.run_helper("sourcegraph-plan")

        self.assertIn("Sourcegraph Docker Compose Plan", result.stdout)
        self.assertIn("docker compose up -d", result.stdout)
        self.assertIn("SOURCEGRAPH_URL", result.stdout)


if __name__ == "__main__":
    unittest.main()
