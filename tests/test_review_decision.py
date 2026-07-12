"""Tests for the structured review decision v1 parser, module, and shell integration.

Covers:
- accept/revise/split/reject decision types and phase/whole-task scope validation
- invalid/missing/unknown field, version, type, and duplicate-id rejection
- next_task consistency rules (null for whole-task accept, object for revise/split)
- raw and fenced JSON extraction, zero/multiple/malformed rejection
- misleading prose cannot override JSON decision
- atomic write outputs, UTF-8 content, spaces in paths, next-task draft extraction
- fake-Codex review-with-codex.sh success and malformed failure
- static/integration assertion that run-loop has no decision-word grep
- installer copies review_decision.py, parse-review-decision.py, and schema
- Python compile and shell syntax checks
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMAS = ROOT / "schemas"

# Make scripts/ importable
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import review_decision as rd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_decision(**overrides):
    """Return a minimal valid review decision dict, with optional overrides."""
    data = {
        "schema_version": 1,
        "decision": "accept",
        "scope": "phase",
        "reasoning": "Tests pass and implementation matches spec.",
        "direction": {"status": "accepted"},
        "acceptance": [
            {"id": "AC-1", "status": "satisfied", "evidence": ["tests/output.log"]}
        ],
        "validation": {"status": "passed", "failed_checks": []},
        "next_task": None,
        "lessons": ["Always validate edge cases."],
    }
    data.update(overrides)
    return data


def _make_next_task(**overrides):
    """Return a minimal valid next_task object."""
    nt = {
        "mode": "builder",
        "goal": "Implement remaining feature.",
        "acceptance": [{"id": "NT-1", "description": "Feature works end-to-end."}],
    }
    nt.update(overrides)
    return nt


# ===========================================================================
# Module: extraction
# ===========================================================================

class TestExtractDecisionJson(unittest.TestCase):
    """extract_decision_json: raw JSON, fenced blocks, zero/multiple/malformed."""

    def test_raw_json_object(self):
        data = _make_valid_decision()
        text = json.dumps(data, indent=2)
        result = rd.extract_decision_json(text)
        self.assertEqual(result["decision"], "accept")

    def test_fenced_json_block(self):
        data = _make_valid_decision(decision="revise", next_task=_make_next_task())
        text = f"Here is my review:\n```json\n{json.dumps(data)}\n```\nDone."
        result = rd.extract_decision_json(text)
        self.assertEqual(result["decision"], "revise")

    def test_raw_json_with_surrounding_whitespace(self):
        data = _make_valid_decision()
        text = f"  \n  {json.dumps(data)}  \n  "
        result = rd.extract_decision_json(text)
        self.assertEqual(result["schema_version"], 1)

    def test_zero_candidates_raises(self):
        with self.assertRaises(rd.ExtractionError):
            rd.extract_decision_json("No JSON here at all.")

    def test_multiple_fenced_blocks_raises(self):
        data1 = _make_valid_decision(decision="accept")
        data2 = _make_valid_decision(decision="reject")
        text = (
            f"```json\n{json.dumps(data1)}\n```\n"
            f"```json\n{json.dumps(data2)}\n```"
        )
        with self.assertRaises(rd.ExtractionError) as ctx:
            rd.extract_decision_json(text)
        self.assertIn("2", str(ctx.exception))

    def test_raw_plus_fenced_raises(self):
        data = _make_valid_decision()
        text = f"{json.dumps(data)}\n```json\n{json.dumps(data)}\n```"
        with self.assertRaises(rd.ExtractionError):
            rd.extract_decision_json(text)

    def test_malformed_json_in_fenced_raises(self):
        text = "```json\n{broken json}\n```"
        with self.assertRaises(rd.ExtractionError):
            rd.extract_decision_json(text)

    def test_non_object_json_raises(self):
        with self.assertRaises(rd.ExtractionError):
            rd.extract_decision_json('"just a string"')

    def test_json_array_raises(self):
        with self.assertRaises(rd.ExtractionError):
            rd.extract_decision_json('[{"decision": "accept"}]')

    def test_misleading_prose_cannot_override_json(self):
        """Prose containing ACCEPT/REVISE/SPLIT/REJECT words must not affect extraction."""
        data = _make_valid_decision(decision="reject")
        prose = (
            "I think this should be ACCEPT. The work looks great. "
            "REVISE would be too harsh. SPLIT is unnecessary.\n"
            f"```json\n{json.dumps(data)}\n```\n"
            "Overall I recommend ACCEPT."
        )
        result = rd.extract_decision_json(prose)
        self.assertEqual(result["decision"], "reject")


# ===========================================================================
# Module: validation — valid decisions
# ===========================================================================

class TestValidateDecisionValid(unittest.TestCase):
    """validate_decision returns empty errors for valid inputs."""

    def test_accept_phase(self):
        data = _make_valid_decision(decision="accept", scope="phase")
        self.assertEqual(rd.validate_decision(data), [])

    def test_accept_whole_task(self):
        data = _make_valid_decision(decision="accept", scope="whole-task", next_task=None)
        self.assertEqual(rd.validate_decision(data), [])

    def test_revise_with_next_task(self):
        data = _make_valid_decision(decision="revise", next_task=_make_next_task())
        self.assertEqual(rd.validate_decision(data), [])

    def test_split_with_next_task(self):
        data = _make_valid_decision(decision="split", next_task=_make_next_task())
        self.assertEqual(rd.validate_decision(data), [])

    def test_reject(self):
        data = _make_valid_decision(decision="reject")
        self.assertEqual(rd.validate_decision(data), [])

    def test_all_direction_statuses(self):
        for status in rd.VALID_DIRECTION_STATUSES:
            data = _make_valid_decision(direction={"status": status})
            errors = rd.validate_decision(data)
            self.assertEqual(errors, [], f"direction status '{status}' should be valid")

    def test_all_acceptance_statuses(self):
        for status in rd.VALID_ACCEPTANCE_STATUSES:
            data = _make_valid_decision(
                acceptance=[{"id": "AC-1", "status": status, "evidence": ["x"]}]
            )
            errors = rd.validate_decision(data)
            self.assertEqual(errors, [], f"acceptance status '{status}' should be valid")

    def test_all_validation_statuses(self):
        for status in rd.VALID_VALIDATION_STATUSES:
            data = _make_valid_decision(validation={"status": status})
            errors = rd.validate_decision(data)
            self.assertEqual(errors, [], f"validation status '{status}' should be valid")

    def test_validation_with_failed_checks(self):
        data = _make_valid_decision(
            validation={"status": "failed", "failed_checks": ["lint", "test-unit"]}
        )
        self.assertEqual(rd.validate_decision(data), [])

    def test_next_task_with_all_modes(self):
        for mode in rd.VALID_MODES:
            data = _make_valid_decision(
                decision="revise", next_task=_make_next_task(mode=mode)
            )
            errors = rd.validate_decision(data)
            self.assertEqual(errors, [], f"next_task mode '{mode}' should be valid")

    def test_next_task_with_scope_and_profile(self):
        nt = _make_next_task()
        nt["scope"] = {"files": ["src/foo.py"]}
        nt["profile"] = "bugfix"
        data = _make_valid_decision(decision="revise", next_task=nt)
        self.assertEqual(rd.validate_decision(data), [])

    def test_acceptance_with_multiple_items(self):
        data = _make_valid_decision(
            acceptance=[
                {"id": "AC-1", "status": "satisfied", "evidence": ["a"]},
                {"id": "AC-2", "status": "partial", "evidence": ["b", "c"]},
            ]
        )
        self.assertEqual(rd.validate_decision(data), [])


# ===========================================================================
# Module: validation — invalid decisions
# ===========================================================================

class TestValidateDecisionInvalid(unittest.TestCase):
    """validate_decision rejects invalid inputs with specific errors."""

    def test_not_a_dict(self):
        errors = rd.validate_decision("not a dict")
        self.assertTrue(any("expected object" in e for e in errors))

    def test_missing_schema_version(self):
        data = _make_valid_decision()
        del data["schema_version"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_wrong_schema_version(self):
        data = _make_valid_decision(schema_version=2)
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected 1" in e for e in errors))

    def test_missing_decision(self):
        data = _make_valid_decision()
        del data["decision"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("decision" in e for e in errors))

    def test_unknown_decision(self):
        data = _make_valid_decision(decision="maybe")
        errors = rd.validate_decision(data)
        self.assertTrue(any("maybe" in e for e in errors))

    def test_missing_scope(self):
        data = _make_valid_decision()
        del data["scope"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("scope" in e for e in errors))

    def test_unknown_scope(self):
        data = _make_valid_decision(scope="universe")
        errors = rd.validate_decision(data)
        self.assertTrue(any("universe" in e for e in errors))

    def test_missing_reasoning(self):
        data = _make_valid_decision()
        del data["reasoning"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("reasoning" in e for e in errors))

    def test_empty_reasoning(self):
        data = _make_valid_decision(reasoning="")
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty string" in e for e in errors))

    def test_missing_direction(self):
        data = _make_valid_decision()
        del data["direction"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("direction" in e for e in errors))

    def test_direction_not_object(self):
        data = _make_valid_decision(direction="accepted")
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected object" in e for e in errors))

    def test_direction_missing_status(self):
        data = _make_valid_decision(direction={})
        errors = rd.validate_decision(data)
        self.assertTrue(any("status" in e for e in errors))

    def test_direction_unknown_status(self):
        data = _make_valid_decision(direction={"status": "pending"})
        errors = rd.validate_decision(data)
        self.assertTrue(any("pending" in e for e in errors))

    def test_direction_unknown_key(self):
        data = _make_valid_decision(direction={"status": "accepted", "extra": True})
        errors = rd.validate_decision(data)
        self.assertTrue(any("unknown key" in e and "extra" in e for e in errors))

    def test_missing_acceptance(self):
        data = _make_valid_decision()
        del data["acceptance"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("acceptance" in e for e in errors))

    def test_acceptance_not_array(self):
        data = _make_valid_decision(acceptance="AC-1")
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected array" in e for e in errors))

    def test_acceptance_item_not_object(self):
        data = _make_valid_decision(acceptance=["AC-1"])
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected object" in e for e in errors))

    def test_acceptance_missing_id(self):
        data = _make_valid_decision(
            acceptance=[{"status": "satisfied", "evidence": ["x"]}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("id" in e for e in errors))

    def test_acceptance_empty_id(self):
        data = _make_valid_decision(
            acceptance=[{"id": "", "status": "satisfied", "evidence": ["x"]}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty" in e for e in errors))

    def test_acceptance_duplicate_id(self):
        data = _make_valid_decision(
            acceptance=[
                {"id": "AC-1", "status": "satisfied", "evidence": ["x"]},
                {"id": "AC-1", "status": "failed", "evidence": ["y"]},
            ]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("duplicate" in e and "AC-1" in e for e in errors))

    def test_acceptance_unknown_status(self):
        data = _make_valid_decision(
            acceptance=[{"id": "AC-1", "status": "unknown", "evidence": ["x"]}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("unknown" in e for e in errors))

    def test_acceptance_missing_evidence(self):
        data = _make_valid_decision(
            acceptance=[{"id": "AC-1", "status": "satisfied"}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("evidence" in e for e in errors))

    def test_acceptance_evidence_not_array(self):
        data = _make_valid_decision(
            acceptance=[{"id": "AC-1", "status": "satisfied", "evidence": "x"}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected array" in e for e in errors))

    def test_acceptance_evidence_empty_string(self):
        data = _make_valid_decision(
            acceptance=[{"id": "AC-1", "status": "satisfied", "evidence": [""]}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty" in e for e in errors))

    def test_acceptance_unknown_key(self):
        data = _make_valid_decision(
            acceptance=[{"id": "AC-1", "status": "satisfied", "evidence": ["x"], "extra": True}]
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("unknown key" in e and "extra" in e for e in errors))

    def test_missing_validation(self):
        data = _make_valid_decision()
        del data["validation"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("validation" in e for e in errors))

    def test_validation_not_object(self):
        data = _make_valid_decision(validation="passed")
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected object" in e for e in errors))

    def test_validation_missing_status(self):
        data = _make_valid_decision(validation={})
        errors = rd.validate_decision(data)
        self.assertTrue(any("status" in e for e in errors))

    def test_validation_unknown_status(self):
        data = _make_valid_decision(validation={"status": "maybe"})
        errors = rd.validate_decision(data)
        self.assertTrue(any("maybe" in e for e in errors))

    def test_validation_failed_checks_not_array(self):
        data = _make_valid_decision(validation={"status": "failed", "failed_checks": "lint"})
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected array" in e for e in errors))

    def test_validation_failed_checks_empty_string(self):
        data = _make_valid_decision(
            validation={"status": "failed", "failed_checks": [""]}
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty" in e for e in errors))

    def test_validation_unknown_key(self):
        data = _make_valid_decision(validation={"status": "passed", "extra": True})
        errors = rd.validate_decision(data)
        self.assertTrue(any("unknown key" in e and "extra" in e for e in errors))

    def test_missing_next_task(self):
        data = _make_valid_decision()
        del data["next_task"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("next_task" in e for e in errors))

    def test_next_task_not_null_or_object(self):
        data = _make_valid_decision(next_task="invalid")
        errors = rd.validate_decision(data)
        self.assertTrue(any("null or object" in e for e in errors))

    def test_next_task_missing_mode(self):
        nt = {"goal": "x", "acceptance": [{"id": "a", "description": "b"}]}
        data = _make_valid_decision(decision="revise", next_task=nt)
        errors = rd.validate_decision(data)
        self.assertTrue(any("mode" in e for e in errors))

    def test_next_task_missing_goal(self):
        nt = {"mode": "builder", "acceptance": [{"id": "a", "description": "b"}]}
        data = _make_valid_decision(decision="revise", next_task=nt)
        errors = rd.validate_decision(data)
        self.assertTrue(any("goal" in e for e in errors))

    def test_next_task_missing_acceptance(self):
        nt = {"mode": "builder", "goal": "x"}
        data = _make_valid_decision(decision="revise", next_task=nt)
        errors = rd.validate_decision(data)
        self.assertTrue(any("acceptance" in e for e in errors))

    def test_next_task_empty_acceptance(self):
        nt = {"mode": "builder", "goal": "x", "acceptance": []}
        data = _make_valid_decision(decision="revise", next_task=nt)
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty array" in e for e in errors))

    def test_next_task_unknown_mode(self):
        data = _make_valid_decision(
            decision="revise", next_task=_make_next_task(mode="turbo")
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("turbo" in e for e in errors))

    def test_next_task_empty_goal(self):
        data = _make_valid_decision(
            decision="revise", next_task=_make_next_task(goal="")
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty" in e for e in errors))

    def test_next_task_acceptance_item_missing_id(self):
        nt = _make_next_task(acceptance=[{"description": "x"}])
        data = _make_valid_decision(decision="revise", next_task=nt)
        errors = rd.validate_decision(data)
        self.assertTrue(any("id" in e for e in errors))

    def test_next_task_acceptance_item_missing_description(self):
        nt = _make_next_task(acceptance=[{"id": "a"}])
        data = _make_valid_decision(decision="revise", next_task=nt)
        errors = rd.validate_decision(data)
        self.assertTrue(any("description" in e for e in errors))

    def test_unknown_top_level_field(self):
        data = _make_valid_decision(extra_field="surprise")
        errors = rd.validate_decision(data)
        self.assertTrue(any("unknown top-level field" in e and "extra_field" in e for e in errors))

    def test_missing_lessons(self):
        data = _make_valid_decision()
        del data["lessons"]
        errors = rd.validate_decision(data)
        self.assertTrue(any("lessons" in e for e in errors))

    def test_lessons_not_array(self):
        data = _make_valid_decision(lessons="learned something")
        errors = rd.validate_decision(data)
        self.assertTrue(any("expected array" in e for e in errors))

    def test_lessons_empty_string(self):
        data = _make_valid_decision(lessons=[""])
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty" in e for e in errors))

    def test_lessons_not_string(self):
        data = _make_valid_decision(lessons=[42])
        errors = rd.validate_decision(data)
        self.assertTrue(any("non-empty" in e for e in errors))


# ===========================================================================
# Module: validation — consistency checks
# ===========================================================================

class TestValidateConsistency(unittest.TestCase):
    """Cross-field consistency rules."""

    def test_whole_task_accept_requires_null_next_task(self):
        data = _make_valid_decision(
            decision="accept", scope="whole-task", next_task=_make_next_task()
        )
        errors = rd.validate_decision(data)
        self.assertTrue(any("whole-task accept" in e and "null" in e for e in errors))

    def test_revise_requires_next_task_object(self):
        data = _make_valid_decision(decision="revise", next_task=None)
        errors = rd.validate_decision(data)
        self.assertTrue(any("revise" in e and "object" in e for e in errors))

    def test_split_requires_next_task_object(self):
        data = _make_valid_decision(decision="split", next_task=None)
        errors = rd.validate_decision(data)
        self.assertTrue(any("split" in e and "object" in e for e in errors))

    def test_phase_accept_allows_null_next_task(self):
        data = _make_valid_decision(decision="accept", scope="phase", next_task=None)
        self.assertEqual(rd.validate_decision(data), [])

    def test_phase_accept_allows_next_task_object(self):
        data = _make_valid_decision(
            decision="accept", scope="phase", next_task=_make_next_task()
        )
        self.assertEqual(rd.validate_decision(data), [])

    def test_reject_allows_null_next_task(self):
        data = _make_valid_decision(decision="reject", next_task=None)
        self.assertEqual(rd.validate_decision(data), [])


# ===========================================================================
# Module: parse_and_validate
# ===========================================================================

class TestParseAndValidate(unittest.TestCase):
    """parse_and_validate: combined extract + validate."""

    def test_valid_raw_json(self):
        data = _make_valid_decision()
        result = rd.parse_and_validate(json.dumps(data))
        self.assertEqual(result["decision"], "accept")

    def test_valid_fenced_json(self):
        data = _make_valid_decision(decision="reject")
        text = f"Review:\n```json\n{json.dumps(data)}\n```\n"
        result = rd.parse_and_validate(text)
        self.assertEqual(result["decision"], "reject")

    def test_extraction_error_propagates(self):
        with self.assertRaises(rd.ExtractionError):
            rd.parse_and_validate("no json")

    def test_validation_error_propagates(self):
        data = _make_valid_decision(decision="invalid")
        with self.assertRaises(rd.ValidationError):
            rd.parse_and_validate(json.dumps(data))

    def test_validation_error_message_contains_path(self):
        data = _make_valid_decision(decision="invalid")
        with self.assertRaises(rd.ValidationError) as ctx:
            rd.parse_and_validate(json.dumps(data))
        self.assertIn("decision", str(ctx.exception))


# ===========================================================================
# Module: extract_next_task_draft
# ===========================================================================

class TestExtractNextTaskDraft(unittest.TestCase):
    """extract_next_task_draft returns next_task or None."""

    def test_returns_none_when_null(self):
        data = _make_valid_decision(next_task=None)
        self.assertIsNone(rd.extract_next_task_draft(data))

    def test_returns_object_when_present(self):
        nt = _make_next_task()
        data = _make_valid_decision(decision="revise", next_task=nt)
        result = rd.extract_next_task_draft(data)
        self.assertIsNotNone(result)
        self.assertEqual(result["mode"], "builder")

    def test_missing_key_returns_none(self):
        data = {"schema_version": 1}
        self.assertIsNone(rd.extract_next_task_draft(data))


# ===========================================================================
# Module: write_json_atomic
# ===========================================================================

class TestWriteJsonAtomic(unittest.TestCase):
    """write_json_atomic: atomic write, UTF-8, spaces in paths."""

    def test_writes_valid_json(self):
        data = _make_valid_decision()
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "decision.json"
            rd.write_json_atomic(data, dest)
            result = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(result["decision"], "accept")
            self.assertEqual(result["schema_version"], 1)

    def test_creates_parent_dirs(self):
        data = _make_valid_decision()
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "sub" / "dir" / "decision.json"
            rd.write_json_atomic(data, dest)
            self.assertTrue(dest.exists())

    def test_overwrites_existing(self):
        data1 = _make_valid_decision(decision="accept")
        data2 = _make_valid_decision(decision="reject")
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "decision.json"
            rd.write_json_atomic(data1, dest)
            rd.write_json_atomic(data2, dest)
            result = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(result["decision"], "reject")

    def test_utf8_content_roundtrip(self):
        data = _make_valid_decision(reasoning="Passes all tests — go/no-go ✓")
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "decision.json"
            rd.write_json_atomic(data, dest)
            result = json.loads(dest.read_text(encoding="utf-8"))
            self.assertIn("✓", result["reasoning"])
            self.assertIn("—", result["reasoning"])

    def test_spaces_in_path(self):
        data = _make_valid_decision()
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "my folder" / "review decision.json"
            rd.write_json_atomic(data, dest)
            self.assertTrue(dest.exists())

    def test_no_temp_file_left_on_success(self):
        data = _make_valid_decision()
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "decision.json"
            rd.write_json_atomic(data, dest)
            tmp_files = list(pathlib.Path(tmp).glob(".review-decision-*.tmp"))
            self.assertEqual(tmp_files, [])


# ===========================================================================
# Module: load_review_text
# ===========================================================================

class TestLoadReviewText(unittest.TestCase):
    """load_review_text: file reading and error handling."""

    def test_reads_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "review.txt"
            path.write_text("Review content here.", encoding="utf-8")
            result = rd.load_review_text(path)
            self.assertEqual(result, "Review content here.")

    def test_missing_file_raises_validation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "nonexistent.txt"
            with self.assertRaises(rd.ValidationError):
                rd.load_review_text(path)

    def test_utf8_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "review.txt"
            path.write_text("Décision: accepté ✓", encoding="utf-8")
            result = rd.load_review_text(path)
            self.assertIn("✓", result)


# ===========================================================================
# Module: find_default_schema_path
# ===========================================================================

class TestFindDefaultSchemaPath(unittest.TestCase):
    """find_default_schema_path resolves correctly."""

    def test_resolves_source_path(self):
        # In source-checkout layout, schemas/ is sibling to scripts/
        path = rd.find_default_schema_path()
        self.assertTrue(path.name.endswith(".schema.json"))

    def test_schema_file_exists(self):
        path = rd.find_default_schema_path()
        self.assertTrue(path.is_file(), f"Schema not found at {path}")


# ===========================================================================
# Module: error classes
# ===========================================================================

class TestErrorClasses(unittest.TestCase):
    """Error class hierarchy and path attribute."""

    def test_review_decision_error_hierarchy(self):
        self.assertTrue(issubclass(rd.ValidationError, rd.ReviewDecisionError))
        self.assertTrue(issubclass(rd.ExtractionError, rd.ReviewDecisionError))

    def test_error_with_path(self):
        err = rd.ValidationError("bad field", path="root.decision")
        self.assertEqual(err.path, "root.decision")
        self.assertIn("root.decision", str(err))

    def test_error_without_path(self):
        err = rd.ValidationError("bad field")
        self.assertEqual(err.path, "")
        self.assertEqual(str(err), "bad field")


# ===========================================================================
# Module: constants
# ===========================================================================

class TestConstants(unittest.TestCase):
    """Module-level constants match schema expectations."""

    def test_schema_version(self):
        self.assertEqual(rd.SCHEMA_VERSION, 1)

    def test_valid_decisions(self):
        self.assertEqual(rd.VALID_DECISIONS, ("accept", "revise", "split", "reject"))

    def test_valid_scopes(self):
        self.assertEqual(rd.VALID_SCOPES, ("phase", "whole-task"))

    def test_required_top_level(self):
        expected = {
            "schema_version", "decision", "scope", "reasoning",
            "direction", "acceptance", "validation", "next_task", "lessons",
        }
        self.assertEqual(rd.TOP_LEVEL_PROPERTY_NAMES, expected)

    def test_acceptance_keys(self):
        self.assertEqual(rd.VALID_ACCEPTANCE_KEYS, {"id", "status", "evidence"})

    def test_direction_keys(self):
        self.assertEqual(rd.VALID_DIRECTION_KEYS, {"status"})

    def test_validation_keys(self):
        self.assertEqual(rd.VALID_VALIDATION_KEYS, {"status", "failed_checks"})

    def test_next_task_keys(self):
        self.assertEqual(rd.VALID_NEXT_TASK_KEYS, {"mode", "goal", "acceptance", "scope", "profile"})


# ===========================================================================
# CLI: parse-review-decision.py
# ===========================================================================

class TestParseReviewDecisionCLI(unittest.TestCase):
    """Integration tests for the parse-review-decision.py CLI."""

    PARSE_SCRIPT = SCRIPTS / "parse-review-decision.py"

    def _run_cli(self, review_text, extra_args=None):
        """Run the CLI with given review text and return (exit_code, stdout, stderr)."""
        with tempfile.TemporaryDirectory() as tmp:
            review_file = pathlib.Path(tmp) / "review.txt"
            review_file.write_text(review_text, encoding="utf-8")
            cmd = [sys.executable, str(self.PARSE_SCRIPT), str(review_file)]
            if extra_args:
                cmd.extend(extra_args)
            result = subprocess.run(
                cmd, cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True,
            )
            return result.returncode, result.stdout, result.stderr

    def test_success_with_raw_json(self):
        data = _make_valid_decision()
        code, stdout, stderr = self._run_cli(json.dumps(data, indent=2))
        self.assertEqual(code, 0)
        self.assertIn("Review Decision:", stdout)

    def test_success_with_fenced_json(self):
        data = _make_valid_decision(decision="reject")
        text = f"Review text:\n```json\n{json.dumps(data)}\n```"
        code, stdout, stderr = self._run_cli(text)
        self.assertEqual(code, 0)
        self.assertIn("Review Decision:", stdout)

    def test_failure_with_no_json(self):
        code, stdout, stderr = self._run_cli("No decision here.")
        self.assertNotEqual(code, 0)
        self.assertIn("Error", stderr)

    def test_failure_with_invalid_decision(self):
        data = _make_valid_decision(decision="invalid")
        code, stdout, stderr = self._run_cli(json.dumps(data))
        self.assertNotEqual(code, 0)
        self.assertIn("Error", stderr)

    def test_writes_output_file(self):
        data = _make_valid_decision()
        with tempfile.TemporaryDirectory() as tmp:
            review_file = pathlib.Path(tmp) / "review.txt"
            review_file.write_text(json.dumps(data), encoding="utf-8")
            output_file = pathlib.Path(tmp) / "output.json"
            cmd = [
                sys.executable, str(self.PARSE_SCRIPT),
                str(review_file), "--output", str(output_file),
            ]
            result = subprocess.run(
                cmd, cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(output_file.exists())
            written = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(written["decision"], "accept")

    def test_next_task_draft_written(self):
        nt = _make_next_task()
        data = _make_valid_decision(decision="revise", next_task=nt)
        with tempfile.TemporaryDirectory() as tmp:
            review_file = pathlib.Path(tmp) / "review.txt"
            review_file.write_text(json.dumps(data), encoding="utf-8")
            draft_file = pathlib.Path(tmp) / "draft.json"
            cmd = [
                sys.executable, str(self.PARSE_SCRIPT),
                str(review_file), "--next-task-draft", str(draft_file),
            ]
            result = subprocess.run(
                cmd, cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(draft_file.exists())
            written = json.loads(draft_file.read_text(encoding="utf-8"))
            self.assertEqual(written["mode"], "builder")
            self.assertIn("Next Task Draft:", result.stdout)

    def test_no_draft_when_next_task_null(self):
        data = _make_valid_decision(next_task=None)
        with tempfile.TemporaryDirectory() as tmp:
            review_file = pathlib.Path(tmp) / "review.txt"
            review_file.write_text(json.dumps(data), encoding="utf-8")
            cmd = [sys.executable, str(self.PARSE_SCRIPT), str(review_file)]
            result = subprocess.run(
                cmd, cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("Next Task Draft:", result.stdout)

    def test_review_decision_path_printed(self):
        """CLI prints 'Review Decision: <path>' for run-loop to grep."""
        data = _make_valid_decision()
        code, stdout, _ = self._run_cli(json.dumps(data))
        self.assertEqual(code, 0)
        # Must start with "Review Decision:" on its own line
        lines = stdout.strip().splitlines()
        decision_lines = [l for l in lines if l.startswith("Review Decision:")]
        self.assertEqual(len(decision_lines), 1)

    def test_spaces_in_paths(self):
        data = _make_valid_decision()
        with tempfile.TemporaryDirectory() as tmp:
            spaced_dir = pathlib.Path(tmp) / "my folder"
            spaced_dir.mkdir()
            review_file = spaced_dir / "review.txt"
            review_file.write_text(json.dumps(data), encoding="utf-8")
            output_file = spaced_dir / "decision output.json"
            cmd = [
                sys.executable, str(self.PARSE_SCRIPT),
                str(review_file), "--output", str(output_file),
            ]
            result = subprocess.run(
                cmd, cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(output_file.exists())


# ===========================================================================
# Shell: review-with-codex.sh integration
# ===========================================================================

class TestReviewWithCodexShell(unittest.TestCase):
    """Integration tests for review-with-codex.sh with fake Codex CLI."""

    REVIEW_SCRIPT = SCRIPTS / "review-with-codex.sh"

    def test_script_has_parse_review_decision_call(self):
        """review-with-codex.sh must call parse-review-decision.py."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("parse-review-decision.py", content)

    def test_script_prints_review_decision_path(self):
        """review-with-codex.sh must print 'Review Decision:' for run-loop."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Review Decision:", content)

    def test_script_exits_on_parse_failure(self):
        """review-with-codex.sh must exit non-zero when parsing fails."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        # Should check PARSE_STATUS and exit
        self.assertIn("PARSE_STATUS", content)
        self.assertIn("exit $PARSE_STATUS", content)

    def test_script_structured_decision_required(self):
        """review-with-codex.sh asserts structured decision is required, not optional prose."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("structured decision required", content.lower())

    def test_script_no_prose_decision_override(self):
        """review-with-codex.sh states prose cannot override JSON decision."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Review text cannot override the JSON decision protocol", content)

    def test_script_json_decision_contract_in_prompt(self):
        """The review prompt must contain the JSON decision contract."""
        content = self.REVIEW_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("schema_version", content)
        self.assertIn('"decision": "accept|revise|split|reject"', content)
        self.assertIn('"scope": "phase|whole-task"', content)
        self.assertIn("JSON decision is authoritative", content)


