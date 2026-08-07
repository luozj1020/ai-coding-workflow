import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-takeover-receipt.py"
SPEC = importlib.util.spec_from_file_location("build_takeover_receipt", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class TakeoverReceiptTests(unittest.TestCase):
    def _bound_attempts(
        self, root, same_session=True, current_strategy="retry-in-place",
    ):
        source = root / "source"
        worktree = root / "worktree"
        source.mkdir()
        worktree.mkdir()
        card = worktree / "TASK_CARD_FULL.md"
        card.write_text(
            "## Scope\n\n- Write paths: src/a.py, tests/test_a.py\n"
            "- Forbidden paths: deploy/\n",
            encoding="utf-8",
        )
        prior_task_id, current_task_id = "round-1", "round-2"
        prior_session = "session-1"
        current_session = prior_session if same_session else "session-2"
        common = {
            "worktree": str(worktree),
            "source_repository": str(source),
            "source_base_commit": "source-base",
            "execution_base_commit": "execution-base",
            "lineage_root_task_id": "root",
        }
        prior_runtime_value = {
            **common,
            "task_id": prior_task_id,
            "strategy": "fresh",
            "retry_ordinal": 0,
            "claude_session_id": prior_session,
        }
        current_runtime_value = {
            **common,
            "task_id": current_task_id,
            "strategy": current_strategy,
            "retry_ordinal": 1,
            "retry_of": prior_task_id,
            "claude_session_id": current_session,
        }
        prior_runtime = root / "prior.runtime.json"
        current_runtime = root / "current.runtime.json"
        prior_runtime.write_text(json.dumps(prior_runtime_value), encoding="utf-8")
        current_runtime.write_text(json.dumps(current_runtime_value), encoding="utf-8")

        def attempt(task_id, session_id, retry_of):
            return {
                "failure_class": "model-no-progress",
                "counts_toward_takeover": True,
                "attempt_identity": {
                    "schema": "aiwf-attempt-identity-v1",
                    "task_id": task_id,
                    "lineage_root_task_id": "root",
                    "task_card_sha256": MOD._hash(card),
                    "source_base_commit": "source-base",
                    "execution_base_commit": "execution-base",
                    "source_repository": str(source.resolve()),
                    "worktree": str(worktree.resolve()),
                    "claude_session_id": session_id,
                    "retry_of": retry_of,
                },
            }

        prior = attempt(prior_task_id, prior_session, None)
        current = attempt(current_task_id, current_session, prior_task_id)
        prior_path, current_path = root / "prior.json", root / "current.json"
        prior_path.write_text(json.dumps(prior), encoding="utf-8")
        current_path.write_text(json.dumps(current), encoding="utf-8")
        return {
            "card": card,
            "prior": prior,
            "prior_path": prior_path,
            "prior_runtime": prior_runtime,
            "current": current,
            "current_path": current_path,
            "current_runtime": current_runtime,
            "prior_task_id": prior_task_id,
            "current_task_id": current_task_id,
        }

    def _build(self, values):
        return MOD.build(
            values["current"], values["current_path"],
            values["prior"], values["prior_path"], values["card"],
            values["current_runtime"], values["prior_runtime"],
            values["current_task_id"], values["prior_task_id"], "root",
        )

    def test_two_counted_rounds_authorize_only_bound_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = self._bound_attempts(Path(temporary))
            value = self._build(values)

            self.assertEqual(value["schema_version"], 3)
            self.assertEqual(value["status"], "preparation-required")
            self.assertEqual(value["authorization"], "codex-takeover-candidate")
            self.assertEqual(value["allowed_write_paths"], ["src/a.py", "tests/test_a.py"])
            self.assertEqual(value["attempt_lineage"]["relation"], "retry-in-place")
            self.assertEqual(
                value["attempt_lineage"]["binding"]["claude_session_id"], "session-1",
            )
            self.assertTrue(value["takeover_preparation_required"])
            self.assertFalse(value["merge_authorized"])

    def test_external_failure_cannot_authorize_takeover(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = self._bound_attempts(Path(temporary))
            external = {
                "failure_class": "transient-transport",
                "counts_toward_takeover": False,
            }
            with self.assertRaises(ValueError):
                MOD.build(
                    external, values["current_path"], external, values["prior_path"],
                    values["card"], values["current_runtime"], values["prior_runtime"],
                    values["current_task_id"], values["prior_task_id"], "root",
                )

    def test_fresh_session_does_not_inherit_prior_failure_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = self._bound_attempts(Path(temporary), same_session=False)
            with self.assertRaisesRegex(ValueError, "claude_session_id"):
                self._build(values)

    def test_reviewed_or_advisor_continuation_cannot_issue_takeover_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = self._bound_attempts(
                Path(temporary), current_strategy="reviewed-continuation",
            )
            with self.assertRaisesRegex(ValueError, "explicit retry"):
                self._build(values)

    def test_task_card_drift_cannot_reuse_prior_failure_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = self._bound_attempts(Path(temporary))
            values["card"].write_text(
                "## Scope\n\n- Write paths: src/other.py\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "task_card_sha256"):
                self._build(values)


if __name__ == "__main__":
    unittest.main()
