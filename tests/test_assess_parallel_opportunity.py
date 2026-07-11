"""Tests for scripts/assess-parallel-opportunity.py - zero-token parallel opportunity classifier."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assess-parallel-opportunity.py"


def run_classifier(*args):
    """Run the classifier and return (returncode, stdout, stderr)."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def run_classifier_json(*args):
    """Run the classifier with --json and return the parsed JSON result."""
    result = run_classifier("--json", *args)
    return result, json.loads(result.stdout) if result.returncode == 0 else None


def write_temp_text(text):
    """Create a closed temporary file so Windows subprocesses can reopen it."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def write_temp_json(payload):
    return write_temp_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# Test: Serial-obvious classifications
# ---------------------------------------------------------------------------

class TestSerialObvious(unittest.TestCase):
    """Serial-obvious inputs must be classified locally with no model invocation."""

    def test_single_work_unit_is_serial(self):
        result, data = run_classifier_json("--work-units", "1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "serial-obvious")
        self.assertTrue(any("fewer than 2 work units" in r for r in data["reasons"]))

    def test_zero_work_units_is_serial(self):
        result, data = run_classifier_json("--work-units", "0")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "serial-obvious")

    def test_single_write_scope_is_serial(self):
        result, data = run_classifier_json(
            "--work-units", "3",
            "--write-scopes", "src/a.py",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "serial-obvious")
        self.assertTrue(any("fewer than 2 distinct write scopes" in r for r in data["reasons"]))

    def test_very_small_work_is_serial(self):
        result, data = run_classifier_json(
            "--work-units", "2",
            "--write-scopes", "src/a.py,src/b.py",
            "--estimated-minutes", "5",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "serial-obvious")
        self.assertTrue(any("very small estimated work" in r for r in data["reasons"]))

    def test_hard_shared_risk_flag_is_serial(self):
        result, data = run_classifier_json(
            "--work-units", "3",
            "--write-scopes", "src/a.py,src/b.py",
            "--hard-risk-flags", "shared-api",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "serial-obvious")
        self.assertTrue(any("hard shared-risk flag" in r for r in data["reasons"]))

    def test_empty_write_scopes_is_serial(self):
        result, data = run_classifier_json("--work-units", "5")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "serial-obvious")


# ---------------------------------------------------------------------------
# Test: Parallel-candidate classifications
# ---------------------------------------------------------------------------

class TestParallelCandidate(unittest.TestCase):
    """Plausible multi-scope tasks become parallel-candidate and recommend Spark."""

    def test_multi_scope_task_is_candidate(self):
        result, data = run_classifier_json(
            "--work-units", "3",
            "--write-scopes", "src/auth,src/api,src/db",
            "--estimated-minutes", "30",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "parallel-candidate")
        self.assertTrue(any("3 work units" in r for r in data["reasons"]))
        self.assertTrue(any("3 distinct write scopes" in r for r in data["reasons"]))
        self.assertIn("Spark", data["recommended_next_action"])
        self.assertIn("does not run", data["recommended_next_action"])

    def test_candidate_with_independent_validations(self):
        result, data = run_classifier_json(
            "--work-units", "2",
            "--write-scopes", "frontend,backend",
            "--estimated-minutes", "20",
            "--validation-count", "3",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "parallel-candidate")
        self.assertTrue(any("3 independent validations" in r for r in data["reasons"]))

    def test_candidate_write_scopes_returned(self):
        result, data = run_classifier_json(
            "--work-units", "2",
            "--write-scopes", "src/a,src/b",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["decision"], "parallel-candidate")
        self.assertEqual(data["write_scopes"], ["src/a", "src/b"])


# ---------------------------------------------------------------------------
# Test: Input modes (JSON file and CLI flags)
# ---------------------------------------------------------------------------

class TestInputModes(unittest.TestCase):
    """Test both --hints JSON file and CLI flag input modes."""

    def test_json_file_input(self):
        path = write_temp_json({
            "work_units": 3,
            "write_scopes": ["src/a", "src/b"],
            "estimated_minutes": 20,
        })
        try:
            result, data = run_classifier_json("--hints", path)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(data["decision"], "parallel-candidate")
        finally:
            os.unlink(path)

    def test_invalid_json_file_exits_2(self):
        path = write_temp_text("not json{{{")
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2)
        finally:
            os.unlink(path)

    def test_nonexistent_hints_file_exits_2(self):
        result = run_classifier("--hints", "/nonexistent/path.json")
        self.assertEqual(result.returncode, 2)

    def test_duplicate_scopes_deduplicated(self):
        result, data = run_classifier_json(
            "--work-units", "2",
            "--write-scopes", "src/a,src/a,src/b",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["write_scopes"], ["src/a", "src/b"])


# ---------------------------------------------------------------------------
# Test: Deterministic (no subprocess/model invocation)
# ---------------------------------------------------------------------------

class TestDeterministicNoSubprocess(unittest.TestCase):
    """Classifier must not invoke any subprocess or model."""

    def test_output_is_deterministic(self):
        """Same input always produces identical output."""
        args = ["--json", "--work-units", "2", "--write-scopes", "a,b"]
        r1 = run_classifier(*args)
        r2 = run_classifier(*args)
        self.assertEqual(r1.stdout, r2.stdout)
        self.assertEqual(r1.returncode, r2.returncode)


# ---------------------------------------------------------------------------
# Test: JSON input validation (exit 2 on invalid types)
# ---------------------------------------------------------------------------

class TestJsonInputValidation(unittest.TestCase):
    """Invalid JSON hints must return exit 2 with concise error, not traceback."""

    def test_string_work_units_exits_2(self):
        """work_units as string must exit 2."""
        path = write_temp_json({"work_units": "three", "write_scopes": ["a", "b"]})
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("work_units must be an integer", result.stderr)
        finally:
            os.unlink(path)

    def test_negative_work_units_exits_2(self):
        """Negative work_units must exit 2."""
        path = write_temp_json({"work_units": -1, "write_scopes": ["a", "b"]})
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("work_units must be nonnegative", result.stderr)
        finally:
            os.unlink(path)

    def test_bool_work_units_exits_2(self):
        """Boolean work_units must exit 2 (isinstance(True, int) is True)."""
        path = write_temp_json({"work_units": True, "write_scopes": ["a", "b"]})
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("work_units must be an integer", result.stderr)
        finally:
            os.unlink(path)

    def test_string_write_scopes_exits_2(self):
        """write_scopes as string instead of list must exit 2."""
        path = write_temp_json({"work_units": 2, "write_scopes": "a,b"})
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("write_scopes must be an array", result.stderr)
        finally:
            os.unlink(path)

    def test_non_dict_root_exits_2(self):
        """Non-object JSON root must exit 2."""
        path = write_temp_json([1, 2, 3])
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("hints must be a JSON object", result.stderr)
        finally:
            os.unlink(path)

    def test_negative_estimated_minutes_exits_2(self):
        """Negative estimated_minutes must exit 2."""
        path = write_temp_json({"work_units": 2, "write_scopes": ["a", "b"], "estimated_minutes": -5})
        try:
            result = run_classifier("--hints", path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("estimated_minutes must be nonnegative", result.stderr)
        finally:
            os.unlink(path)

    def test_valid_input_still_works(self):
        """Valid input should still produce classification."""
        path = write_temp_json({"work_units": 3, "write_scopes": ["src/a", "src/b"], "estimated_minutes": 20})
        try:
            result, data = run_classifier_json("--hints", path)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(data["decision"], "parallel-candidate")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