# ===========================================================================
# Shell: run-loop.sh — no decision-word grep
# ===========================================================================

class TestRunLoopDecisionExtraction(unittest.TestCase):
    """run-loop.sh must use Review Decision: JSON path, not prose grep."""

    RUN_LOOP = SCRIPTS / "run-loop.sh"

    def test_no_decision_word_grep(self):
        """run-loop.sh must NOT grep for accept/revise/split/reject words in prose."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        # Check that there is no grep for decision words
        import re
        # Look for grep patterns that search for decision words in prose
        # Allow the case statement which operates on the extracted decision string
        decision_word_grep = re.compile(
            r"grep\s+.*['\"]?(accept|revise|split|reject)['\"]?", re.IGNORECASE
        )
        matches = decision_word_grep.findall(content)
        # Filter out the case statement which uses uppercase versions
        # The grep should find nothing — decision words must not be grepped from prose
        self.assertEqual(
            matches, [],
            f"run-loop.sh contains grep for decision words: {matches}"
        )

    def test_uses_review_decision_json_path(self):
        """run-loop.sh must grep 'Review Decision:' to get the JSON file path."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        self.assertIn("grep '^Review Decision:'", content)

    def test_reads_decision_from_json_file(self):
        """run-loop.sh must read decision from JSON file with Python."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        self.assertIn("data.get(\"decision\"", content)
        self.assertIn("data.get(\"scope\"", content)

    def test_case_statement_on_extracted_decision(self):
        """run-loop.sh uses case on the extracted decision, not prose matching."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        self.assertIn("case", content)
        # The case should operate on $DECISION, which comes from JSON
        self.assertIn('ACCEPT)', content)
        self.assertIn('REVISE)', content)
        self.assertIn('SPLIT)', content)
        self.assertIn('REJECT)', content)

    def test_requires_review_decision_file(self):
        """run-loop.sh requires the Review Decision file to exist."""
        content = self.RUN_LOOP.read_text(encoding="utf-8")
        self.assertIn("REVIEW_DECISION_FILE", content)
        self.assertIn("missing_review_decision", content)


