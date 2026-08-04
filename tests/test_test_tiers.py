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

    def test_case_shards_are_complete_disjoint_and_balanced(self):
        cases = [f"tests.test_example.Case.test_{index:02d}" for index in range(23)]
        shards = [set(run_tests.select_shard(cases, (index, 4))) for index in range(1, 5)]
        self.assertEqual(set().union(*shards), set(cases))
        for left_index, left in enumerate(shards):
            for right in shards[left_index + 1:]:
                self.assertTrue(left.isdisjoint(right))
        self.assertLessEqual(max(map(len, shards)) - min(map(len, shards)), 1)

    def test_parse_shard_rejects_invalid_ranges(self):
        self.assertEqual(run_tests.parse_shard("2/4"), (2, 4))
        for value in ("0/4", "5/4", "1/0", "bad"):
            with self.subTest(value=value), self.assertRaises(run_tests.argparse.ArgumentTypeError):
                run_tests.parse_shard(value)

    def test_integration_shard_list_uses_individual_test_ids(self):
        result = subprocess.run(
            [sys.executable, str(RUNNER), "integration", "--shard", "1/4", "--list"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("tests.test_") for line in lines))

    def test_ci_uses_quick_matrix_and_sharded_integration_without_duplicate_full(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("python scripts/run-tests.py quick"), 1)
        self.assertEqual(text.count("python scripts/run-tests.py integration --shard"), 1)
        self.assertNotIn("python scripts/run-tests.py full", text)
        self.assertIn("shard: [1, 2, 3, 4]", text)
        self.assertNotIn("unittest discover", text)
        self.assertNotIn("if: github.event_name == 'push'", text)
        self.assertIn("Enable and verify bubblewrap user namespaces", text)


if __name__ == "__main__":
    unittest.main()
