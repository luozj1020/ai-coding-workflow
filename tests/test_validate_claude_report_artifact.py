import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-claude-report.py"
SPEC = importlib.util.spec_from_file_location("validate_claude_report_artifact", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def report(body: str = "Done.") -> str:
    return (
        "# Claude Modification Report\n\n"
        "## Requirements Summary\nDone.\n\n"
        "## Files Changed\n- src/a.py\n\n"
        "## Acceptance Criteria Mapping\n- AC-1 met\n\n"
        "## Out-of-Scope Confirmation\nNone.\n\n"
        "## Plan Match\nfull\n\n"
        f"## Checks Run\n{body}\n"
    )


class ValidateClaudeReportArtifactTests(unittest.TestCase):
    def validate(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CLAUDE_REPORT.md"
            path.write_text(text, encoding="utf-8")
            return MODULE.validate(path)

    def test_accepts_standard_report(self):
        self.assertTrue(self.validate(report())["valid"])

    def test_accepts_safe_report_aliases_and_records_normalization(self):
        value = self.validate(
            "# Implementation Report\n\n"
            "## Requirements Covered\nDone.\n\n"
            "## Changed Files\n- src/a.py\n\n"
            "## Acceptance Mapping\n- AC-1 met\n\n"
            "## Scope Compliance\nNo out-of-scope work.\n\n"
            "## Plan Alignment\nfull\n\n"
            "## Validation Evidence\npytest passed\n"
        )

        self.assertTrue(value["valid"])
        self.assertTrue(value["normalization_applied"])
        self.assertEqual(
            value["normalized_headings"]["Files Changed"], "Changed Files"
        )

    def test_rejects_seeded_progress_role_swap(self):
        value = self.validate(report("AI-CODING-WORKFLOW:DISPATCH-SEEDED-PROGRESS"))
        self.assertFalse(value["valid"])
        self.assertIn("seeded-fallback-or-progress-marker", value["reasons"])

    def test_rejects_missing_schema_sections(self):
        value = self.validate("# Claude Report\n\nDone.\n")
        self.assertFalse(value["valid"])
        self.assertTrue(any(reason.startswith("missing-headings:") for reason in value["reasons"]))

    def test_rejects_progress_artifact_in_report_role(self):
        value = self.validate(report() + "\nCurrent Phase: editing\nImplementation Complete: no\nCompletion Ready: no\n")
        self.assertFalse(value["valid"])
        self.assertIn("progress-report-role-mismatch", value["reasons"])

    def test_rejects_large_source_body(self):
        source = "```python\n" + "\n".join(f"def function_{i}(): pass" for i in range(130)) + "\n```"
        value = self.validate(report(source))
        self.assertFalse(value["valid"])
        self.assertIn("source-body-dominates-report", value["reasons"])

    def test_output_receipt_preserves_exact_invalid_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "CLAUDE_REPORT.md"
            output = root / "validation.json"
            path.write_text("# Claude Report\n\nDone.\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path), "--output", str(output)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(value["valid"])
            self.assertIn("Requirements Summary", value["missing_headings"])


if __name__ == "__main__":
    unittest.main()
