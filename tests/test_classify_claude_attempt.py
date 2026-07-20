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

    def test_timeout_outcome_without_text_is_transport(self):
        result = classify(outcome="timeout")
        self.assertEqual(result["failure_class"], "transient-transport")
        self.assertFalse(result["counts_toward_takeover"])

    def test_acknowledgement_only_counts(self):
        result = classify(exit_code=0, outcome="success", progress="acknowledgement")
        self.assertEqual(result["failure_class"], "acknowledgement-only")
        self.assertTrue(result["counts_toward_takeover"])

    def test_useful_diff_survives_transport_failure(self):
        result = classify(diff_changes=1, error_text="connection reset")
        self.assertEqual(result["failure_class"], "recoverable-evidence")
        self.assertEqual(result["recommended_action"], "review-existing-evidence")

    def test_direction_deviation_wins(self):
        result = classify(diff_changes=2, direction="off-plan")
        self.assertEqual(result["failure_class"], "direction-deviation")
        self.assertTrue(result["counts_toward_takeover"])

    def test_approval_blocker_does_not_count(self):
        result = classify(error_text="command requires permission approval")
        self.assertEqual(result["failure_class"], "external-approval-blocker")
        self.assertFalse(result["counts_toward_takeover"])

    def test_clean_exit_without_progress_counts(self):
        result = classify(exit_code=0, outcome="success")
        self.assertEqual(result["failure_class"], "model-no-progress")
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
