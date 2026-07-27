from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
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
        self.task_id = "claude-round-2"
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
            "process_identity_files": {"claude": str(self.identity)},
        }), encoding="utf-8")
        self.candidate = self.root / "candidate.json"
        self.candidate.write_text(json.dumps({
            "schema_version": 2,
            "status": "preparation-required",
            "authorization": "codex-takeover-candidate",
            "current_task_id": self.task_id,
            "lineage_root_task_id": "root",
            "runtime_receipt": str(self.runtime.resolve()),
            "runtime_receipt_object": MOD.digest(self.runtime),
            "allowed_write_paths": ["file.txt"],
            "forbidden_paths": [],
            "required_validation": "run focused tests",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

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
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["runtime_receipt_object"] = MOD.digest(self.runtime)
        self.candidate.write_text(json.dumps(candidate), encoding="utf-8")
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
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["runtime_receipt_object"] = MOD.digest(self.runtime)
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

    def test_identity_matched_live_process_is_terminated_before_grant(self) -> None:
        sleeper = subprocess.Popen(["sleep", "60"])
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
