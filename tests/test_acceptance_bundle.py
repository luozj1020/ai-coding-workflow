import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-acceptance-bundle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_acceptance_bundle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AcceptanceBundleTests(unittest.TestCase):
    def test_summarizes_gates_without_authorizing_merge(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "changed.py").write_text("value = 1\n", encoding="utf-8")
            outcome = root / "outcome.json"
            report = root / "report.json"
            scope = root / "scope.json"
            outcome.write_text(json.dumps({
                "task_id": "task-1",
                "dispatch_success": True,
                "artifact_valid": True,
                "validation_success": "verified",
                "semantic_acceptance": "pending-codex-review",
                "completion_state": "semantic-review-required",
            }), encoding="utf-8")
            report.write_text(json.dumps({"status": "consistent"}), encoding="utf-8")
            scope.write_text(json.dumps({"enforcement_passed": True}), encoding="utf-8")
            args = argparse.Namespace(
                worktree=root,
                outcome=outcome,
                report_consistency=report,
                write_scope=scope,
                checker_contract=None,
                recovered_completion=None,
            )

            value = module.build(args)

            self.assertEqual(value["recommended_decision"], "codex-semantic-review")
            self.assertFalse(value["merge_authorized"])
            self.assertEqual(value["authority"], "evidence-summary-only")
            self.assertIn("changed.py", value["changed_paths"])

    def test_environment_crash_routes_to_environment_inspection(self):
        module = load_module()
        outcome = {"dispatch_success": True}
        checker = {
            "enforcement_passed": False,
            "environment_failure_observed": True,
        }
        self.assertEqual(
            module.recommend(outcome, None, None, checker),
            "inspect-validation-environment",
        )


if __name__ == "__main__":
    unittest.main()
