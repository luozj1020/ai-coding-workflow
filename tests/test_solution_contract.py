import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "solution_contract", ROOT / "scripts" / "solution-contract.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def draft():
    return {
        "schema_version": 1,
        "task_id": "T-plan",
        "goal": "Add a complete feature",
        "end_state": "The feature is integrated and independently testable.",
        "invariants": ["Existing behavior remains compatible."],
        "non_goals": ["Unrelated cleanup"],
        "unknowns": [],
        "acceptance": [{"id": "AC-1", "description": "Feature works."}],
        "slices": [{
            "id": "S-1", "goal": "Implement the vertical slice",
            "write_scope": ["src/feature.py"], "depends_on": [],
            "acceptance_ids": ["AC-1"],
        }],
    }


class SolutionContractTests(unittest.TestCase):
    def test_valid_contract_freezes_after_nonblocking_review(self):
        value = draft()
        self.assertEqual(MODULE.validate_contract(value), [])
        findings = {"findings": [
            {"severity": "recommended", "disposition": "defer", "summary": "Add a benchmark later."},
            {"severity": "spec-change", "disposition": "reject", "summary": "Broaden public API."},
        ]}
        frozen = MODULE.freeze(value, findings)
        self.assertEqual(frozen["state"], "frozen")
        self.assertEqual(frozen["review"]["adversarial_rounds"], 1)
        self.assertEqual(len(frozen["contract_hash"]), 64)
        self.assertEqual(len(frozen["review"]["deferred"]), 2)

    def test_blocking_finding_prevents_freeze(self):
        findings = {"findings": [{
            "severity": "blocking", "disposition": "fix-now",
            "summary": "Slice violates an invariant.",
        }]}
        with self.assertRaises(ValueError):
            MODULE.freeze(draft(), findings)

    def test_unknown_acceptance_reference_is_invalid(self):
        value = draft()
        value["slices"][0]["acceptance_ids"] = ["AC-missing"]
        self.assertTrue(any("unknown acceptance" in item for item in MODULE.validate_contract(value)))

    def test_cli_writes_no_frozen_file_when_review_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "draft.json"
            findings_path = root / "findings.json"
            output = root / "frozen.json"
            contract_path.write_text(json.dumps(draft()), encoding="utf-8")
            findings_path.write_text(json.dumps({"findings": [{
                "severity": "blocking", "disposition": "fix-now", "summary": "No."
            }]}), encoding="utf-8")
            rc = MODULE.main([
                "freeze", str(contract_path), "--findings", str(findings_path),
                "--output", str(output),
            ])
            self.assertEqual(rc, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
