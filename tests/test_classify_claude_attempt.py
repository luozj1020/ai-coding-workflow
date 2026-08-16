import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "classify-claude-attempt.py"
spec = importlib.util.spec_from_file_location("classify_claude_attempt", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def classify(**overrides):
    values = dict(exit_code=1, outcome="failure", semantic_error=False, diff_changes=0,
                  valid_report=False, progress="none", direction="unknown", error_text="",
                  blocker_kind="none", advisor_used=False, delegation_mode="unknown")
    values.update(overrides)
    return mod.classify(**values)


class ClassifyClaudeAttemptTests(unittest.TestCase):
    def test_transport_before_interaction_does_not_count(self):
        result = classify(error_text="API Error: TLS connection timed out")
        self.assertEqual(result["failure_class"], "transient-transport")
        self.assertFalse(result["counts_toward_takeover"])
        self.assertTrue(result["same_worktree_retry_eligible"])

    def test_transport_retry_budget_is_not_recommended_twice(self):
        result = classify(
            error_text="Unable to connect to API (FailedToOpenSocket)",
            retry_ordinal=1,
        )
        self.assertEqual(result["failure_class"], "transient-transport")
        self.assertEqual(result["recommended_action"], "fallback-local-or-reroute")
        self.assertFalse(result["same_worktree_retry_eligible"])
        self.assertEqual(result["retry_budget_remaining"], 0)

    def test_workspace_trust_is_external_blocker(self):
        result = classify(error_text="this workspace has not been trusted")
        self.assertEqual(result["failure_class"], "external-approval-blocker")

    def test_timeout_outcome_without_text_is_transport(self):
        result = classify(outcome="timeout")
        self.assertEqual(result["failure_class"], "transient-transport")
        self.assertFalse(result["counts_toward_takeover"])

    def test_execution_timeout_after_successful_preflight_counts(self):
        result = classify(outcome="execution_timeout", error_text="first progress timeout")
        self.assertEqual(result["failure_class"], "model-no-progress")
        self.assertTrue(result["counts_toward_takeover"])

    def test_runtime_evidence_error_never_counts(self):
        result = classify(exit_code=0, outcome="runtime_evidence_error")
        self.assertEqual(result["failure_class"], "control-plane-evidence-error")
        self.assertFalse(result["counts_toward_takeover"])
        self.assertEqual(result["recommended_action"], "repair-runtime-before-retry")

    def test_acknowledgement_only_counts(self):
        result = classify(exit_code=0, outcome="success", progress="acknowledgement")
        self.assertEqual(result["failure_class"], "acknowledgement-only")
        self.assertTrue(result["counts_toward_takeover"])

    def test_useful_diff_survives_transport_failure(self):
        result = classify(diff_changes=1, error_text="connection reset")
        self.assertEqual(result["failure_class"], "recoverable-evidence")
        self.assertEqual(result["recommended_action"], "review-existing-evidence")

    def test_tail_evidence_gap_never_requests_implementation_retry(self):
        result = classify(outcome="evidence_tail_incomplete", diff_changes=1)
        self.assertEqual(result["failure_class"], "recoverable-evidence")
        self.assertEqual(result["recommended_action"], "review-existing-evidence")
        self.assertFalse(result["counts_toward_takeover"])

    def test_direction_deviation_wins(self):
        result = classify(diff_changes=2, direction="off-plan")
        self.assertEqual(result["failure_class"], "direction-deviation")
        self.assertTrue(result["counts_toward_takeover"])

    def test_approval_blocker_does_not_count(self):
        result = classify(error_text="command requires permission approval")
        self.assertEqual(result["failure_class"], "external-approval-blocker")
        self.assertFalse(result["counts_toward_takeover"])

    def test_shell_expansion_policy_blocker_does_not_count(self):
        result = classify(
            outcome="execution_timeout",
            error_text="Bash rejected: Contains simple_expansion",
        )
        self.assertEqual(result["failure_class"], "external-approval-blocker")
        self.assertFalse(result["counts_toward_takeover"])

    def test_clean_exit_without_progress_counts(self):
        result = classify(exit_code=0, outcome="success")
        self.assertEqual(result["failure_class"], "model-no-progress")
        self.assertTrue(result["counts_toward_takeover"])

    def test_builder_report_without_product_delta_is_no_progress(self):
        result = classify(
            exit_code=0, outcome="success", valid_report=True,
            progress="none", task_mode="builder", report_consistency="matched",
        )
        self.assertEqual(result["failure_class"], "model-no-progress")
        self.assertTrue(result["counts_toward_takeover"])

    def test_report_role_mismatch_without_diff_counts(self):
        result = classify(
            outcome="execution_timeout", valid_report=True, task_mode="builder",
            report_consistency="role-mismatch",
        )
        self.assertEqual(result["failure_class"], "report-evidence-mismatch")
        self.assertTrue(result["counts_toward_takeover"])

    def test_canary_model_failure_requires_reroute_without_takeover(self):
        result = classify(exit_code=0, outcome="success", delegation_mode="canary")
        self.assertTrue(result["economic_stop_loss"])
        self.assertTrue(result["reroute_required"])
        self.assertFalse(result["takeover_authorized"])
        self.assertEqual(result["recommended_action"], "reroute-before-redispatch")

    def test_canary_transport_keeps_same_worktree_retry(self):
        result = classify(error_text="API Error: connection timed out", delegation_mode="canary")
        self.assertFalse(result["economic_stop_loss"])
        self.assertTrue(result["same_worktree_retry_eligible"])

    def test_attempt_identity_is_preserved_for_lineage_binding(self):
        identity = {
            "schema": "aiwf-attempt-identity-v1",
            "task_id": "round-2",
            "lineage_root_task_id": "round-1",
            "task_card_sha256": "sha256:" + "a" * 64,
            "source_base_commit": "base",
            "execution_base_commit": "execution",
            "source_repository": "/repo",
            "worktree": "/repo/.worktrees/round-1",
            "claude_session_id": "session-1",
            "retry_of": "round-1",
        }
        result = classify(exit_code=0, outcome="success", attempt_identity=identity)
        self.assertEqual(result["attempt_identity"], identity)

    # --- advisor continuation eligibility ---

    def test_useful_onplan_semantic_eligible(self):
        result = classify(diff_changes=1, direction="on-plan", blocker_kind="semantic")
        self.assertTrue(result["advisor_continuation_eligible"])
        self.assertIsNone(result["advisor_rejection_reason"])

    def test_zero_progress_rejected(self):
        result = classify(exit_code=0, outcome="success", direction="on-plan", blocker_kind="semantic")
        self.assertFalse(result["advisor_continuation_eligible"])
        self.assertEqual(result["advisor_rejection_reason"], "no-useful-evidence")

    def test_transport_failure_rejected(self):
        result = classify(diff_changes=1, direction="on-plan", blocker_kind="semantic",
                          error_text="API Error: connection timed out")
        self.assertFalse(result["advisor_continuation_eligible"])
        self.assertEqual(result["advisor_rejection_reason"], "transport-failure")

    def test_approval_failure_rejected(self):
        result = classify(diff_changes=1, direction="on-plan", blocker_kind="semantic",
                          error_text="command requires permission approval")
        self.assertFalse(result["advisor_continuation_eligible"])
        self.assertEqual(result["advisor_rejection_reason"], "approval-blocked")

    def test_offplan_rejected(self):
        result = classify(diff_changes=1, direction="off-plan", blocker_kind="semantic")
        self.assertFalse(result["advisor_continuation_eligible"])
        self.assertEqual(result["advisor_rejection_reason"], "direction-not-on-plan")

    def test_advisor_already_used_rejected(self):
        result = classify(diff_changes=1, direction="on-plan", blocker_kind="semantic", advisor_used=True)
        self.assertFalse(result["advisor_continuation_eligible"])
        self.assertEqual(result["advisor_rejection_reason"], "advisor-already-used")

    def test_non_semantic_blocker_rejected(self):
        result = classify(diff_changes=1, direction="on-plan", blocker_kind="transport")
        self.assertFalse(result["advisor_continuation_eligible"])
        self.assertEqual(result["advisor_rejection_reason"], "blocker-not-semantic")


if __name__ == "__main__":
    unittest.main()
