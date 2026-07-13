"""Tests for validate-advisor-response.py.

Covers: valid response, every enum value, request/hash/reservation mismatch,
risk change, resume false, scope expansion, forbidden relaxation, unknown fields,
unsafe paths, and Windows/Python 3.9-compatible path/JSON behavior.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "validate-advisor-response.py"
spec = importlib.util.spec_from_file_location("validate_advisor_response", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def make_response(**overrides):
    """Build a valid response dict with optional overrides."""
    base = {
        "schema_version": 1,
        "request_id": "abc123",
        "advisor": "spark",
        "reservation_id": "res-001",
        "evidence_hash": "a" * 64,
        "decision": "continue",
        "answer": "Do X then Y",
        "allowed_changes": ["src/foo.py"],
        "forbidden_changes": ["src/forbidden/"],
        "new_validation": ["python -m pytest tests/test_foo.py"],
        "risk_changed": False,
        "resume_allowed": True,
    }
    base.update(overrides)
    return base


class TestValidResponse(unittest.TestCase):
    """Valid responses pass validation."""

    def test_valid_continue_response(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertIsNotNone(data)
        self.assertIsNone(diag)
        self.assertTrue(data["resume_eligible"])

    def test_valid_narrow_response(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(decision="narrow"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertTrue(data["resume_eligible"])

    def test_valid_stop_response(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(decision="stop", resume_allowed=False), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertFalse(data["resume_eligible"])

    def test_valid_split_response(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(decision="split", resume_allowed=False), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertFalse(data["resume_eligible"])


class TestEnums(unittest.TestCase):
    """Every enum value is accepted or rejected correctly."""

    def test_all_valid_advisors(self):
        for advisor in ("spark", "codex", "human"):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(make_response(advisor=advisor), f)
                f.flush()
                ok, data, diag = mod.validate_response(f.name)
            self.assertTrue(ok, f"advisor={advisor} should be valid, got {diag}")

    def test_invalid_advisor_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(advisor="gpt-4"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "invalid-advisor")

    def test_all_valid_decisions(self):
        for decision in ("continue", "narrow", "split", "stop"):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(make_response(decision=decision), f)
                f.flush()
                ok, data, diag = mod.validate_response(f.name)
            self.assertTrue(ok, f"decision={decision} should be valid, got {diag}")

    def test_invalid_decision_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(decision="maybe"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "invalid-decision")


class TestBindingMismatch(unittest.TestCase):
    """Request ID, evidence hash, and reservation ID mismatches are rejected."""

    def test_request_id_mismatch(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(request_id="wrong"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name, expected_request_id="abc123")
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "request-id-mismatch")

    def test_evidence_hash_mismatch(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(evidence_hash="b" * 64), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name, expected_evidence_hash="a" * 64)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "evidence-hash-mismatch")

    def test_reservation_id_mismatch(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(reservation_id="res-wrong"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name, expected_reservation_id="res-001")
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "reservation-id-mismatch")


class TestRiskAndResume(unittest.TestCase):
    """Risk change and resume_allowed interactions."""

    def test_risk_changed_blocks_resume(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(risk_changed=True), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertFalse(data["resume_eligible"])

    def test_resume_false_blocks_resume(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(resume_allowed=False), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertFalse(data["resume_eligible"])

    def test_stop_decision_blocks_resume(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(decision="stop"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertTrue(ok)
        self.assertFalse(data["resume_eligible"])

    def test_risk_changed_not_boolean_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(risk_changed="yes"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "risk-changed-not-boolean")

    def test_resume_allowed_not_boolean_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(resume_allowed=1), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "resume-allowed-not-boolean")


class TestScopeExpansion(unittest.TestCase):
    """Scope expansion and forbidden relaxation are rejected."""

    def test_scope_expansion_rejected(self):
        original_allowed = ["src/foo.py"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(allowed_changes=["src/bar.py"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(
                f.name, original_allowed_changes=original_allowed
            )
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "scope-expansion")

    def test_allowed_subset_of_original_accepted(self):
        original_allowed = ["src/foo.py", "src/bar.py"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(allowed_changes=["src/foo.py"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(
                f.name, original_allowed_changes=original_allowed
            )
        self.assertTrue(ok)

    def test_allowed_prefix_match_accepted(self):
        original_allowed = ["src/"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(allowed_changes=["src/foo.py"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(
                f.name, original_allowed_changes=original_allowed
            )
        self.assertTrue(ok)

    def test_forbidden_relaxation_rejected(self):
        original_forbidden = ["src/secret/", "src/config/"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(forbidden_changes=["src/secret/"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(
                f.name, original_forbidden_changes=original_forbidden
            )
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "forbidden-relaxation")

    def test_forbidden_superset_accepted(self):
        original_forbidden = ["src/secret/"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(forbidden_changes=["src/secret/", "src/extra/"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(
                f.name, original_forbidden_changes=original_forbidden
            )
        self.assertTrue(ok)

    def test_allowed_forbidden_overlap_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(
                allowed_changes=["src/foo.py"],
                forbidden_changes=["src/foo.py"],
            ), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "allowed-forbidden-overlap")


class TestUnknownFields(unittest.TestCase):
    """Unknown and missing fields are rejected."""

    def test_unknown_top_level_field_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            resp = make_response()
            resp["extra_field"] = "bad"
            json.dump(resp, f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "unknown-fields")
        self.assertIn("extra_field", diag["detail"])

    def test_missing_required_field_rejected(self):
        resp = make_response()
        del resp["answer"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(resp, f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "missing-fields")

    def test_empty_answer_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(answer=""), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "empty-answer")


class TestUnsafePaths(unittest.TestCase):
    """Absolute paths and path traversal are rejected."""

    def test_absolute_path_in_allowed_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(allowed_changes=["/etc/passwd"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "allowed-change-unsafe-path")

    def test_traversal_path_in_forbidden_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(forbidden_changes=["../../etc/passwd"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "forbidden-change-unsafe-path")

    def test_duplicate_allowed_changes_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(allowed_changes=["src/foo.py", "src/foo.py"]), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "allowed-changes-duplicate")


class TestMalformedInput(unittest.TestCase):
    """Malformed JSON and non-objects are rejected."""

    def test_not_json_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{not valid json")
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "malformed-json")

    def test_not_object_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "not-an-object")

    def test_file_not_found_rejected(self):
        ok, data, diag = mod.validate_response("/nonexistent/path.json")
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "file-not-found")


class TestSchemaVersion(unittest.TestCase):
    """Schema version must be integer 1."""

    def test_wrong_schema_version_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(schema_version=2), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "unsupported-schema-version")

    def test_string_schema_version_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(make_response(schema_version="1"), f)
            f.flush()
            ok, data, diag = mod.validate_response(f.name)
        self.assertFalse(ok)
        self.assertEqual(diag["reason"], "unsupported-schema-version")


class TestRegistration(unittest.TestCase):
    """aiwf.py and install_workflow.py register the new commands."""

    def test_aiwf_registers_advisor_call(self):
        aiwf_text = (ROOT / "scripts" / "aiwf.py").read_text(encoding="utf-8")
        self.assertIn('"advisor-call"', aiwf_text)
        self.assertIn('"advisor-call.py"', aiwf_text)

    def test_aiwf_registers_validate_advisor_response(self):
        aiwf_text = (ROOT / "scripts" / "aiwf.py").read_text(encoding="utf-8")
        self.assertIn('"validate-advisor-response"', aiwf_text)
        self.assertIn('"validate-advisor-response.py"', aiwf_text)

    def test_install_workflow_registers_new_scripts(self):
        install_text = (ROOT / "scripts" / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("validate-advisor-response.py", install_text)
        self.assertIn("advisor-call.py", install_text)
        self.assertIn("ai/validate-advisor-response.py", install_text)
        self.assertIn("ai/advisor-call.py", install_text)


if __name__ == "__main__":
    unittest.main()
