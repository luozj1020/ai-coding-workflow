import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-acceptance-bundle.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))


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
                "operator_state": "terminal-awaiting-review",
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
            self.assertEqual(value["operator_state"], "terminal-awaiting-review")
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

    def test_review_evidence_exposes_diff_files_and_validation_exit_codes(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "new.py").write_text("value = 1\n", encoding="utf-8")
            outcome = root / "outcome.json"
            validation = root / "validation.json"
            report_artifact = root / "report-artifact.json"
            handoff = root / "handoff.json"
            recovered = root / "recovered.json"
            outcome.write_text(json.dumps({
                "task_id": "task-evidence", "dispatch_success": True,
            }), encoding="utf-8")
            validation.write_text(json.dumps({
                "status": "passed",
                "results": [
                    {"index": 1, "label": "ruff", "command": "ruff check new.py", "exit_code": 0},
                    {"index": 2, "label": "pytest", "command": "pytest -q", "exit_code": 1},
                ],
            }), encoding="utf-8")
            report_artifact.write_text(json.dumps({
                "valid": False,
                "reasons": ["missing-headings:Checks Run"],
                "normalization_applied": True,
            }), encoding="utf-8")
            handoff.write_text(json.dumps({
                "status": "ready",
                "deliverable": True,
                "product_change_count": 1,
                "control_change_count": 2,
                "control_changed_paths": ["TASK_CARD.md", "CLAUDE_PROMPT.md"],
                "out_of_scope_product_paths": [],
                "source_base_commit": "source-base",
                "execution_base_commit": "execution-base",
                "changed_files": [{"path": "new.py", "change": "added"}],
                "patch": {"sha256": "sha256:patch", "bytes": 321},
            }), encoding="utf-8")
            recovered.write_text(json.dumps({
                "diff_sha256": "sha256:recovered",
                "claude_report_complete": False,
            }), encoding="utf-8")
            args = argparse.Namespace(
                worktree=root, outcome=outcome, report_consistency=None,
                report_artifact_validation=report_artifact,
                write_scope=None, checker_contract=None,
                validation_receipt=validation, scoped_handoff=handoff,
                recovered_completion=recovered,
            )
            value = module.build(args)
            evidence = value["review_evidence"]
            self.assertEqual(evidence["diff_sha256"], "sha256:recovered")
            self.assertEqual(evidence["changed_files"][0]["path"], "new.py")
            self.assertFalse(evidence["claude_report_available"])
            self.assertEqual(evidence["validation_command_count"], 2)
            self.assertEqual(evidence["validation_results"][1]["exit_code"], 1)
            self.assertEqual(evidence["execution_base_commit"], "execution-base")
            self.assertEqual(evidence["patch_bytes"], 321)
            self.assertTrue(evidence["deliverable"])
            self.assertEqual(
                evidence["claude_report_invalid_reasons"],
                ["missing-headings:Checks Run"],
            )
            self.assertTrue(evidence["claude_report_normalized"])

    def test_blocked_scoped_handoff_routes_to_scope_revision(self):
        module = load_module()
        self.assertEqual(
            module.recommend(
                {"dispatch_success": True}, None, None, None, None,
                {"status": "blocked"},
            ),
            "revise-scope",
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

    def test_compact_stdout_capsule_references_full_bundle_without_evidence_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "changed.py").write_text("value = 1\n", encoding="utf-8")
            outcome = root / "outcome.json"
            output = root / "acceptance-bundle.json"
            outcome.write_text(json.dumps({
                "task_id": "task-capsule",
                "dispatch_success": True,
                "artifact_valid": True,
                "validation_success": True,
                "semantic_acceptance": "pending-codex-review",
                "operator_state": "implementation-stable-awaiting-review",
            }), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--worktree", str(root),
                    "--outcome", str(outcome),
                    "--output", str(output),
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )

            capsule = json.loads(result.stdout)
            full = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(capsule["kind"], "aiwf-acceptance-capsule")
            self.assertEqual(capsule["output_path"], str(output.resolve()))
            self.assertEqual(
                capsule["operator_state"],
                "implementation-stable-awaiting-review",
            )
            self.assertEqual(capsule["changed_path_count"], len(full["changed_paths"]))
            self.assertNotIn("changed_paths", capsule)
            self.assertNotIn("acceptance_index", capsule)
            self.assertIn("changed.py", full["changed_paths"])
            self.assertLessEqual(len(result.stdout.encode("utf-8")), 2048)

    def test_complex_evidence_recommends_optional_spark_compression(self):
        module = load_module()
        from evidence_capsule import acceptance_compression_route

        value = {
            "changed_paths": ["src/file-{}.py".format(index) for index in range(8)],
            "acceptance_index": [],
            "review_selection": {"expanded_acceptance_ids": []},
            "unresolved_risks": [],
            "frozen_invariants": [],
            "changed_symbols": [],
        }

        route = acceptance_compression_route(value, full_bytes=20_000)

        self.assertTrue(route["spark_recommended"])
        self.assertEqual(route["spark_mode"], "postflight-bundle")
        self.assertIn("many-changed-paths", route["reason_codes"])
        self.assertTrue(route["advisory_only"])
        self.assertFalse(route["spark_can_authorize_acceptance"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            task_card = root / "task-card.md"
            output = root / "acceptance-bundle.json"
            task_card.write_text("# Task\n", encoding="utf-8")
            output.write_text("{}\n", encoding="utf-8")
            value["compression_route"] = route
            capsule = module.compact_capsule(value, output, task_card, root)
            request = capsule["compression_route"]["tool_request"]
            self.assertEqual(request["argv"][:2], ["bash", "ai/run-codex-spark.sh"])
            self.assertIn("postflight-bundle", request["argv"])
            self.assertIn(str(output.resolve()), request["argv"])
            self.assertEqual(request["result_contract"], "advisory-summary-only")
            self.assertEqual(request["input_artifact"]["sha256"], capsule["evidence"]["sha256"])

    def test_stdout_off_keeps_full_bundle_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            outcome = root / "outcome.json"
            output = root / "acceptance-bundle.json"
            outcome.write_text(json.dumps({"task_id": "task-off"}), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--worktree", str(root),
                    "--outcome", str(outcome),
                    "--output", str(output),
                    "--stdout-mode", "off",
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )
            self.assertEqual(result.stdout, "")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
