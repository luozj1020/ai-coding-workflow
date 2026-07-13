import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-tests.py"
SPEC = importlib.util.spec_from_file_location("run_tests", RUNNER)
run_tests = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_tests)


class TestTiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = run_tests.load_manifest(ROOT / "tests" / "test-tiers.json")
        cls.discovered = run_tests.discover_tests(ROOT / "tests")

    def test_tiers_partition_expected_files(self):
        quick = set(run_tests.select_tests("quick", self.manifest, self.discovered))
        integration = set(run_tests.select_tests("integration", self.manifest, self.discovered))
        full = set(run_tests.select_tests("full", self.manifest, self.discovered))
        integration_label = set(self.manifest["labels"]["integration"])
        self.assertEqual(full, set(self.discovered))
        self.assertEqual(quick, full - integration_label)
        self.assertEqual(integration, integration_label)

    def test_selection_is_sorted(self):
        for tier in ("quick", "integration", "full"):
            selected = run_tests.select_tests(tier, self.manifest, self.discovered)
            self.assertEqual(selected, sorted(selected))

    def test_missing_manifest_test_is_rejected(self):
        broken = json.loads(json.dumps(self.manifest))
        broken["labels"]["slow"].append("test_missing.py")
        with self.assertRaisesRegex(ValueError, "missing tests"):
            run_tests.select_tests("quick", broken, self.discovered)

    def test_list_mode_does_not_execute_tests(self):
        result = subprocess.run(
            [sys.executable, str(RUNNER), "quick", "--list"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test_dirty_source_guard.py", result.stdout.splitlines())

    def test_ci_uses_quick_matrix_and_one_full_job(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("python scripts/run-tests.py quick"), 1)
        self.assertEqual(text.count("python scripts/run-tests.py full"), 1)
        self.assertNotIn("unittest discover", text)
        self.assertIn("if: github.event_name == 'push'", text)


if __name__ == "__main__":
    unittest.main()
