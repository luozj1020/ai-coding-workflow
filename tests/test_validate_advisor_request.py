import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "validate-advisor-request.py"
spec = importlib.util.spec_from_file_location("validate_advisor_request", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _write_request(tmp, data):
    """Write a request dict to a temp file and return the path."""
    p = Path(tmp) / "ADVISOR_REQUEST.json"
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(p)


def _valid_request(**overrides):
    """Return a valid request dict with optional overrides."""
    req = {
        "schema_version": 1,
        "task_id": "test-task-123",
        "direction": "on-plan",
        "blocker": {
            "kind": "semantic",
            "question": "How should I handle this edge case?",
            "blocking": True,
        },
        "completed_work": "Implemented the main feature",
        "advisor_used": False,
    }
    req.update(overrides)
    return req


class ValidateAdvisorRequestTests(unittest.TestCase):
    """Unit tests for validate_request."""

    def test_valid_on_plan_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request())
            ok, data, diag = mod.validate_request(p)
            self.assertTrue(ok)
            self.assertIsNotNone(data)
            self.assertIsNone(diag)
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["task_id"], "test-task-123")
            self.assertEqual(data["direction"], "on-plan")
            self.assertEqual(data["blocker"]["kind"], "semantic")
            self.assertTrue(data["blocker"]["blocking"])
            self.assertFalse(data["advisor_used"])

    def test_valid_off_plan_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(direction="off-plan"))
            ok, data, diag = mod.validate_request(p)
            self.assertTrue(ok)
            self.assertEqual(data["direction"], "off-plan")

    def test_valid_all_blocker_kinds(self):
        for kind in ("semantic", "transport", "approval", "direction", "unknown"):
            with tempfile.TemporaryDirectory() as tmp:
                p = _write_request(tmp, _valid_request(
                    blocker={"kind": kind, "question": "test?", "blocking": True}
                ))
                ok, data, diag = mod.validate_request(p)
                self.assertTrue(ok, f"kind={kind} should be valid, got diag={diag}")
                self.assertEqual(data["blocker"]["kind"], kind)

    def test_task_id_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(task_id="my-task"))
            ok, data, diag = mod.validate_request(p, expected_task_id="my-task")
            self.assertTrue(ok)

    def test_task_id_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(task_id="wrong-id"))
            ok, data, diag = mod.validate_request(p, expected_task_id="correct-id")
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "task-id-mismatch")

    # --- Fail-closed rules ---

    def test_missing_file(self):
        ok, data, diag = mod.validate_request("/nonexistent/path.json")
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "file-not-found")

    def test_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text("{not valid json", encoding="utf-8")
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "malformed-json")

    def test_not_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "array.json"
            p.write_text('[1, 2, 3]', encoding="utf-8")
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "not-an-object")

    def test_unknown_fields_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = _valid_request(extra_field="bad")
            p = _write_request(tmp, req)
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "unknown-fields")
            self.assertIn("extra_field", diag["detail"])

    def test_missing_required_field(self):
        for field in ("schema_version", "task_id", "direction", "blocker", "completed_work", "advisor_used"):
            with tempfile.TemporaryDirectory() as tmp:
                req = _valid_request()
                del req[field]
                p = _write_request(tmp, req)
                ok, data, diag = mod.validate_request(str(p))
                self.assertFalse(ok, f"missing {field} should be rejected")
                self.assertEqual(diag["reason"], "missing-fields")
                self.assertIn(field, diag["detail"])

    def test_unsupported_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(schema_version=2))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "unsupported-schema-version")

    def test_schema_version_wrong_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(schema_version="1"))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "unsupported-schema-version")

    def test_empty_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(task_id=""))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "invalid-task-id")

    def test_invalid_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(direction="maybe"))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "invalid-direction")

    def test_blocker_not_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(blocker="not-object"))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "blocker-not-an-object")

    def test_blocker_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = {"kind": "semantic", "question": "q?", "blocking": True, "extra": 1}
            p = _write_request(tmp, _valid_request(blocker=blocker))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "blocker-unknown-fields")

    def test_blocker_missing_fields(self):
        for field in ("kind", "question", "blocking"):
            with tempfile.TemporaryDirectory() as tmp:
                blocker = {"kind": "semantic", "question": "q?", "blocking": True}
                del blocker[field]
                p = _write_request(tmp, _valid_request(blocker=blocker))
                ok, data, diag = mod.validate_request(str(p))
                self.assertFalse(ok, f"blocker missing {field} should be rejected")
                self.assertEqual(diag["reason"], "blocker-missing-fields")

    def test_invalid_blocker_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = {"kind": "invalid-kind", "question": "q?", "blocking": True}
            p = _write_request(tmp, _valid_request(blocker=blocker))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "invalid-blocker-kind")

    def test_empty_blocker_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = {"kind": "semantic", "question": "", "blocking": True}
            p = _write_request(tmp, _valid_request(blocker=blocker))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "invalid-blocker-question")

    def test_blocker_blocking_false_rejected(self):
        """blocking=false must be rejected fail-closed."""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = {"kind": "semantic", "question": "q?", "blocking": False}
            p = _write_request(tmp, _valid_request(blocker=blocker))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "blocker-blocking-false")

    def test_blocker_blocking_not_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = {"kind": "semantic", "question": "q?", "blocking": "true"}
            p = _write_request(tmp, _valid_request(blocker=blocker))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "blocker-blocking-not-boolean")

    def test_empty_completed_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(completed_work=""))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "invalid-completed-work")

    def test_advisor_used_not_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(advisor_used="yes"))
            ok, data, diag = mod.validate_request(str(p))
            self.assertFalse(ok)
            self.assertEqual(diag["reason"], "advisor-used-not-boolean")

    def test_advisor_used_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(advisor_used=True))
            ok, data, diag = mod.validate_request(str(p))
            self.assertTrue(ok)
            self.assertTrue(data["advisor_used"])

    # --- Normalization ---

    def test_normalized_output_is_deterministic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request())
            ok, data, diag = mod.validate_request(str(p))
            self.assertTrue(ok)
            text = json.dumps(data, indent=2, sort_keys=True)
            reparsed = json.loads(text)
            self.assertEqual(reparsed, data)

    def test_normalized_excludes_unknown_fields(self):
        """Even if the input had extra fields (impossible after validation),
        normalized output only contains contract fields."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request())
            ok, data, diag = mod.validate_request(str(p))
            self.assertTrue(ok)
            self.assertEqual(set(data.keys()), mod.REQUIRED_TOP_LEVEL)
            self.assertEqual(set(data["blocker"].keys()), mod.REQUIRED_BLOCKER_FIELDS)


class MainFunctionTests(unittest.TestCase):
    """Test the CLI main function behavior."""

    def test_main_returns_zero_on_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request())
            rc = mod.main([p])
            self.assertEqual(rc, 0)

    def test_main_returns_one_on_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text("{}", encoding="utf-8")
            rc = mod.main([str(p)])
            self.assertEqual(rc, 1)

    def test_archive_valid_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request())
            archive = Path(tmp) / "valid.json"
            rc = mod.main([p, "--archive-valid", str(archive)])
            self.assertEqual(rc, 0)
            self.assertTrue(archive.exists())
            data = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)

    def test_archive_invalid_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text('{"schema_version": 99}', encoding="utf-8")
            archive = Path(tmp) / "invalid.json"
            rc = mod.main([str(p), "--archive-invalid", str(archive)])
            self.assertEqual(rc, 1)
            self.assertTrue(archive.exists())
            data = json.loads(archive.read_text(encoding="utf-8"))
            self.assertIn("raw_input", data)
            self.assertIn("diagnostic", data)

    def test_expected_task_id_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_request(tmp, _valid_request(task_id="wrong"))
            rc = mod.main([p, "--expected-task-id", "correct"])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