# ===========================================================================
# Installer: copies review decision assets
# ===========================================================================

class TestInstallerCopiesReviewDecision(unittest.TestCase):
    """install_workflow.py must copy review_decision.py, parse-review-decision.py, and schema."""

    def test_review_decision_module_installed(self):
        """Installer maps review_decision.py -> ai/review_decision.py."""
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("review_decision.py", content)
        self.assertIn("ai/review_decision.py", content)

    def test_parse_review_decision_installed(self):
        """Installer maps parse-review-decision.py -> ai/parse-review-decision.py."""
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("parse-review-decision.py", content)
        self.assertIn("ai/parse-review-decision.py", content)

    def test_review_decision_schema_installed(self):
        """Installer maps review-decision-v1.schema.json -> ai/schemas/."""
        content = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("review-decision-v1.schema.json", content)
        self.assertIn("ai/schemas/review-decision-v1.schema.json", content)

    def test_installer_integration(self):
        """Run the installer and verify review decision assets are created."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "install_workflow.py"), str(repo)],
                cwd=str(ROOT), text=True, encoding="utf-8",
                errors="replace", capture_output=True, check=True,
            )
            self.assertTrue((repo / "ai" / "review_decision.py").exists())
            self.assertTrue((repo / "ai" / "parse-review-decision.py").exists())
            self.assertTrue((repo / "ai" / "schemas" / "review-decision-v1.schema.json").exists())


# ===========================================================================
# Python compile and shell syntax
# ===========================================================================

class TestPythonCompile(unittest.TestCase):
    """Python files must compile without syntax errors."""

    def test_review_decision_compiles(self):
        path = SCRIPTS / "review_decision.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")

    def test_parse_review_decision_compiles(self):
        path = SCRIPTS / "parse-review-decision.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")

    def test_install_workflow_compiles(self):
        path = SCRIPTS / "install_workflow.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Compile error: {result.stderr}")


@unittest.skipIf(os.name == "nt", "Bash syntax is covered by the dedicated Linux Shell syntax job")
class TestShellSyntax(unittest.TestCase):
    """Shell scripts must pass bash syntax check."""

    def test_review_with_codex_syntax(self):
        path = SCRIPTS / "review-with-codex.sh"
        result = subprocess.run(
            ["bash", "-n", path.name], cwd=str(SCRIPTS),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")

    def test_run_loop_syntax(self):
        path = SCRIPTS / "run-loop.sh"
        result = subprocess.run(
            ["bash", "-n", path.name], cwd=str(SCRIPTS),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")


# ===========================================================================
# Schema file
# ===========================================================================

class TestSchemaFile(unittest.TestCase):
    """Schema file is valid JSON and matches module constants."""

    def test_schema_is_valid_json(self):
        path = SCHEMAS / "review-decision-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_schema_has_required_fields(self):
        path = SCHEMAS / "review-decision-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        required = set(data.get("required", []))
        for field in rd.REQUIRED_TOP_LEVEL:
            self.assertIn(field, required, f"Schema missing required field: {field}")

    def test_schema_decision_enum_matches_module(self):
        path = SCHEMAS / "review-decision-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        schema_decisions = set(data["properties"]["decision"]["enum"])
        self.assertEqual(schema_decisions, set(rd.VALID_DECISIONS))

    def test_schema_scope_enum_matches_module(self):
        path = SCHEMAS / "review-decision-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        schema_scopes = set(data["properties"]["scope"]["enum"])
        self.assertEqual(schema_scopes, set(rd.VALID_SCOPES))

    def test_schema_version_is_const_1(self):
        path = SCHEMAS / "review-decision-v1.schema.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["properties"]["schema_version"]["const"], 1)


if __name__ == "__main__":
    unittest.main()
