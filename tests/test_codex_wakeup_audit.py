import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit-codex-wakeups.py"


def load_script():
    spec = importlib.util.spec_from_file_location("codex_wakeup_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load_script()


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def usage_row(call_id, stage, **overrides):
    value = {
        "schema_version": 1,
        "run_id": "run-1",
        "task_id": "task-1",
        "call_id": call_id,
        "role": "codex",
        "stage": stage,
        "model": "codex-test",
        "input_tokens": 100,
        "output_tokens": 10,
        "reasoning_tokens": 5,
        "wall_time_ms": 1000,
        "result": "success",
        "usage_complete": True,
    }
    value.update(overrides)
    return value


class CodexWakeupAuditTests(unittest.TestCase):
    def _audit(self, rows, *, metrics=None):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ledger = root / "model-usage.jsonl"
            write_jsonl(ledger, rows)
            if metrics is not None:
                (root / "run-metrics.json").write_text(
                    json.dumps(metrics), encoding="utf-8"
                )
            records, quality = audit.load_usage_records([ledger])
            run_metrics, errors = audit.load_run_metrics([ledger])
            episodes, classification_errors = audit.build_episodes(
                records, {}, run_metrics
            )
            summary = audit.build_summary(
                episodes,
                {
                    **quality,
                    "run_metrics_errors": errors,
                    "classification_errors": classification_errors,
                },
                audit.load_broker_summary([]),
            )
            return episodes, summary

    def test_exact_bookend_stages_are_the_only_automatic_classifications(self):
        episodes, summary = self._audit(
            [
                usage_row("freeze", "intent-freeze"),
                usage_row("direction", "diff-review"),
                usage_row("final", "final-review"),
                usage_row("legacy", "review"),
            ]
        )
        by_id = {row["call_id"]: row for row in episodes}
        self.assertEqual(by_id["freeze"]["bookend_counterfactual"], "required_freeze")
        self.assertEqual(
            by_id["final"]["bookend_counterfactual"], "required_final_review"
        )
        self.assertEqual(by_id["direction"]["bookend_counterfactual"], "indeterminate")
        self.assertEqual(by_id["legacy"]["stage"], "unclassified")
        self.assertEqual(
            summary["observed_facts"]["raw_unclassified_stages"], {"review": 1}
        )
        self.assertEqual(
            summary["bookend_counterfactual"]["status"],
            "sufficient-for-conservative-bound",
        )
        self.assertEqual(
            summary["bookend_counterfactual"]["bookend_retained_input_tokens_interval"],
            {"minimum": 200, "maximum": 400},
        )

    def test_missing_tokens_remain_null_and_never_become_savings(self):
        missing = usage_row(
            "missing",
            "diff-review",
            input_tokens=None,
            output_tokens=None,
            usage_complete=False,
        )
        _, summary = self._audit([usage_row("freeze", "intent-freeze"), missing])
        totals = summary["observed_facts"]["totals"]
        self.assertEqual(totals["known_input_tokens"], 100)
        self.assertIsNone(totals["input_tokens"])
        self.assertEqual(totals["input_tokens_missing_calls"], 1)
        counterfactual = summary["bookend_counterfactual"]
        self.assertFalse(counterfactual["input_tokens_complete"])
        self.assertIsNone(counterfactual["safe_savings_ratio"])
        self.assertIsNone(
            counterfactual["bookend_retained_input_tokens_interval"]["minimum"]
        )

    def test_valid_explicit_annotation_can_prove_owner_convergence_saving(self):
        row = usage_row("direction", "diff-review")
        row["audit_v1"] = {
            "proximate_trigger": "builder-complete",
            "root_cause": "policy-mandated-review",
            "policy_required": True,
            "user_triggered": False,
            "semantic_decision_required": False,
            "bookend_counterfactual": "avoidable_by_owner_convergence",
            "classification_confidence": "deterministic",
            "evidence_refs": ["sha256:evidence"],
        }
        episodes, summary = self._audit([row])
        self.assertEqual(
            episodes[0]["bookend_counterfactual"], "avoidable_by_owner_convergence"
        )
        self.assertEqual(
            summary["bookend_counterfactual"]["known_safely_avoidable_input_tokens"],
            100,
        )
        self.assertEqual(summary["bookend_counterfactual"]["safe_savings_ratio"], 1.0)

    def test_invalid_or_contradictory_annotation_falls_back_to_indeterminate(self):
        row = usage_row("direction", "diff-review")
        row["audit_v1"] = {
            "root_cause": "transport-runtime",
            "semantic_decision_required": True,
            "bookend_counterfactual": "required_semantic_escalation",
            "classification_confidence": "deterministic",
            "evidence_refs": ["sha256:evidence"],
        }
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "model-usage.jsonl"
            write_jsonl(ledger, [row])
            wrapped, _ = audit.load_usage_records([ledger])
            episodes, errors = audit.build_episodes(wrapped, {}, {})
        self.assertEqual(episodes[0]["bookend_counterfactual"], "indeterminate")
        self.assertIn(
            "semantic-true-conflicts-with-mechanical-root-cause", errors[0]["errors"]
        )

    def test_identical_duplicates_fold_and_conflicts_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "a" / "model-usage.jsonl"
            second = root / "b" / "model-usage.jsonl"
            row = usage_row("same", "final-review")
            write_jsonl(first, [row])
            write_jsonl(second, [row])
            records, quality = audit.load_usage_records([first, second])
            self.assertEqual(len(records), 1)
            self.assertEqual(quality["duplicate_rows_folded"], 1)

            conflict = dict(row, input_tokens=999)
            write_jsonl(second, [conflict])
            records, _ = audit.load_usage_records([first, second])
            episodes, _ = audit.build_episodes(records, {}, {})
            self.assertEqual(episodes[0]["evidence_quality"], "conflicting")
            self.assertIsNone(episodes[0]["input_tokens"])

    def test_logical_task_metrics_are_null_without_explicit_binding(self):
        metrics = {
            "run_id": "run-1",
            "task_id": "task-1",
            "completed": True,
            "accepted": True,
            "experiment_arm": "full-workflow",
            "actual_owner": "claude-builder",
        }
        _, summary = self._audit([usage_row("final", "final-review")], metrics=metrics)
        tasks = summary["observed_facts"]["tasks"]
        self.assertEqual(tasks["accepted_observed_tasks"], 1)
        self.assertIsNone(tasks["logical_task_count"])
        self.assertEqual(tasks["input_tokens_per_accepted_task"], 100.0)

    def test_reused_run_task_identity_with_different_call_sets_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "experiment-a" / "model-usage.jsonl"
            second = root / "experiment-b" / "model-usage.jsonl"
            write_jsonl(first, [usage_row("call-a", "diff-review")])
            write_jsonl(second, [usage_row("call-b", "diff-review")])
            records, quality = audit.load_usage_records([first, second])
            episodes, errors = audit.build_episodes(records, {}, {})
            summary = audit.build_summary(
                episodes,
                {**quality, "classification_errors": errors},
                audit.load_broker_summary([]),
            )
        tasks = summary["observed_facts"]["tasks"]
        self.assertEqual(tasks["known_distinct_run_task_keys"], 1)
        self.assertEqual(tasks["observed_run_task_identity_collision_count"], 1)
        self.assertIsNone(tasks["observed_run_task_count"])
        self.assertIsNone(tasks["calls_per_observed_run_task"])

    def test_broker_diagnostics_are_control_activity_not_inference_usage(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "run-ledger.jsonl"
            write_jsonl(
                ledger,
                [
                    {
                        "role": "codex",
                        "state": "diagnostic",
                        "task_id": "t",
                        "stage": "health",
                    },
                    {"role": "codex", "state": "reserved", "reservation_id": "r1"},
                    {"role": "codex", "state": "running", "reservation_id": "r1"},
                    {
                        "role": "codex",
                        "state": "succeeded",
                        "reservation_id": "r1",
                        "run_id": "run",
                        "task_id": "task",
                        "stage": "final-review",
                        "call_type": "execution_call",
                        "request_id": "request",
                        "input_hash": "sha256:input",
                        "evidence_hash": "sha256:evidence",
                    },
                ],
            )
            result = audit.load_broker_summary([ledger])
        self.assertEqual(result["non_inference_control_activity"], 1)
        self.assertEqual(result["codex_reservations"], 1)
        self.assertEqual(result["reservation_terminal_states"], {"succeeded": 1})
        self.assertEqual(result["reservations_by_stage"], {"final-review": 1})
        self.assertEqual(result["repeated_request_groups"], 0)

    def test_event_v2_annotation_must_be_uniquely_call_bound(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            usage = root / "model-usage.jsonl"
            events = root / "loop-events.jsonl"
            write_jsonl(usage, [usage_row("direction", "diff-review")])
            write_jsonl(
                events,
                [
                    {
                        "schema_version": 2,
                        "run_id": "run-1",
                        "task_id": "task-1",
                        "detail": {
                            "audit_v1": {
                                "call_id": "direction",
                                "bookend_counterfactual": "avoidable_by_review_reuse",
                                "classification_confidence": "deterministic",
                                "evidence_refs": ["sha256:review"],
                            }
                        },
                    }
                ],
            )
            records, _ = audit.load_usage_records([usage])
            annotations, quality = audit.load_event_annotations([events])
            episodes, errors = audit.build_episodes(records, annotations, {})
        self.assertEqual(quality["call_bound_audit_annotations"], 1)
        self.assertFalse(errors)
        self.assertEqual(
            episodes[0]["bookend_counterfactual"], "avoidable_by_review_reuse"
        )

    def test_cli_writes_summary_and_episode_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_jsonl(
                root / "model-usage.jsonl", [usage_row("final", "final-review")]
            )
            summary_path = root / "summary.json"
            episodes_path = root / "episodes.jsonl"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--output",
                    str(summary_path),
                    "--episodes-output",
                    str(episodes_path),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["protocol"], "codex-wakeup-audit-v1")
            self.assertEqual(summary["observed_facts"]["totals"]["calls"], 1)
            self.assertEqual(
                len(episodes_path.read_text(encoding="utf-8").splitlines()), 1
            )


if __name__ == "__main__":
    unittest.main()
