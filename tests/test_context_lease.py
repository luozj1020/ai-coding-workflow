from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=check,
    )


class ExecutionCapsuleTests(unittest.TestCase):
    def test_helpers_schema_and_cli_are_installed(self) -> None:
        installer = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        cli = (SCRIPTS / "aiwf.py").read_text(encoding="utf-8")
        self.assertIn('(\"context-lease.py\", \"ai/context-lease.py\")', installer)
        self.assertIn(
            '(\"build-execution-capsule.py\", \"ai/build-execution-capsule.py\")',
            installer,
        )
        self.assertIn('("build-context-checkpoint.py", "ai/build-context-checkpoint.py")', installer)
        self.assertIn('("build-recovery-delta.py", "ai/build-recovery-delta.py")', installer)
        self.assertIn("schemas/context-lease-v1.schema.json", installer)
        self.assertIn("schemas/context-checkpoint-v1.schema.json", installer)
        self.assertIn("schemas/recovery-delta-v1.schema.json", installer)
        self.assertIn('\"context-lease\":\"context-lease.py\"', cli)
        self.assertIn('\"context-checkpoint\":\"build-context-checkpoint.py\"', cli)
        self.assertIn('\"recovery-delta\":\"build-recovery-delta.py\"', cli)
        self.assertIn('\"execution-capsule\":\"build-execution-capsule.py\"', cli)

    def test_delta_capsule_omits_control_plane_sections_and_binds_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = root / "card.md"
            output = root / "capsule.md"
            receipt = root / "receipt.json"
            checkpoint = root / "checkpoint.md"
            card.write_text(
                "# Task\n\n"
                "## Goal\n\nImplement the next slice.\n\n"
                "## Routing Economics\n\n" + ("Do not send this control detail. " * 40) + "\n\n"
                "## Scope\n\n- Write paths: src/a.py\n\n"
                "## Handoff Contract\n\n- Must do: edit src/a.py\n\n"
                "## Acceptance Criteria\n\n- AC-2 passes.\n",
                encoding="utf-8",
            )
            checkpoint.write_text("- accepted_fact: API shape is frozen\n", encoding="utf-8")
            completed = run(
                sys.executable, str(SCRIPTS / "build-execution-capsule.py"),
                "--task-card", str(card), "--output", str(output),
                "--mode", "delta", "--continuation-kind", "next-slice",
                "--rehydrate-from", str(checkpoint), "--receipt", str(receipt),
                cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("Implement the next slice", text)
            self.assertIn("API shape is frozen", text)
            self.assertIn("Write paths: src/a.py", text)
            self.assertNotIn("Routing Economics", text)
            self.assertLess(len(text.encode("utf-8")), len(card.read_bytes()) + len(checkpoint.read_bytes()))
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["mode"], "delta"
            )

    def test_strict_contract_coverage_fails_for_missing_categories(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = root / "card.md"
            card.write_text(
                "## Goal\n\nImplement.\n\n"
                "## Scope\n\n- Write paths: src/a.py\n\n"
                "## Handoff Contract\n\n- Must do: edit.\n\n"
                "## Acceptance Criteria\n\n- [ ] pass\n",
                encoding="utf-8",
            )
            completed = run(
                sys.executable, str(SCRIPTS / "build-execution-capsule.py"),
                "--task-card", str(card), "--output", str(root / "out.md"),
                "--require-complete-contract", cwd=ROOT, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("lacks required hard-contract categories", completed.stderr)

    def test_reviewed_continuation_embeds_only_accepted_delta_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = root / "card.md"
            output = root / "capsule.md"
            receipt = root / "receipt.json"
            approval = root / "approval.json"
            card.write_text(
                "# Revision\n\n## Goal\n\nFix the rejected parser behavior.\n\n"
                "## Scope\n\n- Write paths: src/cli.py\n\n"
                "## Required Changes\n\n- Use the real parser entrypoint.\n",
                encoding="utf-8",
            )
            approval.write_text(json.dumps({
                "schema_version": 1,
                "status": "available",
                "decision": "accepted-direction",
                "prior_task_id": "claude-prior",
                "next_task_card_sha256": hashlib.sha256(card.read_bytes()).hexdigest(),
                "accepted_path_state": {
                    "src/cli.py": {"kind": "file", "sha256": "a" * 64}
                },
                "delta_continuation": {
                    "baseline_worktree_state_hash": "sha256:" + "b" * 64,
                    "unresolved_findings": ["Parser flag does not exist"],
                    "new_validation_refs": ["sha256:" + "c" * 64],
                    "full_prior_task_card_repeated": False,
                },
            }), encoding="utf-8")

            completed = run(
                sys.executable, str(SCRIPTS / "build-execution-capsule.py"),
                "--task-card", str(card), "--output", str(output),
                "--mode", "delta", "--continuation-kind", "revision",
                "--reviewed-continuation", str(approval),
                "--receipt", str(receipt), cwd=ROOT,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            text = output.read_text(encoding="utf-8")
            self.assertIn("Accepted Continuation Context", text)
            self.assertIn("Parser flag does not exist", text)
            self.assertNotIn("full prior", text.lower())
            bound = json.loads(receipt.read_text(encoding="utf-8"))[
                "reviewed_continuation"
            ]
            self.assertEqual(bound["accepted_path_count"], 1)
            self.assertFalse(bound["full_prior_task_card_repeated"])

    def test_capsule_fails_when_card_has_no_executable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = root / "card.md"
            card.write_text("# Task\n\n## Routing Economics\n\nOnly routing.\n", encoding="utf-8")
            completed = run(
                sys.executable, str(SCRIPTS / "build-execution-capsule.py"),
                "--task-card", str(card), "--output", str(root / "out.md"),
                cwd=ROOT, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("no executable sections", completed.stderr)


class ContextLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        run("git", "init", "-q", cwd=self.repo)
        run("git", "config", "user.email", "test@example.com", cwd=self.repo)
        run("git", "config", "user.name", "Test", cwd=self.repo)
        scripts = self.repo / "scripts"
        scripts.mkdir()
        for name in (
            "context-lease.py", "prepare-worktree-continuation.py",
            "worktree_state_hash.py", "process-identity.py",
            "build-context-checkpoint.py", "build-execution-capsule.py",
        ):
            shutil.copy2(SCRIPTS / name, scripts / name)
        (self.repo / "src.txt").write_text("base\n", encoding="utf-8")
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-qm", "base", cwd=self.repo)
        self.head = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.worktree = self.repo / ".worktrees" / "builder"
        self.worktree.parent.mkdir()
        run(
            "git", "worktree", "add", "-q", "-b", "builder",
            str(self.worktree), self.head, cwd=self.repo,
        )
        self.task_id = "claude-initial"
        (self.worktree / "TASK_CARD_FULL.md").write_text(
            "| Mode | builder |\n", encoding="utf-8"
        )
        self.runtime_path = self.repo / ".worktrees" / f"{self.task_id}.runtime.json"
        self.runtime = {
            "schema_version": 1,
            "task_id": self.task_id,
            "strategy": "fresh",
            "task_mode": "builder",
            "worktree": str(self.worktree),
            "source_repository": str(self.repo),
            "base_commit": self.head,
            "source_base_commit": self.head,
            "execution_base_commit": self.head,
            "claude_session_id": "5ef9e3c8-bdbc-4d1e-8c64-c8bd0f0e4c66",
            "tool_profile": "minimal-builder",
            "lineage_root_task_id": self.task_id,
            "pid_files": {},
            "process_identity_files": {},
        }
        self.runtime_path.write_text(json.dumps(self.runtime), encoding="utf-8")
        (self.worktree / "src.txt").write_text("accepted slice one\n", encoding="utf-8")
        self.card = self.repo / "next.md"
        self.card.write_text(
            "| Mode | builder |\n\n## Goal\n\nImplement slice two.\n\n"
            "## Handoff Contract\n\n- Must do: update src.txt\n",
            encoding="utf-8",
        )
        self.contract = self.repo / "solution-contract.json"
        self.contract.write_text('{"state":"frozen"}\n', encoding="utf-8")
        self.lease = self.repo / ".worktrees" / "lease.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def helper(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable, str(self.repo / "scripts" / "context-lease.py"),
            *args, cwd=self.repo, check=check,
        )

    def create(self, output: Optional[Path] = None, *extra: str) -> dict:
        target = output or self.lease
        completed = self.helper(
            "create", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--continuation-kind", "next-slice",
            "--solution-contract", str(self.contract),
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--tool-profile", "minimal-builder",
            "--output", str(target), *extra,
        )
        return json.loads(completed.stdout)

    def validate(self, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.helper(
            "validate", "--context-lease", str(self.lease),
            "--next-task-card", str(self.card),
            "--continuation-kind", "next-slice",
            "--tool-profile", "minimal-builder", *extra, check=check,
        )

    def checkpoint(
        self, output: Path, receipt: Path, *, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable, str(self.repo / "scripts" / "build-context-checkpoint.py"),
            "--context-lease", str(self.lease),
            "--next-task-card", str(self.card),
            "--output", str(output), "--receipt", str(receipt),
            cwd=self.repo, check=check,
        )

    def test_create_and_validate_warm_resume(self) -> None:
        lease = self.create()
        self.assertEqual(lease["context_lease"]["session_id"], self.runtime["claude_session_id"])
        validated = json.loads(self.validate().stdout)
        self.assertEqual(validated["route"], "warm-resume")
        self.assertEqual(validated["calls_used"], 1)

    def test_card_tool_and_contract_drift_fail_closed(self) -> None:
        self.create()
        wrong_tool = self.helper(
            "validate", "--context-lease", str(self.lease),
            "--next-task-card", str(self.card),
            "--continuation-kind", "next-slice",
            "--tool-profile", "checker", check=False,
        )
        self.assertEqual(wrong_tool.returncode, 2)
        self.assertIn("tool profile changed", wrong_tool.stderr)

        self.contract.write_text('{"state":"changed"}\n', encoding="utf-8")
        contract_drift = self.validate(check=False)
        self.assertEqual(contract_drift.returncode, 2)
        self.assertIn("solution contract content changed", contract_drift.stderr)

    def test_reviewed_continuation_failure_has_stable_error_contract(self) -> None:
        self.card.write_text("| Mode | checker-test |\n", encoding="utf-8")
        completed = self.helper(
            "create", "--prior-task-id", self.task_id,
            "--next-task-card", str(self.card), "--next-role", "builder",
            "--continuation-kind", "next-slice",
            "--solution-contract", str(self.contract),
            "--accepted-existing-path", "src.txt",
            "--allow-new-write-path", "src.txt",
            "--tool-profile", "minimal-builder",
            "--output", str(self.lease), check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("context-lease:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_warm_limit_requires_rehydration_checkpoint(self) -> None:
        first = self.create(self.lease, "--max-warm-calls", "1")
        self.runtime.update({
            "context_lease_id": first["context_lease"]["lease_id"],
            "context_lease_calls_used": 1,
        })
        self.runtime_path.write_text(json.dumps(self.runtime), encoding="utf-8")
        second_path = self.repo / ".worktrees" / "lease-2.json"
        self.create(second_path, "--parent-lease", str(self.lease), "--max-warm-calls", "1")
        self.lease = second_path
        blocked = self.validate(check=False)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("warm-call limit reached", blocked.stderr)
        pending = json.loads(self.validate("--allow-auto-rehydrate").stdout)
        self.assertEqual(pending["route"], "capsule-rehydrate")
        self.assertTrue(pending["checkpoint_required"])
        checkpoint = self.repo / "checkpoint.md"
        checkpoint.write_text("accepted state summary\n", encoding="utf-8")
        result = json.loads(self.validate("--rehydrate-from", str(checkpoint)).stdout)
        self.assertEqual(result["route"], "capsule-rehydrate")

    def test_auto_checkpoint_is_bound_compact_and_rejects_card_drift(self) -> None:
        first = self.create(self.lease, "--max-warm-calls", "1")
        self.runtime.update({
            "context_lease_id": first["context_lease"]["lease_id"],
            "context_lease_calls_used": 1,
        })
        self.runtime_path.write_text(json.dumps(self.runtime), encoding="utf-8")
        second_path = self.repo / ".worktrees" / "lease-2.json"
        self.create(
            second_path, "--parent-lease", str(self.lease),
            "--max-warm-calls", "1",
        )
        self.lease = second_path

        checkpoint = self.repo / ".worktrees" / "checkpoint.md"
        checkpoint_receipt = self.repo / ".worktrees" / "checkpoint.json"
        generated = json.loads(self.checkpoint(checkpoint, checkpoint_receipt).stdout)
        self.assertEqual(generated["status"], "created")
        checkpoint_text = checkpoint.read_text(encoding="utf-8")
        self.assertIn("aiwf-context-checkpoint-v1", checkpoint_text)
        self.assertIn("Current task-card digest", checkpoint_text)
        self.assertNotIn("accepted slice one", checkpoint_text)
        self.assertLess(len(checkpoint_text.encode("utf-8")), 12 * 1024)

        capsule = self.repo / ".worktrees" / "capsule.md"
        capsule_receipt = self.repo / ".worktrees" / "capsule.json"
        completed = run(
            sys.executable, str(self.repo / "scripts" / "build-execution-capsule.py"),
            "--task-card", str(self.card), "--output", str(capsule),
            "--mode", "delta", "--continuation-kind", "next-slice",
            "--rehydrate-from", str(checkpoint),
            "--rehydrate-receipt", str(checkpoint_receipt),
            "--receipt", str(capsule_receipt), cwd=self.repo,
        )
        self.assertEqual(completed.returncode, 0)
        binding = json.loads(capsule_receipt.read_text(encoding="utf-8"))[
            "checkpoint_binding"
        ]
        self.assertEqual(binding["binding"], "receipt-bound")

        self.card.write_text(
            self.card.read_text(encoding="utf-8") + "\nChanged.\n",
            encoding="utf-8",
        )
        drift = run(
            sys.executable, str(self.repo / "scripts" / "build-execution-capsule.py"),
            "--task-card", str(self.card),
            "--output", str(self.repo / ".worktrees" / "drift.md"),
            "--mode", "delta", "--continuation-kind", "next-slice",
            "--rehydrate-from", str(checkpoint),
            "--rehydrate-receipt", str(checkpoint_receipt),
            cwd=self.repo, check=False,
        )
        self.assertEqual(drift.returncode, 2)
        self.assertIn("not bound to this task card", drift.stderr)


if __name__ == "__main__":
    unittest.main()
