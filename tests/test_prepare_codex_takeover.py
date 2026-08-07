from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare-codex-takeover.py"
SPEC = importlib.util.spec_from_file_location("prepare_codex_takeover", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class PrepareCodexTakeoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.worktree, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.worktree, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.worktree, check=True)
        (self.worktree / "file.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.worktree, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.worktree, check=True)
        self.card = self.worktree / "TASK_CARD_FULL.md"
        self.card.write_text(
            "## Scope\n\n- Write paths: file.txt\n- Forbidden paths: deploy/\n",
            encoding="utf-8",
        )
        self.prior_task_id = "claude-round-1"
        self.task_id = "claude-round-2"
        self.session_id = "session-1"
        self.identity = self.root / "claude.process.json"
        self.identity.write_text(json.dumps({
            "schema_version": 1,
            "task_id": self.task_id,
            "role": "claude",
            "pid": 99999999,
            "start_time_ticks": 1,
            "pid_namespace_inode": 1,
            "cmdline_sha256": "sha256:" + "0" * 64,
        }), encoding="utf-8")
        self.runtime = self.root / "runtime.json"
        self.runtime.write_text(json.dumps({
            "task_id": self.task_id,
            "worktree": str(self.worktree),
            "source_repository": str(self.worktree),
            "source_base_commit": "source-base",
            "execution_base_commit": "execution-base",
            "lineage_root_task_id": "root",
            "retry_ordinal": 1,
            "retry_of": self.prior_task_id,
            "strategy": "retry-in-place",
            "claude_session_id": self.session_id,
            "process_identity_files": {"claude": str(self.identity)},
        }), encoding="utf-8")
        self.prior_runtime = self.root / "prior.runtime.json"
        self.prior_runtime.write_text(json.dumps({
            "task_id": self.prior_task_id,
            "worktree": str(self.worktree),
            "source_repository": str(self.worktree),
            "source_base_commit": "source-base",
            "execution_base_commit": "execution-base",
            "lineage_root_task_id": "root",
            "retry_ordinal": 0,
            "strategy": "fresh",
            "claude_session_id": self.session_id,
        }), encoding="utf-8")
        self.candidate = self.root / "candidate.json"
        self.candidate.write_text(json.dumps({
            "schema_version": 3,
            "status": "preparation-required",
            "authorization": "codex-takeover-candidate",
            "current_task_id": self.task_id,
            "lineage_root_task_id": "root",
            "runtime_receipt": str(self.runtime.resolve()),
            "runtime_receipt_object": MOD.digest(self.runtime),
            "attempt_lineage": {
                "schema": "aiwf-takeover-attempt-lineage-v1",
                "relation": "retry-in-place",
                "prior_runtime_receipt": str(self.prior_runtime.resolve()),
                "prior_runtime_receipt_object": MOD.digest(self.prior_runtime),
                "current_runtime_receipt": str(self.runtime.resolve()),
                "current_runtime_receipt_object": MOD.digest(self.runtime),
                "binding": {
                    "task_id": self.task_id,
                    "lineage_root_task_id": "root",
                    "task_card_sha256": MOD.digest(self.card),
                    "source_base_commit": "source-base",
                    "execution_base_commit": "execution-base",
                    "source_repository": str(self.worktree.resolve()),
                    "worktree": str(self.worktree.resolve()),
                    "claude_session_id": self.session_id,
                },
                "prior_task_id": self.prior_task_id,
                "current_task_id": self.task_id,
            },
            "allowed_write_paths": ["file.txt"],
            "forbidden_paths": [],
            "required_validation": "run focused tests",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _refresh_candidate_current_runtime_digest(self) -> None:
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["runtime_receipt_object"] = MOD.digest(self.runtime)
        candidate["attempt_lineage"]["current_runtime_receipt_object"] = MOD.digest(
            self.runtime
        )
        self.candidate.write_text(json.dumps(candidate), encoding="utf-8")

    def test_prepare_confirms_single_writer_and_writes_deny_marker(self) -> None:
        output = self.root / "grant.json"
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(output),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 0)
        grant = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(grant["single_writer_confirmed"])
        self.assertEqual(grant["write_owner"], "codex")
        self.assertEqual(grant["owner_lease"]["status"], "explicitly-absent")
        marker = self.root / f"{self.task_id}.codex-write-owner.json"
        self.assertTrue(marker.is_file())

    def test_runtime_mutation_after_candidate_fails_closed(self) -> None:
        runtime = json.loads(self.runtime.read_text(encoding="utf-8"))
        runtime["changed"] = True
        self.runtime.write_text(json.dumps(runtime), encoding="utf-8")
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(self.root / "grant.json"),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "grant.json").exists())

    def test_legacy_unbound_candidate_fails_closed(self) -> None:
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["schema_version"] = 2
        candidate.pop("attempt_lineage")
        self.candidate.write_text(json.dumps(candidate), encoding="utf-8")
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(self.root / "grant.json"),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "grant.json").exists())

    def test_prior_session_mismatch_fails_closed(self) -> None:
        prior = json.loads(self.prior_runtime.read_text(encoding="utf-8"))
        prior["claude_session_id"] = "different-session"
        self.prior_runtime.write_text(json.dumps(prior), encoding="utf-8")
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["attempt_lineage"]["prior_runtime_receipt_object"] = MOD.digest(
            self.prior_runtime
        )
        self.candidate.write_text(json.dumps(candidate), encoding="utf-8")
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(self.root / "grant.json"),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "grant.json").exists())

    def test_process_validation_failure_leaves_takeover_deny_marker(self) -> None:
        identity = json.loads(self.identity.read_text(encoding="utf-8"))
        identity.pop("pid")
        self.identity.write_text(json.dumps(identity), encoding="utf-8")
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(self.root / "grant.json"),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 2)
        marker = json.loads(
            (self.root / f"{self.task_id}.codex-write-owner.json").read_text(encoding="utf-8")
        )
        self.assertEqual(marker["status"], "takeover-in-progress")
        self.assertEqual(marker["authorization"], "none")
        self.assertFalse(marker["single_writer_confirmed"])
        self.assertFalse((self.root / "grant.json").exists())

    def test_unstarted_checker_receipt_is_confirmed_inactive(self) -> None:
        runtime = json.loads(self.runtime.read_text(encoding="utf-8"))
        runtime["process_identity_files"]["checker"] = str(self.root / "checker.process.json")
        runtime["pid_files"] = {"checker": str(self.root / "checker.pid")}
        self.runtime.write_text(json.dumps(runtime), encoding="utf-8")
        self._refresh_candidate_current_runtime_digest()
        output = self.root / "grant.json"
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(output),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 0)
        evidence = json.loads(output.read_text(encoding="utf-8"))["process_termination"]
        checker = next(row for row in evidence if row["role"] == "checker")
        self.assertEqual(checker["initial_status"], "not-started")
        self.assertTrue(checker["confirmed_inactive"])

    def test_pid_without_identity_fails_closed(self) -> None:
        runtime = json.loads(self.runtime.read_text(encoding="utf-8"))
        runtime["process_identity_files"]["checker"] = str(self.root / "checker.process.json")
        runtime["pid_files"] = {"checker": str(self.root / "checker.pid")}
        (self.root / "checker.pid").write_text("12345\n", encoding="utf-8")
        self.runtime.write_text(json.dumps(runtime), encoding="utf-8")
        self._refresh_candidate_current_runtime_digest()
        rc = MOD.main([
            "--receipt", str(self.candidate),
            "--runtime", str(self.runtime),
            "--no-owner-lease",
            "--output", str(self.root / "grant.json"),
            "--stability-interval", "0",
        ])
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "grant.json").exists())

    def test_identity_matched_live_process_is_terminated_before_grant(self) -> None:
        sleeper = subprocess.Popen([
            sys.executable, "-c", "import time; time.sleep(60)",
        ])
        try:
            identity = MOD.PROCESS_IDENTITY.capture(sleeper.pid, self.task_id, "claude")
            value = MOD.terminate_identity(identity, self.task_id, "claude", timeout=2.0)
            self.assertTrue(value["terminated"])
            self.assertTrue(value["confirmed_inactive"])
            sleeper.wait(timeout=3)
        finally:
            if sleeper.poll() is None:
                sleeper.kill()
                sleeper.wait(timeout=3)

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group fixture")
    def test_terminate_process_cli_stops_identity_bound_tree_and_writes_receipt(self) -> None:
        child_pid_file = self.root / "child.pid"
        parent = subprocess.Popen([
            sys.executable,
            "-c",
            (
                "import pathlib,subprocess,sys,time;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
                "start_new_session=True);"
                f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid));"
                "time.sleep(60)"
            ),
        ])
        try:
            deadline = time.time() + 5
            while not child_pid_file.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(child_pid_file.exists())
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            identity_path = self.root / "live.process.json"
            identity_path.write_text(
                json.dumps(
                    MOD.PROCESS_IDENTITY.capture(
                        parent.pid, self.task_id, "claude"
                    )
                ),
                encoding="utf-8",
            )
            output = self.root / "termination.json"

            rc = MOD.main([
                "terminate-process",
                "--identity", str(identity_path),
                "--task-id", self.task_id,
                "--role", "claude",
                "--terminate-timeout", "2",
                "--reason", "test-stop",
                "--output", str(output),
            ])

            self.assertEqual(rc, 0)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "confirmed-inactive")
            self.assertTrue(
                receipt["process_termination"]["confirmed_inactive"]
            )
            parent.wait(timeout=3)
            child_state = MOD.PROCESS_IDENTITY._process(child_pid)
            self.assertTrue(
                child_state is None or child_state.get("state") == "Z",
                child_state,
            )
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=3)

    def test_windows_authoritative_wait_closes_visibility_gap(self) -> None:
        identity = {
            "pid": 123,
            "task_id": self.task_id,
            "role": "claude",
            "start_time_ticks": 1,
            "pid_namespace_inode": 1,
            "cmdline_sha256": "sha256:" + "0" * 64,
        }
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(MOD.sys, "platform", "win32"), \
            mock.patch.object(
                MOD.PROCESS_IDENTITY,
                "check",
                side_effect=[
                    ("running-same-process", {}),
                    ("visibility-unknown", {}),
                ],
            ), \
            mock.patch.object(MOD, "_snapshot_processes", return_value={123: identity}), \
            mock.patch.object(MOD, "_same_process", return_value=True), \
            mock.patch.object(MOD, "_wait_windows_inactive", return_value=True), \
            mock.patch.object(MOD.subprocess, "run", return_value=completed):
            value = MOD.terminate_identity(
                identity, self.task_id, "claude", timeout=2.0,
            )
        self.assertTrue(value["terminated"])
        self.assertTrue(value["confirmed_inactive"])


if __name__ == "__main__":
    unittest.main()
