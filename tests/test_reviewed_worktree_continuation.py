"""Tests for reviewed dirty-worktree continuation approval and enforcement."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "prepare-worktree-continuation.py"


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=check,
    )


class ReviewedContinuationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        run("git", "init", "-q", cwd=self.repo)
        run("git", "config", "user.email", "test@example.com", cwd=self.repo)
        run("git", "config", "user.name", "Test", cwd=self.repo)
        (self.repo / "scripts").mkdir()
        shutil.copy2(HELPER, self.repo / "scripts" / HELPER.name)
        shutil.copy2(ROOT / "scripts" / "process-identity.py", self.repo / "scripts")
        shutil.copy2(ROOT / "scripts" / "worktree_state_hash.py", self.repo / "scripts")
        (self.repo / "src.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "test.txt").write_text("base test\n", encoding="utf-8")
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-qm", "base", cwd=self.repo)
        self.head = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.worktree = self.repo / ".worktrees" / "task-worktree"
        self.worktree.parent.mkdir()
        run("git", "worktree", "add", "-q", "-b", "task-branch", str(self.worktree), self.head, cwd=self.repo)
        self.task_id = "claude-task"
        self.prior_card = self.worktree / "TASK_CARD_FULL.md"
        self.prior_card.write_text("| Mode | builder |\n", encoding="utf-8")
        runtime = {
            "schema_version": 1,
            "task_id": self.task_id,
            "strategy": "fresh",
            "task_mode": "builder",
            "worktree": str(self.worktree),
            "source_repository": str(self.repo),
            "base_commit": self.head,
            "claude_session_id": "5ef9e3c8-bdbc-4d1e-8c64-c8bd0f0e4c66",
            "builder_mode": "execution-only",
            "tool_profile": "locator-builder",
            "context_lease_id": "lease-prior-1",
            "pid_files": {},
        }
        (self.repo / ".worktrees" / f"{self.task_id}.runtime.json").write_text(
            json.dumps(runtime), encoding="utf-8"
        )
        (self.worktree / "src.txt").write_text("accepted implementation\n", encoding="utf-8")
        self.card = self.repo / "next-card.md"
        self.card.write_text("| Mode | builder |\n", encoding="utf-8")
        self.approval = self.repo / ".worktrees" / "approval.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def helper(self, command: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable, str(self.repo / "scripts" / HELPER.name), command,
            *args, cwd=self.repo, check=check,
        )

    def prepare(
        self, *, next_role: str = "builder", accepted: str = "src.txt",
        allow: str = "src.txt",
    ) -> dict:
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", next_role,
            "--decision", "accepted-direction",
            "--accepted-existing-path", accepted,
            "--allow-new-write-path", allow,
            "--output", str(self.approval),
        )
        return json.loads(result.stdout)

    def set_current_process_receipt(
        self, role: str = "dispatcher", *, mutate: str | None = None,
    ) -> None:
        pid_file = self.repo / ".worktrees" / f"{role}.pid"
        identity_file = self.repo / ".worktrees" / f"{role}.process.json"
        pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
        captured = run(
            sys.executable, str(self.repo / "scripts" / "process-identity.py"),
            "capture", "--pid", str(os.getpid()), "--task-id", self.task_id,
            "--role", role, "--output", str(identity_file), cwd=self.repo,
        )
        self.assertEqual(captured.returncode, 0)
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
        if mutate == "start-time":
            identity["start_time_ticks"] = int(identity["start_time_ticks"]) + 1
        elif mutate == "task-id":
            identity["task_id"] = "different-task"
        identity_file.write_text(json.dumps(identity), encoding="utf-8")

        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["pid_files"] = {role: str(pid_file)}
        runtime["process_identity_files"] = {role: str(identity_file)}
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

    def test_reused_pid_identity_does_not_block_continuation(self) -> None:
        self.set_current_process_receipt(mutate="start-time")
        self.assertEqual(self.prepare()["status"], "available")

    def test_matching_live_process_identity_blocks_continuation(self) -> None:
        self.set_current_process_receipt()
        rejected = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("still live", rejected.stderr)

    def test_mismatched_identity_fails_closed(self) -> None:
        self.set_current_process_receipt(mutate="task-id")
        rejected = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("not trustworthy", rejected.stderr)

    def test_generic_pid_alias_is_not_rechecked_without_identity(self) -> None:
        self.set_current_process_receipt(role="claude", mutate="start-time")
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["pid_files"]["pid"] = runtime["pid_files"]["claude"]
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        self.assertEqual(self.prepare()["status"], "available")

    def test_prepare_and_validate_bind_exact_state_and_card(self) -> None:
        approval = self.prepare()
        self.assertEqual(approval["prior_strategy"], "fresh")
        self.assertEqual(approval["accepted_existing_paths"], ["src.txt"])
        self.assertEqual(approval["allow_new_write_paths"], ["src.txt"])
        self.assertIn("sha256", approval["accepted_path_state"]["src.txt"])
        self.assertEqual(approval["inherited_builder_mode"], "execution-only")
        self.assertEqual(approval["inherited_tool_profile"], "locator-builder")
        self.assertEqual(approval["prior_context_lease_id"], "lease-prior-1")
        self.assertEqual(
            approval["context_reuse"]["strategy"],
            "same-session-plus-delta-capsule",
        )
        self.assertEqual(
            approval["context_reuse"]["accepted_path_summaries_reused"], 1
        )
        self.assertFalse(
            approval["context_reuse"]["full_prior_task_card_repeated"]
        )
        self.assertTrue(
            Path(approval["authorization_path"]).samefile(self.approval)
        )
        self.assertIn("--reviewed-continuation", approval["dispatch_argv"])
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(json.loads(validated.stdout)["approval_id"], approval["approval_id"])

        self.card.write_text("| Mode | builder |\nchanged\n", encoding="utf-8")
        rejected = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("next_task_card_sha256", rejected.stderr)

    def test_json_task_and_execution_metadata_are_native_continuation_inputs(self) -> None:
        self.card = self.repo / "next-task.json"
        self.card.write_text(json.dumps({
            "schema_version": 1,
            "mode": "builder",
            "extensions": {
                "routing_hints": {"claude_role": "execution-builder"}
            },
        }), encoding="utf-8")
        approval = self.prepare()
        self.assertEqual(approval["next_declared_mode"], "builder")
        self.assertIsNone(approval["next_builder_mode"])
        self.assertEqual(approval["inherited_builder_mode"], "execution-only")

        self.card = self.repo / "next-execution-card.md"
        self.card.write_text(
            "<!-- aiwf-execution-card-v1; task-mode=builder; "
            "builder-mode=execution-only -->\n# Task\n",
            encoding="utf-8",
        )
        approval = self.prepare()
        self.assertEqual(approval["next_role"], "builder")
        self.assertEqual(approval["inherited_builder_mode"], "execution-only")

    def test_default_authorization_is_control_only_and_command_is_copyable(self) -> None:
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card),
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
        )
        approval = json.loads(result.stdout)
        authorization = Path(approval["authorization_path"])
        self.assertTrue(authorization.is_file())
        self.assertTrue(
            authorization.resolve().is_relative_to(
                (self.repo / ".worktrees").resolve()
            )
        )
        self.assertFalse(
            authorization.resolve().is_relative_to(self.worktree.resolve())
        )
        self.assertIn(str(authorization), approval["dispatch_command"])

    def test_authorization_inside_product_worktree_is_rejected(self) -> None:
        rejected = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card),
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.worktree / "approval.json"),
            check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("outside the product worktree", rejected.stderr)
        self.assertFalse((self.worktree / "approval.json").exists())

    def test_revision_mode_is_native_builder_continuation(self) -> None:
        self.card.write_text("| Mode | revision |\n", encoding="utf-8")
        approval = self.prepare(next_role="builder")
        self.assertEqual(approval["next_role"], "builder")
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(validated.returncode, 0)

    def test_prepare_binds_delta_review_findings_and_new_validation_refs(self) -> None:
        packet = {
            "schema_version": 1, "packet_id": "", "mode": "revision",
            "state_id": "sha256:" + "1" * 64,
            "graph_id": "sha256:" + "2" * 64,
            "acceptance_items": [{"id": "AC-2"}],
            "unsupported_acceptance": ["AC-2"],
            "contradictory_evidence": [], "reopened_acceptance": [],
            "changed_decisions": [],
            "new_diff_refs": ["sha256:" + "3" * 64],
            "new_test_refs": ["sha256:" + "4" * 64],
            "omitted_unchanged_accepted": ["AC-1"],
        }
        material = dict(packet)
        material.pop("packet_id")
        packet["packet_id"] = "sha256:" + hashlib.sha256(json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        packet_path = self.repo / "delta-review.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--delta-review-packet", str(packet_path),
            "--unresolved-finding", "AC-2 lacks deterministic evidence",
            "--new-validation-ref", "sha256:" + "4" * 64,
            "--output", str(self.approval),
        )
        delta = json.loads(result.stdout)["delta_continuation"]
        self.assertEqual(delta["delta_review_packet"]["acceptance_ids"], ["AC-2"])
        self.assertEqual(delta["unresolved_findings"], ["AC-2 lacks deterministic evidence"])
        self.assertEqual(delta["new_validation_refs"], ["sha256:" + "4" * 64])
        self.assertFalse(delta["full_prior_task_card_repeated"])

    def test_reviewed_continuation_can_be_rebound_from_latest_hash(self) -> None:
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime.update({
            "strategy": "reviewed-continuation",
            "provenance_root_strategy": "fresh",
            "reuse_count": 1,
        })
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

        approval = self.prepare()

        self.assertEqual(approval["prior_strategy"], "reviewed-continuation")
        self.assertEqual(approval["provenance_root_strategy"], "fresh")
        self.assertEqual(
            approval["worktree_state_hash"],
            json.loads(self.approval.read_text(encoding="utf-8"))["worktree_state_hash"],
        )

    def test_revision_runtime_can_transition_to_checker(self) -> None:
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["task_mode"] = "revision"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        self.prior_card.write_text("| Mode | revision |\n", encoding="utf-8")
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")

        approval = self.prepare(next_role="checker-test", allow="test.txt")

        self.assertEqual(approval["prior_declared_mode"], "revision")
        self.assertEqual(approval["prior_role"], "builder")
        self.assertEqual(approval["next_role"], "checker-test")
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(validated.returncode, 0)

    def test_checker_can_resume_same_session_for_narrow_checker_revision(self) -> None:
        run("git", "checkout", "--", "src.txt", cwd=self.worktree)
        (self.worktree / "test.txt").write_text(
            "accepted checker tests\n", encoding="utf-8"
        )
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["task_mode"] = "checker-test"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        self.prior_card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")

        approval = self.prepare(
            next_role="checker-test", accepted="test.txt", allow="test.txt"
        )

        self.assertEqual(approval["prior_role"], "checker-test")
        self.assertEqual(
            approval["prior_claude_session_id"],
            "5ef9e3c8-bdbc-4d1e-8c64-c8bd0f0e4c66",
        )
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(validated.returncode, 0)

        runtime["claude_session_id"] = "6a176739-6bdb-43fe-bf7a-a2b038b511aa"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        rejected = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("prior_claude_session_id", rejected.stderr)

    def test_checker_continuation_cannot_switch_back_to_builder(self) -> None:
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["task_mode"] = "checker-test"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        self.prior_card.write_text("| Mode | checker-test |\n", encoding="utf-8")

        rejected = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("only start checker-test", rejected.stderr)

    def test_dirty_snapshot_continuation_binds_source_and_execution_bases(self) -> None:
        run("git", "add", "src.txt", cwd=self.worktree)
        run("git", "commit", "-qm", "synthetic dirty snapshot", cwd=self.worktree)
        snapshot_commit = run("git", "rev-parse", "HEAD", cwd=self.worktree).stdout.strip()
        (self.worktree / "src.txt").write_text(
            "accepted implementation after snapshot\n", encoding="utf-8"
        )
        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime.update({
            "source_base_commit": self.head,
            "execution_base_commit": snapshot_commit,
            "worktree_start_commit": snapshot_commit,
            "dirty_snapshot_commit": snapshot_commit,
        })
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

        approval = self.prepare()

        self.assertEqual(approval["base_commit"], self.head)
        self.assertEqual(approval["source_base_commit"], self.head)
        self.assertEqual(approval["execution_base_commit"], snapshot_commit)
        self.assertEqual(approval["worktree_head"], snapshot_commit)
        validated = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card),
        )
        self.assertEqual(json.loads(validated.stdout)["approval_id"], approval["approval_id"])

    def test_prepare_rejects_wrong_paths_and_non_fresh_strategy(self) -> None:
        wrong = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "test.txt",
            "--allow-new-write-path", "test.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(wrong.returncode, 2)
        self.assertFalse(self.approval.exists())

        runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["strategy"] = "reuse-managed"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        rejected = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("not reviewable", rejected.stderr)

    def test_prepare_rejects_zero_byte_placeholder_as_implementation(self) -> None:
        (self.worktree / "src.txt").write_bytes(b"")
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no material implementation evidence", result.stderr)

    def test_prepare_rejects_worktree_transferred_to_codex(self) -> None:
        marker = self.repo / ".worktrees" / "newer-task.codex-write-owner.json"
        marker.write_text(json.dumps({
            "write_owner": "codex", "worktree": str(self.worktree),
        }), encoding="utf-8")
        result = self.helper(
            "prepare", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--output", str(self.approval), check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("transferred to Codex", result.stderr)

    def test_validate_rejects_worktree_drift(self) -> None:
        self.prepare()
        (self.worktree / "src.txt").write_text("drifted\n", encoding="utf-8")
        result = self.helper(
            "validate", "--approval", str(self.approval),
            "--next-task-card", str(self.card), check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("state drifted", result.stderr)

    def test_post_run_allows_declared_new_path_and_rejects_outside_path(self) -> None:
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        self.prepare(next_role="checker-test", allow="test.txt")
        (self.worktree / "test.txt").write_text("new test\n", encoding="utf-8")
        passed = self.helper("post-run", "--approval", str(self.approval))
        self.assertTrue(json.loads(passed.stdout)["protected_existing_unchanged"])

        (self.worktree / "outside.txt").write_text("unexpected\n", encoding="utf-8")
        rejected = self.helper(
            "post-run", "--approval", str(self.approval), check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("outside approval", rejected.stderr)

    def test_checker_cannot_modify_accepted_implementation(self) -> None:
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        self.prepare(next_role="checker-test", allow="test.txt")
        (self.worktree / "src.txt").write_text("checker changed implementation\n", encoding="utf-8")
        result = self.helper("post-run", "--approval", str(self.approval), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("accepted existing paths", result.stderr)

    def test_installer_and_cli_expose_helper(self) -> None:
        installer = (ROOT / "scripts" / "install_workflow.py").read_text(encoding="utf-8")
        cli = (ROOT / "scripts" / "aiwf.py").read_text(encoding="utf-8")
        dispatch = (ROOT / "scripts" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
        self.assertIn(
            '("prepare-worktree-continuation.py", "ai/prepare-worktree-continuation.py")',
            installer,
        )
        self.assertIn('"reviewed-continuation":"prepare-worktree-continuation.py"', cli)
        self.assertIn("CLAUDE_CODE_REVIEWED_CONTINUATION", dispatch)
        self.assertIn("reviewed-continuation-consumed-", dispatch)


if __name__ == "__main__":
    unittest.main()
