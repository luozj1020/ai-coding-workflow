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

    def test_acceptance_index_expands_only_delta_and_semantic_risk(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            graph = root / "graph.json"
            delta = root / "delta.json"
            matrix = root / "matrix.json"
            symbols = root / "symbols.json"
            graph.write_text(json.dumps({"acceptance_items": [
                {"id": "AC-1", "graph_status": "supported", "evidence_paths": ["src/a.py"],
                 "implementation_refs": ["d1"], "test_refs": ["t1"], "result_refs": ["r1"],
                 "unverified_claims": []},
                {"id": "AC-2", "graph_status": "reopened", "evidence_paths": ["src/b.py"],
                 "implementation_refs": ["d2"], "test_refs": [], "result_refs": [],
                 "unverified_claims": ["no-deterministic-or-semantic-support"]},
            ]}), encoding="utf-8")
            delta.write_text(json.dumps({
                "acceptance_items": [{"id": "AC-2"}],
                "omitted_unchanged_accepted": ["AC-1"],
            }), encoding="utf-8")
            matrix.write_text(json.dumps({"rows": [
                {"invariant_id": "INV-1", "acceptance_ids": ["AC-1"], "coverage_status": "covered"},
                {"invariant_id": "INV-2", "acceptance_ids": ["AC-2"], "coverage_status": "uncovered"},
            ], "errors": []}), encoding="utf-8")
            symbols.write_text(json.dumps({"changed_symbols": ["Registry.add"]}), encoding="utf-8")
            args = argparse.Namespace(
                worktree=root, outcome=root / "missing-outcome.json",
                report_consistency=None, write_scope=None, checker_contract=None,
                recovered_completion=None, acceptance_graph=graph,
                delta_review_packet=delta, invariant_matrix=matrix,
                symbol_summary=symbols, task_card=None,
            )
            value = module.build(args)
            self.assertEqual(value["review_selection"]["expanded_acceptance_ids"], ["AC-2"])
            self.assertEqual(value["review_selection"]["omitted_unchanged_accepted"], ["AC-1"])
            self.assertTrue(value["review_selection"]["deep_codex_review_required"])
            self.assertEqual(value["changed_symbols"], ["Registry.add"])
            self.assertIn("invariant:INV-2:uncovered", value["unresolved_risks"])

    def test_closed_acceptance_index_skips_checker_and_deep_review(self):
        module = load_module()
        graph = {"acceptance_items": [{
            "id": "AC-1", "graph_status": "supported", "evidence_paths": ["src/a.py"],
            "implementation_refs": ["d1"], "test_refs": ["t1"], "result_refs": ["r1"],
            "unverified_claims": [],
        }]}
        index, selection, risks = module._acceptance_index(
            graph, {"acceptance_items": [], "omitted_unchanged_accepted": ["AC-1"]}, None,
        )
        self.assertEqual(len(index), 1)
        self.assertEqual(selection["expanded_acceptance_ids"], [])
        self.assertFalse(selection["deep_codex_review_required"])
        self.assertEqual(risks, [])


if __name__ == "__main__":
    unittest.main()
