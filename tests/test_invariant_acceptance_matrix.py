from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-invariant-acceptance-matrix.py"
SPEC = importlib.util.spec_from_file_location("invariant_matrix", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


def contract():
    return {
        "task_id": "T-1",
        "invariants": [{
            "id": "INV-1", "description": "Final failure is ineligible",
            "acceptance_ids": ["AC-1"],
        }],
        "acceptance": [{"id": "AC-1", "description": "Registry rejects final failure"}],
        "slices": [{"id": "S-1", "acceptance_ids": ["AC-1"]}],
    }


class InvariantAcceptanceMatrixTests(unittest.TestCase):
    def test_test_evidence_covers_invariant_by_acceptance_id(self):
        value = MOD.build(contract(), {"tests": [{
            "name": "test_final_failure_ineligible",
            "acceptance_ids": ["AC-1"],
            "invariant_ids": [],
            "outcome": "pass",
        }]})
        self.assertTrue(value["test_coverage_complete"])
        self.assertTrue(value["contradiction_free"])

    def test_failing_test_marks_invariant_contradicted(self):
        value = MOD.build(contract(), {"tests": [{
            "name": "test_final_failure_ineligible",
            "acceptance_ids": ["AC-1"],
            "outcome": "fail",
        }]})
        self.assertEqual(value["rows"][0]["coverage_status"], "contradicted")
        self.assertFalse(value["contradiction_free"])

    def test_skipped_test_does_not_count_as_coverage(self):
        value = MOD.build(contract(), {"tests": [{
            "name": "test_final_failure_ineligible",
            "acceptance_ids": ["AC-1"],
            "outcome": "skipped",
        }]})
        self.assertEqual(value["rows"][0]["coverage_status"], "uncovered")
        self.assertFalse(value["test_coverage_complete"])

    def test_invariant_without_slice_fails_mapping(self):
        value = contract()
        value["slices"] = []
        matrix = MOD.build(value)
        self.assertIn("invariant INV-1 has no implementation slice", matrix["errors"])
        self.assertFalse(matrix["all_invariants_mapped"])

    def test_legacy_string_invariant_fails_closed(self):
        value = contract()
        value["invariants"] = ["unmapped"]
        matrix = MOD.build(value)
        self.assertFalse(matrix["all_invariants_mapped"])
        self.assertTrue(matrix["errors"])


if __name__ == "__main__":
    unittest.main()
